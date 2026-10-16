"""Controller: the Pub/Sub push endpoint. Verify, decode, hand off to the sync service, and ack fast.

Called by Google Cloud Pub/Sub (the push subscription created in scripts/setup_gcp.sh),
not by our own code. Any 2xx acks the message; anything else makes Pub/Sub retry with backoff.
"""

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, abort, request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import get_settings
from app.services.sync_service import sync_mailbox

log = logging.getLogger(__name__)
bp = Blueprint("webhook", __name__)

# Syncs run here after we've replied to Pub/Sub. For production scale, publish to a real
# queue (Cloud Tasks / SQS / Redis) and consume it from separate workers instead.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sync")


@bp.post("/pubsub/push")
def pubsub_push():
    verify_pubsub_jwt()

    try:
        envelope = request.get_json(force=True)
        notification = decode_notification(envelope)
        email = notification["emailAddress"]
    except Exception:
        # Malformed payload will never succeed; ack it so Pub/Sub doesn't retry forever.
        log.exception("dropping malformed Pub/Sub message")
        return "", 204

    log.info(
        "notification for %s historyId=%s pubsubMessageId=%s",
        email, notification.get("historyId"), envelope["message"].get("messageId"),
    )
    schedule_sync(email)
    return "", 204


def verify_pubsub_jwt() -> None:
    """Abort unless the request carries a valid Google-signed token from our push service account."""
    settings = get_settings()
    if not settings.pubsub_verify_jwt:
        return
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        abort(401, "missing bearer token")
    token = auth_header.split(" ", 1)[1]
    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=settings.pubsub_audience or None,
        )
    except ValueError as e:
        abort(401, f"invalid token: {e}")
    if settings.pubsub_sa_email and (
        claims.get("email") != settings.pubsub_sa_email or not claims.get("email_verified")
    ):
        abort(403, "unexpected push service account")


def decode_notification(envelope: dict) -> dict:
    """Pub/Sub envelope -> {"emailAddress": ..., "historyId": ...}"""
    data = envelope["message"]["data"]
    return json.loads(base64.b64decode(data))


def schedule_sync(email: str) -> None:
    """Sync in the background, so Pub/Sub gets its ack right away."""
    _executor.submit(_run_sync, email)


def _run_sync(email: str) -> None:
    try:
        sync_mailbox(email)
    except Exception:
        log.exception("background sync failed for %s", email)
