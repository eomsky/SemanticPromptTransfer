from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .application import OperationalApplicationService
from .operations import DocumentLifecycleService
from .registry import OperationalRegistry
from .storage import LocalDocumentArtifactStore
from .vector_store import ShardedNpzVectorStore
from .version import PACKAGE_VERSION


@dataclass(frozen=True)
class EphemeralColabConfig:
    root: str | Path = "/content/spt_poc_runtime"
    max_lifetime_seconds: int = 12 * 60 * 60
    require_content_root: bool = True
    clean_start: bool = True

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve()
        if self.max_lifetime_seconds < 60:
            raise ValueError("runtime lifetime must be at least 60 seconds")
        lowered = {part.lower() for part in root.parts}
        if {"drive", "mydrive", "gdrive"} & lowered:
            raise ValueError("Google Drive paths are prohibited in ephemeral-only mode")
        content = Path("/content").resolve()
        if self.require_content_root and (not root.is_relative_to(content) or root == content):
            raise ValueError("Colab POC root must be a child of /content")
        if root == Path(root.anchor):
            raise ValueError("filesystem root cannot be used as a POC runtime root")
        object.__setattr__(self, "root", root)


class EphemeralColabRuntime:
    """Own all temporary POC state under one disposable Colab directory."""

    SENTINEL = ".spt_ephemeral_runtime"

    def __init__(self, config: EphemeralColabConfig | None = None) -> None:
        self.config = config or EphemeralColabConfig()
        self.root = Path(self.config.root)
        self._prepare_root()
        self.runtime_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.registry = OperationalRegistry(self.root / "metadata" / "poc.sqlite")
        self.artifacts = LocalDocumentArtifactStore(self.root / "artifacts")
        self.vectors = ShardedNpzVectorStore(self.root / "vectors")
        self.lifecycle = DocumentLifecycleService(self.registry, self.vectors, self.artifacts)
        self.application = OperationalApplicationService(self.registry, self.lifecycle)
        self._closed = False

    def _prepare_root(self) -> None:
        if self.root.exists() and any(self.root.iterdir()):
            sentinel = self.root / self.SENTINEL
            if not sentinel.is_file():
                raise RuntimeError("refusing to replace a non-POC runtime directory")
            if self.config.clean_start:
                shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "metadata").mkdir(exist_ok=True)
        (self.root / self.SENTINEL).write_text(
            "ephemeral-only; safe to remove when the POC runtime ends\n", encoding="utf-8"
        )

    @staticmethod
    def _segment(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    def review_output_path(self, tenant_id: str, case_id: str, job_id: str) -> Path:
        target = self.root.joinpath(
            "reviews", self._segment(tenant_id), self._segment(case_id), f"{job_id}.docx"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def health(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "closed", "version": PACKAGE_VERSION, "runtime_id": self.runtime_id}
        now = time.time()
        disk = shutil.disk_usage(self.root)
        return {
            "status": "ready",
            "version": PACKAGE_VERSION,
            "runtime_id": self.runtime_id,
            "storage_mode": "COLAB_EPHEMERAL_ONLY",
            "persistent_storage": False,
            "google_drive_mounted": False,
            "started_at": self.started_at,
            "expires_at": self.started_at + self.config.max_lifetime_seconds,
            "remaining_seconds": max(
                0, int(self.started_at + self.config.max_lifetime_seconds - now)
            ),
            "registry": self.registry.stats(),
            "vectors": self.vectors.count(),
            "disk_free_bytes": disk.free,
        }

    def purge_case(self, tenant_id: str, case_id: str) -> dict[str, Any]:
        vectors = self.vectors.delete_case(tenant_id, case_id)
        artifacts = self.artifacts.delete_case(tenant_id, case_id)
        review_dir = self.root.joinpath(
            "reviews", self._segment(tenant_id), self._segment(case_id)
        )
        reviews = LocalDocumentArtifactStore._delete_tree_files(review_dir)
        records = self.registry.delete_case_records(tenant_id, case_id)
        return {
            "tenant_id": tenant_id,
            "case_id": case_id,
            **vectors,
            "removed_artifacts": artifacts,
            "removed_reviews": reviews,
            "removed_records": records,
        }

    def close(self, *, purge: bool = True) -> dict[str, Any]:
        if self._closed:
            return {"status": "closed", "purged": not self.root.exists(), "idempotent": True}
        before = self.health()
        self.registry.close()
        self._closed = True
        if purge:
            sentinel = self.root / self.SENTINEL
            if not sentinel.is_file():
                raise RuntimeError("runtime sentinel is missing; refusing recursive cleanup")
            shutil.rmtree(self.root)
        return {
            "status": "closed",
            "purged": purge and not self.root.exists(),
            "idempotent": False,
            "previous": before,
        }

    def __enter__(self) -> "EphemeralColabRuntime":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close(purge=True)

