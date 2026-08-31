# semantic-prompt-transfer 0.26.4

SemanticPromptTransfer v0.26.4 is a time-boxed Colab POC implementation for the
credit-review workflow. The browser remains a thin HTML client. Uploads,
extracted facts, L0 vectors, progress, and generated Word files live only in
the Colab runtime and are removed when that runtime closes. Google Drive is
mounted only by the launcher to stage approved runtime assets.

For conversation-to-conversation continuity, current decisions, verified Drive
locations, version history, and remaining work are consolidated in
[`docs/PROJECT_HANDOFF_v0.22.md`](docs/PROJECT_HANDOFF_v0.22.md).

## POC flow

1. The ngrok URL opens the upload screen directly without application login or
   ngrok Basic Auth. A random browser-local scope separates concurrent users.
2. The user may download the credit-report Excel form, fill it, and upload one
   workbook. The credit report is optional when attachments are available.
3. The user uploads multiple PDF, DOCX, XLSX, TXT, or Markdown attachments.
   Upload only stores each file; parsing and embedding start when generation is requested.
4. Filenames appear inline. The `×` button deletes the server copy, derived
   files, and all vectors for that document after a zero-count verification.
5. One Gemma 4 MoE vLLM server runs A→B→C→D→E sequentially per job while
   continuously batching up to four concurrent requests. Tokens and the current
   stage appear live in the read-only output panel.
6. Clicking a cited claim or its source-type button opens a large, zoomable,
   highlighted source capture for the exact
   PDF block or Excel cell range. The completed opinion is downloaded as DOCX.
7. Only after the full A–E stream completes, a follow-up input appears. Later
   turns omit few shots and the A–E schema, retain the completed opinion and
   accumulated question/answer history, and stream a free-form response.

## Source priority and few shots

Evidence is assembled separately for each review item in this fixed order:

1. `TIER_1`: item-specific cells from the credit-report workbook
2. `TIER_2`: common cells from the same workbook
3. `TIER_3`: retrieved L0 evidence from attachments

Each tier has a separate prompt budget, preventing a large credit workbook from
crowding business-report evidence out of the context. If no credit report is
uploaded, retrieval and generation proceed from `TIER_3` alone and the output
states that limitation.

Three approved few-shot cases are expanded into A–E examples and applied to all
loan and industry types. They control structure and tone only; they are never
treated as current-case evidence.

## Multiple PDFs in one logical vector database

`ShardedNpzVectorStore` presents one exact-search database while storing one
atomic NPZ shard per uploaded document. Search always requires `tenant_id` and
`case_id`. Replace and delete include `document_id`, so changing one PDF does not
rewrite or remove another PDF. The backend implements the same scoped contract
that a production Vector DB adapter must preserve.

## Colab start

The operating notebook is
`notebooks/SemanticPromptTransfer_v0.26.4_COLAB_LAUNCHER.ipynb`. It provides three
pre-populated editable few-shot cells, mounts the owner's Drive, verifies the
approved wheel, starts `google/gemma-4-26B-A4B-it` with vLLM on one A100 80 GB,
loads a batched GPU E5 encoder, serves the packaged HTML and API on one port,
and creates an ngrok URL without a second password prompt. The ngrok endpoint
is reserved before the large model download, so a stale endpoint conflict fails
fast. Colab Secrets require `NGROK_AUTHTOKEN` and `HF_TOKEN` only.

The manual server path remains available below.

Install the POC extras and provide the external E5 ONNX model directory:

```bash
pip install "semantic-prompt-transfer[poc]"
export SPT_MODEL_DIR=/content/models/multilingual-e5-small-onnx-int8
export SPT_ALLOWED_ORIGINS=https://your-html-poc.example
uvicorn semantic_prompt_transfer.poc_server:app --host 0.0.0.0 --port 8000
```

The runtime root defaults to `/content/spt_poc_runtime`. A Google Drive path is
rejected. Expose port 8000 through the chosen temporary HTTPS tunnel and open
the bundled HTML with `?mode=api&api_base=https://your-colab-api.example`.

Optional remote LLM Colab settings:

```bash
export SPT_LLM_BASE_URL=https://your-llm-endpoint.example/v1
export SPT_LLM_MODEL=your-model-name
export SPT_LLM_API_KEY=optional-secret
```

The remote adapter follows an OpenAI-compatible streaming `chat/completions`
contract. Replacing the LLM does not change retrieval, validation, progress, or
DOCX rendering. The response ceiling is 1,400 tokens. A `finish_reason=length`
response automatically continues up to two times so an incomplete final sentence
is never accepted as a completed response.

## Downloadable Excel form

The package includes the supplied seven-sheet blank form as
`credit_report_sample_template.xlsx`. The populated form is parsed first and
its `7. 종합의견` A–E sections are routed to the matching review item before
other credit-report sheets and attachments are considered.

## HTTP surface

```text
GET    /api/v1/runtime/health
POST   /api/v1/poc/users
POST   /api/v1/poc/login
GET    /api/v1/poc/me
DELETE /api/v1/poc/sessions/current
GET    /api/v1/templates/credit-report.xlsx
POST   /api/v1/cases/{case_id}/credit-report
POST   /api/v1/cases/{case_id}/attachments
GET    /api/v1/cases/{case_id}/documents
DELETE /api/v1/cases/{case_id}/documents/{document_id}
POST   /api/v1/cases/{case_id}/review-jobs
GET    /api/v1/review-jobs/{job_id}
GET    /api/v1/review-jobs/{job_id}/stream
GET    /api/v1/review-jobs/{job_id}/evidence/{evidence_id}
GET    /api/v1/review-jobs/{job_id}/evidence/{evidence_id}/capture.png
GET    /api/v1/review-jobs/{job_id}/opinion.docx
POST   /api/v1/review-jobs/{job_id}/chat/stream
```

The v0.26.4 notebook runs the API in anonymous POC mode. The packaged server still
retains the optional identity routes for non-anonymous deployments, but the
notebook HTML neither calls them nor sends `X-POC-Token`.

## Operational boundary

v0.26.4 is suitable for a scheduled single-Colab POC. It is not yet a production
bank deployment. Production still requires enterprise SSO/RBAC, password policy,
malware scanning, encrypted persistent object storage, a managed Vector DB,
distributed jobs, audit retention, runtime recovery, official Excel and Word
forms, and an approved internal LLM with quality evaluation.

The repository and package contain no full STX report, customer upload, model,
credential, or live vector index.
