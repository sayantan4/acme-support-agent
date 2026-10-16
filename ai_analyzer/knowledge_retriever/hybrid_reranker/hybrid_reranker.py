"""Hybrid + reranker: take the hybrid's top candidates and let an LLM re-order them.

The hybrid (BM25 + Dense merged with RRF) usually gets the right section into its top 20, but
often not into 1st place: it only knows how well words or vectors match, not what the customer
actually needs. A reranker reads the email and every candidate together and grades each one:

    3 = the procedure for the customer's main problem
    2 = helps with the main problem or a secondary question
    1 = same topic, but not what the customer needs
    0 = unrelated (left out of the LLM's answer)

Candidates are sorted by grade; equal grades keep the hybrid's order. The top grade is also a
"does any SOP apply?" signal: 0 means the LLM found nothing that fits.

The prompt sent to the LLM is versioned in prompts.py (rerank-v1, rerank-v2, ...); this file
is the same for every version.
"""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ai_analyzer.clients.llm_client import LLMResult, ask
from ai_analyzer.knowledge_retriever.hybrid import HybridRet
from ai_analyzer.knowledge_retriever.hybrid_reranker.prompts import LATEST_PROMPT, RerankPrompt
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface, RetrievedSOP
from ai_analyzer.sops.sop_parser import SOPSection

CANDIDATES_TO_RERANK = 20
HIGHEST_GRADE = 3


@dataclass
class RerankUsage:
    """Running totals, so a run can report its token cost and how often the LLM's reply was unusable."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unusable_replies: int = 0


def ask_for_rerank(instructions: str, user_input: str) -> LLMResult:
    """The shared LLM call, labelled "rerank" in the metrics."""
    return ask(instructions, user_input, purpose="rerank")


class LLMReranker:
    """Grades candidate sections for one email with a single LLM call, using one prompt version."""

    def __init__(self, prompt: RerankPrompt = LATEST_PROMPT, ask_llm: Callable[[str, str], LLMResult] = ask_for_rerank):
        self.prompt = prompt    # which version from prompts.py (rerank-v1, rerank-v2, ...)
        self.ask_llm = ask_llm  # the shared OpenAI call from ai_analyzer/clients/llm_client.py; tests pass a fake
        self.usage = RerankUsage()
        self._usage_lock = threading.Lock()  # evals call grade() from several threads at once

    def grade(self, query: str, sections: list[SOPSection]) -> dict[str, int]:
        """sop_id -> grade (1-3) for the sections the LLM found useful. Missing = 0."""
        result = self.ask_llm(self.prompt.instructions, build_rerank_input(query, sections))
        grades = parse_grades(result.text, allowed_ids={section.sop_id for section in sections})

        with self._usage_lock:
            self.usage.calls += 1
            self.usage.input_tokens += result.input_tokens
            self.usage.output_tokens += result.output_tokens
            if grades is None:
                self.usage.unusable_replies += 1

        return grades if grades is not None else {}  # {} = every candidate gets 0: hybrid order kept


def build_rerank_input(query: str, sections: list[SOPSection]) -> str:
    candidates = "\n".join(describe_candidate(section) for section in sections)
    return f"Customer email:\n{query}\n\nCandidate SOP sections:\n{candidates}"


def describe_candidate(section: SOPSection) -> str:
    """A short card per section: the title and customer phrasings say what it is for.

    The full section (debugging steps, SQL, reply templates) would cost ~10x the tokens
    without helping the LLM decide whether it fits.
    """
    card = f"- {section.sop_id}: {section.title}"
    phrases = "; ".join(section.customer_may_say)
    if phrases:
        card += f"\n  customers say: {phrases}"
    return card


def parse_grades(reply: str, allowed_ids: set[str]) -> dict[str, int] | None:
    """Read the LLM's JSON reply. None if it is not a JSON object; unknown ids are dropped."""
    text = reply.strip()
    if text.startswith("```"):  # tolerate a ```json ... ``` fence
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    grades = {}
    for sop_id, grade in parsed.items():
        if sop_id in allowed_ids and isinstance(grade, (int, float)):
            grades[sop_id] = max(0, min(HIGHEST_GRADE, int(grade)))
    return grades


class HybridRetRanker(RetrievalInterface):
    """Composition: has a HybridRet (which has a BM25Ret and a DenseRet) and an LLMReranker."""

    def __init__(self, hybrid: HybridRet, reranker: LLMReranker | None = None):
        super().__init__(hybrid.sop_parser)
        self.hybrid = hybrid
        self.reranker = reranker or LLMReranker()

    def index_sop(self) -> None:
        self.hybrid.index_sop()

    def get_retrieve_data(self, query: str, number_of_items: int) -> list[RetrievedSOP]:
        candidates = self.hybrid.get_retrieve_data(query, max(number_of_items, CANDIDATES_TO_RERANK))
        if not candidates:
            return []

        grades = self.reranker.grade(query, [candidate.section for candidate in candidates])

        def grade_of(candidate: RetrievedSOP) -> int:
            return grades.get(candidate.section.sop_id, 0)

        # Highest grade first; sorted() is stable, so equal grades keep the hybrid's order.
        reordered = sorted(candidates, key=lambda candidate: -grade_of(candidate))[:number_of_items]
        return [
            RetrievedSOP(section=candidate.section, score=float(grade_of(candidate)), rank=rank)
            for rank, candidate in enumerate(reordered, start=1)
        ]
