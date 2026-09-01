# SemanticPromptTransfer v0.26.6 검증 기록

검증일: 2026-09-01

## 핵심 변경
- 규칙 기반 semantic validator의 생성 제어 제거, 기본 VerificationMode=OFF
- 향후 SHADOW/ENFORCE용 claim 단위 검증과 최소 span patch 구조
- 자유대화 GENERAL / CASE_QA / OPINION_QA 분리, CASE_QA query-time RAG
- 자유대화에는 검증 LLM·few-shot·repair 미적용
- 동일 화면/표 근거 병합, 관련성 범위 내 시각적 근거 다양성 우선
- Word 본문 [근거 N]와 근거 캡처 부록, 내부 북마크/복귀 링크
- Word 상단은 심사건만 유지
- PDF/XLSX context-first 근거 캡처 및 실제 근거 영역 highlight
- v0.26.5 FEW SHOT 구조 유지 및 이미지 기준 FEW_SHOT_1 승계

## 자동 검증
- 전체 pytest 통과
- Python compileall 통과
- v0.26.6 Colab notebook 전체 code cell compile 통과
- 새 FEW_SHOT_1 식별값 포함 및 구 FEW_SHOT_1 식별값 미포함 확인

## 산출물
| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| semantic_prompt_transfer-0.26.6-py3-none-any.whl | 197,157 | `32e26fe6a5202164111907d5260e44f1f8d5783c7709b45100736fdd59ad6eb2` |
| SemanticPromptTransfer_v0.26.6_COLAB_LAUNCHER.ipynb | 53,429 | `06f31b049390adf943411f2986aff1c33a7f6dcdf823f5a0472de1c86e7e6fc0` |
| SemanticPromptTransfer_v0.26.6_COLAB_ASSETS.json | 997 | `c72078ca44b471fca2d8b6df2b80ef58bc3d066c7d39490d16271bbb38eb7c9e` |

실제 A100 Gemma 4 가중치 적재와 첫 실데이터 생성 품질은 Colab 운영 테스트에서 최종 확인한다.
