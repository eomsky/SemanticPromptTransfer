from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .chunking import PackageChunkBuilder
from .config import ArtifactMode, DocumentScope, IndexWriteStrategy, PipelineConfig
from .encoding import EncoderBackend, EncoderRegistry
from .indexing import RAGIndex, index_write_lock
from .identity import global_chunk_id
from .prompting import PromptPackage, PromptPackageBuilder
from .retrieval import RetrievalEngine


from .version import PACKAGE_VERSION


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RAGPipeline:
    """Stateful Cells 4–7 pipeline.

    Build processes call ``prepare(master_path, scope)`` in MEMORY/WRITE mode.
    Online processes call ``prepare()`` once in LOAD mode and reuse the loaded
    encoder, chunks, lexical index, and dense matrix across requests.
    """

    def __init__(
        self,
        config: PipelineConfig,
        encoder: EncoderBackend | None = None,
    ) -> None:
        self.config = config
        self.encoder = encoder or EncoderRegistry.create(
            config.encoder_backend,
            model_dir=config.model_dir,
            batch_size=config.batch_size,
            max_length=config.max_length,
        )
        self.index: RAGIndex | None = None
        self.engine: RetrievalEngine | None = None
        self.started_at: float | None = None

    def _activate(self, index: RAGIndex) -> None:
        expected_level = int(self.config.representation_level)
        actual_level = int(index.metadata.get("representation_level", -1))
        if actual_level != expected_level:
            raise ValueError(f"index level {actual_level} does not match configured level {expected_level}")
        encoder_meta = self.encoder.metadata()
        indexed_encoder = index.metadata.get("encoder", {})
        for key in ("model_id", "model_sha256", "dimension"):
            if indexed_encoder.get(key) != encoder_meta.get(key):
                raise ValueError(f"index encoder mismatch: {key}")
        self.index = index
        self.engine = RetrievalEngine(index.records, index.embeddings, self.encoder)
        self.started_at = time.time()

    def prepare(
        self,
        master_path: str | Path | None = None,
        scope: DocumentScope | None = None,
    ) -> RAGIndex:
        if self.config.index_mode is ArtifactMode.LOAD:
            if master_path is not None or scope is not None:
                raise ValueError("LOAD serving mode reads only the persisted index")
            index = RAGIndex.load(self.config.index_path)
            self._activate(index)
            return index

        if master_path is None or scope is None:
            raise ValueError("MEMORY/WRITE build mode requires master_path and DocumentScope")
        source = Path(master_path)
        master = json.loads(source.read_text(encoding="utf-8"))
        builder = PackageChunkBuilder(
            representation_level=self.config.representation_level,
            max_chars=self.config.max_chars,
            text_overlap_chars=self.config.text_overlap_chars,
            table_overlap_rows=self.config.table_overlap_rows,
        )
        records = []
        scope_metadata = scope.as_metadata()
        for record in builder.build(master):
            local_chunk_id = record.chunk_id
            global_id = global_chunk_id(
                scope.tenant_id,
                scope.case_id,
                scope.document_id,
                local_chunk_id,
                int(self.config.representation_level),
            )
            records.append(
                replace(
                    record,
                    metadata={
                        **record.metadata,
                        **scope_metadata,
                        "local_chunk_id": local_chunk_id,
                        "global_chunk_id": global_id,
                    },
                )
            )
        embeddings = self.encoder.encode_documents(record.embedding_text for record in records)
        index = RAGIndex(
            records=records,
            embeddings=embeddings,
            metadata={
                "schema_version": "rag-index-1.0",
                "package_version": PACKAGE_VERSION,
                "representation_level": int(self.config.representation_level),
                "master_sha256": _sha256(source),
                "master_filename": source.name,
                "encoder": self.encoder.metadata(),
                "scope": scope_metadata,
                "record_count": len(records),
                "created_at_epoch": time.time(),
            },
        )
        if self.config.index_mode is ArtifactMode.WRITE:
            with index_write_lock(self.config.index_path):
                if (
                    self.config.index_write_strategy is IndexWriteStrategy.UPSERT
                    and self.config.index_path.is_file()
                ):
                    index = RAGIndex.load(self.config.index_path).upsert(index)
                index.save(self.config.index_path)
        self._activate(index)
        return index

    def delete_document(self, scope: DocumentScope) -> dict[str, Any]:
        if self.config.index_mode is not ArtifactMode.WRITE:
            raise ValueError("delete_document requires WRITE index mode")
        scope_metadata = scope.as_metadata()
        with index_write_lock(self.config.index_path):
            if not self.config.index_path.is_file():
                return {"deleted_chunks": 0, "scope": scope_metadata}
            current = RAGIndex.load(self.config.index_path)
            updated, deleted = current.delete_document(
                scope.tenant_id,
                scope.case_id,
                scope.document_id,
            )
            updated.save(self.config.index_path)
        self._activate(updated)
        return {"deleted_chunks": deleted, "scope": scope_metadata, "stats": updated.stats()}

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if self.engine is None:
            raise RuntimeError("pipeline is not prepared")
        request_trace = trace_id or uuid.uuid4().hex
        started = time.perf_counter()
        result = self.engine.retrieve(query, top_k=top_k or self.config.top_k, filters=filters)
        result["trace_id"] = request_trace
        result["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        result["representation_level"] = int(self.config.representation_level)
        result["index_schema_version"] = self.index.metadata.get("schema_version") if self.index else None
        return result

    def build_prompt(
        self,
        query_id: str,
        query: str,
        *,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> PromptPackage:
        retrieval = self.retrieve(query, top_k=top_k, filters=filters, trace_id=trace_id)
        builder = PromptPackageBuilder(max_context_chars=self.config.max_context_chars)
        return builder.build(
            query_id=query_id,
            query=query,
            retrieval=retrieval,
            representation_level=int(self.config.representation_level),
            manifest={
                "package_version": PACKAGE_VERSION,
                "trace_id": retrieval["trace_id"],
                "index": self.index.metadata if self.index else {},
                "retrieval_latency_ms": retrieval["latency_ms"],
            },
        )

    def health(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.engine is not None else "not_ready",
            "package_version": PACKAGE_VERSION,
            "representation_level": int(self.config.representation_level),
            "index_mode": self.config.index_mode.value,
            "index_stats": self.index.stats() if self.index else None,
            "uptime_seconds": round(time.time() - self.started_at, 3) if self.started_at else None,
        }
