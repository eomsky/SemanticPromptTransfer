# semantic-prompt-transfer 0.21.0

SemanticPromptTransfer v0.21 aligns the operational package with the approved minimal HTML interface. It adds file-level progress responses, verified file-and-vector deletion, optional HTTP routes, and a replaceable CPU text-generation module while preserving the v0.20 evidence contract and L0 RAG baseline.

## Evidence contract

For each of the five fixed review items, evidence is assembled in this order:

1. `TIER_1`: item-specific facts from the standardized credit-report Excel
2. `TIER_2`: common facts from the same credit report
3. `TIER_3`: retrieved evidence from other attachments

Approved few-shot examples are not evidence. They guide structure and tone only. The validator rejects numbers and forbidden identifiers that appear only in few-shot examples.

## Fixed review items

- A. 재무제표 주요계정(현황 및 향후전망)
- B. 수익성(현황 및 향후전망)
- C. 재무안정성 및 자산의 질(현황 및 향후전망)
- D. 현금흐름 및 채무상환능력(현황 및 향후전망)
- E. 주요 매출처 및 매출비중 변동 추이

Each item has a versioned deterministic query profile. The profile adds relevant accounts, ratios, periods, risk factors, and outlook concepts to the visible item title.

## Credit-report parsing

The credit report is a versioned structured input, not an ordinary attachment. Define the approved workbook mapping:

```python
from semantic_prompt_transfer import CreditReportParser, CreditReportTemplate, DocumentScope

template = CreditReportTemplate.from_json("credit_report_template.json")
scope = DocumentScope(
    tenant_id="bank-a",
    case_id="review-001",
    document_id="credit-report",
    source_filename="credit_report.xlsx",
    document_kind="credit_report",
)
parsed = CreditReportParser().parse("credit_report.xlsx", template, scope)
```

Every fact retains workbook, sheet, cell, formula, unit, period, template, and source-hash provenance.

## Loan-type and industry few-shot selection

```python
from semantic_prompt_transfer import FewShotRegistry, FewShotSelector, ReviewItem

selector = FewShotSelector(FewShotRegistry.from_json("few_shots.json"), max_examples=3)
examples = selector.select(
    ReviewItem.PROFITABILITY,
    loan_type="운전자금",
    industry_code="C29",
    situation_tags=("매출성장",),
)
```

Selection requires the same review item, then ranks exact loan type, industry code or prefix, and situation tags. Only `APPROVED` examples are eligible.

## Multi-document indexing and deletion

```python
from semantic_prompt_transfer import DocumentScope, OfflineIndexBuilder, PipelineConfig

builder = OfflineIndexBuilder(
    PipelineConfig.for_index_build(
        model_dir="models/multilingual-e5-small-onnx-int8",
        index_path="indexes/operational.npz",
        representation_level=0,
        index_write_strategy="UPSERT",
    )
)

builder.build("attachment_a_MASTER.json", DocumentScope("bank-a", "review-001", "attachment-a"))
builder.build("attachment_b_MASTER.json", DocumentScope("bank-a", "review-001", "attachment-b"))
builder.delete(DocumentScope("bank-a", "review-001", "attachment-a"))
```

`global_chunk_id` is derived from tenant, case, document, local chunk, and representation level. Deletion is document-scoped. `DocumentLifecycleService` first blocks the document with `DELETING`, deletes its scoped vectors, verifies that no vector remains, deletes the original and derived artifacts through `DocumentArtifactStore`, and only then records `DELETED`.

## Approved HTML interface contract

The bundled `examples/operational/credit_review_upload_demo.html` is the minimal interface reference. `OperationalApplicationService` returns the exact file-list fields needed by the popup:

- filename and file type
- size in bytes
- progress percentage
- progress stage (`파일적재`, `파일검증`, `파일해석`, `벡터임베딩`, `완료`)
- document-scoped delete availability

The optional FastAPI adapter exposes:

```text
POST   /api/v1/cases/{case_id}/credit-report
POST   /api/v1/cases/{case_id}/attachments
GET    /api/v1/cases/{case_id}/documents
DELETE /api/v1/cases/{case_id}/documents/{document_id}
POST   /api/v1/cases/{case_id}/review-jobs
GET    /api/v1/review-jobs/{job_id}
GET    /api/v1/review-jobs/{job_id}/opinion.docx
```

Install the HTTP adapter only when needed:

```bash
pip install "semantic-prompt-transfer[web]"
```

## Five-item generation

`ReviewGenerationOrchestrator` performs:

```text
PRECHECK -> CREDIT_REPORT_LOAD -> ATTACHMENT_RETRIEVAL
-> ITEM A-E GENERATION -> VALIDATION -> DOCX_RENDER -> COMPLETE
```

It accepts the provider-neutral `TextGenerator`, emits progress events, validates every item, and creates a Word document with evidence trace information.

## Replaceable CPU text generation

The initial CPU implementation uses `Qwen/Qwen2.5-0.5B-Instruct` through a lazy Transformers adapter. The model is not bundled in the wheel. It is loaded once, kept on CPU, and constrained to short deterministic generation. If the model is unavailable or its draft fails the citation/numeric grounding precheck, `EvidenceTemplateGenerator` immediately produces a conservative evidence-only draft.

```bash
pip install "semantic-prompt-transfer[llm-cpu]"
```

```python
from semantic_prompt_transfer import CpuGenerationConfig, default_cpu_generator

generator = default_cpu_generator(
    CpuGenerationConfig(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        max_new_tokens=256,
        num_threads=4,
    )
)
```

Later replacement requires only another object implementing `generate(messages) -> str`; retrieval, validation, progress, and DOCX code do not change.

## Storage backends

- NPZ: reproducible exact-search reference backend
- `InMemoryVectorStore`: adapter conformance and tests
- `ChromaVectorStore`: optional persistent document-scoped UPSERT/delete adapter

Install Chroma support only when needed:

```bash
pip install "semantic-prompt-transfer[chroma]"
```

## CLI

```bash
spt-rag index --master ATTACHMENT_MASTER.json --index operational.npz --model-dir MODEL \
  --tenant-id bank-a --case-id review-001 --document-id attachment-a \
  --source-filename attachment-a.pdf --write-strategy UPSERT

spt-rag delete --index operational.npz --model-dir MODEL \
  --tenant-id bank-a --case-id review-001 --document-id attachment-a

spt-rag credit-report-parse --workbook credit.xlsx --template mapping.json \
  --tenant-id bank-a --case-id review-001 --document-id credit-report --output facts.json

spt-rag fewshot-select --registry few_shots.json --review-item B \
  --loan-type 운전자금 --industry-code C29 --situation-tag 매출성장
```

## Operational boundary

The package now includes the HTML-facing application contract, an optional FastAPI adapter, safe local artifact storage, and replaceable generation adapters. Authentication, enterprise object storage, malware scanning, distributed task queues, production LLM hosting, and bank-specific Excel/Word templates remain deployment integrations.

The bundled operational example contains no real customer data.
