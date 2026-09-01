from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol

from .domain import EvidenceRecord, ReviewItem
from .llm import TextGenerator

_CITATION = re.compile(r"(?:CR|ATT)_[a-f0-9]{20}", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)|\n+")


class VerificationMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    ENFORCE = "ENFORCE"


class VerificationStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RepairSeverity(str, Enum):
    MINOR = "MINOR"
    CLAIM_ERROR = "CLAIM_ERROR"
    PARAGRAPH_ERROR = "PARAGRAPH_ERROR"


@dataclass(frozen=True)
class Claim:
    claim_id: str
    review_item: ReviewItem
    text: str
    start: int
    end: int
    evidence_ids: tuple[str, ...] = ()
    revision: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "review_item": self.review_item.value,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "evidence_ids": list(self.evidence_ids),
            "revision": self.revision,
        }


@dataclass(frozen=True)
class VerificationFinding:
    claim_id: str
    revision: int
    status: VerificationStatus
    severity: RepairSeverity = RepairSeverity.MINOR
    problem_span: str = ""
    reason_code: str = ""
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()
    repair_instruction: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "revision": self.revision,
            "status": self.status.value,
            "severity": self.severity.value,
            "problem_span": self.problem_span,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "repair_instruction": self.repair_instruction,
        }


class VerificationAgent(Protocol):
    def verify(self, claim: Claim, evidence: Iterable[EvidenceRecord]) -> VerificationFinding: ...


class NoOpVerificationAgent:
    def verify(self, claim: Claim, evidence: Iterable[EvidenceRecord]) -> VerificationFinding:
        return VerificationFinding(claim.claim_id, claim.revision, VerificationStatus.NOT_RUN)


class ClaimSegmenter:
    """Convert a completed token stream into immutable verification claims."""

    def segment(self, item: ReviewItem, text: str, allowed_evidence_ids: Iterable[str] = ()) -> tuple[Claim, ...]:
        allowed = {str(v) for v in allowed_evidence_ids}
        claims: list[Claim] = []
        counter = 0
        for match in _SENTENCE.finditer(str(text or "")):
            raw = match.group(0)
            if not raw.strip() or raw.isspace():
                continue
            counter += 1
            evidence_ids = tuple(
                value for value in dict.fromkeys(_CITATION.findall(raw))
                if value in allowed
            )
            claims.append(
                Claim(
                    claim_id=f"{item.value}-{counter:03d}",
                    review_item=item,
                    text=raw,
                    start=match.start(),
                    end=match.end(),
                    evidence_ids=evidence_ids,
                )
            )
        if not claims and str(text or "").strip():
            raw = str(text)
            ids = tuple(value for value in dict.fromkeys(_CITATION.findall(raw)) if value in allowed)
            claims.append(Claim(f"{item.value}-001", item, raw, 0, len(raw), ids))
        return tuple(claims)


