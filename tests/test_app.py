import base64
import json
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

from app.clients.gmail_client import GmailClient
from app.controllers import http_webhook
from app.main import create_app
from app.models import Mailbox, ProcessedMessage
from app.models.session import init_db, session_scope
from app.services import sync_service


@pytest.fixture
def client():
    return create_app().test_client()


def envelope(email="user@example.com", history_id="1234"):
    data = base64.b64encode(json.dumps({"emailAddress": email, "historyId": history_id}).encode()).decode()
    return {"message": {"data": data, "messageId": "m1"}, "subscription": "projects/p/subscriptions/s"}


# ---------- webhook ----------

def test_push_acks_and_schedules_sync(monkeypatch, client):
    calls = []
    monkeypatch.setattr(http_webhook, "schedule_sync", lambda email: calls.append(email))
    r = client.post("/pubsub/push", json=envelope())
    assert r.status_code == 204
    assert calls == ["user@example.com"]


def test_malformed_push_is_acked_not_retried(monkeypatch, client):
    monkeypatch.setattr(http_webhook, "schedule_sync", lambda email: pytest.fail("should not sync"))
    r = client.post("/pubsub/push", json={"message": {"data": "not-base64!!"}})
    assert r.status_code == 204


def test_jwt_required_when_enabled(monkeypatch, client):
    monkeypatch.setattr(http_webhook.get_settings(), "pubsub_verify_jwt", True)
    r = client.post("/pubsub/push", json=envelope())
    assert r.status_code == 401


# ---------- sync ----------

class FakeGmail:
    """Minimal stand-in for the googleapiclient Gmail service."""

    def __init__(self, history_pages=None, history_404=False, inbox=None, profile_history_id="900"):
        self.history_pages = history_pages or []
        self.history_404 = history_404
        self.inbox = inbox or []
        self.profile_history_id = profile_history_id
        self.fetched = []

    def users(self):
        return self

    def history(self):
        return self

    def messages(self):
        return self

    def _exec(self, value):
        return SimpleNamespace(execute=lambda: value() if callable(value) else value)

    def list(self, userId, pageToken=None, startHistoryId=None, labelIds=None, **kw):
        if startHistoryId is not None:  # history.list
            if self.history_404:
                def boom():
                    raise HttpError(SimpleNamespace(status=404, reason="Not Found"), b"{}")
                return self._exec(boom)
            idx = int(pageToken or 0)
            return self._exec(self.history_pages[idx])
        return self._exec({"messages": [{"id": i} for i in self.inbox]})  # messages.list

    def get(self, userId, id, **kw):
        self.fetched.append(id)
        return self._exec({"id": id, "threadId": id, "snippet": "hi", "payload": {"headers": []}})

    def getProfile(self, userId):
        return self._exec({"historyId": self.profile_history_id})


@pytest.fixture
def mailbox():
    init_db()
    with session_scope() as s:
        s.query(ProcessedMessage).delete()
        s.query(Mailbox).delete()
        s.add(Mailbox(email="user@example.com", credentials_enc="plain:{}", last_history_id="100"))
    return "user@example.com"


def _cursor(email):
    with session_scope() as s:
        return s.get(Mailbox, email).last_history_id


def test_incremental_sync_pages_dedupes_and_advances_cursor(monkeypatch, mailbox):
    added = lambda mid: {"messagesAdded": [{"message": {"id": mid, "labelIds": ["INBOX"]}}]}
    fake = FakeGmail(history_pages=[
        {"history": [added("a"), added("b")], "nextPageToken": "1", "historyId": "150"},
        {"history": [added("b"), added("c")], "historyId": "200"},  # "b" duplicated
    ])
    monkeypatch.setattr(sync_service, "gmail_client_for", lambda mb: GmailClient(fake))
    handled = []
    monkeypatch.setattr(sync_service.message_service, "on_new_message", lambda s, m: handled.append(m.message_id))

    assert sync_service.sync_mailbox(mailbox) == 3
    assert handled == ["a", "b", "c"]
    assert _cursor(mailbox) == "200"

    # Re-delivery of the same notification processes nothing new.
    fake.history_pages = [{"history": [added("c")], "historyId": "200"}]
    assert sync_service.sync_mailbox(mailbox) == 0


def test_stale_history_id_triggers_full_resync(monkeypatch, mailbox):
    fake = FakeGmail(history_404=True, inbox=["new2", "new1"], profile_history_id="999")
    monkeypatch.setattr(sync_service, "gmail_client_for", lambda mb: GmailClient(fake))
    handled = []
    monkeypatch.setattr(sync_service.message_service, "on_new_message", lambda s, m: handled.append(m.message_id))

    assert sync_service.sync_mailbox(mailbox) == 2
    assert handled == ["new1", "new2"]  # oldest first
    assert _cursor(mailbox) == "999"


def test_handler_failure_keeps_cursor_for_retry(monkeypatch, mailbox):
    added = lambda mid: {"messagesAdded": [{"message": {"id": mid, "labelIds": ["INBOX"]}}]}
    fake = FakeGmail(history_pages=[{"history": [added("ok"), added("bad")], "historyId": "300"}])
    monkeypatch.setattr(sync_service, "gmail_client_for", lambda mb: GmailClient(fake))

    def handler(s, m):
        if m.message_id == "bad":
            raise RuntimeError("downstream down")

    monkeypatch.setattr(sync_service.message_service, "on_new_message", handler)
    with pytest.raises(RuntimeError):
        sync_service.sync_mailbox(mailbox)
    assert _cursor(mailbox) == "100"  # not advanced

    # Next sync retries only the failed one.
    monkeypatch.setattr(sync_service.message_service, "on_new_message", lambda s, m: None)
    fake.fetched.clear()
    assert sync_service.sync_mailbox(mailbox) == 1
    assert fake.fetched == ["bad"]
    assert _cursor(mailbox) == "300"


# ---------- pull mode ----------

class FakePubsubMessage:
    def __init__(self, data: bytes):
        self.data, self.message_id, self.result = data, "pm1", None

    def ack(self):
        self.result = "ack"

    def nack(self):
        self.result = "nack"


def test_pull_acks_after_successful_sync(monkeypatch):
    from app.controllers import pubsub_worker

    calls = []
    monkeypatch.setattr(pubsub_worker, "sync_mailbox", lambda email: calls.append(email))
    msg = FakePubsubMessage(b'{"emailAddress": "user@example.com", "historyId": "1"}')
    pubsub_worker.handle_message(msg)
    assert calls == ["user@example.com"] and msg.result == "ack"


def test_pull_nacks_when_sync_fails(monkeypatch):
    from app.controllers import pubsub_worker

    def boom(email):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(pubsub_worker, "sync_mailbox", boom)
    msg = FakePubsubMessage(b'{"emailAddress": "user@example.com", "historyId": "1"}')
    pubsub_worker.handle_message(msg)
    assert msg.result == "nack"


def test_pull_acks_malformed_message(monkeypatch):
    from app.controllers import pubsub_worker

    monkeypatch.setattr(pubsub_worker, "sync_mailbox", lambda email: pytest.fail("should not sync"))
    msg = FakePubsubMessage(b"not json")
    pubsub_worker.handle_message(msg)
    assert msg.result == "ack"
