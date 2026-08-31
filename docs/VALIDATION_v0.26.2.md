# SemanticPromptTransfer v0.26.2 검증 기록

검증일: 2026-08-31

## 검증 항목

- Python 단위·통합 테스트 41개 통과
- v0.26.2 노트북의 모든 Python 코드 셀 컴파일 통과
- vLLM 전용 `uv` 환경과 현재 Colab E5 환경의 격리 유지
- Gemma 4 MoE native 구현 강제 플래그 확인
- A100 BF16 GPU 메모리 사용률 0.90 및 16,384토큰 설정 확인
- 엔진 최초 원인 로그와 전체 로그 경로 노출 계약 확인
- FEW SHOT 3건 × A~E, 익명 사용자 분리, 스트리밍, 자동 이어쓰기,
  후속 대화 문맥, 근거 캡처 및 Word 다운로드의 기존 계약 유지
- v0.26.1과 v0.26.2의 wheel·manifest·Drive 경로 독립성 확인

## 산출물 무결성

| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| `semantic_prompt_transfer-0.26.2-py3-none-any.whl` | 176,206 | `f481ab0bf758cfd24d9de54fb6d1490da45796b8ea92cdf7b19ba48b267d0832` |
| `SemanticPromptTransfer_v0.26.2_COLAB_LAUNCHER.ipynb` | 44,936 | `8737c25c2ca10446baba80c45098d0b425aef255df98544fdfc05576f56b71e6` |

## 실제 Colab 확인 필요

개발 환경에는 A100이 없으므로 Gemma 4 MoE의 실제 가중치 적재와 첫 생성은
Colab에서 확인한다. 성공 기준은 `vLLM ready`, 업로드 자료 100%, A~E 스트림
완료, 근거 팝업, Word 다운로드, 후속 대화 스트리밍이다.
