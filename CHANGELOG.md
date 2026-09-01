# Changelog

## 0.26.6 - 2026-09-01

- Replaced destructive rule-validation generation gate with OFF/SHADOW/ENFORCE verification lanes; OFF is default.
- Added claim segmentation, verifier-only findings, minimal-scope repair, patch guard, and streamed claim_patch events.
- Separated free chat into GENERAL / CASE_QA / OPINION_QA; CASE_QA now performs query-time RAG and chat never invokes the verifier.
- Added human evidence trace ledger, same-screen clustering, retrieval diversity preference, numbered evidence references, and DOCX evidence appendix with source captures.
- Changed PDF/XLSX evidence capture to context-first views with wider table/header context and highlighted source spans.
- Simplified Word header to case id only; removed loan type, industry classification, and target-company rows pending later redesign.
- Preserved the v0.26.5 few-shot structure while carrying forward the refreshed image-grounded FEW_SHOT_1.

## 0.26.5 - 2026-09-01

- Added per-anonymous-case preload of ABC기업 credit-report and business-report demo files from owner Drive.
- Demo files remain UPLOADED with zero vectors until review generation starts; no parsing or embedding occurs on page load.
- Added scope-bound source-file download by clicking the displayed filename.
- Deleting a demo copy does not delete the Drive original and does not re-seed the same case after refresh.
- Kept the one-time STX→ABC PDF anonymization outside application and repository code.

## 0.26.4 - 2026-09-01

- Replaced terminal validation failures with a non-failing grounded repair/fallback pipeline.
- Removed standing credit-report evidence priority; credit-report values win only on direct same-fact conflicts.
- Added a 50% minimum credit-report context representation constraint using the closest credit-report rows/chunks when available.
- Added claim-local typed numeric validation, FEW SHOT sanitization, RAG relevance/dedup gates, cross-section checks, and minimal DOCX fallback.
- Updated the upload UI to label the source simply as `신용조사서` with no first-priority text.

## 0.26.3 - 2026-08-31

- Installed Ninja explicitly inside the isolated vLLM environment so the
  native Gemma 4 warm-up can compile generated kernels.
- Prepended the isolated environment's `bin` directory to the vLLM process
  `PATH`, allowing PyTorch and vLLM child processes to resolve that exact Ninja
  executable without mutating the Colab kernel environment.
- Added a Ninja version preflight before model startup, turning a late failure
  after weight loading and CUDA-graph capture into an immediate actionable
  dependency error.
- Preserved v0.26.2 artifacts unchanged and published separate v0.26.3 assets.

## 0.26.2 - 2026-08-31

- Forced Gemma 4 MoE to use the native vLLM model implementation instead of
  falling back to the Transformers modeling backend during engine startup.
- Aligned the single-A100 BF16 reservation with the official Gemma 4 recipe by
  raising `--gpu-memory-utilization` from 0.88 to 0.90 while retaining the
  conservative 16,384-token context and four-sequence cap.
- Replaced the final-8,000-character error tail with root-cause-first engine log
  extraction so CUDA, kernel, model-backend, and weight-loading failures remain
  visible in Colab exceptions.
- Preserved v0.26.1 artifacts unchanged and published separate v0.26.2 assets.

## 0.26.1 - 2026-08-31

- Isolated vLLM and its dependency resolver in a dedicated `uv` virtual
  environment instead of mutating the active Colab kernel packages.
- Added an E5 dependency probe before the large model download and delayed the
  first application import until after vLLM installation, preventing mixed
  NumPy/SciPy modules in one Python process.
- Preserved the v0.26 runtime assets unchanged and published separate v0.26.1
  wheel, manifest, notebook, documentation, and Drive paths.

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
