# SemanticPromptTransfer v0.24 Colab 운영

## 운영 파일

`notebooks/SemanticPromptTransfer_v0.24_COLAB_LAUNCHER.ipynb`

위에서부터 모든 셀을 실행한다. FEW SHOT 1~3 셀에는 익명화된 실제 우수
심사역 사례를 입력하며, 각 사례의 A~E 답안은 모든 여신유형·업종에 문체와
분석 구조로만 적용된다.

## Colab Secrets

- `NGROK_AUTHTOKEN`: 필수. 별도 키 입력창 없이 Secrets에서만 읽는다.
- `HF_TOKEN`: 필수. Gemma 모델 접근 토큰이다.
- `SPT_GATE_PASSWORD`: 필수. 외부 화면의 HTTP Basic Auth 비밀번호다.
- `SPT_GATE_USER`: 선택. 기본값은 `spt-poc`이다.

## Drive 배치

```text
MyDrive/SemanticPromptTransfer/
├─ versions/v0.24/semantic_prompt_transfer-0.24.0-py3-none-any.whl
└─ runtime-assets/v0.24/
   ├─ SemanticPromptTransfer_v0.24_COLAB_ASSETS.json
   └─ SemanticPromptTransfer_v0.24_COLAB_LAUNCHER.ipynb
```

매니페스트는 검증된 v0.22 임베딩 모델·토크나이저 자산을 재사용한다. 사용자
업로드, 추출 결과, 벡터, 사용자 정보 및 Word 결과는 `/content`에만 존재하며
Colab 런타임 종료 시 삭제된다.

## 실행 동작

1. 첫 실행 셀이 Drive 자산의 크기와 SHA-256을 검증하고 wheel을 설치한다.
2. 마지막 실행 셀이 ngrok 엔드포인트를 먼저 예약한다. 기존 세션 충돌 시
   62.5GB Gemma 다운로드 전에 중단하고 기존 Colab 런타임 종료를 안내한다.
3. Gemma 4 31B를 4-bit로 로드한다. 이미 같은 런타임에 로드된 모델은 재사용한다.
4. 업로드 시에는 파일만 저장한다. `심사의견 생성` 클릭 후 파싱·임베딩을 시작한다.
5. 하나의 Gemma 생성 모델이 A→E를 순차 실행하며 항목당 최대 1,200 tokens를 쓴다.
6. 진행 단계와 생성 텍스트를 NDJSON 스트림으로 표시한다.
7. 근거 연결 문구를 누르면 PDF bbox 또는 Excel 셀 범위를 강조한 PNG 팝업이 열린다.
8. 다섯 항목과 기계적 근거 검증이 완료되면 Word 다운로드가 활성화된다.

별도 검증 LLM과 Annotated PDF 등 추가 산출물은 v0.24 운영 경로에서 사용하지 않는다.
