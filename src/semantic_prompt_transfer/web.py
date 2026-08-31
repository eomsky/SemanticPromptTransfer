import hashlib
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .application import OperationalApplicationService
from .config import DocumentScope
from .domain import DocumentKind, FileStatus
from .poc_identity import PocIdentityService
from .poc_session import PocSession, PocSessionManager
from .storage import LocalDocumentArtifactStore
from .version import PACKAGE_VERSION


class UploadProcessor(Protocol):
    def process(
        self,
        scope: DocumentScope,
        source_path: Path,
        document_kind: DocumentKind,
        progress: Callable[[FileStatus, int | None, str | None], None],
    ) -> None: ...


class ReviewJobStarter(Protocol):
    def start(self, tenant_id: str, case_id: str) -> dict[str, Any]: ...

    def run(self, job_id: str) -> Any: ...


def create_fastapi_app(
    application: OperationalApplicationService,
    storage: LocalDocumentArtifactStore,
    upload_processor: UploadProcessor,
    *,
    review_jobs: ReviewJobStarter | None = None,
    session_manager: PocSessionManager | PocIdentityService | None = None,
    runtime_health: Callable[[], dict[str, Any]] | None = None,
    purge_case: Callable[[str, str], dict[str, Any]] | None = None,
    allowed_origins: Sequence[str] = (),
    max_upload_bytes: int = 50 * 1024 * 1024,
    download_root: str | Path | None = None,
    credit_template_download: str | Path | None = None,
):
    """Create the time-boxed POC API while preserving the v0.21 route contract."""
    try:
        from fastapi import (
            BackgroundTasks,
            Body,
            FastAPI,
            File,
            Header,
            HTTPException,
            Query,
            UploadFile,
        )
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("install semantic-prompt-transfer[poc]") from exc

    if max_upload_bytes < 1:
        raise ValueError("max_upload_bytes must be positive")
    output_root = Path(download_root).expanduser().resolve() if download_root else storage.root
    template_download = (
        Path(credit_template_download).expanduser().resolve()
        if credit_template_download
        else None
    )
    app = FastAPI(title="SemanticPromptTransfer Colab POC API", version=PACKAGE_VERSION)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-POC-Token"],
        )

    def authorize(
        token: str | None,
        *,
        tenant_id: str | None = None,
        case_id: str | None = None,
    ) -> tuple[str, str, PocSession | None]:
        if session_manager is None:
            if not tenant_id or not case_id:
                raise HTTPException(status_code=422, detail="tenant_id and case_id are required")
            return tenant_id, case_id, None
        try:
            session = session_manager.require(token, tenant_id=tenant_id, case_id=case_id)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return session.tenant_id, session.case_id, session

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
        payload = await file.read(max_upload_bytes + 1)
        if not payload:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(payload) > max_upload_bytes:
            raise HTTPException(status_code=413, detail="upload exceeds the POC size limit")
        document_id = uuid.uuid4().hex
        scope = DocumentScope(tenant_id, case_id, document_id)
        filename = Path(file.filename or "upload.bin").name
        suffix = Path(filename).suffix.lower()
        allowed = (
            {".xlsx"}
            if document_kind is DocumentKind.CREDIT_REPORT
            else {".pdf", ".docx", ".xlsx", ".txt", ".md"}
        )
        if suffix not in allowed:
            raise HTTPException(status_code=415, detail=f"unsupported upload type: {suffix}")
        source_path = storage.put(scope, filename, payload)
        try:
            row = application.register_upload(
                scope,
                filename=filename,
                document_kind=document_kind,
                size_bytes=len(payload),
                storage_uri=str(source_path),
                source_hash=hashlib.sha256(payload).hexdigest(),
                derived_uri=str(storage.derived_path(scope)),
            )
        except Exception:
            source_path.unlink(missing_ok=True)
            raise
        background_tasks.add_task(process_upload, scope, source_path, document_kind)
        return row

    @app.get("/api/v1/runtime/health")
    def health() -> dict[str, Any]:
        value = runtime_health() if runtime_health else {
            "status": "ready",
            "version": PACKAGE_VERSION,
            "storage_mode": "LOCAL",
        }
        value = dict(value)
        value["authentication"] = "POC_SESSION" if session_manager else "DEPLOYMENT_DEFINED"
        value["active_sessions"] = session_manager.active_count() if session_manager else None
        value["registered_users"] = (
            session_manager.user_count() if isinstance(session_manager, PocIdentityService) else None
        )
        return value

    @app.post("/api/v1/poc/users", status_code=201)
    def register_user(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not isinstance(session_manager, PocIdentityService):
            raise HTTPException(status_code=501, detail="POC identity service is not configured")
        try:
            return session_manager.register(
                department=str(payload.get("department") or ""),
                name=str(payload.get("name") or ""),
                employee_number=str(payload.get("employee_number") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    @app.post("/api/v1/poc/login")
    def login(payload: dict[str, Any] = Body(...)) -> dict[str, object]:
        if not isinstance(session_manager, PocIdentityService):
            raise HTTPException(status_code=501, detail="POC identity service is not configured")
        try:
            return session_manager.login(
                str(payload.get("user_id") or ""),
                str(payload.get("password") or ""),
            ).to_dict()
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/api/v1/poc/me")
    def current_user(x_poc_token: str | None = Header(None)) -> dict[str, object]:
        _, _, session = authorize(x_poc_token)
        assert session is not None
        return session.to_dict()

    @app.get("/api/v1/templates/credit-report.xlsx")
    def download_credit_template(x_poc_token: str | None = Header(None)):
        authorize(x_poc_token)
        if template_download is None or not template_download.is_file():
            raise HTTPException(status_code=404, detail="credit-report template is not configured")
        return FileResponse(
            template_download,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=template_download.name,
        )

    @app.post("/api/v1/poc/sessions", status_code=201)
    def create_session(payload: dict[str, Any] = Body(...)) -> dict[str, object]:
        if session_manager is None or not isinstance(session_manager, PocSessionManager):
            raise HTTPException(status_code=501, detail="POC session manager is not configured")
        try:
            grant = session_manager.create(
                str(payload.get("access_code") or ""),
                label=str(payload.get("label") or "POC tester"),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return grant.to_dict()

    @app.delete("/api/v1/poc/sessions/current")
    def close_session(x_poc_token: str | None = Header(None)) -> dict[str, Any]:
        if session_manager is None:
            raise HTTPException(status_code=501, detail="POC session manager is not configured")
        _, _, session = authorize(x_poc_token)
        assert session is not None
        session_manager.revoke(x_poc_token or "")
        return {"status": "CLOSED", "session_id": session.session_id, "purged": False}

    @app.post("/api/v1/cases/{case_id}/credit-report", status_code=202)
    async def upload_credit_report(
        case_id: str,
        background_tasks: BackgroundTasks,
        tenant_id: str | None = Query(None),
        x_poc_token: str | None = Header(None),
        file: UploadFile = File(...),
    ):
        tenant, case, _ = authorize(
            x_poc_token, tenant_id=tenant_id, case_id=case_id
        )
        return await accept_upload(
            tenant, case, DocumentKind.CREDIT_REPORT, file, background_tasks
        )

    @app.post("/api/v1/cases/{case_id}/attachments", status_code=202)
    async def upload_attachment(
        case_id: str,
        background_tasks: BackgroundTasks,
        tenant_id: str | None = Query(None),
        x_poc_token: str | None = Header(None),
        file: UploadFile = File(...),
    ):
        tenant, case, _ = authorize(
            x_poc_token, tenant_id=tenant_id, case_id=case_id
        )
        return await accept_upload(
            tenant, case, DocumentKind.ATTACHMENT, file, background_tasks
        )

    @app.get("/api/v1/cases/{case_id}/documents")
    def list_documents(
        case_id: str,
        tenant_id: str | None = Query(None),
        x_poc_token: str | None = Header(None),
    ):
        tenant, case, _ = authorize(
            x_poc_token, tenant_id=tenant_id, case_id=case_id
        )
        return application.list_uploads(tenant, case)

    @app.delete("/api/v1/cases/{case_id}/documents/{document_id}")
    def delete_document(
        case_id: str,
        document_id: str,
        tenant_id: str | None = Query(None),
        x_poc_token: str | None = Header(None),
    ):
        tenant, case, _ = authorize(
            x_poc_token, tenant_id=tenant_id, case_id=case_id
        )
        try:
            return application.delete_upload(DocumentScope(tenant, case, document_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/cases/{case_id}/review-jobs", status_code=202)
    def start_review(
        case_id: str,
        background_tasks: BackgroundTasks,
        tenant_id: str | None = Query(None),
        x_poc_token: str | None = Header(None),
    ):
        tenant, case, _ = authorize(
            x_poc_token, tenant_id=tenant_id, case_id=case_id
        )
        if review_jobs is None:
            raise HTTPException(status_code=501, detail="review job starter is not configured")
        try:
            job = review_jobs.start(tenant, case)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        background_tasks.add_task(review_jobs.run, str(job["job_id"]))
        return job

    def authorize_job(job_id: str, token: str | None):
        try:
            job = application.registry.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="review job not found") from exc
        authorize(token, tenant_id=job.tenant_id, case_id=job.case_id)
        return job

    @app.get("/api/v1/review-jobs/{job_id}")
    def review_status(job_id: str, x_poc_token: str | None = Header(None)):
        authorize_job(job_id, x_poc_token)
        return application.get_review_job(job_id)

    @app.get("/api/v1/review-jobs/{job_id}/opinion.docx")
    def download_opinion(job_id: str, x_poc_token: str | None = Header(None)):
        job = authorize_job(job_id, x_poc_token)
        if job.progress != 100 or not job.output_path:
            raise HTTPException(status_code=409, detail="opinion document is not ready")
        target = Path(job.output_path).expanduser().resolve()
        if not target.is_relative_to(output_root):
            raise HTTPException(status_code=403, detail="opinion path is outside the POC runtime")
        if not target.is_file():
            raise HTTPException(status_code=410, detail="opinion document is unavailable")
        return FileResponse(
            target,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=target.name,
        )

    return app
