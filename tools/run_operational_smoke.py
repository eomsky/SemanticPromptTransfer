from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from openpyxl import Workbook

from semantic_prompt_transfer import (
    CaseContext,
    CreditReportParser,
    CreditReportTemplate,
    DocumentScope,
    FewShotRegistry,
    FewShotSelector,
    OfflineIndexBuilder,
    OnlineRAGService,
    PipelineConfig,
    ReviewGenerationOrchestrator,
)


class CitationOnlyLLM:
    def generate(self, messages):
        match = re.search(r"evidence_id=([^\n]+)", messages[1]["content"])
        if not match:
            raise RuntimeError("smoke prompt has no evidence")
        return "제공된 근거를 기준으로 현황과 주요 변동요인 및 향후 확인사항을 검토하였다. " + match.group(1)


def master(text: str):
    return {
        "annotation": {
            "page_furniture": [],
            "body_text": [
                {
                    "object_id": "B1",
                    "page": 1,
                    "bbox": [10, 20, 100, 40],
                    "text": text,
                    "scope_heading_id": "H1",
                }
            ],
        },
        "semantic_elements": {
            "headings": [
                {
                    "element_id": "H1",
                    "text": "재무에 관한 사항",
                    "parent_id": None,
                    "page": 1,
                    "bbox": [10, 5, 100, 15],
                }
            ],
            "tables": [],
        },
        "raw_document": {"physical_tables": []},
    }


def main(output_dir: str, model_dir: str) -> int:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    package_root = Path(__file__).resolve().parents[1]
    example_root = package_root / "src" / "semantic_prompt_transfer" / "examples" / "operational"

    master_a = output / "attachment_a_MASTER.json"
    master_b = output / "attachment_b_MASTER.json"
    master_a.write_text(
        json.dumps(master("주요 매출처의 매출비중과 계약 지속성을 검토한다."), ensure_ascii=False),
        encoding="utf-8",
    )
    master_b.write_text(
        json.dumps(master("영업현금흐름과 차입금 상환재원을 검토한다."), ensure_ascii=False),
        encoding="utf-8",
    )

    index_path = output / "operational_smoke_index.npz"
    builder = OfflineIndexBuilder(
        PipelineConfig.for_index_build(
            model_dir=model_dir,
            index_path=index_path,
            representation_level=0,
            index_write_strategy="UPSERT",
        )
    )
    scope_a = DocumentScope("example", "case-001", "attachment-a", source_filename="attachment-a.pdf")
    scope_b = DocumentScope("example", "case-001", "attachment-b", source_filename="attachment-b.pdf")
    builder.build(master_a, scope_a)
    stats = builder.build(master_b, scope_b)

    workbook_path = output / "sample_credit_report.xlsx"
    workbook = Workbook()
    base = workbook.active
    base.title = "기초자료"
    base["B2"] = 120000
    base["C2"] = "2025"
    base["B3"] = 8500
    base["C3"] = "2025"
    common = workbook.create_sheet("공통")
    common["B2"] = "샘플 제조기업으로 운전자금 심사를 진행한다."
    workbook.save(workbook_path)

    credit = CreditReportParser().parse(
        workbook_path,
        CreditReportTemplate.from_json(example_root / "credit_report_template.json"),
        DocumentScope(
            "example",
            "case-001",
            "credit-report",
            source_filename=workbook_path.name,
            document_kind="credit_report",
        ),
    )

    online = OnlineRAGService(
        PipelineConfig.for_serving(
            model_dir=model_dir,
            index_path=index_path,
            representation_level=0,
            top_k=2,
        )
    )
    health = online.start()
    selector = FewShotSelector(FewShotRegistry.from_json(example_root / "few_shots.json"))
    opinion_path = output / "SemanticPromptTransfer_v0.20_SMOKE_OPINION.docx"
    generation = ReviewGenerationOrchestrator(online, selector).generate(
        CaseContext(
            tenant_id="example",
            case_id="case-001",
            loan_type="운전자금",
            industry_code="C29",
            company_name="샘플기업",
            situation_tags=("매출성장",),
        ),
        list(credit.facts),
        CitationOnlyLLM(),
        opinion_path,
    )

    delete_result = builder.delete(scope_a)
    after_delete = OnlineRAGService(
        PipelineConfig.for_serving(
            model_dir=model_dir,
            index_path=index_path,
            representation_level=0,
            top_k=5,
        )
    )
    after_delete.start()
    remaining = after_delete.search(
        "현금흐름",
        filters={"tenant_id": "example", "case_id": "case-001"},
    )
    report = {
        "package_version": "0.20.0",
        "real_encoder": True,
        "initial_index_stats": stats,
        "initial_health": health,
        "credit_fact_count": len(credit.facts),
        "generated_sections": len(generation.sections),
        "selected_few_shots": {
            prompt.review_item.value: [row["example_id"] for row in prompt.few_shots]
            for prompt in generation.prompts
        },
        "evidence_tiers": {
            prompt.review_item.value: [row["source_tier"] for row in prompt.evidence]
            for prompt in generation.prompts
        },
        "progress": [event.to_dict() for event in generation.progress_events],
        "opinion_path": str(opinion_path),
        "delete_result": delete_result,
        "remaining_document_ids": sorted(
            {hit["metadata"].get("document_id") for hit in remaining["hits"]}
        ),
    }
    (output / "SemanticPromptTransfer_v0.20_OPERATIONAL_SMOKE.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
