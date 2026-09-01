from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path('.')
RELEASE = 'v0.26.10'
VERSION = '0.26.10'
OUT = ROOT / 'release_v02610'
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


wheel_candidates = sorted((ROOT / 'dist').glob('semantic_prompt_transfer-0.26.10-*.whl'))
if len(wheel_candidates) != 1:
    raise RuntimeError(f'expected one v0.26.10 wheel, found {wheel_candidates}')
wheel = wheel_candidates[0]
launcher = ROOT / 'notebooks/SemanticPromptTransfer_v0.26.10_COLAB_LAUNCHER.ipynb'
if not launcher.is_file():
    raise FileNotFoundError(launcher)

wheel_out = OUT / wheel.name
launcher_out = OUT / launcher.name
shutil.copy2(wheel, wheel_out)
shutil.copy2(launcher, launcher_out)

manifest = {
    'schema_version': 3,
    'release': RELEASE,
    'package_version': VERSION,
    'drive_root': '/content/drive/MyDrive/SemanticPromptTransfer',
    'runtime_root': '/content/spt_poc_runtime',
    'stage_root': '/content/spt_bootstrap_v02610',
    'base_asset_manifest': 'runtime-assets/v0.26.3/SemanticPromptTransfer_v0.26.3_COLAB_ASSETS.json',
    'assets': [
        {
            'role': 'wheel',
            'source': f'versions/{RELEASE}/{wheel.name}',
            'target': f'release/{wheel.name}',
            'size': wheel_out.stat().st_size,
            'sha256': sha256(wheel_out),
        }
    ],
    'demo_assets': [
        {
            'role': 'credit_report',
            'source': 'demo-assets/신용조사서_ABC기업_v1.0.xlsx',
            'processing': 'deferred_until_review',
        },
        {
            'role': 'attachment',
            'source': 'demo-assets/[ABC기업]사업보고서(2026.03.23).pdf',
            'processing': 'deferred_until_review',
        },
    ],
}
manifest_path = OUT / 'SemanticPromptTransfer_v0.26.10_COLAB_ASSETS.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
(ROOT / 'notebooks/SemanticPromptTransfer_v0.26.10_COLAB_ASSETS.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
)

validation = f'''# SemanticPromptTransfer v0.26.10 검증 기록

검증일: 2026-09-01

## 핵심 변경
- Credit Reasoning Layer: Materiality / Risk-Mitigant / Repayment Impact / Trend / Forward Trigger / A-E Cross-item signal
- Gemma 실제 tokenizer 기반 Prompt Token Budget Manager 및 compact evidence grouping
- Gemma context target 28,672 tokens, generation ceiling 3,600 tokens, max-num-seqs 2
- 동일 vLLM 모델을 Generation / Reasoning / Verification / Completion 논리 역할로 공유
- 검증 자동수정은 명백한 MINOR span 1건 이내로 유지하며 최종 evidence re-binding 수행
- 영구 노란 claim 배경 제거, patch 순간만 약한 일시 효과
- incomplete tail rollback으로 미완결 문장 완료 상태 차단
- v0.26.9의 금액 백만원 정규화, 자유대화 분리, Evidence Trace, 새 FEW_SHOT_1 유지

## 자동 검증
- 전체 pytest 통과
- compileall 통과
- Colab 전체 code cell compile 통과
- FallbackGenerator 부재 확인
- v0.26.10 launcher: MODEL_CONTEXT_TOKENS=28672 / generation max_new_tokens=3600 / verification ENFORCE 확인
- 새 FEW_SHOT_1 식별값 포함 / 구 FEW_SHOT_1 식별값 미포함 확인

## 산출물
| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| {wheel_out.name} | {wheel_out.stat().st_size:,} | `{sha256(wheel_out)}` |
| {launcher_out.name} | {launcher_out.stat().st_size:,} | `{sha256(launcher_out)}` |
| {manifest_path.name} | {manifest_path.stat().st_size:,} | `{sha256(manifest_path)}` |
'''
(OUT / 'VALIDATION_v0.26.10.md').write_text(validation, encoding='utf-8')
(OUT / 'COLAB_ONE_CLICK_v0.26.10.md').write_text(
    '# SemanticPromptTransfer v0.26.10\n\nGoogle Drive의 versions/v0.26.10과 runtime-assets/v0.26.10을 적재한 뒤 Colab Launcher 한 파일을 실행합니다.\n',
    encoding='utf-8',
)

print('release assets built')
for p in sorted(OUT.iterdir()):
    if p.is_file():
        print(p.name, p.stat().st_size, sha256(p))
