"""Text embeddings with automatic fallback.

Primary: an embedding model (Ollama `nomic-embed-text` by default).
Fallback: a local feature-hashing embedder (lexical, no model needed) so memory
search keeps working even when no embedding model is installed. Each stored vector
remembers which model produced it, and vectors are re-embedded once the real model
becomes available.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
import unicodedata

import httpx
import numpy as np

log = logging.getLogger("cazung.embed")

HASH_DIM = 1024
HASH_MODEL_ID = f"hash-{HASH_DIM}"
_WORD = re.compile(r"\w+", re.UNICODE)


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(strip_accents(text.lower())) if len(w) > 1 or w.isdigit()]


def _hash_vec(text: str) -> np.ndarray:
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    words = tokenize(text)
    feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    counts: dict[str, int] = {}
    for f in feats:
        counts[f] = counts.get(f, 0) + 1
    for f, n in counts.items():
        h = hashlib.blake2b(f.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % HASH_DIM
        sign = 1.0 if h[4] & 1 else -1.0
        weight = (1.0 + math.log(n)) * (0.7 if "_" in f else 1.0)
        vec[idx] += sign * weight
    return vec


def normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class Embedder:
    def __init__(self, cfg):
        e = cfg.embedding
        self.provider = e.provider.lower()
        self.base_url = (e.base_url or cfg.llm.base_url).rstrip("/")
        self.model = e.model
        self.api_key = e.api_key
        self._down_until = 0.0
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))

    @property
    def remote_id(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def active_id(self) -> str:
        if self.provider == "hash" or time.time() < self._down_until:
            return HASH_MODEL_ID
        return self.remote_id

    @property
    def is_lexical(self) -> bool:
        return self.active_id == HASH_MODEL_ID

    async def close(self):
        await self.client.aclose()

    async def embed(self, texts: list[str]) -> tuple[np.ndarray, str]:
        """Return (normalized matrix [n, d], model_id)."""
        if not texts:
            return np.zeros((0, HASH_DIM), dtype=np.float32), HASH_MODEL_ID
        if self.active_id != HASH_MODEL_ID:
            try:
                vecs = await self._remote(texts)
                return normalize(np.asarray(vecs, dtype=np.float32)), self.remote_id
            except Exception as e:  # noqa: BLE001
                log.warning("Embedding model unavailable (%s) -> using lexical hash embeddings for 5 min", e)
                self._down_until = time.time() + 300
        return normalize(np.stack([_hash_vec(t) for t in texts])), HASH_MODEL_ID

    async def _remote(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "ollama":
            r = await self.client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": texts}
            )
            if r.status_code == 404 and "not found" not in r.text:  # very old Ollama
                out = []
                for t in texts:
                    rr = await self.client.post(
                        f"{self.base_url}/api/embeddings", json={"model": self.model, "prompt": t}
                    )
                    rr.raise_for_status()
                    out.append(rr.json()["embedding"])
                return out
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code} {r.text[:200]}")
            return r.json()["embeddings"]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        r = await self.client.post(
            f"{self.base_url}/v1/embeddings", json={"model": self.model, "input": texts}, headers=headers
        )
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        return [d["embedding"] for d in r.json()["data"]]


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
