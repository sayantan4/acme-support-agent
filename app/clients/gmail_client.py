"""The Gmail API, behind one class. The rest of the app never calls googleapiclient directly.

    gmail = gmail_client_for(mailbox)       # loads the OAuth token, refreshing it if needed
    gmail.history_page(...)                 # sync: what changed since our cursor
    gmail.full_message(id), gmail.send(...) # read a message, send a reply
    gmail.watch(...), gmail.stop()          # push notifications

Also owns the mailbox's OAuth credentials: they are stored encrypted on the Mailbox row and
refreshed here. The caller commits its session afterwards, so a refreshed token is saved.
"""

import json

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app import crypto
from app.config import get_settings
from app.models import Mailbox

ME = "me"  # Gmail's name for "the mailbox these credentials belong to"


class MailboxDisconnected(Exception):
    """Refresh token revoked/expired; the user must reconnect via OAuth."""


class GmailClient:
    """One mailbox's Gmail API. Wraps the googleapiclient service so callers get plain data back."""

    def __init__(self, service):
        self._service = service

    # ---------------------------------------------------------------- mailbox

    def email_address(self) -> str:
        return self._profile()["emailAddress"]

    def current_history_id(self) -> str:
        """Where the mailbox's change history is now: a cursor to sync from later."""
        return str(self._profile()["historyId"])

    def _profile(self) -> dict:
        return self._service.users().getProfile(userId=ME).execute()

    # ---------------------------------------------------------------- reading

    def history_page(self, since_history_id: str, page_token: str | None) -> dict:
        """One page of "messages added to the INBOX after since_history_id" (history.list).

        Raises HttpError 404 when since_history_id is too old for Gmail to remember.
        """
        request = self._service.users().history().list(
            userId=ME,
            startHistoryId=since_history_id,
            historyTypes=["messageAdded"],
            labelId="INBOX",
            pageToken=page_token,
        )
        return request.execute()

    def inbox_message_ids(self, max_results: int) -> list[str]:
        """Ids of the newest INBOX messages, newest first."""
        response = self._service.users().messages().list(
            userId=ME, labelIds=["INBOX"], maxResults=max_results,
        ).execute()
        return [message["id"] for message in response.get("messages", [])]

    def full_message(self, message_id: str) -> dict | None:
        """The message with headers and body, or None if it was deleted in the meantime."""
        try:
            # format="full" includes the body; "metadata" would only give headers.
            return self._service.users().messages().get(userId=ME, id=message_id, format="full").execute()
        except HttpError as error:
            if error.resp.status == 404:
                return None
            raise

    def message_headers(self, message_id: str, names: list[str]) -> dict:
        """The message with only these headers (no body)."""
        return self._service.users().messages().get(
            userId=ME, id=message_id, format="metadata", metadataHeaders=names,
        ).execute()

    # ---------------------------------------------------------------- sending

    def send(self, raw_email: str, thread_id: str) -> str:
        """Send a base64url-encoded email into a thread. Returns Gmail's id for the sent message."""
        sent = self._service.users().messages().send(
            userId=ME, body={"raw": raw_email, "threadId": thread_id},
        ).execute()
        return sent["id"]

    # ---------------------------------------------------------------- push notifications

    def watch(self, topic: str, label_ids: list[str]) -> dict:
        """Ask Gmail to notify the Pub/Sub topic about changes to these labels: {historyId, expiration}."""
        body = {"topicName": topic, "labelIds": label_ids, "labelFilterBehavior": "INCLUDE"}
        return self._service.users().watch(userId=ME, body=body).execute()

    def stop(self) -> None:
        self._service.users().stop(userId=ME).execute()


# ==================================================================== credentials

def gmail_client_for(mailbox: Mailbox) -> GmailClient:
    """A Gmail client for this mailbox, refreshing (and storing) its access token if needed.

    Raises MailboxDisconnected if the refresh token no longer works.
    """
    credentials = load_credentials(mailbox)
    if not credentials.valid or _missing_scopes(mailbox):  # an unexpired but narrower token can't send
        try:
            credentials.refresh(Request())
        except RefreshError as error:
            raise MailboxDisconnected(str(error)) from error
        store_credentials(mailbox, credentials)
    return GmailClient(build("gmail", "v1", credentials=credentials, cache_discovery=False))


def load_credentials(mailbox: Mailbox) -> Credentials:
    info = json.loads(crypto.decrypt(mailbox.credentials_enc))
    # Ask for every scope the stored token has, plus our configured ones. A process started with an
    # older GMAIL_SCOPES (read-only) must never refresh the shared token down to fewer scopes.
    scopes = sorted(set(info.get("scopes") or []) | set(get_settings().scopes))
    return Credentials.from_authorized_user_info(info, scopes)


def store_credentials(mailbox: Mailbox, credentials: Credentials) -> None:
    mailbox.credentials_enc = crypto.encrypt(credentials.to_json())


def _missing_scopes(mailbox: Mailbox) -> set[str]:
    """Configured scopes the stored access token was not issued with (e.g. gmail.send)."""
    stored = json.loads(crypto.decrypt(mailbox.credentials_enc)).get("scopes") or []
    return set(get_settings().scopes) - set(stored)
