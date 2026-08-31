from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from semantic_prompt_transfer import (
    CreditReportTemplate,
    DocumentKind,
    DocumentScope,
    E5OnnxEncoder,
    EphemeralColabConfig,
    EphemeralColabRuntime,
    EphemeralReviewJobService,
    EvidenceTemplateGenerator,
    FewShotRegistry,
    FewShotSelector,
    PocIdentityService,
    PocUploadProcessor,
    ShardedAttachmentRetriever,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register_and_process(
    runtime: EphemeralColabRuntime,
    processor: PocUploadProcessor,
    scope: DocumentScope,
    source: Path,
    kind: DocumentKind,
) -> None:
    stored = runtime.artifacts.put(scope, source.name, source.read_bytes())
    runtime.application.register_upload(
        scope,
        filename=source.name,
        document_kind=kind,
        size_bytes=stored.stat().st_size,
        storage_uri=str(stored),
        source_hash=digest(stored),
        derived_uri=str(runtime.artifacts.derived_path(scope)),
    )
    processor.process(
        scope,
        stored,
        kind,
        lambda status, progress, message: runtime.application.update_upload(
            scope, status, progress=progress, message=message
        ),
    )


def main(
    output_dir: str,
    model_dir: str,
    credit_workbook: str,
    first_pdf: str,
    second_pdf: str,
) -> int:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    package_root = Path(__file__).resolve().parents[1]
    example_root = package_root / "src/semantic_prompt_transfer/examples/operational"
    sources = [Path(credit_workbook).resolve(), Path(first_pdf).resolve(), Path(second_pdf).resolve()]
    if not all(path.is_file() for path in sources):
        raise FileNotFoundError([str(path) for path in sources if not path.is_file()])

    with tempfile.TemporaryDirectory(prefix="spt-v022-") as tmp:
        runtime_root = Path(tmp) / "runtime"
        runtime = EphemeralColabRuntime(
            EphemeralColabConfig(
                root=runtime_root,
                require_content_root=False,
                clean_start=True,
            )
        )
        identities = PocIdentityService(runtime.root / "metadata/identity.sqlite")
        try:
            identities.register(
                department="기업심사부", name="POC 검증사용자", employee_number="POC22001"
            )
            grant = identities.login("POC22001", "POC22001")
            tenant_id = grant.session.tenant_id
            case_id = grant.session.case_id

            smoke_workbook = Path(tmp) / "filled_credit_report.xlsx"
            shutil.copy2(sources[0], smoke_workbook)
            workbook = load_workbook(smoke_workbook)
            workbook["기초자료"]["B2"] = 120000
            workbook["기초자료"]["C2"] = "2025"
            workbook["기초자료"]["B3"] = 8500
            workbook["기초자료"]["C3"] = "2025"
            workbook["공통"]["B2"] = "샘플 제조기업의 운전자금 심사"
            workbook.save(smoke_workbook)
            workbook.close()

            encoder = E5OnnxEncoder(model_dir, batch_size=4)
            processor = PocUploadProcessor(
                encoder,
                runtime.vectors,
                runtime.artifacts,
                credit_template=CreditReportTemplate.from_json(
                    example_root / "credit_report_template.json"
                ),
            )
            credit_scope = DocumentScope(tenant_id, case_id, "credit-report")
            first_scope = DocumentScope(tenant_id, case_id, "attachment-a")
            second_scope = DocumentScope(tenant_id, case_id, "attachment-b")
            register_and_process(
                runtime, processor, credit_scope, smoke_workbook, DocumentKind.CREDIT_REPORT
            )
            register_and_process(
                runtime, processor, first_scope, sources[1], DocumentKind.ATTACHMENT
            )
            register_and_process(
                runtime, processor, second_scope, sources[2], DocumentKind.ATTACHMENT
            )

            vector_count_before = runtime.vectors.count(
                {"tenant_id": tenant_id, "case_id": case_id}
            )
            shard_count_before = len(list((runtime.root / "vectors").glob("*.npz")))
            retriever = ShardedAttachmentRetriever(encoder, runtime.vectors, top_k=5)
            retrieval = retriever.search(
                "매출액과 차입금 상환 조건 및 재무안정성",
                filters={"tenant_id": tenant_id, "case_id": case_id},
            )
            hit_document_ids = sorted(
                {str(row["metadata"]["document_id"]) for row in retrieval["hits"]}
            )

            review_jobs = EphemeralReviewJobService(
                runtime,
                retriever,
                FewShotSelector(FewShotRegistry.from_json(example_root / "few_shots.json")),
                EvidenceTemplateGenerator(),
                loan_type="운전자금",
                industry_code="C29",
                company_name="POC 샘플기업",
            )
            job = review_jobs.start(tenant_id, case_id)
            generated = review_jobs.run(str(job["job_id"]))
            opinion_output = output / "SemanticPromptTransfer_v0.22_SMOKE_OPINION.docx"
            shutil.copy2(generated.output_path, opinion_output)

            first_record = runtime.registry.get_document(
                tenant_id, case_id, first_scope.document_id
            )
            first_original = Path(first_record.storage_uri or "")
            deletion = runtime.application.delete_upload(first_scope)
            vector_count_after_document_delete = runtime.vectors.count(
                {"tenant_id": tenant_id, "case_id": case_id}
            )
            remaining_ids = sorted(
                {
                    row["metadata"]["document_id"]
                    for row in retriever.search(
                        "차입금 상환 조건",
                        filters={"tenant_id": tenant_id, "case_id": case_id},
                    )["hits"]
                }
            )
            health_before_close = runtime.health()
            report = {
                "package_version": health_before_close["version"],
                "storage_mode": health_before_close["storage_mode"],
                "persistent_storage": health_before_close["persistent_storage"],
                "registered_users": identities.user_count(),
                "session_scope": {
                    "tenant_id": tenant_id,
                    "case_id_is_hashed": case_id.startswith("poc-") and "POC22001" not in case_id,
                },
                "input_documents": {
                    "credit_report": 1,
                    "attachments": 2,
                    "download_template_sha256": digest(sources[0]),
                    "parsed_credit_fact_count": len(
                        review_jobs.facts.load(tenant_id, case_id)
                    ),
                },
                "vector_count_before_delete": vector_count_before,
                "document_shards_before_delete": shard_count_before,
                "retrieval_document_ids": hit_document_ids,
                "generated_sections": len(generated.sections),
                "job_progress": runtime.registry.get_job(str(job["job_id"])).progress,
                "opinion_docx": opinion_output.name,
                "document_delete": deletion,
                "deleted_original_absent": not first_original.exists(),
                "deleted_document_vectors": runtime.vectors.count(
                    {
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "document_id": first_scope.document_id,
                    }
                ),
                "vector_count_after_document_delete": vector_count_after_document_delete,
                "remaining_attachment_document_ids": remaining_ids,
                "encoder": encoder.metadata(),
            }
        finally:
            identities.close()
            close_result = runtime.close(purge=True)

        report["runtime_close"] = close_result
        report["runtime_root_absent"] = not runtime_root.exists()
        report_path = output / "SemanticPromptTransfer_v0.22_COLAB_POC_SMOKE.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:6]))
