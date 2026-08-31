# SemanticPromptTransfer v0.26.3 Colab 운영

## 준비

- Colab 런타임: NVIDIA A100 80GB 1장
- Colab Secrets: `NGROK_AUTHTOKEN`, `HF_TOKEN`
- 별도 로그인, `SPT_GATE_PASSWORD`, ngrok Basic Auth는 사용하지 않음
- `HF_TOKEN`은 `google/gemma-4-26B-A4B-it` 접근 권한을 가져야 함

Google Drive 자산 경로는 다음과 같다.

```text
MyDrive/SemanticPromptTransfer/
├── runtime-assets/v0.26.3/SemanticPromptTransfer_v0.26.3_COLAB_ASSETS.json
└── versions/v0.26.3/semantic_prompt_transfer-0.26.3-py3-none-any.whl
```

## 실행

1. 새 Colab 런타임에서 `SemanticPromptTransfer_v0.26.3_COLAB_LAUNCHER.ipynb`를 연다.
2. A100 GPU를 선택하고 모든 셀을 위에서 아래로 실행한다.
3. `vLLM build tool ready · ninja`를 확인한다.
4. `E5 dependency stack ready · vLLM environment isolated`를 확인한다.
5. `loading native vLLM`과 `vLLM ready`를 확인한다.
6. 출력된 **심사 화면 바로 열기** 링크를 연다.

## v0.26.3 엔진 설정

- 모델: `google/gemma-4-26B-A4B-it` MoE
- 구현: `--model-impl vllm`로 native vLLM 강제
- 정밀도: BF16
- GPU 메모리 사용률: 0.90
- 최대 컨텍스트: 16,384토큰
- 최대 동시 시퀀스: 4
- E5와 vLLM Python 환경 분리
- vLLM과 Ninja를 동일한 격리환경에 설치
- vLLM 하위 프로세스 `PATH`에 격리환경 `bin`을 우선 적용
- 텍스트 전용 멀티모달 제한, prefix cache, 비동기 스케줄링 사용

Ninja 버전 검사는 모델 가중치 적재 전에 실행된다. 이 검사를 통과하면 vLLM의
커널 워밍업 하위 프로세스도 `/content/spt_bootstrap_v0263/vllm-env/bin/ninja`를
검색할 수 있다.

엔진이 실패하면 예외에 `/content/spt_bootstrap_v0263/vllm.log` 경로와
`EngineCore failed to start` 직전부터의 최초 원인 스택이 함께 표시된다.

## 처리 및 운영 경계

업로드 시에는 파일 저장만 하고 **심사의견 생성**을 누른 뒤 파싱·임베딩·검색·
A→E 순차 생성을 시작한다. 전체 스트림이 끝난 후에만 후속 대화 입력창을 표시하며,
완료된 심사의견과 누적 질문·답변을 같은 심사건 문맥으로 유지한다.

이 구성은 익명 단일-Colab POC다. 실제 은행 운영 전에는 SSO/RBAC, 악성파일 검사,
감사로그, 지속형 저장소, 요청 제한과 장애 복구를 추가해야 한다.
