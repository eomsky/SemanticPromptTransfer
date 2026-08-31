from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .colab_runtime import EphemeralColabRuntime
from .config import DocumentScope
from .domain import CaseContext, CreditFact, DocumentKind, FileStatus, JobStage, ReviewItem, ReviewSectionDraft
from .evidence_capture import EvidenceCaptureService
from .fewshot import FewShotSelector
from .llm import TextGenerator
from .orchestration import ReviewGenerationOrchestrator, ReviewGenerationResult
from .poc_processing import PocUploadProcessor, ShardedAttachmentRetriever
from .review_docx import OpinionDocumentBuilder


class PocCreditFactRepository:
    def __init__(self, runtime: EphemeralColabRuntime) -> None:
        self.runtime = runtime

    def load(self, tenant_id: str, case_id: str) -> list[CreditFact]:
        candidates = [
            row
            for row in self.runtime.registry.list_documents(tenant_id, case_id)
            if row.document_kind is DocumentKind.CREDIT_REPORT and row.status is FileStatus.READY
        ]
        if not candidates:
            return []
        if len(candidates) > 1:
            raise ValueError("at most one ready credit report is allowed")
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
        self._results: dict[str, ReviewGenerationResult] = {}
        self._chat_history: dict[str, list[dict[str, str]]] = {}
        self._chat_locks: dict[str, threading.Lock] = {}
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
        if len(credit_reports) > 1:
            raise ValueError("at most one uploaded credit report is allowed")
        if not credit_reports and not any(
            row.document_kind is DocumentKind.ATTACHMENT for row in documents
        ):
            raise ValueError("a credit report or at least one attachment is required")
        job = self.runtime.registry.create_job(tenant_id, case_id)
        with self._condition:
            self._events[job.job_id] = []
            self._evidence[job.job_id] = {}
            self._chat_history[job.job_id] = []
            self._chat_locks[job.job_id] = threading.Lock()
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
                if document.status in {FileStatus.READY, FileStatus.EXCLUDED, FileStatus.DELETING, FileStatus.DELETED}:
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
                    try:
                        self.runtime.application.update_upload(
                            scope,
                            FileStatus.EXCLUDED,
                            progress=100,
                            message="이 자료는 사용에서 제외하고 나머지 자료로 계속 진행합니다.",
                            error=str(exc),
                        )
                    except Exception:
                        pass
                    recorder = getattr(self.runtime.registry, "record_audit_event", None)
                    if callable(recorder):
                        try:
                            recorder(job.tenant_id, job.case_id, document.document_id, "UPLOAD_PROCESSING_EXCLUDED", {
                                "error_type": type(exc).__name__, "message": str(exc)[:1500]
                            })
                        except Exception:
                            pass
                    self._publish(
                        job_id,
                        "file_progress",
                        stage=JobStage.PRECHECK.value,
                        progress=min(4, self.runtime.registry.get_job(job_id).progress),
                        message=f"{document.filename}: 자료 사용 제외 · 계속 진행",
                        document_id=document.document_id,
                        filename=document.filename,
                        file_status=FileStatus.EXCLUDED.value,
                        file_progress=100,
                    )
                    continue

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
            try:
                credit_facts = self.facts.load(job.tenant_id, job.case_id)
            except Exception as exc:
                credit_facts = []
                recorder = getattr(self.runtime.registry, "record_audit_event", None)
                if callable(recorder):
                    try:
                        recorder(job.tenant_id, job.case_id, job_id, "CREDIT_FACT_LOAD_RECOVERED", {
                            "error_type": type(exc).__name__, "message": str(exc)[:1500]
                        })
                    except Exception:
                        pass
            result = self.orchestrator.generate(
                case,
                credit_facts,
                None,
                output,
                progress_callback=on_progress,
                token_callback=on_token,
                section_callback=on_section,
                job_id=job_id,
            )
            with self._condition:
                self._results[job_id] = result
            final_job = self.runtime.registry.get_job(job_id)
            self._publish(
                job_id,
                "complete",
                stage=final_job.stage.value,
                progress=100,
                message=final_job.message or "심사의견 생성이 완료되었습니다.",
                output_filename=Path(result.output_path).name,
                recovered=final_job.stage is JobStage.COMPLETE_WITH_WARNINGS,
            )
            return result
        except Exception as exc:
            # Last application-boundary safety net. Raw errors are audited only; the user receives
            # a conservative five-item document and a normal completion event.
            recorder = getattr(self.runtime.registry, "record_audit_event", None)
            if callable(recorder):
                try:
                    recorder(job.tenant_id, job.case_id, job_id, "JOB_BOUNDARY_EMERGENCY_RECOVERY", {
                        "error_type": type(exc).__name__, "message": str(exc)[:1500]
                    })
                except Exception:
                    pass
            sections = tuple(
                ReviewSectionDraft(
                    item,
                    "현재 처리 가능한 근거 범위가 제한되어 해당 심사항목은 추가 자료 확인이 필요하다.",
                    (),
                    {"valid": True, "issues": [], "cited_evidence_ids": [], "recovered": True},
                )
                for item in ReviewItem.ordered()
            )
            output = self.runtime.review_output_path(job.tenant_id, job.case_id, job_id)
            target = OpinionDocumentBuilder().build_minimal(case, sections, output)
            result = ReviewGenerationResult(job_id, sections, (), (), str(target))
            with self._condition:
                self._results[job_id] = result
            try:
                self.runtime.registry.update_job(
                    job_id, JobStage.COMPLETE_WITH_WARNINGS, 100,
                    "보수적 대체문구로 심사의견 생성을 완료했습니다.", str(target)
                )
            except Exception:
                pass
            self._publish(
                job_id, "complete", stage=JobStage.COMPLETE_WITH_WARNINGS.value, progress=100,
                message="보수적 대체문구로 심사의견 생성을 완료했습니다.",
                output_filename=Path(target).name, recovered=True,
            )
            return result
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

    @staticmethod
    def _visible_text(text: str) -> str:
        value = re.sub(r"\[?\s*(?:CR|ATT)_[a-f0-9]{20}\s*\]?", "", str(text), flags=re.I)
        value = re.sub(r"\s+([.,;:])", r"\1", value)
        return re.sub(r"[ \t]{2,}", " ", value).strip()

    def assert_chat_ready(self, job_id: str) -> None:
        job = self.runtime.registry.get_job(job_id)
        with self._condition:
            result = self._results.get(job_id)
        if job.stage not in {JobStage.COMPLETE, JobStage.COMPLETE_WITH_WARNINGS} or job.progress != 100 or result is None:
            raise RuntimeError("심사의견 스트림 완료 후에만 후속 대화를 시작할 수 있습니다.")

    def _chat_messages(self, job_id: str, message: str) -> list[dict[str, str]]:
        self.assert_chat_ready(job_id)
        with self._condition:
            result = self._results[job_id]
            history = [dict(row) for row in self._chat_history.get(job_id, [])]
            evidence_rows = list(self._evidence.get(job_id, {}).values())

        opinion = "\n\n".join(
            f"{section.review_item.value}. {section.review_item.title}\n"
            f"{self._visible_text(section.text)}"
            for section in result.sections
        )
        evidence_blocks: list[str] = []
        evidence_characters = 0
        for row in evidence_rows:
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            block = (
                f"자료: {row.get('source_filename') or '업로드 자료'}"
                f" / 위치: {row.get('location') or row.get('page') or '-'}\n"
                f"{content}"
            )
            if evidence_characters + len(block) > 10000:
                break
            evidence_blocks.append(block)
            evidence_characters += len(block)

        system = (
            "당신은 기업여신 검토를 지원하는 '심사지원 에이전트'다. "
            "심사의견 생성 이후의 자유로운 후속 대화이므로 심사항목 A-E 형식, "
            "few-shot 문체 예시, 심사의견 생성용 검증 절차를 적용하지 않는다. "
            "사용자의 질문과 요청에 자연스럽게 답하라. 심사건의 사실을 말할 때에는 "
            "아래 심사의견과 업로드 근거자료를 우선 사용하고, 근거에 없는 수치는 만들어내지 마라. "
            "내부 근거 키(CR_, ATT_)는 화면에 출력하지 마라. "
            "답변은 마지막 문장까지 완결하고 토큰 한도에서 문장을 중단하지 마라.\n\n"
            "[완료된 심사의견]\n"
            + opinion
            + "\n\n[업로드 근거자료]\n"
            + ("\n\n".join(evidence_blocks) if evidence_blocks else "별도 근거자료 없음")
        )

        # The UI has no turn limit. Only the request context is bounded so the
        # vLLM call remains inside the configured model context window.
        selected: list[dict[str, str]] = []
        used = 0
        for row in reversed(history):
            size = len(row.get("content", ""))
            if used + size > 14000:
                break
            selected.append(row)
            used += size
        selected.reverse()
        return [{"role": "system", "content": system}, *selected, {"role": "user", "content": message}]

    def stream_chat(self, job_id: str, message: str) -> Iterator[dict[str, Any]]:
        prompt = str(message or "").strip()
        if not prompt:
            raise ValueError("대화 메시지를 입력해 주세요.")
        self.assert_chat_ready(job_id)
        with self._condition:
            chat_lock = self._chat_locks.setdefault(job_id, threading.Lock())
        if not chat_lock.acquire(blocking=False):
            raise RuntimeError("이 심사건의 이전 답변이 아직 생성 중입니다.")
        try:
            messages = self._chat_messages(job_id, prompt)
            with self._condition:
                self._chat_history.setdefault(job_id, []).append(
                    {"role": "user", "content": prompt}
                )
            yield {"type": "chat_start", "agent": "심사지원 에이전트"}
            pieces: list[str] = []
            try:
                stream = getattr(self.generator, "stream", None)
                if callable(stream):
                    for token in stream(messages):
                        token_text = str(token)
                        if token_text:
                            pieces.append(token_text)
                            yield {"type": "chat_token", "token": token_text}
                else:
                    generated = self.generator.generate(messages)
                    if generated:
                        pieces.append(str(generated))
                        yield {"type": "chat_token", "token": str(generated)}
                answer = "".join(pieces).strip()
                if not answer:
                    raise RuntimeError("empty chat response")
            except Exception as exc:
                recorder = getattr(self.runtime.registry, "record_audit_event", None)
                if callable(recorder):
                    try:
                        job = self.runtime.registry.get_job(job_id)
                        recorder(job.tenant_id, job.case_id, job_id, "CHAT_GENERATION_RECOVERED", {
                            "error_type": type(exc).__name__, "message": str(exc)[:1000]
                        })
                    except Exception:
                        pass
                answer = "현재 완료된 심사의견과 확인된 근거자료 범위에서 답변을 이어가겠습니다. 요청사항은 추가 자료 확인이 필요한 부분을 구분하여 검토해야 합니다."
                yield {"type": "chat_token", "token": answer}
            with self._condition:
                self._chat_history.setdefault(job_id, []).append(
                    {"role": "assistant", "content": answer}
                )
            yield {"type": "chat_complete", "text": answer, "agent": "심사지원 에이전트"}
        finally:
            chat_lock.release()
