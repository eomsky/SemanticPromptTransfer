from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import DocumentScope, PipelineConfig
from .pipeline import RAGPipeline
from .domain import FileStatus
from .registry import OperationalRegistry
from .storage import DocumentArtifactStore
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
        artifact_store: DocumentArtifactStore,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.artifact_store = artifact_store

    def delete(self, scope: DocumentScope) -> dict[str, Any]:
        current = self.registry.get_document(scope.tenant_id, scope.case_id, scope.document_id)
        if current.status is FileStatus.DELETED:
            return {"status": "DELETED", "deleted_vectors": 0, "idempotent": True}
        self.registry.transition_document(
            scope.tenant_id,
            scope.case_id,
            scope.document_id,
            FileStatus.DELETING,
            progress=current.progress,
            message="원본 파일과 임베딩 벡터를 삭제하고 있습니다.",
        )
        try:
            deleted_vectors = self.vector_store.delete_document(scope)
            remaining_vectors = self.vector_store.count(
                {
                    "tenant_id": scope.tenant_id,
                    "case_id": scope.case_id,
                    "document_id": scope.document_id,
                }
            )
            if remaining_vectors:
                raise RuntimeError(
                    f"vector deletion verification failed: {remaining_vectors} points remain"
                )
            artifacts = self.artifact_store.delete(current)
            if not artifacts.original_absent:
                raise RuntimeError("uploaded file deletion verification failed")
            final = self.registry.transition_document(
                scope.tenant_id,
                scope.case_id,
                scope.document_id,
                FileStatus.DELETED,
                progress=100,
                message="원본 파일과 임베딩 벡터를 삭제했습니다.",
            )
            return {
                "status": final.status.value,
                "deleted_vectors": deleted_vectors,
                "remaining_vectors": remaining_vectors,
                "original_deleted": artifacts.original_deleted,
                "original_absent": artifacts.original_absent,
                "derived_deleted": artifacts.derived_deleted,
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
