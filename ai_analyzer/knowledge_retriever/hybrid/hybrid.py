"""Hybrid retrieval: run BM25 and Dense, then merge their two rankings with Reciprocal Rank Fusion.

BM25 is good at exact words ("hacked", "UTR", "Form 16A"); Dense is good at meaning
("top up" ~ "deposit"). Each misses things the other finds, so we combine their lists.

Reciprocal Rank Fusion (RRF) only looks at each section's position in each list, never at the
raw scores. That matters because BM25 scores (0 to 30+) and cosine similarities (-1 to 1) are on
different scales and can't be added directly.

    RRF score of a section = sum over the lists of 1 / (RRF_K + its position in that list)

Example with RRF_K = 60: 1st in both lists -> 1/61 + 1/61 = 0.0328; 1st in one list only -> 0.0164;
3rd in both -> 1/63 + 1/63 = 0.0317. Being found by both retrievers counts more than being 1st
in just one of them.
"""

from collections import defaultdict

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface, RetrievedSOP
from ai_analyzer.sops.sop_parser import SOPSection

RRF_K = 60                     # the standard value from the original RRF paper (Cormack et al., 2009)
CANDIDATES_PER_RETRIEVER = 50  # how far down each list we read before merging


class HybridRet(RetrievalInterface):
    """Composition: has a DenseRet and a BM25Ret, both built on the same SOPParser."""

    def __init__(self, dense: DenseRet, bm25_ret: BM25Ret):
        if dense.sop_parser is not bm25_ret.sop_parser:
            raise ValueError("dense and bm25_ret must share one SOPParser, so their sections are the same")
        super().__init__(bm25_ret.sop_parser)
        self.dense = dense
        self.bm25_ret = bm25_ret

    def index_sop(self) -> None:
        self.bm25_ret.index_sop()
        self.dense.index_sop()

    def get_retrieve_data(self, query: str, number_of_items: int) -> list[RetrievedSOP]:
        # Read deeper than we return: a section 30th in one list can still win if both lists have it.
        depth = max(number_of_items, CANDIDATES_PER_RETRIEVER)
        bm25_results = self.bm25_ret.get_retrieve_data(query, depth)
        dense_results = self.dense.get_retrieve_data(query, depth)
        return reciprocal_rank_fusion([bm25_results, dense_results], number_of_items)


def reciprocal_rank_fusion(
    result_lists: list[list[RetrievedSOP]], number_of_items: int, rrf_k: int = RRF_K
) -> list[RetrievedSOP]:
    """Merge ranked lists into one: each section scores sum(1 / (rrf_k + rank)) over the lists."""
    fused_scores: dict[str, float] = defaultdict(float)
    sections: dict[str, SOPSection] = {}
    for results in result_lists:
        for item in results:
            fused_scores[item.section.sop_id] += 1 / (rrf_k + item.rank)
            sections[item.section.sop_id] = item.section

    # sorted() keeps the first list's order for equal scores, so ties go to BM25's ranking.
    best_first = sorted(fused_scores, key=lambda sop_id: -fused_scores[sop_id])[:number_of_items]
    return [
        RetrievedSOP(section=sections[sop_id], score=fused_scores[sop_id], rank=rank)
        for rank, sop_id in enumerate(best_first, start=1)
    ]
