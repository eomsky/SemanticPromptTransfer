from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "semantic_prompt_transfer.egg-info",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def include_source(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not any(part in EXCLUDED_PARTS for part in relative.parts)


def copy_named(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def build(package_root: Path, output_dir: Path, version: str) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = "v" + ".".join(version.split(".")[:2])
    artifacts: list[Path] = []

    copies = {
        package_root / "dist" / f"semantic_prompt_transfer-{version}-py3-none-any.whl":
            output_dir / "dist" / f"semantic_prompt_transfer-{version}-py3-none-any.whl",
        package_root / "dist" / f"semantic_prompt_transfer-{version}.tar.gz":
            output_dir / "dist" / f"semantic_prompt_transfer-{version}.tar.gz",
        package_root / "src" / "semantic_prompt_transfer" / "examples" / "operational" / "credit_review_upload_demo.html":
            output_dir / f"credit_review_upload_demo_{tag}.html",
        package_root / "src" / "semantic_prompt_transfer" / "examples" / "operational" / "credit_report_sample_template.xlsx":
            output_dir / f"credit_report_sample_template_{tag}.xlsx",
        package_root / "docs" / f"REQUIREMENTS_{tag}.md":
            output_dir / f"SemanticPromptTransfer_{tag}_REQUIREMENTS.md",
        package_root / "docs" / f"VALIDATION_{tag}.md":
            output_dir / f"SemanticPromptTransfer_{tag}_VALIDATION.md",
        package_root / "docs" / "OPERATIONAL_ARCHITECTURE.md":
            output_dir / f"SemanticPromptTransfer_{tag}_OPERATIONAL_ARCHITECTURE.md",
        package_root / "docs" / "COLAB_POC_RUNBOOK.md":
            output_dir / f"SemanticPromptTransfer_{tag}_COLAB_POC_RUNBOOK.md",
        package_root / "docs" / "RELEASE_MANAGEMENT.md":
            output_dir / f"SemanticPromptTransfer_{tag}_RELEASE_MANAGEMENT.md",
    }
    for source, target in copies.items():
        if not source.is_file():
            raise FileNotFoundError(source)
        artifacts.append(copy_named(source, target))

    bundle = output_dir / f"SemanticPromptTransfer_{tag}_SOURCE_BUNDLE.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(path for path in package_root.rglob("*") if path.is_file()):
            if include_source(source, package_root):
                archive.write(source, source.relative_to(package_root))
    artifacts.append(bundle)

    for suffix in ("docx", "pdf"):
        existing = output_dir / f"SemanticPromptTransfer_{tag}_REQUIREMENTS.{suffix}"
        if existing.is_file():
            artifacts.append(existing)
    artifacts.extend(
        sorted(
            path
            for path in output_dir.glob(f"SemanticPromptTransfer_{tag}_*.json")
            if "MANIFEST" not in path.name
        )
    )

    unique_artifacts = sorted({path.resolve() for path in artifacts})
    manifest = {
        "package": "semantic-prompt-transfer",
        "version": version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": [
            {
                "path": str(path.relative_to(output_dir.resolve())),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in unique_artifacts
        ],
    }
    manifest_path = output_dir / f"SemanticPromptTransfer_{tag}_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", default="0.22.0")
    args = parser.parse_args()
    manifest = build(args.package_root.resolve(), args.output_dir.resolve(), args.version)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
