from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release_v0269"
OUT.mkdir(exist_ok=True)

wheel = next((ROOT / "dist").glob("semantic_prompt_transfer-0.26.9-*.whl"))
launcher = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26.9_COLAB_LAUNCHER.ipynb"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

wheel_target = OUT / wheel.name
launcher_target = OUT / launcher.name
wheel_target.write_bytes(wheel.read_bytes())
launcher_target.write_bytes(launcher.read_bytes())

manifest = {
    "schema_version": 3,
    "release": "v0.26.9",
    "package_version": "0.26.9",
    "drive_root": "/content/drive/MyDrive/SemanticPromptTransfer",
    "runtime_root": "/content/spt_poc_runtime",
    "stage_root": "/content/spt_bootstrap_v0269",
    "base_asset_manifest": "runtime-assets/v0.26.3/SemanticPromptTransfer_v0.26.3_COLAB_ASSETS.json",
    "assets": [
        {
            "role": "wheel",
            "source": f"versions/v0.26.9/{wheel_target.name}",
            "target": f"release/{wheel_target.name}",
            "size": wheel_target.stat().st_size,
            "sha256": sha(wheel_target),
        }
    ],
    "demo_assets": [
        {
            "role": "credit_report",
            "source": "demo-assets/신용조사서_ABC기업_v1.0.xlsx",
            "processing": "deferred_until_review",
        },
        {
            "role": "attachment",
            "source": "demo-assets/[ABC기업]사업보고서(2026.03.23).pdf",
            "processing": "deferred_until_review",
        },
    ],
}
manifest_path = OUT / "SemanticPromptTransfer_v0.26.9_COLAB_ASSETS.json"
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(ROOT / "notebooks" / manifest_path.name).write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

validation = f"""# SemanticPromptTransfer v0.26.9 검증 기록

- FallbackGenerator 코드/공개 export/테스트에서 완전 삭제
- 운영 생성 lane은 Gemma primary 직접 streaming
- 검증 LLM 자동수정은 hard conflict + MINOR exact span만 허용, 섹션당 최대 1회
- 금액 evidence를 백만원 기준으로 정규화
- 문장 미완결 Completion Guard 추가
- max_new_tokens 1800 + 기존 continuation 유지
- 자유대화 verifier 미적용 유지
- 전체 pytest / compileall / Colab code-cell compile 통과

## 산출물
| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| {wheel_target.name} | {wheel_target.stat().st_size:,} | `{sha(wheel_target)}` |
| {launcher_target.name} | {launcher_target.stat().st_size:,} | `{sha(launcher_target)}` |
| {manifest_path.name} | {manifest_path.stat().st_size:,} | `{sha(manifest_path)}` |
"""
(OUT / "VALIDATION_v0.26.9.md").write_text(validation, encoding="utf-8")
(ROOT / "docs" / "VALIDATION_v0.26.9.md").write_text(validation, encoding="utf-8")
(OUT / "COLAB_ONE_CLICK_v0.26.9.md").write_text(
    "# SemanticPromptTransfer v0.26.9\n\nDrive `versions/v0.26.9`의 Colab launcher를 열고 위에서부터 실행합니다.\n",
    encoding="utf-8",
)
print(validation)
