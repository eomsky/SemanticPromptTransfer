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
    """Optional verifier: it reports problems but never writes final review prose."""

    def __init__(self, generator: TextGenerator) -> None:
        self.generator = generator

    def verify(self, claim: Claim, evidence: Iterable[EvidenceRecord]) -> VerificationFinding:
        evidence_text = "\n".join(f"[{row.evidence_id}] {row.content}" for row in evidence) or "근거 없음"
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 기업여신 심사의견 검증기다. 문장을 직접 고쳐 쓰지 않는다. "
                    "주어진 claim과 근거만 비교하고 JSON 하나만 반환한다. status는 PASS/WARN/FAIL/"
                    "INSUFFICIENT_EVIDENCE 중 하나다. FAIL이면 severity는 MINOR/CLAIM_ERROR/"
                    "PARAGRAPH_ERROR 중 하나이며 problem_span은 원문에서 정확히 복사한다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"claim_id={claim.claim_id}\nclaim={claim.text}\n\n[evidence]\n{evidence_text}\n\n"
                    "JSON keys: claim_id, revision, status, severity, problem_span, reason_code, reason, "
                    "evidence_ids, repair_instruction"
                ),
            },
        ]
        raw = str(self.generator.generate(messages) or "").strip()
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return VerificationFinding(
                claim.claim_id,
                claim.revision,
                VerificationStatus.WARN,
                reason_code="VERIFIER_PARSE_ERROR",
                reason="검증기 JSON 파싱 실패",
            )
        try:
            value = json.loads(match.group(0))
            status = VerificationStatus(str(value.get("status") or "WARN").upper())
            severity = RepairSeverity(str(value.get("severity") or "MINOR").upper())
            return VerificationFinding(
                claim_id=claim.claim_id,
                revision=claim.revision,
                status=status,
                severity=severity,
                problem_span=str(value.get("problem_span") or ""),
                reason_code=str(value.get("reason_code") or ""),
                reason=str(value.get("reason") or ""),
                evidence_ids=tuple(str(v) for v in value.get("evidence_ids", [])),
                repair_instruction=str(value.get("repair_instruction") or ""),
            )
        except Exception:
            return VerificationFinding(
                claim.claim_id,
                claim.revision,
                VerificationStatus.WARN,
                reason_code="VERIFIER_SCHEMA_ERROR",
                reason="검증기 응답 스키마 오류",
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
