from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
from openpyxl import Workbook

from semantic_prompt_transfer import (
    CaseContext,
    CreditFact,
    CreditFieldMapping,
    CreditReportParser,
    CreditReportTemplate,
    DocumentScope,
    DocumentKind,
    DocumentLifecycleService,
    EvidenceAssembler,
    EvidenceRecord,
    FewShotExample,
    FewShotRegistry,
    FewShotSelector,
    FileStatus,
    InMemoryVectorStore,
    LocalDocumentArtifactStore,
    OperationalApplicationService,
    OpinionDocumentBuilder,
    OpinionValidator,
    OperationalRegistry,
    PipelineConfig,
    RAGIndex,
    RAGPipeline,
    RepresentationLevel,
    ReviewGenerationOrchestrator,
    ReviewItem,
    ReviewPromptBuilder,
    ReviewSectionDraft,
    SourceTier,
    EvidenceTemplateGenerator,
    FallbackGenerator,
    TransformersCpuGenerator,
)
from semantic_prompt_transfer._chunk_builder_base import ChunkRecord
from semantic_prompt_transfer.encoding import EncoderBackend
from semantic_prompt_transfer.operations import OnlineRAGService
from semantic_prompt_transfer.cli import build_parser


class FakeEncoder(EncoderBackend):
    dimension = 8

    @staticmethod
    def _matrix(texts):
        rows = []
        for text in texts:
            value = str(text)
            row = np.zeros(8, dtype=np.float32)
            for index, char in enumerate(value):
                row[(ord(char) + index) % 8] += 1.0
            row /= max(float(np.linalg.norm(row)), 1e-9)
            rows.append(row)
        return np.vstack(rows) if rows else np.empty((0, 8), dtype=np.float32)

    def encode_documents(self, texts):
        return self._matrix(list(texts))

    def encode_queries(self, texts):
        return self._matrix(list(texts))

    def metadata(self):
        return {
            "provider": "fake",
            "model_id": "test/fake",
            "model_sha256": "fake-sha256",
            "dimension": self.dimension,
        }


