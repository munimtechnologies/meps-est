#!/usr/bin/env python3
# meps_tables.py - reproduce AHRQ's official MEPS-HC summary tables with meps_est.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""Check meps_est against AHRQ's official MEPS-HC summary tables, cell by cell.

AHRQ publishes the numbers behind its interactive MEPS-HC summary tables as CSV files in
github.com/HHS-AHRQ/MEPS-summary-tables (formatted_tables/<table>/DY<year>.csv), with each
estimate and standard error at full precision and the unweighted sample size. They were made in
R with the survey package: svydesign(id=~VARPSU, strata=~VARSTR, weights=~PERWTF, nest=TRUE).

This module rebuilds the subgroup variables the way AHRQ's code/add_subgrps.R does (as read on
2026-11-17), recomputes every cell it knows how to define from the public-use ASCII file, and
compares. Supported now: the health-insurance table (hc_ins), statistics totPOP (number of people,
in thousands) and pctPOP (percent), by ind, agegrps (all three age cuts), region, married, race,
sex and racesex. Rows for other subgroups are counted and reported as not attempted.

    python meps_tables.py ins --year 2022 --data h243dat.zip --layout h243su.txt \
        --ref DY2022.csv --out st22_ins.json
"""
__version__ = "0.1.0"

import argparse
import csv
import json
import math
import sys

import meps_est as me

AGE_CUTS = [
    ([-1, 4.5, 17.5, 44.5, 64.5, math.inf], ["Under 5", "5-17", "18-44", "45-64", "65+"]),
    ([-1, 17.5, 64.5, math.inf], ["Under 18", "18-64", "65+"]),
    ([-1, 4.5, 6.5, 12.5, 17.5, 18.5, 24.5, 29.5, 34.5, 44.5, 54.5, 64.5, math.inf],
     ["Under 5", "5-6", "7-12", "13-17", "18", "19-24", "25-29", "30-34", "35-44", "45-54",
      "55-64", "65+"]),
]
REGION = {1: "Northeast", 2: "Midwest", 3: "South", 4: "West"}
MARRIED = {1: "Married", 2: "Widowed", 3: "Divorced", 4: "Separated", 5: "Never married",
           6: "Inapplicable (age < 16)"}
RACE = {1: "Hispanic", 2: "White", 3: "Black", 4: "Amer. Indian, AK Native, or mult. races",
        5: "Asian, Hawaiian, or Pacific Islander", 9: "White and other"}
SEX = {1: "Male", 2: "Female"}
INSCOV = {1: "Any private, all ages", 2: "Public only, all ages", 3: "Uninsured, all ages"}
INSURC = {1: "<65, Any private", 2: "<65, Public only", 3: "<65, Uninsured",
          4: "65+, Medicare only", 5: "65+, Medicare and private",
          6: "65+, Medicare and other public", 7: "65+, No medicare", 8: "65+, No medicare"}
ROW_VARS = ("ind", "agegrps", "region", "married", "race", "sex", "racesex")
COL_VARS = ("insurance", "ins_lt65", "ins_ge65")


def columns(year):
    yy = "%02d" % (year % 100)
    return ["PERWT%sF" % yy, "VARSTR", "VARPSU", "AGE%sX" % yy, "AGE42X", "AGE31X",
            "REGION%s" % yy, "REGION42", "REGION31", "MARRY%sX" % yy, "MARRY42X", "MARRY31X",
            "RACETHX", "RACEV1X", "SEX", "INSCOV%s" % yy, "INSURC%s" % yy]


def _coalesce_nonneg(*vals):
    """R's coalesce after AHRQ sets negative codes to NA."""
    for v in vals:
        if v is not None and v >= 0:
            return v
    return None


def cut_label(x, breaks, labels):
    """R's cut(x, breaks, labels) with right-closed intervals; None for NA or out of range."""
    if x is None:
        return None
    for lo, hi, lab in zip(breaks[:-1], breaks[1:], labels):
        if lo < x <= hi:
            return lab
    return None


def race_code(racethx, racev1x):
    """add_subgrps.R, years >= 2012: race = 1*hisp + 2*white + 3*black + 4*native + 5*asian."""
    hisp = racethx == 1
    white = racethx == 2
    black = racethx == 3
    big = racethx is not None and racethx > 3
    native = big and racev1x in (3, 6)
    asian = big and racev1x in (4, 5)
    return 1 * hisp + 2 * white + 3 * black + 4 * native + 5 * asian


