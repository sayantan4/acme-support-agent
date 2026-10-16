"""Eval results, imported from the eval runners' JSON files so Grafana can chart them (ai-evals dashboard).
Stored in ai_analyzer's own database (ANALYZER_DATABASE_URL), like every table in ai_analyzer/models.

    eval_runs               EvalRun              one row per results file per run (re-runs keep history)
    eval_retrieval_metrics  EvalRetrievalMetric  hit@1 / hit@3 / MRR / latency: overall, per eval file, per category
    eval_retrieval_cases    EvalRetrievalCase    every retrieval case: expected SOP, rank found, top 5, latency
    eval_agent_cases        EvalAgentCase        every agent case: passed, failed checks, tokens, seconds, reply
    eval_agent_checks       EvalAgentCheck       every check of every agent case
    eval_gate_cases         EvalGateCase         every scope gate case: expected vs actual decision, reason

Filled by services/eval_import_service.py (python -m ai_analyzer evals-import).
"""

from ai_analyzer.models.evals.agent_case import EvalAgentCase
from ai_analyzer.models.evals.agent_check import EvalAgentCheck
from ai_analyzer.models.evals.eval_run import EvalRun
from ai_analyzer.models.evals.gate_case import EvalGateCase
from ai_analyzer.models.evals.retrieval_case import EvalRetrievalCase
from ai_analyzer.models.evals.retrieval_metric import EvalRetrievalMetric

__all__ = ["EvalAgentCase", "EvalAgentCheck", "EvalGateCase", "EvalRetrievalCase", "EvalRetrievalMetric", "EvalRun"]
