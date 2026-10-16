"""The main app's Postgres tables, as SQLAlchemy models. One file per table.

    mailboxes             Mailbox            connected Gmail accounts (OAuth tokens + history cursor)
    processed_messages    ProcessedMessage   every inbound email we stored, and every reply we wrote
    thread_summary_info   ThreadSummaryInfo  the running AI summary per thread (written only by C2)

Queries on them live in app/repositories; services never write SQL. session.py has the engine
and session_scope().
"""

from app.models.base import Base
from app.models.mailbox import Mailbox
from app.models.processed_message import ProcessedMessage
from app.models.thread_summary import ThreadSummaryInfo

__all__ = ["Base", "Mailbox", "ProcessedMessage", "ThreadSummaryInfo"]
