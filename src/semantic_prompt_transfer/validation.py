from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

from .domain import EvidenceRecord, FewShotExample, ReviewSectionDraft


CITATION_PATTERN = re.compile(r"(?:CR|ATT)_[a-f0-9]{20}", re.IGNORECASE)
CELL_COORD_PATTERN = re.compile(r"\b[A-Z]{1,3}\d{1,7}(?==)|\b[A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7}\b")
DATE_PATTERN = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:[./-]|년\s*)((?:0?[1-9]|1[0-2]))\s*(?:[./-]|월\s*)((?:0?[1-9]|[12]\d|3[01]))\s*(?:일)?(?!\d)"
)
YEAR_MONTH_PATTERN = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*년\s*((?:0?[1-9]|1[0-2]))\s*월(?!\s*\d)"
)
PERCENT_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([+-]?\d[\d,]*(?:\.\d+)?)\s*%(?![A-Za-z0-9_])")
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([+-]?\d[\d,]*(?:\.\d+)?)(?![A-Za-z0-9_])")
UNIT_PATTERN = re.compile(r"^\s*(억원|백만원|천만원|만원|천원|원|배|개월|년|월|일)")
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*년")
TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z]{2,}")
SENTENCE_PATTERN = re.compile(r"[^.!?\n]+(?:[.!?]+|$)|\n+")
_STOPWORDS = {
    "현재", "자료", "근거", "기준", "관련", "항목", "현황", "향후", "전망", "확인", "따르면",
    "신용조사서", "첨부자료", "기타", "해당", "대한", "그리고", "또한", "으로", "에서", "있으며",
}


@dataclass(frozen=True)
class NumericToken:
    raw: str
    canonical: str
    kind: str
    unit: str | None
    periods: tuple[str, ...]

    def display(self) -> str:
        return f"{self.raw}{self.unit or ''}"


