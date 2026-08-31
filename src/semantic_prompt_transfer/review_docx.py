from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .domain import CaseContext, ReviewItem, ReviewSectionDraft


class OpinionDocumentBuilder:
    """Render the validated five-item review opinion as a Word document."""

    def build(
        self,
        case: CaseContext,
        sections: Iterable[ReviewSectionDraft],
        output_path: str | Path,
        *,
        title: str = "여신 심사의견",
    ) -> Path:
        try:
            from docx import Document
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            from docx.shared import Cm, Pt
        except ImportError as exc:  # pragma: no cover - dependency error is environment-specific
            raise RuntimeError("python-docx is required for Word output") from exc

        by_item = {section.review_item: section for section in sections}
        missing = [item.value for item in ReviewItem.ordered() if item not in by_item]
        if missing:
            raise ValueError(f"review sections missing: {missing}")

        document = Document()
        section = document.sections[0]
        section.top_margin = Cm(1.9)
        section.bottom_margin = Cm(1.8)
        section.left_margin = Cm(2.1)
        section.right_margin = Cm(2.1)

        styles = document.styles
        for name in ("Normal", "Title", "Heading 1"):
            style = styles[name]
            style.font.name = "Arial"
            style.font.size = Pt(10.5 if name == "Normal" else 14 if name == "Heading 1" else 21)
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")

        title_paragraph = document.add_paragraph()
        title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title_paragraph.add_run(title)
        title_run.bold = True
        title_run.font.size = Pt(21)
        title_run.font.name = "Arial"
        title_run._element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")

        info = document.add_table(rows=4, cols=2)
        info.style = "Table Grid"
        labels = (
            ("심사건", case.case_id),
            ("여신유형", case.loan_type),
            ("산업분류", case.industry_code),
            ("대상기업", case.company_name or "미지정"),
        )
        for row, (label, value) in zip(info.rows, labels, strict=True):
            row.cells[0].text = label
            row.cells[1].text = value
            row.cells[0].paragraphs[0].runs[0].bold = True

        document.add_paragraph()
        korean_labels = ("가", "나", "다", "라", "마")
        for label, item in zip(korean_labels, ReviewItem.ordered(), strict=True):
            heading = document.add_paragraph(style="Heading 1")
            heading.paragraph_format.space_before = Pt(12)
            heading.paragraph_format.space_after = Pt(4)
            run = heading.add_run(f"{label}. {item.title}")
            run.bold = True
            body = document.add_paragraph(by_item[item].text)
            body.paragraph_format.line_spacing = 1.25
            body.paragraph_format.space_after = Pt(6)

        document.add_page_break()
        document.add_heading("근거 추적 정보", level=1)
        trace = document.add_table(rows=1, cols=2)
        trace.style = "Table Grid"
        trace.rows[0].cells[0].text = "심사항목"
        trace.rows[0].cells[1].text = "사용 근거 ID"
        for item in ReviewItem.ordered():
            cells = trace.add_row().cells
            cells[0].text = f"{item.value}. {item.title}"
            cells[1].text = ", ".join(by_item[item].evidence_ids) or "없음"

        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer.add_run("SemanticPromptTransfer v0.20 · 근거 추적형 생성문서")

        settings = document.settings.element
        update_fields = OxmlElement("w:updateFields")
        update_fields.set(qn("w:val"), "true")
        settings.append(update_fields)

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        document.save(target)
        return target
