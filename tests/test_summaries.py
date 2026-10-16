"""AI pipeline: handler -> scheduler (batches) -> C1 (agent) -> C2 (summary) and C3 (reply), with fake
Redis and a fake agent. C1 and C3 use the real internal API (Flask test client) to read messages and
store reply_service. The agent's own loop is tested in tests/test_agent.py.
"""

import base64
import json

import fakeredis
import pytest

import ai_analyzer.clients.stream_client as analyzer_streams
import ai_analyzer.models.session as analyzer_session
import ai_analyzer.services.reply_service as c3
import ai_analyzer.services.summarize_service as c1
from ai_analyzer.agent import AgentResult
from ai_analyzer.agent import orchestrator
from ai_analyzer.agent.scope import ScopeDecision
from ai_analyzer.models import AgentRun, LLMCall, ReplyReview
from ai_analyzer.services import review_service
from app.clients import stream_client
from app.main import create_app
from app.models import ProcessedMessage, ThreadSummaryInfo
from app.models.session import init_db, session_scope
from app.services import summary_service
from app.utils import email_text

MAILBOX = "user@example.com"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    # One fake Redis shared by both services, like the real one.
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(stream_client, "_client", fake)
    monkeypatch.setattr(analyzer_streams, "_client", fake)

    # Fake agent: the "summary" is the old summary plus each new subject, so contents are visible,
    # and the reply names the message it answers.
    def fake_run_agent(summary_till_now, messages, reply_to_message_id, previous_replies=None,
                       last_reply_kind=None, scope_note=None):
        parts = ([summary_till_now] if summary_till_now else []) + [m["subject"] for m in messages]
        target = next((m for m in messages if m["id"] == reply_to_message_id), None)
        return AgentResult(summary="+".join(parts), reply=f"reply to {target['subject']}" if target else None,
                           escalate=False, escalation_note=None, sop_ids_used=[],
                           reply_to_message_id=reply_to_message_id, model="fake")

    monkeypatch.setattr(orchestrator, "run_agent", fake_run_agent)
    # The scope gate is a real LLM call: stub it. Default: every message goes to the (fake) agent.
    monkeypatch.setattr(orchestrator, "check_scope", lambda summary, messages, previous, last_kind: ScopeDecision(
        decision="in_scope", reason="test", summary=summary))
    monkeypatch.setattr(c1.runs_file, "append", lambda report: None)  # keep the real agent_runs.log clean

    # C1 and C3 call the main app's real internal API, through Flask's test client instead of HTTP.
    client = create_app().test_client()

    def get_messages(mailbox, thread_id, after):
        response = client.get(f"/internal/threads/{thread_id}/messages",
                              query_string={"mailbox": mailbox, "after": after})
        assert response.status_code == 200
        return response.get_json()

    def get_sent_replies(mailbox, thread_id, limit=3):
        response = client.get(f"/internal/threads/{thread_id}/replies",
                              query_string={"mailbox": mailbox, "limit": limit})
        assert response.status_code == 200
        return response.get_json()

    def get_message(mailbox, msg_id):
        response = client.get(f"/internal/messages/{msg_id}", query_string={"mailbox": mailbox})
        assert response.status_code == 200
        return response.get_json()

    def store_reply(reply):
        response = client.post("/internal/replies", json=reply)
        assert response.status_code in (200, 201)
        return response.get_json()

    monkeypatch.setattr(c1, "get_messages", get_messages)
    monkeypatch.setattr(c1, "get_sent_replies", get_sent_replies)
    monkeypatch.setattr(c3, "get_messages", get_messages)
    monkeypatch.setattr(c3, "get_message", get_message)
    monkeypatch.setattr(c3, "store_reply", store_reply)

    init_db()
    with session_scope() as s:
        s.query(ThreadSummaryInfo).delete()
        s.query(ProcessedMessage).delete()
    analyzer_session.init_db()
    with analyzer_session.session_scope() as s:
        s.query(ReplyReview).delete()
        s.query(LLMCall).delete()  # references agent_runs
        s.query(AgentRun).delete()
    return fake


def store_message(subject, thread_id="T1", automated=False, user_id=101) -> int:
    """A new inbound message, as the handler stores it: waiting for the next batch.

    user_id=None = a sender the auth service doesn't know (the handler wouldn't mark it pending, but a
    message like that can still sit in a thread next to a verified customer's).
    """
    with session_scope() as s:
        msg = ProcessedMessage(email=MAILBOX, message_id=f"gmail-{subject}", thread_id=thread_id,
                               sender="a@b.com", subject=subject, body_text="hello",
                               is_automated=automated, user_id=user_id)
        summary_service.mark_for_summary(msg)
        s.add(msg)
        s.flush()
        return msg.id


