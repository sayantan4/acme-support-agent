"""Which SOP sections does this email need, and which tools do those sections allow?

Uses the best retriever from the evals: BM25 + Dense merged with RRF, then the LLM reranker with
prompt rerank-v1. Only sections the reranker grades 2 or 3 are used ("helps" or "the procedure").
If none qualifies, the agent gets no SOP and no tools (it escalates if the customer raised an issue).
"""

import re
from dataclasses import dataclass

from ai_analyzer.agent.email_text import new_text
from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.hybrid import HybridRet
from ai_analyzer.knowledge_retriever.hybrid_reranker import RERANK_V1, HybridRetRanker, LLMReranker
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface
from ai_analyzer.sops.sop_parser import SOPParser, SOPSection
from ai_analyzer.sops.sop_parser.parser import SOPS_DIR

MIN_GRADE = 2       # reranker grade: 3 = the procedure, 2 = helps, 1 = same topic only
MAX_SECTIONS = 3    # SOP sections put into the prompt
SUPPORT_GUIDELINES = SOPS_DIR / "support_guidelines_sop.txt"  # always in the prompt, never searched

_retriever: RetrievalInterface | None = None


@dataclass(frozen=True)
class SopMatch:
    section: SOPSection
    grade: int


def get_retriever() -> RetrievalInterface:
    """Built once per process: indexing embeds every SOP section (one OpenAI call per batch)."""
    global _retriever
    if _retriever is None:
        parser = SOPParser()
        hybrid = HybridRet(dense=DenseRet(parser), bm25_ret=BM25Ret(parser))
        _retriever = HybridRetRanker(hybrid, LLMReranker(prompt=RERANK_V1))
        _retriever.index_sop()
    return _retriever


def messages_to_query(messages: list[dict]) -> str:
    """What we search with: the subject and the new text (quotes removed) of each new message."""
    parts = []
    for message in messages:
        subject = message.get("subject") or ""
        body = new_text(message.get("body") or "")
        parts.append(f"{subject}\n{body}".strip())
    return "\n\n".join(parts)


def find_sops(query: str, retriever: RetrievalInterface | None = None) -> list[SopMatch]:
    """The best sections for the query, at most MAX_SECTIONS, each graded MIN_GRADE or higher."""
    hits = (retriever or get_retriever()).get_retrieve_data(query, 20)
    top_hits = hits[:MAX_SECTIONS]
    return [SopMatch(hit.section, int(hit.score)) for hit in top_hits if hit.score >= MIN_GRADE]


def tools_listed(section: SOPSection) -> list[str]:
    """The tool names on the section's "TOOLS:" line ("none" -> [])."""
    match = re.search(r"^TOOLS:(.*)$", section.text, re.M)
    names = [name.strip() for name in match.group(1).split(",")] if match else []
    return [name for name in names if name and name != "none"]


def allowed_tools(sops: list[SopMatch]) -> list[str]:
    """Union of the chosen sections' tools, in order of first appearance."""
    tools: list[str] = []
    for sop in sops:
        for name in tools_listed(sop.section):
            if name not in tools:
                tools.append(name)
    return tools


def support_guidelines() -> str:
    """Read on every run, so edits to the file apply without a restart."""
    return SUPPORT_GUIDELINES.read_text()
