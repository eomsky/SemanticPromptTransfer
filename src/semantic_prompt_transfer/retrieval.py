from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from ._chunk_builder_base import ChunkRecord
from .encoding import EncoderBackend


def _clean(text: str) -> str:
    return " ".join(str(text or "").lower().replace("ㆍ", " ").split())


def lexical_tokens(text: str) -> list[str]:
    normalized = _clean(text)
    words = re.findall(r"[가-힣a-z]+|\d+(?:[.,]\d+)*", normalized)
    tokens = list(words)
    for word in words:
        compact = word.replace(",", "")
        if re.fullmatch(r"[가-힣a-z]+", compact) and len(compact) >= 2:
            tokens.extend(f"c2:{compact[i:i+2]}" for i in range(len(compact) - 1))
    return tokens


class BM25Index:
    def __init__(self, documents: Iterable[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [lexical_tokens(text) for text in documents]
        self.k1 = float(k1)
        self.b = float(b)
        self.lengths = np.asarray([len(doc) for doc in self.docs], dtype=np.float32)
        self.avgdl = float(self.lengths.mean()) if len(self.lengths) else 1.0
        self.term_frequencies = [Counter(doc) for doc in self.docs]
        df = Counter()
        for doc in self.docs:
            df.update(set(doc))
        count = max(len(self.docs), 1)
        self.idf = {
            term: math.log(1.0 + (count - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query: str) -> np.ndarray:
        query_terms = Counter(lexical_tokens(query))
        scores = np.zeros(len(self.docs), dtype=np.float32)
        for index, frequencies in enumerate(self.term_frequencies):
            dl = float(self.lengths[index])
            for term, query_frequency in query_terms.items():
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * dl / max(self.avgdl, 1e-9)
                )
                scores[index] += (
                    self.idf.get(term, 0.0)
                    * frequency
                    * (self.k1 + 1.0)
                    / denominator
                    * min(query_frequency, 2)
                )
        return scores


class CreditQueryPlanner:
    """Deterministic domain expansion; it does not generate facts or answers."""

    EXPANSIONS = {
        "현금및현금성자산": ["현금성자산", "순부채", "자금조달비율"],
        "단기차입금": ["차입금", "유동부채", "금융부채"],
        "특수관계자": ["관계기업", "종속기업", "채권 채무", "주요 잔액"],
        "소송": ["법적소송우발부채", "계류법원", "소송 상대방", "소송 금액", "진행상황"],
        "공급자금융약정": ["매입채무", "단기차입금", "장부금액", "지급기일"],
        "영업활동현금흐름": ["현금흐름표", "당기순이익", "운전자본", "비현금", "조정"],
    }

    def expand(self, query: str) -> str:
        additions = []
        for trigger, values in self.EXPANSIONS.items():
            if trigger in query:
                additions.extend(values)
        unique = list(dict.fromkeys(additions))
        return query if not unique else query + "\n검색개념: " + ", ".join(unique)


@dataclass(frozen=True)
class RetrievalHit:
    rank: int
    chunk: ChunkRecord
    score: float
    dense_score: float
    lexical_score: float
    dense_rank: int
    lexical_rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk.chunk_id,
            "variant_id": self.chunk.metadata.get("variant_id"),
            "score": self.score,
            "dense_score": self.dense_score,
            "lexical_score": self.lexical_score,
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "embedding_text": self.chunk.embedding_text,
            "document": self.chunk.document,
            "metadata": self.chunk.metadata,
        }


class RetrievalEngine:
    def __init__(
        self,
        chunks: list[ChunkRecord],
        embeddings: np.ndarray,
        encoder: EncoderBackend,
        rrf_k: int = 60,
        candidate_k: int = 50,
        query_planner: CreditQueryPlanner | None = None,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have equal length")
        self.chunks = chunks
        self.embeddings = np.asarray(embeddings, dtype=np.float32)
        self.encoder = encoder
        self.rrf_k = int(rrf_k)
        self.candidate_k = int(candidate_k)
        self.planner = query_planner or CreditQueryPlanner()
        self.lexical = BM25Index(chunk.embedding_text for chunk in chunks)

    @staticmethod
    def _ranks(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        order = np.argsort(-scores, kind="stable")
        ranks = np.empty(len(order), dtype=np.int32)
        ranks[order] = np.arange(1, len(order) + 1)
        return order, ranks

    @staticmethod
    def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
        for key, expected in filters.items():
            actual = metadata.get(key)
            expected_values = set(expected if isinstance(expected, (list, tuple, set)) else [expected])
            actual_values = set(actual if isinstance(actual, (list, tuple, set)) else [actual])
            if not (expected_values & actual_values):
                return False
        return True

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        expanded = self.planner.expand(query)
        query_vector = self.encoder.encode_queries([expanded])[0]
        active_filters = dict(filters or {})
        eligible = [
            index
            for index, chunk in enumerate(self.chunks)
            if self._matches_filters(chunk.metadata, active_filters)
        ]
        if not eligible:
            return {
                "query": query,
                "expanded_query": expanded,
                "top_k": int(top_k),
                "filters": active_filters,
                "eligible_chunk_count": 0,
                "hits": [],
            }
        eligible_array = np.asarray(eligible, dtype=np.int32)
        dense_scores = self.embeddings[eligible_array] @ query_vector
        lexical_scores = self.lexical.score(expanded)[eligible_array]
        dense_order, dense_ranks = self._ranks(dense_scores)
        lexical_order, lexical_ranks = self._ranks(lexical_scores)
        candidates = set(dense_order[: self.candidate_k]) | set(lexical_order[: self.candidate_k])
        fused = []
        amount_query = any(term in query for term in ("금액", "잔액", "비교", "현금", "차입금"))
        for local_index in candidates:
            global_index = eligible[local_index]
            score = 1.0 / (self.rrf_k + int(dense_ranks[local_index]))
            score += 1.0 / (self.rrf_k + int(lexical_ranks[local_index]))
            chunk = self.chunks[global_index]
            if amount_query and chunk.metadata.get("content_type") == "table":
                score += 0.0015
            if amount_query and "단위" in chunk.embedding_text:
                score += 0.0007
            fused.append((score, float(dense_scores[local_index]), float(lexical_scores[local_index]), global_index, local_index))
        fused.sort(key=lambda item: (-item[0], -item[1], item[3]))
        hits = []
        for rank, (score, dense, lexical, index, local_index) in enumerate(fused[:top_k], 1):
            hits.append(
                RetrievalHit(
                    rank=rank,
                    chunk=self.chunks[index],
                    score=float(score),
                    dense_score=dense,
                    lexical_score=lexical,
                    dense_rank=int(dense_ranks[local_index]),
                    lexical_rank=int(lexical_ranks[local_index]),
                )
            )
        return {
            "query": query,
            "expanded_query": expanded,
            "top_k": int(top_k),
            "filters": active_filters,
            "eligible_chunk_count": len(eligible),
            "hits": [hit.to_dict() for hit in hits],
        }
