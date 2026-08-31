from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from openpyxl import Workbook

from semantic_prompt_transfer import (
    CaseContext,
    CpuGenerationConfig,
    CreditReportParser,
    CreditReportTemplate,
    DocumentScope,
    FewShotRegistry,
    FewShotSelector,
    DocumentKind,
    DocumentLifecycleService,
    FileStatus,
    InMemoryVectorStore,
    LocalDocumentArtifactStore,
    OperationalApplicationService,
    OperationalRegistry,
    OfflineIndexBuilder,
    OnlineRAGService,
    PipelineConfig,
    ReviewGenerationOrchestrator,
    default_cpu_generator,
)
from semantic_prompt_transfer._chunk_builder_base import ChunkRecord


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
    opinion_path = output / "SemanticPromptTransfer_v0.21_SMOKE_OPINION.docx"
    cpu_generator = default_cpu_generator(
        CpuGenerationConfig(local_files_only=True, max_new_tokens=192)
    )
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
        cpu_generator,
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

    storage = LocalDocumentArtifactStore(output / "storage")
    registry = OperationalRegistry(output / "operational_registry.sqlite")
    vector = InMemoryVectorStore()
    lifecycle = DocumentLifecycleService(registry, vector, storage)
    application = OperationalApplicationService(registry, lifecycle)
    ui_scope = DocumentScope("example", "case-001", "ui-delete-doc")
    ui_source = storage.put(ui_scope, "ui-delete.pdf", b"sample pdf")
    application.register_upload(
        ui_scope,
        filename=ui_source.name,
        document_kind=DocumentKind.ATTACHMENT,
        size_bytes=ui_source.stat().st_size,
        storage_uri=str(ui_source),
    )
    for file_status in (
        FileStatus.VALIDATING,
        FileStatus.PARSING,
        FileStatus.INDEXING,
        FileStatus.READY,
    ):
        application.update_upload(ui_scope, file_status)
    vector.upsert_document(
        [
            ChunkRecord(
                "CH_UI_1",
                "sample",
                "sample",
                {
                    "tenant_id": ui_scope.tenant_id,
                    "case_id": ui_scope.case_id,
                    "document_id": ui_scope.document_id,
                    "global_chunk_id": "GCH_UI_1",
                },
            )
        ],
        np.ones((1, 8), dtype=np.float32),
    )
    ui_before = application.list_uploads("example", "case-001")
    ui_delete = application.delete_upload(ui_scope)
    ui_after = application.list_uploads("example", "case-001")
    report = {
        "package_version": "0.21.0",
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
        "cpu_generator_backend": cpu_generator.last_backend,
        "cpu_generator_primary_error": cpu_generator.last_error,
        "delete_result": delete_result,
        "html_contract_before_delete": ui_before,
        "html_contract_delete_result": ui_delete,
        "html_contract_after_delete": ui_after,
        "remaining_document_ids": sorted(
            {hit["metadata"].get("document_id") for hit in remaining["hits"]}
        ),
    }
    (output / "SemanticPromptTransfer_v0.21_OPERATIONAL_SMOKE.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
