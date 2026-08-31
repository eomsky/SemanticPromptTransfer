# SemanticPromptTransfer v0.26.4 검증 기록

검증일: 2026-09-01

## 핵심 변경

- 검증 실패를 terminal FAILED로 종료하지 않고 자동수정 → 근거기반 대체생성 → 최소 보수문구로 수렴
- 신용조사서와 기타 첨부자료의 상시 우선순위를 폐지하고 동일 사실의 직접 충돌에서만 신용조사서를 채택
- 신용조사서가 존재할 때 prompt evidence의 최소 50%를 가장 근접한 신용조사서 행/청크로 확보(가용 내용이 부족하면 전량 포함)
- 수치 검증을 claim-local citation + signed numeric/date/period/unit 기준으로 강화하고 Excel cell coordinate를 제외
- FEW SHOT 수치/기간/식별자 사전 sanitization, RAG relevance gate·중복 제거, A~E 교차검증 추가
- DOCX 최소 OOXML renderer와 vLLM/검증/파일처리 복구 경로 추가
- UI에서 신용조사서(선택) 및 제1우선순위 문구 제거

## 로컬 검증

- 전체 Python compileall 통과
- v0.26.4 targeted resilience tests 통과
- RAG relevance gate / duplicate removal 테스트 통과
- wheel 신규 설치 smoke test 통과
- Colab notebook 전체 Python code cell compile 통과

## 산출물

| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| `semantic_prompt_transfer-0.26.4-py3-none-any.whl` | 439,517 | `0778813c22a9f4489939f1234f9fb452737f9be163928102c4db813504a26230` |
| `SemanticPromptTransfer_v0.26.4_COLAB_LAUNCHER.ipynb` | 46,154 | `6874cf5ee3690ce36d4bbd160ee7c337ee9683f424e399b1cb2d43d199e75451` |

실제 A100의 Gemma 4 가중치 적재 및 첫 생성은 Colab에서 최종 확인한다.
