---
name: meps-guarded-estimates
description: Answer U.S. health-spending, insurance and use questions from AHRQ's Medical Expenditure Panel Survey (MEPS-HC public-use files) with survey-correct estimates, AHRQ's own suppression rule, and a checkable receipt for every number. Use when someone asks for national or Census-region statistics such as mean spending, share uninsured or number of people with a visit.
license: Apache-2.0
---

# Guarded MEPS estimates

Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com). Apache-2.0.
Validation against AHRQ's published numbers is pre-registered and not yet run; see README.md.

## What this gives you
An MCP server, `meps_mcp.py`, that uses only the Python standard library. It reads AHRQ's public-use
files and returns design-based estimates (Taylor-series standard errors with VARSTR/VARPSU, the
method AHRQ's own tables use). AHRQ's precision rule decides what may be shown. Every allowed
answer comes with an analysis receipt whose id is the SHA-256 of its contents.

## Setup
1. Download the year's files from AHRQ into one folder, e.g. for 2022
   `https://meps.ahrq.gov/mepsweb/data_files/pufs/h243/h243dat.zip` and
   `https://meps.ahrq.gov/mepsweb/data_stats/download_data/pufs/h243/h243su.txt`.
2. Register the server with your client:
   `python meps_mcp.py --data-dir <folder> --receipts-dir <folder>/receipts`

## How to answer a question: always in this order
1. `meps_policy` once per session. Read the refusal codes and the disclosure rule.
2. `meps_find_variables` to find variable names for the year (e.g. pattern `^TOTEXP`, `^INSCOV`).
3. `meps_estimate` with the user's question in words in `question` (required) and a structured
   request: `year`, `stat` (mean | total | proportion | count), `variable`, `condition` (for a
   proportion), `domain`, `by` and `compare`. Use `{yy}` for the two-digit year,
   e.g. `TOTEXP{yy}`.
4. Draft the answer in plain descriptive English. Copy the numbers at the precision you show,
   include the 95% CI, and cite the receipt id (its first 12 characters are enough). Mark flagged
   cells (RSE > 30%) with an asterisk, and say "suppressed" for suppressed cells. Use digits, not
   number words. Do not explain why, predict, or advise.
5. Call `meps_final_answer` with the draft and the receipt ids. **Show the user exactly the
   `answer` it returns, and nothing else.** With `status` "replaced", the answer is a descriptive
   rendering of the receipts. With "blocked", it is null: tell the user the result can't be shown,
   and why (see `findings`). `meps_verify_answer` explains what is wrong with a draft.

## Refusals and blocks are deliberate
Explain them to the user. Do not route around them, for example by splitting a refused request
into pieces or by computing the number yourself:
- **GEO_BELOW_REGION**: the public-use file identifies only the four Census regions. Offer the
  regional breakdown instead (REGIONX 1-4).
- **CAUSAL_CLAIM**: a survey tabulation shows associations. Offer a descriptive comparison instead.
- **RECORD_LEVEL**: no individual records, identifiers or extremes.
- **INDIVIDUAL_ADVICE**: population estimates are not advice for one person.
- **Suppressed cells** (fewer than 60 people, SE of 0, or RSE over 50%) have no number. Do not
  estimate one.

## Example
Question: "What share of people under 65 were uninsured all year in 2022, by region?"

```json
{"question": "What share of people under 65 were uninsured all year in 2022, by region?",
 "year": 2022, "stat": "proportion", "variable": "INSURC{yy}",
 "condition": {"op": "==", "value": 3},
 "domain": [{"variable": "AGELAST", "op": "<", "value": 65}],
 "by": {"variable": "REGIONX", "levels": [1, 2, 3, 4],
        "labels": {"1": "Northeast", "2": "Midwest", "3": "South", "4": "West"}}}
```

## Limits
- It covers the U.S. civilian noninstitutionalized population. Dollar amounts are nominal.
- Supported years are 2019-2022, one year per request. Pooled years are refused until AHRQ's pooled
  variance file is supported.
- The question screen is a word-pattern check, used as defense in depth. The structural checks, the
  answer verifier and the answer frame do not depend on it.