def status_of(msg_id):
    with session_scope() as s:
        return s.get(ProcessedMessage, msg_id).summary_status


def take_events(fake, stream):
    """Return all events in a stream and empty it (stands in for a consumer reading them)."""
    events = [json.loads(fields["data"]) for _id, fields in fake.xrange(stream)]
    fake.delete(stream)
    return events


def run_batch():
    return summary_service.send_pending_summary_requests()


def run_c1(fake):
    for event in take_events(fake, stream_client.SUMMARY_REQUESTED):
        c1.handle_summary_request(event)


def run_c2_and_c3(fake):
    """C2 saves every summary C1 published; C3 stores every reply C1 published."""
    for event in take_events(fake, stream_client.SUMMARY_COMPLETED):
        summary_service.apply_summary(event)
    for event in take_events(fake, stream_client.REPLY_READY):
        c3.handle_reply(event)


def saved(thread_id="T1"):
    with session_scope() as s:
        row = s.get(ThreadSummaryInfo, (MAILBOX, thread_id))
        return (row.summary, row.last_summary_msg_id) if row else None


def stored_replies():
    with session_scope() as s:
        return [(r.in_reply_to_id, r.subject, r.body_text, r.thread_id)
                for r in s.query(ProcessedMessage).filter_by(direction="outbound").all()]


# ---------- batching: messages wait as "pending" until the scheduler sends them ----------

def test_new_message_waits_for_the_batch(fake_redis):
    a = store_message("A")
    assert status_of(a) == "pending"
    assert take_events(fake_redis, stream_client.SUMMARY_REQUESTED) == []  # nothing sent yet

    assert run_batch() == 1
    assert status_of(a) == "requested"
    assert run_batch() == 0  # nothing pending any more


def test_batch_sends_one_request_per_thread(fake_redis):
    a, b, c = store_message("M1"), store_message("M2"), store_message("M3")  # e.g. 22:00, 22:02, 22:04
    store_message("other", thread_id="T2")

    assert run_batch() == 2
    requests = take_events(fake_redis, stream_client.SUMMARY_REQUESTED)
    assert [r["thread_id"] for r in requests] == ["T1", "T2"]
    assert (requests[0]["summary_till_now"], requests[0]["last_summary_msg_id"]) == ("", 0)

    for request in requests:
        c1.handle_summary_request(request)
    [t1_result, _t2_result] = take_events(fake_redis, stream_client.SUMMARY_COMPLETED)
    # One LLM call for all three messages of T1; the summary goes up to the newest one.
    assert (t1_result["summary_data"], t1_result["last_summary_msg_id"]) == ("M1+M2+M3", c)


def test_request_carries_the_saved_summary_and_its_newest_message(fake_redis):
    a = store_message("A")
    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)
    assert saved() == ("A", a)

    b = store_message("B")
    run_batch()
    [request] = take_events(fake_redis, stream_client.SUMMARY_REQUESTED)
    assert (request["summary_till_now"], request["last_summary_msg_id"]) == ("A", a)

    c1.handle_summary_request(request)
    run_c2_and_c3(fake_redis)
    assert saved() == ("A+B", b)


def test_c1_includes_messages_that_arrived_after_the_request(fake_redis):
    store_message("A")
    run_batch()
    b = store_message("B")  # arrives before C1 picks up the request
    run_c1(fake_redis)
    [result] = take_events(fake_redis, stream_client.SUMMARY_COMPLETED)
    assert (result["summary_data"], result["last_summary_msg_id"]) == ("A+B", b)


def test_failed_publish_keeps_messages_pending(fake_redis, monkeypatch):
    a = store_message("A")

    def redis_down(stream, event):
        raise ConnectionError("redis down")

    monkeypatch.setattr(stream_client, "publish", redis_down)
    with pytest.raises(ConnectionError):
        run_batch()
    assert status_of(a) == "pending"  # the next run tries again


