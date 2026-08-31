# Operational architecture

The browser and application server manage two upload classes: a standardized credit-report Excel and other attachments. The credit report is parsed deterministically into canonical facts. Attachments are transformed into MASTER records, L0 chunks, embeddings, and vector records.

The generation layer runs the five fixed review items. It injects item-specific credit facts and common credit facts before retrieving attachments. Approved examples are selected by item, loan type, industry, and situation, but remain a separate style-only prompt section.

File deletion is a verified lifecycle operation. The registry first marks the document `DELETING`, the vector backend deletes every point under the tenant/case/document scope, and a count query must return zero. The artifact store then removes the original and derived assets and verifies absence before the registry records `DELETED`. On any failure the document becomes `FAILED`, remains visible to operators, and is not reported as successfully removed. Audit events remain.

The HTML-facing application service groups one credit report and multiple attachments and exposes file type, size, progress percentage, progress stage, and delete availability. Optional HTTP routes translate the interface actions into application-service calls; the parser/indexer remains a replaceable `UploadProcessor` so the frozen upstream PDF/Excel implementation can be connected without changing the web layer.

The initial text-generation adapter runs `Qwen/Qwen2.5-0.5B-Instruct` on CPU when the optional dependencies and model are available. It is isolated behind `TextGenerator`. A grounding precheck and evidence-only fallback prevent missing citations or unsupported numbers from stopping early tests. The final Word document is available only after item-level source and numeric validation, validation-result aggregation, and rendering complete. Bank-specific cross-item consistency rules remain a deployment integration.
