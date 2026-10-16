"""The stored Gmail token is shared by every process: a refresh must never narrow its scopes."""

import json
from types import SimpleNamespace

from app import crypto
from app.clients import gmail_client

READ = "https://www.googleapis.com/auth/gmail.readonly"
SEND = "https://www.googleapis.com/auth/gmail.send"


def mailbox(scopes):
    info = {"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s", "scopes": scopes}
    return SimpleNamespace(credentials_enc=crypto.encrypt(json.dumps(info)))


def configured(monkeypatch, scopes):
    monkeypatch.setattr(gmail_client, "get_settings", lambda: SimpleNamespace(scopes=scopes))


def test_an_old_read_only_process_keeps_the_send_scope(monkeypatch):
    configured(monkeypatch, [READ])
    assert set(gmail_client.load_credentials(mailbox([READ, SEND])).scopes) == {READ, SEND}


def test_a_token_without_a_configured_scope_needs_a_refresh(monkeypatch):
    configured(monkeypatch, [READ, SEND])
    assert gmail_client._missing_scopes(mailbox([READ])) == {SEND}
    assert gmail_client._missing_scopes(mailbox([READ, SEND])) == set()
