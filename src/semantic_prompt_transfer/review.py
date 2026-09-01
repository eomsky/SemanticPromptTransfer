from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .domain import CaseContext, CreditFact, EvidenceRecord, FewShotExample, ReviewItem, SourceTier
from .evidence_trace import visual_group_key
from .identity import evidence_id


_TOKEN = re.compile(r"[가-힣A-Za-z]{2,}")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])[+-]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_CELL = re.compile(r"\b[A-Z]{1,3}\d{1,7}(?==)")
_COMPANY = re.compile(r"(?:(?:주식회사|㈜|\(주\))\s*)[가-힣A-Za-z0-9&.\-]{2,}")
_STOPWORDS = {
    "현재", "자료", "근거", "기준", "관련", "항목", "현황", "향후", "전망", "확인",
    "신용조사서", "첨부자료", "기타", "해당", "대한", "그리고", "또한", "으로", "에서",
}


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
        value = value.replace(token, "[ENTITY]")
    value = _COMPANY.sub("[COMPANY]", value)
    value = re.sub(r"(?<!\d)(?:19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2}(?!\d)", "[DATE]", value)
    value = re.sub(r"(?<!\d)(?:19|20)\d{2}\s*년", "[PERIOD]", value)
    value = _NUMBER.sub("[VALUE]", value)
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
            rendered = f"{fact.field_name}={fact.value}"
            if fact.unit:
                rendered += f"; 단위={fact.unit}"
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
                    content=str(hit.get("document") or hit.get("embedding_text") or ""),
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
    """Source-neutral prompt builder with a credit-report minimum representation constraint."""

    def __init__(
        self,
        max_context_chars: int = 18000,
        *,
        credit_report_min_share: float = 0.50,
    ) -> None:
        self.max_context_chars = int(max_context_chars)
        self.credit_report_min_share = float(credit_report_min_share)
        if self.max_context_chars < 1000:
            raise ValueError("max_context_chars must be at least 1000")
        if not 0.0 <= self.credit_report_min_share <= 1.0:
            raise ValueError("credit_report_min_share must be between 0 and 1")

    @staticmethod
    def _block(row: EvidenceRecord) -> str:
        label = "CREDIT_REPORT" if row.source_class == "credit_report" else "ATTACHMENT"
        conflicts = ",".join(row.metadata.get("conflicts_with_credit_ids") or [])
        return (
            f"[{label} EVIDENCE]\n"
            f"source_class={row.source_class}\n"
            f"evidence_id={row.evidence_id}\n"
            f"document_id={row.document_id}\n"
            f"source_filename={row.source_filename}\n"
            f"page={row.page}\n"
            f"direct_conflict_credit_ids={conflicts}\n"
            f"content={row.content}"
        )

    def _ranked(self, query: str, evidence: list[EvidenceRecord]) -> list[tuple[float, EvidenceRecord, str]]:
        ranked: list[tuple[float, EvidenceRecord, str]] = []
        for row in evidence:
            lexical = _similarity(query, row.content)
            vector_score = row.metadata.get("score") if row.source_class == "attachment" else None
            try:
                vector = max(-1.0, min(1.0, float(vector_score))) if vector_score is not None else 0.0
            except (TypeError, ValueError):
                vector = 0.0
            # No source-type bonus: both sources compete on query fit.  The separate
            # credit-report floor below is a coverage constraint, not factual priority.
            score = lexical + max(0.0, vector) * 0.35
            metadata = dict(row.metadata)
            metadata["selection_score"] = round(score, 6)
            ranked.append((score, replace(row, metadata=metadata), self._block(replace(row, metadata=metadata))))
        ranked.sort(key=lambda value: (-value[0], value[1].evidence_id))
        return ranked

    def _select(self, query: str, evidence: list[EvidenceRecord]) -> tuple[list[EvidenceRecord], dict[str, Any]]:
        ranked = self._ranked(query, evidence)
        credit = [entry for entry in ranked if entry[1].source_class == "credit_report"]
        target_credit = min(
            sum(len(entry[2]) for entry in credit),
            int(self.max_context_chars * self.credit_report_min_share),
        ) if credit else 0

        def novelty_order(entries, initially_seen=None):
            seen = set(initially_seen or ())
            unique, repeats = [], []
            for entry in entries:
                key = visual_group_key(entry[1])
                if key in seen:
                    repeats.append(entry)
                else:
                    unique.append(entry); seen.add(key)
            return unique + repeats

        selected = []
        chosen = set()
        seen_groups = set()
        used = 0
        credit_used = 0
        for entry in novelty_order(credit):
            if credit_used >= target_credit: break
            size = len(entry[2])
            if used + size > self.max_context_chars: continue
            selected.append(entry); chosen.add(entry[1].evidence_id)
            seen_groups.add(visual_group_key(entry[1])); used += size; credit_used += size

        remaining = [entry for entry in ranked if entry[1].evidence_id not in chosen]
        for entry in novelty_order(remaining, seen_groups):
            size = len(entry[2])
            if used + size > self.max_context_chars: continue
            selected.append(entry); chosen.add(entry[1].evidence_id)
            seen_groups.add(visual_group_key(entry[1])); used += size
            if entry[1].source_class == "credit_report": credit_used += size

        selected.sort(key=lambda value: (-value[0], value[1].evidence_id))
        rows = [entry[1] for entry in selected]
        attachment_used = sum(len(entry[2]) for entry in selected if entry[1].source_class == "attachment")
        return rows, {
            "context_characters": used,
            "evidence_characters_by_source": {"credit_report": credit_used, "attachment": attachment_used},
            "credit_report_minimum_context_share": self.credit_report_min_share,
            "credit_report_target_characters": target_credit,
            "credit_report_floor_satisfied": credit_used >= target_credit,
            "credit_report_available_characters": sum(len(entry[2]) for entry in credit),
            "unique_visual_evidence_groups": len({visual_group_key(entry[1]) for entry in selected}),
        }

    def build(
        self,
        case: CaseContext,
        review_item: ReviewItem,
        query: str,
        evidence: list[EvidenceRecord],
        few_shots: list[FewShotExample],
    ) -> ReviewPromptPackage:
        system = (
            "현재 심사건에서 제공된 근거만 사실로 사용한다. 신용조사서와 기타 첨부자료는 "
            "출처 종류만으로 상시 우선순위를 부여하지 않고 각각 독립 근거로 검토한다. "
            "단, 동일 사실·동일 기준시점 또는 기간·동일 단위에 대해 두 출처가 직접 충돌할 때에만 "
            "신용조사서 내용을 채택하고 차이가 있음을 명시한다. 기준시점이나 기간이 다르면 충돌로 "
            "간주하지 말고 각 시점의 정보로 함께 활용한다. FEW SHOT은 수치·회사명·기간을 제거한 "
            "문체와 분석 구조만 참고한다. 수치·부호·단위·기간을 임의 변환하거나 계산하지 않는다. "
            "각 핵심 주장 문장 끝에는 제공된 [evidence_id]를 붙이고 존재하지 않는 근거 ID를 만들지 않는다."
        )
        example_blocks: list[str] = []
        for example in few_shots:
            example_blocks.append(
                f"[STYLE_ONLY_FEW_SHOT {example.example_id}]\n"
                f"입력상황: {_sanitize_style_text(example.input_summary, example.forbidden_tokens)}\n"
                f"작성예시: {_sanitize_style_text(example.output_example, example.forbidden_tokens)}\n"
                "주의: placeholder와 문체·논리 구조만 참고하며 원래 사실은 제공되지 않는다."
            )

        kept_evidence, selection = self._select(query, evidence)
        evidence_blocks = [self._block(row) for row in kept_evidence]
        user = (
            f"심사건: tenant={case.tenant_id}, case={case.case_id}\n"
            f"여신유형: {case.loan_type}\n"
            f"산업분류: {case.industry_code}\n"
            f"심사항목: {review_item.value}. {review_item.title}\n\n"
            f"[QUERY_PROFILE]\n{query}\n\n"
            "[FEW_SHOT_STYLE_ONLY]\n"
            + ("\n\n".join(example_blocks) if example_blocks else "선택된 예시 없음")
            + "\n\n[CURRENT_CASE_EVIDENCE]\n"
            + ("\n\n".join(evidence_blocks) if evidence_blocks else "현재 근거 없음")
            + "\n\n[작성요청]\n현황, 주요 원인, 위험·완화요인 및 향후전망을 근거 중심으로 작성하라. "
            "서로 보완되는 신용조사서와 첨부자료는 함께 사용하되 불필요한 출처 우열을 만들지 마라. "
            "direct_conflict_credit_ids가 표시된 첨부근거와 해당 신용조사서가 같은 사실에서 충돌하면 "
            "신용조사서를 채택하라. 각 핵심 문장 끝에는 반드시 [CR_…] 또는 [ATT_…] 근거를 붙이고 "
            "최종 심사의견만 출력하라. 마지막 문장은 반드시 완결하라."
        )
        conflicts = {
            row.evidence_id: list(row.metadata.get("conflicts_with_credit_ids") or [])
            for row in kept_evidence
            if row.metadata.get("conflicts_with_credit_ids")
        }
        return ReviewPromptPackage(
            schema_version="review-prompt-1.1",
            review_item=review_item,
            query=query,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
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
                for row in few_shots
            ],
            manifest={
                "tenant_id": case.tenant_id,
                "case_id": case.case_id,
                "loan_type": case.loan_type,
                "industry_code": case.industry_code,
                "evidence_policy": "source_neutral_with_credit_report_direct_conflict_resolution",
                "conflict_resolution": "credit_report_on_direct_conflict",
                "few_shot_is_evidence": False,
                "few_shot_sanitized": True,
                **selection,
                "credit_report_available": any(row.source_class == "credit_report" for row in kept_evidence),
                "attachment_evidence_available": any(row.source_class == "attachment" for row in kept_evidence),
                "direct_conflicts": conflicts,
            },
        )
