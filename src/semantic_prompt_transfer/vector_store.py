from __future__ import annotations

import json
from dataclasses import dataclass
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
