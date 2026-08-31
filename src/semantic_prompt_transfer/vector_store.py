from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Protocol

import numpy as np

from ._chunk_builder_base import ChunkRecord
from .config import DocumentScope


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    document: str
    embedding_text: str
    embedding: np.ndarray
    metadata: dict[str, Any]


class VectorStoreBackend(Protocol):
    def upsert_document(self, records: list[ChunkRecord], embeddings: np.ndarray) -> int: ...

    def delete_document(self, scope: DocumentScope) -> int: ...

    def count(self, filters: dict[str, Any] | None = None) -> int: ...


class InMemoryVectorStore:
    """Deterministic backend used for tests and adapter conformance."""

    def __init__(self) -> None:
        self._points: dict[str, VectorPoint] = {}

    def upsert_document(self, records: list[ChunkRecord], embeddings: np.ndarray) -> int:
        if len(records) != len(embeddings):
            raise ValueError("records and embeddings must align")
        if records:
            first = records[0].metadata
            scope = (first.get("tenant_id"), first.get("case_id"), first.get("document_id"))
            self._points = {
                key: point
                for key, point in self._points.items()
                if (
                    point.metadata.get("tenant_id"),
                    point.metadata.get("case_id"),
                    point.metadata.get("document_id"),
                )
                != scope
            }
        for record, embedding in zip(records, embeddings, strict=True):
            point_id = str(record.metadata.get("global_chunk_id") or "")
            if not point_id:
                raise ValueError("global_chunk_id is required for vector-store upsert")
            self._points[point_id] = VectorPoint(
                point_id=point_id,
                document=record.document,
                embedding_text=record.embedding_text,
                embedding=np.asarray(embedding, dtype=np.float32),
                metadata=dict(record.metadata),
            )
        return len(records)

    def delete_document(self, scope: DocumentScope) -> int:
        target = (scope.tenant_id, scope.case_id, scope.document_id)
        ids = [
            point_id
            for point_id, point in self._points.items()
            if (
                point.metadata.get("tenant_id"),
                point.metadata.get("case_id"),
                point.metadata.get("document_id"),
            )
            == target
        ]
        for point_id in ids:
            self._points.pop(point_id)
        return len(ids)

    def count(self, filters: dict[str, Any] | None = None) -> int:
        filters = filters or {}
        return sum(
            1
            for point in self._points.values()
            if all(point.metadata.get(key) == value for key, value in filters.items())
        )


