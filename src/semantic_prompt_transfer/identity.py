from __future__ import annotations

import hashlib


def global_chunk_id(
    tenant_id: str,
    case_id: str,
    document_id: str,
    local_chunk_id: str,
    representation_level: int,
) -> str:
    material = "\x1f".join(
        str(value or "").strip()
        for value in (tenant_id, case_id, document_id, local_chunk_id, representation_level)
    )
    return "GCH_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def evidence_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(value or "").strip() for value in parts)
    return f"{prefix}_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
