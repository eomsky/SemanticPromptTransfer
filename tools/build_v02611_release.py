from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path('.')
RELEASE = 'v0.26.11'
VERSION = '0.26.11'
OUT = ROOT / 'release_v02611'
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


wheel_candidates = sorted((ROOT / 'dist').glob('semantic_prompt_transfer-0.26.11-*.whl'))
if len(wheel_candidates) != 1:
    raise RuntimeError(f'expected one v0.26.11 wheel, found {wheel_candidates}')
wheel = wheel_candidates[0]
launcher = ROOT / 'notebooks/SemanticPromptTransfer_v0.26.11_COLAB_LAUNCHER.ipynb'
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
    'stage_root': '/content/spt_bootstrap_v02611',
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
            'processing': 'background_after_seed',
        },
        {
            'role': 'attachment',
            'source': 'demo-assets/[ABC기업]사업보고서(2026.03.23).pdf',
            'processing': 'background_after_seed',
        },
    ],
}
manifest_path = OUT / 'SemanticPromptTransfer_v0.26.11_COLAB_ASSETS.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
(ROOT / 'notebooks/SemanticPromptTransfer_v0.26.11_COLAB_ASSETS.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
)

validation = f'''# SemanticPromptTransfer v0.26.11 검증 기록

검증일: 2026-09-01

## 핵심 변경
- 소수점·날짜 점을 문장 경계로 오인하지 않는 decimal/date-safe ClaimSegmenter
- 프론트의 별도 문장 regex 제거, 근거 번호만 자연스럽게 inline 매핑
- FEW SHOT STYLE_ONLY의 [VALUE]/[PERIOD]/[COMPANY]/[DATE]/[ENTITY] 유출 차단 및 최종 출력 방어
- Word 근거 이미지를 crop 없이 A4 사용 가능 영역에 자동 축소하는 auto-fit
- 업로드 직후 background parsing/embedding, 심사 시작 시 진행 중 전처리 대기 후 재사용
- E5 A~E query 1회 batch encode
- 검증 LLM을 claim별 호출에서 항목별 batch verifier 호출로 축소
- Generation과 이전 항목 Verification을 max-num-seqs=2에서 overlap하는 2-lane 파이프라인
- 파일 status 실시간 갱신 및 생성 완료 후 서버 상태 재동기화
- v0.26.10 Credit Reasoning, 28,672-token context, 3,600-token generation, evidence trace 유지

## 성능 목표
- 모델·evidence·출력 길이를 축소하지 않고 LLM 호출 수와 대기구간을 줄임
- verifier 호출은 일반적으로 claim 수(수십 회)에서 A~E 최대 5회로 감소
- 전처리는 업로드 후 선행하여 사용자의 생성 버튼 이후 대기시간을 단축
- 실제 A100 체감 단축률 목표는 약 35~55%이며 실데이터 측정으로 확정

## 자동 검증
- 전체 pytest 통과
- compileall 통과
- Colab 전체 code cell compile 통과
- FallbackGenerator 부재 확인
- launcher MODEL_CONTEXT_TOKENS=28672 / generation max_new_tokens=3600 / max-num-seqs=2 유지
- [VALUE] placeholder를 STYLE_ONLY prompt에 사용하지 않는지 확인

## 산출물
| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| {wheel_out.name} | {wheel_out.stat().st_size:,} | `{sha256(wheel_out)}` |
| {launcher_out.name} | {launcher_out.stat().st_size:,} | `{sha256(launcher_out)}` |
| {manifest_path.name} | {manifest_path.stat().st_size:,} | `{sha256(manifest_path)}` |
'''
(OUT / 'VALIDATION_v0.26.11.md').write_text(validation, encoding='utf-8')
(OUT / 'COLAB_ONE_CLICK_v0.26.11.md').write_text(
    '# SemanticPromptTransfer v0.26.11\n\nGoogle Drive의 versions/v0.26.11과 runtime-assets/v0.26.11을 적재한 뒤 Colab Launcher 한 파일을 실행합니다.\n',
    encoding='utf-8',
)

print('release assets built')
for p in sorted(OUT.iterdir()):
    if p.is_file():
        print(p.name, p.stat().st_size, sha256(p))
