"""Gmail watch leases: Gmail notifies our Pub/Sub topic about INBOX changes for ~7 days; renew daily."""

import logging
from datetime import datetime, timezone

from app.clients.gmail_client import MailboxDisconnected, gmail_client_for
from app.config import get_settings
from app.models import Mailbox
from app.models.session import session_scope
from app.repositories import mailbox_repo

log = logging.getLogger(__name__)


def start_watch(mailbox: Mailbox, *, set_cursor: bool) -> dict:
    """Create or renew the watch.

    set_cursor=True only on first registration / after a full resync. On a renewal the
    returned historyId must NOT replace last_history_id, or unprocessed changes are skipped.
    """
    response = gmail_client_for(mailbox).watch(topic=get_settings().pubsub_topic, label_ids=["INBOX"])
    expiration_ms = int(response["expiration"])
    mailbox.watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)
    if set_cursor or not mailbox.last_history_id:
        mailbox.last_history_id = str(response["historyId"])
    log.info("watch active for %s until %s", mailbox.email, mailbox.watch_expiration)
    return response


def disconnect(email: str) -> None:
    """Stop watching the mailbox and mark it disconnected (python -m app.cli stop)."""
    with session_scope() as session:
        mailbox = mailbox_repo.get(session, email)
        if mailbox is None:
            raise ValueError(f"unknown mailbox {email}")
        gmail_client_for(mailbox).stop()
        mailbox.watch_expiration = None
        mailbox.status = "disconnected"


def renew_all_watches() -> dict:
    """Run daily (cron / Cloud Scheduler)."""
    renewed, failed = 0, 0
    with session_scope() as session:
        emails = mailbox_repo.active_emails(session)

    for email in emails:
        try:
            if _renew_one(email):
                renewed += 1
            else:
                failed += 1
        except Exception:
            log.exception("failed to renew watch for %s", email)
            failed += 1
    return {"renewed": renewed, "failed": failed}


def _renew_one(email: str) -> bool:
    """Renew one mailbox's watch, in its own transaction. False if the mailbox got disconnected."""
    with session_scope() as session:
        mailbox = mailbox_repo.get(session, email)
        try:
            start_watch(mailbox, set_cursor=False)
            return True
        except MailboxDisconnected:
            log.warning("refresh token invalid for %s; marking disconnected", email)
            mailbox.status = "disconnected"
            return False
