"""Redis Streams client: publish events and consume them. Events are stored as one JSON field, "data".

ai_analyzer/streams.py is a copy of this file on purpose: the analyzer will become its own
service, so it must not import anything from `app`.
"""

import json
import logging
import time
from typing import Callable

import redis

from app.config import get_settings

log = logging.getLogger(__name__)

# Main app (summary scheduler) -> ai_analyzer (C1): "summarize this thread's new messages"
SUMMARY_REQUESTED = "gmail:summary:requested"
# ai_analyzer (C1) -> main app (C2, saves it): "here is the new summary"
SUMMARY_COMPLETED = "gmail:summary:completed"
# ai_analyzer (C1) -> ai_analyzer (C3, stores it): "here is the reply the agent wrote"
REPLY_READY = "gmail:reply:ready"

POLL_SECONDS = 1  # how long to wait before asking Redis again when there's nothing new

_client = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def publish(stream: str, event: dict) -> None:
    # maxlen keeps the stream from growing forever; "~" lets Redis trim cheaply.
    get_redis().xadd(stream, {"data": json.dumps(event)}, maxlen=10_000, approximate=True)


def consume(stream: str, group: str, consumer: str, handle: Callable[[dict], None],
            new_group_starts_at: str = "0") -> None:
    """Call `handle(event)` for each event, forever. An event is acked only if handle() succeeds.

    A failed event stays "pending" in Redis and is retried the next time this consumer starts.
    `new_group_starts_at` only matters the first time `group` is created: "0" = read the whole
    stream, "$" = only events published from now on.
    """
    r = get_redis()
    try:
        r.xgroup_create(stream, group, id=new_group_starts_at, mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):  # BUSYGROUP = group already exists, which is fine
            raise

    log.info("consuming %s as %s/%s", stream, group, consumer)
    read_from = "0"  # "0" = our own unacked events from a previous run; ">" = new events
    while True:
        # Ask without Redis' BLOCK option: our local Redis stopped ending blocked reads after
        # some hours of uptime, so we poll and sleep instead. At most POLL_SECONDS of delay.
        try:
            response = r.xreadgroup(group, consumer, {stream: read_from}, count=100)
        except (redis.ConnectionError, redis.TimeoutError):
            log.warning("redis not reachable; retrying in 5s")
            time.sleep(5)
            continue
        if read_from == "0":
            read_from = ">"  # old pending events are retried once per start, then only new ones

        entries = [entry for _stream, stream_entries in response or [] for entry in stream_entries]
        if not entries:
            time.sleep(POLL_SECONDS)
            continue

        for entry_id, fields in entries:
            try:
                handle(json.loads(fields["data"]))
                r.xack(stream, group, entry_id)
            except Exception:
                log.exception("failed to handle %s %s; will retry on restart", stream, entry_id)