def test_older_result_arriving_last_is_skipped(fake_redis):
    store_message("A")
    run_batch()
    [request_a] = take_events(fake_redis, stream_client.SUMMARY_REQUESTED)
    c1.handle_summary_request(request_a)
    b = store_message("B")
    run_batch(); run_c1(fake_redis)
    result_a, result_b = take_events(fake_redis, stream_client.SUMMARY_COMPLETED)

    summary_service.apply_summary(result_b)
    summary_service.apply_summary(result_a)  # older, arrives late
    summary_service.apply_summary(result_b)  # duplicate
    assert saved() == ("A+B", b)


# ---------- C3: reply unless a newer message has arrived ----------

def test_reply_is_written_and_stored_for_a_person(fake_redis):
    a = store_message("Lunch?")
    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)
    assert stored_replies() == [(a, "Re: Lunch?", "reply to Lunch?", "T1")]


def test_c3_drops_a_reply_when_a_newer_message_arrived(fake_redis):
    store_message("Lunch?")
    run_batch(); run_c1(fake_redis)
    b = store_message("Make it dinner")  # arrives after the summary was made
    run_c2_and_c3(fake_redis)
    assert stored_replies() == []  # dropped: the agent hadn't seen b

    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)
    assert stored_replies() == [(b, "Re: Make it dinner", "reply to Make it dinner", "T1")]


def test_our_own_reply_does_not_make_a_reply_stale(fake_redis):
    store_message("Lunch?")
    run_batch(); run_c1(fake_redis)
    [event] = take_events(fake_redis, stream_client.REPLY_READY)
    c3.handle_reply(event)
    c3.handle_reply(event)  # redelivered after a crash: our stored reply is newer, but outbound
    assert len(stored_replies()) == 1  # not dropped, and still only one reply


def test_the_reply_kind_is_stored_with_the_reply_and_handed_to_the_next_run(fake_redis, monkeypatch):
    seen = []

    def closing_agent(summary_till_now, messages, reply_to_message_id, previous_replies=None, last_reply_kind=None, scope_note=None):
        seen.append((previous_replies, last_reply_kind))
        return AgentResult(summary="s", reply="Glad I could help.", reply_kind="closing", escalate=False,
                           escalation_note=None, sop_ids_used=[], reply_to_message_id=reply_to_message_id)

    monkeypatch.setattr(orchestrator, "run_agent", closing_agent)
    store_message("Thanks")
    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)
    with session_scope() as s:
        reply = s.query(ProcessedMessage).filter_by(direction="outbound").one()
        assert reply.reply_kind == "closing"
        reply.send_status = "sent"  # as if SEND_REPLIES were on

    store_message("Thanks again")
    run_batch(); run_c1(fake_redis)
    assert seen == [([], None), (["Glad I could help."], "closing")]


def runs_and_reviews():
    with analyzer_session.session_scope() as s:
        runs = [(r.outcome, r.reply_id, r.in_reply_to_id) for r in s.query(AgentRun).order_by("id")]
        reviews = [(r.agent_run_id, r.status) for r in s.query(ReplyReview).order_by("id")]
        return runs, reviews


def test_c1_saves_the_run_and_a_pending_review_before_publishing_the_reply(fake_redis):
    msg = store_message("Deposit pending")
    run_batch(); run_c1(fake_redis)

    # Before C3 has done anything: run saved, review PENDING, and the event points at the run.
    [event] = take_events(fake_redis, stream_client.REPLY_READY)
    assert runs_and_reviews() == ([("published", None, msg)], [(event["agent_run_id"], "PENDING")])

    c3.handle_reply(event)
    c3.handle_reply(event)  # redelivered: nothing changes
    with session_scope() as s:
        reply_id = s.query(ProcessedMessage).filter_by(direction="outbound").one().id
    assert runs_and_reviews() == ([("stored", reply_id, msg)], [(event["agent_run_id"], "PENDING")])
    with analyzer_session.session_scope() as s:
        run = s.get(AgentRun, event["agent_run_id"])
        assert run.reply == "reply to Deposit pending" and run.summary == "Deposit pending" and run.prompt_version


def test_a_run_without_a_reply_is_still_reviewed(fake_redis, monkeypatch):
    def silent_agent(summary_till_now, messages, reply_to_message_id, previous_replies=None, last_reply_kind=None, scope_note=None):
        return AgentResult(summary="s", reply=None, escalate=False, escalation_note=None, sop_ids_used=[],
                           reply_to_message_id=reply_to_message_id)

    monkeypatch.setattr(orchestrator, "run_agent", silent_agent)
    msg = store_message("Thanks again")
    run_batch(); run_c1(fake_redis)
    assert take_events(fake_redis, stream_client.REPLY_READY) == []
    runs, reviews = runs_and_reviews()
    assert runs == [("no_reply", None, msg)] and reviews[0][1] == "PENDING"  # was staying silent right?


