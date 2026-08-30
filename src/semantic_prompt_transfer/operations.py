from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import DocumentScope, PipelineConfig
from .pipeline import RAGPipeline


class OfflineIndexBuilder:
    def __init__(self, config: PipelineConfig) -> None:
        if config.index_mode.value != "WRITE":
            raise ValueError("OfflineIndexBuilder requires WRITE index mode")
        self.pipeline = RAGPipeline(config)

    def build(self, master_path: str | Path, scope: DocumentScope) -> dict[str, Any]:
        return self.pipeline.prepare(master_path, scope).stats()


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
