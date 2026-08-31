# Operational POC example

This directory contains the minimal HTML interface, a versioned credit-report
mapping, approved style-only few shots, and a downloadable sample workbook. It
contains no real customer data.

`credit_report_sample_template.xlsx` is a placeholder used only to prove the
download and parsing flow. Replace both the workbook and
`credit_report_template.json` with the bank-approved Excel template and mapping
when the real form is supplied. The HTML and API route do not need to change.

The POC runtime keeps uploads, derived facts, vectors, user registrations, and
generated opinions under one disposable Colab directory. It never mounts or
writes Google Drive.
