# meps-est: guarded, checkable estimates from AHRQ's Medical Expenditure Panel Survey

An open toolkit, written in standard-library Python, for answering questions about U.S. health spending,
insurance and health care use from AHRQ's MEPS Household Component public-use files. AI agents get
an MCP server that:

- computes **design-based estimates** (Taylor-series standard errors with VARSTR/VARPSU, as AHRQ's
  own tables do);
- **refuses what the public data cannot support**: geography below Census region, record-level
  output, causal claims, pooled years without AHRQ's variance file, the wrong survey weight,
  personal advice;
- applies **AHRQ's own disclosure rule** (suppress at fewer than 60 sample people or RSE > 50%;
  asterisk at RSE > 30%);
- attaches an **analysis receipt** to every number: a JSON record whose id is the SHA-256 of
  its contents;
- **checks the final text before anyone sees it.** `meps_verify` blocks any number that does not
  trace to a cited, untampered receipt. `meps_frame` (new in 0.3.0) allows free text only if
  every word comes from a fixed descriptive vocabulary. Anything else is replaced by a
  descriptive answer rendered from the receipts, or, if that cannot be done, nothing is shown.
  A warning never stands in for blocking.

Made with AI agents working in the name of MV2 (Munim, Inc., mv2@munimtech.com). Licensed
Apache-2.0. MEPS data are public-use files from the Agency for Healthcare Research and Quality
(AHRQ). This project is not affiliated with or endorsed by AHRQ.

## Status: please read before relying on it

| Part | Checked so far | Not yet checked |
|---|---|---|
| `meps_est` (survey estimator) | unit tests on synthetic data (run before every commit) | reproduction of AHRQ's published workshop outputs (2015, 2016) and of every cell of AHRQ's official 2022 health-insurance summary table, both **pre-registered and still waiting for compute** ([docs/preregistrations](docs/preregistrations)) |
| `meps_guard` 0.1.1 (request policy, disclosure, receipts) | unit tests | end-to-end run on the real 2022 file; a 234-item in-sample red-team set; a 90-item blind held-out set for the question screen (all pre-registered, waiting) |
| `meps_verify` 0.1.0 (answer verifier) | unit tests | a 35-case labeled benchmark (pre-registered, waiting) |
| `meps_frame` 0.1.0 (answer frame) | unit tests | a blind held-out set of drafts written by someone who has not seen the vocabulary (planned) |

No person has checked these modules against AHRQ's numbers yet. The pre-registrations in
`docs/preregistrations/` were written before any result existed. Results will be added next to
them, whichever way they come out.

**One finding already shapes the design.** We hand-traced the question screen (a word-pattern
check) on the blind held-out set. That trace is not a graded result, and it suggested the screen
generalizes poorly. So the safety of the final text does not rest on word lists: `meps_frame` uses
an allowlist, and the request policy refuses structurally (variable names, weights, years,
statistics).

## Quick start

1. Download one year's files from AHRQ into a folder, e.g. for 2022:
   `https://meps.ahrq.gov/mepsweb/data_files/pufs/h243/h243dat.zip` and
   `https://meps.ahrq.gov/mepsweb/data_stats/download_data/pufs/h243/h243su.txt`.
2. Run the MCP server (stdio):
   `python meps_mcp.py --data-dir <folder> --receipts-dir <folder>/receipts`
3. Tools: `meps_policy`, `meps_find_variables`, `meps_check_request`, `meps_estimate`,
   `meps_verify_answer`, `meps_final_answer`. Agents should show the user exactly the `answer`
   that `meps_final_answer` returns. See [SKILL.md](SKILL.md).

Command-line use without MCP:

```
python meps_verify.py --answer answer.txt --receipt r1.json      # exit 0 pass, 2 blocked
python meps_frame.py  --draft draft.txt  --receipt r1.json       # exit 0 draft, 1 replaced, 2 blocked
```

Tests: `python -m unittest -v` (synthetic files only; no download needed).

## Files

| File | What it does |
|---|---|
| `meps_est.py` | reads AHRQ's fixed-width files and SAS layouts; design-based totals, means and proportions with linearized SEs |
| `meps_tables.py` | rebuilds AHRQ's MEPS-HC summary-table cells (used for validation against the official tables) |
| `meps_guard.py` | request policy, AHRQ disclosure rule, execution and analysis receipts |
| `meps_verify.py` | answer verifier: numbers must trace to cited receipts at the precision written |
| `meps_frame.py` | descriptive answer frame: allowlisted vocabulary, rendered answers, final decision |
| `meps_mcp.py` | the MCP server (JSON-RPC 2.0 over stdio) |
| `test_*.py` | unit tests on synthetic MEPS-shaped files |
| `docs/preregistrations/` | the pre-registered validation and safeguard tests |

## Limits

- Covers the U.S. civilian noninstitutionalized population; one year per request (2019-2022).
  Dollar amounts are nominal.
- The answer frame allows direction words ("higher", "lower") but does not check them against the
  numbers. It cannot rule out every causal reading that two descriptive sentences invite when put
  side by side. Answers in other languages are always replaced by the English rendered answer.
- Numbers written as words are not checked by `meps_verify`, so `meps_frame` blocks them.
- Survey estimates describe associations in a population. They are not causal effects and not
  advice for any individual.
