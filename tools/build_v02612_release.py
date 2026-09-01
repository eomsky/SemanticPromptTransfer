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
    'assets': [{
        'role': 'wheel',
        'source': f'versions/{RELEASE}/{wheel.name}',
        'target': f'release/{wheel.name}',
        'size': wheel_out.stat().st_size,
        'sha256': sha256(wheel_out),
    }],
    'demo_assets': [
        {'role': 'credit_report', 'source': 'demo-assets/신용조사서_ABC기업_v1.0.xlsx', 'processing': 'background_on_seed'},
        {'role': 'attachment', 'source': 'demo-assets/[ABC기업]사업보고서(2026.03.23).pdf', 'processing': 'background_on_seed'},
    ],
    'multi_user': {
        'scheduler_policy': 'ROUND_ROBIN_FAIR_SHARE',
        'fair_share_parallel_quanta': 2,
        'vllm_max_num_seqs': 4,
        'queue_idle_timeout_seconds': 30,
        'idle_policy': 'finish_current_quantum_then_suspend',
        'resume_policy': 'same_case_same_job',
        'e5_gpu_serialized_by_encoder_lock': True,
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
- 직렬 FIFO 독점 방식 대신 ROUND_ROBIN_FAIR_SHARE scheduler 적용
- Retrieval / Credit Reasoning / A~E Generation / Verification을 work quantum으로 분할해 여러 사용자에게 순환 배분
- app-level 동시 expensive quantum 2건 + vLLM max-num-seqs 4 continuous batching
- 30초 실제 사용자 조작이 없으면 현재 quantum 종료 후 SUSPENDED, GPU 몫을 다른 접속자에게 양보
- 재접속 시 기존 업로드·임베딩·생성 결과를 유지한 채 동일 job을 RUNNABLE로 복귀
- 26% 구간을 E5 query batch / 근거 선별 / Credit Reasoning 단계로 세분화
- 최종 심사의견은 prose-only: 근거 요약표/Markdown 표/CSV 출력 금지 및 방어적 제거
- 값이 없는 기간을 '-' / 0 / 임의 수치로 채우는 행위 금지
- v0.26.11 Credit Reasoning, 28,672 context, 3,600 generation tokens, batch verifier, 근거추적/Word 품질 유지

## 자동 검증
- 전체 pytest 통과
- compileall 통과
- Colab 전체 code cell compile 통과
- launcher MODEL_CONTEXT_TOKENS=28672 / generation max_new_tokens=3600 / max-num-seqs=4 확인
- fair_share_parallel_quanta=2 / queue_idle_timeout_seconds=30 확인
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
    '# SemanticPromptTransfer v0.26.12\n\nGoogle Drive의 versions/v0.26.12와 runtime-assets/v0.26.12를 적재한 뒤 Colab Launcher를 실행합니다. 다중접속은 공정공유 방식으로 순환 처리되며, 30초 미사용 시 현재 작업단위 종료 후 일시 중단하고 재접속 시 이어서 진행합니다.\n',
    encoding='utf-8',
)

print('release assets built')
for p in sorted(OUT.iterdir()):
    if p.is_file():
        print(p.name, p.stat().st_size, sha256(p))
