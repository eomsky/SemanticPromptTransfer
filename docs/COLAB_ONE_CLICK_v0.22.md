# SemanticPromptTransfer v0.22 단일 실행 Colab

## 목적

`SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb`의 코드 셀 하나를 실행해
Google Drive의 승인된 wheel과 E5 모델을 검증·복사하고, API와 HTML을 같은
ngrok URL로 기동한다.

## Drive 배치

```text
MyDrive/SemanticPromptTransfer/
├─ versions/v0.22/
│  └─ semantic_prompt_transfer-0.22.0-py3-none-any.whl
└─ runtime-assets/v0.22/
   ├─ SemanticPromptTransfer_v0.22_COLAB_ASSETS.json
   ├─ SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb
   └─ model/onnx/
      ├─ model_qint8_avx512_vnni.onnx.gz
      └─ tokenizer.json
```

노트북은 Drive의 파일을 직접 실행하지 않는다. SHA-256과 크기를 확인하면서
`/content/spt_bootstrap_v022`로 복사하고, 압축 모델을 해제한 뒤 원본 모델의
SHA-256을 다시 확인해 패키지를 설치하고 모델을 로드한다.
회원정보, 업로드 원본, 파생파일, 벡터, 심사의견은 `/content/spt_poc_runtime`에만
기록한다.

## 최초 1회 설정

Colab의 열쇠 아이콘에서 다음 Secrets를 만들고 Notebook access를 허용한다.

- `NGROK_AUTHTOKEN`: ngrok 계정의 Authtoken
- `SPT_GATE_PASSWORD`: 외부 URL 공통 접속 비밀번호, 8자 이상
- `SPT_GATE_USER`: 선택, 기본값 `spt-poc`

별도 LLM Colab을 연결할 때만 다음을 추가한다.

- `SPT_LLM_BASE_URL`
- `SPT_LLM_MODEL`
- `SPT_LLM_API_KEY`

Secrets가 없으면 실행 중 숨김 입력창이 표시된다.

## 실행과 종료

1. Drive에서 노트북을 Google Colab으로 연다.
2. 코드 셀 하나를 실행한다.
3. Drive 마운트 권한을 승인한다.
4. 출력된 `심사 화면 열기` 링크를 사용한다.

노트북을 재실행하면 이전 서버·터널·임시 심사자료를 먼저 종료하고 새로 시작한다.
Colab 런타임을 종료하면 `/content`의 사용자 자료와 벡터가 폐기된다.

## 제한

이 구성은 시간 제한형 POC 운영 개시용이다. 사번 기반 로그인, 단일 Colab 프로세스,
임시 SQLite·NPZ, ngrok 공통 게이트는 정식 운영 인증·저장·작업큐를 대신하지 않는다.
실제 신용조사서 양식과 심사의견 Word 양식도 별도 확정해야 한다.
