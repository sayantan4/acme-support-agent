"""Turn "something changed" notifications into processed messages.

A Pub/Sub notification only says the mailbox changed. The source of truth is history.list,
diffed from our stored cursor (last_history_id). Duplicate / out-of-order / coalesced
notifications are therefore harmless: each one just triggers a sync from the cursor.
"""

import logging
import threading
from collections import defaultdict
from typing import Iterator

from googleapiclient.errors import HttpError  # history_page raises it when the cursor is too old
from sqlalchemy import select

from app import email_text, handlers
from app.clients.gmail_client import GmailClient, MailboxDisconnected, gmail_client_for
from app.config import get_settings
from app.db import Mailbox, ProcessedMessage, session_scope

log = logging.getLogger(__name__)

# One sync per mailbox at a time. In-process only: with multiple workers/hosts, replace with a
# distributed lock (Redis SET NX, Postgres advisory lock) or a FIFO queue grouped by mailbox.
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def sync_mailbox(email: str) -> int:
    """Process all inbox additions since the stored cursor. Returns the number of new messages handled."""
    # If a sync is already running, wait and run again instead of dropping this trigger:
    # the running sync may have finished paging before this change landed.
    with _locks[email], session_scope() as session:
        mailbox = session.get(Mailbox, email)
        if mailbox is None or mailbox.status != "active":
            log.info("ignoring notification for unknown/inactive mailbox %s", email)
            return 0
        try:
            return _sync_from_cursor(session, mailbox, gmail_client_for(mailbox))
        except MailboxDisconnected:
            log.warning("refresh token invalid for %s; marking disconnected", email)
            mailbox.status = "disconnected"
            return 0


def _sync_from_cursor(session, mailbox: Mailbox, gmail: GmailClient) -> int:
    """Incremental sync from the cursor; a full resync if there is no cursor or Gmail forgot it."""
    if not mailbox.last_history_id:
        return _full_resync(session, mailbox, gmail)
    try:
        return _incremental_sync(session, mailbox, gmail)
    except HttpError as error:
        if error.resp.status != 404:
            raise
        log.warning("historyId %s too old for %s; full resync", mailbox.last_history_id, mailbox.email)
        return _full_resync(session, mailbox, gmail)


def _incremental_sync(session, mailbox: Mailbox, gmail: GmailClient) -> int:
    """Process every message added to the INBOX since the cursor, one history page at a time."""
    new_messages = 0
    newest_history_id = mailbox.last_history_id
    next_page_token = None

    while True:
        page = gmail.history_page(since_history_id=mailbox.last_history_id, page_token=next_page_token)
        for message_id in _added_inbox_message_ids(page):
            new_messages += _process_message(session, mailbox.email, gmail, message_id)

        newest_history_id = page.get("historyId", newest_history_id)
        next_page_token = page.get("nextPageToken")
        if not next_page_token:  # that was the last page
            break

    # Advance the cursor only after every page was processed successfully.
    mailbox.last_history_id = str(newest_history_id)
    return new_messages


def _added_inbox_message_ids(history_page: dict) -> Iterator[str]:
    """Ids of the messages added to the INBOX in one page of history.list."""
    for record in history_page.get("history", []):
        for added in record.get("messagesAdded", []):
            message = added["message"]
            if "INBOX" in message.get("labelIds", []):
                yield message["id"]


def _full_resync(session, mailbox: Mailbox, gmail: GmailClient) -> int:
    """No usable cursor: process the newest INBOX messages, oldest first, then start a new cursor."""
    # Take the new cursor BEFORE listing so nothing that arrives during the resync is lost.
    new_cursor = gmail.current_history_id()
    newest_first = gmail.inbox_message_ids(max_results=get_settings().full_sync_max_messages)
    new_messages = 0
    for message_id in reversed(newest_first):  # oldest first
        new_messages += _process_message(session, mailbox.email, gmail, message_id)
    mailbox.last_history_id = new_cursor
    return new_messages


def _process_message(session, email: str, gmail: GmailClient, message_id: str) -> int:
    """Store one Gmail message and run the handler on it. Returns 1 if it was new, else 0."""
    if _already_processed(session, email, message_id):
        return 0
    gmail_message = gmail.full_message(message_id)  # None: deleted after the notification
    if gmail_message is None:
        return 0
    stored = ProcessedMessage(
        email=email,
        message_id=message_id,
        thread_id=gmail_message["threadId"],
        sender=email_text.header(gmail_message, "From"),
        subject=email_text.header(gmail_message, "Subject"),
        sent_at=email_text.header(gmail_message, "Date"),
        body_text=email_text.body_text(gmail_message),
        is_automated=email_text.is_automated(gmail_message),
    )
    session.add(stored)
    session.flush()  # assigns stored.id
    handlers.on_new_message(session, stored)
    # Commit per message so a later failure doesn't re-run handlers for ones already done.
    session.commit()
    return 1


def _already_processed(session, email: str, message_id: str) -> bool:
    stored_id = session.scalar(select(ProcessedMessage.id).where(
        ProcessedMessage.email == email, ProcessedMessage.message_id == message_id))
    return stored_id is not None


def sync_all_active() -> dict:
    """Safety-net poll for missed notifications (e.g. endpoint was down past Pub/Sub's retry window)."""
    with session_scope() as session:
        emails = session.scalars(select(Mailbox.email).where(Mailbox.status == "active")).all()
    results = {}
    for email in emails:
        try:
            results[email] = sync_mailbox(email)
        except Exception:
            log.exception("sync failed for %s", email)
            results[email] = "error"
    return results
