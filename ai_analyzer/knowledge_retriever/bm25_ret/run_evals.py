"""Run the retrieval evals on BM25Ret.

    uv run python -m ai_analyzer.knowledge_retriever.bm25_ret.run_evals

The eval logic is shared by every retriever and lives in knowledge_retriever/evaluation.py.
A summary is printed; every case is saved to eval_results.json next to this file.
"""

from pathlib import Path

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.services.eval_import_service import import_quietly
from ai_analyzer.sops.sop_parser import SOPParser

RESULTS_FILE = Path(__file__).parent / "eval_results_1.json"


def main() -> None:
    retriever = BM25Ret(SOPParser())
    retriever.index_sop()
    retriever.run_evals(results_file=RESULTS_FILE)
    import_quietly(RESULTS_FILE)  # for the ai-evals Grafana dashboard


if __name__ == "__main__":
    main()
