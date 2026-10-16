"""The contract every SOP retriever implements, and the result type it returns."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ai_analyzer.knowledge_retriever.evaluation import (
    DEFAULT_EVAL_FILES,
    DEFAULT_MAX_WORKERS,
    DEFAULT_TOP_K,
    EvalReport,
    run_retrieval_evals,
)
from ai_analyzer.sops.sop_parser import SOPParser, SOPSection


@dataclass(frozen=True)
class RetrievedSOP:
    section: SOPSection
    score: float  # retriever-specific scale: compare scores within one retriever only
    rank: int     # 1 = best match


class RetrievalInterface(ABC):
    """A retriever finds the SOP sections that best match a customer's message.

    Composition: every retriever *has* an SOPParser and gets sections only from it,
    never by reading SOP files itself.
    """

    def __init__(self, sop_parser: SOPParser):
        self.sop_parser = sop_parser

    @abstractmethod
    def get_retrieve_data(self, query: str, number_of_items: int) -> list[RetrievedSOP]:
        """Best-matching sections for `query`, best first, at most `number_of_items`."""

    def run_evals(
        self,
        eval_files: tuple[str, ...] = DEFAULT_EVAL_FILES,
        top_k: int = DEFAULT_TOP_K,
        results_file: Path | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> EvalReport:
        """Score this retriever on the eval sets. Shared by every retriever: see evaluation.py."""
        return run_retrieval_evals(
            self, eval_files=eval_files, top_k=top_k, results_file=results_file, max_workers=max_workers
        )
