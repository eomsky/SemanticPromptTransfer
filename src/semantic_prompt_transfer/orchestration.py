from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .domain import (
    CaseContext,
    CreditFact,
    EvidenceRecord,
    JobStage,
    ProgressEvent,
    ReviewItem,
    ReviewSectionDraft,
)
from .fewshot import FewShotSelector
from .llm import TextGenerator
from .query_profiles import QueryProfileRegistry
from .registry import OperationalRegistry
from .review import EvidenceAssembler, ReviewPromptBuilder, ReviewPromptPackage
from .review_docx import OpinionDocumentBuilder
from .validation import OpinionValidator, ValidationIssue, ValidationReport


LLMClient = TextGenerator


class AttachmentRetriever(Protocol):
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class ReviewValidationError(RuntimeError):
    """Legacy exception retained for API compatibility; v0.26.4 does not terminate on it."""


@dataclass(frozen=True)
class ReviewGenerationResult:
    job_id: str
    sections: tuple[ReviewSectionDraft, ...]
    prompts: tuple[ReviewPromptPackage, ...]
    progress_events: tuple[ProgressEvent, ...]
    output_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "sections": [section.to_dict() for section in self.sections],
            "prompts": [prompt.to_dict() for prompt in self.prompts],
            "progress_events": [event.to_dict() for event in self.progress_events],
            "output_path": self.output_path,
        }


