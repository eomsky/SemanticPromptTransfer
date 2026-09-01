from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .domain import CaseContext, CreditFact, EvidenceRecord, JobStage, ProgressEvent, ReviewItem, ReviewSectionDraft
from .credit_reasoning import CreditReasoningLayer
from .evidence_trace import EvidenceTraceLedger
from .fewshot import FewShotSelector
from .llm import TextGenerator
from .query_profiles import QueryProfileRegistry
from .registry import OperationalRegistry
from .review import EvidenceAssembler, ReviewPromptBuilder, ReviewPromptPackage
from .review_docx import OpinionDocumentBuilder
from .validation import OpinionValidator
from .verification_flow import (
    ClaimSegmenter,
    NoOpVerificationAgent,
    RepairCoordinator,
    VerificationAgent,
    VerificationMode,
    VerificationStatus,
)

LLMClient = TextGenerator
_CITATION = re.compile(r"(?:CR|ATT)_[a-f0-9]{20}", re.IGNORECASE)
_COMPLETE_SUFFIXES = ("함", "임", "됨", "음", "필요함", "양호함", "판단됨", "예상됨", "전망됨", "확인됨")
_INCOMPLETE_SUFFIXES = ("등에", "으로", "하며", "하고", "및", "따라", "대해", "대한", "에서", "위해", "통해", "반면", "이나", "지만", "경우", "때문에")
_STYLE_PLACEHOLDER = re.compile(r"\[(?:VALUE|PERIOD|COMPANY|DATE|ENTITY)\]", re.IGNORECASE)


