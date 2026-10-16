"""Controller: C3, the reply worker. Reads REPLY_READY and hands each reply to the reply service
(stale check -> main app stores it, and sends it unless it waits for approval).

    python -m ai_analyzer replier
"""

from ai_analyzer.clients import stream_client
from ai_analyzer.models.session import init_db
from ai_analyzer.services import reply_service


def run() -> None:
    init_db()
    stream_client.consume(stream_client.REPLY_READY, group="ai-replier", consumer="replier-1",
                          handle=reply_service.handle_reply)
