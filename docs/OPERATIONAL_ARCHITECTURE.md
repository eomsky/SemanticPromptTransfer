# Operational architecture

The browser and application server manage two upload classes: a standardized credit-report Excel and other attachments. The credit report is parsed deterministically into canonical facts. Attachments are transformed into MASTER records, L0 chunks, embeddings, and vector records.

The generation layer runs the five fixed review items. It injects item-specific credit facts and common credit facts before retrieving attachments. Approved examples are selected by item, loan type, industry, and situation, but remain a separate style-only prompt section.

File deletion is a lifecycle operation. The registry first marks the document `DELETING`, the vector backend deletes every point under the tenant/case/document scope, derived assets and the original are removed, and the registry records `DELETED`. Audit events remain.

The final Word document is available only after item-level source and numeric validation, validation-result aggregation, and rendering complete. Bank-specific cross-item consistency rules remain a deployment integration.
