# SemanticPromptTransfer v0.26.3 검증 기록

검증일: 2026-08-31

## 검증 항목

- Python 단위·통합 테스트 42개 통과
- v0.26.3 노트북의 모든 Python 코드 셀 컴파일 통과
- vLLM 전용 `uv` 환경과 현재 Colab E5 환경의 격리 유지
- 격리환경 내 Ninja 명시 설치 및 실행파일 사전검사 확인
- vLLM 프로세스 `PATH`에 격리환경 `bin` 우선 주입 확인
- Gemma 4 MoE native 구현, A100 BF16, GPU 메모리 사용률 0.90,
  16,384토큰 및 동시 시퀀스 4 설정 유지
- 엔진 최초 원인 로그와 전체 로그 경로 노출 계약 유지
- FEW SHOT 3건 × A~E, 익명 사용자 분리, 스트리밍, 자동 이어쓰기,
  후속 대화 문맥, 근거 캡처 및 Word 다운로드의 기존 계약 유지
- v0.26.2와 v0.26.3의 wheel·manifest·Drive 경로 독립성 확인

## 산출물 무결성

| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| `semantic_prompt_transfer-0.26.3-py3-none-any.whl` | 176,207 | `4c32ef5c1c815726ead4f50ed25a15fffe16f97a96585b732fb2200d58119c7b` |
| `SemanticPromptTransfer_v0.26.3_COLAB_LAUNCHER.ipynb` | 45,864 | `85b665f5e2c1b511683921eef47864d4c6e338557dc562dda7ffb9cc2d05c89b` |

## 실제 Colab 확인 필요

개발 환경에는 A100이 없으므로 Gemma 4 MoE의 실제 가중치 적재와 첫 생성은
Colab에서 확인한다. 성공 기준은 Ninja 사전검사, `vLLM ready`, 업로드 자료 100%,
A~E 스트림 완료, 근거 팝업, Word 다운로드, 후속 대화 스트리밍이다.
