from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ._chunk_builder_base import ChunkRecord


@dataclass(frozen=True)
class RAGIndex:
    records: list[ChunkRecord]
    embeddings: np.ndarray
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        matrix = np.asarray(self.embeddings, dtype=np.float32)
        if matrix.ndim != 2 or len(self.records) != len(matrix):
            raise ValueError("records and two-dimensional embeddings must align")
        object.__setattr__(self, "embeddings", matrix)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        rows = np.asarray(
            [json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) for record in self.records],
            dtype=np.str_,
        )
        metadata = np.asarray(json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":")), dtype=np.str_)
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, embeddings=self.embeddings, records=rows, metadata=metadata)
        temporary.replace(target)
        return target

    def upsert(self, newer: "RAGIndex") -> "RAGIndex":
        """Replace chunks from matching documents and retain other documents."""

        for key in ("representation_level",):
            if self.metadata.get(key) != newer.metadata.get(key):
                raise ValueError(f"cannot merge indexes with different {key}")
        for key in ("model_id", "model_sha256", "dimension"):
            if self.metadata.get("encoder", {}).get(key) != newer.metadata.get("encoder", {}).get(key):
                raise ValueError(f"cannot merge indexes with different encoder {key}")

        replacement_documents = {
            (
                record.metadata.get("tenant_id"),
                record.metadata.get("case_id"),
                record.metadata.get("document_id"),
            )
            for record in newer.records
        }
        keep_indices = [
            index
            for index, record in enumerate(self.records)
            if (
                record.metadata.get("tenant_id"),
                record.metadata.get("case_id"),
                record.metadata.get("document_id"),
            )
            not in replacement_documents
        ]
        records = [self.records[index] for index in keep_indices] + newer.records
        matrices = []
        if keep_indices:
            matrices.append(self.embeddings[np.asarray(keep_indices, dtype=np.int32)])
        if len(newer.embeddings):
            matrices.append(newer.embeddings)
        dimension = self.embeddings.shape[1]
        embeddings = np.vstack(matrices) if matrices else np.empty((0, dimension), dtype=np.float32)
        scopes = sorted(
            {
                (
                    str(record.metadata.get("tenant_id") or ""),
                    str(record.metadata.get("case_id") or ""),
                    str(record.metadata.get("document_id") or ""),
                )
                for record in records
            }
        )
        metadata = {
            **{
                key: value
                for key, value in self.metadata.items()
                if key not in {"scope", "master_filename", "master_sha256"}
            },
            "record_count": len(records),
            "document_scopes": [list(scope) for scope in scopes],
            "last_upsert": {
                "scope": newer.metadata.get("scope"),
                "master_filename": newer.metadata.get("master_filename"),
                "master_sha256": newer.metadata.get("master_sha256"),
                "created_at_epoch": newer.metadata.get("created_at_epoch"),
            },
        }
        return RAGIndex(records=records, embeddings=embeddings, metadata=metadata)

    @classmethod
    def load(cls, path: str | Path) -> "RAGIndex":
        source = Path(path)
        with np.load(source, allow_pickle=False) as payload:
            embeddings = np.asarray(payload["embeddings"], dtype=np.float32)
            rows = payload["records"].tolist()
            metadata = json.loads(str(payload["metadata"].item()))
        records = []
        for row in rows:
            value = json.loads(str(row))
            records.append(
                ChunkRecord(
                    chunk_id=value["chunk_id"],
                    embedding_text=value["embedding_text"],
                    document=value["document"],
                    metadata=value["metadata"],
                )
            )
        return cls(records=records, embeddings=embeddings, metadata=metadata)

    def stats(self) -> dict[str, Any]:
        return {
            "record_count": len(self.records),
            "dimension": int(self.embeddings.shape[1]) if len(self.embeddings) else 0,
            "representation_level": self.metadata.get("representation_level"),
            "document_scopes": sorted(
                {
                    (
                        str(record.metadata.get("tenant_id") or ""),
                        str(record.metadata.get("case_id") or ""),
                        str(record.metadata.get("document_id") or ""),
                    )
                    for record in self.records
                }
            ),
        }


@contextmanager
def index_write_lock(path: str | Path):
    """Advisory process lock used around read-modify-write index updates."""

    import fcntl

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
