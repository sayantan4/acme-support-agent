"""ai_analyzer's own Postgres tables (ANALYZER_DATABASE_URL), as SQLAlchemy models. One file per table.

    agent_runs       AgentRun      every agent run: SOPs, tool calls and results, reply (or none), tokens
    reply_reviews    ReplyReview   one per run, created PENDING; a human sets REVIEWED and agent_was_wrong
    llm_calls        LLMCall       every LLM call of a run: purpose, tokens (input/cached/output), latency
    model_prices     ModelPrice    USD per 1M tokens per model (from model_prices.json), for cost estimates
    eval_*           EvalRun ...   eval results imported from the runners' JSON files (models/evals/)

The main app owns the email data; this service owns its runs and their reviews. Queries live in
ai_analyzer/repositories; session.py has the engine and session_scope().
"""

from ai_analyzer.models.agent_run import AgentRun
from ai_analyzer.models.base import Base
from ai_analyzer.models.evals import (
    EvalAgentCase, EvalAgentCheck, EvalRetrievalCase, EvalRetrievalMetric, EvalRun,
)
from ai_analyzer.models.llm_call import LLMCall
from ai_analyzer.models.model_price import ModelPrice
from ai_analyzer.models.reply_review import ReplyReview

__all__ = ["AgentRun", "Base", "EvalAgentCase", "EvalAgentCheck", "EvalRetrievalCase", "EvalRetrievalMetric",
           "EvalRun", "LLMCall", "ModelPrice", "ReplyReview"]
