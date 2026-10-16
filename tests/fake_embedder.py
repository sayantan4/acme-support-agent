"""A stand-in for the OpenAI embedder, so retriever tests need no network or API key."""

import re
import zlib

import numpy as np

from ai_analyzer.knowledge_retriever.dense.dense import normalize_rows


class BagOfWordsEmbedder:
    """Each word adds 1 to a fixed slot, so texts sharing words get similar vectors."""

    dimensions = 512

    def __init__(self):
        self.calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        vectors = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vectors[row, zlib.crc32(word.encode()) % self.dimensions] += 1
        return normalize_rows(vectors)