def test_a_dropped_reply_has_nothing_to_review(fake_redis):
    store_message("Lunch?")
    run_batch(); run_c1(fake_redis)
    store_message("Make it dinner")  # arrives before C3 runs: the reply is stale
    run_c2_and_c3(fake_redis)
    runs, reviews = runs_and_reviews()
    assert runs[0][0] == "dropped" and reviews[0][1] == "SKIPPED"


def test_no_reply_to_automated_mail(fake_redis):
    store_message("Your invoice", automated=True)
    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)
    assert stored_replies() == []


def test_stored_replies_are_left_out_of_summaries(fake_redis):
    store_message("Lunch?")
    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)

    store_message("Re: Lunch?")
    run_batch(); run_c1(fake_redis)
    [result] = take_events(fake_redis, stream_client.SUMMARY_COMPLETED)
    assert result["summary_data"] == "Lunch?+Re: Lunch?"  # our unsent reply isn't in there


def test_an_unverified_senders_message_never_reaches_c1(fake_redis):
    store_message("Who are you?", user_id=None)
    run_batch_for_all_threads()  # even if a request were sent, C1 gets no messages for it
    run_c1(fake_redis)
    assert take_events(fake_redis, stream_client.SUMMARY_COMPLETED) == []
    assert take_events(fake_redis, stream_client.REPLY_READY) == []


def test_reply_goes_to_the_newest_verified_customer_not_a_stranger_in_the_thread(fake_redis):
    a = store_message("Deposit pending")
    store_message("FYI from a stranger", user_id=None)
    run_batch(); run_c1(fake_redis)
    [reply] = take_events(fake_redis, stream_client.REPLY_READY)
    assert reply["in_reply_to_id"] == a and reply["reply"] == "reply to Deposit pending"
    [result] = take_events(fake_redis, stream_client.SUMMARY_COMPLETED)
    assert "stranger" not in result["summary_data"]  # the stranger's text never reached the agent


def run_batch_for_all_threads():
    """Mark every inbound message pending (as if verified) so a request is sent, then send it."""
    with session_scope() as s:
        for m in s.query(ProcessedMessage).filter_by(direction="inbound").all():
            m.summary_status = "pending"
    return run_batch()


# ---------- internal API and email parsing ----------

def test_api_returns_inbound_messages_after_an_id(fake_redis):
    a = store_message("A")
    store_message("other thread", thread_id="T2")
    store_message("B")
    c = store_message("C")

    assert [m["subject"] for m in c1.get_messages(MAILBOX, "T1", after=a)] == ["B", "C"]
    assert c1.get_messages(MAILBOX, "T1", after=c) == []


def test_api_returns_only_replies_the_customer_received(fake_redis):
    store_message("A")
    with session_scope() as s:
        for body, status in [("not sent", None), ("first", "sent"), ("failed", "failed"), ("second", "sent")]:
            s.add(ProcessedMessage(email=MAILBOX, message_id=f"out-{body}", thread_id="T1", direction="outbound",
                                   sender=MAILBOX, subject="Re: A", body_text=body, send_status=status,
                                   reply_kind="closing" if body == "second" else "answer"))

    def bodies_and_kinds(**kwargs):
        return [(r["body"], r["kind"]) for r in c1.get_sent_replies(MAILBOX, "T1", **kwargs)]

    assert bodies_and_kinds() == [("first", "answer"), ("second", "closing")]
    assert bodies_and_kinds(limit=1) == [("second", "closing")]
    assert c1.get_sent_replies(MAILBOX, "T2") == []


def test_api_rejects_other_machines():
    client = create_app().test_client()
    response = client.get("/internal/threads/T1/messages", query_string={"mailbox": MAILBOX},
                          environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert response.status_code == 403


def test_body_text_prefers_plain_then_html():
    def part(mime, text):
        return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}

    plain_and_html = {"payload": {"mimeType": "multipart/alternative",
                                  "parts": [part("text/html", "<b>hi</b>"), part("text/plain", "hi plain")]}}
    html_only = {"payload": {"mimeType": "multipart/alternative",
                             "parts": [part("text/html", "<p>Hello&nbsp;<b>world</b></p><style>x{}</style>")]}}

    assert email_text.body_text(plain_and_html) == "hi plain"
    assert email_text.body_text(html_only) == "Hello world"


