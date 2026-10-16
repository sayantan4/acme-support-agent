"""Semantic retrieval with embeddings: matches meaning ("top up" ~ "deposit"), weaker on exact codes.

Every SOP section and every query is turned into a vector (a list of numbers) by an embedding
model. Texts with similar meaning get vectors pointing in a similar direction, so the best
sections are the ones whose vectors are closest to the query's (cosine similarity).
"""

import numpy as np

from ai_analyzer.clients import llm_client
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface, RetrievedSOP
from ai_analyzer.sops.sop_parser import SOPParser, SOPSection

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_BATCH_SIZE = 64  # texts per API request when indexing


class OpenAIEmbedder:
    """Turns text into embedding vectors. Corpus and queries must use the same model."""

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, client=None):
        self.model = model
        self.client = client  # None: the shared, measured client (ai_analyzer/clients/llm_client.py)

    def embed(self, texts: list[str]) -> np.ndarray:
        """One row per text, each row scaled to length 1 (so a dot product = cosine similarity)."""
        rows = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start:start + EMBEDDING_BATCH_SIZE]
            rows.extend(self._embed_batch(batch))  # returned in input order
        return normalize_rows(np.array(rows, dtype=np.float32))

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        if self.client is None:
            return llm_client.embed(batch, self.model)
        response = self.client.embeddings.create(model=self.model, input=batch)
        return [item.embedding for item in response.data]


def normalize_rows(vectors: np.ndarray) -> np.ndarray:
    """Scale each row to length 1; all-zero rows stay zero instead of dividing by 0."""
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(lengths == 0, 1, lengths)


class DenseRet(RetrievalInterface):
    """Composition: has an SOPParser (from the interface) and an embedder."""

    def __init__(self, sop_parser: SOPParser, embedder: OpenAIEmbedder | None = None):
        super().__init__(sop_parser)
        self.embedder = embedder or OpenAIEmbedder()
        self.sop_list: list[SOPSection] = []          # row i of section_vectors = self.sop_list[i]
        self.section_vectors: np.ndarray | None = None

    def index_sop(self) -> None:
        """Embed the full text of every retrievable section (one API call per batch)."""
        self.sop_list = self.sop_parser.parse()
        self.section_vectors = self.embedder.embed([section.text for section in self.sop_list])

    def get_sop_list(self) -> list[SOPSection]:
        if not self.sop_list:  # index lazily on first use
            self.index_sop()
        return self.sop_list

    def get_retrieve_data(self, query: str, number_of_items: int) -> list[RetrievedSOP]:
        sop_list = self.get_sop_list()
        if not query.strip():  # nothing to embed
            return []

        query_vector = self.embedder.embed([query])[0]
        similarities = self.section_vectors @ query_vector  # cosine similarity per section, -1..1
        k = min(number_of_items, len(sop_list))
        best_first = np.argsort(-similarities)[:k]
        return [
            RetrievedSOP(section=sop_list[index], score=float(similarities[index]), rank=rank)
            for rank, index in enumerate(best_first, start=1)
        ]