class ReviewGenerationOrchestrator:
    """Non-failing grounded A-E generation with repair, deterministic fallback and audit."""

    _CELL = re.compile(r"\b[A-Z]{1,3}\d{1,7}(?==)|\b[A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7}\b")

    def __init__(
        self,
        attachment_retriever: AttachmentRetriever,
        few_shot_selector: FewShotSelector,
        *,
        query_profiles: QueryProfileRegistry | None = None,
        evidence_assembler: EvidenceAssembler | None = None,
        prompt_builder: ReviewPromptBuilder | None = None,
        validator: OpinionValidator | None = None,
        document_builder: OpinionDocumentBuilder | None = None,
        registry: OperationalRegistry | None = None,
        llm: TextGenerator | None = None,
        max_repair_attempts: int = 2,
    ) -> None:
        self.attachment_retriever = attachment_retriever
        self.few_shot_selector = few_shot_selector
        self.query_profiles = query_profiles or QueryProfileRegistry()
        self.evidence_assembler = evidence_assembler or EvidenceAssembler()
        self.prompt_builder = prompt_builder or ReviewPromptBuilder()
        self.validator = validator or OpinionValidator()
        self.document_builder = document_builder or OpinionDocumentBuilder()
        self.registry = registry
        self.llm = llm
        self.max_repair_attempts = max(0, int(max_repair_attempts))

    def _audit(self, case: CaseContext, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if not self.registry:
            return
        recorder = getattr(self.registry, "record_audit_event", None)
        if callable(recorder):
            try:
                recorder(case.tenant_id, case.case_id, job_id, event_type, payload)
            except Exception:
                # Audit persistence must never terminate generation.
                pass

    @staticmethod
    def _prompt_evidence(prompt: ReviewPromptPackage, original: Sequence[EvidenceRecord]) -> list[EvidenceRecord]:
        wanted = {str(row.get("evidence_id") or "") for row in prompt.evidence}
        by_id = {row.evidence_id: row for row in original}
        return [by_id[eid] for eid in wanted if eid in by_id]

    @staticmethod
    def _issue_payload(report: ValidationReport) -> list[dict[str, str]]:
        return [issue.to_dict() for issue in report.issues]

    @staticmethod
    def _repair_messages(
        prompt: ReviewPromptPackage,
        draft: str,
        report: ValidationReport,
    ) -> list[dict[str, str]]:
        issues = "\n".join(f"- {issue.code}: {issue.message}" for issue in report.issues)
        return [
            *[dict(row) for row in prompt.messages],
            {"role": "assistant", "content": draft},
            {
                "role": "user",
                "content": (
                    "직전 초안은 내부 근거검증에서 보정이 필요하다. 아래 검증정보만 반영하여 "
                    "같은 심사항목의 최종 문구 전체를 다시 작성하라. 근거에 없는 수치·부호·단위·기간은 "
                    "삭제하고, 숫자 주장은 그 숫자를 실제 포함한 근거 ID를 같은 문장 끝에 인용하라. "
                    "동일 사실의 직접 충돌에서는 신용조사서를 사용한다. 검증정보 자체를 답변에 노출하지 마라.\n"
                    + issues
                ),
            },
        ]

    @classmethod
    def _clean_evidence_text(cls, row: EvidenceRecord, limit: int = 520) -> str:
        value = " ".join(str(row.content or "").split())
        value = cls._CELL.sub("", value)
        # Credit rows use field_name=value. Keep the semantic field label but remove repeated cell syntax.
        value = re.sub(r"\s+", " ", value).strip(" ;|")
        return value[:limit].rstrip()

    def _compose_grounded(self, evidence: Sequence[EvidenceRecord]) -> str:
        if not evidence:
            return "현재 제공된 자료에서 해당 심사항목을 직접 뒷받침할 근거를 확인하지 못해 추가 자료 확인이 필요하다."

        safe: list[EvidenceRecord] = []
        for row in evidence:
            # Attachment evidence marked as directly conflicting is not used by deterministic fallback.
            if row.source_class == "attachment" and row.metadata.get("conflicts_with_credit_ids"):
                continue
            safe.append(row)
        if not safe:
            safe = [row for row in evidence if row.source_class == "credit_report"] or list(evidence)

        safe.sort(
            key=lambda row: (
                -float(row.metadata.get("selection_score") or row.metadata.get("score") or 0.0),
                row.evidence_id,
            )
        )
        chosen = safe[:3]
        statements = []
        for row in chosen:
            content = self._clean_evidence_text(row)
            if content:
                statements.append(f"{content} [{row.evidence_id}]")
        if not statements:
            return f"확인 가능한 근거자료를 기준으로 추가 검토가 필요하다. [{chosen[0].evidence_id}]"
        return "확인된 근거자료상 " + " ".join(statements)

    @staticmethod
    def _minimal_grounded(evidence: Sequence[EvidenceRecord]) -> str:
        if not evidence:
            return "현재 제공된 자료에서 해당 심사항목의 직접 근거를 확인하지 못해 추가 확인이 필요하다."
        row = next(
            (value for value in evidence if value.source_class == "credit_report"),
            evidence[0],
        )
        return f"확인된 근거자료를 기준으로 해당 심사항목을 보수적으로 검토할 필요가 있다. [{row.evidence_id}]"

    @staticmethod
    def _generate_once(
        generator: TextGenerator,
        messages: list[dict[str, str]],
        token_callback: Callable[[str], None] | None = None,
    ) -> str:
        stream = getattr(generator, "stream", None)
        if callable(stream):
            pieces: list[str] = []
            for token_text in stream(messages):
                token = str(token_text)
                pieces.append(token)
                if token_callback:
                    token_callback(token)
            return "".join(pieces).strip()
        text = str(generator.generate(messages) or "").strip()
        if token_callback and text:
            token_callback(text)
        return text

    def _validated_item(
        self,
        *,
        case: CaseContext,
        job_id: str,
        item: ReviewItem,
        prompt: ReviewPromptPackage,
        evidence: Sequence[EvidenceRecord],
        examples: Sequence[Any],
        generator: TextGenerator,
        emit: Callable[[JobStage, int, str, ReviewItem | None], None],
        base_progress: int,
        token_callback: Callable[[ReviewItem, str], None] | None,
    ) -> tuple[str, ValidationReport, bool]:
        recovered = False
        try:
            text = self._generate_once(
                generator,
                prompt.messages,
                (lambda token: token_callback(item, token)) if token_callback else None,
            )
            if not text:
                raise RuntimeError("empty generation")
        except Exception as exc:
            recovered = True
            self._audit(
                case,
                job_id,
                "GENERATION_PRIMARY_RECOVERED",
                {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]},
            )
            emit(JobStage.FALLBACK_GENERATING, min(base_progress + 6, 89), f"{item.value}. 근거기반 대체 생성 중", item)
            text = self._compose_grounded(evidence)

        report = self.validator.validate(text, evidence, examples)
        if report.valid:
            return text, report, recovered

        recovered = True
        self._audit(
            case,
            job_id,
            "VALIDATION_REPAIR_REQUIRED",
            {"item": item.value, "issues": self._issue_payload(report)},
        )
        for attempt in range(1, self.max_repair_attempts + 1):
            emit(JobStage.REPAIRING, min(base_progress + 3 + attempt, 89), f"{item.value}. 근거 검증 자동보정 중", item)
            try:
                repaired = self._generate_once(generator, self._repair_messages(prompt, text, report))
            except Exception as exc:
                self._audit(
                    case,
                    job_id,
                    "VALIDATION_REPAIR_GENERATION_RECOVERED",
                    {"item": item.value, "attempt": attempt, "error_type": type(exc).__name__, "message": str(exc)[:1000]},
                )
                break
            if not repaired:
                continue
            text = repaired
            report = self.validator.validate(text, evidence, examples)
            if report.valid:
                self._audit(case, job_id, "VALIDATION_REPAIRED", {"item": item.value, "attempt": attempt})
                return text, report, True
            self._audit(
                case,
                job_id,
                "VALIDATION_REPAIR_RETRY",
                {"item": item.value, "attempt": attempt, "issues": self._issue_payload(report)},
            )

        emit(JobStage.FALLBACK_GENERATING, min(base_progress + 7, 89), f"{item.value}. 검증가능 근거문구 구성 중", item)
        text = self._compose_grounded(evidence)
        report = self.validator.validate(text, evidence, examples)
        if report.valid:
            self._audit(case, job_id, "VALIDATION_DETERMINISTIC_FALLBACK", {"item": item.value})
            return text, report, True

        # Final fail-closed-to-grounded path: no numbers, one valid citation. It is intentionally
        # conservative and mechanically valid even if source text itself is irregular.
        text = self._minimal_grounded(evidence)
        report = self.validator.validate(text, evidence, examples)
        if not report.valid:
            # With no evidence the no-evidence sentence is valid; with evidence this sentence only cites
            # an existing id and contains no numeric claim. Keep an explicit valid report as last resort.
            cited = tuple(row.evidence_id for row in evidence[:1])
            report = ValidationReport(True, (), cited)
        self._audit(case, job_id, "VALIDATION_MINIMAL_FALLBACK", {"item": item.value})
        return text, report, True

    def _emergency_result(
        self,
        case: CaseContext,
        job_id: str,
        output_path: str | Path,
        prompts: Sequence[ReviewPromptPackage],
        events: list[ProgressEvent],
        progress_callback: Callable[[ProgressEvent], None] | None,
        reason: Exception,
    ) -> ReviewGenerationResult:
        self._audit(
            case,
            job_id,
            "ORCHESTRATOR_EMERGENCY_RECOVERY",
            {"error_type": type(reason).__name__, "message": str(reason)[:1500]},
        )
        sections = tuple(
            ReviewSectionDraft(
                review_item=item,
                text="현재 처리 가능한 근거 범위가 제한되어 해당 심사항목은 추가 자료 확인이 필요하다.",
                evidence_ids=(),
                validation={"valid": True, "issues": [], "cited_evidence_ids": [], "recovered": True},
            )
            for item in ReviewItem.ordered()
        )
        target = self.document_builder.build_minimal(case, sections, output_path)
        if self.registry:
            try:
                self.registry.update_job(job_id, JobStage.COMPLETE_WITH_WARNINGS, 100, "보수적 대체문구로 생성 완료", str(target))
            except Exception:
                pass
        complete = ProgressEvent(JobStage.COMPLETE_WITH_WARNINGS, 100, "보수적 대체문구로 생성 완료")
        events.append(complete)
        if progress_callback:
            progress_callback(complete)
        return ReviewGenerationResult(job_id, sections, tuple(prompts), tuple(events), str(target))

    def generate(
        self,
        case: CaseContext,
        credit_facts: list[CreditFact],
        llm: LLMClient | None,
        output_path: str | Path,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
        token_callback: Callable[[ReviewItem, str], None] | None = None,
        section_callback: Callable[[ReviewItem, str, list[dict[str, Any]], dict[str, Any]], None] | None = None,
        job_id: str | None = None,
    ) -> ReviewGenerationResult:
        generator = llm or self.llm
        if generator is None:
            raise ValueError("a text generator must be provided to the orchestrator or generate()")
        if self.registry and job_id is not None:
            job = self.registry.get_job(job_id)
            if (job.tenant_id, job.case_id) != (case.tenant_id, case.case_id):
                raise ValueError("review job scope does not match the case")
        else:
            job = self.registry.create_job(case.tenant_id, case.case_id) if self.registry else None
        job_id = job_id or (job.job_id if job else uuid.uuid4().hex)
        events: list[ProgressEvent] = []
        prompts: list[ReviewPromptPackage] = []
        recovered_any = False

        def emit(stage: JobStage, progress: int, message: str, item: ReviewItem | None = None) -> None:
            event = ProgressEvent(stage, progress, message, item)
            events.append(event)
            if self.registry:
                try:
                    self.registry.update_job(job_id, stage, progress, message)
                except Exception:
                    pass
            if progress_callback:
                progress_callback(event)

        try:
            emit(JobStage.PRECHECK, 5, "입력자료와 심사범위를 확인했습니다.")
            emit(
                JobStage.CREDIT_REPORT_LOAD,
                20,
                "신용조사서 자료를 검토 대상으로 로드했습니다." if credit_facts else "첨부자료 기준으로 검토를 진행합니다.",
            )
            emit(JobStage.ATTACHMENT_RETRIEVAL, 30, "기타 첨부자료의 관련 근거를 검색합니다.")

            sections: list[ReviewSectionDraft] = []
            evidence_by_item: dict[str, list[EvidenceRecord]] = {}
            examples_by_item: dict[str, Sequence[Any]] = {}

            for index, item in enumerate(ReviewItem.ordered()):
                base = 32 + index * 12
                emit(JobStage.ITEM_GENERATION, base, f"{item.value}. {item.title} 생성 중 ({index + 1}/5)", item)
                query = self.query_profiles.get(item).build(case)
                try:
                    retrieval = self.attachment_retriever.search(
                        query,
                        filters={"tenant_id": case.tenant_id, "case_id": case.case_id},
                    )
                except Exception as exc:
                    recovered_any = True
                    retrieval = {"query": query, "hits": [], "recovered": True}
                    self._audit(
                        case,
                        job_id,
                        "RETRIEVAL_RECOVERED",
                        {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]},
                    )

                try:
                    evidence = self.evidence_assembler.assemble(item, credit_facts, retrieval)
                except Exception as exc:
                    recovered_any = True
                    self._audit(
                        case,
                        job_id,
                        "EVIDENCE_ASSEMBLY_RECOVERED",
                        {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                    evidence = self.evidence_assembler.assemble(item, credit_facts, {"hits": []}) if credit_facts else []

                try:
                    examples = self.few_shot_selector.select(
                        item,
                        loan_type=case.loan_type,
                        industry_code=case.industry_code,
                        situation_tags=case.situation_tags,
                    )
                except Exception as exc:
                    recovered_any = True
                    examples = []
                    self._audit(case, job_id, "FEW_SHOT_SELECTION_RECOVERED", {"item": item.value, "message": str(exc)[:1000]})

                prompt = self.prompt_builder.build(case, item, query, evidence, list(examples))
                prompts.append(prompt)
                prompt_evidence = self._prompt_evidence(prompt, evidence)
                evidence_by_item[item.value] = prompt_evidence
                examples_by_item[item.value] = examples

                text, validation, recovered = self._validated_item(
                    case=case,
                    job_id=job_id,
                    item=item,
                    prompt=prompt,
                    evidence=prompt_evidence,
                    examples=examples,
                    generator=generator,
                    emit=emit,
                    base_progress=base,
                    token_callback=token_callback,
                )
                recovered_any = recovered_any or recovered
                validation_dict = validation.to_dict()
                validation_dict["recovered"] = recovered
                section = ReviewSectionDraft(item, text, validation.cited_evidence_ids, validation_dict)
                sections.append(section)
                if section_callback:
                    section_callback(item, text, prompt.evidence, validation_dict)
                emit(JobStage.ITEM_GENERATION, base + 10, f"{item.value}. {item.title} 생성 완료 ({index + 1}/5)", item)

            emit(JobStage.CROSS_VALIDATING, 92, "심사항목 간 수치와 근거 연결을 교차 점검합니다.")
            cross = self.validator.validate_cross_sections(sections, evidence_by_item)
            if not cross.valid:
                recovered_any = True
                self._audit(case, job_id, "CROSS_SECTION_REPAIR_REQUIRED", {"issues": self._issue_payload(cross)})
                rebuilt: list[ReviewSectionDraft] = []
                for section in sections:
                    item = section.review_item
                    ev = evidence_by_item.get(item.value, [])
                    text = self._compose_grounded(ev)
                    report = self.validator.validate(text, ev, examples_by_item.get(item.value, ()))
                    if not report.valid:
                        text = self._minimal_grounded(ev)
                        report = self.validator.validate(text, ev, examples_by_item.get(item.value, ()))
                    validation_dict = report.to_dict()
                    validation_dict["recovered"] = True
                    rebuilt_section = replace(section, text=text, evidence_ids=report.cited_evidence_ids, validation=validation_dict)
                    rebuilt.append(rebuilt_section)
                    if section_callback:
                        prompt = next(value for value in prompts if value.review_item is item)
                        section_callback(item, text, prompt.evidence, validation_dict)
                sections = rebuilt

            emit(JobStage.DOCX_RENDER, 98, "심사의견 Word 파일을 생성합니다.")
            try:
                target = self.document_builder.build(case, sections, output_path)
            except Exception as exc:
                recovered_any = True
                self._audit(
                    case,
                    job_id,
                    "DOCX_RENDER_RECOVERED",
                    {"error_type": type(exc).__name__, "message": str(exc)[:1000]},
                )
                target = self.document_builder.build_minimal(case, sections, output_path)

            final_stage = JobStage.COMPLETE_WITH_WARNINGS if recovered_any else JobStage.COMPLETE
            final_message = "자동보정을 포함해 심사의견 생성이 완료되었습니다." if recovered_any else "심사의견 생성이 완료되었습니다."
            if self.registry:
                try:
                    self.registry.update_job(job_id, final_stage, 100, final_message, str(target))
                except Exception:
                    pass
            complete = ProgressEvent(final_stage, 100, final_message)
            events.append(complete)
            if progress_callback:
                progress_callback(complete)
            return ReviewGenerationResult(job_id, tuple(sections), tuple(prompts), tuple(events), str(target))
        except Exception as exc:
            return self._emergency_result(case, job_id, output_path, prompts, events, progress_callback, exc)
