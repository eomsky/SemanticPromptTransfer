from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .chat_routing import ChatIntent, ChatIntentRouter
from .credit_reasoning import CreditReasoningLayer
from .colab_runtime import EphemeralColabRuntime
from .config import DocumentScope
from .domain import CaseContext, CreditFact, DocumentKind, FileStatus, JobStage, ReviewItem, ReviewSectionDraft
from .evidence_capture import EvidenceCaptureService
from .fewshot import FewShotSelector
from .llm import TextGenerator
from .orchestration import ReviewGenerationOrchestrator, ReviewGenerationResult
from .poc_processing import PocUploadProcessor, ShardedAttachmentRetriever
from .review import ReviewPromptBuilder
from .review_docx import OpinionDocumentBuilder
from .verification_flow import LLMVerificationAgent, VerificationMode


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
        verification_mode: VerificationMode | str = VerificationMode.OFF,
        verification_generator: TextGenerator | None = None,
        reasoning_generator: TextGenerator | None = None,
        completion_generator: TextGenerator | None = None,
        prompt_builder: ReviewPromptBuilder | None = None,
    ) -> None:
        self.runtime = runtime
        self.facts = PocCreditFactRepository(runtime)
        self.generator = generator
        self.upload_processor = upload_processor
        self.loan_type = loan_type
        self.industry_code = industry_code
        self.company_name = company_name
        self.capture_service = EvidenceCaptureService(runtime)
        self.retriever = retriever
        self.chat_router = ChatIntentRouter()
        mode = VerificationMode(str(getattr(verification_mode, "value", verification_mode)).upper())
        verifier = LLMVerificationAgent(verification_generator or generator) if mode is not VerificationMode.OFF else None
        reasoner = CreditReasoningLayer(reasoning_generator or generator)
        self.orchestrator = ReviewGenerationOrchestrator(
            retriever,
            few_shots,
            registry=runtime.registry,
            llm=generator,
            prompt_builder=prompt_builder,
            document_builder=OpinionDocumentBuilder(capture_service=self.capture_service),
            verification_mode=mode,
            verifier=verifier,
            reasoner=reasoner,
            completion_generator=completion_generator or generator,
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
                if document.status in {FileStatus.VALIDATING, FileStatus.PARSING, FileStatus.INDEXING}:
                    deadline = time.time() + 900.0
                    while time.time() < deadline:
                        current = self.runtime.registry.get_document(job.tenant_id, job.case_id, document.document_id)
                        if current.status not in {FileStatus.VALIDATING, FileStatus.PARSING, FileStatus.INDEXING}:
                            document = current
                            break
                        time.sleep(0.2)
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
                    evidence_refs=list(validation.get("evidence_refs") or []),
                    validation=validation,
                )

            def on_claim(item: ReviewItem, payload: dict[str, Any]) -> None:
                event = dict(payload)
                event_type = str(event.pop("type", "claim_complete"))
                event.pop("review_item", None)
                self._publish(job_id, event_type, review_item=item.value, **event)

            def on_patch(item: ReviewItem, payload: dict[str, Any]) -> None:
                event = dict(payload)
                event_type = str(event.pop("type", "claim_patch"))
                event.pop("review_item", None)
                self._publish(job_id, event_type, review_item=item.value, **event)

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
                claim_callback=on_claim,
                patch_callback=on_patch,
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
                    "일시적인 처리 문제로 해당 심사항목의 문구를 확정하지 못했습니다.",
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
                    "기술 복구를 포함해 심사의견 생성을 완료했습니다.", str(target)
                )
            except Exception:
                pass
            self._publish(
                job_id, "complete", stage=JobStage.COMPLETE_WITH_WARNINGS.value, progress=100,
                message="기술 복구를 포함해 심사의견 생성을 완료했습니다.",
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
        intent = self.chat_router.route(message)
        with self._condition:
            result = self._results[job_id]
            history = [dict(row) for row in self._chat_history.get(job_id, [])]
            evidence_rows = list(self._evidence.get(job_id, {}).values())
        job = self.runtime.registry.get_job(job_id)

        if intent is ChatIntent.GENERAL:
            system = (
                "당신은 기업여신 업무를 지원하는 대화형 에이전트다. 자유대화에는 심사의견용 few-shot, "
                "검증 LLM, repair/fallback 규칙을 적용하지 않는다. 일반 질문에는 업로드 자료의 근거를 "
                "억지로 요구하지 말고 자연스럽고 완결된 답변을 한다. 사용자가 현재 심사건을 명시적으로 "
                "지칭할 때에만 해당 건의 자료를 근거로 답한다."
            )
        elif intent is ChatIntent.CASE_QA:
            blocks = []
            try: facts = self.facts.load(job.tenant_id, job.case_id)
            except Exception: facts = []
            for fact in facts[:60]:
                rendered = f"신용조사서 | {fact.field_name}={fact.value}"
                if fact.unit: rendered += f" {fact.unit}"
                if fact.period: rendered += f" | 기간={fact.period}"
                blocks.append(rendered)
            try:
                retrieval = self.retriever.search(message, filters={"tenant_id": job.tenant_id, "case_id": job.case_id})
            except Exception:
                retrieval = {"hits": []}
            for hit in retrieval.get("hits", [])[:12]:
                metadata = dict(hit.get("metadata") or {})
                pages = ','.join(str(v) for v in (metadata.get("pages") or [])) or '-'
                blocks.append(f"첨부자료 | {metadata.get('source_filename') or '업로드 자료'} | 페이지={pages}\n{str(hit.get('document') or hit.get('embedding_text') or '')}")
            context = "\n\n".join(blocks)[:18000]
            system = (
                "당신은 현재 심사건을 질의응답하는 기업여신 심사지원 에이전트다. 이 경로에는 검증 LLM을 "
                "연결하지 않는다. 아래 자료는 사용자의 현재 질문으로 새로 검색한 query-time RAG 결과다. "
                "자료가 뒷받침하는 내용은 구체적으로 답하고 자료에 없는 사실을 지어내지 않는다. 내부 근거 키는 출력하지 않는다.\n\n"
                "[현재 질문 기반 심사건 자료]\n" + (context or "관련 자료가 검색되지 않음")
            )
        else:
            opinion = "\n\n".join(f"{section.review_item.value}. {section.review_item.title}\n{self._visible_text(section.text)}" for section in result.sections)
            evidence_blocks = []; used = 0
            for row in evidence_rows:
                content = str(row.get("content") or "").strip()
                if not content: continue
                block = f"자료: {row.get('source_filename') or '업로드 자료'} / 위치: {row.get('location') or row.get('page') or '-'}\n{content}"
                if used + len(block) > 12000: break
                evidence_blocks.append(block); used += len(block)
            system = (
                "당신은 이미 생성된 심사의견을 설명하는 기업여신 심사지원 에이전트다. 자유대화에는 검증 LLM, "
                "few-shot, 심사의견 생성 repair를 적용하지 않는다. 아래 완료 의견과 그 생성 과정의 근거를 사용해 "
                "사용자가 묻는 판단 이유나 근거를 설명한다. 내부 근거 키는 출력하지 않는다.\n\n[완료된 심사의견]\n"
                + opinion + "\n\n[관련 근거]\n" + ("\n\n".join(evidence_blocks) if evidence_blocks else "별도 근거자료 없음")
            )

        selected = []; used_history = 0
        for row in reversed(history):
            size = len(row.get("content", ""))
            if used_history + size > 14000: break
            selected.append(row); used_history += size
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
            yield {"type": "chat_start", "agent": "심사지원 에이전트", "intent": self.chat_router.route(prompt).value}
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
                answer = "응답 생성이 일시적으로 중단되었습니다. 같은 질문을 다시 보내 주시면 이어서 답변하겠습니다."
                yield {"type": "chat_token", "token": answer}
            with self._condition:
                self._chat_history.setdefault(job_id, []).append(
                    {"role": "assistant", "content": answer}
                )
            yield {"type": "chat_complete", "text": answer, "agent": "심사지원 에이전트"}
        finally:
            chat_lock.release()
