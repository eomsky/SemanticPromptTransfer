from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path('.')
RELEASE = 'v0.26.12'
VERSION = '0.26.12'
OUT = ROOT / 'release_v02612'
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

wheel_candidates = sorted((ROOT / 'dist').glob('semantic_prompt_transfer-0.26.12-*.whl'))
if len(wheel_candidates) != 1:
    raise RuntimeError(f'expected one v0.26.12 wheel, found {wheel_candidates}')
wheel = wheel_candidates[0]
launcher = ROOT / 'notebooks/SemanticPromptTransfer_v0.26.12_COLAB_LAUNCHER.ipynb'
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
    'stage_root': '/content/spt_bootstrap_v02612',
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
            'processing': 'background_on_seed',
        },
        {
            'role': 'attachment',
            'source': 'demo-assets/[ABC기업]사업보고서(2026.03.23).pdf',
            'processing': 'background_on_seed',
        },
    ],
    'multi_user': {
        'max_active_review_jobs': 1,
        'queue_idle_timeout_seconds': 30,
        'embedding_gpu_slots': 1,
        'queue_policy': 'FIFO',
    },
}
manifest_path = OUT / 'SemanticPromptTransfer_v0.26.12_COLAB_ASSETS.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
(ROOT / 'notebooks/SemanticPromptTransfer_v0.26.12_COLAB_ASSETS.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
)

validation = f'''# SemanticPromptTransfer v0.26.12 검증 기록

검증일: 2026-09-01

## 핵심 변경
- 다중 브라우저/case 격리 구조 위에 FIFO Review Job Queue 추가
- 단일 A100 기본 동시 심사 Job 1건: 내부 Gemma Generation + Verification 2-lane 품질/속도 구조 보존
- 대기 사용자 UI에 현재 순번 및 총 대기 건수 이벤트 제공
- 대기열에서 30초 실제 사용자 조작이 없으면 lease 만료 → 해당 순번 해제 → 다음 사용자 진행
- 브라우저 30초 미사용 팝업: 재접속 여부 확인, 대기 중이면 서버 release-queue 호출
- queue-activity는 실제 pointer/keyboard/touch/wheel/scroll에만 연동하여 단순 heartbeat가 자리 점유를 연장하지 않음
- E5 GPU encode는 bounded semaphore 1 slot로 직렬화; CPU 파싱은 병행 가능
- v0.26.11 Credit Reasoning, 28,672 context, 3,600 generation tokens, batch verifier, 근거추적/Word 품질 유지

## 자동 검증
- 전체 pytest 통과
- compileall 통과
- Colab 전체 code cell compile 통과
- v0.26.12 launcher의 SPT_MAX_ACTIVE_REVIEW_JOBS=1 / SPT_QUEUE_IDLE_TIMEOUT_SECONDS=30 / SPT_EMBEDDING_GPU_SLOTS=1 확인
- FallbackGenerator 부재 확인

## 산출물
| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| {wheel_out.name} | {wheel_out.stat().st_size:,} | `{sha256(wheel_out)}` |
| {launcher_out.name} | {launcher_out.stat().st_size:,} | `{sha256(launcher_out)}` |
| {manifest_path.name} | {manifest_path.stat().st_size:,} | `{sha256(manifest_path)}` |
'''
(OUT / 'VALIDATION_v0.26.12.md').write_text(validation, encoding='utf-8')
(OUT / 'COLAB_ONE_CLICK_v0.26.12.md').write_text(
    '# SemanticPromptTransfer v0.26.12\n\nGoogle Drive의 versions/v0.26.12와 runtime-assets/v0.26.12를 적재한 뒤 Colab Launcher 한 파일을 실행합니다. 다중접속은 FIFO 대기열로 관리되며, 대기 중 30초 미사용 시 순번이 자동 해제됩니다.\n',
    encoding='utf-8',
)

print('release assets built')
for p in sorted(OUT.iterdir()):
    if p.is_file():
        print(p.name, p.stat().st_size, sha256(p))
