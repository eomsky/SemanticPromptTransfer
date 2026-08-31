# SemanticPromptTransfer v0.25 Colab 운영

## 준비

- Colab 런타임: NVIDIA A100 80 GB 1장
- Colab Secrets: `NGROK_AUTHTOKEN`, `HF_TOKEN`
- 별도 `SPT_GATE_PASSWORD`, 회원가입, 애플리케이션 로그인은 사용하지 않음
- Hugging Face 토큰은 `google/gemma-4-26B-A4B-it` 접근 권한을 가져야 함

Google Drive에는 다음 두 파일이 있어야 한다.

```text
MyDrive/SemanticPromptTransfer/
├── runtime-assets/v0.25/SemanticPromptTransfer_v0.25_COLAB_ASSETS.json
└── versions/v0.25/semantic_prompt_transfer-0.25.0-py3-none-any.whl
```

매니페스트는 wheel의 크기와 SHA-256을 검사한다. v0.25는 GPU E5를
Hugging Face에서 로드하므로 이전 ONNX 압축 자산은 필요하지 않다.

## 실행

1. `SemanticPromptTransfer_v0.25_COLAB_LAUNCHER.ipynb`를 Colab에서 연다.
2. 런타임 유형을 A100 GPU로 선택한다.
3. 설정 셀, FEW SHOT 1~3 셀, 운영 개시 셀을 위에서 아래로 실행한다.
4. `vLLM ready`와 `ready: package=0.25.0` 로그를 확인한다.
5. 출력된 **심사 화면 바로 열기** 링크를 연다.

세 FEW SHOT 셀에는 승인된 기본 사례가 입력되어 있다. 수정하지 않으면
기본값 3건 × A~E, 총 15개 스타일 예시가 일괄 적용된다.

## 처리 방식

- 업로드 시에는 파일 저장만 수행한다.
- **심사의견 생성**을 누르면 파싱, 청크 병합, GPU 배치 임베딩, 검색,
  A→B→C→D→E 순차 생성을 시작한다.
- 첨부자료 임베딩은 `multilingual-e5-small`, 배치 128, 최대 길이 384를
  사용한다. 70% 이후에는 실제 배치 완료량이 진행률과 상태 문구에 표시된다.
- 동일 파일·동일 범위의 임베딩은 런타임 메모리 캐시를 재사용한다.
- 신용조사서가 없으면 PDF·DOCX·XLSX·TXT·MD 첨부자료만으로 RAG를
  구성한다.
- 신용조사서가 있더라도 첨부자료에 별도 컨텍스트 예산을 예약해
  사업보고서 근거가 입력 길이에서 밀려나지 않게 한다.
- Annotated PDF 등 추가 산출물은 생성하지 않는다.
- A~E 전체 스트림이 완료된 이후에만 후속 대화 입력창이 나타난다.
- 후속 대화는 few-shot과 A~E 생성 형식을 사용하지 않으며, 완료된 심사의견,
  업로드 근거자료와 같은 심사건의 누적 질문·답변을 문맥으로 사용한다.

## 생성 모델과 동시 접속

- 모델: `google/gemma-4-26B-A4B-it` MoE
- 서버: vLLM OpenAI-compatible streaming endpoint
- 설치: 공식 Gemma 4 CUDA 12.9 prerelease wheel 경로
- 정밀도: BF16
- GPU 메모리 사용률: 0.88
- 최대 컨텍스트: 16,384토큰
- 동시 시퀀스: 4
- 요청별 출력 상한: 1,400토큰
- 길이 제한 종료 시 최대 2회 자동 이어쓰기 후 완결성 확인
- 텍스트 전용 멀티모달 제한 및 비동기 스케줄링 사용

한 사용자의 A~E는 순서대로 생성한다. 서로 다른 사용자의 요청은 vLLM이
continuous batching하며, 4개를 넘는 요청은 자원이 생길 때까지 대기한다.
GPU 임베딩은 메모리 충돌을 방지하기 위해 한 번에 하나씩 실행한다.
후속 대화의 질문과 답변은 Colab 런타임 메모리에 심사건별로 누적된다.

브라우저가 생성한 128비트 임의 ID를 `localStorage`에 보관하고 이 값을
tenant/case 범위로 사용한다. 접속 IP를 ID로 쓰지 않으므로 동일 NAT·프록시
사용자가 같은 자료 공간을 공유하지 않는다. 이 방식은 POC 격리일 뿐 인증이
아니며, URL을 아는 사람은 화면에 접근할 수 있다.

## 근거 표시

모델 출력의 `CR_…`, `ATT_…`는 서버 내부 근거 키로 유지하지만 화면과 Word
본문에서는 숨긴다. 생성 완료 후 문장 또는 **신용조사서 근거**/**첨부자료
근거** 버튼을 누르면 원문의 해당 위치를 큰 팝업으로 연다.

- PDF: 원문 페이지의 해당 좌표를 2배 해상도로 캡처하고 강조
- XLSX: 실제 행 높이·열 너비·셀 서식을 반영한 시트형 캡처와 선택 범위 강조
- 팝업: 화면 최대 96% × 94%, 50~300% 확대/축소, 스크롤 지원

## 운영 제한

- 임베딩 30초 이내는 일반적인 POC 문서량에 대한 목표값이며, PDF 페이지 수,
  OCR 필요 여부, 동시 요청 수에 따라 초과할 수 있다.
- 이미지형 스캔 PDF에는 별도 OCR이 필요하다. OCR은 기본 경로에서 제외한다.
- Colab 종료 시 업로드, 벡터, 브라우저 범위에 대응하는 서버 상태와 생성 파일이
  삭제된다.
- 실제 운영 전에는 SSO/RBAC, 악성파일 검사, 요청 제한, 감사로그와 지속형
  저장소를 추가해야 한다.
