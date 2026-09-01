from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from semantic_prompt_transfer.colab_runtime import EphemeralColabConfig, EphemeralColabRuntime
from semantic_prompt_transfer.web import create_fastapi_app


class NeverProcess:
    def __init__(self):
        self.calls = 0

    def process(self, scope, source_path, document_kind, progress):
        self.calls += 1
        raise AssertionError("demo seeding must not parse or embed")


def test_demo_files_seed_once_are_downloadable_and_begin_background_processing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime = EphemeralColabRuntime(EphemeralColabConfig(root=root / "runtime", require_content_root=False, clean_start=True))
        credit = root / "신용조사서_ABC기업_v1.0.xlsx"
        report = root / "[ABC기업]사업보고서(2026.03.23).pdf"
        credit_bytes = b"demo-xlsx-bytes"
        report_bytes = b"%PDF-1.4 demo-pdf-bytes"
        credit.write_bytes(credit_bytes)
        report.write_bytes(report_bytes)
        processor = NeverProcess()
        app = create_fastapi_app(
            runtime.application,
            runtime.artifacts,
            processor,
            session_manager=None,
            demo_credit_report_path=credit,
            demo_attachment_paths=(report,),
        )
        client = TestClient(app)
        response = client.get("/api/v1/cases/case-demo/documents", params={"tenant_id": "poc-demo"})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["credit_report"]) == 1
        assert len(payload["attachments"]) == 1
        rows = payload["credit_report"] + payload["attachments"]
        assert all(row["status"] in {"VALIDATING", "PARSING", "INDEXING", "READY", "EXCLUDED"} for row in rows)
        assert all(0 <= int(row["progress_percent"]) <= 100 for row in rows)
        assert all(row["is_demo"] is True for row in rows)
        for _ in range(50):
            if processor.calls >= 2:
                break
            time.sleep(0.01)
        assert processor.calls == 2
        assert runtime.vectors.count() == 0
        credit_row = payload["credit_report"][0]
        downloaded = client.get(
            f"/api/v1/cases/case-demo/documents/{credit_row['document_id']}/download",
            params={"tenant_id": "poc-demo"},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == credit_bytes
        deleted = client.delete(
            f"/api/v1/cases/case-demo/documents/{credit_row['document_id']}",
            params={"tenant_id": "poc-demo"},
        )
        assert deleted.status_code == 200
        after = client.get("/api/v1/cases/case-demo/documents", params={"tenant_id": "poc-demo"}).json()
        assert after["credit_report"] == []
        assert len(after["attachments"]) == 1
        assert processor.calls == 2
        assert runtime.vectors.count() == 0
        runtime.close(purge=True)


def test_v0265_notebook_declares_deferred_demo_assets():
    import json
    notebook_path = Path(__file__).resolve().parents[1] / "notebooks" / "SemanticPromptTransfer_v0.26.5_COLAB_LAUNCHER.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    code = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"] if cell.get("cell_type") == "code")
    for cell in notebook["cells"]:
        if cell.get("cell_type") == "code":
            compile("".join(cell.get("source", [])), notebook_path.name, "exec")
    assert 'RELEASE = "v0.26.5"' in code
    assert 'PACKAGE_VERSION = "0.26.5"' in code
    assert '신용조사서_ABC기업_v1.0.xlsx' in code
    assert '[ABC기업]사업보고서(2026.03.23).pdf' in code
    assert 'demo_credit_report_path=DEMO_CREDIT_REPORT' in code
    assert 'demo_attachment_paths=DEMO_ATTACHMENTS' in code
