from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any


class RepresentationLevel(IntEnum):
    PLAIN = 0
    STRUCTURED = 1
    HIERARCHICAL = 2


class ArtifactMode(str, Enum):
    LOAD = "LOAD"
    MEMORY = "MEMORY"
    WRITE = "WRITE"


class IndexWriteStrategy(str, Enum):
    REPLACE = "REPLACE"
    UPSERT = "UPSERT"


@dataclass(frozen=True)
class DocumentScope:
    """Operational isolation metadata added to every chunk in one index build."""

    tenant_id: str
    case_id: str
    document_id: str
    financial_scope: str = "unspecified"
    source_version: str | None = None
    source_filename: str | None = None
    document_kind: str = "attachment"
    loan_type: str | None = None
    industry_code: str | None = None
    tags: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, Any]:
        if not self.tenant_id or not self.case_id or not self.document_id:
            raise ValueError("tenant_id, case_id, and document_id are required")
        return {
            "tenant_id": self.tenant_id,
            "case_id": self.case_id,
            "document_id": self.document_id,
            "financial_scope": self.financial_scope,
            "source_version": self.source_version,
            "source_filename": self.source_filename,
            "document_kind": self.document_kind,
            "loan_type": self.loan_type,
            "industry_code": self.industry_code,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class PipelineConfig:
    """Cells 4–7 runtime configuration.

    Level 0 and in-memory indexing are deliberate production defaults.  Levels
    1 and 2 require an explicit value and remain experimental.
    """

    model_dir: str | Path
    representation_level: int | RepresentationLevel = RepresentationLevel.PLAIN
    master_mode: str | ArtifactMode = ArtifactMode.LOAD
    index_mode: str | ArtifactMode = ArtifactMode.MEMORY
    index_path: str | Path | None = None
    index_write_strategy: str | IndexWriteStrategy = IndexWriteStrategy.REPLACE
    max_chars: int = 1800
    text_overlap_chars: int = 180
    table_overlap_rows: int = 0
    encoder_backend: str = "e5_onnx"
    batch_size: int = 24
    max_length: int = 512
    top_k: int = 5
    max_context_chars: int = 24000

    def __post_init__(self) -> None:
        level = RepresentationLevel(int(self.representation_level))
        master_mode = ArtifactMode(str(getattr(self.master_mode, "value", self.master_mode)).upper())
        index_mode = ArtifactMode(str(getattr(self.index_mode, "value", self.index_mode)).upper())
        write_strategy = IndexWriteStrategy(
            str(getattr(self.index_write_strategy, "value", self.index_write_strategy)).upper()
        )
        if master_mode is not ArtifactMode.LOAD:
            raise ValueError("Cells 1-3 are frozen: master_mode must be LOAD")
        if index_mode in {ArtifactMode.LOAD, ArtifactMode.WRITE} and self.index_path is None:
            raise ValueError("index_path is required for LOAD or WRITE index mode")
        if self.max_chars < 256:
            raise ValueError("max_chars must be at least 256")
        if not 0 <= self.text_overlap_chars < self.max_chars:
            raise ValueError("text_overlap_chars must be smaller than max_chars")
        if self.top_k < 1:
            raise ValueError("top_k must be positive")
        object.__setattr__(self, "representation_level", level)
        object.__setattr__(self, "master_mode", master_mode)
        object.__setattr__(self, "index_mode", index_mode)
        object.__setattr__(self, "index_write_strategy", write_strategy)
        object.__setattr__(self, "model_dir", Path(self.model_dir))
        object.__setattr__(self, "index_path", Path(self.index_path) if self.index_path else None)

    @property
    def is_experimental_level(self) -> bool:
        return self.representation_level is not RepresentationLevel.PLAIN

    @classmethod
    def for_index_build(
        cls, *, model_dir: str | Path, index_path: str | Path, **kwargs: Any
    ) -> "PipelineConfig":
        return cls(model_dir=model_dir, index_mode=ArtifactMode.WRITE, index_path=index_path, **kwargs)

    @classmethod
    def for_serving(
        cls, *, model_dir: str | Path, index_path: str | Path, **kwargs: Any
    ) -> "PipelineConfig":
        return cls(model_dir=model_dir, index_mode=ArtifactMode.LOAD, index_path=index_path, **kwargs)