def derive(rec, year):
    """Subgroup labels for one person record, following AHRQ's add_subgrps.R."""
    yy = "%02d" % (year % 100)
    agelast = _coalesce_nonneg(rec.get("AGE%sX" % yy), rec.get("AGE42X"), rec.get("AGE31X"))
    region = _coalesce_nonneg(rec.get("REGION%s" % yy), rec.get("REGION42"), rec.get("REGION31"))
    married = _coalesce_nonneg(rec.get("MARRY%sX" % yy), rec.get("MARRY42X"), rec.get("MARRY31X"))
    race = RACE.get(race_code(rec.get("RACETHX"), rec.get("RACEV1X")), "Missing")
    sex = SEX.get(rec.get("SEX"), "Missing")
    ages = set()
    for breaks, labels in AGE_CUTS:
        lab = cut_label(agelast, breaks, labels)
        if lab is not None:
            ages.add(lab)
    rows = {
        "ind": {"Total"},
        "agegrps": ages,
        "region": {REGION.get(region, "Missing")},
        "married": {MARRIED.get(married, "Missing")},
        "race": {race},
        "sex": {sex},
        "racesex": {"%s, %s" % (sex, race)},
    }
    cols = {
        "insurance": INSCOV.get(rec.get("INSCOV%s" % yy), "Missing"),
        "ins_lt65": INSURC.get(rec.get("INSURC%s" % yy), "Missing"),
        "ins_ge65": INSURC.get(rec.get("INSURC%s" % yy), "Missing"),
    }
    subset = {
        "insurance": True,
        "ins_lt65": agelast is not None and agelast < 65,
        "ins_ge65": agelast is not None and agelast >= 65,
    }
    return {"PERWT": rec["PERWT%sF" % yy], "VARSTR": rec["VARSTR"], "VARPSU": rec["VARPSU"],
            "agelast": agelast, "rows": rows, "cols": cols, "subset": subset}


def _num(s):
    s = (s or "").strip()
    if s in ("", "NA", "NaN", "."):
        return None
    return float(s)


