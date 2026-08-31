from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .colab_runtime import EphemeralColabRuntime
from .config import DocumentScope
from .domain import CaseContext, CreditFact, DocumentKind, FileStatus, JobStage, ReviewItem
from .evidence_capture import EvidenceCaptureService
from .fewshot import FewShotSelector
from .llm import TextGenerator
from .orchestration import ReviewGenerationOrchestrator, ReviewGenerationResult
from .poc_processing import PocUploadProcessor, ShardedAttachmentRetriever


class PocCreditFactRepository:
    def __init__(self, runtime: EphemeralColabRuntime) -> None:
        self.runtime = runtime

    def load(self, tenant_id: str, case_id: str) -> list[CreditFact]:
        candidates = [
            row
            for row in self.runtime.registry.list_documents(tenant_id, case_id)
            if row.document_kind is DocumentKind.CREDIT_REPORT and row.status is FileStatus.READY
        ]
        if len(candidates) != 1:
            raise ValueError("exactly one ready credit report is required")
        document = candidates[0]
        if not document.derived_uri:
            raise RuntimeError("credit report derived path is missing")
        path = Path(document.derived_uri) / "credit_facts.json"
        if not path.is_file():
            raise RuntimeError("credit report facts are unavailable")
        value = json.loads(path.read_text(encoding="utf-8"))
        return [CreditFact.from_dict(row) for row in value.get("facts", [])]


