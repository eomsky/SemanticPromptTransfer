from __future__ import annotations

import io
import re
import textwrap
from pathlib import Path
from typing import Any

from .colab_runtime import EphemeralColabRuntime


class EvidenceCaptureService:
    """Render a bounded, case-scoped source excerpt for one cited evidence ID."""

    def __init__(self, runtime: EphemeralColabRuntime) -> None:
        self.runtime = runtime

    def _document(self, tenant_id: str, case_id: str, evidence: dict[str, Any]):
        document_id = str(evidence.get("document_id") or "")
        document = self.runtime.registry.get_document(tenant_id, case_id, document_id)
        if not document.storage_uri:
            raise RuntimeError("evidence source file is unavailable")
        source = Path(document.storage_uri).expanduser().resolve()
        if not source.is_relative_to(self.runtime.root.resolve()) or not source.is_file():
            raise PermissionError("evidence source is outside the case runtime")
        return document, source

    @staticmethod
    def _font(size: int):
        from PIL import ImageFont

        candidates = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
        for candidate in candidates:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size=size)
        return ImageFont.load_default()

    @staticmethod
    def _png(image) -> bytes:
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def describe(
        self,
        tenant_id: str,
        case_id: str,
        evidence_id: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        document, source = self._document(tenant_id, case_id, evidence)
        metadata = dict(evidence.get("metadata") or {})
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            kind = "pdf"
            location = f"{int(evidence.get('page') or 1)}페이지"
        elif suffix == ".xlsx":
            kind = "xlsx"
            sheet_name, cell_range = self._xlsx_location(metadata)
            location = f"{sheet_name or '시트'} · {cell_range or '범위'}"
        else:
            kind = "text"
            location = str(metadata.get("source_location") or "원문")
        return {
            "evidence_id": evidence_id,
            "kind": kind,
            "source_tier": int(evidence.get("source_tier") or 3),
            "filename": document.filename,
            "location": location,
            "excerpt": str(evidence.get("content") or ""),
            "capture_available": True,
        }

    def capture_png(
        self,
        tenant_id: str,
        case_id: str,
        evidence: dict[str, Any],
    ) -> bytes:
        _, source = self._document(tenant_id, case_id, evidence)
        if source.suffix.lower() == ".pdf":
            return self._pdf(source, evidence)
        if source.suffix.lower() == ".xlsx":
            return self._xlsx(source, evidence)
        return self._text(evidence)

    def _pdf(self, source: Path, evidence: dict[str, Any]) -> bytes:
        import fitz
        from PIL import Image, ImageDraw

        metadata = dict(evidence.get("metadata") or {})
        page_number = max(1, int(evidence.get("page") or 1))
        document = fitz.open(str(source))
        try:
            if page_number > document.page_count:
                raise ValueError("evidence page is outside the PDF")
            page = document[page_number - 1]
            raw_bbox = metadata.get("bbox")
            if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
                bbox = fitz.Rect(*(float(value) for value in raw_bbox))
            else:
                bbox = page.rect
            margin = 28.0
            clip = fitz.Rect(
                max(page.rect.x0, bbox.x0 - margin),
                max(page.rect.y0, bbox.y0 - margin),
                min(page.rect.x1, bbox.x1 + margin),
                min(page.rect.y1, bbox.y1 + margin),
            )
            matrix = fitz.Matrix(2.0, 2.0)
            pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            draw = ImageDraw.Draw(image, "RGBA")
            if bbox != page.rect:
                left = max(0, int((bbox.x0 - clip.x0) * 2))
                top = max(0, int((bbox.y0 - clip.y0) * 2))
                right = min(image.width - 1, int((bbox.x1 - clip.x0) * 2))
                bottom = min(image.height - 1, int((bbox.y1 - clip.y0) * 2))
                draw.rectangle((left, top, right, bottom), fill=(255, 213, 0, 55), outline=(255, 160, 0, 255), width=6)
            return self._png(image)
        finally:
            document.close()

    def _xlsx(self, source: Path, evidence: dict[str, Any]) -> bytes:
        from openpyxl import load_workbook
        from openpyxl.utils.cell import range_boundaries
        from PIL import Image, ImageDraw

        metadata = dict(evidence.get("metadata") or {})
        sheet_name, cell_range = self._xlsx_location(metadata)
        workbook = load_workbook(source, data_only=True, read_only=False)
        try:
            if sheet_name not in workbook.sheetnames:
                raise ValueError("evidence sheet is unavailable")
            sheet = workbook[sheet_name]
            if cell_range.startswith("ROW:"):
                from openpyxl.utils import get_column_letter

                row_number = max(1, int(cell_range.split(":", 1)[1]))
                cell_range = f"A{row_number}:{get_column_letter(min(max(1, sheet.max_column), 12))}{row_number}"
            min_col, min_row, max_col, max_row = range_boundaries(cell_range)
            first_row = max(1, min_row - 2)
            last_row = min(sheet.max_row, max_row + 2)
            first_col = max(1, min_col - 1)
            last_col = min(sheet.max_column, max(max_col + 1, first_col + 3), 12)
            columns = last_col - first_col + 1
            rows = last_row - first_row + 1
            column_width, row_height = 180, 42
            title_height = 58
            image = Image.new("RGB", (columns * column_width + 2, rows * row_height + title_height + 2), "white")
            draw = ImageDraw.Draw(image)
            font = self._font(17)
            small = self._font(14)
            draw.text((12, 14), f"{sheet_name} · {cell_range}", fill="#20242b", font=font)
            for row_offset, row_number in enumerate(range(first_row, last_row + 1)):
                for col_offset, col_number in enumerate(range(first_col, last_col + 1)):
                    x0 = col_offset * column_width + 1
                    y0 = title_height + row_offset * row_height + 1
                    x1, y1 = x0 + column_width, y0 + row_height
                    highlighted = min_row <= row_number <= max_row and min_col <= col_number <= max_col
                    draw.rectangle(
                        (x0, y0, x1, y1),
                        fill="#fff3ad" if highlighted else "#ffffff",
                        outline="#ff9f00" if highlighted else "#9da7b3",
                        width=4 if highlighted else 1,
                    )
                    value = sheet.cell(row_number, col_number).value
                    text = "" if value is None else " ".join(str(value).split())
                    if len(text) > 20:
                        text = text[:19] + "…"
                    draw.text((x0 + 7, y0 + 11), text, fill="#20242b", font=small)
            return self._png(image)
        finally:
            workbook.close()

    @staticmethod
    def _xlsx_location(metadata: dict[str, Any]) -> tuple[str, str]:
        sheet_name = str(metadata.get("sheet_name") or "")
        cell_range = str(metadata.get("cell_range") or "")
        if sheet_name and cell_range:
            return sheet_name, cell_range
        source_location = str(metadata.get("source_location") or "")
        matched = re.fullmatch(r"sheet:(.+):row:(\d+)", source_location)
        if matched:
            return matched.group(1), f"ROW:{matched.group(2)}"
        return sheet_name, cell_range or "A1"

    def _text(self, evidence: dict[str, Any]) -> bytes:
        from PIL import Image, ImageDraw

        content = " ".join(str(evidence.get("content") or "").split())
        lines = textwrap.wrap(content, width=58)[:12] or ["원문을 표시할 수 없습니다."]
        image = Image.new("RGB", (1100, 90 + len(lines) * 34), "white")
        draw = ImageDraw.Draw(image, "RGBA")
        font = self._font(19)
        draw.rectangle((20, 20, image.width - 20, image.height - 20), fill=(255, 229, 120, 100), outline=(255, 160, 0, 255), width=5)
        for index, line in enumerate(lines):
            draw.text((38, 40 + index * 34), line, fill="#20242b", font=font)
        return self._png(image)
