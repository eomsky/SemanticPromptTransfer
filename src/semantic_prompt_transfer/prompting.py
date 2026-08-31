from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class PromptPackage:
    schema_version: str
    query_id: str
    query: str
    representation_level: int
    messages: list[dict[str, str]]
    evidence: list[dict[str, Any]]
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PromptPackageBuilder:
    """Cell 7: build a provider-neutral, source-traceable LLM prompt."""

    def __init__(self, max_context_chars: int = 24000) -> None:
        self.max_context_chars = int(max_context_chars)

    def build(
        self,
        query_id: str,
        query: str,
        retrieval: dict[str, Any],
        representation_level: int,
        manifest: dict[str, Any],
    ) -> PromptPackage:
        evidence = []
        rendered = []
        used = 0
        for hit in retrieval["hits"]:
            document = str(hit["document"])
            metadata = hit["metadata"]
            global_id = metadata.get("global_chunk_id") or hit["chunk_id"]
            block = (
                f"[근거 {hit['rank']}]\n"
                f"evidence_id={query_id}-L{representation_level}-R{hit['rank']}\n"
                f"global_chunk_id={global_id}\n"
                f"local_chunk_id={metadata.get('local_chunk_id') or hit['chunk_id']}\n"
                f"document_id={metadata.get('document_id')}\n"
                f"source_filename={metadata.get('source_filename')}\n"
                f"source_pages={metadata.get('pages', [])}\n"
                f"content:\n{document}\n"
            )
            if rendered and used + len(block) > self.max_context_chars:
                break
            used += len(block)
            rendered.append(block)
            evidence.append(
                {
                    "evidence_id": f"{query_id}-L{representation_level}-R{hit['rank']}",
                    "chunk_id": hit["chunk_id"],
                    "global_chunk_id": global_id,
                    "document_id": metadata.get("document_id"),
                    "source_filename": metadata.get("source_filename"),
                    "variant_id": hit.get("variant_id"),
                    "pages": metadata.get("pages", []),
                    "logical_table_id": metadata.get("logical_table_id"),
                    "score": hit["score"],
                }
            )
        system = (
            "제공된 근거만 사용한다. 수치, 기간 및 단위를 변형하지 않는다. "
            "근거가 부족하면 부족하다고 명시하며 모든 핵심 주장에 evidence_id를 표시한다."
        )
        user = f"질의:\n{query}\n\n구조화 수준: {representation_level}\n\n" + "\n".join(rendered)
        return PromptPackage(
            schema_version="prompt-package-1.0",
            query_id=query_id,
            query=query,
            representation_level=int(representation_level),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            evidence=evidence,
            manifest=dict(manifest),
        )

