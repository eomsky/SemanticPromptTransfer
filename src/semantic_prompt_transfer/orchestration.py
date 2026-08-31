from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .domain import (
    CaseContext,
    CreditFact,
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
from .validation import OpinionValidator


LLMClient = TextGenerator


class AttachmentRetriever(Protocol):
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class ReviewValidationError(RuntimeError):
    pass


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
    """Provider-neutral five-item generation with progress and validation gates."""

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

    def generate(
        self,
        case: CaseContext,
        credit_facts: list[CreditFact],
        llm: LLMClient | None,
        output_path: str | Path,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> ReviewGenerationResult:
        generator = llm or self.llm
        if generator is None:
            raise ValueError("a text generator must be provided to the orchestrator or generate()")
        job = self.registry.create_job(case.tenant_id, case.case_id) if self.registry else None
        job_id = job.job_id if job else uuid.uuid4().hex
        events: list[ProgressEvent] = []

        def emit(stage: JobStage, progress: int, message: str, item: ReviewItem | None = None) -> None:
            event = ProgressEvent(stage, progress, message, item)
            events.append(event)
            if self.registry:
                self.registry.update_job(job_id, stage, progress, message)
            if progress_callback:
                progress_callback(event)

        try:
            emit(JobStage.PRECHECK, 5, "입력자료와 심사범위를 확인했습니다.")
            if not credit_facts:
                raise ValueError("credit report facts are required")
            emit(JobStage.CREDIT_REPORT_LOAD, 20, "신용조사서 정형자료를 로드했습니다.")
            emit(JobStage.ATTACHMENT_RETRIEVAL, 30, "기타 첨부파일 검색을 시작합니다.")

            sections: list[ReviewSectionDraft] = []
            prompts: list[ReviewPromptPackage] = []
            for index, item in enumerate(ReviewItem.ordered()):
                query = self.query_profiles.get(item).build(case)
                retrieval = self.attachment_retriever.search(
                    query,
                    filters={"tenant_id": case.tenant_id, "case_id": case.case_id},
                )
                evidence = self.evidence_assembler.assemble(item, credit_facts, retrieval)
                examples = self.few_shot_selector.select(
                    item,
                    loan_type=case.loan_type,
                    industry_code=case.industry_code,
                    situation_tags=case.situation_tags,
                )
                prompt = self.prompt_builder.build(case, item, query, evidence, examples)
                prompts.append(prompt)
                text = generator.generate(prompt.messages)
                validation = self.validator.validate(text, evidence, examples)
                if not validation.valid:
                    raise ReviewValidationError(
                        f"item {item.value} validation failed: "
                        + "; ".join(issue.message for issue in validation.issues)
                    )
                sections.append(
                    ReviewSectionDraft(
                        review_item=item,
                        text=text,
                        evidence_ids=validation.cited_evidence_ids,
                        validation=validation.to_dict(),
                    )
                )
                emit(
                    JobStage.ITEM_GENERATION,
                    40 + index * 10,
                    f"{item.value} 항목 생성과 검증을 완료했습니다.",
                    item,
                )

            emit(JobStage.VALIDATING, 90, "다섯 항목의 항목별 근거·수치 검증 결과를 집계했습니다.")
            target = self.document_builder.build(case, sections, output_path)
            emit(JobStage.DOCX_RENDER, 98, "심사의견 Word 파일을 생성했습니다.")
            if self.registry:
                self.registry.update_job(job_id, JobStage.COMPLETE, 100, "완료", str(target))
            complete = ProgressEvent(JobStage.COMPLETE, 100, "다운로드할 수 있습니다.")
            events.append(complete)
            if progress_callback:
                progress_callback(complete)
            return ReviewGenerationResult(
                job_id=job_id,
                sections=tuple(sections),
                prompts=tuple(prompts),
                progress_events=tuple(events),
                output_path=str(target),
            )
        except Exception as exc:
            if self.registry:
                self.registry.update_job(job_id, JobStage.FAILED, min(events[-1].progress if events else 0, 99), str(exc))
            raise
