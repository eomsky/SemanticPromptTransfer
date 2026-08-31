from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Protocol

from .registry import DocumentRecord
from .config import DocumentScope


@dataclass(frozen=True)
class ArtifactDeletionResult:
    original_deleted: bool
    original_absent: bool
    derived_deleted: int


class DocumentArtifactStore(Protocol):
    """Storage adapter used by the document lifecycle transaction boundary."""

    def delete(self, document: DocumentRecord) -> ArtifactDeletionResult: ...


class LocalDocumentArtifactStore:
    """Delete only paths contained by one configured operational storage root."""

    def __init__(self, root: str | Path, *, require_original: bool = True) -> None:
        self.root = Path(root).expanduser().resolve()
        self.require_original = require_original

    def _resolve(self, uri: str | None) -> Path | None:
        if not uri:
            return None
        candidate = Path(uri).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"artifact path escapes storage root: {uri}")
        return resolved

    @staticmethod
    def _scope_segment(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    def document_path(self, scope: DocumentScope) -> Path:
        return self.root.joinpath(
            self._scope_segment(scope.tenant_id),
            self._scope_segment(scope.case_id),
            self._scope_segment(scope.document_id),
        )

    def derived_path(self, scope: DocumentScope) -> Path:
        return self.document_path(scope) / "derived"

    def put(self, scope: DocumentScope, filename: str, content: bytes) -> Path:
        safe_name = Path(filename).name
        if safe_name in {"", ".", ".."}:
            raise ValueError("a valid filename is required")
        target_dir = self.document_path(scope)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / safe_name).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("upload path escapes storage root")
        target.write_bytes(content)
        return target

    def case_path(self, tenant_id: str, case_id: str) -> Path:
        return self.root.joinpath(
            self._scope_segment(tenant_id),
            self._scope_segment(case_id),
        )

    @staticmethod
    def _delete_tree_files(path: Path) -> int:
        if path.is_file():
            path.unlink()
            return 1
        if not path.exists():
            return 0
        count = 0
        files = sorted(
            (item for item in path.rglob("*") if item.is_file()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for item in files:
            item.unlink()
            count += 1
        directories = sorted(
            (item for item in path.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for item in directories:
            item.rmdir()
        path.rmdir()
        return count

    def delete(self, document: DocumentRecord) -> ArtifactDeletionResult:
        original = self._resolve(document.storage_uri)
        derived = self._resolve(document.derived_uri)

        if self.require_original and original is None:
            raise ValueError("storage_uri is required for operational file deletion")

        original_deleted = False
        if original is not None:
            if original.exists() and not original.is_file():
                raise ValueError("storage_uri must identify one uploaded file")
            if original.exists():
                original.unlink()
                original_deleted = True
            if original.exists():
                raise RuntimeError(f"uploaded file still exists after deletion: {original}")

        derived_deleted = self._delete_tree_files(derived) if derived is not None else 0
        return ArtifactDeletionResult(
            original_deleted=original_deleted,
            original_absent=original is None or not original.exists(),
            derived_deleted=derived_deleted,
        )

    def delete_case(self, tenant_id: str, case_id: str) -> int:
        """Remove the case-owned artifact subtree without touching other cases."""
        target = self.case_path(tenant_id, case_id).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError("invalid case artifact path")
        return self._delete_tree_files(target)
