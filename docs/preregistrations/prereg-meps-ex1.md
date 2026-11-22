# Pre-registration: meps-ex1-v1 (plain-Python MEPS estimates against AHRQ's published SAS outputs)

Written 2026-11-17 (~09:40Z) in run 20261117T084555Z-civic-tech, before filing and before any MEPS record
was read. Made with AI agents working in MV2's name. No person has checked this plan.

## Why
AHRQ's $2.1M SI Tech Sprint (submissions 2027-03-01..09-09) pays for AI-enabled apps that use AHRQ data
(MEPS, HCUP, CAHPS, SOPS...) "in a material and auditable manner". Any agent or app that answers MEPS
questions must get the survey design right (weights, strata VARSTR, PSUs VARPSU); a wrong standard error
is a wrong answer. AHRQ's official code is R, SAS and Stata only (github.com/HHS-AHRQ/MEPS has no Python).
meps_est.py 0.1.0 (standard library only) reads the ASCII public-use file with AHRQ's own SAS column
statements and computes Taylor-linearized estimates. This job checks it, figure by figure, against AHRQ's
published workshop outputs for two years. The 2016 file (Exercise 1a) and the 2015 file (Exercise 1b) are
checked by the same code; neither result is seen before filing.

## Method
compete-src/meps-ex1: meps_est.py, test_meps_est.py, reference_ex1.json (figures as printed, as strings;
tolerance = half a unit in the last printed digit). Job: unittest; fetch h192dat.zip, h192su.txt,
h181dat.zip, h181su.txt from meps.ahrq.gov to /data (data_key meps-fy) and the two raw *_OUTPUT.TXT files
from github to /out/ref; layout check; workshop --year 2016 and --year 2015; compare each against
reference_ex1.json. python:3.12-slim, 1 CPU, 1 GB.

## Gate
G1. unittest prints OK (16 tests expected; the count is reported, not gated).
G2. all six fetches succeed, both workshop runs exit 0, both compare reports are written.
If the gate fails, every prediction is UNTESTED.

## Predictions
Reference stats in reference_ex1.json: 1a 34 (4 design + 5 figures x 6); 1b 196 (6 design + 5 shares x 2
+ 6 flags x 5 + 5 total-expense figures x 6 + 20 service figures x 6); 230 in all. compare prints the count.
P1. 1a design: records 34655, used 33259, nonpositive 1396, pop 323141687 all match.
P2. 1a: all 30 figure stats (5 figures x n, pop, mean, se_mean, total, se_total) match.
P3. 1b design: records 35427, used 33893, nonpositive 1534, strata 165, psus 369, pop 321423251 all match.
P4. 1b: every point estimate (n, pop, mean, total, ratio) matches.
P5. 1b: every standard error (se_mean, se_total, se_ratio) matches.
P6. Over both years at least 98% of all compared stats match.
P7. Peak memory <= 1 GB and each workshop step < 5 minutes.

## Grading rules written now
- A mismatch where the raw OUTPUT.TXT (saved in /out/ref) shows a different digit from reference_ex1.json is
  a transcription error: fix the reference, re-grade that stat against the raw text, and say so.
- Known risk: AHRQ may have re-released the 2015/2016 files after the 2018 workshop. If every point
  estimate of a year is off by a common pattern (e.g. weights), report it as a data-version difference, not
  a method success.
- Known risk: SAS counts clusters among records with positive weight (assumed here). If only the psus count
  misses, P3 fails and that is reported plainly.

## Decision rule
- P1-P5 hold (after transcription fixes): publish meps-est as a new Apache-2.0 repo
  munimtechnologies/meps-est with the validation report, then add an agent SKILL.md and more workshop
  exercises (other files, other years) as further out-of-sample checks.
- Point estimates match but some SEs do not: investigate the variance (zero-weight PSUs, singleton strata)
  before publishing anything.
- Point estimates miss: fix the reader (layout, implied decimals) before anything else.
