"""Sending replies through Gmail: threading headers, send once, retry after a failure. Fake Gmail only."""

import base64
import email

import pytest

from app.clients.gmail_client import GmailClient
from app.config import get_settings
from app.models import Mailbox, ProcessedMessage
from app.models.session import init_db, session_scope
from app.services import reply_service

MAILBOX = "support@acme.example"


class FakeGmail:
    """Just enough of the Gmail API: messages().get() for the original's headers, messages().send()."""

    def __init__(self, fail_sends=0):
        self.sent, self.fail_sends = [], fail_sends

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format, metadataHeaders):
        headers = [{"name": "Message-ID", "value": "<orig-123@mail.gmail.com>"},
                   {"name": "References", "value": "<older-1@mail.gmail.com>"},
                   {"name": "From", "value": "Rahul <rahul@example.com>"}]
        return _Executable({"payload": {"headers": headers}})

    def send(self, userId, body):
        if self.fail_sends:
            self.fail_sends -= 1
            raise RuntimeError("gmail is down")
        self.sent.append(body)
        return _Executable({"id": f"sent-{len(self.sent)}"})


class _Executable:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


@pytest.fixture
def gmail(monkeypatch):
    fake = FakeGmail()
    monkeypatch.setattr(reply_service, "gmail_client_for", lambda mailbox: GmailClient(fake))
    monkeypatch.setattr(get_settings(), "send_replies", True)
    init_db()
    with session_scope() as s:
        s.query(ProcessedMessage).delete()
        if s.get(Mailbox, MAILBOX) is None:
            s.add(Mailbox(email=MAILBOX, credentials_enc="plain:{}"))
    return fake


def inbound(user_id=101) -> int:
    with session_scope() as s:
        msg = ProcessedMessage(email=MAILBOX, message_id="gmail-abc", thread_id="T1", sender="Rahul <rahul@example.com>",
                               subject="UPI deposit pending", body_text="hi", user_id=user_id)
        s.add(msg)
        s.flush()
        return msg.id


def reply_for(msg_id):
    return {"mailbox": MAILBOX, "thread_id": "T1", "in_reply_to_id": msg_id, "subject": "Re: UPI deposit pending",
            "reply": "Hi Rahul, your deposit is pending."}


def test_reply_is_sent_in_the_same_thread_with_threading_headers(gmail):
    row_id, created, status = reply_service.save_reply(reply_for(inbound()))
    assert (created, status) == (True, "sent")

    [sent] = gmail.sent
    assert sent["threadId"] == "T1"
    message = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert message["To"] == "rahul@example.com" and message["Subject"] == "Re: UPI deposit pending"
    assert message["In-Reply-To"] == "<orig-123@mail.gmail.com>"
    assert message["References"] == "<older-1@mail.gmail.com> <orig-123@mail.gmail.com>"
    assert "your deposit is pending" in message.get_payload()

    with session_scope() as s:
        row = s.get(ProcessedMessage, row_id)
        assert (row.send_status, row.gmail_sent_id) == ("sent", "sent-1")


def test_the_same_reply_handed_over_twice_is_sent_once(gmail):
    msg_id = inbound()
    reply_service.save_reply(reply_for(msg_id))
    assert reply_service.save_reply(reply_for(msg_id))[1:] == (False, "sent")
    assert len(gmail.sent) == 1


def test_a_failed_send_is_recorded_raised_and_retried(gmail):
    gmail.fail_sends = 1
    msg_id = inbound()
    with pytest.raises(RuntimeError):
        reply_service.save_reply(reply_for(msg_id))
    with session_scope() as s:
        assert s.query(ProcessedMessage).filter_by(direction="outbound").one().send_status == "failed"

    assert reply_service.save_reply(reply_for(msg_id))[1:] == (False, "sent")  # C3's retry
    assert len(gmail.sent) == 1


def test_nothing_is_sent_to_an_unverified_sender(gmail):
    reply_service.save_reply(reply_for(inbound(user_id=None)))
    assert gmail.sent == []


def test_with_sending_off_replies_are_only_stored(gmail, monkeypatch):
    monkeypatch.setattr(get_settings(), "send_replies", False)
    assert reply_service.save_reply(reply_for(inbound()))[1:] == (True, None)
    assert gmail.sent == []


# ---------- human approval of escalated replies ----------

@pytest.fixture
def stream(monkeypatch):
    import fakeredis

    from app.clients import stream_client
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(stream_client, "_client", fake)
    return fake


def status_of(row_id):
    with session_scope() as s:
        return s.get(ProcessedMessage, row_id).send_status


def test_an_escalated_reply_waits_and_is_sent_only_after_approval(gmail, stream):
    msg = inbound()
    row_id, created, status = reply_service.save_reply({**reply_for(msg), "escalate": True})
    assert (created, status, gmail.sent) == (True, "awaiting_approval", [])
    assert [r.id for r in reply_service.awaiting_approval(MAILBOX)] == [row_id]

    reply_service.save_reply({**reply_for(msg), "escalate": True})  # C3 redelivered: still waiting
    assert gmail.sent == []

    reply_service.approve_reply(row_id)
    assert status_of(row_id) == "approved" and gmail.sent == []  # C3 sends it, not the approve command
    [(_, fields)] = stream.xrange("gmail:reply:ready")
    event = __import__("json").loads(fields["data"])
    assert (event["approved_reply_id"], event["in_reply_to_id"]) == (row_id, msg)

    # What C3 does with that event: hand it back to the main app, which now sends it.
    assert reply_service.save_reply({**reply_for(msg), "reply": event["reply"]})[2] == "sent"
    assert len(gmail.sent) == 1 and reply_service.awaiting_approval(MAILBOX) == []


def test_a_rejected_reply_is_never_published_or_sent(gmail, stream):
    msg = inbound()
    row_id, _, _ = reply_service.save_reply({**reply_for(msg), "escalate": True})
    reply_service.reject_reply(row_id)

    assert status_of(row_id) == "rejected" and stream.xlen("gmail:reply:ready") == 0
    assert reply_service.save_reply(reply_for(msg))[2] == "rejected" and gmail.sent == []  # even if C3 retries
    with pytest.raises(ValueError, match="not awaiting approval"):
        reply_service.approve_reply(row_id)
