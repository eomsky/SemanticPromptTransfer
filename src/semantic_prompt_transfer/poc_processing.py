from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from docx import Document
from openpyxl import load_workbook

from ._chunk_builder_base import ChunkRecord
from .config import DocumentScope
from .credit_report import CreditReportParser, CreditReportTemplate
from .domain import DocumentKind, FileStatus
from .encoding import EncoderBackend
from .storage import LocalDocumentArtifactStore
from .vector_store import ShardedNpzVectorStore


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    page: int | None = None
    location: str | None = None


class PocDocumentExtractor:
    """Small deterministic extractor for the temporary POC upload path."""

    SUPPORTED = {".pdf", ".docx", ".xlsx", ".txt", ".md"}

    def extract(self, path: str | Path) -> list[ExtractedBlock]:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix not in self.SUPPORTED:
            raise ValueError(f"unsupported attachment type: {suffix or 'unknown'}")
        if suffix == ".pdf":
            return self._pdf(source)
        if suffix == ".docx":
            return self._docx(source)
        if suffix == ".xlsx":
            return self._xlsx(source)
        return self._text(source)

    @staticmethod
    def _pdf(path: Path) -> list[ExtractedBlock]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - optional POC dependency
            raise RuntimeError("install semantic-prompt-transfer[poc] for PDF uploads") from exc
        reader = PdfReader(str(path))
        blocks = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(ExtractedBlock(text=text, page=page_number, location=f"page:{page_number}"))
        return blocks

    @staticmethod
    def _docx(path: Path) -> list[ExtractedBlock]:
        document = Document(str(path))
        blocks = [
            ExtractedBlock(text=paragraph.text.strip(), location=f"paragraph:{index}")
            for index, paragraph in enumerate(document.paragraphs, start=1)
            if paragraph.text.strip()
        ]
        for table_index, table in enumerate(document.tables, start=1):
            for row_index, row in enumerate(table.rows, start=1):
                values = [" ".join(cell.text.split()) for cell in row.cells]
                if any(values):
                    blocks.append(
                        ExtractedBlock(
                            text=" | ".join(values),
                            location=f"table:{table_index}:row:{row_index}",
                        )
                    )
        return blocks

    @staticmethod
    def _xlsx(path: Path) -> list[ExtractedBlock]:
        workbook = load_workbook(path, data_only=False, read_only=True)
        blocks: list[ExtractedBlock] = []
        try:
            for sheet in workbook.worksheets:
                for row_index, row in enumerate(sheet.iter_rows(values_only=False), start=1):
                    values = []
                    for cell in row:
                        if cell.value is not None:
                            values.append(f"{cell.coordinate}={cell.value}")
                    if values:
                        blocks.append(
                            ExtractedBlock(
                                text=f"시트={sheet.title}; " + "; ".join(values),
                                location=f"sheet:{sheet.title}:row:{row_index}",
                            )
                        )
        finally:
            workbook.close()
        return blocks

    @staticmethod
    def _text(path: Path) -> list[ExtractedBlock]:
        payload = path.read_bytes()
        text = None
        for encoding in ("utf-8-sig", "cp949", "utf-8"):
            try:
                text = payload.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("text attachment encoding is not supported")
        return [ExtractedBlock(text=text.strip(), location="text")]


