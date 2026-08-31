# Changelog

## 0.26.0 - 2026-08-31

- Added post-review free chat that appears only after the complete A–E stream,
  omits few shots, retains the completed opinion and accumulated Q&A context,
  and streams through the same vLLM server.
- Raised the response ceiling to 1,400 tokens and added automatic continuation
  on `finish_reason=length`, so incomplete responses fail or continue instead
  of being silently accepted.
- Restyled the interface as a more angular bank-workstation UI, renamed the
  download action and agent label, and moved the return-to-opinion control below
  the composer.
- Published a separate v0.26 wheel, asset manifest, Colab launcher, validation
  record, and Google Drive runtime-asset paths.

## 0.25.0 - 2026-08-31

- Replaced the dense Gemma runtime with `google/gemma-4-26B-A4B-it` MoE served
  by vLLM, with BF16, prefix caching, streaming, and four continuous-batched sequences.
- Added a shared GPU E5 encoder with batch progress, conservative PDF block
  coalescing, an in-memory reuse cache, and thread-safe single-GPU execution.
- Removed the notebook's application login and ngrok Basic Auth; anonymous POC
  users receive random browser-local scopes instead of IP-derived identities.
- Allowed attachment-only RAG when no credit report is uploaded and reserved a
  separate TIER 3 prompt budget so business-report evidence is not crowded out.
- Hid raw `CR_`/`ATT_` identifiers from visible HTML and Word text while keeping
  the server-side evidence mapping and clickable source-type controls.
- Enlarged the evidence modal and added zoomable, high-resolution PDF and
  Excel-style captures with Korean fonts and source highlighting.
- Pre-populated all three notebook FEW SHOT cells and set the per-item generation
  ceiling to 700 tokens.
- Serialized the text-only multimodal limit as JSON for current vLLM CLI
  compatibility instead of passing the rejected legacy comma syntax.

## 0.22.0 - 2026-08-31

- Added a Drive-backed, single-code-cell Colab launcher with asset SHA-256
  verification, compressed E5 staging, same-origin HTML/API serving, ngrok
  Basic Auth, rerun cleanup, and ephemeral-only user data.
- Added a disposable Colab runtime that rejects Google Drive paths and purges all POC state on close.
- Added ephemeral signup/login using department, name, and employee number; POC ID and password equal the employee number.
- Replaced the upload-status popup and upload progress bars with inline filenames, stages, and verified `×` deletion.
- Added the credit-report template download route and bundled blank sample XLSX.
- Added PDF, DOCX, XLSX, TXT, and Markdown extraction for the time-boxed upload path.
- Added one logical exact-search Vector DB backed by atomic per-document NPZ shards.
- Added OpenAI-compatible remote LLM support with the existing evidence-grounded CPU fallback.
- Added a Uvicorn entry point, FastAPI end-to-end smoke, real CPU E5 two-PDF smoke, and user-scope isolation checks.
- Preserved L0 as the default and the TIER 1 > TIER 2 > TIER 3 evidence contract.

## 0.21.0 - 2026-08-31

- Aligned the package with the approved minimal HTML upload/status/download interface.
- Added file-level progress percentages and Korean processing stages to the registry contract.
- Added `OperationalApplicationService` and optional FastAPI upload/list/delete/job/download routes.
- Added required artifact-store deletion and post-delete vector/file verification.
- Added safe local operational storage with root containment checks.
- Added replaceable `TextGenerator`, CPU Qwen 0.5B adapter, grounding precheck, and evidence-only fallback.
- Bundled the approved standalone HTML as an operational example.
- Preserved v0.20 source priority, few-shot isolation, five-item workflow, and L0 retrieval behavior.

## 0.20.0 - 2026-08-31

- Added five fixed credit-review items and deterministic query profiles.
- Added standardized Excel mapping parser with cell-level provenance.
- Enforced TIER 1 credit-item facts, TIER 2 credit common facts, and TIER 3 attachment evidence.
- Added approved few-shot registry and selection by review item, loan type, industry classification, and situation tags.
- Added few-shot numeric and forbidden-token leakage validation.
- Added global chunk IDs while retaining local chunk IDs.
- Added document-scoped delete for NPZ and vector-store adapters.
- Added operational file/job registry and progress events.
- Added provider-neutral five-item generation orchestration and Word output.
- Added optional persistent Chroma adapter.
- Preserved v0.19 L0 chunk content and retrieval APIs.
