from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .domain import EvidenceRecord, ReviewItem

_ROW = re.compile(r"(\d+)")


def _first_row(cell_range: str) -> int | None:
    match = _ROW.search(str(cell_range or ""))
    return int(match.group(1)) if match else None


def visual_group_key(row: EvidenceRecord) -> tuple[Any, ...]:
    """Stable human-view grouping key, separate from retrieval identity.

    Several RAG chunks may be useful to the model while pointing to the same page/table
    that a human would regard as one source view.  This key is used for evidence
    clustering and for diversity preference; it never changes the raw evidence id.
    """
    metadata = dict(row.metadata or {})
    if row.source_class == "credit_report":
        sheet = str(metadata.get("sheet_name") or "")
        cell_range = str(metadata.get("cell_range") or "")
        row_no = _first_row(cell_range)
        # Approximate a single worksheet viewport without collapsing distant tables.
        band = (row_no - 1) // 12 if row_no else cell_range
        return ("credit_report", row.document_id, sheet, band)

    logical_table = str(metadata.get("logical_table_id") or "").strip()
    source_location = str(metadata.get("source_location") or "").strip()
    if logical_table:
        region = ("table", logical_table)
    elif source_location:
        region = ("location", source_location)
    else:
        bbox = metadata.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            region = ("bbox", *(round(float(v) / 90.0) for v in bbox))
        else:
            region = ("page",)
    return ("attachment", row.document_id, row.page or 0, region)


@dataclass(frozen=True)
class EvidenceReference:
    ref_no: int
    representative_id: str
    member_ids: tuple[str, ...]
    source_class: str
    source_filename: str
    page: int | None
    location: str
    review_items: tuple[str, ...]
    highlight_bboxes: tuple[tuple[float, float, float, float], ...] = ()
    highlight_cell_ranges: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_no": self.ref_no,
            "representative_id": self.representative_id,
            "member_ids": list(self.member_ids),
            "source_class": self.source_class,
            "source_filename": self.source_filename,
            "page": self.page,
            "location": self.location,
            "review_items": list(self.review_items),
            "highlight_bboxes": [list(v) for v in self.highlight_bboxes],
            "highlight_cell_ranges": list(self.highlight_cell_ranges),
        }


class EvidenceTraceLedger:
    """Map many retrieval chunks to a compact set of human-verifiable source views."""

    def __init__(self) -> None:
        self._groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._id_to_group: dict[str, tuple[Any, ...]] = {}
        self._next_ref = 1

    @staticmethod
    def _location(row: EvidenceRecord) -> str:
        metadata = dict(row.metadata or {})
        if row.source_class == "credit_report":
            sheet = str(metadata.get("sheet_name") or "시트")
            cell = str(metadata.get("cell_range") or "범위")
            return f"{sheet} · {cell}"
        label = str(metadata.get("logical_table_id") or metadata.get("source_location") or "").strip()
        page = f"{row.page}페이지" if row.page else "원문"
        return f"{page} · {label}" if label else page

    def register(
        self,
        item: ReviewItem,
        evidence: Iterable[EvidenceRecord],
        cited_ids: Iterable[str],
    ) -> tuple[EvidenceReference, ...]:
        by_id = {row.evidence_id: row for row in evidence}
        ordered = [str(value) for value in cited_ids if str(value) in by_id]
        seen_ids: set[str] = set()
        section_groups: list[tuple[Any, ...]] = []
        for evidence_id in ordered:
            if evidence_id in seen_ids:
                continue
            seen_ids.add(evidence_id)
            row = by_id[evidence_id]
            key = visual_group_key(row)
            group = self._groups.get(key)
            if group is None:
                group = {
                    "ref_no": self._next_ref,
                    "representative_id": evidence_id,
                    "member_ids": [],
                    "source_class": row.source_class,
                    "source_filename": str(row.source_filename or "업로드 자료"),
                    "page": row.page,
                    "location": self._location(row),
                    "review_items": [],
                    "highlight_bboxes": [],
                    "highlight_cell_ranges": [],
                }
                self._next_ref += 1
                self._groups[key] = group
            if evidence_id not in group["member_ids"]:
                group["member_ids"].append(evidence_id)
            if item.value not in group["review_items"]:
                group["review_items"].append(item.value)
            metadata = dict(row.metadata or {})
            bbox = metadata.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                normalized = tuple(float(v) for v in bbox)
                if normalized not in group["highlight_bboxes"]:
                    group["highlight_bboxes"].append(normalized)
            cell_range = str(metadata.get("cell_range") or "").strip()
            if cell_range and cell_range not in group["highlight_cell_ranges"]:
                group["highlight_cell_ranges"].append(cell_range)
            self._id_to_group[evidence_id] = key
            if key not in section_groups:
                section_groups.append(key)
        return tuple(self._freeze(self._groups[key]) for key in section_groups)

    @staticmethod
    def _freeze(value: dict[str, Any]) -> EvidenceReference:
        return EvidenceReference(
            ref_no=int(value["ref_no"]),
            representative_id=str(value["representative_id"]),
            member_ids=tuple(str(v) for v in value["member_ids"]),
            source_class=str(value["source_class"]),
            source_filename=str(value["source_filename"]),
            page=int(value["page"]) if value.get("page") is not None else None,
            location=str(value["location"]),
            review_items=tuple(str(v) for v in value["review_items"]),
            highlight_bboxes=tuple(tuple(float(x) for x in box) for box in value["highlight_bboxes"]),
            highlight_cell_ranges=tuple(str(v) for v in value["highlight_cell_ranges"]),
        )

    def ref_no_for(self, evidence_id: str) -> int | None:
        key = self._id_to_group.get(str(evidence_id))
        return int(self._groups[key]["ref_no"]) if key in self._groups else None

    def all_references(self) -> tuple[EvidenceReference, ...]:
        return tuple(
            self._freeze(group)
            for group in sorted(self._groups.values(), key=lambda value: int(value["ref_no"]))
        )
