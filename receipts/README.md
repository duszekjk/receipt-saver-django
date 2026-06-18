# Receipts App

Reusable Django app for receipt scanning, OCR, savings tracking, bank statement import and transaction matching.

Add 'receipts' to INSTALLED_APPS and include receipts.urls in the host project.

Required settings:
- OPENAI_KEY
- OPENAI_RECEIPT_MODEL (optional)

The host project manages migrations, database, authentication, media storage and deployment.