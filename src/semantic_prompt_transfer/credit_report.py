from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DocumentScope
from .domain import CreditFact, ReviewItem
from .identity import evidence_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class CreditFieldMapping:
    field_id: str
    field_name: str
    sheet_name: str
    cell_range: str
    review_items: tuple[ReviewItem, ...] = ()
    common: bool = False
    unit: str | None = None
    unit_cell: str | None = None
    period: str | None = None
    period_cell: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CreditFieldMapping":
        review_items = tuple(ReviewItem(str(item)) for item in value.get("review_items", []))
        common = bool(value.get("common", False))
        if not common and not review_items:
            raise ValueError(f"field {value.get('field_id')} needs review_items or common=true")
        return cls(
            field_id=str(value["field_id"]),
            field_name=str(value.get("field_name") or value["field_id"]),
            sheet_name=str(value["sheet_name"]),
            cell_range=str(value.get("cell_range") or value.get("cell")),
            review_items=review_items,
            common=common,
            unit=value.get("unit"),
            unit_cell=value.get("unit_cell"),
            period=value.get("period"),
            period_cell=value.get("period_cell"),
        )


@dataclass(frozen=True)
class CreditReportTemplate:
    template_id: str
    version: str
    mappings: tuple[CreditFieldMapping, ...]

    @classmethod
    def from_json(cls, path: str | Path) -> "CreditReportTemplate":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            template_id=str(value["template_id"]),
            version=str(value["version"]),
            mappings=tuple(CreditFieldMapping.from_dict(row) for row in value["mappings"]),
        )


@dataclass(frozen=True)
class CreditReportParseResult:
    template_id: str
    template_version: str
    source_hash: str
    facts: tuple[CreditFact, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "template_version": self.template_version,
            "source_hash": self.source_hash,
            "facts": [fact.to_dict() for fact in self.facts],
        }


class CreditReportParser:
    """Extract mapped values from a versioned, standardized Excel workbook."""

    def parse(
        self,
        workbook_path: str | Path,
        template: CreditReportTemplate,
        scope: DocumentScope,
    ) -> CreditReportParseResult:
        if scope.document_kind != "credit_report":
            raise ValueError("credit report parsing requires document_kind='credit_report'")
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover - dependency error is environment-specific
            raise RuntimeError("openpyxl is required to parse credit reports") from exc

        source = Path(workbook_path)
        values_book = load_workbook(source, data_only=True, read_only=False)
        formula_book = load_workbook(source, data_only=False, read_only=False)
        source_hash = _sha256(source)
        facts: list[CreditFact] = []

        for mapping in template.mappings:
            if mapping.sheet_name not in values_book.sheetnames:
                raise ValueError(f"missing sheet: {mapping.sheet_name}")
            values_sheet = values_book[mapping.sheet_name]
            formula_sheet = formula_book[mapping.sheet_name]
            unit = mapping.unit or (
                str(values_sheet[mapping.unit_cell].value) if mapping.unit_cell else None
            )
            period = mapping.period or (
                str(values_sheet[mapping.period_cell].value) if mapping.period_cell else None
            )
            cells = values_sheet[mapping.cell_range]
            if not isinstance(cells, tuple):
                cells = ((cells,),)
            elif cells and not isinstance(cells[0], tuple):
                cells = (cells,)
            for row in cells:
                for cell in row:
                    if cell.value is None or str(cell.value).strip() == "":
                        continue
                    coordinate = cell.coordinate
                    formula_value = formula_sheet[coordinate].value
                    formula = formula_value if isinstance(formula_value, str) and formula_value.startswith("=") else None
                    field_id = mapping.field_id if mapping.cell_range == coordinate else f"{mapping.field_id}:{coordinate}"
                    facts.append(
                        CreditFact(
                            fact_id=evidence_id(
                                "FACT",
                                scope.tenant_id,
                                scope.case_id,
                                scope.document_id,
                                field_id,
                                coordinate,
                            ),
                            field_id=field_id,
                            field_name=mapping.field_name,
                            value=cell.value,
                            unit=unit,
                            period=period,
                            review_items=mapping.review_items,
                            common=mapping.common,
                            document_id=scope.document_id,
                            source_filename=scope.source_filename or source.name,
                            sheet_name=mapping.sheet_name,
                            cell_range=coordinate,
                            formula=formula,
                            source_hash=source_hash,
                        )
                    )
        return CreditReportParseResult(
            template_id=template.template_id,
            template_version=template.version,
            source_hash=source_hash,
            facts=tuple(facts),
        )
