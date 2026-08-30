# semantic-prompt-transfer 0.19.0

SemanticPromptTransfer Cells 4–7을 설치 가능한 Python 패키지로 분리한 운영 RAG 기준 구현입니다. Cells 1–3은 동결되어 있으며, 외부에서 생성·검증된 MASTER JSON을 입력으로만 사용합니다.

## 기본 계약

- 표현 수준: `0` (`PLAIN`, 현재 의미 선형화 표현)
- MASTER 모드: `LOAD` 전용
- 개발 기본 인덱스 모드: `MEMORY`
- 운영 인코더: CPU multilingual E5 ONNX INT8
- 검색: 범위 선필터 + exact cosine + BM25 + RRF
- 출력: 근거 ID와 출처를 포함한 provider-neutral prompt package

L1과 L2는 실험 옵션이며 `representation_level=1|2`를 명시해야만 활성화됩니다. 운영 검색에는 `tenant_id`와 `case_id` 범위가 필수입니다.

## 운영 수명주기

### 1. 오프라인 인덱스 빌드 또는 문서 UPSERT

```python
from semantic_prompt_transfer import (
    DocumentScope,
    OfflineIndexBuilder,
    PipelineConfig,
)

config = PipelineConfig.for_index_build(
    model_dir="models/multilingual-e5-small-onnx-int8",
    index_path="indexes/credit_case.npz",
    representation_level=0,
    index_write_strategy="UPSERT",
)
builder = OfflineIndexBuilder(config)
stats = builder.build(
    "SemanticPromptTransfer_v0.17_MASTER.json",
    DocumentScope(
        tenant_id="bank-a",
        case_id="review-2026-001",
        document_id="stx-engine-2025-report",
        financial_scope="consolidated",
        source_version="v0.17",
    ),
)
```

`UPSERT`는 동일 `(tenant_id, case_id, document_id)` 문서만 교체하고 다른 문서는 유지합니다. 쓰기는 프로세스 잠금과 같은 파일시스템 안의 원자적 교체로 보호됩니다.

### 2. 온라인 서비스에서 인덱스 1회 로드

```python
from semantic_prompt_transfer import OnlineRAGService, PipelineConfig

service = OnlineRAGService(
    PipelineConfig.for_serving(
        model_dir="models/multilingual-e5-small-onnx-int8",
        index_path="indexes/credit_case.npz",
        representation_level=0,
        top_k=5,
    )
)
service.start()
result = service.search(
    "당기말 단기차입금과 현금성자산을 비교하라",
    filters={"tenant_id": "bank-a", "case_id": "review-2026-001"},
)
```

온라인 프로세스는 요청마다 MASTER를 다시 읽거나 문서를 재임베딩하지 않습니다. 모델, 벡터 행렬과 BM25를 시작 시 한 번 로드해 재사용합니다.

## CLI

```bash
spt-rag index --master MASTER.json --index case.npz --model-dir MODEL_DIR \
  --tenant-id bank-a --case-id review-2026-001 --document-id annual-report \
  --write-strategy UPSERT

spt-rag retrieve --index case.npz --model-dir MODEL_DIR \
  --tenant-id bank-a --case-id review-2026-001 \
  --query "특수관계자 채권·채무 잔액을 설명하라"
```

## STX엔진 예제 자료

`semantic_prompt_transfer.examples.stx_engine`에는 다음 축약 자료만 포함됩니다.

- L0 형식의 짧은 예제 청크 5개
- 기본 여신심사 질의 5개
- 전체 STX 회귀시험의 기대 Top-1 물리표 ID

이는 설치·형식·API 확인용입니다. 전체 STX엔진 PDF, MASTER, 저장 모델과 운영 인덱스는 용량과 권리·보안 경계를 위해 wheel에 포함하지 않습니다.

현재 NPZ 정확검색 백엔드는 소규모 심사건과 재현 기준용입니다. 다수 사용자·대규모 문서 운영에서는 같은 청크·필터·인코더 계약 뒤에 별도 Vector DB 백엔드를 추가하십시오.
