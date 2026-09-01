from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .domain import CaseContext, CreditFact, EvidenceRecord, FewShotExample, ReviewItem, SourceTier
from .evidence_trace import visual_group_key
from .identity import evidence_id
from .prompt_budget import PromptTokenBudgetManager


_TOKEN = re.compile(r"[가-힣A-Za-z]{2,}")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])[+-]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_CELL = re.compile(r"\b[A-Z]{1,3}\d{1,7}(?==)")
_COMPANY = re.compile(r"(?:(?:주식회사|㈜|\(주\))\s*)[가-힣A-Za-z0-9&.\-]{2,}")
_STOPWORDS = {
    "현재", "자료", "근거", "기준", "관련", "항목", "현황", "향후", "전망", "확인",
    "신용조사서", "첨부자료", "기타", "해당", "대한", "그리고", "또한", "으로", "에서",
}


_MONEY_TO_MILLION = {
    "원": Decimal("0.000001"),
    "천원": Decimal("0.001"),
    "만원": Decimal("0.01"),
    "백만원": Decimal("1"),
    "억원": Decimal("100"),
}
_EXPLICIT_MONEY = re.compile(
    r"(?P<value>\(?[+-]?\d[\d,]*(?:\.\d+)?\)?)\s*(?P<unit>백만원|억원|만원|천원|원)(?![가-힣])"
)


def _money_decimal(value: Any) -> Decimal | None:
    raw = str(value if value is not None else "").strip()
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    raw = raw.replace(",", "")
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return -result if negative else result


