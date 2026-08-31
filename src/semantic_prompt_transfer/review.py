from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .domain import CaseContext, CreditFact, EvidenceRecord, FewShotExample, ReviewItem, SourceTier
from .identity import evidence_id


class EvidenceAssembler:
    """Build evidence in the enforced order TIER 1 -> TIER 2 -> TIER 3."""

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
                tier = SourceTier.CREDIT_REPORT_COMMON
            else:
                if review_item not in fact.review_items:
                    continue
                tier = SourceTier.CREDIT_REPORT_ITEM
            rendered = f"{fact.field_name}={fact.value}"
            if fact.unit:
                rendered += f"; 단위={fact.unit}"
            if fact.period:
                rendered += f"; 기간={fact.period}"
            rows.append(
                EvidenceRecord(
                    evidence_id=evidence_id("CR", review_item.value, fact.fact_id),
                    review_item=review_item,
                    source_tier=tier,
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
                    },
                )
            )
        rows.sort(key=lambda row: (int(row.source_tier), row.evidence_id))
        return rows


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
    def __init__(self, max_context_chars: int = 24000) -> None:
        self.max_context_chars = int(max_context_chars)

    def build(
        self,
        case: CaseContext,
        review_item: ReviewItem,
        query: str,
        evidence: list[EvidenceRecord],
        few_shots: list[FewShotExample],
    ) -> ReviewPromptPackage:
        system = (
            "현재 심사건의 근거만 사실로 사용한다. 근거 우선순위는 "
            "TIER_1 신용조사서 항목자료, TIER_2 신용조사서 공통자료, "
            "TIER_3 기타 첨부파일 순서다. FEW SHOT은 문체와 분석 구조만 참고하며 "
            "그 안의 수치, 회사명, 기간 또는 사실을 현재 심사건에 사용하지 않는다. "
            "수치·단위·기간을 변형하지 않고 핵심 주장마다 evidence_id를 표시한다. "
            "자료가 충돌하면 신용조사서를 기준으로 하고 차이를 명시한다."
        )
        example_blocks = []
        for example in few_shots:
            example_blocks.append(
                f"[STYLE_ONLY_FEW_SHOT {example.example_id}]\n"
                f"입력상황: {example.input_summary}\n"
                f"작성예시: {example.output_example}\n"
                "주의: 이 예시의 사실과 수치는 현재 심사건 근거가 아니다."
            )

        evidence_blocks = []
        used = 0
        kept_evidence: list[EvidenceRecord] = []
        for row in evidence:
            block = (
                f"[TIER_{int(row.source_tier)} EVIDENCE]\n"
                f"evidence_id={row.evidence_id}\n"
                f"document_id={row.document_id}\n"
                f"source_filename={row.source_filename}\n"
                f"page={row.page}\n"
                f"content={row.content}"
            )
            if evidence_blocks and used + len(block) > self.max_context_chars:
                break
            used += len(block)
            evidence_blocks.append(block)
            kept_evidence.append(row)

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
            + "\n\n[작성요청]\n현황, 주요 원인, 위험·완화요인 및 향후전망을 근거 중심으로 작성하라."
        )
        return ReviewPromptPackage(
            schema_version="review-prompt-1.0",
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
                "evidence_priority": [1, 2, 3],
                "few_shot_is_evidence": False,
                "context_characters": used,
            },
        )
