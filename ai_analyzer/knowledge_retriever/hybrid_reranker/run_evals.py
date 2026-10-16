"""Run the retrieval evals on HybridRetRanker (BM25 + Dense merged with RRF, then an LLM reranker).

    uv run python -m ai_analyzer.knowledge_retriever.hybrid_reranker.run_evals                    # latest
    uv run python -m ai_analyzer.knowledge_retriever.hybrid_reranker.run_evals --prompt rerank-v1

Each prompt version saves to its own file, eval_results_<version>.json, next to this file.
Needs OPENAI_API_KEY in .env: each eval email costs one embedding call and one LLM call,
so the latency numbers include both round trips.
The eval logic is shared by every retriever and lives in knowledge_retriever/evaluation.py.
"""

import argparse
from pathlib import Path

from ai_analyzer.config import get_settings
from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.hybrid import HybridRet
from ai_analyzer.knowledge_retriever.hybrid_reranker import HybridRetRanker, LLMReranker
from ai_analyzer.knowledge_retriever.hybrid_reranker.prompts import LATEST_PROMPT, PROMPTS
from ai_analyzer.services.eval_import_service import import_quietly
from ai_analyzer.sops.sop_parser import SOPParser

RESULTS_DIR = Path(__file__).parent
# Emails in flight at once. Bounded by the org's limit of 200,000 tokens per minute for the
# reranker model: one worker sends ~1,900 tokens every ~1.7 s (~65,000 a minute), so 2 workers
# (~130,000) leave headroom. 8 workers hit the limit and the run failed.
MAX_WORKERS = 2


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arg_parser.add_argument("--prompt", choices=sorted(PROMPTS), default=LATEST_PROMPT.version,
                            help=f"reranker prompt version (default: {LATEST_PROMPT.version})")
    prompt = PROMPTS[arg_parser.parse_args().prompt]

    parser = SOPParser()  # one parser, shared, so every part searches the same sections
    hybrid = HybridRet(dense=DenseRet(parser), bm25_ret=BM25Ret(parser))
    retriever = HybridRetRanker(hybrid, LLMReranker(prompt=prompt))
    retriever.index_sop()
    results_file = RESULTS_DIR / f"eval_results_{prompt.version}.json"
    retriever.run_evals(results_file=results_file, max_workers=MAX_WORKERS)
    import_quietly(results_file)  # for the ai-evals Grafana dashboard

    usage = retriever.reranker.usage
    print(f"\nReranker ({get_settings().openai_model}, prompt {prompt.version}): {usage.calls} calls, "
          f"{usage.input_tokens:,} input + {usage.output_tokens:,} output tokens, "
          f"{usage.unusable_replies} unusable replies")


if __name__ == "__main__":
    main()
