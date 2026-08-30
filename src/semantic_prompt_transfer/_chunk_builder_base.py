from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Iterable


class ChunkRepresentationLevel(IntEnum):
    PLAIN = 0
    STRUCTURED = 1
    HIERARCHICAL = 2


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    embedding_text: str
    document: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChunkBuilder:
    """Build traceable RAG chunks from SemanticPromptTransfer MASTER_JSON.

    ``representation_level`` changes only the outgoing representation.  Chunk
    identity, source coverage, and embedding text stay invariant across levels.
    """

    SCHEMA_NAME = "semantic_chunk"
    SCHEMA_VERSION = "1.0"
    RENDERER_NAMES = {
        ChunkRepresentationLevel.PLAIN: "plain",
        ChunkRepresentationLevel.STRUCTURED: "structured",
        ChunkRepresentationLevel.HIERARCHICAL: "hierarchical",
    }

    def __init__(
        self,
        representation_level: int | ChunkRepresentationLevel = 0,
        max_chars: int = 4000,
        text_overlap_chars: int = 240,
        table_overlap_rows: int = 0,
    ):
        self.representation_level = ChunkRepresentationLevel(
            representation_level
        )
        self.max_chars = int(max_chars)
        self.text_overlap_chars = int(text_overlap_chars)
        self.table_overlap_rows = int(table_overlap_rows)
        if self.max_chars < 256:
            raise ValueError("max_chars must be at least 256")
        if not 0 <= self.text_overlap_chars < self.max_chars:
            raise ValueError("text_overlap_chars must satisfy 0 <= overlap < max_chars")
        if self.table_overlap_rows < 0:
            raise ValueError("table_overlap_rows must be non-negative")

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").replace("\u00a0", " ").split())

    @classmethod
    def _dedupe(cls, values: Iterable[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = cls._clean(value)
            if text and (not result or result[-1] != text):
                result.append(text)
        return result

    @staticmethod
    def _bbox_key(page: int, bbox: Iterable[Any]) -> tuple[Any, ...]:
        return (int(page), *(round(float(value), 2) for value in bbox))

    @classmethod
    def _semantic_key(cls, item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            *cls._bbox_key(item.get("page", -1), item.get("bbox", [])),
            cls._clean(item.get("text", "")),
        )

    @staticmethod
    def _center_inside(bbox: list[float], outer: list[float]) -> bool:
        x = (float(bbox[0]) + float(bbox[2])) / 2.0
        y = (float(bbox[1]) + float(bbox[3])) / 2.0
        return (
            float(outer[0]) <= x <= float(outer[2])
            and float(outer[1]) <= y <= float(outer[3])
        )

    def _heading_path(
        self, heading_id: str | None, headings: dict[str, dict[str, Any]]
    ) -> list[str]:
        path: list[str] = []
        visited: set[str] = set()
        current = heading_id
        while current and current not in visited and current in headings:
            visited.add(current)
            heading = headings[current]
            text = self._clean(heading.get("text"))
            if text:
                path.append(text)
            current = heading.get("parent_id")
        return list(reversed(path))

    def _excluded_body_keys(self, master: dict[str, Any]) -> set[tuple[Any, ...]]:
        excluded: set[tuple[Any, ...]] = set()
        for heading in master.get("semantic_elements", {}).get("headings", []):
            excluded.add(self._semantic_key(heading))
        for table in master.get("semantic_elements", {}).get("tables", []):
            context = table.get("logical_context") or {}
            candidates = []
            if table.get("title"):
                candidates.append(table["title"])
            candidates.extend(table.get("units") or [])
            candidates.extend(table.get("notes") or [])
            candidates.extend(context.get("titles") or [])
            candidates.extend(context.get("units") or [])
            candidates.extend(context.get("notes") or [])
            for item in candidates:
                if item.get("bbox"):
                    excluded.add(self._semantic_key(item))
        return excluded

    def _body_blocks(self, master: dict[str, Any]) -> list[dict[str, Any]]:
        excluded = self._excluded_body_keys(master)
        furniture = {
            self._bbox_key(item["page"], item["bbox"])
            for item in master.get("annotation", {}).get("page_furniture", [])
            if item.get("bbox")
        }
        table_boxes: dict[int, list[list[float]]] = {}
        for table in master.get("raw_document", {}).get("physical_tables", []):
            table_boxes.setdefault(int(table["page"]), []).append(table["bbox"])
        result = []
        for block in master.get("annotation", {}).get("body_text", []):
            text = self._clean(block.get("text"))
            bbox = block.get("bbox")
            if not text or not bbox:
                continue
            if self._semantic_key(block) in excluded:
                continue
            if self._bbox_key(block["page"], bbox) in furniture:
                continue
            if any(
                self._center_inside(bbox, outer)
                for outer in table_boxes.get(int(block["page"]), [])
            ):
                continue
            result.append(block)
        return sorted(
            result,
            key=lambda item: (
                int(item["page"]),
                float(item["bbox"][1]),
                float(item["bbox"][0]),
            ),
        )

    def _text_windows(self, master: dict[str, Any]) -> list[dict[str, Any]]:
        heading_lookup = {
            heading["element_id"]: heading
            for heading in master.get("semantic_elements", {}).get("headings", [])
        }
        blocks = self._body_blocks(master)
        windows: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        current_scope: str | None = None

        def length(items: list[dict[str, Any]]) -> int:
            return sum(len(self._clean(item.get("text"))) + 1 for item in items)

        def emit() -> None:
            if not current:
                return
            index = len(windows) + 1
            texts = [self._clean(item["text"]) for item in current]
            pages = sorted({int(item["page"]) for item in current})
            path = self._heading_path(current_scope, heading_lookup)
            body = "\n".join(texts)
            embedding = "\n".join([*path, body]) if path else body
            windows.append(
                {
                    "kind": "text",
                    "chunk_id": f"CH_TEXT_{index:05d}",
                    "sort_key": (
                        int(current[0]["page"]),
                        float(current[0]["bbox"][1]),
                        0,
                    ),
                    "pages": pages,
                    "object_ids": [item["object_id"] for item in current],
                    "bboxes": [item["bbox"] for item in current],
                    "section_path": path,
                    "content": body,
                    "embedding_text": embedding,
                }
            )

        for block in blocks:
            scope = block.get("scope_heading_id")
            addition = len(self._clean(block.get("text"))) + 1
            scope_changed = bool(current) and scope != current_scope
            over_limit = bool(current) and length(current) + addition > self.max_chars
            if scope_changed or over_limit:
                previous = list(current)
                emit()
                current = []
                if over_limit and not scope_changed and self.text_overlap_chars:
                    carry: list[dict[str, Any]] = []
                    carry_length = 0
                    for item in reversed(previous):
                        item_length = len(self._clean(item.get("text"))) + 1
                        if carry and carry_length + item_length > self.text_overlap_chars:
                            break
                        carry.append(item)
                        carry_length += item_length
                    current = list(reversed(carry))
            if not current:
                current_scope = scope
            current.append(block)
        emit()
        return windows

    def _filled_grid(
        self,
        matrix: list[list[Any]],
        merges: list[dict[str, Any]],
        row_limit: int | None = None,
    ) -> tuple[list[list[str]], list[list[str | None]]]:
        rows = len(matrix) if row_limit is None else min(len(matrix), row_limit)
        columns = max((len(row) for row in matrix), default=0)
        grid = [
            [self._clean(matrix[row][column]) if column < len(matrix[row]) else "" for column in range(columns)]
            for row in range(rows)
        ]
        origins: list[list[str | None]] = [
            [f"R{row}:C{column}" if grid[row][column] else None for column in range(columns)]
            for row in range(rows)
        ]
        for merge in merges or []:
            row = int(merge.get("row", -1))
            column = int(merge.get("column", -1))
            if not (0 <= row < rows and 0 <= column < columns):
                continue
            value = grid[row][column]
            origin = f"R{row}:C{column}"
            for target_row in range(row, min(rows, row + int(merge.get("row_span", 1)))):
                for target_column in range(
                    column,
                    min(columns, column + int(merge.get("col_span", 1))),
                ):
                    grid[target_row][target_column] = value
                    origins[target_row][target_column] = origin
        return grid, origins

    def _local_column_paths(
        self, table: dict[str, Any]
    ) -> tuple[list[list[str]], list[list[str]]]:
        columns = int(table.get("column_count", 0))
        header_depth = int(table.get("header_depth", 0))
        if header_depth <= 0:
            return ([[] for _ in range(columns)], [[] for _ in range(columns)])
        grid, origins = self._filled_grid(
            table.get("matrix") or [], table.get("merge_evidence") or [], header_depth
        )
        paths: list[list[str]] = []
        source_ids: list[list[str]] = []
        for column in range(columns):
            values = [grid[row][column] for row in range(len(grid))]
            paths.append(self._dedupe(values))
            ids = []
            for row in range(len(grid)):
                origin = origins[row][column]
                if origin:
                    cell_id = f"{table['table_id']}:{origin}"
                    if cell_id not in ids:
                        ids.append(cell_id)
            source_ids.append(ids)
        return paths, source_ids

    def _column_paths(
        self,
        table: dict[str, Any],
        by_id: dict[str, dict[str, Any]],
        incoming_links: dict[str, dict[str, Any]],
    ) -> tuple[list[list[str]], list[list[str]]]:
        columns = int(table["column_count"])
        stub = int(table["stub_column_count"])
        local_paths, local_ids = self._local_column_paths(table)
        inherited_paths = [[] for _ in range(columns)]
        inherited_ids = [[] for _ in range(columns)]
        inherited = table.get("inherited_header") or {}
        source_id = inherited.get("source_table_id")
        source = by_id.get(source_id)
        if source:
            source_paths, source_ids = self._local_column_paths(source)
            if len(source_paths) == columns:
                inherited_paths = source_paths
                inherited_ids = source_ids
            else:
                link = incoming_links.get(table["table_id"], {})
                counts = (
                    link.get("parent_child_mapping", {}).get(
                        "successor_columns_per_parent"
                    )
                    or []
                )
                source_stub = int(source.get("stub_column_count", 1))
                expanded_paths: list[list[str]] = []
                expanded_ids: list[list[str]] = []
                for index in range(min(stub, len(source_paths))):
                    expanded_paths.append(source_paths[index])
                    expanded_ids.append(source_ids[index])
                for parent_index, count in enumerate(counts):
                    source_column = source_stub + parent_index
                    parent_path = (
                        source_paths[source_column]
                        if source_column < len(source_paths)
                        else []
                    )
                    parent_ids = (
                        source_ids[source_column]
                        if source_column < len(source_ids)
                        else []
                    )
                    for _ in range(int(count)):
                        expanded_paths.append(parent_path)
                        expanded_ids.append(parent_ids)
                if len(expanded_paths) == columns:
                    inherited_paths = expanded_paths
                    inherited_ids = expanded_ids

        combined_paths: list[list[str]] = []
        combined_ids: list[list[str]] = []
        for column in range(columns):
            combined_paths.append(
                self._dedupe([*inherited_paths[column], *local_paths[column]])
            )
            combined_ids.append(
                list(dict.fromkeys([*inherited_ids[column], *local_ids[column]]))
            )
        return combined_paths, combined_ids

    def _row_hierarchy(
        self, table: dict[str, Any], row_indices: list[int]
    ) -> list[dict[str, Any]]:
        matrix = table.get("matrix") or []
        stub = int(table["stub_column_count"])
        grid, origins = self._filled_grid(
            matrix, table.get("merge_evidence") or [], len(matrix)
        )
        result = []
        for row in row_indices:
            path = self._dedupe(grid[row][:stub]) if row < len(grid) else []
            ids = []
            if row < len(origins):
                for origin in origins[row][:stub]:
                    if origin:
                        cell_id = f"{table['table_id']}:{origin}"
                        if cell_id not in ids:
                            ids.append(cell_id)
            result.append(
                {
                    "row_id": f"r{row}",
                    "row_index": row,
                    "path": path or [f"행 {row + 1}"],
                    "source_cell_ids": ids,
                }
            )
        return result

    def _table_context(self, table: dict[str, Any]) -> dict[str, Any]:
        context = table.get("logical_context") or {}
        titles = context.get("titles") or ([table["title"]] if table.get("title") else [])
        units = context.get("units") or table.get("units") or []
        notes = context.get("notes") or table.get("notes") or []
        local_units = [
            unit for unit in units if unit.get("source_table_id") == table["table_id"]
        ]
        if not local_units:
            local_units = units
        normalized_units = []
        for index, unit in enumerate(local_units, 1):
            normalized_units.append(
                {
                    "unit_id": f"u{index}",
                    "text": self._clean(unit.get("text")),
                    "page": unit.get("page"),
                    "scope": "table",
                    "source_table_id": unit.get("source_table_id"),
                    "assignment_source": unit.get("assignment_source"),
                    "confidence": unit.get("confidence"),
                }
            )
        normalized_notes = [
            {
                "text": self._clean(note.get("text")),
                "page": note.get("page"),
                "source_table_id": note.get("source_table_id"),
            }
            for note in notes
            if self._clean(note.get("text"))
        ]
        return {
            "title": self._clean(titles[0].get("text")) if titles else None,
            "titles": [
                {
                    "text": self._clean(title.get("text")),
                    "page": title.get("page"),
                    "source_table_id": title.get("source_table_id"),
                }
                for title in titles
                if self._clean(title.get("text"))
            ],
            "units": normalized_units,
            "notes": normalized_notes,
        }

    @classmethod
    def _escape_markdown(cls, value: Any) -> str:
        return cls._clean(value).replace("|", "\\|").replace("\n", "<br>")

    def _column_labels(
        self, paths: list[list[str]], stub: int
    ) -> list[str]:
        labels = []
        for column, path in enumerate(paths):
            if path:
                labels.append(" > ".join(path))
            elif column < stub:
                labels.append("구분" if stub == 1 else f"구분 {column + 1}")
            else:
                labels.append(f"열 {column + 1}")
        return labels

    def _table_markdown(
        self,
        table: dict[str, Any],
        row_indices: list[int],
        column_paths: list[list[str]],
    ) -> str:
        stub = int(table["stub_column_count"])
        labels = self._column_labels(column_paths, stub)
        rows = [
            "| " + " | ".join(self._escape_markdown(value) for value in labels) + " |",
            "| "
            + " | ".join("---" if column < stub else "---:" for column in range(len(labels)))
            + " |",
        ]
        matrix = table.get("matrix") or []
        for row_index in row_indices:
            row = list(matrix[row_index])
            row.extend([""] * (len(labels) - len(row)))
            rows.append(
                "| "
                + " | ".join(self._escape_markdown(value) for value in row[: len(labels)])
                + " |"
            )
        return "\n".join(rows)

    def _table_plain(
        self,
        table: dict[str, Any],
        row_indices: list[int],
        column_paths: list[list[str]],
        context: dict[str, Any],
    ) -> str:
        lines = []
        if context["title"]:
            lines.append(context["title"])
        if context["units"]:
            lines.append("단위: " + "; ".join(unit["text"] for unit in context["units"]))
        labels = self._column_labels(column_paths, int(table["stub_column_count"]))
        matrix = table.get("matrix") or []
        for row_index in row_indices:
            row = list(matrix[row_index])
            row.extend([""] * (len(labels) - len(row)))
            values = [
                f"{labels[column]}={self._clean(row[column])}"
                for column in range(len(labels))
                if self._clean(row[column])
            ]
            if values:
                lines.append("; ".join(values))
        for note in context["notes"]:
            lines.append("주석: " + note["text"])
        return "\n".join(lines)

    @staticmethod
    def _content_type(value: str) -> str:
        compact = value.replace(",", "").replace(" ", "")
        if not compact:
            return "empty"
        if compact in {"-", "–", "—", "N/A", "n/a"}:
            return "missing"
        if re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?", compact):
            return "number"
        return "text"

    def _table_hierarchy(
        self,
        table: dict[str, Any],
        row_indices: list[int],
        column_paths: list[list[str]],
        column_source_ids: list[list[str]],
        units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stub = int(table["stub_column_count"])
        columns = [
            {
                "column_id": f"c{column}",
                "column_index": column,
                "role": "row_dimension" if column < stub else "measure",
                "path": path or [
                    "구분" if column < stub else f"열 {column + 1}"
                ],
                "source_cell_ids": column_source_ids[column],
            }
            for column, path in enumerate(column_paths)
        ]
        rows = self._row_hierarchy(table, row_indices)
        matrix = table.get("matrix") or []
        unit_refs = [unit["unit_id"] for unit in units]
        cells = []
        for row_index in row_indices:
            for column in range(stub, int(table["column_count"])):
                value = self._clean(
                    matrix[row_index][column]
                    if column < len(matrix[row_index])
                    else ""
                )
                cells.append(
                    {
                        "row_id": f"r{row_index}",
                        "column_id": f"c{column}",
                        "value": value,
                        "content_type": self._content_type(value),
                        "unit_refs": unit_refs,
                        "source_cell_id": f"{table['table_id']}:R{row_index}:C{column}",
                    }
                )
        spans = [
            {
                "source_table_id": table["table_id"],
                "row": int(merge.get("row", 0)),
                "column": int(merge.get("column", 0)),
                "row_span": int(merge.get("row_span", 1)),
                "col_span": int(merge.get("col_span", 1)),
            }
            for merge in table.get("merge_evidence") or []
        ]
        return {"columns": columns, "rows": rows, "cells": cells, "spans": spans}

    def _split_table_rows(
        self, table: dict[str, Any], row_indices: list[int]
    ) -> list[list[int]]:
        if not row_indices:
            return [[]]
        matrix = table.get("matrix") or []
        groups: list[list[int]] = []
        current: list[int] = []
        current_length = 0
        for row_index in row_indices:
            row_length = sum(len(self._clean(value)) + 3 for value in matrix[row_index])
            if current and current_length + row_length > self.max_chars:
                groups.append(current)
                overlap = current[-self.table_overlap_rows :] if self.table_overlap_rows else []
                current = list(overlap)
                current_length = sum(
                    sum(len(self._clean(value)) + 3 for value in matrix[index])
                    for index in current
                )
            current.append(row_index)
            current_length += row_length
        if current:
            groups.append(current)
        return groups

    def _table_windows(self, master: dict[str, Any]) -> list[dict[str, Any]]:
        tables = sorted(
            master.get("semantic_elements", {}).get("tables", []),
            key=lambda table: (
                int(table["page"]),
                float(table["bbox"][1]),
                float(table["bbox"][0]),
            ),
        )
        by_id = {table["table_id"]: table for table in tables}
        incoming_links = {
            link["to"]: link
            for link in master.get("table_continuity", [])
            if link.get("status") == "CONFIRMED"
        }
        group_has_body: dict[str, bool] = {}
        for table in tables:
            logical_id = table.get("logical_table_id") or table["table_id"]
            group_has_body[logical_id] = group_has_body.get(logical_id, False) or (
                int(table.get("header_depth", 0)) < int(table.get("row_count", 0))
            )

        windows: list[dict[str, Any]] = []
        for table in tables:
            header_depth = int(table.get("header_depth", 0))
            row_count = int(table.get("row_count", len(table.get("matrix") or [])))
            logical_id = table.get("logical_table_id") or table["table_id"]
            row_indices = list(range(header_depth, row_count))
            if not row_indices and group_has_body.get(logical_id):
                continue
            column_paths, column_source_ids = self._column_paths(
                table, by_id, incoming_links
            )
            context = self._table_context(table)
            parts = self._split_table_rows(table, row_indices)
            for part_index, part_rows in enumerate(parts, 1):
                chunk_id = f"CH_TABLE_{table['table_id']}_{part_index:03d}"
                source_ids = [table["table_id"]]
                inherited = table.get("inherited_header") or {}
                if inherited.get("source_table_id"):
                    source_ids.insert(0, inherited["source_table_id"])
                plain = self._table_plain(
                    table, part_rows, column_paths, context
                )
                windows.append(
                    {
                        "kind": "table",
                        "chunk_id": chunk_id,
                        "sort_key": (
                            int(table["page"]),
                            float(table["bbox"][1]),
                            1,
                            part_index,
                        ),
                        "pages": sorted(
                            {
                                int(table["page"]),
                                *(
                                    [int(inherited["source_page"])]
                                    if inherited.get("source_page")
                                    else []
                                ),
                            }
                        ),
                        "object_ids": list(dict.fromkeys(source_ids)),
                        "bboxes": [table["bbox"]],
                        "section_path": [],
                        "logical_table_id": logical_id,
                        "table": table,
                        "row_indices": part_rows,
                        "column_paths": column_paths,
                        "column_source_ids": column_source_ids,
                        "context": context,
                        "part_index": part_index,
                        "part_count": len(parts),
                        "embedding_text": plain,
                    }
                )
        return windows

    def _source_payload(self, window: dict[str, Any]) -> dict[str, Any]:
        source = {
            "pages": window["pages"],
            "object_ids": window["object_ids"],
            "bboxes": window["bboxes"],
        }
        if window.get("logical_table_id"):
            source["logical_table_id"] = window["logical_table_id"]
        return source

    def _structured_payload(self, window: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "representation_level": int(self.representation_level),
            "representation_name": self.RENDERER_NAMES[self.representation_level],
            "chunk_id": window["chunk_id"],
            "source": self._source_payload(window),
            "section_path": window["section_path"],
            "elements": [],
        }
        if window["kind"] == "text":
            payload["elements"].append(
                {"type": "text", "content": window["content"]}
            )
            return payload

        table = window["table"]
        context = window["context"]
        table_element: dict[str, Any] = {
            "type": "table",
            "logical_table_id": window["logical_table_id"],
            "physical_table_id": table["table_id"],
            "segment": {
                "part_index": window["part_index"],
                "part_count": window["part_count"],
                "header_depth": int(table["header_depth"]),
                "stub_column_count": int(table["stub_column_count"]),
            },
            "title": context["title"],
            "content": {
                "format": "markdown",
                "value": self._table_markdown(
                    table, window["row_indices"], window["column_paths"]
                ),
            },
            "units": context["units"],
            "notes": context["notes"],
        }
        if self.representation_level >= ChunkRepresentationLevel.HIERARCHICAL:
            table_element["hierarchy"] = self._table_hierarchy(
                table,
                window["row_indices"],
                window["column_paths"],
                window["column_source_ids"],
                context["units"],
            )
        payload["elements"].append(table_element)
        return payload

    def _record(self, window: dict[str, Any]) -> ChunkRecord:
        metadata = {
            "representation_level": int(self.representation_level),
            "representation_name": self.RENDERER_NAMES[self.representation_level],
            "content_type": window["kind"],
            "pages": window["pages"],
            "object_ids": window["object_ids"],
            "section_path": window["section_path"],
        }
        if window.get("logical_table_id"):
            metadata["logical_table_id"] = window["logical_table_id"]
            metadata["physical_table_id"] = window["table"]["table_id"]
            metadata["part_index"] = window["part_index"]
            metadata["part_count"] = window["part_count"]
        if self.representation_level == ChunkRepresentationLevel.PLAIN:
            document = window["embedding_text"]
        else:
            document = json.dumps(
                self._structured_payload(window),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return ChunkRecord(
            chunk_id=window["chunk_id"],
            embedding_text=window["embedding_text"],
            document=document,
            metadata=metadata,
        )

    def build(self, master_json: dict[str, Any]) -> list[ChunkRecord]:
        if not isinstance(master_json, dict):
            raise TypeError("master_json must be a dict")
        required = {"annotation", "semantic_elements", "raw_document"}
        missing = required.difference(master_json)
        if missing:
            raise ValueError(f"MASTER_JSON missing keys: {sorted(missing)}")
        windows = [
            *self._text_windows(master_json),
            *self._table_windows(master_json),
        ]
        windows.sort(key=lambda window: window["sort_key"])
        records = [self._record(window) for window in windows]
        ids = [record.chunk_id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Chunk IDs must be unique")
        return records


def chunk_records_to_dicts(records: Iterable[ChunkRecord]) -> list[dict[str, Any]]:
    return [record.to_dict() for record in records]


