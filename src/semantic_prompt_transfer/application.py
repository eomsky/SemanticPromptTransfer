from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import DocumentScope
from .domain import DocumentKind, FileStatus
from .operations import DocumentLifecycleService
from .registry import DocumentRecord, OperationalRegistry


class OperationalApplicationService:
    """Application boundary matching the approved minimal HTML interface."""

    def __init__(
        self,
        registry: OperationalRegistry,
        lifecycle: DocumentLifecycleService,
    ) -> None:
        self.registry = registry
        self.lifecycle = lifecycle

    @staticmethod
    def _file_row(document: DocumentRecord) -> dict[str, Any]:
        suffix = Path(document.filename).suffix.lstrip(".").upper() or "FILE"
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "document_kind": document.document_kind.value,
            "file_type": suffix,
            "size_bytes": document.size_bytes,
            "progress_percent": document.progress,
            "progress_stage": document.status.progress_stage,
            "processing_message": document.processing_message,
            "status": document.status.value,
            "is_demo": document.document_id.startswith("demo-"),
            "can_delete": document.status not in {FileStatus.DELETING, FileStatus.DELETED},
        }

    def register_upload(
        self,
        scope: DocumentScope,
        *,
        filename: str,
        document_kind: DocumentKind,
        size_bytes: int,
        storage_uri: str,
        source_hash: str | None = None,
        derived_uri: str | None = None,
    ) -> dict[str, Any]:
        if document_kind is DocumentKind.CREDIT_REPORT:
            existing = self.registry.list_documents(scope.tenant_id, scope.case_id)
            conflict = [
                row
                for row in existing
                if row.document_kind is DocumentKind.CREDIT_REPORT
                and row.status not in {FileStatus.EXCLUDED, FileStatus.DELETED}
                and row.document_id != scope.document_id
            ]
            if conflict:
                raise ValueError("one active credit report is allowed per review case")
        record = self.registry.register_document(
            tenant_id=scope.tenant_id,
            case_id=scope.case_id,
            document_id=scope.document_id,
            filename=filename,
            document_kind=document_kind,
            source_hash=source_hash,
            size_bytes=size_bytes,
            storage_uri=storage_uri,
            derived_uri=derived_uri,
        )
        return self._file_row(record)

    def update_upload(
        self,
        scope: DocumentScope,
        status: FileStatus,
        *,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        record = self.registry.transition_document(
            scope.tenant_id,
            scope.case_id,
            scope.document_id,
            status,
            error=error,
            progress=progress,
            message=message,
        )
        return self._file_row(record)

    def list_uploads(self, tenant_id: str, case_id: str) -> dict[str, Any]:
        documents = self.registry.list_documents(tenant_id, case_id)
        return {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "credit_report": [
                self._file_row(row)
                for row in documents
                if row.document_kind is DocumentKind.CREDIT_REPORT
            ],
            "attachments": [
                self._file_row(row)
                for row in documents
                if row.document_kind is DocumentKind.ATTACHMENT
            ],
        }

    def delete_upload(self, scope: DocumentScope) -> dict[str, Any]:
        result = self.lifecycle.delete(scope)
        result["document_id"] = scope.document_id
        result["removed_from_active_list"] = True
        return result

    def get_review_job(self, job_id: str) -> dict[str, Any]:
        return self.registry.get_job(job_id).to_dict()
