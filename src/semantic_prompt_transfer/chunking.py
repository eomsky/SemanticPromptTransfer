from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Iterable

from ._chunk_builder_base import ChunkBuilder, ChunkRecord, ChunkRepresentationLevel
from .config import RepresentationLevel


class PackageChunkBuilder(ChunkBuilder):
    """Package Cell 4 builder with production level 0 by default."""

    SCHEMA_VERSION = "1.2"

    def __init__(
        self,
        representation_level: int | RepresentationLevel = RepresentationLevel.PLAIN,
        **kwargs: Any,
    ) -> None:
        super().__init__(representation_level=int(representation_level), **kwargs)

    def _structured_embedding_text(self, window: dict[str, Any]) -> str:
        if window["kind"] == "text":
            path = " > ".join(window.get("section_path") or [])
            return "\n".join(part for part in (f"문서계층: {path}" if path else "", "내용:", window["content"]) if part)
        table = window["table"]
        context = window["context"]
        parts = []
        if context.get("title"):
            parts.append(f"표제목: {context['title']}")
        if context.get("units"):
            parts.append("단위: " + "; ".join(unit["text"] for unit in context["units"]))
        parts.extend(
            [
                f"논리표: {window['logical_table_id']}",
                "표내용:",
                self._table_markdown(table, window["row_indices"], window["column_paths"]),
            ]
        )
        parts.extend(f"주석: {note['text']}" for note in context.get("notes") or [])
        return "\n".join(parts)

    def _hierarchical_embedding_text(self, window: dict[str, Any]) -> str:
        if window["kind"] == "text":
            path = window.get("section_path") or []
            return "\n".join([*(f"계층 {i}: {value}" for i, value in enumerate(path, 1)), "본문:", window["content"]])
        table = window["table"]
        context = window["context"]
        hierarchy = self._table_hierarchy(
            table,
            window["row_indices"],
            window["column_paths"],
            window["column_source_ids"],
            context["units"],
        )
        row_paths = {row["row_id"]: " > ".join(row.get("path") or []) for row in hierarchy["rows"]}
        column_paths = {column["column_id"]: " > ".join(column.get("path") or []) for column in hierarchy["columns"]}
        parts = []
        if context.get("title"):
            parts.append(f"표제목: {context['title']}")
        if context.get("units"):
            parts.append("단위: " + "; ".join(unit["text"] for unit in context["units"]))
        parts.append(f"논리표: {window['logical_table_id']}")
        for column in hierarchy["columns"]:
            parts.append(f"열계층[{column['column_id']}]: {column_paths[column['column_id']]}")
        for cell in hierarchy["cells"]:
            if not cell.get("value"):
                continue
            parts.append(
                "셀: "
                f"행={row_paths.get(cell['row_id'], cell['row_id'])}; "
                f"열={column_paths.get(cell['column_id'], cell['column_id'])}; "
                f"값={cell['value']}"
            )
        parts.extend(f"주석: {note['text']}" for note in context.get("notes") or [])
        return "\n".join(parts)

    def _active_embedding_text(self, window: dict[str, Any]) -> str:
        if self.representation_level == ChunkRepresentationLevel.PLAIN:
            return window["embedding_text"]
        if self.representation_level == ChunkRepresentationLevel.STRUCTURED:
            return self._structured_embedding_text(window)
        return self._hierarchical_embedding_text(window)

    def _record(self, window: dict[str, Any]) -> ChunkRecord:
        base = super()._record(window)
        canonical = window["embedding_text"]
        active = self._active_embedding_text(window)
        level = int(self.representation_level)
        metadata = {
            **base.metadata,
            "canonical_chunk_id": base.chunk_id,
            "variant_id": f"{base.chunk_id}:L{level}",
            "embedding_profile": "semantic_linearized" if level == 0 else "experimental_representation",
            "canonical_embedding_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "canonical_embedding_chars": len(canonical),
            "active_embedding_chars": len(active),
        }
        return replace(base, embedding_text=active, metadata=metadata)


def build_experimental_matrix(
    master_json: dict[str, Any],
    levels: Iterable[int] = (0, 1, 2),
    **builder_kwargs: Any,
) -> dict[int, list[ChunkRecord]]:
    """Explicit development-only level matrix; never called by production defaults."""

    matrix = {
        int(level): PackageChunkBuilder(representation_level=int(level), **builder_kwargs).build(master_json)
        for level in levels
    }
    ids = {level: tuple(record.chunk_id for record in records) for level, records in matrix.items()}
    if len(set(ids.values())) != 1:
        raise ValueError("representation levels must preserve canonical chunk boundaries")
    return matrix


__all__ = ["ChunkRecord", "PackageChunkBuilder", "build_experimental_matrix"]
