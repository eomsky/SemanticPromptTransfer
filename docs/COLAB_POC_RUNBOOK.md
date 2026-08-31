# Colab POC 실행 안내

단일 실행 경로는 `COLAB_ONE_CLICK_v0.22.md`와
`notebooks/SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb`를 기준으로 한다.
아래 내용은 API와 HTML을 별도로 구성하는 수동 실행 경로다.

## 1. 전제

- 애플리케이션 Colab 세션은 테스트 시간에만 유지한다.
- 영속 Google Drive는 mount하지 않는다.
- E5 ONNX 모델은 Colab `/content` 아래에 준비한다.
- HTML과 API를 다른 URL로 제공하면 API의 `SPT_ALLOWED_ORIGINS`에 HTML origin을 넣는다.
- 실제 터널 URL, 내부 LLM URL, API key는 소스에 저장하지 않는다.

## 2. 설치와 서버 시작

```bash
pip install "semantic-prompt-transfer[poc]"

export SPT_MODEL_DIR=/content/models/multilingual-e5-small-onnx-int8
export SPT_POC_ROOT=/content/spt_poc_runtime
export SPT_ALLOWED_ORIGINS=https://HTML-호스트.example

uvicorn semantic_prompt_transfer.poc_server:app \
  --host 0.0.0.0 --port 8000
```

서버를 임시 HTTPS URL로 노출한 뒤 다음과 같이 HTML을 연다.

```text
credit_review_upload_demo.html?mode=api&api_base=https://API-터널.example
```

## 3. 선택형 LLM Colab

별도 Colab에서 OpenAI 호환 `POST /v1/chat/completions` endpoint를 열었다면
애플리케이션 Colab 시작 전에 설정한다.

```bash
export SPT_LLM_BASE_URL=https://LLM-터널.example/v1
export SPT_LLM_MODEL=배포한-모델명
export SPT_LLM_API_KEY=필요한-경우의-토큰
```

설정이 없거나 호출에 실패하면 근거형 CPU 폴백이 동작한다.

## 4. 사용자 흐름

1. 부서명·이름·사번으로 회원가입한다.
2. 아이디와 비밀번호에 같은 사번을 넣어 로그인한다.
3. `양식 다운로드`로 XLSX를 받고 작성한다.
4. 신용조사서 1개와 기타 첨부자료 여러 개를 올린다.
5. 파일명 옆 단계가 `완료`인지 확인한다.
6. 불필요한 파일은 `×`로 제거한다.
7. 심사의견 버튼으로 생성하고 100% 후 DOCX를 내려받는다.

## 5. 종료

정상 프로세스 종료 시 `poc_server`가 runtime bundle을 닫고 `/content/spt_poc_runtime`
전체를 제거한다. 강제 종료된 세션은 Colab 자체가 임시 디스크를 폐기한다. 중요한
테스트 결과는 사용자가 DOCX로 내려받아야 하며 애플리케이션은 Google Drive에
자동 저장하지 않는다.

## 6. 실제 양식 수령 후 교체

다음 두 파일을 같은 릴리스에서 함께 변경하고 테스트한다.

- `credit_report_sample_template.xlsx`: 실제 다운로드 양식
- `credit_report_template.json`: sheet/cell/기간/단위/심사항목 매핑

HTML의 버튼과 API 경로는 유지한다.
