# STX엔진 축약 예제

이 예제는 v0.18 검증에 사용한 STX엔진 사업보고서에서 검색 구조와 사실값 일부만 축약한 L0 샘플이다. 전체 PDF, 전체 MASTER, 학습 모델 및 임베딩은 패키지에 포함하지 않는다.

`sample_chunks.json`은 패키지 출력 형식 검토용이고, `queries.json`은 다섯 개 기본 여신심사 쿼리다. 실제 운영 검증은 외부의 전체 MASTER로 `spt-rag index`를 실행해야 한다.

운영 예시:

```bash
spt-rag index --master SemanticPromptTransfer_v0.17_MASTER.json \
  --index stx_l0_index.npz --model-dir multilingual-e5-small-onnx-int8 \
  --tenant-id example --case-id stx-2025 --document-id stx-business-report

spt-rag retrieve --index stx_l0_index.npz \
  --model-dir multilingual-e5-small-onnx-int8 \
  --tenant-id example --case-id stx-2025 \
  --query "당기말 현금성자산과 단기차입금을 비교하라"
```
