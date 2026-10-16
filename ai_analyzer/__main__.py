"""python -m ai_analyzer summarizer   # C1: support agent (summary + reply, using SOPs and tools)
python -m ai_analyzer replier      # C3: store the agent's replies (drops out-of-date ones)
python -m ai_analyzer prices       # load model_prices.json into the DB (C1 also does it at start)
python -m ai_analyzer evals-import # load eval results files into the DB, for the ai-evals dashboard
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# The OpenAI client logs every HTTP request at INFO; keep our own lines readable.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="ai_analyzer")
    parser.add_argument("worker", nargs="?", default="summarizer", choices=["summarizer", "replier", "prices", "evals-import"])
    worker = parser.parse_args().worker
    if worker == "prices":
        from ai_analyzer.models.session import init_db
        from ai_analyzer.services.price_service import sync_prices

        init_db()
        print(f"loaded prices for {sync_prices()} models")
        raise SystemExit(0)
    if worker == "evals-import":
        from ai_analyzer.models.session import init_db
        from ai_analyzer.services.eval_import_service import import_all

        init_db()
        print(import_all())
        raise SystemExit(0)
    if worker == "summarizer":
        from ai_analyzer.controllers.summarize_worker import run
    else:
        from ai_analyzer.controllers.reply_worker import run
    run()
