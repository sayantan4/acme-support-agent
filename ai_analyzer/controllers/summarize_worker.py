"""Controller: C1, the summarizer worker. Reads SUMMARY_REQUESTED and hands each request to the
summarize service (agent run -> summary for C2, reply for C3).

    python -m ai_analyzer summarizer

Also serves Prometheus metrics on ANALYZER_METRICS_PORT (default 9101) for the dashboard (monitoring/).
"""

import logging

from ai_analyzer.agent import retrieval
from ai_analyzer.clients import metrics_client, stream_client
from ai_analyzer.config import get_settings
from ai_analyzer.models.session import init_db
from ai_analyzer.services import price_service, summarize_service

log = logging.getLogger(__name__)


def run() -> None:
    init_db()
    price_service.sync_prices()  # model_prices.json -> DB, for the dashboard's cost estimates
    port = get_settings().analyzer_metrics_port
    metrics_client.start_server(port)
    log.info("metrics on http://localhost:%d/metrics", port)
    # Index the SOPs now (embeds every section) rather than inside the first email's agent run,
    # so that run's latency and LLM calls are only its own.
    retrieval.get_retriever()
    stream_client.consume(stream_client.SUMMARY_REQUESTED, group="ai-analyzer", consumer="analyzer-1",
                          handle=handle_request)


def handle_request(event: dict) -> None:
    summarize_service.handle_summary_request(event)
