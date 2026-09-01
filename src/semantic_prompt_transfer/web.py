import hashlib
import json
import threading
import uuid
from collections.abc import Iterator
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

    def stream_events(self, job_id: str, after: int = 0) -> Iterator[dict[str, Any]]: ...

    def get_evidence(self, job_id: str, evidence_id: str) -> dict[str, Any]: ...

    def capture_evidence(self, job_id: str, evidence_id: str) -> bytes: ...

    def assert_chat_ready(self, job_id: str) -> None: ...

    def stream_chat(self, job_id: str, message: str) -> Iterator[dict[str, Any]]: ...


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
    demo_credit_report_path: str | Path | None = None,
    demo_attachment_paths: Sequence[str | Path] = (),
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
        from fastapi.responses import FileResponse, Response, StreamingResponse
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
    demo_credit = (
        Path(demo_credit_report_path).expanduser().resolve()
        if demo_credit_report_path
        else None
    )
    demo_attachments = tuple(
        Path(value).expanduser().resolve() for value in demo_attachment_paths
    )
    demo_specs: list[tuple[DocumentKind, Path]] = []
    if demo_credit is not None:
        demo_specs.append((DocumentKind.CREDIT_REPORT, demo_credit))
    demo_specs.extend((DocumentKind.ATTACHMENT, path) for path in demo_attachments)
    for _, source in demo_specs:
        if not source.is_file():
            raise FileNotFoundError(f"demo source is missing: {source}")
    demo_seed_lock = threading.RLock()
    demo_seeded_cases: set[tuple[str, str]] = set()
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
            return tenant_id or "anonymous", case_id or "public", None
        try:
            session = session_manager.require(token, tenant_id=tenant_id, case_id=case_id)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return session.tenant_id, session.case_id, session

    def ensure_demo_seeded(tenant_id: str, case_id: str) -> list[dict[str, Any]]:
        if not demo_specs:
            return []
        key = (tenant_id, case_id)
        with demo_seed_lock:
            if key in demo_seeded_cases:
                return []
            if application.registry.list_documents(tenant_id, case_id):
                demo_seeded_cases.add(key)
                return []
            seeded: list[dict[str, Any]] = []
            for document_kind, source in demo_specs:
                payload = source.read_bytes()
                document_id = f"demo-{document_kind.value}-{uuid.uuid4().hex}"
                scope = DocumentScope(tenant_id, case_id, document_id)
                stored = storage.put(scope, source.name, payload)
                try:
                    seeded.append(
                        application.register_upload(
                            scope,
                            filename=source.name,
                            document_kind=document_kind,
                            size_bytes=len(payload),
                            storage_uri=str(stored),
                            source_hash=hashlib.sha256(payload).hexdigest(),
                            derived_uri=str(storage.derived_path(scope)),
                        )
                    )
                    application.update_upload(
                        scope, FileStatus.VALIDATING, progress=1,
                        message="샘플 자료 분석을 준비합니다.",
                    )
                    threading.Thread(
                        target=process_upload, args=(scope, stored, document_kind), daemon=True
                    ).start()
                except Exception:
                    stored.unlink(missing_ok=True)
                    raise
            demo_seeded_cases.add(key)
            return seeded

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
                FileStatus.EXCLUDED,
                progress=100,
                message="이 자료는 사용에서 제외하고 나머지 자료로 계속 진행합니다.",
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
        try:
            application.update_upload(
                scope, FileStatus.VALIDATING, progress=1,
                message="업로드 완료 · 자료 분석을 준비합니다.",
            )
            background_tasks.add_task(process_upload, scope, source_path, document_kind)
            row = dict(row)
            row["status"] = FileStatus.VALIDATING.value
            row["progress_percent"] = 1
            row["progress_stage"] = "자료 분석 준비"
        except Exception:
            pass
        return row

    @app.get("/api/v1/runtime/health")
    def health() -> dict[str, Any]:
        value = runtime_health() if runtime_health else {
            "status": "ready",
            "version": PACKAGE_VERSION,
            "storage_mode": "LOCAL",
        }
        value = dict(value)
        value["authentication"] = "POC_SESSION" if session_manager else "ANONYMOUS_POC"
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
        ensure_demo_seeded(tenant, case)
        return application.list_uploads(tenant, case)

    @app.get("/api/v1/cases/{case_id}/documents/{document_id}/download")
    def download_document(
        case_id: str,
        document_id: str,
        tenant_id: str | None = Query(None),
        x_poc_token: str | None = Header(None),
    ):
        tenant, case, _ = authorize(
            x_poc_token, tenant_id=tenant_id, case_id=case_id
        )
        try:
            document = application.registry.get_document(tenant, case, document_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        if document.status is FileStatus.DELETED or not document.storage_uri:
            raise HTTPException(status_code=404, detail="document not found")
        source = Path(document.storage_uri).expanduser().resolve()
        if not source.is_relative_to(storage.root):
            raise HTTPException(status_code=409, detail="document path escaped runtime storage")
        if not source.is_file():
            raise HTTPException(status_code=404, detail="document file is missing")
        return FileResponse(source, filename=document.filename)

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

    @app.get("/api/v1/review-jobs/{job_id}/stream")
    def review_stream(
        job_id: str,
        after: int = Query(0, ge=0),
        x_poc_token: str | None = Header(None),
    ):
        authorize_job(job_id, x_poc_token)
        if review_jobs is None:
            raise HTTPException(status_code=501, detail="review job starter is not configured")

        def lines():
            for event in review_jobs.stream_events(job_id, after=after):
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"

        return StreamingResponse(
            lines(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/review-jobs/{job_id}/evidence/{evidence_id}")
    def evidence_metadata(
        job_id: str,
        evidence_id: str,
        x_poc_token: str | None = Header(None),
    ):
        authorize_job(job_id, x_poc_token)
        if review_jobs is None:
            raise HTTPException(status_code=501, detail="review job starter is not configured")
        try:
            return review_jobs.get_evidence(job_id, evidence_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc
        except (PermissionError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/review-jobs/{job_id}/evidence/{evidence_id}/capture.png")
    def evidence_capture(
        job_id: str,
        evidence_id: str,
        x_poc_token: str | None = Header(None),
    ):
        authorize_job(job_id, x_poc_token)
        if review_jobs is None:
            raise HTTPException(status_code=501, detail="review job starter is not configured")
        try:
            payload = review_jobs.capture_evidence(job_id, evidence_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc
        except (PermissionError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            payload,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=300"},
        )

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

    @app.post("/api/v1/review-jobs/{job_id}/chat/stream")
    def chat_stream(
        job_id: str,
        payload: dict[str, Any] = Body(...),
        x_poc_token: str | None = Header(None),
    ):
        authorize_job(job_id, x_poc_token)
        if review_jobs is None:
            raise HTTPException(status_code=501, detail="review job starter is not configured")
        message = str(payload.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="chat message is required")
        try:
            review_jobs.assert_chat_ready(job_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        def lines():
            try:
                for event in review_jobs.stream_chat(job_id, message):
                    yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            except Exception as exc:
                yield json.dumps(
                    {"type": "chat_error", "message": str(exc)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"

        return StreamingResponse(
            lines(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    return app
