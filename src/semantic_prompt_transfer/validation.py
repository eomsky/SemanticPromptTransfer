from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .domain import EvidenceRecord, FewShotExample


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z_])\d[\d,]*(?:\.\d+)?%?")


def _numbers(text: str) -> set[str]:
    values = set()
    for match in NUMBER_PATTERN.findall(text or ""):
        normalized = match.replace(",", "")
        digits = re.sub(r"\D", "", normalized)
        if len(digits) >= 2 or "." in normalized or "%" in normalized:
            values.add(normalized)
    return values


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...]
    cited_evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
            "cited_evidence_ids": list(self.cited_evidence_ids),
        }


class OpinionValidator:
    """Reject unsupported numbers and leakage from style-only few-shot examples."""

    def validate(
        self,
        text: str,
        evidence: Iterable[EvidenceRecord],
        few_shots: Iterable[FewShotExample],
    ) -> ValidationReport:
        evidence_rows = tuple(evidence)
        example_rows = tuple(few_shots)
        evidence_ids = {row.evidence_id for row in evidence_rows}
        cited = tuple(sorted(evidence_id for evidence_id in evidence_ids if evidence_id in text))
        issues: list[ValidationIssue] = []
        if evidence_rows and not cited:
            issues.append(
                ValidationIssue("missing_evidence_citation", "ERROR", "근거가 있으나 evidence_id 인용이 없습니다.")
            )
        attachment_ids = {
            row.evidence_id for row in evidence_rows if int(row.source_tier) == 3
        }
        if attachment_ids and not attachment_ids.intersection(cited):
            issues.append(
                ValidationIssue(
                    "missing_attachment_citation",
                    "WARNING",
                    "관련 첨부자료가 제공되었으나 최종 문구에 첨부자료 근거가 인용되지 않았습니다.",
                )
            )

        scrubbed = text
        for evidence_id in evidence_ids:
            scrubbed = scrubbed.replace(evidence_id, "")
        evidence_numbers = _numbers("\n".join(row.content for row in evidence_rows))
        output_numbers = _numbers(scrubbed)
        unsupported = sorted(output_numbers - evidence_numbers)
        if unsupported:
            issues.append(
                ValidationIssue(
                    "unsupported_numeric_fact",
                    "ERROR",
                    "현재 심사건 근거에 없는 수치: " + ", ".join(unsupported),
                )
            )

        few_shot_numbers = _numbers("\n".join(row.output_example for row in example_rows))
        leaked_numbers = sorted((few_shot_numbers - evidence_numbers).intersection(output_numbers))
        if leaked_numbers:
            issues.append(
                ValidationIssue(
                    "few_shot_numeric_leakage",
                    "ERROR",
                    "FEW SHOT에서만 존재하는 수치가 출력에 포함됨: " + ", ".join(leaked_numbers),
                )
            )
        forbidden = sorted(
            {
                token
                for row in example_rows
                for token in row.forbidden_tokens
                if token and token in text
                and not any(token in evidence_row.content for evidence_row in evidence_rows)
            }
        )
        if forbidden:
            issues.append(
                ValidationIssue(
                    "few_shot_token_leakage",
                    "ERROR",
                    "FEW SHOT 전용 식별자가 출력에 포함됨: " + ", ".join(forbidden),
                )
            )
        return ValidationReport(
            valid=not any(issue.severity == "ERROR" for issue in issues),
            issues=tuple(issues),
            cited_evidence_ids=cited,
        )