def _decimal(value: str) -> str:
    cleaned = value.replace(",", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return cleaned
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", "+0"} else normalized


def _clean_for_numbers(text: str) -> str:
    value = CITATION_PATTERN.sub(" ", str(text or ""))
    return CELL_COORD_PATTERN.sub(" ", value)


def _period_context(text: str) -> tuple[str, ...]:
    periods = {m.group(1) for m in YEAR_PATTERN.finditer(text)}
    periods.update(m.group(1) for m in DATE_PATTERN.finditer(text))
    periods.update(m.group(1) for m in YEAR_MONTH_PATTERN.finditer(text))
    return tuple(sorted(periods))


def extract_numeric_tokens(text: str) -> tuple[NumericToken, ...]:
    """Parse dates, periods, percentages and signed values while ignoring Excel coordinates."""
    cleaned = _clean_for_numbers(text)
    occupied: list[tuple[int, int]] = []
    values: list[NumericToken] = []
    periods = _period_context(cleaned)

    def overlaps(start: int, end: int) -> bool:
        return any(start < right and end > left for left, right in occupied)

    for match in DATE_PATTERN.finditer(cleaned):
        year, month, day = match.groups()
        values.append(
            NumericToken(match.group(0).strip(), f"{int(year):04d}-{int(month):02d}-{int(day):02d}", "date", None, (year,))
        )
        occupied.append(match.span())

    for match in YEAR_MONTH_PATTERN.finditer(cleaned):
        if overlaps(*match.span()):
            continue
        year, month = match.groups()
        values.append(
            NumericToken(match.group(0).strip(), f"{int(year):04d}-{int(month):02d}", "period", "월", (year,))
        )
        occupied.append(match.span())

    for match in PERCENT_PATTERN.finditer(cleaned):
        if overlaps(*match.span()):
            continue
        values.append(NumericToken(match.group(0).strip(), _decimal(match.group(1)), "percent", "%", periods))
        occupied.append(match.span())

    for match in NUMBER_PATTERN.finditer(cleaned):
        if overlaps(*match.span()):
            continue
        raw = match.group(1)
        tail = cleaned[match.end() : match.end() + 10]
        unit_match = UNIT_PATTERN.match(tail)
        unit = unit_match.group(1) if unit_match else None
        # A lone short integer without a unit is usually an item number, page number or enumeration.
        digits_only = re.sub(r"\D", "", raw)
        if len(digits_only) < 2 and unit is None and "." not in raw and not raw.startswith(("+", "-")):
            continue
        canonical = _decimal(raw)
        kind = "year" if unit == "년" and re.fullmatch(r"(?:19|20)\d{2}", digits_only) else "number"
        values.append(NumericToken(raw, canonical, kind, unit, periods))
        occupied.append(match.span())
    return tuple(values)


def _units_compatible(output: NumericToken, evidence: NumericToken) -> bool:
    if output.kind != evidence.kind:
        # A year captured as a generic number in a compact table may still ground a year statement.
        if {output.kind, evidence.kind} != {"year", "number"}:
            return False
    if output.unit and evidence.unit and output.unit != evidence.unit:
        return False
    return True


def _periods_compatible(output: NumericToken, evidence: NumericToken) -> bool:
    if output.periods and evidence.periods and set(output.periods).isdisjoint(evidence.periods):
        return False
    return True


def _token_matches(output: NumericToken, evidence: NumericToken) -> bool:
    return (
        output.canonical == evidence.canonical
        and _units_compatible(output, evidence)
        and _periods_compatible(output, evidence)
    )


def _sentences(text: str) -> list[str]:
    pieces = [piece.strip() for piece in SENTENCE_PATTERN.findall(str(text or "")) if piece.strip() and not piece.isspace()]
    merged: list[str] = []
    citation_only = re.compile(r"^(?:\[?\s*(?:CR|ATT)_[a-f0-9]{20}\s*\]?\s*)+$", re.IGNORECASE)
    for piece in pieces:
        if merged and citation_only.fullmatch(piece):
            merged[-1] = merged[-1] + " " + piece
        else:
            merged.append(piece)
    return merged


def _sentences_for_evidence(text: str, evidence_ids: Iterable[str]) -> list[str]:
    ids = tuple(str(value) for value in evidence_ids if str(value))
    pieces = _sentences(text)
    merged: list[str] = []
    for piece in pieces:
        stripped = piece.strip().strip("[] ")
        if merged and any(stripped == evidence_id for evidence_id in ids):
            merged[-1] = merged[-1] + " " + piece
        else:
            merged.append(piece)
    return merged


def _anchor(sentence: str, numeric: NumericToken) -> tuple[str, ...]:
    prefix = sentence.split(numeric.raw, 1)[0]
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(prefix) if token.lower() not in _STOPWORDS]
    return tuple(tokens[-4:])


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
    """Claim-local grounding validator for citations, typed numerics and direct conflicts."""

    def validate(
        self,
        text: str,
        evidence: Iterable[EvidenceRecord],
        few_shots: Iterable[FewShotExample],
    ) -> ValidationReport:
        evidence_rows = tuple(evidence)
        example_rows = tuple(few_shots)
        evidence_map = {row.evidence_id: row for row in evidence_rows}
        evidence_tokens = {
            row.evidence_id: extract_numeric_tokens(row.content)
            for row in evidence_rows
        }
        cited = tuple(sorted(evidence_id for evidence_id in evidence_map if evidence_id in text))
        issues: list[ValidationIssue] = []

        if evidence_rows and not cited:
            issues.append(ValidationIssue("missing_evidence_citation", "ERROR", "근거가 있으나 인용된 evidence_id가 없습니다."))

        unsupported: list[str] = []
        uncited_numeric: list[str] = []
        direct_conflicts: list[str] = []
        for sentence in _sentences_for_evidence(text, evidence_map):
            sentence_ids = tuple(evidence_id for evidence_id in evidence_map if evidence_id in sentence)
            numeric_values = extract_numeric_tokens(sentence)
            if numeric_values and not sentence_ids:
                uncited_numeric.extend(value.display() for value in numeric_values)
                continue
            for value in numeric_values:
                if not any(
                    _token_matches(value, candidate)
                    for evidence_id in sentence_ids
                    for candidate in evidence_tokens.get(evidence_id, ())
                ):
                    unsupported.append(value.display())
                    continue
                for evidence_id in sentence_ids:
                    row = evidence_map[evidence_id]
                    conflict_ids = tuple(row.metadata.get("conflicts_with_credit_ids") or ())
                    if not conflict_ids:
                        continue
                    if not any(_token_matches(value, candidate) for candidate in evidence_tokens.get(evidence_id, ())):
                        continue
                    if not any(
                        _token_matches(value, candidate)
                        for credit_id in conflict_ids
                        for candidate in evidence_tokens.get(credit_id, ())
                    ):
                        direct_conflicts.append(value.display())

        if uncited_numeric:
            issues.append(
                ValidationIssue(
                    "numeric_claim_without_citation",
                    "ERROR",
                    "숫자 주장을 해당 문장의 근거와 연결해야 합니다: " + ", ".join(sorted(set(uncited_numeric))),
                )
            )
        if unsupported:
            issues.append(
                ValidationIssue(
                    "unsupported_numeric_fact",
                    "ERROR",
                    "인용된 근거에서 확인되지 않는 수치: " + ", ".join(sorted(set(unsupported))),
                )
            )
        if direct_conflicts:
            issues.append(
                ValidationIssue(
                    "credit_report_direct_conflict",
                    "ERROR",
                    "동일 사실의 직접 충돌에서는 신용조사서 값을 사용해야 합니다: " + ", ".join(sorted(set(direct_conflicts))),
                )
            )

        # Defense in depth: FEW SHOT remains non-evidence even if a caller bypasses sanitization.
        evidence_all = [token for row in evidence_rows for token in evidence_tokens[row.evidence_id]]
        few_tokens = [token for row in example_rows for token in extract_numeric_tokens(row.output_example)]
        output_tokens = extract_numeric_tokens(text)
        leaked: list[str] = []
        for token in output_tokens:
            if any(_token_matches(token, ev) for ev in evidence_all):
                continue
            if any(_token_matches(token, few) for few in few_tokens):
                leaked.append(token.display())
        if leaked:
            issues.append(
                ValidationIssue(
                    "few_shot_numeric_leakage",
                    "ERROR",
                    "스타일 예시에만 존재하는 수치가 출력에 포함되었습니다: " + ", ".join(sorted(set(leaked))),
                )
            )

        forbidden = sorted(
            {
                token
                for row in example_rows
                for token in row.forbidden_tokens
                if token and token in text and not any(token in evidence_row.content for evidence_row in evidence_rows)
            }
        )
        if forbidden:
            issues.append(
                ValidationIssue(
                    "few_shot_token_leakage",
                    "ERROR",
                    "스타일 예시 전용 식별자가 출력에 포함되었습니다: " + ", ".join(forbidden),
                )
            )
        return ValidationReport(
            valid=not any(issue.severity == "ERROR" for issue in issues),
            issues=tuple(issues),
            cited_evidence_ids=cited,
        )

    def validate_cross_sections(
        self,
        sections: Sequence[ReviewSectionDraft],
        evidence_by_item: dict[str, Sequence[EvidenceRecord]],
    ) -> ValidationReport:
        """Detect same-anchor/same-period numeric contradictions across A-E after item validation."""
        issues: list[ValidationIssue] = []
        claims: dict[tuple[tuple[str, ...], tuple[str, ...], str | None], list[tuple[str, NumericToken]]] = {}
        cited_union: set[str] = set()
        for section in sections:
            item_key = section.review_item.value
            allowed_ids = {row.evidence_id for row in evidence_by_item.get(item_key, ())}
            for sentence in _sentences_for_evidence(section.text, allowed_ids):
                cited_union.update(evidence_id for evidence_id in allowed_ids if evidence_id in sentence)
                for token in extract_numeric_tokens(sentence):
                    # Require an explicit period for cross-section hard conflicts to avoid false positives.
                    if not token.periods:
                        continue
                    anchor = _anchor(sentence, token)
                    if not anchor:
                        continue
                    key = (anchor, token.periods, token.unit)
                    claims.setdefault(key, []).append((item_key, token))
        for key, rows in claims.items():
            values = {token.canonical for _, token in rows}
            items = {item for item, _ in rows}
            if len(values) > 1 and len(items) > 1:
                issues.append(
                    ValidationIssue(
                        "cross_section_numeric_conflict",
                        "ERROR",
                        "동일 기간의 동일 주장으로 추정되는 수치가 심사항목 간 불일치합니다.",
                    )
                )
                break
        return ValidationReport(
            valid=not any(issue.severity == "ERROR" for issue in issues),
            issues=tuple(issues),
            cited_evidence_ids=tuple(sorted(cited_union)),
        )
