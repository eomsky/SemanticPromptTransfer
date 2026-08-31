from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import DocumentKind, FileStatus, JobStage


ALLOWED_FILE_TRANSITIONS = {
    FileStatus.UPLOADED: {FileStatus.VALIDATING, FileStatus.DELETING, FileStatus.FAILED},
    FileStatus.VALIDATING: {FileStatus.PARSING, FileStatus.FAILED, FileStatus.DELETING},
    FileStatus.PARSING: {FileStatus.INDEXING, FileStatus.READY, FileStatus.FAILED, FileStatus.DELETING},
    FileStatus.INDEXING: {FileStatus.READY, FileStatus.FAILED, FileStatus.DELETING},
    FileStatus.READY: {FileStatus.DELETING, FileStatus.FAILED},
    FileStatus.FAILED: {FileStatus.VALIDATING, FileStatus.DELETING},
    FileStatus.DELETING: {FileStatus.DELETED, FileStatus.FAILED},
    FileStatus.DELETED: set(),
}


@dataclass(frozen=True)
class DocumentRecord:
    tenant_id: str
    case_id: str
    document_id: str
    filename: str
    document_kind: DocumentKind
    status: FileStatus
    source_hash: str | None
    size_bytes: int | None
    created_at: float
    updated_at: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value["document_kind"] = self.document_kind.value
        value["status"] = self.status.value
        return value


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    tenant_id: str
    case_id: str
    stage: JobStage
    progress: int
    message: str
    output_path: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value["stage"] = self.stage.value
        return value


class OperationalRegistry:
    """SQLite metadata registry for uploaded files and generation jobs."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                tenant_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                document_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                source_hash TEXT,
                size_bytes INTEGER,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, case_id, document_id)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL,
                message TEXT NOT NULL,
                output_path TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        self.connection.commit()

    def _audit(self, tenant_id: str, case_id: str, entity_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, tenant_id, case_id, entity_id, event_type, json.dumps(payload, ensure_ascii=False), time.time()),
        )

    def register_document(
        self,
        *,
        tenant_id: str,
        case_id: str,
        document_id: str,
        filename: str,
        document_kind: DocumentKind,
        source_hash: str | None = None,
        size_bytes: int | None = None,
    ) -> DocumentRecord:
        now = time.time()
        self.connection.execute(
            """
            INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(tenant_id, case_id, document_id) DO UPDATE SET
                filename=excluded.filename,
                document_kind=excluded.document_kind,
                status=excluded.status,
                source_hash=excluded.source_hash,
                size_bytes=excluded.size_bytes,
                error=NULL,
                updated_at=excluded.updated_at
            """,
            (
                tenant_id, case_id, document_id, filename, document_kind.value,
                FileStatus.UPLOADED.value, source_hash, size_bytes, now, now,
            ),
        )
        self._audit(tenant_id, case_id, document_id, "DOCUMENT_REGISTERED", {"filename": filename})
        self.connection.commit()
        return self.get_document(tenant_id, case_id, document_id)

    def get_document(self, tenant_id: str, case_id: str, document_id: str) -> DocumentRecord:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE tenant_id=? AND case_id=? AND document_id=?",
            (tenant_id, case_id, document_id),
        ).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._document(row)

    def list_documents(self, tenant_id: str, case_id: str, include_deleted: bool = False) -> list[DocumentRecord]:
        query = "SELECT * FROM documents WHERE tenant_id=? AND case_id=?"
        params: list[Any] = [tenant_id, case_id]
        if not include_deleted:
            query += " AND status<>?"
            params.append(FileStatus.DELETED.value)
        query += " ORDER BY created_at, document_id"
        return [self._document(row) for row in self.connection.execute(query, params)]

    def transition_document(
        self,
        tenant_id: str,
        case_id: str,
        document_id: str,
        status: FileStatus,
        error: str | None = None,
    ) -> DocumentRecord:
        current = self.get_document(tenant_id, case_id, document_id)
        if status not in ALLOWED_FILE_TRANSITIONS[current.status]:
            raise ValueError(f"invalid file transition: {current.status.value} -> {status.value}")
        now = time.time()
        self.connection.execute(
            "UPDATE documents SET status=?, error=?, updated_at=? WHERE tenant_id=? AND case_id=? AND document_id=?",
            (status.value, error, now, tenant_id, case_id, document_id),
        )
        self._audit(tenant_id, case_id, document_id, "DOCUMENT_STATUS", {"from": current.status.value, "to": status.value, "error": error})
        self.connection.commit()
        return self.get_document(tenant_id, case_id, document_id)

    def create_job(self, tenant_id: str, case_id: str) -> JobRecord:
        now = time.time()
        job_id = uuid.uuid4().hex
        self.connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (job_id, tenant_id, case_id, JobStage.QUEUED.value, 0, "대기 중", now, now),
        )
        self._audit(tenant_id, case_id, job_id, "JOB_CREATED", {})
        self.connection.commit()
        return self.get_job(job_id)

    def update_job(
        self,
        job_id: str,
        stage: JobStage,
        progress: int,
        message: str,
        output_path: str | None = None,
    ) -> JobRecord:
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        current = self.get_job(job_id)
        if progress < current.progress and stage is not JobStage.FAILED:
            raise ValueError("job progress cannot decrease")
        self.connection.execute(
            "UPDATE jobs SET stage=?, progress=?, message=?, output_path=COALESCE(?, output_path), updated_at=? WHERE job_id=?",
            (stage.value, progress, message, output_path, time.time(), job_id),
        )
        self._audit(current.tenant_id, current.case_id, job_id, "JOB_PROGRESS", {"stage": stage.value, "progress": progress})
        self.connection.commit()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord:
        row = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return JobRecord(
            job_id=row["job_id"], tenant_id=row["tenant_id"], case_id=row["case_id"],
            stage=JobStage(row["stage"]), progress=int(row["progress"]), message=row["message"],
            output_path=row["output_path"], created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _document(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            tenant_id=row["tenant_id"], case_id=row["case_id"], document_id=row["document_id"],
            filename=row["filename"], document_kind=DocumentKind(row["document_kind"]),
            status=FileStatus(row["status"]), source_hash=row["source_hash"], size_bytes=row["size_bytes"],
            error=row["error"], created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
        )
