# semantic-prompt-transfer 0.22.0

SemanticPromptTransfer v0.22 is a time-boxed Colab POC implementation for the
credit-review workflow. The browser remains a thin HTML client. Uploads,
extracted facts, L0 vectors, user registrations, progress, and generated Word
files live only in the Colab runtime and are removed when that runtime closes.
Google Drive is not mounted by the application.

## POC flow

1. A user signs up with department, name, and employee number.
2. For this limited POC, both login ID and initial password equal the employee
   number. Password material is hashed in the temporary SQLite registry.
3. The user downloads the credit-report Excel form, fills it, and uploads one
   workbook.
4. The user uploads multiple PDF, DOCX, XLSX, TXT, or Markdown attachments.
5. Filenames appear inline. The `×` button deletes the server copy, derived
   files, and all vectors for that document after a zero-count verification.
6. The five fixed review items run and the completed opinion is downloaded as
   DOCX.

The employee-number password rule is intentionally limited to a scheduled POC.
It is not an enterprise authentication design.

## Source priority and few shots

Evidence is assembled separately for each review item in this fixed order:

1. `TIER_1`: item-specific cells from the credit-report workbook
2. `TIER_2`: common cells from the same workbook
3. `TIER_3`: retrieved L0 evidence from attachments

Approved few shots are selected by review item, loan type, industry code, and
situation tags. They control structure and tone only; they are never treated as
current-case evidence.

## Multiple PDFs in one logical vector database

`ShardedNpzVectorStore` presents one exact-search database while storing one
atomic NPZ shard per uploaded document. Search always requires `tenant_id` and
`case_id`. Replace and delete include `document_id`, so changing one PDF does not
rewrite or remove another PDF. The backend implements the same scoped contract
that a production Vector DB adapter must preserve.

## Colab start

The one-notebook path is
`notebooks/SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb`. Its single code
cell mounts the owner's Drive, verifies the approved wheel and compressed E5
asset, stages both under `/content`, serves the packaged HTML and API on one
port, and creates an ngrok URL protected by Basic Auth. See
`docs/COLAB_ONE_CLICK_v0.22.md` for the Drive layout and required Colab Secrets.

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

The remote adapter follows an OpenAI-compatible `chat/completions` contract. If
it is absent or fails grounding checks, the CPU-fast evidence template generator
creates a conservative draft. Replacing the LLM does not change retrieval,
validation, progress, or DOCX rendering.

## Downloadable Excel form

The package includes `credit_report_sample_template.xlsx` and its versioned
`credit_report_template.json` mapping. The workbook is a blank POC placeholder.
When the real form is supplied, replace both the workbook and mapping together;
the HTML button and `/api/v1/templates/credit-report.xlsx` route remain stable.

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
GET    /api/v1/review-jobs/{job_id}/opinion.docx
```

Every route except health, signup, and login requires `X-POC-Token`.

## Operational boundary

v0.22 is suitable for a scheduled single-Colab POC. It is not yet a production
bank deployment. Production still requires enterprise SSO/RBAC, password policy,
malware scanning, encrypted persistent object storage, a managed Vector DB,
distributed jobs, audit retention, runtime recovery, official Excel and Word
forms, and an approved internal LLM with quality evaluation.

The repository and package contain no full STX report, customer upload, model,
credential, or live vector index.
