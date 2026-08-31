from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from semantic_prompt_transfer import E5OnnxEncoder, build_colab_poc


def main(
    output_dir: str,
    model_dir: str,
    credit_template: str,
    first_pdf: str,
    second_pdf: str,
) -> int:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    package_root = Path(__file__).resolve().parents[1]
    mapping = package_root / "src/semantic_prompt_transfer/examples/operational/credit_report_template.json"
    few_shots = package_root / "src/semantic_prompt_transfer/examples/operational/few_shots.json"

    with tempfile.TemporaryDirectory(prefix="spt-v022-api-") as tmp:
        tmp_root = Path(tmp)
        filled = tmp_root / "신용조사서_검증.xlsx"
        shutil.copy2(credit_template, filled)
        workbook = load_workbook(filled)
        workbook["기초자료"]["B2"] = 120000
        workbook["기초자료"]["C2"] = "2025"
        workbook["기초자료"]["B3"] = 8500
        workbook["기초자료"]["C3"] = "2025"
        workbook["공통"]["B2"] = "API 계약 검증용 샘플 기업"
        workbook.save(filled)
        workbook.close()

        runtime_root = tmp_root / "runtime"
        bundle = build_colab_poc(
            model_dir=model_dir,
            root=runtime_root,
            credit_template_path=mapping,
            few_shot_path=few_shots,
            encoder=E5OnnxEncoder(model_dir, batch_size=4),
            require_content_root=False,
        )
        try:
            with TestClient(bundle.app) as client:
                health = client.get("/api/v1/runtime/health")
                assert health.status_code == 200, health.text
                signup = client.post(
                    "/api/v1/poc/users",
                    json={
                        "department": "기업심사부",
                        "name": "API 검증사용자",
                        "employee_number": "API22001",
                    },
                )
                assert signup.status_code == 201, signup.text
                duplicate = client.post(
                    "/api/v1/poc/users",
                    json={
                        "department": "기업심사부",
                        "name": "중복",
                        "employee_number": "API22001",
                    },
                )
                assert duplicate.status_code == 409, duplicate.text
                login = client.post(
                    "/api/v1/poc/login",
                    json={"user_id": "API22001", "password": "API22001"},
                )
                assert login.status_code == 200, login.text
                grant = login.json()
                token = grant["access_token"]
                case_id = grant["case_id"]
                auth = {"X-POC-Token": token}

                assert client.get("/api/v1/templates/credit-report.xlsx").status_code == 401
                template_response = client.get(
                    "/api/v1/templates/credit-report.xlsx", headers=auth
                )
                assert template_response.status_code == 200, template_response.text
                assert template_response.content[:2] == b"PK"

                with filled.open("rb") as handle:
                    credit = client.post(
                        f"/api/v1/cases/{case_id}/credit-report",
                        headers=auth,
                        files={
                            "file": (
                                filled.name,
                                handle,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                        },
                    )
                assert credit.status_code == 202, credit.text

                attachment_rows = []
                for source_name in (first_pdf, second_pdf):
                    source = Path(source_name)
                    with source.open("rb") as handle:
                        response = client.post(
                            f"/api/v1/cases/{case_id}/attachments",
                            headers=auth,
                            files={"file": (source.name, handle, "application/pdf")},
                        )
                    assert response.status_code == 202, response.text
                    attachment_rows.append(response.json())

                listed = client.get(
                    f"/api/v1/cases/{case_id}/documents", headers=auth
                )
                assert listed.status_code == 200, listed.text
                documents = listed.json()
                assert len(documents["credit_report"]) == 1
                assert len(documents["attachments"]) == 2
                assert all(
                    row["status"] == "READY"
                    for row in documents["credit_report"] + documents["attachments"]
                )

                start = client.post(
                    f"/api/v1/cases/{case_id}/review-jobs", headers=auth
                )
                assert start.status_code == 202, start.text
                job_id = start.json()["job_id"]
                job = client.get(f"/api/v1/review-jobs/{job_id}", headers=auth)
                assert job.status_code == 200 and job.json()["progress"] == 100, job.text
                opinion = client.get(
                    f"/api/v1/review-jobs/{job_id}/opinion.docx", headers=auth
                )
                assert opinion.status_code == 200 and opinion.content[:2] == b"PK", opinion.text

                delete_id = attachment_rows[0]["document_id"]
                deleted = client.delete(
                    f"/api/v1/cases/{case_id}/documents/{delete_id}", headers=auth
                )
                assert deleted.status_code == 200, deleted.text
                after = client.get(
                    f"/api/v1/cases/{case_id}/documents", headers=auth
                ).json()
                assert len(after["attachments"]) == 1
                assert bundle.runtime.vectors.count(
                    {
                        "tenant_id": grant["tenant_id"],
                        "case_id": case_id,
                        "document_id": delete_id,
                    }
                ) == 0

                client.post(
                    "/api/v1/poc/users",
                    json={
                        "department": "타부서",
                        "name": "격리 검증사용자",
                        "employee_number": "API22002",
                    },
                )
                other_login = client.post(
                    "/api/v1/poc/login",
                    json={"user_id": "API22002", "password": "API22002"},
                ).json()
                cross = client.get(
                    f"/api/v1/cases/{case_id}/documents",
                    headers={"X-POC-Token": other_login["access_token"]},
                )
                assert cross.status_code == 401, cross.text

                opinion_path = output / "SemanticPromptTransfer_v0.22_API_SMOKE_OPINION.docx"
                opinion_path.write_bytes(opinion.content)
                report = {
                    "package_version": health.json()["version"],
                    "health_status": health.json()["status"],
                    "storage_mode": health.json()["storage_mode"],
                    "signup_status": signup.status_code,
                    "duplicate_signup_status": duplicate.status_code,
                    "login_status": login.status_code,
                    "anonymous_template_status": 401,
                    "authorized_template_status": template_response.status_code,
                    "credit_upload_status": credit.status_code,
                    "attachment_upload_count": len(attachment_rows),
                    "ready_document_count": 3,
                    "review_progress": job.json()["progress"],
                    "opinion_download_status": opinion.status_code,
                    "delete_result": deleted.json(),
                    "active_attachments_after_delete": len(after["attachments"]),
                    "cross_user_scope_status": cross.status_code,
                }
        finally:
            close_result = bundle.close()

        report["runtime_purged"] = bool(close_result["purged"])
        report["runtime_root_absent"] = not runtime_root.exists()
        target = output / "SemanticPromptTransfer_v0.22_FASTAPI_SMOKE.json"
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:6]))

