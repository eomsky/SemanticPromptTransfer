from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser(description="Check version and packaged-resource alignment")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = project["project"]["version"]
    version_text = (root / "src/semantic_prompt_transfer/version.py").read_text(encoding="utf-8")
    match = re.search(r'^PACKAGE_VERSION\s*=\s*"([^"]+)"', version_text, re.MULTILINE)
    if not match:
        raise RuntimeError("PACKAGE_VERSION is missing")
    runtime_version = match.group(1)

    checks = {
        "pyproject_runtime_version_match": pyproject_version == runtime_version,
        "changelog_has_version": f"## {runtime_version}" in (root / "CHANGELOG.md").read_text(encoding="utf-8"),
        "requirements_has_version": runtime_version
        in (root / "docs/REQUIREMENTS_v0.22.md").read_text(encoding="utf-8"),
        "html_source_exists": (root / "src/semantic_prompt_transfer/examples/operational/credit_review_upload_demo.html").is_file(),
        "credit_template_exists": (root / "src/semantic_prompt_transfer/examples/operational/credit_report_sample_template.xlsx").is_file(),
        "poc_server_exists": (root / "src/semantic_prompt_transfer/poc_server.py").is_file(),
        "release_management_exists": (root / "docs/RELEASE_MANAGEMENT.md").is_file(),
    }

    if args.wheel:
        with zipfile.ZipFile(args.wheel) as archive:
            names = archive.namelist()
        checks["wheel_has_html"] = any(
            name.endswith("examples/operational/credit_review_upload_demo.html")
            for name in names
        )
        checks["wheel_has_version_module"] = any(
            name.endswith("semantic_prompt_transfer/version.py") for name in names
        )
        checks["wheel_has_credit_template"] = any(
            name.endswith("examples/operational/credit_report_sample_template.xlsx")
            for name in names
        )
        checks["wheel_has_poc_server"] = any(
            name.endswith("semantic_prompt_transfer/poc_server.py") for name in names
        )

    report = {
        "version": runtime_version,
        "checks": checks,
        "passed": all(checks.values()),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
