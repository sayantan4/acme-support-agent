"""The support agent's loop with a scripted fake LLM, fake retrieval and fake tools: no network."""

import json
from types import SimpleNamespace

import pytest

from ai_analyzer.agent import format_report, run_agent
from ai_analyzer.agent import loop, retrieval
from ai_analyzer.agent.prompt import AgentAnswer
from ai_analyzer.knowledge_retriever.interface import RetrievedSOP
from ai_analyzer.sops.sop_parser import SOPParser

MESSAGES = [{"id": 57, "from": "Rahul <rahul.mehta@example.com>", "subject": "UPI deposit pending",
             "date": "Thu, 2 Oct 2026", "body": "I added Rs 5000 by UPI 45 minutes ago, still not in my wallet."}]
SECTIONS = {s.sop_id: s for s in SOPParser().parse()}


class FakeRetriever:
    def __init__(self, grades: dict[str, int]):
        self.grades = grades

    def get_retrieve_data(self, query, number_of_items):
        return [RetrievedSOP(section=SECTIONS[sop_id], score=float(grade), rank=rank)
                for rank, (sop_id, grade) in enumerate(self.grades.items(), start=1)]


def tool_call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


def final_answer(**answer):
    answer = {"summary": "s", "reply": "r", "escalate": False, "escalation_note": None, "sop_ids_used": [],
              "reply_kind": "answer", **answer}
    return SimpleNamespace(output=[SimpleNamespace(type="message")], output_text=json.dumps(answer),
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def asks_for(*calls):
    return SimpleNamespace(output=list(calls), output_text="", usage=SimpleNamespace(input_tokens=100, output_tokens=10))


class ScriptedLLM:
    """Returns the scripted responses in order and keeps every request it got."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, **request):
        self.requests.append({**request, "input": list(request["input"])})
        return self.responses.pop(0)


class FakeTools:
    def __init__(self):
        self.calls = []

    def __call__(self, name, arguments, message_id):
        self.calls.append((name, arguments, message_id))
        return {"result": {"deposit": {"status": "PENDING", "age_minutes": 45}, "ledger_credited": False}}


def test_agent_follows_the_sop_calls_tools_and_returns_summary_and_reply():
    llm = ScriptedLLM(
        asks_for(tool_call("get_deposits", {"method": "UPI", "days": 7}, "c1")),
        asks_for(tool_call("check_deposit", {"deposit_id": 401}, "c2")),
        final_answer(summary="Rahul's UPI deposit 401 is pending 45 min.", reply="Hi Rahul, ...",
                     sop_ids_used=["PAY-01"]),
    )
    tools = FakeTools()
    result = run_agent("", MESSAGES, reply_to_message_id=57, retriever=FakeRetriever({"PAY-01": 3}),
                       create=llm, run_tool=tools)

    assert (result.summary, result.reply, result.sop_ids_used) == (
        "Rahul's UPI deposit 401 is pending 45 min.", "Hi Rahul, ...", ["PAY-01"])
    assert [c[0] for c in tools.calls] == ["get_deposits", "check_deposit"]
    assert all(message_id == 57 for _, _, message_id in tools.calls)  # from our code, every time
    assert [(c.turn, c.name, c.ok) for c in result.tool_calls] == [(1, "get_deposits", True), (2, "check_deposit", True)]
    assert (result.turns, result.input_tokens, result.output_tokens) == (3, 300, 40)


def test_only_the_retrieved_sections_tools_are_offered():
    llm = ScriptedLLM(final_answer())
    result = run_agent("", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 3}), create=llm, run_tool=FakeTools())
    offered = [t["name"] for t in llm.requests[0]["tools"]]
    assert result.offered_tools == retrieval.tools_listed(SECTIONS["PAY-01"])  # TOOLS: line order
    assert set(offered) == set(result.offered_tools)                          # nothing else is sent
    assert all(t["strict"] is False for t in llm.requests[0]["tools"])
    assert llm.requests[0]["text"]["format"]["schema"] == AgentAnswer.model_json_schema()


def test_a_tool_that_was_not_offered_is_refused_without_calling_toolsvc():
    llm = ScriptedLLM(asks_for(tool_call("get_risk_holds", {}, "c1")), final_answer())
    tools = FakeTools()
    result = run_agent("", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 3}), create=llm, run_tool=tools)
    assert tools.calls == []
    assert result.tool_calls[0].ok is False and "not available" in result.tool_calls[0].result_preview
    sent_back = llm.requests[1]["input"][-1]
    assert sent_back["type"] == "function_call_output" and "not available" in sent_back["output"]


def test_low_grade_sections_are_dropped_and_then_no_tools_are_offered():
    llm = ScriptedLLM(final_answer(escalate=True, escalation_note="no SOP"))
    result = run_agent("", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 1, "PAY-11": 0}), create=llm,
                       run_tool=FakeTools())
    assert result.sops == [] and result.offered_tools == [] and "tools" not in llm.requests[0]
    assert "(no SOP section matches)" in llm.requests[0]["input"][0]["content"]
    assert result.escalate is True


def test_without_a_verified_customer_there_are_no_tools_and_no_reply():
    llm = ScriptedLLM(final_answer(reply="should be ignored"))
    result = run_agent("", MESSAGES, None, retriever=FakeRetriever({"PAY-01": 3}), create=llm, run_tool=FakeTools())
    assert "tools" not in llm.requests[0] and result.reply is None
    assert "Reply to: none" in llm.requests[0]["input"][0]["content"]


def test_the_last_turn_forbids_more_tool_calls(monkeypatch):
    monkeypatch.setattr(loop, "MAX_TURNS", 2)
    llm = ScriptedLLM(asks_for(tool_call("get_deposits", {}, "c1")), final_answer())
    run_agent("", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 3}), create=llm, run_tool=FakeTools())
    assert "tool_choice" not in llm.requests[0] and llm.requests[1]["tool_choice"] == "none"


def test_prompt_has_summary_messages_sops_and_guidelines():
    llm = ScriptedLLM(final_answer())
    run_agent("Earlier: asked about KYC.", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 3}), create=llm,
              run_tool=FakeTools())
    prompt = llm.requests[0]["input"][0]["content"]
    assert "Earlier: asked about KYC." in prompt and "--- Message id 57 ---" in prompt
    assert "[PAY-01, relevance 3/3]" in prompt and "Reply to: message id 57" in prompt
    assert "## SUP-03" in llm.requests[0]["instructions"]  # support guidelines always included
    assert "user_id" not in json.dumps(llm.requests[0].get("tools", []))


def test_report_shows_sops_tools_summary_and_reply():
    llm = ScriptedLLM(asks_for(tool_call("check_deposit", {"deposit_id": 401}, "c1")),
                      final_answer(summary="Deposit pending.", reply="Hi Rahul, please wait 30 minutes.",
                                   sop_ids_used=["PAY-01"]))
    result = run_agent("", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 3}), create=llm, run_tool=FakeTools())
    report = format_report(result, "T1")
    for expected in ["AGENT RESULT  thread=T1  reply_to=msg 57", "RETRIEVAL      :", "AGENT LOOP     :", "TOTAL          :", "PAY-01 (grade 3)", '1. [turn 1] check_deposit({"deposit_id": 401}) -> ok',
                     "FINAL SUMMARY", "Deposit pending.", "FINAL REPLY", "please wait 30 minutes", "ESCALATE       : no"]:
        assert expected in report, expected


@pytest.mark.parametrize("line, tools", [("TOOLS: get_deposits, check_deposit", ["get_deposits", "check_deposit"]),
                                         ("TOOLS: none", [])])
def test_tools_line_parsing(line, tools):
    section = SimpleNamespace(text=f"## X-01: t\n{line}\nDEBUGGING STEPS:")
    assert retrieval.tools_listed(section) == tools


def test_prompt_shows_our_earlier_replies_and_only_the_new_part_of_each_message():
    llm = ScriptedLLM(final_answer())
    thanks = [{**MESSAGES[0], "body": "Thanks\r\n\r\nOn Fri, 2 Oct, 2026, 21:50 , <support@acme.in> wrote:\r\n\r\n"
                                       "> Your UPI deposit is pending.\r\n>"}]
    run_agent("", thanks, 57, previous_replies=["Your UPI deposit is pending."],
              retriever=FakeRetriever({}), create=llm, run_tool=FakeTools())
    prompt = llm.requests[0]["input"][0]["content"]
    assert "--- Our reply 1 ---\nYour UPI deposit is pending." in prompt
    assert "Thanks" in prompt and "> Your UPI deposit" not in prompt and "wrote:" not in prompt


@pytest.mark.parametrize("body, new", [
    ("Thanks", "Thanks"),
    ("Thanks\r\n\r\nOn Fri, 2 Oct, 2026, 17:37 Rahul Mehta, <a@b.com> wrote:\r\n\r\n> I got it\r\n>", "Thanks"),
    ("Ok\n\nOn Fri, 2 Oct, 2026 at 17:37, Very Long Name\n<a@b.com> wrote:\n> old", "Ok"),   # wrapped header
    ("> only a quote", "> only a quote"),                                                        # keep, not empty
    ("Line one\nOn Monday I paid twice\nLine three", "Line one\nOn Monday I paid twice\nLine three"),
])
def test_new_text_drops_quoted_history(body, new):
    from ai_analyzer.agent.email_text import new_text
    assert new_text(body) == new


@pytest.mark.parametrize("kind, last_kind, sent", [
    ("closing", "closing", False),   # "thanks" again after our closing reply: nothing more
    ("closing", "answer", True),     # first "thanks" after an answer: one closing reply
    ("closing", None, True),
    ("answer", "closing", True),     # a new issue after the closing still gets answered
])
def test_only_one_closing_reply_in_a_row(kind, last_kind, sent):
    llm = ScriptedLLM(final_answer(reply="Glad I could help. Have a nice day!", reply_kind=kind))
    result = run_agent("", MESSAGES, 57, last_reply_kind=last_kind, retriever=FakeRetriever({}), create=llm,
                       run_tool=FakeTools())
    assert (result.reply is not None) is sent
    assert (result.reply_dropped is not None) is (not sent)
