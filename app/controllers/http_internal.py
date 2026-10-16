"""Controller: the internal API for other services (ai_analyzer, ToolSvc). Only answers trusted hosts.

When ai_analyzer runs on another host, replace the network check with a service token.
"""

from flask import Blueprint, abort, jsonify, request

from app.config import get_settings, is_trusted
from app.models import ProcessedMessage
from app.services import message_service, reply_service

bp = Blueprint("internal", __name__, url_prefix="/internal")


@bp.before_request
def only_from_trusted_hosts():
    if not is_trusted(request.remote_addr, get_settings().trusted_networks):
        abort(403)


@bp.get("/messages/<int:msg_id>")
def one_message(msg_id: int):
    """GET /internal/messages/7?mailbox=...  ->  that message, or 404"""
    message = message_service.get_message(request.args["mailbox"], msg_id)
    if message is None:
        abort(404)
    return jsonify(_message_as_json(message))


@bp.get("/messages/<int:msg_id>/user")
def message_user(msg_id: int):
    """GET /internal/messages/7/user  ->  {"message_id": 7, "user_id": 101}

    toolsvc asks this before every tool call, so tools only ever see the data of the customer whose
    email is being handled. 404 if the message doesn't exist or its sender wasn't a verified customer.
    """
    user_id = message_service.owner_of(msg_id)
    if user_id is None:
        abort(404)
    return jsonify({"message_id": msg_id, "user_id": user_id})


@bp.get("/threads/<thread_id>/messages")
def thread_messages(thread_id: str):
    """GET /internal/threads/<thread_id>/messages?mailbox=...&after=3  ->  inbound messages with id > 3"""
    after = int(request.args.get("after", 0))
    messages = message_service.inbound_after(request.args["mailbox"], thread_id, after)
    return jsonify([_message_as_json(message) for message in messages])


@bp.get("/threads/<thread_id>/replies")
def thread_replies(thread_id: str):
    """GET /internal/threads/<thread_id>/replies?mailbox=...&limit=3  ->  our last sent replies, oldest first

    [{"id": 21, "body": "...", "kind": "answer"}, ...]   kind: answer | closing | holding | small_talk | out_of_scope | null (older rows)
    """
    limit = int(request.args.get("limit", 3))
    replies = message_service.sent_replies(request.args["mailbox"], thread_id, limit)
    return jsonify([{"id": reply.id, "body": reply.body_text, "kind": reply.reply_kind} for reply in replies])


@bp.post("/replies")
def store_reply():
    """POST /internal/replies  {mailbox, thread_id, in_reply_to_id, subject, reply, reply_kind, escalate}
         ->  {id, created, send_status}   send_status: "sent" | "failed" | "awaiting_approval" | null

    A failed Gmail send answers 500, so C3 doesn't ack its event and the send is retried.
    """
    reply_id, created, send_status = reply_service.save_reply(request.get_json())
    status_code = 201 if created else 200
    return jsonify({"id": reply_id, "created": created, "send_status": send_status}), status_code


def _message_as_json(message: ProcessedMessage) -> dict:
    """A stored message as other services see it."""
    return {
        "id": message.id,
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "from": message.sender,
        "subject": message.subject,
        "date": message.sent_at,
        "body": message.body_text,
        "is_automated": message.is_automated,
    }
