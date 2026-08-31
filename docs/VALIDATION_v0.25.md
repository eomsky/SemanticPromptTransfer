# SemanticPromptTransfer v0.25 검증 기록

검증일: 2026-08-31

## 완료한 검증

- Python 단위·통합 테스트 35개 통과
- v0.25 노트북의 모든 Python 코드 셀 컴파일 통과
- 기본 FEW SHOT 3건 × A~E가 모두 비어 있지 않음을 확인
- MoE 모델 ID, vLLM 4개 시퀀스, GPU E5, 익명 POC 설정을 노트북 계약으로 확인
- 신용조사서 없이 첨부자료만 업로드한 A~E 생성 경로 통과
- OpenAI-compatible SSE 스트리밍 수신 통과
- PDF 좌표 캡처 및 XLSX 원문형 캡처 PNG 생성 통과
- 실제 제공된 `양식파일_ABC기업_v1.0.xlsx`의 `7. 종합의견` 범위를 렌더링해
  한글, 숫자, 행·열 구조와 강조 표시를 육안 확인
- v0.25 wheel 내부에 수정된 HTML, 근거 캡처, 첨부자료 처리 코드와 버전
  `0.25.0`이 포함됨을 확인

## 산출물 무결성

| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| `semantic_prompt_transfer-0.25.0-py3-none-any.whl` | 171,834 | `01f76379388d237e2e9d577c57555e9939fee684ffebe6861d101de55dcaeaba` |
| `SemanticPromptTransfer_v0.25_COLAB_LAUNCHER.ipynb` | 42,645 | `2d7c8873292aca94de614dd2854cd3f5c6e2351a4fee2d5ae4434dd436012437` |
| `few_shot_defaults_v1.json` | 18,439 | `846f2b7a301491948223b964d9926176a0ee49a19f05a9f2f9c427fdc234e01d` |

## Colab에서 확인할 항목

이 개발 환경에는 A100이 없으므로 아래 항목은 실제 Colab 실행 검증이 필요하다.

1. Gemma 4 MoE의 Hugging Face 권한과 CUDA 12.9 prerelease vLLM 로드 성공
2. A100 80 GB에서 GPU 메모리 사용률 0.88과 E5 동시 상주
3. 대표 사업보고서 기준 임베딩 총시간과 70~92% 배치 진행 표시
4. 동시 사용자 1~4명의 첫 토큰 지연과 처리량
5. 실제 ngrok 계정에서 새 무작위 URL이 로그인 없이 바로 열리는지

운영 판정 기준은 모델 로드 완료, 업로드 자료별 100%, A~E 스트리밍 완료,
근거 팝업 정상 표시, Word 다운로드 성공이다.
