# Pre-registration: meps-verify-v1 (answer verifier: the output-side safeguard)

Written 2026-11-17 ~16:00Z, before filing job meps-verify-v1. Nothing in it has run yet.
Made with AI agents working in MV2's name. Code: compete-src/meps-verify (meps_verify 0.1.0,
test_meps_verify.py, bench_verify.py). Apache-2.0. Standard library only, no network, no data.

## Why
AHRQ's SI Tech Sprint gives safeguards 25 of 100 points and says a warning may not replace blocking
a prohibited operation. meps_guard (filed as meps-guard-v1) blocks bad REQUESTS. Nothing yet blocks
a bad ANSWER: an agent can take a correct receipt and still write a number that is not in it, quote a
suppressed cell, drop AHRQ's unreliability asterisk, or phrase an association as a cause. meps_verify
reads the final text and blocks it unless every number is backed by a cited, untampered receipt.
Prior art (searched 2026-11-17): the general idea is NOT new. github.com/Jegoba90/anti-hallucination
(library, CLI and MCP; checks each sentence's numbers against supplied facts, keeps receipts) and
arXiv 2603.10060 "Tool Receipts, Not Zero-Knowledge Proofs" (HMAC-signed tool receipts, claims
cross-checked against them) both do receipt-grounded number checking. What is specific here is the
survey-statistics layer: grounding at the precision a number is written with (incl. percent vs
proportion and "million" scaling), AHRQ's suppression and RSE-asterisk semantics, CI/SE fields, and
causal-wording blocks for survey tables. The Census MCP servers found (Glama registry, e.g.
cyanheads/census-mcp-server) decode suppression codes but do not check the agent's answer.
So this is a product component for the AHRQ entry, not a discovery.

## The benchmark (labels fixed now, in test_meps_verify.py CASES)
35 answers against 3 synthetic receipts shaped like meps_guard 0.1.0 receipts: 17 should pass,
18 should be blocked (main reason: ungrounded number 3, suppressed cell 3, missing caveat 1, causal
wording 4, personal advice 2, citation problem 3, tampered receipt 1, no receipt 1; T04, T13 and T14
must also carry a second code).

## Gate and predictions
- G1: `python -m unittest -v test_meps_verify` passes all 10 tests (stderr "Ran 10 tests", "OK").
  The benchmark is one of the 10 tests, so G1 and V2 fail together if a label is not reproduced.
- V2: bench_verify prints "PREDICTION V2 ... HELD": all 35 verdicts and all listed codes reproduced.
- V3: zero false passes (no should-block answer gets through), even if V2 fails on a false block.
Honest risk, stated now: the code has never been run (the run environment has no shell), so a
first-run failure from a plain bug is quite possible; that would be a code failure, not a result.

## Not tested here (stated so nobody over-reads a pass)
Real meps_guard receipts (the id check uses the same canonical JSON, but that is only shown once
both modules run together); real agent-written answers; numbers written in words; sign errors.

## Decision rule
If G1, V2 and V3 hold: meps_verify goes into the planned public repo munimtechnologies/meps-est with
meps_guard (0.1.1, with the suppressed-cell population fix below) and the MCP server gains a
meps_verify_answer tool. If V3 fails, the verifier is fixed before it is published anywhere.

## Bug found in meps_guard 0.1.0 while writing this (fix pending, not yet run)
execute() keeps "population_weighted" (and "weighted_share_excluded") in a SUPPRESSED cell. For
stat "count" that number IS the suppressed estimate, so suppression is defeated. Fix for 0.1.1: set
both to null when a cell is suppressed, and add a unit test. Benchmark case T30 shows the verifier
blocking an answer that quotes such a leaked number.
