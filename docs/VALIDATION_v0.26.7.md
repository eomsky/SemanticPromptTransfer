# SemanticPromptTransfer v0.26.7 검증 기록

검증일: 2026-09-01

## 핵심 변경
- 실제 LLMVerificationAgent를 Colab 심사의견 lane에 ENFORCE로 활성화
- 생성/검증 Agent는 동일 Gemma 4 vLLM 엔진을 공유하되 프롬프트와 역할은 완전 분리
- 명백한 evidence-bound 사실 오류만 FAIL 허용
- WARN/INSUFFICIENT_EVIDENCE는 생성 문구를 변경하지 않음
- FAIL도 problem_span/claim 범위만 PatchGuard로 최소 수정
- 자유대화에는 검증 LLM·few-shot·repair 미적용
- v0.26.6의 Evidence Trace, 근거 병합/다양성, Word 캡처 부록, context-first capture, 새 FEW_SHOT_1 유지

## 자동 검증
- 전체 pytest 통과
- compileall 통과
- Colab 전체 code cell compile 통과
- `verification_mode="ENFORCE"` 확인
- 새 FEW_SHOT_1 식별값 포함 / 구 FEW_SHOT_1 식별값 미포함 확인

## 산출물
| 산출물 | 크기 | SHA-256 |
|---|---:|---|
| semantic_prompt_transfer-0.26.7-py3-none-any.whl | 198,356 | `5ee003884a1819f16e61b059ca7339640f692c7729dfe509c3aa100c07a02df6` |
| SemanticPromptTransfer_v0.26.7_COLAB_LAUNCHER.ipynb | 53,745 | `dca345e7a0241058738278e77e5ee01200e3159471527a7db09229248be939bb` |
| SemanticPromptTransfer_v0.26.7_COLAB_ASSETS.json | 997 | `76e2c135c821a1c1b02fe5ea33509156f0b591f840d3071255f673db5df7504b` |