def test_is_automated_detection():
    def msg(**headers):
        return {"payload": {"headers": [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]}}

    assert email_text.is_automated(msg(From="Google Cloud <CloudPlatform-noreply@google.com>"))
    assert email_text.is_automated(msg(From="News <news@shop.com>", List_Unsubscribe="<mailto:u@shop.com>"))
    assert email_text.is_automated(msg(From="Bob <bob@x.com>", Auto_Submitted="auto-replied"))
    assert email_text.is_automated(msg(From="Bob <bob@x.com>", Precedence="bulk"))
    assert not email_text.is_automated(msg(From="Rahul Mehta <rahul.mehta@example.com>"))


def test_when_two_runs_answer_the_same_message_only_the_one_whose_reply_was_stored_is_reviewed(fake_redis):
    run = {"prompt_version": "v", "model": "m", "sops_retrieved": [], "sops_followed": [], "tool_calls": [],
           "summary": "s", "reply": "r", "reply_kind": "answer", "escalate": False, "escalation_note": None,
           "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "seconds": 0.0}
    first = review_service.start_review(run, mailbox=MAILBOX, thread_id="T1", in_reply_to_id=7)
    second = review_service.start_review(run, mailbox=MAILBOX, thread_id="T1", in_reply_to_id=7)  # redelivered request

    review_service.reply_stored(first, reply_id=50)
    review_service.reply_stored(second, reply_id=50)  # the main app already had a reply for msg 7
    assert runs_and_reviews() == ([("stored", 50, 7), ("dropped", None, 7)], [(first, "PENDING"), (second, "SKIPPED")])


def test_c3_holds_escalated_replies_and_passes_on_an_approved_one_even_after_newer_mail(fake_redis, monkeypatch):
    from app.services import reply_service

    def escalating_agent(summary_till_now, messages, reply_to_message_id, previous_replies=None, last_reply_kind=None, scope_note=None):
        return AgentResult(summary="s", reply="A specialist will follow up.", reply_kind="holding", escalate=True,
                           escalation_note="legal threat", sop_ids_used=[], reply_to_message_id=reply_to_message_id)

    monkeypatch.setattr(orchestrator, "run_agent", escalating_agent)
    store_message("I am calling my lawyer")
    run_batch(); run_c1(fake_redis); run_c2_and_c3(fake_redis)
    with session_scope() as s:
        reply = s.query(ProcessedMessage).filter_by(direction="outbound").one()
        assert reply.send_status == "awaiting_approval"
        reply_id = reply.id

    store_message("Hello??")  # newer mail: a normal reply would now be dropped as stale
    reply_service.approve_reply(reply_id)
    handed_back = []
    monkeypatch.setattr(c3, "store_reply", lambda r: handed_back.append(r) or {"id": reply_id, "send_status": "sent"})
    for event in take_events(fake_redis, stream_client.REPLY_READY):
        c3.handle_reply(event)
    assert [r["in_reply_to_id"] for r in handed_back] == [reply.in_reply_to_id]  # not dropped


def test_an_out_of_scope_message_gets_the_template_without_running_the_agent(fake_redis, monkeypatch):
    monkeypatch.setattr(orchestrator, "run_agent", lambda *a, **k: pytest.fail("the agent must not run"))
    monkeypatch.setattr(orchestrator, "check_scope", lambda summary, messages, previous, last_kind: ScopeDecision(
        decision="out_of_scope", reason="asks for a loan", summary="Customer asked for a loan.",
        out_of_scope_topics=["loans"]))
    store_message("Can I get a loan?")
    run_batch(); run_c1(fake_redis)

    [reply] = take_events(fake_redis, stream_client.REPLY_READY)
    assert "doesn't offer loans" in reply["reply"] and reply["reply_kind"] == "out_of_scope"
    [summary] = take_events(fake_redis, stream_client.SUMMARY_COMPLETED)
    assert summary["summary_data"] == "Customer asked for a loan."
    with analyzer_session.session_scope() as s:
        run = s.query(AgentRun).one()
        assert (run.scope_decision, run.scope_reason, run.turns) == ("out_of_scope", "asks for a loan", 0)
        assert run.customer_message == "hello"  # the body (test messages all say "hello"), for the dashboard