def minimal_master():
    return {
        "annotation": {
            "page_furniture": [],
            "body_text": [
                {
                    "object_id": "B1",
                    "page": 1,
                    "bbox": [10, 20, 100, 40],
                    "text": "당기 현금및현금성자산과 단기차입금 비교",
                    "scope_heading_id": "H1",
                },
                {
                    "object_id": "B2",
                    "page": 1,
                    "bbox": [10, 50, 100, 70],
                    "text": "단위 천원",
                    "scope_heading_id": "H1",
                },
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


class PackageTests(unittest.TestCase):
    def test_default_is_l0_memory(self):
        config = PipelineConfig(model_dir="unused")
        self.assertEqual(config.representation_level, RepresentationLevel.PLAIN)
        self.assertEqual(config.index_mode.value, "MEMORY")
        self.assertFalse(config.is_experimental_level)

    def test_pipeline_build_retrieve_and_scope_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            master_path = Path(tmp) / "master.json"
            master_path.write_text(json.dumps(minimal_master(), ensure_ascii=False), encoding="utf-8")
            pipeline = RAGPipeline(PipelineConfig(model_dir="unused"), encoder=FakeEncoder())
            index = pipeline.prepare(
                master_path,
                DocumentScope("tenant-a", "case-a", "doc-a", "consolidated"),
            )
            self.assertEqual(len(index.records), 1)
            self.assertEqual(index.records[0].metadata["tenant_id"], "tenant-a")
            result = pipeline.retrieve("현금성자산", filters={"tenant_id": "tenant-a"})
            self.assertEqual(len(result["hits"]), 1)
            rejected = pipeline.retrieve("현금성자산", filters={"tenant_id": "tenant-b"})
            self.assertEqual(rejected["hits"], [])

    def test_write_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master_path = root / "master.json"
            index_path = root / "index.npz"
            master_path.write_text(json.dumps(minimal_master(), ensure_ascii=False), encoding="utf-8")
            build_config = PipelineConfig.for_index_build(model_dir="unused", index_path=index_path)
            build = RAGPipeline(build_config, encoder=FakeEncoder())
            build.prepare(master_path, DocumentScope("tenant-a", "case-a", "doc-a"))
            self.assertTrue(index_path.is_file())
            loaded = RAGIndex.load(index_path)
            self.assertEqual(loaded.stats()["record_count"], 1)
            serve_config = PipelineConfig.for_serving(model_dir="unused", index_path=index_path)
            serve = RAGPipeline(serve_config, encoder=FakeEncoder())
            serve.prepare()
            self.assertEqual(serve.health()["status"], "ready")
            self.assertEqual(len(serve.retrieve("단기차입금")["hits"]), 1)

    def test_explicit_experimental_level(self):
        config = PipelineConfig(model_dir="unused", representation_level=2)
        self.assertEqual(config.representation_level, RepresentationLevel.HIERARCHICAL)
        self.assertTrue(config.is_experimental_level)

    def test_index_upsert_replaces_document_and_retains_others(self):
        def record(chunk_id, document_id, text):
            return ChunkRecord(
                chunk_id=chunk_id,
                embedding_text=text,
                document=text,
                metadata={
                    "tenant_id": "tenant-a",
                    "case_id": "case-a",
                    "document_id": document_id,
                },
            )

        metadata = {
            "representation_level": 0,
            "encoder": FakeEncoder().metadata(),
        }
        base = RAGIndex(
            [record("old-a", "doc-a", "old"), record("keep-b", "doc-b", "keep")],
            np.eye(2, 8, dtype=np.float32),
            metadata,
        )
        newer = RAGIndex(
            [record("new-a", "doc-a", "new")],
            np.ones((1, 8), dtype=np.float32),
            metadata,
        )
        merged = base.upsert(newer)
        self.assertEqual([row.chunk_id for row in merged.records], ["keep-b", "new-a"])

    def test_online_service_requires_tenant_and_case_scope(self):
        with self.assertRaises(ValueError):
            OnlineRAGService._require_operational_scope({"tenant_id": "tenant-a"})
        OnlineRAGService._require_operational_scope(
            {"tenant_id": "tenant-a", "case_id": "case-a"}
        )

    def test_online_cli_requires_tenant_and_case_scope(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "retrieve",
                    "--index",
                    "index.npz",
                    "--model-dir",
                    "model",
                    "--query",
                    "현금성자산",
                ]
            )
        args = parser.parse_args(
            [
                "retrieve",
                "--index",
                "index.npz",
                "--model-dir",
                "model",
                "--tenant-id",
                "tenant-a",
                "--case-id",
                "case-a",
                "--query",
                "현금성자산",
            ]
        )
        self.assertEqual(args.representation_level, 0)


class OperationalPackageTests(unittest.TestCase):
    @staticmethod
    def fact(item, value="확인", common=False, fact_id=None):
        return CreditFact(
            fact_id=fact_id or f"FACT-{item.value}-{'C' if common else 'I'}",
            field_id=f"field-{item.value}",
            field_name=f"항목 {item.value} 기초자료",
            value=value,
            unit=None,
            period=None,
            review_items=() if common else (item,),
            common=common,
            document_id="credit-report",
            source_filename="credit.xlsx",
            sheet_name="기초자료",
            cell_range="B2",
        )

    def test_global_chunk_ids_are_unique_across_documents_and_delete_is_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master_a = root / "a.json"
            master_b = root / "b.json"
            master_a.write_text(json.dumps(minimal_master(), ensure_ascii=False), encoding="utf-8")
            master_b.write_text(json.dumps(minimal_master(), ensure_ascii=False), encoding="utf-8")
            index_path = root / "index.npz"
            config = PipelineConfig.for_index_build(
                model_dir="unused", index_path=index_path, index_write_strategy="UPSERT"
            )
            pipeline = RAGPipeline(config, encoder=FakeEncoder())
            pipeline.prepare(master_a, DocumentScope("tenant", "case", "doc-a"))
            pipeline.prepare(master_b, DocumentScope("tenant", "case", "doc-b"))
            loaded = RAGIndex.load(index_path)
            self.assertEqual(len(loaded.records), 2)
            self.assertEqual(
                {row.metadata["local_chunk_id"] for row in loaded.records},
                {"CH_TEXT_00001"},
            )
            self.assertEqual(len({row.metadata["global_chunk_id"] for row in loaded.records}), 2)
            result = pipeline.delete_document(DocumentScope("tenant", "case", "doc-a"))
            self.assertEqual(result["deleted_chunks"], 1)
            remaining = RAGIndex.load(index_path)
            self.assertEqual([row.metadata["document_id"] for row in remaining.records], ["doc-b"])

    def test_credit_report_parser_preserves_cell_provenance_and_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credit.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "기초자료"
            sheet["B2"] = 1250
            sheet["C2"] = "2025"
            sheet["B3"] = "제조업체"
            workbook.save(path)
            template = CreditReportTemplate(
                "sample",
                "1.0",
                (
                    CreditFieldMapping(
                        "sales", "매출액", "기초자료", "B2",
                        review_items=(ReviewItem.PROFITABILITY,), unit="백만원", period_cell="C2",
                    ),
                    CreditFieldMapping(
                        "summary", "기업개요", "기초자료", "B3", common=True,
                    ),
                ),
            )
            parsed = CreditReportParser().parse(
                path,
                template,
                DocumentScope(
                    "tenant", "case", "credit", source_filename="credit.xlsx", document_kind="credit_report"
                ),
            )
            self.assertEqual(len(parsed.facts), 2)
            self.assertEqual(parsed.facts[0].cell_range, "B2")
            self.assertEqual(parsed.facts[0].tier, SourceTier.CREDIT_REPORT_ITEM)
            self.assertEqual(parsed.facts[1].tier, SourceTier.CREDIT_REPORT_COMMON)

    def test_few_shot_selection_uses_item_loan_type_industry_and_tags(self):
        rows = [
            FewShotExample(
                "exact", ReviewItem.PROFITABILITY, "in", "out",
                loan_types=("운전자금",), industry_codes=("C29",), situation_tags=("성장",),
            ),
            FewShotExample(
                "industry", ReviewItem.PROFITABILITY, "in", "out",
                loan_types=("*",), industry_codes=("C",),
            ),
            FewShotExample(
                "wrong-loan", ReviewItem.PROFITABILITY, "in", "out",
                loan_types=("시설자금",), industry_codes=("C29",),
            ),
            FewShotExample(
                "wrong-item", ReviewItem.CASH_FLOW, "in", "out",
                loan_types=("운전자금",), industry_codes=("C29",),
            ),
        ]
        selected = FewShotSelector(FewShotRegistry(rows), 3).select(
            ReviewItem.PROFITABILITY,
            loan_type="운전자금",
            industry_code="C29",
            situation_tags=("성장",),
        )
        self.assertEqual([row.example_id for row in selected], ["exact", "industry"])

    def test_tiered_evidence_and_prompt_keep_few_shot_out_of_evidence(self):
        item = ReviewItem.PROFITABILITY
        facts = [self.fact(item), self.fact(item, common=True, fact_id="COMMON")]
        retrieval = {
            "hits": [
                {
                    "chunk_id": "CH_TEXT_00001",
                    "document": "첨부파일 보완 근거",
                    "score": 0.1,
                    "metadata": {
                        "global_chunk_id": "GCH-1",
                        "document_id": "attachment-1",
                        "source_filename": "attachment.pdf",
                        "pages": [2],
                    },
                }
            ]
        }
        evidence = EvidenceAssembler().assemble(item, facts, retrieval)
        self.assertEqual([int(row.source_tier) for row in evidence], [1, 2, 3])
        example = FewShotExample("FS-1", item, "예시 입력", "예시 출력")
        prompt = ReviewPromptBuilder().build(
            CaseContext("tenant", "case", "운전자금", "C29"),
            item,
            item.title,
            evidence,
            [example],
        )
        self.assertTrue(all(row["example_id"] != evidence_row["evidence_id"] for row in prompt.few_shots for evidence_row in prompt.evidence))
        self.assertTrue(prompt.manifest["few_shot_is_evidence"] is False)

    def test_validator_blocks_few_shot_numeric_and_token_leakage(self):
        item = ReviewItem.PROFITABILITY
        evidence = [
            EvidenceRecord("EV-1", item, SourceTier.CREDIT_REPORT_ITEM, "매출액=100", "credit")
        ]
        example = FewShotExample(
            "FS-1", item, "과거", "과거기업의 비율은 77%", forbidden_tokens=("과거기업",)
        )
        valid = OpinionValidator().validate("매출액 100을 확인하였다. EV-1", evidence, [example])
        self.assertTrue(valid.valid)
        invalid = OpinionValidator().validate("과거기업 비율은 77%이다. EV-1", evidence, [example])
        self.assertFalse(invalid.valid)
        self.assertIn("few_shot_numeric_leakage", {issue.code for issue in invalid.issues})
        self.assertIn("few_shot_token_leakage", {issue.code for issue in invalid.issues})

    def test_registry_and_vector_delete_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "tenant" / "case" / "doc" / "a.pdf"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"pdf")
            derived = root / "derived" / "tenant" / "case" / "doc"
            derived.mkdir(parents=True)
            (derived / "master.json").write_text("{}", encoding="utf-8")

            registry = OperationalRegistry()
            registry.register_document(
                tenant_id="tenant", case_id="case", document_id="doc", filename="a.pdf",
                document_kind=DocumentKind.ATTACHMENT,
                storage_uri=str(original), derived_uri=str(derived),
            )
            for status in (
                FileStatus.VALIDATING, FileStatus.PARSING, FileStatus.INDEXING, FileStatus.READY
            ):
                registry.transition_document("tenant", "case", "doc", status)
            vector = InMemoryVectorStore()
            record = ChunkRecord(
                "CH_TEXT_00001", "text", "text",
                {"tenant_id": "tenant", "case_id": "case", "document_id": "doc", "global_chunk_id": "GCH-1"},
            )
            vector.upsert_document([record], np.ones((1, 8), dtype=np.float32))
            deleted = DocumentLifecycleService(
                registry, vector, LocalDocumentArtifactStore(root)
            ).delete(DocumentScope("tenant", "case", "doc"))
            self.assertEqual(deleted["deleted_vectors"], 1)
            self.assertEqual(deleted["remaining_vectors"], 0)
            self.assertTrue(deleted["original_absent"])
            self.assertFalse(original.exists())
            self.assertFalse(derived.exists())
            self.assertEqual(registry.get_document("tenant", "case", "doc").status, FileStatus.DELETED)

    def test_html_application_contract_lists_progress_and_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "attachment.pdf"
            source.write_bytes(b"pdf")
            registry = OperationalRegistry()
            vector = InMemoryVectorStore()
            lifecycle = DocumentLifecycleService(
                registry, vector, LocalDocumentArtifactStore(root)
            )
            service = OperationalApplicationService(registry, lifecycle)
            scope = DocumentScope("tenant", "case", "attachment-1")
            row = service.register_upload(
                scope,
                filename="attachment.pdf",
                document_kind=DocumentKind.ATTACHMENT,
                size_bytes=source.stat().st_size,
                storage_uri=str(source),
            )
            self.assertEqual(row["progress_stage"], "업로드 완료")
            self.assertEqual(row["progress_percent"], 0)
            for status in (
                FileStatus.VALIDATING, FileStatus.PARSING, FileStatus.INDEXING, FileStatus.READY
            ):
                row = service.update_upload(scope, status)
            self.assertEqual(row["progress_percent"], 100)
            self.assertEqual(service.list_uploads("tenant", "case")["attachments"][0]["file_type"], "PDF")
            result = service.delete_upload(scope)
            self.assertTrue(result["removed_from_active_list"])
            self.assertEqual(service.list_uploads("tenant", "case")["attachments"], [])

    def test_delete_does_not_remove_file_when_vectors_remain(self):
        class RefusingVectorStore(InMemoryVectorStore):
            def delete_document(self, scope):
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            source.write_bytes(b"pdf")
            registry = OperationalRegistry()
            registry.register_document(
                tenant_id="tenant", case_id="case", document_id="doc",
                filename="source.pdf", document_kind=DocumentKind.ATTACHMENT,
                storage_uri=str(source),
            )
            for status in (
                FileStatus.VALIDATING, FileStatus.PARSING, FileStatus.INDEXING, FileStatus.READY
            ):
                registry.transition_document("tenant", "case", "doc", status)
            vector = RefusingVectorStore()
            vector.upsert_document(
                [ChunkRecord(
                    "CH", "text", "text",
                    {"tenant_id": "tenant", "case_id": "case", "document_id": "doc", "global_chunk_id": "GCH"},
                )],
                np.ones((1, 8), dtype=np.float32),
            )
            lifecycle = DocumentLifecycleService(
                registry, vector, LocalDocumentArtifactStore(root)
            )
            with self.assertRaises(RuntimeError):
                lifecycle.delete(DocumentScope("tenant", "case", "doc"))
            self.assertTrue(source.exists())
            self.assertEqual(
                registry.get_document("tenant", "case", "doc").status,
                FileStatus.FAILED,
            )

    def test_cpu_generator_adapter_and_grounded_fallback(self):
        class Broken:
            def generate(self, messages):
                raise RuntimeError("model unavailable")

        messages = [
            {"role": "system", "content": "grounded"},
            {
                "role": "user",
                "content": (
                    "[CURRENT_CASE_EVIDENCE]\n[TIER_1 EVIDENCE]\n"
                    "evidence_id=EV-1\ndocument_id=credit\nsource_filename=credit.xlsx\n"
                    "page=None\ncontent=매출액=100 백만원\n\n[작성요청]\n작성"
                ),
            },
        ]
        fallback = FallbackGenerator(Broken(), EvidenceTemplateGenerator())
        text = fallback.generate(messages)
        self.assertIn("EV-1", text)
        self.assertIn("100", text)
        self.assertEqual(fallback.last_backend, "EvidenceTemplateGenerator")

        class FakeTokenizer:
            eos_token_id = 0
            def apply_chat_template(self, messages, tokenize, add_generation_prompt):
                return "prompt"
            def __call__(self, prompt, return_tensors):
                return {"input_ids": np.array([[1, 2]], dtype=np.int64)}
            def decode(self, ids, skip_special_tokens):
                return "CPU draft"

        class FakeModel:
            def generate(self, **kwargs):
                return np.array([[1, 2, 3, 4]], dtype=np.int64)

        generated = TransformersCpuGenerator(
            tokenizer=FakeTokenizer(), model=FakeModel()
        ).generate(messages)
        self.assertEqual(generated, "CPU draft")

    def test_v020_registry_migrates_progress_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE documents (
                    tenant_id TEXT NOT NULL, case_id TEXT NOT NULL, document_id TEXT NOT NULL,
                    filename TEXT NOT NULL, document_kind TEXT NOT NULL, status TEXT NOT NULL,
                    source_hash TEXT, size_bytes INTEGER, error TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, case_id, document_id)
                )"""
            )
            connection.execute(
                "INSERT INTO documents VALUES ('t','c','d','a.pdf','attachment','READY',NULL,10,NULL,1,1)"
            )
            connection.commit()
            connection.close()
            migrated = OperationalRegistry(path).get_document("t", "c", "d")
            self.assertEqual(migrated.progress, 100)
            self.assertEqual(migrated.status.progress_stage, "완료")

    def test_package_contains_approved_html_contract(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "semantic_prompt_transfer"
            / "examples"
            / "operational"
            / "credit_review_upload_demo.html"
        )
        content = path.read_text(encoding="utf-8")
        for label in (
            "로그인 없는 시간 제한형 Colab POC",
            "sptAnonymousClientV1",
            "신용조사서",
            "양식 다운로드",
            "기타 첨부자료",
            "심사의견 생성",
            "Word 다운로드",
            "file-x",
            "/api/v1/cases/",
            "/api/v1/templates/credit-report.xlsx",
        ):
            self.assertIn(label, content)
        self.assertNotIn("회원가입", content)
        self.assertNotIn("로그아웃", content)
        self.assertNotIn("X-POC-Token", content)
        self.assertNotIn("업로드 자료현황", content)

    def test_five_item_orchestration_generates_docx_and_progress(self):
        class EmptyRetriever:
            def search(self, query, **kwargs):
                return {"query": query, "hits": []}

        class CitationLLM:
            def generate(self, messages):
                match = __import__("re").search(r"evidence_id=([^\n]+)", messages[1]["content"])
                return f"현재 자료를 근거로 현황과 전망을 검토하였다. {match.group(1)}"

        examples = [
            FewShotExample(f"FS-{item.value}", item, "상황", "구조 예시", loan_types=("*",), industry_codes=("*",))
            for item in ReviewItem.ordered()
        ]
        facts = [self.fact(item) for item in ReviewItem.ordered()]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "opinion.docx"
            result = ReviewGenerationOrchestrator(
                EmptyRetriever(), FewShotSelector(FewShotRegistry(examples))
            ).generate(
                CaseContext("tenant", "case", "운전자금", "C29", "샘플기업"),
                facts,
                CitationLLM(),
                output,
            )
            self.assertEqual(len(result.sections), 5)
            self.assertEqual(result.progress_events[-1].progress, 100)
            self.assertTrue(output.is_file())

    def test_docx_builder_requires_all_five_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                OpinionDocumentBuilder().build(
                    CaseContext("tenant", "case", "운전자금", "C29"),
                    [ReviewSectionDraft(ReviewItem.PROFITABILITY, "text", ())],
                    Path(tmp) / "bad.docx",
                )


if __name__ == "__main__":
    unittest.main()
