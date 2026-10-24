"""Judge-injectable anomalies for the live demo (Hyphenate may try their own scenario).
duplicate_invoice | rate_mismatch | no_work_order | above_threshold | invoice_before_fix | clean_fix
Each writes a crafted invoice into ledger/ap_inbox/ and prints the expected decision."""