def _format_million(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return f"{int(rounded):,}"
    return f"{rounded:,.1f}"


def _normalized_money(value: Any, unit: str | None) -> tuple[str, str | None, bool]:
    source_unit = str(unit or "").strip()
    factor = _MONEY_TO_MILLION.get(source_unit)
    number = _money_decimal(value)
    if factor is None or number is None:
        return str(value), unit, False
    million = number * factor
    return _format_million(million), "백만원", source_unit != "백만원"


def _normalize_explicit_money_text(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        number, unit, _ = _normalized_money(match.group("value"), match.group("unit"))
        return f"{number}{unit or match.group('unit')}"
    return _EXPLICIT_MONEY.sub(repl, str(value or ""))


def _terms(text: str) -> set[str]:
    values: set[str] = set()
    for token in _TOKEN.findall(str(text or "")):
        value = token.lower()
        for suffix in ("으로", "에서", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "의", "에", "로"):
            if value.endswith(suffix) and len(value) - len(suffix) >= 2:
                value = value[: -len(suffix)]
                break
        if value and value not in _STOPWORDS:
            values.add(value)
    return values


def _numeric_lexemes(text: str) -> set[str]:
    cleaned = _CELL.sub("", str(text or ""))
    return {match.replace(",", "") for match in _NUMBER.findall(cleaned)}


def _periods(text: str) -> set[str]:
    return set(_YEAR.findall(str(text or "")))


def _similarity(query: str, text: str) -> float:
    wanted = _terms(query)
    got = _terms(text)
    if not wanted or not got:
        return 0.0
    overlap = len(wanted & got)
    if not overlap:
        return 0.0
    return overlap / max(1.0, (len(wanted) * len(got)) ** 0.5)


def _sanitize_style_text(text: str, forbidden_tokens: Iterable[str] = ()) -> str:
    value = str(text or "")
    for token in sorted({str(v) for v in forbidden_tokens if str(v)}, key=len, reverse=True):
        value = value.replace(token, "특정대상")
    value = _COMPANY.sub("특정기업", value)
    value = re.sub(r"(?<!\d)(?:19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2}(?!\d)", "특정일자", value)
    value = re.sub(r"(?<!\d)(?:19|20)\d{2}\s*년", "특정기간", value)
    value = _NUMBER.sub("수치", value)
    return value


class EvidenceAssembler:
    """Build source-neutral evidence and annotate only direct credit/attachment conflicts."""

    def assemble(
        self,
        review_item: ReviewItem,
        credit_facts: Iterable[CreditFact],
        attachment_retrieval: dict[str, Any],
    ) -> list[EvidenceRecord]:
        rows: list[EvidenceRecord] = []
        for fact in credit_facts:
            if fact.common:
                if fact.review_items and review_item not in fact.review_items:
                    continue
                provenance = SourceTier.CREDIT_REPORT_COMMON
            else:
                if review_item not in fact.review_items:
                    continue
                provenance = SourceTier.CREDIT_REPORT_ITEM
            display_value, display_unit, unit_normalized = _normalized_money(fact.value, fact.unit)
            rendered = f"{fact.field_name}={display_value}"
            if display_unit:
                rendered += f"; 단위={display_unit}"
            if fact.period:
                rendered += f"; 기간={fact.period}"
            rows.append(
                EvidenceRecord(
                    evidence_id=evidence_id("CR", review_item.value, fact.fact_id),
                    review_item=review_item,
                    source_tier=provenance,
                    content=rendered,
                    document_id=fact.document_id,
                    source_filename=fact.source_filename,
                    metadata={
                        "fact_id": fact.fact_id,
                        "field_id": fact.field_id,
                        "sheet_name": fact.sheet_name,
                        "cell_range": fact.cell_range,
                        "formula": fact.formula,
                        "source_hash": fact.source_hash,
                        "source_class": "credit_report",
                        "raw_value": fact.value,
                        "raw_unit": fact.unit,
                        "display_unit": display_unit,
                        "unit_normalized": unit_normalized,
                    },
                )
            )

        for hit in attachment_retrieval.get("hits", []):
            metadata = dict(hit.get("metadata") or {})
            pages = metadata.get("pages") or []
            global_id = metadata.get("global_chunk_id") or hit.get("chunk_id")
            rows.append(
                EvidenceRecord(
                    evidence_id=evidence_id("ATT", review_item.value, global_id),
                    review_item=review_item,
                    source_tier=SourceTier.ATTACHMENT,
                    content=_normalize_explicit_money_text(str(hit.get("document") or hit.get("embedding_text") or "")),
                    document_id=str(metadata.get("document_id") or "unknown"),
                    source_filename=metadata.get("source_filename"),
                    page=int(pages[0]) if pages else None,
                    metadata={
                        "global_chunk_id": global_id,
                        "local_chunk_id": metadata.get("local_chunk_id") or hit.get("chunk_id"),
                        "logical_table_id": metadata.get("logical_table_id"),
                        "score": hit.get("score"),
                        "relevance_status": hit.get("relevance_status", "accepted"),
                        "bbox": metadata.get("bbox"),
                        "page_size": metadata.get("page_size"),
                        "source_location": metadata.get("source_location"),
                        "source_spans": metadata.get("source_spans") or [],
                        "source_class": "attachment",
                    },
                )
            )
        return self._annotate_direct_conflicts(rows)

    @staticmethod
    def _annotate_direct_conflicts(rows: list[EvidenceRecord]) -> list[EvidenceRecord]:
        credit = [row for row in rows if row.source_class == "credit_report"]
        attachments = [row for row in rows if row.source_class == "attachment"]
        if not credit or not attachments:
            return rows
        conflicts_by_attachment: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = {}
        for att in attachments:
            att_terms = _terms(att.content)
            att_numbers = _numeric_lexemes(att.content)
            att_periods = _periods(att.content)
            if not att_terms or not att_numbers:
                continue
            for cr in credit:
                cr_terms = _terms(cr.content)
                cr_numbers = _numeric_lexemes(cr.content)
                cr_periods = _periods(cr.content)
                if not cr_terms or not cr_numbers or cr_numbers == att_numbers:
                    continue
                # Different explicit periods are complementary evidence, not a conflict.
                if cr_periods and att_periods and cr_periods.isdisjoint(att_periods):
                    continue
                common = cr_terms & att_terms
                union = cr_terms | att_terms
                lexical = len(common) / max(1, len(union))
                same_period = bool(cr_periods and att_periods and not cr_periods.isdisjoint(att_periods))
                if (len(common) >= 2 and lexical >= 0.20) or (same_period and len(common) >= 1):
                    conflicts_by_attachment.setdefault(att.evidence_id, []).append(cr.evidence_id)
                    reverse.setdefault(cr.evidence_id, []).append(att.evidence_id)
        if not conflicts_by_attachment:
            return rows
        updated: list[EvidenceRecord] = []
        for row in rows:
            metadata = dict(row.metadata)
            if row.evidence_id in conflicts_by_attachment:
                metadata["conflicts_with_credit_ids"] = sorted(conflicts_by_attachment[row.evidence_id])
                metadata["conflict_resolution"] = "credit_report_on_direct_conflict"
            if row.evidence_id in reverse:
                metadata["conflicting_attachment_ids"] = sorted(reverse[row.evidence_id])
            updated.append(replace(row, metadata=metadata))
        return updated


@dataclass(frozen=True)
class ReviewPromptPackage:
    schema_version: str
    review_item: ReviewItem
    query: str
    messages: list[dict[str, str]]
    evidence: list[dict[str, Any]]
    few_shots: list[dict[str, Any]]
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "review_item": self.review_item.value,
            "review_item_title": self.review_item.title,
            "query": self.query,
            "messages": self.messages,
            "evidence": self.evidence,
            "few_shots": self.few_shots,
            "manifest": self.manifest,
        }


class ReviewPromptBuilder:
    """Evidence-maximizing prompt builder with explicit credit-reasoning guidance."""

    _OUTPUT_DEPTH = {
        ReviewItem.MAJOR_ACCOUNTS: "핵심 이슈 4~6개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.PROFITABILITY: "핵심 이슈 3~5개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.FINANCIAL_STABILITY: "핵심 이슈 4~6개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.CASH_FLOW: "핵심 이슈 4~6개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.MAJOR_CUSTOMERS: "핵심 이슈 3~5개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
    }

    def __init__(
        self,
        max_context_chars: int = 18000,
        *,
        credit_report_min_share: float = 0.50,
        token_budget_manager: PromptTokenBudgetManager | None = None,
    ) -> None:
        self.max_context_chars = int(max_context_chars)
        self.credit_report_min_share = float(credit_report_min_share)
        self.token_budget = token_budget_manager
        if self.max_context_chars < 1000:
            raise ValueError("max_context_chars must be at least 1000")
        if not 0.0 <= self.credit_report_min_share <= 1.0:
            raise ValueError("credit_report_min_share must be between 0 and 1")

    @staticmethod
    def _legacy_block(row: EvidenceRecord) -> str:
        label = "CREDIT_REPORT" if row.source_class == "credit_report" else "ATTACHMENT"
        conflicts = ",".join(row.metadata.get("conflicts_with_credit_ids") or [])
        return (
            f"[{label} EVIDENCE]\nsource_class={row.source_class}\n"
            f"evidence_id={row.evidence_id}\ndocument_id={row.document_id}\n"
            f"source_filename={row.source_filename}\npage={row.page}\n"
            f"direct_conflict_credit_ids={conflicts}\ncontent={row.content}"
        )

    @staticmethod
    def _compact_entry(row: EvidenceRecord) -> str:
        conflicts = ",".join(row.metadata.get("conflicts_with_credit_ids") or [])
        suffix = f" | direct_conflict_credit_ids={conflicts}" if conflicts else ""
        return f"id={row.evidence_id}{suffix} | {row.content}"

    @staticmethod
    def _group_header(row: EvidenceRecord) -> str:
        metadata = dict(row.metadata or {})
        if row.source_class == "credit_report":
            loc = f"{metadata.get('sheet_name') or '시트'}:{metadata.get('cell_range') or '범위'}"
            return f"[CREDIT_REPORT_GROUP file={row.source_filename or 'credit.xlsx'} location={loc}]"
        loc = metadata.get("logical_table_id") or metadata.get("source_location") or "region"
        return f"[ATTACHMENT_GROUP file={row.source_filename or 'attachment'} page={row.page or '-'} region={loc}]"

    def _render_grouped(self, rows: list[EvidenceRecord]) -> list[str]:
        groups: dict[tuple[Any, ...], list[EvidenceRecord]] = {}
        order: list[tuple[Any, ...]] = []
        for row in rows:
            key = visual_group_key(row)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        blocks = []
        for key in order:
            members = groups[key]
            blocks.append(self._group_header(members[0]) + "\n" + "\n".join(self._compact_entry(row) for row in members))
        return blocks

    def _ranked(
        self,
        query: str,
        evidence: list[EvidenceRecord],
        reasoning_blueprint: dict[str, Any] | None = None,
    ) -> list[tuple[float, EvidenceRecord]]:
        blueprint = reasoning_blueprint or {}
        priority = {str(v) for v in blueprint.get("priority_evidence_ids") or []}
        supporting = set()
        for issue in blueprint.get("priority_issues") or []:
            if isinstance(issue, dict):
                supporting.update(str(v) for v in issue.get("evidence_ids") or [])
        ranked: list[tuple[float, EvidenceRecord]] = []
        for row in evidence:
            lexical = _similarity(query, row.content)
            vector_score = row.metadata.get("score") if row.source_class == "attachment" else None
            try:
                vector = max(-1.0, min(1.0, float(vector_score))) if vector_score is not None else 0.0
            except (TypeError, ValueError):
                vector = 0.0
            score = lexical + max(0.0, vector) * 0.35
            if row.evidence_id in priority:
                score += 1.5
            elif row.evidence_id in supporting:
                score += 0.75
            if row.metadata.get("conflicts_with_credit_ids") or row.metadata.get("conflicting_attachment_ids"):
                score += 0.25
            metadata = dict(row.metadata)
            metadata["selection_score"] = round(score, 6)
            ranked.append((score, replace(row, metadata=metadata)))
        ranked.sort(key=lambda value: (-value[0], value[1].evidence_id))
        return ranked

    @staticmethod
    def _novelty_order(entries, initially_seen=None):
        seen = set(initially_seen or ())
        unique, repeats = [], []
        for entry in entries:
            key = visual_group_key(entry[1])
            if key in seen:
                repeats.append(entry)
            else:
                unique.append(entry)
                seen.add(key)
        return unique + repeats

    def _select_legacy(self, query: str, evidence: list[EvidenceRecord], reasoning_blueprint=None):
        ranked = [(s, row, self._legacy_block(row)) for s, row in self._ranked(query, evidence, reasoning_blueprint)]
        credit = [entry for entry in ranked if entry[1].source_class == "credit_report"]
        target_credit = min(
            sum(len(entry[2]) for entry in credit),
            int(self.max_context_chars * self.credit_report_min_share),
        ) if credit else 0
        selected, chosen, seen_groups = [], set(), set()
        used = credit_used = 0
        for entry in self._novelty_order(credit):
            if credit_used >= target_credit:
                break
            size = len(entry[2])
            if used + size > self.max_context_chars:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id)
            seen_groups.add(visual_group_key(entry[1])); used += size; credit_used += size
        remaining = [entry for entry in ranked if entry[1].evidence_id not in chosen]
        for entry in self._novelty_order(remaining, seen_groups):
            size = len(entry[2])
            if used + size > self.max_context_chars:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id); used += size
            if entry[1].source_class == "credit_report":
                credit_used += size
        selected.sort(key=lambda value: (-value[0], value[1].evidence_id))
        rows = [entry[1] for entry in selected]
        return rows, {
            "selection_mode": "legacy_char_budget",
            "context_characters": used,
            "credit_report_target_characters": target_credit,
            "credit_report_floor_satisfied": credit_used >= target_credit,
            "unique_visual_evidence_groups": len({visual_group_key(row) for row in rows}),
        }

    def _select_tokens(
        self,
        query: str,
        evidence: list[EvidenceRecord],
        evidence_budget_tokens: int,
        reasoning_blueprint: dict[str, Any] | None,
    ):
        assert self.token_budget is not None
        ranked = self._ranked(query, evidence, reasoning_blueprint)
        entries = [(score, row, self.token_budget.count_text(self._compact_entry(row)) + 10) for score, row in ranked]
        credit = [entry for entry in entries if entry[1].source_class == "credit_report"]
        available_credit = sum(entry[2] for entry in credit)
        target_credit = min(available_credit, int(evidence_budget_tokens * self.credit_report_min_share)) if credit else 0
        selected, chosen, seen_groups = [], set(), set()
        used = credit_used = 0
        for entry in self._novelty_order(credit):
            if credit_used >= target_credit:
                break
            if used + entry[2] > evidence_budget_tokens:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id)
            seen_groups.add(visual_group_key(entry[1])); used += entry[2]; credit_used += entry[2]
        remaining = [entry for entry in entries if entry[1].evidence_id not in chosen]
        for entry in self._novelty_order(remaining, seen_groups):
            if used + entry[2] > evidence_budget_tokens:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id); used += entry[2]
            if entry[1].source_class == "credit_report":
                credit_used += entry[2]
        selected.sort(key=lambda value: (-value[0], value[1].evidence_id))
        rows = [entry[1] for entry in selected]
        return rows, {
            "selection_mode": "token_budget_materiality_diversity",
            "evidence_budget_tokens": evidence_budget_tokens,
            "evidence_tokens_used": used,
            "credit_report_target_tokens": target_credit,
            "credit_report_tokens_used": credit_used,
            "credit_report_floor_satisfied": credit_used >= target_credit,
            "unique_visual_evidence_groups": len({visual_group_key(row) for row in rows}),
        }

    @staticmethod
    def _blueprint_text(value: dict[str, Any] | None) -> str:
        if not value:
            return "구조화 판단정보 없음"
        compact = {
            "judgment_focus": value.get("judgment_focus"),
            "priority_issues": value.get("priority_issues") or [],
            "cross_item_signals": value.get("cross_item_signals") or [],
        }
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    def build(
        self,
        case: CaseContext,
        review_item: ReviewItem,
        query: str,
        evidence: list[EvidenceRecord],
        few_shots: list[FewShotExample],
        reasoning_blueprint: dict[str, Any] | None = None,
    ) -> ReviewPromptPackage:
        system = (
            "현재 심사건에서 제공된 근거만 사실로 사용한다. 신용조사서와 기타 첨부자료는 출처 종류만으로 상시 우선순위를 부여하지 않고 독립 근거로 검토한다. "
            "동일 사실·동일 기간·동일 단위가 직접 충돌할 때에만 신용조사서 내용을 채택한다. FEW SHOT은 사실이 아니라 심사역의 표현·분석 구조만 참고한다. "
            "CREDIT_REASONING_BLUEPRINT의 중요도와 항목 간 연계를 우선 반영한다. 단순 사실 나열에 그치지 말고 각 핵심 이슈에서 사실→의미→위험→완화요인→상환능력 영향→향후 관찰변수를 연결한다. "
            "자료에 없는 원인은 만들지 않는다. 금액은 CURRENT_CASE_EVIDENCE의 정규화 단위를 사용하고 한 비교축에서 단위를 혼용하지 않는다. "
            "각 핵심 주장에는 제공된 evidence_id를 붙이며 존재하지 않는 ID를 만들지 않는다. "
            "최종 출력에는 표·마크다운 표·CSV·열 정렬·신용조사서 근거 요약 같은 중간자료를 출력하지 않고 심사의견 문장만 작성한다. "
            "근거에 값이 없는 기간을 대시(-), 0 또는 임의 수치로 채우지 말고 해당 값 자체를 언급하지 않는다."
        )
        blueprint_text = self._blueprint_text(reasoning_blueprint)
        depth = self._OUTPUT_DEPTH[review_item]
        fixed_prefix = (
            f"심사건: case={case.case_id}\n심사항목: {review_item.value}. {review_item.title}\n\n"
            f"[QUERY_PROFILE]\n{query}\n\n[CREDIT_REASONING_BLUEPRINT]\n{blueprint_text}\n\n"
        )
        writing = (
            "\n\n[작성요청]\n심사역 관점에서 중요도가 높은 내용을 우선한다. " + depth + ". "
            "LOW 중요도 사실은 핵심 판단을 설명하는 데 필요할 때만 사용한다. 위험요인만 나열하지 말고 확인 가능한 완화요인과 상환재원 영향을 함께 비교한다. "
            "손익과 현금흐름, 운전자본과 차입금, 차입금과 자본완충, 매출처와 외형 안정성처럼 관련 항목을 연결한다. "
            "현재보다 충분히 상세하게 작성하되 반복은 피하고 최종 심사의견 문장만 출력한다. 표나 근거 요약 블록을 앞에 붙이지 않는다. 마지막 문장은 반드시 완결한다."
        )
        style_candidates = []
        for example in few_shots:
            summary = _sanitize_style_text(example.input_summary, example.forbidden_tokens)
            output = _sanitize_style_text(example.output_example, example.forbidden_tokens)
            if self.token_budget is not None:
                summary = summary[:480]
            style_candidates.append(
                f"[STYLE_ONLY_FEW_SHOT {example.example_id}]\n입력맥락: {summary}\n작성예시: {output}\n주의: 사실은 전이하지 않는다."
            )

        selection: dict[str, Any]
        if self.token_budget is None:
            kept_style = style_candidates
            kept_evidence, selection = self._select_legacy(query, evidence, reasoning_blueprint)
            evidence_blocks = [self._legacy_block(row) for row in kept_evidence]
            user = fixed_prefix + "[FEW_SHOT_STYLE_ONLY]\n" + ("\n\n".join(kept_style) if kept_style else "선택된 예시 없음") + "\n\n[CURRENT_CASE_EVIDENCE]\n" + ("\n\n".join(evidence_blocks) if evidence_blocks else "현재 근거 없음") + writing
            budget_info = {"token_budget_enabled": False}
        else:
            input_budget = self.token_budget.input_budget_tokens
            style_cap = min(4200, max(900, int(input_budget * 0.22)))
            kept_style, style_tokens = [], 0
            for block in style_candidates:
                cost = self.token_budget.count_text(block) + 8
                if kept_style and style_tokens + cost > style_cap:
                    continue
                if style_tokens + cost > style_cap and kept_style:
                    continue
                kept_style.append(block); style_tokens += cost
            if not kept_style and style_candidates:
                kept_style = [style_candidates[0]]
                style_tokens = self.token_budget.count_text(style_candidates[0]) + 8
            fixed_tokens = self.token_budget.count_text(system + fixed_prefix + writing) + style_tokens + 80
            evidence_budget = max(700, input_budget - fixed_tokens)
            kept_evidence, selection = self._select_tokens(query, evidence, evidence_budget, reasoning_blueprint)
            while True:
                evidence_blocks = self._render_grouped(kept_evidence)
                user = fixed_prefix + "[FEW_SHOT_STYLE_ONLY]\n" + ("\n\n".join(kept_style) if kept_style else "선택된 예시 없음") + "\n\n[CURRENT_CASE_EVIDENCE]\n" + ("\n\n".join(evidence_blocks) if evidence_blocks else "현재 근거 없음") + writing
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                if self.token_budget.fits(messages) or len(kept_evidence) <= 1:
                    break
                kept_evidence.pop()
            snap = self.token_budget.snapshot(messages)
            budget_info = {"token_budget_enabled": True, **snap.to_dict(), "style_tokens": style_tokens}

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        conflicts = {
            row.evidence_id: list(row.metadata.get("conflicts_with_credit_ids") or [])
            for row in kept_evidence if row.metadata.get("conflicts_with_credit_ids")
        }
        priority_ids = list((reasoning_blueprint or {}).get("priority_evidence_ids") or [])
        return ReviewPromptPackage(
            schema_version="review-prompt-2.0",
            review_item=review_item,
            query=query,
            messages=messages,
            evidence=[row.to_dict() for row in kept_evidence],
            few_shots=[
                {
                    "example_id": row.example_id,
                    "review_item": row.review_item.value,
                    "version": row.version,
                    "style_only": True,
                    "sanitized": True,
                    "loan_types": list(row.loan_types),
                    "industry_codes": list(row.industry_codes),
                    "situation_tags": list(row.situation_tags),
                }
                for row in few_shots[: len(kept_style)]
            ],
            manifest={
                "tenant_id": case.tenant_id,
                "case_id": case.case_id,
                "evidence_policy": "source_neutral_with_credit_report_direct_conflict_resolution",
                "conflict_resolution": "credit_report_on_direct_conflict",
                "few_shot_is_evidence": False,
                "reasoning_layer": "credit_reasoning_materiality_cross_item",
                "priority_evidence_ids": priority_ids,
                "selected_evidence_count": len(kept_evidence),
                "available_evidence_count": len(evidence),
                "credit_report_available": any(row.source_class == "credit_report" for row in kept_evidence),
                "attachment_evidence_available": any(row.source_class == "attachment" for row in kept_evidence),
                "direct_conflicts": conflicts,
                **selection,
                **budget_info,
            },
        )