class LLMVerificationAgent:
    """Claim-local verifier that never writes review prose.

    FAIL is deliberately narrow: only a clear evidence-backed factual error may
    request a patch. Ambiguity, missing evidence, style preference, or analytical
    disagreement cannot mutate generated text.
    """

    _FAIL_REASON_CODES = {
        "FACT_CONTRADICTION",
        "UNSUPPORTED_NUMERIC",
        "UNSUPPORTED_FACT",
        "UNSUPPORTED_CAUSAL",
        "PERIOD_MISMATCH",
        "UNIT_MISMATCH",
        "SOURCE_CONFLICT",
    }

    def __init__(self, generator: TextGenerator) -> None:
        self.generator = generator

    @staticmethod
    def _warn(claim: Claim, code: str, reason: str) -> VerificationFinding:
        return VerificationFinding(
            claim.claim_id,
            claim.revision,
            VerificationStatus.WARN,
            reason_code=code,
            reason=reason,
        )

    def verify(self, claim: Claim, evidence: Iterable[EvidenceRecord]) -> VerificationFinding:
        rows = tuple(evidence)
        allowed_ids = {row.evidence_id for row in rows}
        evidence_text = "\n".join(f"[{row.evidence_id}] {row.content}" for row in rows) or "근거 없음"
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 기업여신 심사의견의 독립 검증 에이전트다. 최종 문장을 직접 작성하거나 "
                    "문체를 개선하지 않는다. 오직 주어진 claim과 evidence의 사실 일치 여부만 판단하여 "
                    "JSON 하나만 반환한다. 기본 판단은 PASS다. 근거가 부족하거나 판단이 애매하면 "
                    "INSUFFICIENT_EVIDENCE 또는 WARN이며 절대로 FAIL로 판정하지 않는다. 문체, 표현 선호, "
                    "분석 강도의 차이도 WARN 이하로만 판정한다. FAIL은 근거에 의해 명백히 확인되는 수치, "
                    "기간, 단위, 사실, 인과관계의 직접 오류가 있을 때만 허용한다. 동일 사실·동일 기간·동일 "
                    "단위가 직접 충돌할 때 신용조사서 근거가 있으면 신용조사서 값을 채택한다. FAIL이면 "
                    "problem_span은 반드시 claim 원문에서 그대로 복사하고, evidence_ids에는 실제 판단에 쓴 "
                    "제공 근거 ID만 넣는다. corrected_sentence나 수정문은 작성하지 않는다. "
                    "reason_code는 FACT_CONTRADICTION, UNSUPPORTED_NUMERIC, UNSUPPORTED_FACT, "
                    "UNSUPPORTED_CAUSAL, PERIOD_MISMATCH, UNIT_MISMATCH, SOURCE_CONFLICT 중 하나만 사용한다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"claim_id={claim.claim_id}\nrevision={claim.revision}\nclaim={claim.text}\n\n"
                    f"[evidence]\n{evidence_text}\n\n"
                    "다음 key만 가진 JSON을 반환하라: claim_id, revision, status, severity, problem_span, "
                    "reason_code, reason, evidence_ids, repair_instruction. status는 PASS/WARN/FAIL/"
                    "INSUFFICIENT_EVIDENCE, severity는 MINOR/CLAIM_ERROR/PARAGRAPH_ERROR 중 하나다."
                ),
            },
        ]
        try:
            raw = str(self.generator.generate(messages) or "").strip()
        except Exception as exc:
            return self._warn(claim, "VERIFIER_GENERATION_ERROR", f"검증 LLM 호출 실패: {type(exc).__name__}")
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return self._warn(claim, "VERIFIER_PARSE_ERROR", "검증 LLM JSON 파싱 실패")
        try:
            value = json.loads(match.group(0))
            status = VerificationStatus(str(value.get("status") or "WARN").upper())
            severity = RepairSeverity(str(value.get("severity") or "MINOR").upper())
        except Exception:
            return self._warn(claim, "VERIFIER_SCHEMA_ERROR", "검증 LLM 응답 스키마 오류")

        problem_span = str(value.get("problem_span") or "")
        reason_code = str(value.get("reason_code") or "").upper()
        reason = str(value.get("reason") or "")
        evidence_ids = tuple(
            eid for eid in (str(v) for v in value.get("evidence_ids", [])) if eid in allowed_ids
        )
        repair_instruction = str(value.get("repair_instruction") or "")

        # Fail-closed for mutation: verifier uncertainty can never rewrite prose.
        if status is VerificationStatus.FAIL:
            if not rows:
                return VerificationFinding(
                    claim.claim_id, claim.revision, VerificationStatus.INSUFFICIENT_EVIDENCE,
                    reason_code="NO_EVIDENCE", reason="검증에 사용할 근거가 없음",
                )
            if reason_code not in self._FAIL_REASON_CODES:
                return self._warn(claim, "UNSAFE_FAIL_REASON", reason or "FAIL 사유가 허용 범위를 벗어남")
            if not problem_span or problem_span not in claim.text:
                return self._warn(claim, "INVALID_FAIL_SPAN", "FAIL이지만 원문 problem_span을 특정하지 못함")
            if not evidence_ids:
                return VerificationFinding(
                    claim.claim_id, claim.revision, VerificationStatus.INSUFFICIENT_EVIDENCE,
                    reason_code="UNBOUND_FAIL_EVIDENCE", reason="FAIL 판정을 특정 근거에 연결하지 못함",
                )

        if status is VerificationStatus.INSUFFICIENT_EVIDENCE:
            problem_span = ""
            repair_instruction = ""
        if status in {VerificationStatus.PASS, VerificationStatus.WARN}:
            repair_instruction = ""

        return VerificationFinding(
            claim_id=claim.claim_id,
            revision=claim.revision,
            status=status,
            severity=severity,
            problem_span=problem_span,
            reason_code=reason_code,
            reason=reason,
            evidence_ids=evidence_ids,
            repair_instruction=repair_instruction,
        )


class PatchGuard:
    """Ensure verification can never rewrite unrelated claims or sections."""

    @staticmethod
    def apply(claim: Claim, finding: VerificationFinding, replacement: str) -> str | None:
        replacement = str(replacement or "").strip()
        if not replacement:
            return None
        if finding.severity is RepairSeverity.MINOR:
            span = str(finding.problem_span or "")
            if not span or span not in claim.text:
                return None
            if len(replacement) > max(160, len(span) * 3):
                return None
            return claim.text.replace(span, replacement, 1)
        # The caller replaces only claim.start:claim.end, so even a larger correction cannot
        # mutate a different claim.  Section-wide/A-E rewrites do not exist in this path.
        if len(replacement) > max(1200, len(claim.text) * 3):
            return None
        return replacement


class RepairCoordinator:
    def __init__(self, max_attempts: int = 2) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.guard = PatchGuard()

    def repair(
        self,
        generator: TextGenerator,
        claim: Claim,
        finding: VerificationFinding,
        evidence: Iterable[EvidenceRecord],
    ) -> str | None:
        evidence_text = "\n".join(f"[{row.evidence_id}] {row.content}" for row in evidence)
        for _ in range(self.max_attempts):
            if finding.severity is RepairSeverity.MINOR:
                output_rule = "문장 전체가 아니라 problem_span을 대체할 문자열만 출력한다."
            else:
                output_rule = "해당 claim 하나만 다시 작성한다. 다른 문장이나 설명을 출력하지 않는다."
            messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 기업여신 심사의견 생성기다. 검증기가 지정한 최소 범위만 수정한다. "
                        "검증과 무관한 어휘·어순·문체는 보존한다. " + output_rule
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"원문 claim:\n{claim.text}\n\nproblem_span:\n{finding.problem_span}\n\n"
                        f"수정이유:\n{finding.reason}\n\n수정지시:\n{finding.repair_instruction}\n\n"
                        f"근거:\n{evidence_text or '근거 없음'}"
                    ),
                },
            ]
            candidate = str(generator.generate(messages) or "").strip().strip("`").strip()
            patched = self.guard.apply(claim, finding, candidate)
            if patched:
                return patched
        return None
