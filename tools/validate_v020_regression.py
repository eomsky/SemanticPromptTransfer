from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SNAPSHOT_CODE = r'''
import hashlib, json, sys
from semantic_prompt_transfer.chunking import PackageChunkBuilder
master = json.loads(open(sys.argv[1], encoding="utf-8").read())
records = PackageChunkBuilder(
    representation_level=0,
    max_chars=1800,
    text_overlap_chars=180,
    table_overlap_rows=0,
).build(master)
payload = [[r.chunk_id, r.embedding_text, r.document] for r in records]
raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
print(json.dumps({"count": len(records), "sha256": hashlib.sha256(raw).hexdigest()}))
'''


def snapshot(package_root: Path, master_path: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(package_root / "src")
    output = subprocess.check_output(
        [sys.executable, "-c", SNAPSHOT_CODE, str(master_path)],
        env=env,
        text=True,
    )
    return json.loads(output)


def main(v019_root: str, v020_root: str, master_path: str, output_path: str) -> int:
    old = snapshot(Path(v019_root).resolve(), Path(master_path).resolve())
    new = snapshot(Path(v020_root).resolve(), Path(master_path).resolve())
    report = {
        "baseline": "v0.19.0",
        "candidate": "v0.20.0",
        "comparison_scope": ["chunk_id", "embedding_text", "document"],
        "configuration": {"max_chars": 1800, "text_overlap_chars": 180, "table_overlap_rows": 0},
        "v019": old,
        "v020": new,
        "identical": old == new,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