class PocUploadProcessor:
    """Parse, chunk and embed uploads entirely inside one ephemeral runtime."""

    def __init__(
        self,
        encoder: EncoderBackend,
        vector_store: ShardedNpzVectorStore,
        artifact_store: LocalDocumentArtifactStore,
        *,
        credit_template: CreditReportTemplate | None = None,
        extractor: PocDocumentExtractor | None = None,
        max_chars: int = 1800,
        overlap_chars: int = 180,
    ) -> None:
        if max_chars < 256 or not 0 <= overlap_chars < max_chars:
            raise ValueError("invalid POC chunk size or overlap")
        self.encoder = encoder
        self.vector_store = vector_store
        self.artifact_store = artifact_store
        self.credit_template = credit_template
        self.extractor = extractor or PocDocumentExtractor()
        self.max_chars = int(max_chars)
        self.overlap_chars = int(overlap_chars)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _split(self, text: str) -> list[str]:
        normalized = re.sub(r"[ \t]+", " ", text).strip()
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + self.max_chars)
            if end < len(normalized):
                boundary = max(
                    normalized.rfind("\n", start + self.max_chars // 2, end),
                    normalized.rfind(". ", start + self.max_chars // 2, end),
                    normalized.rfind(" ", start + self.max_chars // 2, end),
                )
                if boundary > start:
                    end = boundary + 1
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            next_start = max(start + 1, end - self.overlap_chars)
            start = next_start
        return chunks

    def _records(
        self,
        scope: DocumentScope,
        source_path: Path,
        blocks: list[ExtractedBlock],
    ) -> list[ChunkRecord]:
        records: list[ChunkRecord] = []
        for block_index, block in enumerate(blocks, start=1):
            for part_index, text in enumerate(self._split(block.text), start=1):
                local_id = f"CH_POC_{len(records) + 1:05d}"
                global_id = DocumentScope(
                    scope.tenant_id, scope.case_id, scope.document_id
                ).as_metadata()
                identity = "\x1f".join(
                    (scope.tenant_id, scope.case_id, scope.document_id, local_id, "0")
                )
                chunk_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                metadata = {
                    **global_id,
                    "global_chunk_id": f"GCH_{chunk_hash}",
                    "local_chunk_id": local_id,
                    "representation_level": 0,
                    "source_filename": source_path.name,
                    "document_kind": DocumentKind.ATTACHMENT.value,
                    "pages": [block.page] if block.page else [],
                    "source_location": block.location,
                    "block_index": block_index,
                    "part_index": part_index,
                }
                records.append(
                    ChunkRecord(
                        chunk_id=local_id,
                        embedding_text=text,
                        document=text,
                        metadata=metadata,
                    )
                )
        return records

    def process(
        self,
        scope: DocumentScope,
        source_path: Path,
        document_kind: DocumentKind,
        progress: Callable[[FileStatus, int | None, str | None], None],
    ) -> None:
        derived = self.artifact_store.derived_path(scope)
        progress(FileStatus.VALIDATING, 25, "파일 형식과 범위를 검증했습니다.")
        if document_kind is DocumentKind.CREDIT_REPORT:
            if source_path.suffix.lower() != ".xlsx":
                raise ValueError("POC 신용조사서는 .xlsx 형식이어야 합니다")
            if self.credit_template is None:
                raise RuntimeError("credit-report template is not configured")
            progress(FileStatus.PARSING, 55, "신용조사서 정형 셀을 읽고 있습니다.")
            parsed = CreditReportParser().parse(
                source_path,
                self.credit_template,
                DocumentScope(
                    scope.tenant_id,
                    scope.case_id,
                    scope.document_id,
                    source_filename=source_path.name,
                    document_kind=DocumentKind.CREDIT_REPORT.value,
                ),
            )
            self._write_json(derived / "credit_facts.json", parsed.to_dict())
            self.vector_store.delete_document(scope)
            progress(FileStatus.READY, 100, "신용조사서 기초자료 적재를 완료했습니다.")
            return

        progress(FileStatus.PARSING, 45, "첨부자료 텍스트를 추출하고 있습니다.")
        blocks = self.extractor.extract(source_path)
        records = self._records(scope, source_path, blocks)
        if not records:
            raise ValueError("첨부자료에서 검색 가능한 텍스트를 찾지 못했습니다")
        progress(FileStatus.INDEXING, 70, "L0 청크의 임베딩 벡터를 생성하고 있습니다.")
        embeddings = np.asarray(
            self.encoder.encode_documents([row.embedding_text for row in records]),
            dtype=np.float32,
        )
        self.vector_store.upsert_document(records, embeddings)
        self._write_json(
            derived / "chunks.json",
            {
                "document_id": scope.document_id,
                "source_filename": source_path.name,
                "chunk_count": len(records),
                "chunks": [
                    {
                        "chunk_id": row.chunk_id,
                        "embedding_text": row.embedding_text,
                        "document": row.document,
                        "metadata": row.metadata,
                    }
                    for row in records
                ],
            },
        )
        progress(FileStatus.READY, 100, "첨부자료 임베딩과 벡터 적재를 완료했습니다.")


class ShardedAttachmentRetriever:
    def __init__(
        self,
        encoder: EncoderBackend,
        vector_store: ShardedNpzVectorStore,
        *,
        top_k: int = 5,
    ) -> None:
        self.encoder = encoder
        self.vector_store = vector_store
        self.top_k = int(top_k)

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        filters = dict(kwargs.get("filters") or {})
        missing = [key for key in ("tenant_id", "case_id") if not filters.get(key)]
        if missing:
            raise ValueError(f"POC retrieval requires scope filters: {missing}")
        embedding = self.encoder.encode_queries([query])[0]
        hits = self.vector_store.search(embedding, top_k=self.top_k, filters=filters)
        return {"query": query, "filters": filters, "hits": hits}