class AttachmentRetriever(Protocol):
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class ReviewValidationError(RuntimeError):
    """Legacy compatibility only. Semantic validation no longer gates generation."""


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
    """Streaming generation core with optional non-destructive verification.

    The reusable core defaults to OFF. The operating Colab activates a conservative LLM verifier
    in ENFORCE mode. ENFORCE can only patch the failing claim/span; whole-section
    and A-E rewrites are structurally absent from this orchestrator.
    """

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
        verification_mode: VerificationMode | str = VerificationMode.OFF,
        verifier: VerificationAgent | None = None,
        max_auto_patches_per_section: int = 1,
        max_completion_attempts: int = 2,
        reasoner: CreditReasoningLayer | None = None,
        completion_generator: TextGenerator | None = None,
    ) -> None:
        self.attachment_retriever = attachment_retriever
        self.few_shot_selector = few_shot_selector
        self.query_profiles = query_profiles or QueryProfileRegistry()
        self.evidence_assembler = evidence_assembler or EvidenceAssembler()
        self.prompt_builder = prompt_builder or ReviewPromptBuilder()
        # Diagnostic compatibility only. It is deliberately not a generation gate.
        self.validator = validator or OpinionValidator()
        self.document_builder = document_builder or OpinionDocumentBuilder()
        self.registry = registry
        self.llm = llm
        self.verification_mode = VerificationMode(str(getattr(verification_mode, "value", verification_mode)).upper())
        self.verifier = verifier or NoOpVerificationAgent()
        self.segmenter = ClaimSegmenter()
        self.repair = RepairCoordinator(max_repair_attempts)
        self.max_auto_patches_per_section = max(0, int(max_auto_patches_per_section))
        self.max_completion_attempts = max(0, int(max_completion_attempts))
        self.reasoner = reasoner or CreditReasoningLayer(None)
        self.completion_generator = completion_generator

    def _audit(self, case: CaseContext, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if not self.registry:
            return
        recorder = getattr(self.registry, "record_audit_event", None)
        if callable(recorder):
            try:
                recorder(case.tenant_id, case.case_id, job_id, event_type, payload)
            except Exception:
                pass

    @staticmethod
    def _prompt_evidence(prompt: ReviewPromptPackage, original: Sequence[EvidenceRecord]) -> list[EvidenceRecord]:
        wanted = {str(row.get("evidence_id") or "") for row in prompt.evidence}
        by_id = {row.evidence_id: row for row in original}
        return [by_id[eid] for eid in wanted if eid in by_id]

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

    @classmethod
    def _looks_complete(cls, text: str) -> bool:
        visible = _CITATION.sub("", str(text or "")).strip()
        visible = re.sub(r"\s+", " ", visible).rstrip()
        if not visible:
            return False
        if visible.endswith((".", "!", "?", "。")):
            return True
        if any(visible.endswith(suffix) for suffix in _COMPLETE_SUFFIXES):
            return True
        if visible.endswith((",", ";", ":", "·", "-")):
            return False
        if any(visible.endswith(suffix) for suffix in _INCOMPLETE_SUFFIXES):
            return False
        return False

    @staticmethod
    def _novel_continuation(base: str, continuation: str) -> str:
        value = str(continuation or "").strip()
        if not value:
            return ""
        if value.startswith(base):
            return value[len(base):].lstrip()
        maximum = min(240, len(base), len(value))
        for size in range(maximum, 12, -1):
            if base[-size:] == value[:size]:
                return value[size:].lstrip()
        return value

    def _ensure_complete(
        self,
        *,
        case: CaseContext,
        job_id: str,
        item: ReviewItem,
        prompt: ReviewPromptPackage,
        generator: TextGenerator,
        text: str,
        token_callback: Callable[[ReviewItem, str], None] | None,
    ) -> str:
        current = str(text or "").strip()
        if self._looks_complete(current):
            return current
        for attempt in range(1, self.max_completion_attempts + 1):
            messages = [
                *[dict(row) for row in prompt.messages],
                {"role": "assistant", "content": current},
                {
                    "role": "user",
                    "content": (
                        "직전 심사의견의 마지막 문장이 중간에서 끊겼다. 이미 작성한 내용을 반복하거나 "
                        "새 분석 포인트를 추가하지 말고, 끊긴 마지막 문장만 자연스럽게 이어서 완결하라. "
                        "출력은 이어질 문자열만 작성한다."
                    ),
                },
            ]
            try:
                completion = self.completion_generator or generator
                continuation = str(completion.generate(messages) or "").strip()
            except Exception as exc:
                self._audit(case, job_id, "GENERATION_COMPLETION_ERROR", {
                    "item": item.value,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                })
                break
            addition = self._novel_continuation(current, continuation)
            if not addition:
                break
            current = current.rstrip() + ("" if current.endswith((" ", "\n")) else " ") + addition.lstrip()
            if token_callback:
                token_callback(item, addition)
            self._audit(case, job_id, "GENERATION_COMPLETION_CONTINUED", {
                "item": item.value,
                "attempt": attempt,
            })
            if self._looks_complete(current):
                break
        current = current.strip()
        return current if self._looks_complete(current) else self._rollback_incomplete_tail(current)

    @classmethod
    def _rollback_incomplete_tail(cls, text: str) -> str:
        value = str(text or "").rstrip()
        if not value or cls._looks_complete(value):
            return value
        # Preserve a citation immediately following the last completed sentence.
        last = max(value.rfind("."), value.rfind("!"), value.rfind("?"), value.rfind("。"))
        if last >= 0:
            prefix = value[: last + 1]
            rest = value[last + 1 :]
            cite = re.match(r"\s*(?:\[(?:CR|ATT)_[a-f0-9]{20}\]\s*)*", rest, flags=re.I)
            return (prefix + (cite.group(0) if cite else "")).rstrip()
        lines = [line.rstrip() for line in value.splitlines() if line.strip()]
        kept = []
        for line in lines:
            if cls._looks_complete(line):
                kept.append(line)
            else:
                break
        return "\n".join(kept).strip() or value

    @staticmethod
    def _strip_style_placeholder_leakage(text: str) -> str:
        value = str(text or "")
        if not _STYLE_PLACEHOLDER.search(value):
            return value.strip()
        clean_lines = [line for line in value.splitlines() if not _STYLE_PLACEHOLDER.search(line)]
        cleaned = "\n".join(clean_lines).strip()
        if cleaned:
            return cleaned
        return _STYLE_PLACEHOLDER.sub("", value).strip()

    @staticmethod
    def _cited_ids(text: str, evidence: Sequence[EvidenceRecord]) -> tuple[str, ...]:
        allowed = {row.evidence_id for row in evidence}
        return tuple(value for value in dict.fromkeys(_CITATION.findall(str(text or ""))) if value in allowed)

    def _technical_generate(
        self,
        *,
        case: CaseContext,
        job_id: str,
        item: ReviewItem,
        prompt: ReviewPromptPackage,
        generator: TextGenerator,
        token_callback: Callable[[ReviewItem, str], None] | None,
    ) -> tuple[str, bool]:
        try:
            text = self._generate_once(
                generator,
                prompt.messages,
                (lambda token: token_callback(item, token)) if token_callback else None,
            )
            if text:
                text = self._strip_style_placeholder_leakage(text)
                text = self._ensure_complete(
                    case=case,
                    job_id=job_id,
                    item=item,
                    prompt=prompt,
                    generator=generator,
                    text=text,
                    token_callback=token_callback,
                )
                return text, False
            raise RuntimeError("empty generation")
        except Exception as first:
            self._audit(
                case,
                job_id,
                "GENERATION_RETRY",
                {"item": item.value, "error_type": type(first).__name__, "message": str(first)[:1000]},
            )
            try:
                # A partially streamed primary response may already be visible. Do not stream
                # the retry; section_complete atomically replaces it when the retry succeeds.
                text = self._generate_once(generator, prompt.messages)
                if text:
                    text = self._strip_style_placeholder_leakage(text)
                    text = self._ensure_complete(
                        case=case,
                        job_id=job_id,
                        item=item,
                        prompt=prompt,
                        generator=generator,
                        text=text,
                        token_callback=None,
                    )
                    return text, True
                raise RuntimeError("empty retry generation")
            except Exception as second:
                self._audit(
                    case,
                    job_id,
                    "GENERATION_TECHNICAL_RECOVERY",
                    {"item": item.value, "error_type": type(second).__name__, "message": str(second)[:1000]},
                )
                # Technical recovery never dumps raw evidence or fabricates a credit judgment.
                return "일시적인 생성 처리 문제로 해당 심사항목의 문구를 확정하지 못했습니다.", True

    def _verify_and_patch(
        self,
        *,
        item: ReviewItem,
        text: str,
        evidence: Sequence[EvidenceRecord],
        generator: TextGenerator,
        claim_callback: Callable[[ReviewItem, dict[str, Any]], None] | None,
        patch_callback: Callable[[ReviewItem, dict[str, Any]], None] | None,
    ) -> tuple[str, list[dict[str, Any]], bool]:
        allowed_ids = [row.evidence_id for row in evidence]
        claims = self.segmenter.segment(item, text, allowed_ids)
        for claim in claims:
            if claim_callback:
                claim_callback(item, {"type": "claim_complete", **claim.to_dict()})

        if self.verification_mode is VerificationMode.OFF:
            return text, [], False

        findings: list[tuple[Any, Any]] = []
        by_id = {row.evidence_id: row for row in evidence}
        claim_evidence_rows: list[list[EvidenceRecord]] = []
        for claim in claims:
            claim_evidence = [by_id[eid] for eid in claim.evidence_ids if eid in by_id] or list(evidence)
            claim_evidence_rows.append(claim_evidence)
            if claim_callback:
                claim_callback(
                    item,
                    {"type": "verification_started", "claim_id": claim.claim_id, "revision": claim.revision},
                )
        batch_verify = getattr(self.verifier, "verify_many", None)
        batch_results = None
        if callable(batch_verify) and claims:
            try:
                candidate = tuple(batch_verify(claims, claim_evidence_rows))
                if len(candidate) == len(claims):
                    batch_results = candidate
            except Exception:
                batch_results = None
        if batch_results is None:
            batch_results = tuple(
                self.verifier.verify(claim, claim_evidence)
                for claim, claim_evidence in zip(claims, claim_evidence_rows, strict=True)
            )
        for claim, finding in zip(claims, batch_results, strict=True):
            findings.append((claim, finding))
            if claim_callback:
                claim_callback(item, {"type": "verification_result", **finding.to_dict()})

        if self.verification_mode is VerificationMode.SHADOW:
            return text, [finding.to_dict() for _, finding in findings], False

        repaired_any = False
        current = text
        patch_count = 0
        for claim, finding in sorted(findings, key=lambda pair: pair[0].start):
            if finding.status is not VerificationStatus.FAIL:
                continue
            if patch_count >= self.max_auto_patches_per_section:
                break
            claim_evidence = [by_id[eid] for eid in claim.evidence_ids if eid in by_id] or list(evidence)
            repaired = self.repair.repair(generator, claim, finding, claim_evidence)
            if not repaired or repaired == claim.text:
                continue
            current = current[: claim.start] + repaired + current[claim.end :]
            repaired_any = True
            patch_count += 1
            if patch_callback:
                patch_callback(
                    item,
                    {
                        "type": "claim_patch",
                        "claim_id": claim.claim_id,
                        "revision": claim.revision + 1,
                        "old_text": claim.text,
                        "new_text": repaired,
                        "section_text": current,
                    },
                )
        return current, [finding.to_dict() for _, finding in findings], repaired_any

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
                item,
                "일시적인 처리 문제로 해당 심사항목의 문구를 확정하지 못했습니다.",
                (),
                {"valid": True, "verification_mode": self.verification_mode.value, "technical_recovery": True},
            )
            for item in ReviewItem.ordered()
        )
        target = self.document_builder.build_minimal(case, sections, output_path)
        if self.registry:
            try:
                self.registry.update_job(
                    job_id,
                    JobStage.COMPLETE_WITH_WARNINGS,
                    100,
                    "기술 복구를 포함해 생성 완료",
                    str(target),
                )
            except Exception:
                pass
        complete = ProgressEvent(JobStage.COMPLETE_WITH_WARNINGS, 100, "기술 복구를 포함해 생성 완료")
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
        claim_callback: Callable[[ReviewItem, dict[str, Any]], None] | None = None,
        patch_callback: Callable[[ReviewItem, dict[str, Any]], None] | None = None,
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
            emit(JobStage.CREDIT_REPORT_LOAD, 18, "신용조사서와 첨부자료를 심사 근거로 로드했습니다.")
            emit(JobStage.ATTACHMENT_RETRIEVAL, 26, "A~E 전체 관련 근거를 검색하고 중요도를 평가합니다.")

            prepared: dict[ReviewItem, tuple[str, list[EvidenceRecord], list[Any]]] = {}
            evidence_by_item: dict[ReviewItem, list[EvidenceRecord]] = {}
            ordered_items = ReviewItem.ordered()
            queries = {item: self.query_profiles.get(item).build(case) for item in ordered_items}
            retrieval_by_item: dict[ReviewItem, dict[str, Any]] = {}
            bulk_search = getattr(self.attachment_retriever, "search_many", None)
            if callable(bulk_search):
                try:
                    bulk_rows = list(bulk_search(
                        [queries[item] for item in ordered_items],
                        filters={"tenant_id": case.tenant_id, "case_id": case.case_id},
                    ))
                    if len(bulk_rows) == len(ordered_items):
                        retrieval_by_item = dict(zip(ordered_items, bulk_rows, strict=True))
                except Exception as exc:
                    self._audit(case, job_id, "BATCH_RETRIEVAL_FALLBACK", {"error_type": type(exc).__name__, "message": str(exc)[:1000]})
            for index, item in enumerate(ordered_items):
                query = queries[item]
                retrieval = retrieval_by_item.get(item)
                if retrieval is None:
                    try:
                        retrieval = self.attachment_retriever.search(
                            query,
                            filters={"tenant_id": case.tenant_id, "case_id": case.case_id},
                        )
                    except Exception as exc:
                        recovered_any = True
                        retrieval = {"query": query, "hits": [], "recovered": True}
                        self._audit(case, job_id, "RETRIEVAL_RECOVERED", {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]})
                try:
                    evidence = self.evidence_assembler.assemble(item, credit_facts, retrieval)
                except Exception as exc:
                    recovered_any = True
                    evidence = self.evidence_assembler.assemble(item, credit_facts, {"hits": []}) if credit_facts else []
                    self._audit(case, job_id, "EVIDENCE_ASSEMBLY_RECOVERED", {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]})
                try:
                    examples = list(self.few_shot_selector.select(
                        item,
                        loan_type=case.loan_type,
                        industry_code=case.industry_code,
                        situation_tags=case.situation_tags,
                    ))
                except Exception as exc:
                    recovered_any = True
                    examples = []
                    self._audit(case, job_id, "FEW_SHOT_SELECTION_RECOVERED", {"item": item.value, "message": str(exc)[:1000]})
                prepared[item] = (query, evidence, examples)
                evidence_by_item[item] = evidence
                emit(JobStage.ATTACHMENT_RETRIEVAL, 27 + index * 2, f"{item.value}. 관련 근거 검색 완료", item)

            emit(JobStage.ATTACHMENT_RETRIEVAL, 38, "중요 이슈·위험·완화요인·상환영향 및 A~E 연계를 설계합니다.")
            try:
                portfolio = self.reasoner.plan(case, evidence_by_item)
            except Exception as exc:
                recovered_any = True
                portfolio = CreditReasoningLayer(None).plan(case, evidence_by_item)
                self._audit(case, job_id, "CREDIT_REASONING_RECOVERED", {"error_type": type(exc).__name__, "message": str(exc)[:1000]})

            sections: list[ReviewSectionDraft] = []
            trace = EvidenceTraceLedger()
            evidence_catalog: dict[str, dict[str, Any]] = {}
            pending: dict[ReviewItem, dict[str, Any]] = {}

            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="spt-verification") as verification_pool:
                for index, item in enumerate(ReviewItem.ordered()):
                    base = 40 + index * 11
                    emit(JobStage.ITEM_GENERATION, base, f"{item.value}. {item.title} 생성 중 ({index + 1}/5)", item)
                    query, evidence, examples = prepared[item]
                    blueprint = portfolio.item_blueprint(item)
                    prompt = self.prompt_builder.build(case, item, query, evidence, examples, reasoning_blueprint=blueprint)
                    prompts.append(prompt)
                    prompt_evidence = self._prompt_evidence(prompt, evidence)
                    for row in prompt_evidence:
                        evidence_catalog[row.evidence_id] = row.to_dict()
                    generated_text, recovered = self._technical_generate(
                        case=case,
                        job_id=job_id,
                        item=item,
                        prompt=prompt,
                        generator=generator,
                        token_callback=token_callback,
                    )
                    recovered_any = recovered_any or recovered
                    future = verification_pool.submit(
                        self._verify_and_patch,
                        item=item,
                        text=generated_text,
                        evidence=prompt_evidence,
                        generator=generator,
                        claim_callback=claim_callback,
                        patch_callback=patch_callback,
                    )
                    pending[item] = {
                        "future": future,
                        "prompt": prompt,
                        "prompt_evidence": prompt_evidence,
                        "blueprint": blueprint,
                        "recovered": recovered,
                        "base": base,
                    }
                    emit(JobStage.ITEM_GENERATION, min(94, base + 8), f"{item.value}. 생성 완료 · 검증 진행 중 ({index + 1}/5)", item)

                for index, item in enumerate(ReviewItem.ordered()):
                    payload = pending[item]
                    text_value, verification, repaired = payload["future"].result()
                    recovered_any = recovered_any or repaired
                    prompt = payload["prompt"]
                    prompt_evidence = payload["prompt_evidence"]
                    blueprint = payload["blueprint"]
                    cited_ids = self._cited_ids(text_value, prompt_evidence)
                    refs = trace.register(item, prompt_evidence, cited_ids)
                    final_claims = self.segmenter.segment(item, text_value, [row.evidence_id for row in prompt_evidence])
                    claim_evidence_map = []
                    for claim in final_claims:
                        ref_nos = []
                        for eid in claim.evidence_ids:
                            ref_no = trace.ref_no_for(eid)
                            if ref_no is not None and ref_no not in ref_nos:
                                ref_nos.append(ref_no)
                        claim_evidence_map.append({"claim_id": claim.claim_id, "evidence_ids": list(claim.evidence_ids), "ref_nos": ref_nos})
                    meta = {
                        "valid": True,
                        "verification_mode": self.verification_mode.value,
                        "verification": verification,
                        "recovered": payload["recovered"],
                        "repaired": repaired,
                        "verification_pipeline": "section_batch_overlapped",
                        "evidence_rebound_after_patch": bool(repaired),
                        "cited_evidence_ids": list(cited_ids),
                        "evidence_refs": [ref.to_dict() for ref in refs],
                        "claim_evidence_map": claim_evidence_map,
                        "credit_reasoning": blueprint,
                        "prompt_budget": dict(prompt.manifest),
                    }
                    section = ReviewSectionDraft(
                        review_item=item,
                        text=text_value,
                        evidence_ids=cited_ids,
                        validation=meta,
                        evidence_refs=tuple(ref.to_dict() for ref in refs),
                    )
                    sections.append(section)
                    if section_callback:
                        section_callback(item, text_value, prompt.evidence, meta)
                    emit(JobStage.ITEM_GENERATION, min(95, int(payload["base"]) + 9), f"{item.value}. {item.title} 검증 완료 ({index + 1}/5)", item)

            emit(JobStage.DOCX_RENDER, 97, "심사의견과 근거 부록 Word 파일을 생성합니다.")
            try:
                target = self.document_builder.build(case, sections, output_path, evidence_catalog=evidence_catalog)
            except Exception as exc:
                recovered_any = True
                self._audit(case, job_id, "DOCX_RENDER_RECOVERED", {"error_type": type(exc).__name__, "message": str(exc)[:1000]})
                target = self.document_builder.build_minimal(case, sections, output_path)

            final_stage = JobStage.COMPLETE_WITH_WARNINGS if recovered_any else JobStage.COMPLETE
            if self.verification_mode is VerificationMode.OFF:
                final_message = "심사의견 생성이 완료되었습니다."
            elif self.verification_mode is VerificationMode.SHADOW:
                final_message = "심사의견 생성 및 비개입 검증이 완료되었습니다."
            else:
                final_message = "심사의견 생성 및 최소범위 검증 반영이 완료되었습니다."
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
