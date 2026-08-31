from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any


class ReviewItem(str, Enum):
    MAJOR_ACCOUNTS = "A"
    PROFITABILITY = "B"
    FINANCIAL_STABILITY = "C"
    CASH_FLOW = "D"
    MAJOR_CUSTOMERS = "E"

    @property
    def title(self) -> str:
        return {
            ReviewItem.MAJOR_ACCOUNTS: "재무제표 주요계정(현황 및 향후전망)",
            ReviewItem.PROFITABILITY: "수익성(현황 및 향후전망)",
            ReviewItem.FINANCIAL_STABILITY: "재무안정성 및 자산의 질(현황 및 향후전망)",
            ReviewItem.CASH_FLOW: "현금흐름 및 채무상환능력(현황 및 향후전망)",
            ReviewItem.MAJOR_CUSTOMERS: "주요 매출처 및 매출비중 변동 추이",
        }[self]

    @classmethod
    def ordered(cls) -> tuple["ReviewItem", ...]:
        return tuple(cls)


class SourceTier(IntEnum):
    CREDIT_REPORT_ITEM = 1
    CREDIT_REPORT_COMMON = 2
    ATTACHMENT = 3


class DocumentKind(str, Enum):
    CREDIT_REPORT = "credit_report"
    ATTACHMENT = "attachment"
    GENERATED_OPINION = "generated_opinion"


class FileStatus(str, Enum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    PARSING = "PARSING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETED = "DELETED"

    @property
    def progress_stage(self) -> str:
        return {
            FileStatus.UPLOADED: "업로드 완료",
            FileStatus.VALIDATING: "파일검증",
            FileStatus.PARSING: "파일해석",
            FileStatus.INDEXING: "벡터임베딩",
            FileStatus.READY: "완료",
            FileStatus.FAILED: "실패",
            FileStatus.DELETING: "삭제중",
            FileStatus.DELETED: "삭제완료",
        }[self]

    @property
    def default_progress(self) -> int:
        return {
            FileStatus.UPLOADED: 0,
            FileStatus.VALIDATING: 25,
            FileStatus.PARSING: 45,
            FileStatus.INDEXING: 70,
            FileStatus.READY: 100,
            FileStatus.FAILED: 0,
            FileStatus.DELETING: 100,
            FileStatus.DELETED: 100,
        }[self]


class JobStage(str, Enum):
    QUEUED = "QUEUED"
    PRECHECK = "PRECHECK"
    CREDIT_REPORT_LOAD = "CREDIT_REPORT_LOAD"
    ATTACHMENT_RETRIEVAL = "ATTACHMENT_RETRIEVAL"
    ITEM_GENERATION = "ITEM_GENERATION"
    VALIDATING = "VALIDATING"
    DOCX_RENDER = "DOCX_RENDER"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CaseContext:
    tenant_id: str
    case_id: str
    loan_type: str
    industry_code: str
    company_name: str | None = None
    situation_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("tenant_id", "case_id", "loan_type", "industry_code"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")

    def as_metadata(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreditFact:
    fact_id: str
    field_id: str
    field_name: str
    value: Any
    unit: str | None
    period: str | None
    review_items: tuple[ReviewItem, ...]
    common: bool
    document_id: str
    source_filename: str
    sheet_name: str
    cell_range: str
    formula: str | None = None
    source_hash: str | None = None

    @property
    def tier(self) -> SourceTier:
        return SourceTier.CREDIT_REPORT_COMMON if self.common else SourceTier.CREDIT_REPORT_ITEM

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["review_items"] = [item.value for item in self.review_items]
        value["tier"] = int(self.tier)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CreditFact":
        return cls(
            fact_id=str(value["fact_id"]),
            field_id=str(value["field_id"]),
            field_name=str(value["field_name"]),
            value=value.get("value"),
            unit=value.get("unit"),
            period=value.get("period"),
            review_items=tuple(ReviewItem(str(item)) for item in value.get("review_items", [])),
            common=bool(value.get("common", False)),
            document_id=str(value["document_id"]),
            source_filename=str(value["source_filename"]),
            sheet_name=str(value["sheet_name"]),
            cell_range=str(value["cell_range"]),
            formula=value.get("formula"),
            source_hash=value.get("source_hash"),
        )


@dataclass(frozen=True)
class FewShotExample:
    example_id: str
    review_item: ReviewItem
    input_summary: str
    output_example: str
    version: str = "1"
    approval_status: str = "APPROVED"
    loan_types: tuple[str, ...] = ()
    industry_codes: tuple[str, ...] = ()
    situation_tags: tuple[str, ...] = ()
    style_tags: tuple[str, ...] = ()
    forbidden_tokens: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FewShotExample":
        return cls(
            example_id=str(value["example_id"]),
            review_item=ReviewItem(str(value["review_item_code"])),
            input_summary=str(value.get("input_summary") or ""),
            output_example=str(value.get("output_example") or ""),
            version=str(value.get("example_version") or value.get("version") or "1"),
            approval_status=str(value.get("approval_status") or "APPROVED").upper(),
            loan_types=tuple(str(x) for x in value.get("loan_types", [])),
            industry_codes=tuple(str(x) for x in value.get("industry_codes", [])),
            situation_tags=tuple(str(x) for x in value.get("situation_tags", [])),
            style_tags=tuple(str(x) for x in value.get("style_tags", [])),
            forbidden_tokens=tuple(str(x) for x in value.get("forbidden_tokens", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "review_item_code": self.review_item.value,
            "input_summary": self.input_summary,
            "output_example": self.output_example,
            "example_version": self.version,
            "approval_status": self.approval_status,
            "loan_types": list(self.loan_types),
            "industry_codes": list(self.industry_codes),
            "situation_tags": list(self.situation_tags),
            "style_tags": list(self.style_tags),
            "forbidden_tokens": list(self.forbidden_tokens),
        }


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    review_item: ReviewItem
    source_tier: SourceTier
    content: str
    document_id: str
    source_filename: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["review_item"] = self.review_item.value
        value["source_tier"] = int(self.source_tier)
        return value


@dataclass(frozen=True)
class ReviewSectionDraft:
    review_item: ReviewItem
    text: str
    evidence_ids: tuple[str, ...]
    validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_item": self.review_item.value,
            "title": self.review_item.title,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "validation": self.validation,
        }


@dataclass(frozen=True)
class ProgressEvent:
    stage: JobStage
    progress: int
    message: str
    review_item: ReviewItem | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "progress": self.progress,
            "message": self.message,
            "review_item": self.review_item.value if self.review_item else None,
        }
