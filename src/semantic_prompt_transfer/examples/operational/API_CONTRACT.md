# Colab POC HTML API contract

## Authentication

- `POST /api/v1/poc/users`: register `department`, `name`, `employee_number`.
- `POST /api/v1/poc/login`: authenticate with `user_id` and `password`.
- `GET /api/v1/poc/me`: restore the current screen session.
- `DELETE /api/v1/poc/sessions/current`: revoke only the browser token.

For this limited POC, user ID and initial password both equal the employee
number. Every protected request carries `X-POC-Token`. The server derives and
enforces the user's tenant/case scope; query-string values cannot widen it.

## Credit-report form

`GET /api/v1/templates/credit-report.xlsx` returns the configured workbook only
to an authenticated user. The current file is a blank sample matching
`credit_report_template.json`. The real workbook and mapping will replace it as
one versioned pair.

## Upload and inline filenames

```text
POST /api/v1/cases/{case_id}/credit-report
POST /api/v1/cases/{case_id}/attachments
GET  /api/v1/cases/{case_id}/documents
```

The file-list response has one `credit_report` list and one `attachments` list.
Each row supplies `document_id`, `filename`, `file_type`, `size_bytes`,
`progress_percent`, `progress_stage`, `status`, and `can_delete`. The HTML renders
these rows as filename chips with a stage and `×`; there is no status popup.

## Verified delete

`DELETE /api/v1/cases/{case_id}/documents/{document_id}` succeeds only after:

1. document state changes to `DELETING`;
2. every matching tenant/case/document vector is deleted;
3. a count confirms zero vectors;
4. derived artifacts and the server-side original are absent; and
5. the registry records `DELETED`.

The user's pre-upload local source is outside this deletion boundary.

## Review generation

`POST /api/v1/cases/{case_id}/review-jobs` starts the five-item job. The HTML
polls `GET /api/v1/review-jobs/{job_id}` and retains the only progress bar in the
screen. At 100%, `GET /api/v1/review-jobs/{job_id}/opinion.docx` returns the Word
file.

Only a completed job accepts `POST /api/v1/review-jobs/{job_id}/chat/stream`.
The NDJSON response streams `chat_start`, `chat_token`, and `chat_complete`.
Follow-up prompts omit generation few shots and include the completed opinion,
uploaded evidence, and the same job's accumulated user/assistant turns.