def read_reference(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def rel_diff(ours, ref):
    if ref == 0:
        return abs(ours)
    return abs(ours - ref) / abs(ref)


def suppress_rule(stat, e):
    """AHRQ's disclosure rule from functions_format.R (as read 2026-11-17): suppress when RSE > 0.5,
    except a percentage under 0.1 (as a 0-1 proportion) with RSE < (0.1/value - 1)/1.96; always suppress
    when the unweighted sample size is under 60 or the SE is 0."""
    if stat.startswith("pct"):
        value, se = e["mean"], e["se_mean"]
    else:
        value, se = e["total"], e["se_total"]
    rse = se / value if value else math.inf
    sup = rse > 0.5
    if stat.startswith("pct") and value < 0.1 and value > 0 and rse < (0.1 / value - 1) / 1.96:
        sup = False
    if e["n"] < 60 or se == 0:
        sup = True
    return sup


def reproduce(recs, ref_rows, value_tol=1e-8, se_tol=1e-6):
    """Compare every attemptable reference row. Returns a report dict."""
    design = me.Design(recs)
    cache = {}

    def cell(row_var, row_level, col_var, col_level):
        key = (row_var, row_level, col_var, col_level)
        if key not in cache:
            if row_level == "All persons":
                dom = lambda r: r["subset"][col_var]
            else:
                dom = lambda r: r["subset"][col_var] and row_level in r["rows"][row_var]
            cache[key] = design.estimate(lambda r: 1.0 if r["cols"][col_var] == col_level else 0.0, dom)
        return cache[key]

    groups = {}
    not_attempted = {}
    mismatches = []
    star_rows = star_agree = 0
    star_disagree = []
    sup_check = {"rows": 0, "agree": 0, "disagree_first50": []}
    for i, row in enumerate(ref_rows):
        stat, rv, rl = row.get("stat_var"), row.get("row_var"), row.get("rowLevels")
        cv, cl = row.get("col_var"), row.get("colLevels")
        value, se, n_ref = _num(row.get("value")), _num(row.get("se")), _num(row.get("sample_size"))
        if stat not in ("totPOP", "pctPOP") or rv not in ROW_VARS or cv not in COL_VARS:
            k = "%s|%s|%s" % (stat, rv, cv)
            not_attempted[k] = not_attempted.get(k, 0) + 1
            continue
        part = "all_persons" if rl == "All persons" else "main"
        g = groups.setdefault("%s|%s|%s" % (part, stat, rv), {
            "rows": 0, "suppressed": 0, "value_ok": 0, "se_ok": 0, "n_ok": 0, "n_checked": 0,
            "max_rel_value": 0.0, "max_rel_se": 0.0})
        g["rows"] += 1
        e = cell(rv, rl, cv, cl)
        if part == "main":
            ours_sup = suppress_rule(stat, e)
            ahrq_sup = value is None or se is None
            sup_check["rows"] += 1
            if ours_sup == ahrq_sup:
                sup_check["agree"] += 1
            elif len(sup_check["disagree_first50"]) < 50:
                sup_check["disagree_first50"].append({"line": i + 2, "stat": stat, "row_var": rv, "row": rl,
                                                      "col": cl, "ahrq_suppressed": ahrq_sup, "n": e["n"],
                                                      "mean": e["mean"], "total": e["total"]})
        if value is None or se is None:
            g["suppressed"] += 1
            continue
        if stat == "totPOP":
            ours_v, ours_se = e["total"] / 1000.0, e["se_total"] / 1000.0
        else:
            ours_v, ours_se = e["mean"] * 100.0, e["se_mean"] * 100.0
        rv_ = rel_diff(ours_v, value)
        rs_ = rel_diff(ours_se, se)
        v_ok = rv_ <= value_tol
        s_ok = rs_ <= se_tol
        n_ok = None
        if n_ref is not None:
            g["n_checked"] += 1
            n_ok = int(n_ref) == e["n"]
            g["n_ok"] += n_ok
        g["value_ok"] += v_ok
        g["se_ok"] += s_ok
        g["max_rel_value"] = max(g["max_rel_value"], rv_)
        g["max_rel_se"] = max(g["max_rel_se"], rs_)
        if not (v_ok and s_ok and n_ok is not False):
            mismatches.append({"line": i + 2, "part": part, "stat": stat, "row_var": rv, "row": rl,
                               "col_var": cv, "col": cl, "ref_value": value, "ours_value": ours_v,
                               "ref_se": se, "ours_se": ours_se, "ref_n": n_ref, "ours_n": e["n"]})
        if part == "main" and value > 0:
            star_rows += 1
            pred = se / value > 0.30
            got = (row.get("asterisk") or "").strip() == "*"
            if pred == got:
                star_agree += 1
            elif len(star_disagree) < 50:
                star_disagree.append({"line": i + 2, "stat": stat, "row": rl, "col": cl,
                                      "value": value, "se": se, "rse": se / value,
                                      "asterisk": row.get("asterisk"), "n": n_ref})
    main = [g for k, g in groups.items() if k.startswith("main|")]
    tot = {k: sum(g[k] for g in main) for k in ("rows", "suppressed", "value_ok", "se_ok", "n_ok", "n_checked")}
    tot["compared"] = tot["rows"] - tot["suppressed"]
    return {
        "design": {"records_used": len(design.rows), "nonpositive": design.n_nonpositive,
                   "strata": design.n_strata, "psus": design.n_psu,
                   "singleton_strata": len(design.singleton_strata), "pop": design.population},
        "tolerances": {"value_rel": value_tol, "se_rel": se_tol},
        "main_totals": tot,
        "groups": groups,
        "not_attempted": not_attempted,
        "reference_rows": len(ref_rows),
        "asterisk_rule_rse_gt_0.30": {"rows": star_rows, "agree": star_agree, "disagree_first50": star_disagree},
        "suppression_rule": sup_check,
        "mismatches_first200": mismatches[:200],
        "mismatch_count": len(mismatches),
        "cells_computed": len(cache),
    }


def zero_only_psus(raw, year):
    """PSUs whose records all have weight <= 0 (R keeps them in the design; meps_est drops them)."""
    yy = "%02d" % (year % 100)
    pos, seen = set(), set()
    for r in raw:
        k = (r["VARSTR"], r["VARPSU"])
        seen.add(k)
        w = r["PERWT%sF" % yy]
        if w is not None and w > 0:
            pos.add(k)
    return len(seen - pos)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("ins", help="reproduce the hc_ins (health insurance) table for one year")
    t.add_argument("--year", type=int, required=True)
    t.add_argument("--data", required=True)
    t.add_argument("--layout", required=True)
    t.add_argument("--ref", required=True, help="formatted_tables/hc_ins/DY<year>.csv")
    t.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    with open(a.layout, encoding="latin-1") as fh:
        layout = me.parse_layout(fh.read())
    want = columns(a.year)
    missing = [c for c in want if c not in layout]
    if missing:
        print("columns not in layout: %s" % ", ".join(missing))
        return 2
    raw = list(me.read_records(a.data, layout, want))
    recs = [derive(r, a.year) for r in raw]
    ref = read_reference(a.ref)
    rep = reproduce(recs, ref)
    rep["meps_tables"] = __version__
    rep["meps_est"] = me.__version__
    rep["year"] = a.year
    rep["records_in_file"] = len(raw)
    rep["zero_weight_only_psus"] = zero_only_psus(raw, a.year)
    rep["agelast_missing"] = sum(1 for r in recs if r["agelast"] is None)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    print("records %d, design %s" % (len(raw), json.dumps(rep["design"], sort_keys=True)))
    print("zero-weight-only PSUs: %d; AGELAST missing: %d" % (rep["zero_weight_only_psus"], rep["agelast_missing"]))
    print("main totals: %s" % json.dumps(rep["main_totals"], sort_keys=True))
    for k in sorted(rep["groups"]):
        print("group %s: %s" % (k, json.dumps(rep["groups"][k], sort_keys=True)))
    print("not attempted: %s" % json.dumps(rep["not_attempted"], sort_keys=True))
    s = rep["asterisk_rule_rse_gt_0.30"]
    print("asterisk rule (RSE > 30%%): %d of %d agree" % (s["agree"], s["rows"]))
    s = rep["suppression_rule"]
    print("suppression rule: %d of %d agree" % (s["agree"], s["rows"]))
    for m_ in rep["mismatches_first200"][:40]:
        print("MISMATCH %s" % json.dumps(m_, sort_keys=True))
    print("mismatch count %d; cells computed %d" % (rep["mismatch_count"], rep["cells_computed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
