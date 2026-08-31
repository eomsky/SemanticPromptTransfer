from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


class ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.tags: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] += 1
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))


def main(html_path: str, output_path: str) -> int:
    path = Path(html_path)
    html = path.read_text(encoding="utf-8")
    parser = ContractParser()
    parser.feed(html)
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.DOTALL)
    javascript = "\n".join(scripts)
    js_result = subprocess.run(
        ["node", "--check", "-"],
        input=javascript,
        text=True,
        capture_output=True,
        check=False,
    )
    id_counts = Counter(parser.ids)
    checks = {
        "html_parse_completed": True,
        "unique_element_ids": all(count == 1 for count in id_counts.values()),
        "javascript_syntax": js_result.returncode == 0,
        "login_and_signup": all(
            value in html for value in ("회원가입", "부서명", "이름", "사번", "로그인")
        ),
        "inline_filename_delete": all(
            value in html for value in ("inline-files", "file-chip-name", "file-x", "deleteItem")
        ),
        "template_download": all(
            value in html
            for value in ("양식 다운로드", "/api/v1/templates/credit-report.xlsx")
        ),
        "upload_popup_removed": "업로드 자료현황" not in html
        and parser.tags.get("dialog", 0) == 0,
        "upload_progress_bars_removed": "creditBar" not in html
        and "attachmentBar" not in html,
        "review_progress_retained": all(
            value in html for value in ("reportBar", "reportText", "status('report'")
        ),
        "mobile_single_column": "@media(max-width:700px)" in html
        and ".row{grid-template-columns:1fr" in html,
        "sample_xlsx_sibling": path.with_name("credit_report_sample_template.xlsx").is_file(),
    }
    report = {
        "html": path.name,
        "checks": checks,
        "passed": all(checks.values()),
        "element_id_count": len(parser.ids),
        "duplicate_ids": sorted(key for key, count in id_counts.items() if count > 1),
        "javascript_error": js_result.stderr.strip() or None,
        "visual_browser_render": "NOT_AVAILABLE_IN_CURRENT_BROWSER_TOOLING",
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))

