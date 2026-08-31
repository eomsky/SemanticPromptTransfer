# Changelog

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
