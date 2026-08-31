from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .config import DocumentScope, PipelineConfig
from .pipeline import RAGPipeline
from .domain import FileStatus
from .registry import OperationalRegistry
from .vector_store import VectorStoreBackend


class OfflineIndexBuilder:
    def __init__(self, config: PipelineConfig) -> None:
        if config.index_mode.value != "WRITE":
            raise ValueError("OfflineIndexBuilder requires WRITE index mode")
        self.pipeline = RAGPipeline(config)

    def build(self, master_path: str | Path, scope: DocumentScope) -> dict[str, Any]:
        return self.pipeline.prepare(master_path, scope).stats()

    def delete(self, scope: DocumentScope) -> dict[str, Any]:
        return self.pipeline.delete_document(scope)


class OnlineRAGService:
    """Load once at process start, then reuse for all scoped requests."""

    def __init__(self, config: PipelineConfig) -> None:
        if config.index_mode.value != "LOAD":
            raise ValueError("OnlineRAGService requires LOAD index mode")
        self.pipeline = RAGPipeline(config)

    def start(self) -> dict[str, Any]:
        self.pipeline.prepare()
        return self.pipeline.health()

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self._require_operational_scope(kwargs.get("filters"))
        return self.pipeline.retrieve(query, **kwargs)

    def prompt(self, query_id: str, query: str, **kwargs: Any):
        self._require_operational_scope(kwargs.get("filters"))
        return self.pipeline.build_prompt(query_id, query, **kwargs)

    def health(self) -> dict[str, Any]:
        return self.pipeline.health()

    @staticmethod
    def _require_operational_scope(filters: dict[str, Any] | None) -> None:
        filters = filters or {}
        missing = [key for key in ("tenant_id", "case_id") if not filters.get(key)]
        if missing:
            raise ValueError(f"online service requires scope filters: {missing}")


class DocumentLifecycleService:
    """Coordinate file-list deletion with vector and derived-asset removal."""

    def __init__(
        self,
        registry: OperationalRegistry,
        vector_store: VectorStoreBackend,
        *,
        remove_original: Callable[[DocumentScope], None] | None = None,
        remove_derived: Callable[[DocumentScope], None] | None = None,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.remove_original = remove_original
        self.remove_derived = remove_derived

    def delete(self, scope: DocumentScope) -> dict[str, Any]:
        current = self.registry.get_document(scope.tenant_id, scope.case_id, scope.document_id)
        if current.status is FileStatus.DELETED:
            return {"status": "DELETED", "deleted_vectors": 0, "idempotent": True}
        self.registry.transition_document(
            scope.tenant_id, scope.case_id, scope.document_id, FileStatus.DELETING
        )
        try:
            deleted_vectors = self.vector_store.delete_document(scope)
            if self.remove_derived:
                self.remove_derived(scope)
            if self.remove_original:
                self.remove_original(scope)
            final = self.registry.transition_document(
                scope.tenant_id, scope.case_id, scope.document_id, FileStatus.DELETED
            )
            return {
                "status": final.status.value,
                "deleted_vectors": deleted_vectors,
                "idempotent": False,
            }
        except Exception as exc:
            self.registry.transition_document(
                scope.tenant_id,
                scope.case_id,
                scope.document_id,
                FileStatus.FAILED,
                error=str(exc),
            )
            raise
