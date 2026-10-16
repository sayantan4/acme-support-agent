"""Run the retrieval evals on HybridRet (BM25 + Dense, merged with RRF).

    uv run python -m ai_analyzer.knowledge_retriever.hybrid.run_evals

Needs OPENAI_API_KEY in .env, because the Dense half embeds every query.
The eval logic is shared by every retriever and lives in knowledge_retriever/evaluation.py.
"""

from pathlib import Path

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.hybrid import HybridRet
from ai_analyzer.services.eval_import_service import import_quietly
from ai_analyzer.sops.sop_parser import SOPParser

RESULTS_FILE = Path(__file__).parent / "eval_results.json"
MAX_WORKERS = 8  # emails in flight at once: each one mostly waits on OpenAI


def main() -> None:
    parser = SOPParser()  # one parser, shared, so both halves search the same sections
    retriever = HybridRet(dense=DenseRet(parser), bm25_ret=BM25Ret(parser))
    retriever.index_sop()
    retriever.run_evals(results_file=RESULTS_FILE, max_workers=MAX_WORKERS)
    import_quietly(RESULTS_FILE)  # for the ai-evals Grafana dashboard


if __name__ == "__main__":
    main()
