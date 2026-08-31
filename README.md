# semantic-prompt-transfer 0.20.0

SemanticPromptTransfer v0.20 extends the v0.19 L0 RAG baseline with an operational credit-review layer. It keeps provider-neutral retrieval while enforcing source priority, case isolation, document deletion, item-specific query profiles, and style-only few-shot selection by loan type and industry.

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

`global_chunk_id` is derived from tenant, case, document, local chunk, and representation level. Deletion is document-scoped. `DocumentLifecycleService` coordinates registry state, vector deletion, derived assets, and original-file removal.

## Five-item generation

`ReviewGenerationOrchestrator` performs:

```text
PRECHECK -> CREDIT_REPORT_LOAD -> ATTACHMENT_RETRIEVAL
-> ITEM A-E GENERATION -> VALIDATION -> DOCX_RENDER -> COMPLETE
```

It accepts a provider-neutral `LLMClient`, emits progress events, validates every item, and creates a Word document with evidence trace information. The package does not ship or call a specific LLM.

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

The package exposes the domain and execution contracts. Authentication, HTML upload endpoints, object storage, malware scanning, distributed task queues, LLM hosting, and bank-specific Excel/Word templates remain deployment integrations.

The bundled operational example contains no real customer data.