class ShardedNpzVectorStore:
    """One logical exact-search store backed by replaceable document NPZ shards.

    The layout is designed for a time-boxed Colab runtime.  One upload rewrites
    only its own shard, and one delete never rewrites unrelated documents.
    """

    SENTINEL = ".spt_ephemeral_vector_store"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.SENTINEL).write_text("semantic-prompt-transfer\n", encoding="utf-8")
        self._lock = threading.RLock()

    @staticmethod
    def _scope(records: list[ChunkRecord]) -> DocumentScope:
        if not records:
            raise ValueError("at least one record is required for a document shard")
        values = {
            (
                str(row.metadata.get("tenant_id") or ""),
                str(row.metadata.get("case_id") or ""),
                str(row.metadata.get("document_id") or ""),
            )
            for row in records
        }
        if len(values) != 1:
            raise ValueError("one NPZ shard must contain exactly one document scope")
        tenant_id, case_id, document_id = values.pop()
        return DocumentScope(tenant_id, case_id, document_id)

    @staticmethod
    def _digest(scope: DocumentScope) -> str:
        raw = "\x1f".join((scope.tenant_id, scope.case_id, scope.document_id))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, scope: DocumentScope) -> Path:
        return self.root / f"{self._digest(scope)}.npz"

    @staticmethod
    def _metadata_json(records: list[ChunkRecord]) -> np.ndarray:
        return np.asarray(
            [json.dumps(row.metadata, ensure_ascii=False, separators=(",", ":")) for row in records],
            dtype=np.str_,
        )

    def upsert_document(self, records: list[ChunkRecord], embeddings: np.ndarray) -> int:
        if len(records) != len(embeddings):
            raise ValueError("records and embeddings must align")
        scope = self._scope(records)
        ids = [str(row.metadata.get("global_chunk_id") or "") for row in records]
        if not all(ids) or len(ids) != len(set(ids)):
            raise ValueError("unique global_chunk_id values are required")
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError("embeddings must be a two-dimensional matrix")
        target = self._path(scope)
        temporary = target.with_suffix(f".{os.getpid()}.tmp")
        with self._lock:
            try:
                with temporary.open("wb") as handle:
                    np.savez_compressed(
                        handle,
                        ids=np.asarray(ids, dtype=np.str_),
                        documents=np.asarray([row.document for row in records], dtype=np.str_),
                        embedding_texts=np.asarray(
                            [row.embedding_text for row in records], dtype=np.str_
                        ),
                        embeddings=matrix,
                        metadatas_json=self._metadata_json(records),
                    )
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return len(records)

    @staticmethod
    def _load(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
        with np.load(path, allow_pickle=False) as payload:
            ids = payload["ids"]
            documents = payload["documents"]
            embedding_texts = payload["embedding_texts"]
            embeddings = np.asarray(payload["embeddings"], dtype=np.float32)
            metadatas = [json.loads(str(value)) for value in payload["metadatas_json"]]
        lengths = {len(ids), len(documents), len(embedding_texts), len(embeddings), len(metadatas)}
        if len(lengths) != 1:
            raise RuntimeError(f"corrupt vector shard: {path.name}")
        return ids, documents, embedding_texts, embeddings, metadatas

    def _shards(self) -> list[Path]:
        return sorted(self.root.glob("*.npz"))

    def delete_document(self, scope: DocumentScope) -> int:
        target = self._path(scope)
        with self._lock:
            if not target.exists():
                return 0
            count = len(self._load(target)[0])
            target.unlink()
            if target.exists():
                raise RuntimeError("vector shard still exists after deletion")
            return count

    def count(self, filters: dict[str, Any] | None = None) -> int:
        wanted = filters or {}
        total = 0
        with self._lock:
            for shard in self._shards():
                metadatas = self._load(shard)[4]
                total += sum(
                    1
                    for metadata in metadatas
                    if all(metadata.get(key) == value for key, value in wanted.items())
                )
        return total

    def search(
        self,
        query_embedding: np.ndarray,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        query_norm = max(float(np.linalg.norm(query)), 1e-12)
        wanted = filters or {}
        hits: list[dict[str, Any]] = []
        with self._lock:
            for shard in self._shards():
                ids, documents, embedding_texts, embeddings, metadatas = self._load(shard)
                if embeddings.shape[1] != len(query):
                    raise ValueError("query and document embedding dimensions differ")
                row_norms = np.maximum(np.linalg.norm(embeddings, axis=1), 1e-12)
                scores = (embeddings @ query) / (row_norms * query_norm)
                for index, metadata in enumerate(metadatas):
                    if not all(metadata.get(key) == value for key, value in wanted.items()):
                        continue
                    hits.append(
                        {
                            "chunk_id": str(ids[index]),
                            "document": str(documents[index]),
                            "embedding_text": str(embedding_texts[index]),
                            "score": float(scores[index]),
                            "metadata": metadata,
                        }
                    )
        hits.sort(key=lambda row: (-row["score"], row["chunk_id"]))
        return hits[:top_k]

    def delete_case(self, tenant_id: str, case_id: str) -> dict[str, int]:
        removed_shards = 0
        removed_vectors = 0
        with self._lock:
            for shard in self._shards():
                metadatas = self._load(shard)[4]
                if not metadatas:
                    continue
                first = metadatas[0]
                if first.get("tenant_id") == tenant_id and first.get("case_id") == case_id:
                    removed_vectors += len(metadatas)
                    shard.unlink()
                    removed_shards += 1
        return {"removed_shards": removed_shards, "removed_vectors": removed_vectors}


def _chroma_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    converted: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            converted[key] = value
        else:
            converted[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return converted


class ChromaVectorStore:
    """Optional persistent Chroma adapter with document-scoped UPSERT and delete."""

    def __init__(self, persist_directory: str, collection_name: str = "semantic_prompt_transfer") -> None:
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install semantic-prompt-transfer[chroma]") from exc
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    @staticmethod
    def _where(scope: DocumentScope) -> dict[str, Any]:
        return {
            "$and": [
                {"tenant_id": {"$eq": scope.tenant_id}},
                {"case_id": {"$eq": scope.case_id}},
                {"document_id": {"$eq": scope.document_id}},
            ]
        }

    def upsert_document(self, records: list[ChunkRecord], embeddings: np.ndarray) -> int:
        if len(records) != len(embeddings):
            raise ValueError("records and embeddings must align")
        if not records:
            return 0
        first = records[0].metadata
        scope = DocumentScope(
            tenant_id=str(first["tenant_id"]),
            case_id=str(first["case_id"]),
            document_id=str(first["document_id"]),
        )
        self.collection.delete(where=self._where(scope))
        ids = [str(record.metadata["global_chunk_id"]) for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("global_chunk_id collision")
        self.collection.upsert(
            ids=ids,
            embeddings=np.asarray(embeddings, dtype=np.float32).tolist(),
            documents=[record.embedding_text for record in records],
            metadatas=[_chroma_metadata(record.metadata) for record in records],
        )
        return len(records)

    def delete_document(self, scope: DocumentScope) -> int:
        existing = self.collection.get(where=self._where(scope), include=[])
        ids = existing.get("ids", [])
        self.collection.delete(where=self._where(scope))
        return len(ids)

    def count(self, filters: dict[str, Any] | None = None) -> int:
        if not filters:
            return int(self.collection.count())
        clauses = [{key: {"$eq": value}} for key, value in filters.items()]
        where: dict[str, Any] = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        return len(self.collection.get(where=where, include=[]).get("ids", []))
