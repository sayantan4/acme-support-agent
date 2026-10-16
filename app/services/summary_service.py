"""Thread summaries, the main app's side: batch messages into requests for C1, save C1's results (C2).

    mark_for_summary()                a new inbound message waits for the next batch
    send_pending_summary_requests()   every SUMMARY_BATCH_SECONDS: one request per thread with
                                      waiting messages -> SUMMARY_REQUESTED -> ai_analyzer (C1)
    apply_summary()                   C2: SUMMARY_COMPLETED -> thread_summary_info

Messages are summarized in batches, not one by one. M1 (22:00), M2 (22:02) and M3 (22:04) wait as
"pending"; the next scheduler run (say 22:05) sends one request for their thread, and C1 summarizes
all three with one LLM call.

A request carries the thread's saved summary and the id of the newest message it covers
(last_summary_msg_id). C1 fetches every message after that id. Its result says which message the
new summary goes up to, and C2 keeps it only if that is newer than the saved one.
"""

import logging
import textwrap
import uuid

from app.clients import stream_client
from app.models import ProcessedMessage
from app.models.session import session_scope
from app.repositories import message_repo, summary_repo

log = logging.getLogger(__name__)

PENDING = "pending"      # stored, waiting for the next batch
REQUESTED = "requested"  # included in a request to C1


def mark_for_summary(message: ProcessedMessage) -> None:
    """Don't summarize now, wait for the next batch."""
    message.summary_status = PENDING


def send_pending_summary_requests() -> int:
    """Send one summary request per thread that has pending messages. Returns how many were sent.

    Everything happens in one transaction: if publishing fails, the messages stay "pending" and
    the next run tries again. A request that was published but not marked is sent twice at worst,
    and C2 ignores a summary that isn't newer than the saved one.
    """
    with session_scope() as session:
        pending = message_repo.with_summary_status(session, PENDING)
        threads = _unique_threads(pending)

        for mailbox, thread_id in threads:
            _publish_request(session, mailbox, thread_id)
        for message in pending:
            message.summary_status = REQUESTED
    return len(threads)


def apply_summary(event: dict) -> None:
    """C2: save the summary unless we already have one that is as new or newer."""
    with session_scope() as session:
        if _save_if_newer(session, event):
            log.info("saved summary thread=%s up to msg %d\n%s",
                     event["thread_id"], event["last_summary_msg_id"], _pretty(event))
        else:
            log.info("skipped summary thread=%s up to msg %d: saved one is as new or newer",
                     event["thread_id"], event["last_summary_msg_id"])


def _unique_threads(messages: list[ProcessedMessage]) -> list[tuple[str, str]]:
    """(mailbox, thread_id) of each thread, once, in the order its first message appears."""
    threads = []
    for message in messages:
        thread = (message.email, message.thread_id)
        if thread not in threads:
            threads.append(thread)
    return threads


def _publish_request(session, mailbox: str, thread_id: str) -> None:
    """Ask C1 to extend the thread's saved summary with every message after the one it covers."""
    current = summary_repo.get(session, mailbox, thread_id)
    event = {
        "event_id": str(uuid.uuid4()),
        "mailbox": mailbox,
        "thread_id": thread_id,
        "summary_till_now": current.summary if current else "",
        "last_summary_msg_id": current.last_summary_msg_id if current else 0,  # 0 = no summary yet
    }
    stream_client.publish(stream_client.SUMMARY_REQUESTED, event)
    log.info("requested summary thread=%s: messages after %d", thread_id, event["last_summary_msg_id"])


def _save_if_newer(session, event: dict) -> bool:
    """True if the event's summary was saved: the thread had none, or the saved one is older."""
    mailbox, thread_id = event["mailbox"], event["thread_id"]
    summary, up_to = event["summary_data"], event["last_summary_msg_id"]
    if summary_repo.get(session, mailbox, thread_id) is None:
        summary_repo.add(session, mailbox, thread_id, summary, last_summary_msg_id=up_to)
        return True
    return summary_repo.update_if_newer(session, mailbox, thread_id, summary, last_summary_msg_id=up_to)


def _pretty(event: dict) -> str:
    """Readable block for the terminal: which model was called, then the summary it wrote."""
    line = "─" * 72
    # Wrap each line on its own so the model's own line breaks (e.g. bullet points) survive.
    wrapped_lines = []
    for summary_line in event["summary_data"].splitlines():
        if summary_line.strip():
            wrapped_lines.append(
                textwrap.fill(summary_line, width=70, initial_indent="  ", subsequent_indent="    "))
    summary = "\n".join(wrapped_lines)
    return (
        f"{line}\n"
        f"MODEL CALLED : {event.get('model')}  (prompt {event.get('prompt_version')})\n"
        f"TOKENS       : {event.get('input_tokens')} in / {event.get('output_tokens')} out\n"
        f"THREAD       : {event['thread_id']}  "
        f"({event.get('message_count')} new message(s), up to msg {event['last_summary_msg_id']})\n"
        f"SUMMARY      :\n{summary}\n"
        f"{line}"
    )
