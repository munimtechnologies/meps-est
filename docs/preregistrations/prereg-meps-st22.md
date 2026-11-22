# Pre-registration: meps-st22-v1 (plain-Python MEPS against AHRQ's official 2022 insurance summary table)

Written 2026-11-17 (~12:10Z) in run 20261117T112142Z-civic-tech, before filing and before any 2022 MEPS
record or the full reference CSV was read. Made with AI agents working in MV2's name. No person has
checked this plan.

## Why
meps-ex1-v1 (queued, not yet run) checks meps_est against AHRQ's 2015/2016 SAS workshop printouts, which
are rounded. This is a harder, out-of-sample check on the newest year AHRQ has tabulated (2022, HC-243):
AHRQ's official MEPS-HC summary tables are published as CSVs in github.com/HHS-AHRQ/MEPS-summary-tables
(archived; last commit 2026-06-09) with every estimate and standard error at full double precision and the
unweighted sample size. They were made in R (survey package, svydesign(id=~VARPSU, strata=~VARSTR,
weights=~PERWTF, nest=TRUE); subsets as domains). Matching them to ~1e-8 means an independent
implementation reproduces AHRQ's official numbers, not merely their rounding, and is the "material and
auditable use of AHRQ data" that the $2.1M SI Tech Sprint judges (20 + 25 + 10 of 100 points).

What I saw before filing (via WebFetch, first ~330k of 468k characters of the CSV): columns Year ...
value, se, value_rd, se_rd, asterisk, Percent, sample_size, caption; stat_var totPOP (thousands) and pctPOP
(percent); row_vars ind, agegrps, region, married, race, sex, mnhlth (and possibly more); first rows e.g.
Total x "Any private, all ages" 217255.601338417 / se 8594.801858966259 / n 21747. The file has 22,431
persons (AHRQ's download page). Subgroup definitions were taken from AHRQ's code/add_subgrps.R and run_ins.R
as summarized by WebFetch (not quoted verbatim, since lines were paraphrased).

## Method
compete-src/meps-st22: meps_est.py 0.1.0 (unchanged copy), meps_tables.py 0.1.0, test_meps_tables.py.
Attempted rows: stat_var in {totPOP, pctPOP}, col_var in {insurance, ins_lt65, ins_ge65}, row_var in
{ind, agegrps, region, married, race, sex, racesex}, non-empty value and se. rowLevels "All persons" is
reported separately (exploratory: its meaning is not confirmed). Everything else is counted as not attempted.
Value match: relative difference <= 1e-8 (totPOP compared as total/1000, pctPOP as mean*100).
SE match: relative difference <= 1e-6. n match: sample_size == positive-weight records in the domain.

## Gate
G1. unittest prints OK. G2. both MEPS fetches and the CSV fetch succeed, the layout has all 17 columns, and
meps_tables exits 0 with /out/st22_ins.json written. Otherwise every prediction is UNTESTED.

## Predictions
P1. The three Total x insurance (all ages) totPOP cells match in value and SE, and n = 21747 for each.
P2. >= 99% of attempted main rows match in value.
P3. >= 99% of attempted main rows match in SE.
P4. >= 99% of attempted main rows with a sample_size match in n (positive-weight count; R's unwtd.count).
P5. The asterisk equals the rule "se/value > 0.30" on >= 95% of attempted main rows with value > 0
    (a guess from 9 flagged rows seen before filing; AHRQ may also use a sample-size rule).
P6. zero_weight_only_psus is 0 (if not, SE mismatches confined to the affected strata are explained by it).
P7. Peak memory <= 1 GB and the meps_tables step finishes in < 10 minutes.
Reported, not predicted: number of attempted rows, not-attempted row_vars, design counts, "All persons" rows.
Added after P1-P7 were written, still before filing (exploratory, not graded as a prediction): after the
predictions above were fixed, AHRQ's functions_format.R was read (via WebFetch summary). It gives the
asterisk as RSE > 0.3 on unsuppressed rows (so P5 is now expected to hold; it stays graded as written but is
no longer a blind test) and suppression as RSE > 0.5, except a proportion < 0.1 with RSE < (0.1/p - 1)/1.96,
and always when n < 60 or se == 0. The job also reports how often this rule, applied to meps_est's own
numbers, agrees with AHRQ's blank cells ("suppression_rule").

## Grading rules written now
- If P2 holds but P3 fails, check first whether the SE misses sit in strata with zero-weight-only PSUs or in
  domains that contain no sample in a PSU, before calling it a method error.
- If a whole row_var misses (e.g. race), it is a subgroup-definition error in meps_tables, not in meps_est:
  report it as such and fix the definition from AHRQ's code before the next version.
- A pctPOP/totPOP scale miss (every value off by exactly 10^k) is a reporting-unit error; report and fix.

## Decision rule
- P1-P4 hold (and meps-ex1 P1-P5 hold): create munimtechnologies/meps-est (Apache-2.0) with meps_est,
  meps_tables, both validation reports, a SKILL.md for coding agents, and the claim "reproduces N official
  AHRQ MEPS-HC 2022 summary-table cells to 1e-8".
- Then extend to more tables (hc_use needs event files; hc_care_access) and years (2019-2021), and build
  the analysis-receipt + refusal layer the Tech Sprint scores most (25 points).
