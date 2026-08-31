# Minimal HTML API contract

Every request is scoped by `tenant_id` and `case_id`. Every uploaded document receives an opaque `document_id`.

## File list

`GET /api/v1/cases/{case_id}/documents?tenant_id={tenant_id}` returns one `credit_report` list and one `attachments` list. Each row contains:

```json
{
  "document_id": "opaque-id",
  "filename": "attachment.pdf",
  "document_kind": "attachment",
  "file_type": "PDF",
  "size_bytes": 12345,
  "progress_percent": 70,
  "progress_stage": "벡터임베딩",
  "processing_message": "임베딩을 생성하고 있습니다.",
  "status": "INDEXING",
  "can_delete": true
}
```

## Delete

`DELETE /api/v1/cases/{case_id}/documents/{document_id}?tenant_id={tenant_id}` succeeds only after:

1. the document is marked `DELETING`;
2. all vectors matching tenant, case, and document are deleted;
3. a vector count confirms zero remaining points;
4. derived artifacts and the stored original are absent; and
5. the registry records `DELETED`.

The browser removes the row only after a successful response. It never deletes the user's local source file.

## Review generation

`POST /api/v1/cases/{case_id}/review-jobs` starts the five-item job. The browser polls `GET /api/v1/review-jobs/{job_id}` and enables the download button only when progress is 100. The Word file is returned by `GET /api/v1/review-jobs/{job_id}/opinion.docx`.
