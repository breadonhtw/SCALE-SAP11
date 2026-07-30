# Data Quality Report — TRUSTSPHERE_REFERENCE snapshot

Date: 2026-07-30
Source: read-only schema `TRUSTSPHERE_REFERENCE` (HANA Cloud, team-11 tenant)
Target: cleaned snapshot in writable schema `TEAM_11_USER`
Scripts: `scripts/profile_data.py` (diagnosis), `scripts/clean_data.py` (materialisation, idempotent)

## Cleaning philosophy

Financial-crime data: never invent facts. Only values derivable from other
columns are corrected, always into a **new** column with the source column
preserved. Contradictions that cannot be resolved are flagged, not altered.
Every rule applied is logged in `TEAM_11_USER.DQ_ISSUES`.

## What was checked and found clean

Profiling covered duplicates, nulls, referential integrity, value ranges,
date ordering, and cross-column consistency on all 16 accessible objects.
Found fully clean (copied as-is):

- `TRANSACTIONS` (150,000) — no duplicate IDs/UUIDs/refs, no nulls, no
  orphan company/country FKs, amounts positive, `AMOUNT_USD` consistent with
  `AMOUNT_ORIGINAL × EXCHANGE_RATE` (within 1%), settlement after initiation,
  `IS_CROSS_BORDER` consistent with country pair.
- `TRANSACTION_BASELINES` (150,000), `COMPANY_RISK_PROFILES` (5,000),
  `COMPLIANCE_CASES` (500), `CASE_ALERTS` (580), `JOULE_EXPLANATIONS` (5,000),
  and the 5 reference tables (COUNTRIES, INDUSTRIES, REGIONS, SANCTIONS_LISTS,
  SCREENING_RULES).

## Issues found and actions taken

| # | Object | Issue | Rows | Action |
|---|---|---|---:|---|
| 1 | RISK_ALERTS | `SLA_BREACHED` only flags *currently open* breaches; all 3,414 historically resolved-late alerts were `FALSE` | 3,414 | Added `SLA_BREACHED_DERIVED` recomputed from `RESOLVED_AT` vs `SLA_DUE_AT` (source kept as `SLA_BREACHED_SOURCE`). **Use the derived column as the SLA-model label.** |
| 2 | RISK_ALERTS | Bulk-close batch stamped `RESOLVED_AT = 2026-06-24 16:17:30` earlier than `CREATED_AT` | 25 | Flagged `ALERT_DQ_FLAG='RESOLVED_BEFORE_CREATED'`; `RESOLUTION_HOURS` set NULL so they drop out of duration training. |
| 3 | COMPANIES | `KYC_STATUS='VERIFIED'` but expiry date already passed | 8 | `KYC_EFFECTIVE_STATUS='EXPIRED'`; flagged `STATUS_STALE_EXPIRED`. |
| 4 | COMPANIES | `KYC_STATUS='EXPIRED'` but expiry date still in future | 189 | Flagged `STATUS_DATE_CONTRADICTION`; source status kept (cannot infer a verification that may never have happened). |
| 5 | COMPANIES | 3,001 rows share a `LEGAL_NAME` with another company | 3,001 | No change — registration numbers and LEIs are all distinct, so these are distinct legal entities with colliding synthetic names. **Entity resolution must key on `REGISTRATION_NUMBER`/`LEI_CODE`, never name.** |
| 6 | COMPANY_BENEFICIAL_OWNERS | Ownership percentages per company sum above 100% (max 109.89%) | 377 companies | Flagged `OWNERSHIP_DQ_FLAG` + `COMPANY_OWNERSHIP_SUM` added; percentages preserved (rescaling would fabricate data). |

## Added columns (all source columns preserved)

- `RISK_ALERTS`: `SLA_BREACHED_SOURCE`, `SLA_BREACHED_DERIVED`,
  `RESOLUTION_HOURS`, `ALERT_DQ_FLAG`
- `COMPANIES`: `KYC_EFFECTIVE_STATUS`, `KYC_DQ_FLAG`
- `COMPANY_BENEFICIAL_OWNERS`: `OWNERSHIP_DQ_FLAG`, `COMPANY_OWNERSHIP_SUM`

## Findings that shape later work

1. **SLA label class balance is extreme: 99.4% breached** (4,968 of 5,000
   after correction; only 32 non-breached). Historical resolution times
   average ~10,700 hours (~15 months) against much tighter SLA windows.
   A "will this breach?" classifier trained on closed alerts is near-useless
   at this balance — the SLA-risk layer should target *time-to-breach /
   remaining-margin* (e.g. predicted `RESOLUTION_HOURS` vs `SLA_DUE_AT`) or
   frame the label over an early observation window. Decide before building
   the predictive layer.
2. KYC is expired for ~60% of companies (`KYC_EFFECTIVE_STATUS='EXPIRED'`
   for 3,010 of 5,000) — a strong deterministic urgency factor.
3. `ML_MODEL_REGISTRY` and `TRANSACTION_RISK_SCORES` views in the reference
   schema are broken server-side (error 391 "invalidated view") — do not
   build against them.
4. Alert statuses are `OPEN` / `INVESTIGATING` / `CLOSED_TRUE` /
   `CLOSED_FALSE` (closed-true ≈ confirmed, 965; closed-false ≈ dismissed,
   2,481). 1,554 alerts are still open (743 OPEN + 811 INVESTIGATING) —
   that is the live queue for the cockpit.
