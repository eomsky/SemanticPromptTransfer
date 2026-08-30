from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from semantic_prompt_transfer import (
    DocumentScope,
    PipelineConfig,
    RAGIndex,
    RAGPipeline,
    RepresentationLevel,
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


if __name__ == "__main__":
    unittest.main()
