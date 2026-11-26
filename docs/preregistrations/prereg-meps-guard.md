# Pre-registration: meps-guard-v1 (MEPS safeguards + MCP server, end-to-end on real 2022 data)

Written 2026-11-17 ~14:00Z, before filing job meps-guard-v1. Nothing in it has run yet.
Made with AI agents working in MV2's name. Code: compete-src/meps-guard (meps_est 0.1.0 copy,
meps_guard 0.1.0, meps_mcp 0.1.0, demo_mcp.py, test_meps_guard.py). Apache-2.0.

## Why
AHRQ's SI Tech Sprint ($2.1M, submissions 2027-03-01..09-09) gives "trustworthy SI & safeguards" 25 of
100 points, says "a warning may not be used as a substitute for blocking a prohibited operation", wants
an analysis receipt for every material output, and a $250k cross-track prize for MCP/agent interoperability.
This job tests the safeguards layer and the MCP server that would sit on top of the meps_est estimator.

## Gate
G1: `python -m unittest -v test_meps_guard` passes all tests (count read from stderr "Ran N tests").
If G1 fails, nothing below is graded; fix and file -v2.

## Predictions (graded from stdout "PREDICTION" lines and out/demo_report.json)
- P2 handshake: initialize echoes protocolVersion 2025-06-18; tools/list returns exactly meps_policy,
  meps_find_variables, meps_check_request, meps_estimate; the server exits 0 when stdin closes.
- P3 refusals: all 8 policy-violating requests (state, causal, record/max, wrong SAQ weight, pooled
  years, unweighted, DUPERSID breakdown, personal advice) are refused with their expected code, and
  none of the 8 responses contains any result.
- P3b: all 7 allowed calls (including the repeat) return status ok.
- P4: the 18-year-old / Northeast / non-Hispanic Asian / under-65 cell is suppressed (n < 60) and
  carries no value or SE.
- P5 official agreement: of the 7 official hc_ins cells checked (4 region pctPOP "<65, Uninsured",
  2 sex pctPOP "Any private, all ages", 1 ind totPOP "Any private, all ages"), all 7 are found in
  DY2022.csv and all 7 match (value rel <= 1e-8, SE rel <= 1e-6). NOT independent of meps-st22-v1:
  if st22 shows a mismatch in these same groups, P5 fails for the same reason. Possible failure modes,
  stated now: (a) the ind row label may not be "Total" (then found = 6); (b) if INSURC22/INSCOV22 had
  reserved codes, our denominators exclude them while AHRQ's include a "Missing" level (n_excluded_missing
  in the stdout shows this).
- P6 receipts: the repeated request gets the same receipt_id; one receipt file per distinct allowed
  answer (6 files: the repeat overwrites its twin).
- P7: every suppressed cell in the transcript has value null and SE null.

## Exploratory (no prediction)
- The weight the guard picks for ADGENH42 (should be SAQWT22F per the h243 documentation), and how many
  SAQ respondents have reserved codes on it.
- Women-minus-men mean total expenditure 2022 with its covariance-aware SE.
- Sample size convention for totPOP rows in hc_ins (our n vs sample_size).

## Decision rule
If G1 and P2-P7 hold (P5 may be 6/7 only because of failure mode (a)), the safeguards layer and MCP
server go into the planned public repo munimtechnologies/meps-est together with the estimator, once
meps-st22-v1/meps-ex1-v1 have validated the estimator. If P3, P4 or P7 fail, the guard is fixed before
anything is published.
