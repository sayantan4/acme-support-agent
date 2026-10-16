"""Controller: GET /mailboxes (connected Gmail accounts) and GET /healthz."""

from flask import Blueprint, jsonify

from app.models import Mailbox
from app.services import mailbox_service

bp = Blueprint("mailboxes", __name__)


@bp.get("/healthz")
def healthz():
    return {"ok": True}


@bp.get("/mailboxes")
def list_mailboxes():
    return jsonify([_mailbox_as_json(mailbox) for mailbox in mailbox_service.list_mailboxes()])


def _mailbox_as_json(mailbox: Mailbox) -> dict:
    return {
        "email": mailbox.email,
        "status": mailbox.status,
        "last_history_id": mailbox.last_history_id,
        "watch_expiration": mailbox.watch_expiration.isoformat() if mailbox.watch_expiration else None,
    }
