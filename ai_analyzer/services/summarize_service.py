"""C1's logic: for each summary request, run the support agent and publish its two results.

Request (SUMMARY_REQUESTED): {mailbox, thread_id, summary_till_now, last_summary_msg_id}

    1. Fetch every message after last_summary_msg_id, and our last sent replies, from the main app.
    2. Pick the message to answer: the newest one from a verified customer that isn't automated mail.
    3. The orchestrator (agent/orchestrator.py) routes it: the scope gate (a small model) decides what kind of message it is,
         in_scope / mixed        -> the main agent: SOPs (hybrid + rerank-v1) + tools -> new summary + reply
         no_issue / out_of_scope -> a template reply, or none (agent/template_replies.py): no agent run
    4. Save the run (agent_runs) and a PENDING review (reply_reviews) in our own DB.
    5. Publish the summary to SUMMARY_COMPLETED (C2 saves it) and the reply to REPLY_READY (C3 stores it).
    6. Print the agent's report: SOPs, tools called, final summary, final reply.
"""

import logging
import uuid
from dataclasses import asdict

from ai_analyzer.agent import format_report, handle
from ai_analyzer.agent.email_text import new_text
from ai_analyzer.clients import runs_file, stream_client
from ai_analyzer.clients.main_app_client import get_messages, get_sent_replies
from ai_analyzer.services.review_service import start_review

log = logging.getLogger(__name__)


MESSAGE_PREVIEW_CHARS = 160  # the customer's message, shortened, so a run is easy to recognise


def run_record(result, target: dict | None = None) -> dict:
    """What we save in agent_runs, so a reviewer can see why the agent replied (or didn't)."""
    sops_retrieved = [{"sop_id": sop.section.sop_id, "grade": sop.grade} for sop in result.sops]
    tool_calls = [
        {"turn": call.turn, "name": call.name, "arguments": call.arguments, "ok": call.ok, "output": call.output}
        for call in result.tool_calls
    ]
    return {
        "prompt_version": result.prompt_version,
        "model": result.model,
        "sops_retrieved": sops_retrieved,
        "sops_followed": result.sop_ids_used,
        "tool_calls": tool_calls,
        "summary": result.summary,
        "reply": result.reply,
        "reply_kind": result.reply_kind,
        "escalate": result.escalate,
        "escalation_note": result.escalation_note,
        "input_tokens": result.input_tokens + result.retrieval_input_tokens,
        "cached_input_tokens": result.cached_input_tokens,
        "output_tokens": result.output_tokens + result.retrieval_output_tokens,
        "seconds": round(result.retrieval_seconds + result.loop_seconds, 2),
        "retrieval_seconds": round(result.retrieval_seconds, 3),
        "loop_seconds": round(result.loop_seconds, 3),
        "turns": result.turns,
        "tool_call_count": len(result.tool_calls),
        "scope_decision": result.scope_decision,
        "scope_reason": result.scope_reason,
        "customer_message": new_text(target.get("body") or "")[:MESSAGE_PREVIEW_CHARS] if target else None,
    }


def message_to_answer(messages: list[dict]) -> dict | None:
    """Newest message the customer wrote themselves, not their mailbox's auto-reply (e.g. out of office).

    The main app sends only verified customers' messages, so every message here is from one. Replying to
    an auto-reply could start a mail loop, so automated messages are summarized but never answered.
    """
    candidates = [m for m in messages if not m.get("is_automated")]
    return candidates[-1] if candidates else None


def handle_summary_request(event: dict) -> None:
    """Run the agent on the thread's new messages; publish the summary (C2) and the reply (C3)."""
    after = event["last_summary_msg_id"]
    log.info("request thread=%s messages after %d | summary so far: %r",
             event["thread_id"], after, event["summary_till_now"] or "(none)")

    # Every message after the saved summary, including ones that arrived after the request was sent.
    messages = get_messages(event["mailbox"], event["thread_id"], after)
    if not messages:
        log.info("no messages after %d; nothing to do", after)
        return
    newest_msg_id = messages[-1]["id"]  # oldest first, so the last one is the newest
    target = message_to_answer(messages)
    target_id = target["id"] if target else None

    # What we already told the customer, so the agent doesn't repeat itself or reply to every "thanks".
    sent = get_sent_replies(event["mailbox"], event["thread_id"])
    previous_replies = [reply["body"] for reply in sent]
    last_reply_kind = sent[-1]["kind"] if sent else None

    result = handle(event["summary_till_now"], messages, target_id, previous_replies, last_reply_kind)
    report = format_report(result, event["thread_id"])
    log.info("%s", report)
    runs_file.append(report)

    # Saved before publishing, so every reply C3 handles already has its run and a review to fill in.
    agent_run_id = start_review(run_record(result, target), mailbox=event["mailbox"], thread_id=event["thread_id"],
                                in_reply_to_id=target_id,
                                llm_calls=[asdict(call) for call in result.llm_calls])

    stream_client.publish(stream_client.SUMMARY_COMPLETED,
                    summary_completed_event(event, result, newest_msg_id, message_count=len(messages)))
    if target is not None and result.reply:
        stream_client.publish(stream_client.REPLY_READY,
                        reply_ready_event(event, result, target_id, newest_msg_id, agent_run_id))


def summary_completed_event(request: dict, result, newest_msg_id: int, message_count: int) -> dict:
    """For C2 (main app), which saves the new summary."""
    return {
        "event_id": str(uuid.uuid4()),
        "request_id": request["event_id"],
        "mailbox": request["mailbox"],
        "thread_id": request["thread_id"],
        "last_summary_msg_id": newest_msg_id,  # the newest message this summary covers
        "summary_data": result.summary,
        "model": result.model,
        "prompt_version": result.prompt_version,
        "message_count": message_count,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def reply_ready_event(request: dict, result, in_reply_to_id: int, newest_msg_id: int,
                      agent_run_id: int) -> dict:
    """For C3, which stores the reply (and may send it)."""
    return {
        "event_id": str(uuid.uuid4()),
        "request_id": request["event_id"],
        "mailbox": request["mailbox"],
        "thread_id": request["thread_id"],
        "in_reply_to_id": in_reply_to_id,
        "last_summary_msg_id": newest_msg_id,  # C3 drops the reply if a newer message arrived since
        "reply": result.reply,
        "reply_kind": result.reply_kind,
        "escalate": result.escalate,
        "escalation_note": result.escalation_note,
        "sop_ids_used": result.sop_ids_used,
        "tools_called": [call.name for call in result.tool_calls],
        "agent_run_id": agent_run_id,  # C3 links the stored reply to this run
    }
