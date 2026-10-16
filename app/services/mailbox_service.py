"""Connecting Gmail accounts: OAuth consent -> store tokens -> start watch. And listing them."""

import logging
import secrets

from google.oauth2.credentials import Credentials

from app.clients import google_oauth_client
from app.clients.gmail_client import gmail_client_for, store_credentials
from app.models import Mailbox
from app.models.session import session_scope
from app.repositories import mailbox_repo
from app.services.watch_service import start_watch

log = logging.getLogger(__name__)

# state -> PKCE code_verifier, between start_connect() and finish_connect().
# In-memory: use Redis/DB if running more than one instance.
_pending: dict[str, str | None] = {}


class ConnectError(Exception):
    """The OAuth callback can't complete (bad state, consent refused, no refresh token)."""


def start_connect() -> str:
    """The Google consent URL to send the user to."""
    state = secrets.token_urlsafe(24)
    url, code_verifier = google_oauth_client.authorization_url(state)
    _pending[state] = code_verifier
    return url


def finish_connect(state: str | None, code: str | None) -> dict:
    """Google redirected back: trade the code for tokens, store them, start the watch."""
    if not state or state not in _pending:
        raise ConnectError("unknown or expired OAuth state")
    credentials = google_oauth_client.exchange_code(state, code, code_verifier=_pending.pop(state))
    if not credentials.refresh_token:
        raise ConnectError("no refresh token returned; revoke app access and retry")

    with session_scope() as session:
        mailbox = _save_mailbox(session, credentials)
        email, watch_expiration = mailbox.email, mailbox.watch_expiration
    log.info("connected mailbox %s", email)
    return {"connected": email, "watch_expiration": watch_expiration.isoformat()}


def list_mailboxes() -> list[Mailbox]:
    with session_scope() as session:
        return mailbox_repo.all_mailboxes(session)


def _save_mailbox(session, credentials: Credentials) -> Mailbox:
    """Store the tokens on the account's row (new or reconnected) and start its watch."""
    new_mailbox = Mailbox(email="pending", credentials_enc="")
    store_credentials(new_mailbox, credentials)
    email = gmail_client_for(new_mailbox).email_address()

    existing = mailbox_repo.get(session, email)
    if existing:
        existing.credentials_enc = new_mailbox.credentials_enc
        existing.status = "active"
        mailbox = existing
    else:
        new_mailbox.email = email
        mailbox_repo.add(session, new_mailbox)
        mailbox = new_mailbox
    # First registration: take the cursor from watch(). Reconnect: keep the old cursor so
    # history catches up on (or 404s -> full resync for) whatever happened meanwhile.
    start_watch(mailbox, set_cursor=existing is None)
    return mailbox
