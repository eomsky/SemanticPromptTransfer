import hashlib
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from .application import OperationalApplicationService
from .config import DocumentScope
from .domain import DocumentKind, FileStatus
from .storage import LocalDocumentArtifactStore
from .version import PACKAGE_VERSION


class UploadProcessor(Protocol):
    """Adapter for the frozen PDF/Excel parsing and indexing implementation."""

    def process(
        self,
        scope: DocumentScope,
        source_path: Path,
        document_kind: DocumentKind,
        progress: Callable[[FileStatus, int | None, str | None], None],
    ) -> None: ...


class ReviewJobStarter(Protocol):
    def start(self, tenant_id: str, case_id: str) -> dict[str, Any]: ...


def create_fastapi_app(
    application: OperationalApplicationService,
    storage: LocalDocumentArtifactStore,
    upload_processor: UploadProcessor,
    *,
    review_jobs: ReviewJobStarter | None = None,
):
    """Create optional HTTP routes matching the approved HTML; install the `web` extra."""
    try:
        from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
        from fastapi.responses import FileResponse
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("install semantic-prompt-transfer[web]") from exc

    app = FastAPI(title="SemanticPromptTransfer operational API", version=PACKAGE_VERSION)

    def process_upload(
        scope: DocumentScope,
        source_path: Path,
        document_kind: DocumentKind,
    ) -> None:
        def progress(status: FileStatus, percent: int | None, message: str | None) -> None:
            application.update_upload(scope, status, progress=percent, message=message)

        try:
            upload_processor.process(scope, source_path, document_kind, progress)
        except Exception as exc:
            application.update_upload(
                scope,
                FileStatus.FAILED,
                progress=0,
                message="파일 처리에 실패했습니다.",
                error=str(exc),
            )

    async def accept_upload(
        tenant_id: str,
        case_id: str,
        document_kind: DocumentKind,
        file: UploadFile,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        payload = await file.read()
        if not payload:
            raise HTTPException(status_code=400, detail="empty upload")
        document_id = uuid.uuid4().hex
        scope = DocumentScope(tenant_id, case_id, document_id)
        filename = Path(file.filename or "upload.bin").name
        source_path = storage.put(scope, filename, payload)
        try:
            row = application.register_upload(
                scope,
                filename=filename,
                document_kind=document_kind,
                size_bytes=len(payload),
                storage_uri=str(source_path),
                source_hash=hashlib.sha256(payload).hexdigest(),
            )
        except Exception:
            source_path.unlink(missing_ok=True)
            raise
        background_tasks.add_task(process_upload, scope, source_path, document_kind)
        return row

    @app.post("/api/v1/cases/{case_id}/credit-report", status_code=202)
    async def upload_credit_report(
        case_id: str,
        background_tasks: BackgroundTasks,
        tenant_id: str = Query(...),
        file: UploadFile = File(...),
    ):
        return await accept_upload(
            tenant_id, case_id, DocumentKind.CREDIT_REPORT, file, background_tasks
        )

    @app.post("/api/v1/cases/{case_id}/attachments", status_code=202)
    async def upload_attachment(
        case_id: str,
        background_tasks: BackgroundTasks,
        tenant_id: str = Query(...),
        file: UploadFile = File(...),
    ):
        return await accept_upload(
            tenant_id, case_id, DocumentKind.ATTACHMENT, file, background_tasks
        )

    @app.get("/api/v1/cases/{case_id}/documents")
    def list_documents(case_id: str, tenant_id: str = Query(...)):
        return application.list_uploads(tenant_id, case_id)

    @app.delete("/api/v1/cases/{case_id}/documents/{document_id}")
    def delete_document(case_id: str, document_id: str, tenant_id: str = Query(...)):
        try:
            return application.delete_upload(DocumentScope(tenant_id, case_id, document_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/cases/{case_id}/review-jobs", status_code=202)
    def start_review(case_id: str, tenant_id: str = Query(...)):
        if review_jobs is None:
            raise HTTPException(status_code=501, detail="review job starter is not configured")
        return review_jobs.start(tenant_id, case_id)

    @app.get("/api/v1/review-jobs/{job_id}")
    def review_status(job_id: str):
        try:
            return application.get_review_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="review job not found") from exc

    @app.get("/api/v1/review-jobs/{job_id}/opinion.docx")
    def download_opinion(job_id: str):
        try:
            job = application.registry.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="review job not found") from exc
        if job.progress != 100 or not job.output_path:
            raise HTTPException(status_code=409, detail="opinion document is not ready")
        target = Path(job.output_path)
        if not target.is_file():
            raise HTTPException(status_code=410, detail="opinion document is unavailable")
        return FileResponse(
            target,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=target.name,
        )

    return app
