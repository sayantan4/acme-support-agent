"""Keyword retrieval with BM25: good at exact terms (UTR, TDS, Form 16A), blind to paraphrase."""

import bm25s
from bm25s.tokenization import Tokenizer

from ai_analyzer.knowledge_retriever.interface import RetrievalInterface, RetrievedSOP
from ai_analyzer.sops.sop_parser import SOPParser, SOPSection


class BM25Tokenizer:
    """Turns text into BM25 tokens. Corpus and queries must share one vocabulary."""

    def __init__(self):
        # Default splitter keeps words of 2+ letters/digits, so codes like "16a" and "trc20" survive.
        self._tokenizer = Tokenizer(lower=True, stopwords="en")

    def tokenize_corpus(self, texts: list[str]):
        return self._tokenizer.tokenize(texts, update_vocab=True, return_as="tuple", show_progress=False)

    def tokenize_query(self, query: str) -> list[list[int]]:
        # update_vocab=False: words that appear in no SOP are dropped (they can't match anything).
        # allow_empty=False: if every word is dropped, return an empty list instead of a placeholder.
        return self._tokenizer.tokenize(
            [query], update_vocab=False, allow_empty=False, return_as="ids", show_progress=False
        )


class BM25Ret(RetrievalInterface):
    """Composition: has an SOPParser (from the interface), a BM25Tokenizer and a bm25s engine."""

    def __init__(self, sop_parser: SOPParser, tokenizer: BM25Tokenizer | None = None):
        super().__init__(sop_parser)
        self.tokenizer = tokenizer or BM25Tokenizer()
        self.ret = bm25s.BM25()
        self.sop_list: list[SOPSection] = []  # position i in the index = self.sop_list[i]

    def index_sop(self) -> None:
        """Build the index over the full text of every retrievable section."""
        self.sop_list = self.sop_parser.parse()
        corpus_tokens = self.tokenizer.tokenize_corpus([section.text for section in self.sop_list])
        self.ret.index(corpus_tokens, show_progress=False)

    def get_sop_list(self) -> list[SOPSection]:
        if not self.sop_list:  # index lazily on first use
            self.index_sop()
        return self.sop_list

    def get_retrieve_data(self, query: str, number_of_items: int) -> list[RetrievedSOP]:
        sop_list = self.get_sop_list()
        query_tokens = self.tokenizer.tokenize_query(query)
        if not query_tokens[0]:  # every query word was dropped: none appear in any SOP
            return []

        k = min(number_of_items, len(sop_list))
        doc_ids, scores = self.ret.retrieve(query_tokens, k=k, show_progress=False)
        return [
            RetrievedSOP(section=sop_list[doc_id], score=float(score), rank=rank)
            for rank, (doc_id, score) in enumerate(zip(doc_ids[0], scores[0]), start=1)
            if score > 0  # score 0 = no shared words: not a match, just padding up to k
        ]
