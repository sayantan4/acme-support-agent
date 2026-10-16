"""Controller: the pull-mode worker. Asks Pub/Sub for notifications (instead of Pub/Sub POSTing to us)
and hands each one to the sync service.

No public URL, tunnel or JWT check needed (connections are outbound only). Authenticates with
Application Default Credentials: run `gcloud auth application-default login` once.

    python -m app.cli pull
"""

import json
import logging

from google.cloud import pubsub_v1

from app.config import get_settings
from app.services.sync_service import sync_mailbox

log = logging.getLogger(__name__)


def handle_message(message) -> None:
    """Called by the Pub/Sub client (in its own thread pool) for each notification."""
    try:
        notification = json.loads(message.data)
        email = notification["emailAddress"]
    except Exception:
        # Malformed payload will never succeed; ack it so it isn't redelivered forever.
        log.exception("dropping malformed Pub/Sub message %s", message.message_id)
        message.ack()
        return

    log.info("notification for %s historyId=%s", email, notification.get("historyId"))
    try:
        sync_mailbox(email)
    except Exception:
        # Unlike push mode, we can nack: Pub/Sub redelivers after backoff.
        log.exception("sync failed for %s; nacking for redelivery", email)
        message.nack()
        return
    message.ack()


def run() -> None:
    subscription = get_settings().pubsub_subscription
    if not subscription:
        raise SystemExit("set PUBSUB_SUBSCRIPTION in .env (projects/<id>/subscriptions/<name>)")

    subscriber = pubsub_v1.SubscriberClient()
    future = subscriber.subscribe(
        subscription,
        callback=handle_message,
        flow_control=pubsub_v1.types.FlowControl(max_messages=10),
    )
    log.info("listening on %s (Ctrl+C to stop)", subscription)
    with subscriber:
        try:
            future.result()
        except KeyboardInterrupt:
            future.cancel()
            future.result()
