"""Auth service API, and the main app's strict "registered customers only" gate at ingest."""

import pytest
import requests

from app.clients import auth_client
from app.main import create_app as create_main_app
from app.models import ProcessedMessage
from app.models.session import init_db, session_scope
from app.services import message_service
from auth.services import user_service
from auth.controllers.http_api import create_app as create_auth_app
from auth.models import User
from auth.models.session import session_scope as auth_session

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def auth_api():
    client = create_auth_app().test_client()
    with auth_session() as s:
        s.query(User).delete()
    return client


def register(client, email, user_id=None):
    return client.post("/register/user", json={"email_id": email, "user_id": user_id}, environ_base=LOCAL)


def verify(client, email):
    return client.post("/verify/user", json={"email_id": email}, environ_base=LOCAL)


# ---------- auth service ----------

def test_register_then_verify(auth_api):
    created = register(auth_api, "Rahul.Mehta@Example.com", user_id=101)
    assert created.status_code == 201
    assert created.get_json()["email_id"] == "rahul.mehta@example.com"  # stored lowercase

    found = verify(auth_api, "Rahul <RAHUL.MEHTA@example.com>")      # any case, display name allowed
    assert found.status_code == 200
    assert (found.get_json()["user_id"], found.get_json()["is_active"]) == (101, True)


def test_unknown_email_is_404_and_duplicates_are_409(auth_api):
    assert verify(auth_api, "nobody@example.com").status_code == 404
    register(auth_api, "a@example.com")
    assert register(auth_api, "A@example.com").status_code == 409
    assert register(auth_api, "not-an-email").status_code == 400


def test_user_ids_are_assigned_when_not_given(auth_api):
    first = register(auth_api, "one@example.com").get_json()["user_id"]
    second = register(auth_api, "two@example.com").get_json()["user_id"]
    assert second > first


def test_inactive_user_is_reported_as_inactive(auth_api):
    user_id = register(auth_api, "gone@example.com").get_json()["user_id"]
    user_service.set_active(user_id, False)
    assert verify(auth_api, "gone@example.com").get_json()["is_active"] is False


def test_seed_registers_the_demo_customers_once(auth_api):
    assert len(user_service.seed_demo_users()) == 6
    assert user_service.seed_demo_users() == []  # second run changes nothing
    assert verify(auth_api, "neha.r@example.com").get_json()["user_id"] == 104


def test_auth_api_only_answers_this_machine(auth_api):
    assert auth_api.post("/verify/user", json={"email_id": "a@b.com"},
                         environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403


# ---------- main app: only registered, active senders are processed ----------

@pytest.fixture
def main_app_talks_to_auth(monkeypatch, auth_api):
    """The main app's auth client calls the real auth API (Flask test client instead of HTTP)."""
    class Response:
        def __init__(self, flask_response):
            self.status_code, self._json = flask_response.status_code, flask_response.get_json()

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(self.status_code)

    monkeypatch.setattr(auth_client.requests, "post",
                        lambda url, json, timeout: Response(auth_api.post("/verify/user", json=json, environ_base=LOCAL)))
    init_db()
    with session_scope() as s:
        s.query(ProcessedMessage).delete()
    return auth_api


def new_message(sender) -> int:
    """Store an inbound message and run the handler on it, as sync.py does."""
    with session_scope() as s:
        msg = ProcessedMessage(email="support@acme.example", message_id=f"gmail-{sender}", thread_id="T1",
                               sender=sender, subject="Help", body_text="hello")
        s.add(msg)
        s.flush()
        message_service.on_new_message(s, msg)
        return msg.id


def stored(msg_id):
    with session_scope() as s:
        m = s.get(ProcessedMessage, msg_id)
        return m.user_id, m.summary_status


def test_registered_sender_is_linked_to_their_user_and_processed(main_app_talks_to_auth):
    register(main_app_talks_to_auth, "rahul.mehta@example.com", user_id=101)
    msg_id = new_message("Rahul Mehta <Rahul.Mehta@example.com>")
    assert stored(msg_id) == (101, "pending")


def test_unregistered_sender_is_stored_but_not_processed(main_app_talks_to_auth):
    msg_id = new_message("Stranger <stranger@example.com>")
    assert stored(msg_id) == (None, None)  # kept for dedupe, never summarized or replied to


def test_inactive_sender_is_not_processed(main_app_talks_to_auth):
    user_id = register(main_app_talks_to_auth, "gone@example.com").get_json()["user_id"]
    user_service.set_active(user_id, False)
    assert stored(new_message("gone@example.com")) == (None, None)


def test_auth_outage_raises_so_the_message_is_retried(monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("auth is down")

    monkeypatch.setattr(auth_client.requests, "post", down)
    with pytest.raises(requests.ConnectionError):
        auth_client.verified_user_id("rahul.mehta@example.com")


def test_main_app_tells_toolsvc_which_user_a_message_belongs_to(main_app_talks_to_auth):
    register(main_app_talks_to_auth, "rahul.mehta@example.com", user_id=101)
    known = new_message("rahul.mehta@example.com")
    unknown = new_message("stranger@example.com")

    api = create_main_app().test_client()
    assert api.get(f"/internal/messages/{known}/user", environ_base=LOCAL).get_json() == {
        "message_id": known, "user_id": 101}
    assert api.get(f"/internal/messages/{unknown}/user", environ_base=LOCAL).status_code == 404
    assert api.get("/internal/messages/999999/user", environ_base=LOCAL).status_code == 404
