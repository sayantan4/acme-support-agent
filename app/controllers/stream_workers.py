"""Controllers: the long-running summary workers.

    run_summary_scheduler()   every SUMMARY_BATCH_SECONDS: pending messages -> one request per thread (C1)
    run_summary_writer()      C2: reads SUMMARY_COMPLETED and saves each summary
"""

import logging
import time

from app.clients import stream_client
from app.config import get_settings
from app.services import summary_service

log = logging.getLogger(__name__)


def run_summary_scheduler(every_seconds: int | None = None) -> None:
    """python -m app.cli summary-scheduler"""
    every_seconds = every_seconds or get_settings().summary_batch_seconds
    log.info("summary scheduler: sending pending messages every %ds", every_seconds)
    while True:
        try:
            sent = summary_service.send_pending_summary_requests()
            if sent:
                log.info("sent %d summary request(s)", sent)
        except Exception:
            log.exception("summary batch failed; messages stay pending for the next run")
        time.sleep(every_seconds)


def run_summary_writer() -> None:
    """C2: python -m app.cli summary-writer"""
    stream_client.consume(stream_client.SUMMARY_COMPLETED, group="summary-writer", consumer="writer-1",
                          handle=summary_service.apply_summary)
