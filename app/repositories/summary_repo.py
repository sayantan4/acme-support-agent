"""Queries on thread_summary_info: the running AI summary per thread. Every function takes the
caller's session."""

from sqlalchemy.orm import Session

from app.models import ThreadSummaryInfo


def get(session: Session, mailbox: str, thread_id: str) -> ThreadSummaryInfo | None:
    return session.get(ThreadSummaryInfo, (mailbox, thread_id))


def add(session: Session, mailbox: str, thread_id: str, summary: str, last_summary_msg_id: int) -> None:
    session.add(ThreadSummaryInfo(email=mailbox, thread_id=thread_id, summary=summary,
                                  last_summary_msg_id=last_summary_msg_id))


def update_if_newer(session: Session, mailbox: str, thread_id: str, summary: str,
                    last_summary_msg_id: int) -> bool:
    """Replace the saved summary only if it covers fewer messages. True if it was replaced.

    Check and write in one statement, so two writers can't both think they're newest.
    """
    rows_updated = session.query(ThreadSummaryInfo).filter(
        ThreadSummaryInfo.email == mailbox,
        ThreadSummaryInfo.thread_id == thread_id,
        ThreadSummaryInfo.last_summary_msg_id < last_summary_msg_id,
    ).update(
        {"summary": summary, "last_summary_msg_id": last_summary_msg_id},
        synchronize_session=False,
    )
    return rows_updated == 1