class EphemeralReviewJobService:
    """Run one five-item review and expose resumable live events and evidence captures."""

    def __init__(
        self,
        runtime: EphemeralColabRuntime,
        retriever: ShardedAttachmentRetriever,
        few_shots: FewShotSelector,
        generator: TextGenerator,
        upload_processor: PocUploadProcessor | None = None,
        *,
        loan_type: str = "운전자금",
        industry_code: str = "*",
        company_name: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.facts = PocCreditFactRepository(runtime)
        self.generator = generator
        self.upload_processor = upload_processor
        self.loan_type = loan_type
        self.industry_code = industry_code
        self.company_name = company_name
        self.capture_service = EvidenceCaptureService(runtime)
        self.orchestrator = ReviewGenerationOrchestrator(
            retriever,
            few_shots,
            registry=runtime.registry,
            llm=generator,
        )
        self._condition = threading.Condition(threading.RLock())
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._evidence: dict[str, dict[str, dict[str, Any]]] = {}
        self._terminal_jobs: set[str] = set()

    def _publish(self, job_id: str, event_type: str, **payload: Any) -> dict[str, Any]:
        with self._condition:
            events = self._events.setdefault(job_id, [])
            event = {
                "sequence": len(events) + 1,
                "type": event_type,
                "timestamp": time.time(),
                **payload,
            }
            events.append(event)
            self._condition.notify_all()
            return event

    def start(self, tenant_id: str, case_id: str) -> dict[str, object]:
        documents = self.runtime.registry.list_documents(tenant_id, case_id)
        if not documents:
            raise ValueError("uploaded documents are required")
        credit_reports = [row for row in documents if row.document_kind is DocumentKind.CREDIT_REPORT]
        if len(credit_reports) != 1:
            raise ValueError("exactly one uploaded credit report is required")
        blocked = [
            row.filename
            for row in documents
            if row.status in {FileStatus.FAILED, FileStatus.DELETING, FileStatus.DELETED}
        ]
        if blocked:
            raise ValueError("remove failed or deleting uploads first: " + ", ".join(blocked))
        job = self.runtime.registry.create_job(tenant_id, case_id)
        with self._condition:
            self._events[job.job_id] = []
            self._evidence[job.job_id] = {}
            self._terminal_jobs.discard(job.job_id)
        self._publish(
            job.job_id,
            "queued",
            stage=JobStage.QUEUED.value,
            progress=0,
            message="심사의견 생성 작업이 대기 중입니다.",
        )
        return job.to_dict()

    def run(self, job_id: str) -> ReviewGenerationResult:
        job = self.runtime.registry.get_job(job_id)
        case = CaseContext(
            job.tenant_id,
            job.case_id,
            self.loan_type,
            self.industry_code,
            self.company_name,
        )
        documents = self.runtime.registry.list_documents(job.tenant_id, job.case_id)
        try:
            for document_index, document in enumerate(documents):
                if document.status is FileStatus.READY:
                    continue
                if self.upload_processor is None:
                    raise RuntimeError("upload processor is required for deferred document processing")
                scope = DocumentScope(job.tenant_id, job.case_id, document.document_id)

                def progress(
                    status: FileStatus,
                    percent: int | None,
                    message: str | None,
                    *,
                    current_scope: DocumentScope = scope,
                    current_document_id: str = document.document_id,
                    current_filename: str = document.filename,
                    current_index: int = document_index,
                ) -> None:
                    self.runtime.application.update_upload(
                        current_scope, status, progress=percent, message=message
                    )
                    file_percent = status.default_progress if percent is None else int(percent)
                    total = max(1, len(documents))
                    overall = min(4, int(((current_index + file_percent / 100) / total) * 4))
                    status_message = message or status.progress_stage
                    current_job = self.runtime.registry.get_job(job_id)
                    if overall >= current_job.progress:
                        self.runtime.registry.update_job(
                            job_id,
                            JobStage.PRECHECK,
                            overall,
                            f"{current_filename}: {status_message}",
                        )
                    self._publish(
                        job_id,
                        "file_progress",
                        stage=JobStage.PRECHECK.value,
                        progress=overall,
                        message=f"{current_filename}: {status_message}",
                        document_id=current_document_id,
                        filename=current_filename,
                        file_status=status.value,
                        file_progress=file_percent,
                    )

                try:
                    self.upload_processor.process(
                        scope,
                        Path(document.storage_uri),
                        document.document_kind,
                        progress,
                    )
                except Exception as exc:
                    current = self.runtime.registry.get_document(
                        job.tenant_id, job.case_id, document.document_id
                    )
                    if current.status is not FileStatus.FAILED:
                        self.runtime.application.update_upload(
                            scope,
                            FileStatus.FAILED,
                            progress=0,
                            message="파일 처리에 실패했습니다.",
                            error=str(exc),
                        )
                    raise

            def on_progress(event) -> None:
                self._publish(job_id, "stage", **event.to_dict())

            def on_token(item: ReviewItem, token: str) -> None:
                self._publish(
                    job_id,
                    "token",
                    review_item=item.value,
                    token=token,
                )

            def on_section(
                item: ReviewItem,
                text: str,
                evidence: list[dict[str, Any]],
                validation: dict[str, Any],
            ) -> None:
                cited = [str(value) for value in validation.get("cited_evidence_ids", [])]
                with self._condition:
                    job_evidence = self._evidence.setdefault(job_id, {})
                    for row in evidence:
                        evidence_id = str(row.get("evidence_id") or "")
                        if evidence_id:
                            job_evidence[evidence_id] = dict(row)
                self._publish(
                    job_id,
                    "section_complete",
                    review_item=item.value,
                    title=item.title,
                    text=text,
                    evidence_ids=cited,
                    validation=validation,
                )

            output = self.runtime.review_output_path(job.tenant_id, job.case_id, job_id)
            result = self.orchestrator.generate(
                case,
                self.facts.load(job.tenant_id, job.case_id),
                None,
                output,
                progress_callback=on_progress,
                token_callback=on_token,
                section_callback=on_section,
                job_id=job_id,
            )
            self._publish(
                job_id,
                "complete",
                stage=JobStage.COMPLETE.value,
                progress=100,
                message="심사의견 생성이 완료되었습니다.",
                output_filename=Path(result.output_path).name,
            )
            return result
        except Exception as exc:
            current = self.runtime.registry.get_job(job_id)
            if current.stage is not JobStage.FAILED:
                self.runtime.registry.update_job(
                    job_id,
                    JobStage.FAILED,
                    min(current.progress, 99),
                    str(exc),
                )
            self._publish(
                job_id,
                "error",
                stage=JobStage.FAILED.value,
                progress=min(current.progress, 99),
                message=str(exc),
            )
            raise
        finally:
            with self._condition:
                self._terminal_jobs.add(job_id)
                self._condition.notify_all()

    def stream_events(self, job_id: str, after: int = 0) -> Iterator[dict[str, Any]]:
        self.runtime.registry.get_job(job_id)
        cursor = max(0, int(after))
        while True:
            with self._condition:
                events = [
                    event
                    for event in self._events.get(job_id, [])
                    if int(event["sequence"]) > cursor
                ]
                terminal = job_id in self._terminal_jobs
                if not events and not terminal:
                    self._condition.wait(timeout=15.0)
                    events = [
                        event
                        for event in self._events.get(job_id, [])
                        if int(event["sequence"]) > cursor
                    ]
                    terminal = job_id in self._terminal_jobs
            if events:
                for event in events:
                    cursor = int(event["sequence"])
                    yield dict(event)
                continue
            if terminal:
                return
            yield {
                "sequence": cursor,
                "type": "heartbeat",
                "timestamp": time.time(),
            }

    def get_evidence(self, job_id: str, evidence_id: str) -> dict[str, Any]:
        job = self.runtime.registry.get_job(job_id)
        with self._condition:
            evidence = self._evidence.get(job_id, {}).get(evidence_id)
        if evidence is None:
            raise KeyError(evidence_id)
        return self.capture_service.describe(
            job.tenant_id, job.case_id, evidence_id, evidence
        )

    def capture_evidence(self, job_id: str, evidence_id: str) -> bytes:
        job = self.runtime.registry.get_job(job_id)
        with self._condition:
            evidence = self._evidence.get(job_id, {}).get(evidence_id)
        if evidence is None:
            raise KeyError(evidence_id)
        return self.capture_service.capture_png(job.tenant_id, job.case_id, evidence)
