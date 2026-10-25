"""Text embeddings for semantic search over the knowledge base.

Two interchangeable providers (OPSPILOT_EMBEDDING_PROVIDER):

* ``hashing`` (default) — a deterministic, dependency-free embedding: tokens are normalised (camelCase split,
  light stemming, an ops-domain synonym table so "freezes" lands near "timeout"), weighted with sublinear TF and
  feature-hashed into 384 signed dimensions. It runs anywhere (CI, laptops without a GPU) and gives the same vector
  on every machine, so retrieval tests are exact. It is lexical at heart: good on runbooks that share vocabulary
  with the logs, weaker on paraphrases.
* ``fastembed`` — BAAI/bge-small-en-v1.5 via ONNX (pip install -r requirements-ml.txt). A real sentence-embedding
  model with the same 384 dimensions, so switching needs no migration — only a re-index, which happens
  automatically because every chunk records the model that embedded it.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings
from app.models.knowledge import EMBEDDING_DIMENSIONS


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_STOPWORDS_TEXT = (
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "after before when then than not no but if so do does did can"
)
STOPWORDS = frozenset(_STOPWORDS_TEXT.split())
# Customer language → engineering language. Applied after stemming; both forms are kept.
SYNONYMS = {
    "freez": "timeout",
    "hang": "timeout",
    "stuck": "timeout",
    "slow": "latency",
    "sluggish": "latency",
    "lag": "latency",
    "pay": "payment",
    "checkout": "payment",
    "card": "payment",
    "500": "error",
    "fail": "error",
    "broken": "error",
    "email": "smtp",
    "mail": "smtp",
    "login": "auth",
    "signin": "auth",
    "password": "auth",
    "db": "database",
    "postgr": "database",
    "queuepool": "connection",
    "pool": "connection",
}


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _WORD.findall(text):
        parts = _CAMEL.findall(raw) if not raw.islower() else [raw]
        for word in dict.fromkeys([raw.lower(), *(p.lower() for p in parts)]):  # ordered: bigrams must be stable
            if word in STOPWORDS or len(word) < 2:
                continue
            root = stem(word)
            tokens.append(root)
            if root in SYNONYMS:
                tokens.append(SYNONYMS[root])
    return tokens


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    return value % EMBEDDING_DIMENSIONS, 1.0 if (value >> 63) & 1 else -1.0


class HashingEmbedder:
    name = "hashing-v1"

    def embed_one(self, text: str) -> list[float]:
        tokens = tokenize(text)
        features = Counter(tokens)
        features.update(f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False))
        vec = [0.0] * EMBEDDING_DIMENSIONS
        for feature, tf in features.items():
            index, sign = _bucket(feature)
            weight = 1 + math.log(tf)
            vec[index] += sign * (weight if "_" not in feature else 0.5 * weight)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class FastEmbedEmbedder:
    name = "bge-small-en-v1.5"

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # optional dependency (requirements-ml.txt)

        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]


@lru_cache
def get_embedder() -> Embedder:
    provider = get_settings().embedding_provider
    if provider == "fastembed":
        return FastEmbedEmbedder()
    if provider == "hashing":
        return HashingEmbedder()
    raise ValueError(f"unknown embedding provider {provider!r}")
