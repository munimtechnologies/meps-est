#!/usr/bin/env python3
# meps_guard.py - safeguards, AHRQ disclosure rules and analysis receipts for MEPS estimates.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""A guarded front end to meps_est: every request is checked, every number gets a receipt.

An analysis request is a small JSON object, for example

    {"year": 2022, "stat": "proportion", "variable": "INSURC{yy}",
     "condition": {"op": "==", "value": 3},
     "domain": [{"variable": "AGELAST", "op": "<", "value": 65}],
     "by": {"variable": "REGIONX", "levels": [1, 2, 3, 4]},
     "question": "What share of people under 65 were uninsured all year, by region?"}

Three layers, each of which BLOCKS (no number leaves the module) rather than warns:

1. Request policy (plan_request): requests that the public-use file or the method cannot
   support are refused with a code: geography below Census region, record-level output or
   identifiers, causal claims, individual advice, pooled years (need AHRQ's pooled linkage
   file), unweighted population claims, the wrong weight for SAQ or Diabetes Care Survey
   variables, design variables used as domains, unsupported statistics. The words of the
   question are screened by screen_question(); the structured request is checked separately,
   so a request without a question is still held to every structural rule.
2. Execution rules (execute): MEPS reserved codes (-1 inapplicable, -7 refused, -8 don't know,
   -15 cannot be computed, ...) never satisfy a domain condition (so "-1" is never counted as
   "under 65"); means and proportions leave them out and say how many; a population total is
   refused when some people in the domain have no valid value. Domains keep the full design.
3. Disclosure (disclose): AHRQ's own precision rule from its MEPS-HC summary tables
   (HHS-AHRQ/MEPS-summary-tables, functions_format.R, read 2026-11-17): suppress when the
   unweighted sample size is under 60, the standard error is 0, or the relative standard error
   (RSE) is over 50% (except a proportion under 0.1 whose RSE < (0.1/p - 1)/1.96); mark RSE
   over 30% as unreliable (AHRQ's asterisk). A suppressed cell has no value, SE, interval,
   weighted population or weighted missing share (0.1.1: the last two were returned in 0.1.0,
   and for a count the weighted population IS the suppressed number).

Every allowed answer carries an analysis receipt: the request, the normalized plan, the data
files and their SHA-256, the design (weight, VARSTR, VARPSU, strata, PSUs, degrees of freedom),
the variance method, missing-value handling, results with their disclosure decisions, the
limitations, and a receipt_id (SHA-256 of the canonical receipt) that makes it checkable.
"""
__version__ = "0.1.1"

import hashlib
import json
import math
import platform
import re

import meps_est as me

# MEPS-HC full-year consolidated public-use files.
YEAR_FILES = {2019: "h216", 2020: "h224", 2021: "h233", 2022: "h243"}
DATA_URL = "https://meps.ahrq.gov/mepsweb/data_files/pufs/{h}/{h}dat.zip"
LAYOUT_URL = "https://meps.ahrq.gov/mepsweb/data_stats/download_data/pufs/{h}/{h}su.txt"

STATS = ("mean", "total", "proportion", "count")
OPS = ("==", "!=", "<", "<=", ">", ">=", "in", "not in")
ALLOWED_KEYS = {"question", "year", "years", "stat", "variable", "condition", "domain", "by",
                "compare", "weight", "unweighted"}
MAX_LEVELS = 60
MIN_N = 60
RSE_SUPPRESS = 0.5
RSE_FLAG = 0.3

# Reserved codes in MEPS-HC public-use files (h243 documentation, Table 2).
RESERVED = frozenset((-1, -2, -7, -8, -9, -10, -13, -14, -15))

# Variables AHRQ's summary tables build by coalescing round-specific values (add_subgrps.R):
# the first non-negative of the end-of-year, round 4/2 and round 3/1 values.
DERIVED = {
    "AGELAST": ("AGE{YY}X", "AGE42X", "AGE31X"),
    "REGIONX": ("REGION{YY}", "REGION42", "REGION31"),
}

# h243 documentation: "The variables created from the SAQ begin with 'AD,' except SAQELIG";
# SAQ summary scores without the prefix; DCS variables are listed by name and all begin "DS".
SAQ_EXTRA = ("K6SUM42", "PHQ242", "VPCS42", "VMCS42", "VRFLAG42")
SUBSAMPLE_WEIGHT = {"FULL": "PERWT{YY}F", "SAQ": "SAQWT{YY}F", "DCS": "DIABW{YY}F"}

ID_NAMES = ("DUPERSID", "DUID", "PID", "HIEUIDX")
ID_PREFIXES = ("FAMID", "CPSFAMID", "RULETR")
DESIGN_NAMES = ("VARSTR", "VARPSU")
GEO_NAME_RE = re.compile(r"(STATE|COUNTY|FIPS|ZIP|MSA|CBSA|TRACT)")

# ---------------------------------------------------------------- question screen
# 0.1.0 patterns (kept unchanged) ...
US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington state", "west virginia", "wisconsin", "wyoming", "puerto rico")
GEO_RE = re.compile(
    r"\b(state[- ]level|states?[- ]by[- ]state|by state|each state|per state|every state|which states?|"
    r"county|counties|zip ?codes?|census tracts?|city|cities|metro(politan)? areas?|msas?|fips|"
    + "|".join(re.escape(s) for s in US_STATES) + r")\b", re.I)
CAUSAL_RE = re.compile(
    r"\b(effects? of|causal(ly)?|impacts? of|causes?|caused by|leads? to|led to|"
    r"(would|will) (reduce|increase|lower|raise))\b", re.I)
RECORD_RE = re.compile(
    r"\b(list (the |all )?(people|persons|respondents|records|individuals|patients)|"
    r"which (person|respondent|individual|patient|household)|re-?identif\w*|"
    r"identify (the |a |which )?(person|respondent|individual|patient|household)|"
    r"(individual|person|respondent)[- ]level (data|records?|rows?)|raw records?|"
    r"(highest|lowest|largest|smallest|top|bottom)[- ](spender|person|patient|individual|respondent)s?)\b", re.I)
ADVICE_RE = re.compile(
    r"\b(should i|will i|am i|my (risk|chance|odds|cost|costs|premium|diagnosis|health)|"
    r"(diagnose|treat) me|for me)\b", re.I)

# ... and the 0.1.1 additions (each code is refused if either pattern matches).
CITY_NAMES = ("new york city", "nyc", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
              "san antonio", "san diego", "dallas", "san jose", "san francisco", "seattle", "denver",
              "boston", "detroit", "atlanta", "miami", "baltimore", "bay area", "silicon valley",
              "brooklyn", "manhattan", "the bronx")
DEMONYMS = ("texans", "floridians", "californians", "new yorkers", "ohioans", "michiganders",
            "pennsylvanians", "oregonians", "arizonans", "coloradans", "virginians", "minnesotans",
            "hoosiers", "georgians", "alaskans", "hawaiians", "louisianans", "kentuckians",
            "tennesseans", "new jerseyans")
GEO2_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in CITY_NAMES + DEMONYMS) + r"|"
    r"(largest|smallest|biggest|individual|different|top|several|particular|specific|other|"
    r"neighbou?ring|red|blue|swing|expansion|non-?expansion|poorest|richest)\s+states|"
    r"(all|the)\s+(50|fifty)\s+states|across\s+(the\s+)?states|(?<!united )states\s+(with|that|where)|"
    r"medicaid\s+expansion|census\s+(divisions?|tracts?|blocks?)|"
    r"neighbou?rhoods?|towns?|zip|postal\s+codes?|near\s+me|my\s+area|local\s+area|"
    r"metro|metropolitan|non-?metropolitan|nonmetro|urban|rural|suburban|suburbs?|"
    r"district\s+of\s+columbia|washington,?\s+d\.?c)\b", re.I)
CAUSAL2_RE = re.compile(
    r"\b(impacts?\s+on|affects?|affecting|influences?|why|what\s+would\s+happen|"
    r"what\s+happens\s+(to|if|when)|"
    r"result(s|ed|ing)?\s+in\s+(a\s+|an\s+)?(higher|lower|more|less|greater|fewer|increased|decreased|reduced)|"
    r"responsible\s+for|attributable\s+to|driven\s+by|thanks\s+to|as\s+a\s+result\s+of|"
    r"(reduces|reduced|cuts|drives|drove|lowers|raises|increases|decreases)\s+(health|medical|total|"
    r"out[- ]of[- ]pocket|spending|costs?|expenses|expenditures?|use|utilization|visits|"
    r"the\s+(cost|risk|odds|chance|number|likelihood))|"
    r"(does|do|did|would|will|could|can|might)\s+\w+(\s+\w+){0,4}\s+(reduce|cut|prevent|save|worsen|drive)|"
    r"if\s+[^.?!]{0,80}\b(would|will))\b", re.I)
RECORD2_RE = re.compile(
    r"\b((the\s+)?(person|people|patient|respondent|individual|household|family|families|one|ones)s?\s+"
    r"(who|that)\s+(spent|spend|had|has|paid|pay|used|use|incurred)\s+(the\s+)?"
    r"(most|least|highest|lowest|largest|biggest|smallest)|"
    r"who\s+(spent|paid|had|used)\s+the\s+(most|least|highest|lowest|largest|biggest)|"
    r"most\s+expensive\s+(person|patient|respondent|individual|household|family|case)s?|"
    r"(top|bottom)\s+\d+\s+(people|persons|patients|respondents|individuals|spenders|households|families|cases)|"
    r"(show|give|export|dump|download|print|return|send|output)(\s+me)?(\s+the|\s+all)?"
    r"(\s+(raw|individual|underlying|all|person|respondent))?\s+(rows|records|microdata|row[- ]level|"
    r"record[- ]level|data\s+rows)|"
    r"dupersids?|person\s+ids?|respondent\s+ids?|"
    r"(oldest|youngest|richest|poorest|sickest|heaviest)\s+(person|respondent|patient|individual)|"
    r"single\s+(person|respondent|patient|individual))\b", re.I)
ADVICE2_RE = re.compile(
    r"\b(my\s+(spending|expenses?|bills?|insurance|plan|policy|coverage|family|kids?|children|child|son|"
    r"daughter|mother|father|mom|dad|wife|husband|partner|parents?|doctor|deductible|premiums?|"
    r"out[- ]of[- ]pocket|medications?|prescriptions?)|"
    r"(i\s+am|i'm|im)\s+(a\s+|an\s+)?\d{1,3}|i\s+(have|had|was\s+diagnosed|take|need|got|pay|spend|spent)|"
    r"what\s+will\s+(i|my)|how\s+much\s+(will|should|would)\s+(i|we|my)|"
    r"should\s+(my|our|we\s+(buy|get|switch|enroll|choose|drop|cancel))|"
    r"(is|would)\s+it\s+(be\s+)?(worth|better|cheaper)\s+(it\s+)?for\s+(me|us|my)|"
    r"for\s+someone\s+like\s+me|people\s+like\s+me)\b", re.I)

QUESTION_SCREEN = (
    ("GEO_BELOW_REGION", (GEO_RE, GEO2_RE)),
    ("CAUSAL_CLAIM", (CAUSAL_RE, CAUSAL2_RE)),
    ("RECORD_LEVEL", (RECORD_RE, RECORD2_RE)),
    ("INDIVIDUAL_ADVICE", (ADVICE_RE, ADVICE2_RE)),
)

POLICY = {
    "guard_version": __version__,
    "refusal_codes": {
        "BAD_REQUEST": "The request is not well formed; nothing is computed.",
        "UNSUPPORTED_YEAR": "Only the MEPS-HC full-year consolidated files listed in years_supported.",
        "POOLED_YEARS": "Pooling years needs AHRQ's pooled variance linkage file (HC-036) and weights "
                        "divided by the number of years; not supported yet, so it is refused.",
        "UNSUPPORTED_STAT": "Medians and percentiles need a different variance method; not supported.",
        "RECORD_LEVEL": "Record-level output, identifiers, extremes (min/max) or 'which person' "
                        "questions can identify people and are refused.",
        "GEO_BELOW_REGION": "MEPS-HC public-use files identify only the four Census regions; state, "
                            "county, city, metro/nonmetro, urban/rural, Census division and state-policy "
                            "groupings are refused.",
        "CAUSAL_CLAIM": "Survey tabulations show associations, not effects; causal wording is refused. "
                        "Rephrase descriptively (e.g. 'mean spending by insurance status').",
        "INDIVIDUAL_ADVICE": "Population estimates are not advice for an individual; refused.",
        "UNWEIGHTED": "Population statements need the survey weights and design; unweighted is refused.",
        "WRONG_WEIGHT": "SAQ variables need SAQWTyyF and Diabetes Care Survey variables DIABWyyF "
                        "(h243 documentation); another weight is refused.",
        "MIXED_SUBSAMPLES": "SAQ and DCS variables have different weights and cannot be combined.",
        "DESIGN_VARIABLE": "VARSTR/VARPSU and weights are design variables, not analysis or domain variables.",
        "TOO_MANY_LEVELS": "A breakdown may have at most %d listed levels." % MAX_LEVELS,
        "UNKNOWN_VARIABLE": "The variable is not in that year's file layout.",
        "NOT_NUMERIC": "Means and totals need a numeric variable.",
        "MISSING_IN_TOTAL": "A population total is refused when some people in the domain have no valid "
                            "value (reserved code); restrict the domain to valid values or use a mean.",
        "QUESTION_REQUIRED": "The MCP server needs the user's question in words so the question screen "
                             "can check it; a request without one is refused there.",
    },
    "disclosure_rule": {
        "source": "AHRQ MEPS-HC summary tables, github.com/HHS-AHRQ/MEPS-summary-tables "
                  "(functions_format.R), read 2026-11-17",
        "suppress": "unweighted n < %d; or SE == 0; or RSE > %.2f, except a proportion p < 0.1 with "
                    "RSE < (0.1/p - 1)/1.96" % (MIN_N, RSE_SUPPRESS),
        "flag": "RSE > %.2f: shown with an asterisk as potentially unreliable" % RSE_FLAG,
        "suppressed_cells": "no value, SE, interval, weighted population or weighted missing share is "
                            "returned for a suppressed cell",
    },
    "question_screen": "Word patterns for each of GEO_BELOW_REGION, CAUSAL_CLAIM, RECORD_LEVEL and "
                       "INDIVIDUAL_ADVICE. A pattern screen is defense in depth, not a guarantee: the "
                       "structured request is checked independently, and the public-use file has no "
                       "geography below region and no way to return records through this server.",
    "missing_values": "Reserved codes %s and blanks are missing: they never satisfy a domain "
                      "condition, are left out of means and proportions (and counted in the receipt), "
                      "and make a population total refused." % sorted(RESERVED),
    "years_supported": {str(y): h.upper() for y, h in sorted(YEAR_FILES.items())},
    "derived_variables": {k: "first non-negative of " + ", ".join(v) for k, v in DERIVED.items()},
}

SUGGESTIONS = {
    "GEO_BELOW_REGION": "Ask by Census region (REGIONX: 1 Northeast, 2 Midwest, 3 South, 4 West).",
    "CAUSAL_CLAIM": "Ask for a descriptive comparison, e.g. mean spending by insurance status.",
    "INDIVIDUAL_ADVICE": "Ask about a population group, e.g. mean out-of-pocket spending for adults 45-64 "
                         "with diabetes; the answer is not advice for one person.",
}

LIMITATIONS = [
    "MEPS-HC represents the U.S. civilian noninstitutionalized population; people in nursing homes, "
    "prisons or on active military duty are not covered.",
    "Estimates are descriptive and show associations, not causal effects.",
    "Standard errors use Taylor-series linearization with the public-use VARSTR and VARPSU, the method "
    "of AHRQ's summary tables (R survey: svydesign(id=~VARPSU, strata=~VARSTR, weights=~weight, nest=TRUE)).",
    "95% intervals use a normal approximation; for proportions near 0 or 1 or for small domains they can "
    "be too narrow. The design degrees of freedom are given in the receipt.",
    "Dollar amounts are nominal dollars of the data year.",
    "Reserved codes are treated as missing; if many people in a domain are missing, the estimate describes "
    "only those with valid values (counts are in each result).",
]


def _yy(year):
    return "%02d" % (year % 100)


def expand(name, yy):
    """Upper-case a variable name and fill a {yy} year placeholder."""
    return str(name).upper().replace("{YY}", yy)


def subsample(var):
    v = var.upper()
    if v in ("SAQELIG", "DCSELIG"):
        return "FULL"
    if v.startswith("AD") or v in SAQ_EXTRA:
        return "SAQ"
    if v.startswith("DS"):
        return "DCS"
    return "FULL"


def is_missing(x):
    if x is None:
        return True
    if isinstance(x, str):
        return x.strip() == ""
    if isinstance(x, float) and math.isnan(x):
        return True
    return x in RESERVED


def compare_op(x, op, val):
    """A domain or proportion condition. Missing values never satisfy any condition."""
    if is_missing(x):
        return False
    if op == "==":
        return x == val
    if op == "!=":
        return x != val
    if op == "in":
        return x in val
    if op == "not in":
        return x not in val
    try:
        if op == "<":
            return x < val
        if op == "<=":
            return x <= val
        if op == ">":
            return x > val
        if op == ">=":
            return x >= val
    except TypeError:
        return False
    raise ValueError("unknown operator %r" % op)


def get_value(rec, var, yy):
    parts = DERIVED.get(var)
    if parts is None:
        return rec.get(var)
    for p in parts:
        x = rec.get(p.replace("{YY}", yy))
        if x is not None and not isinstance(x, str) and x >= 0:
            return x
    return None


def columns_for(var, yy):
    parts = DERIVED.get(var)
    if parts is None:
        return [var]
    return [p.replace("{YY}", yy) for p in parts]


# ---------------------------------------------------------------- layer 1: request policy

def screen_question(question):
    """Refusal codes the words of a question trigger, in a fixed order (empty list = none)."""
    q = " ".join(str(question or "").split())
    if not q:
        return []
    return [code for code, pats in QUESTION_SCREEN if any(p.search(q) for p in pats)]


def _check_condition(c, where, refuse):
    if not isinstance(c, dict) or c.get("op") not in OPS or "value" not in c:
        refuse("BAD_REQUEST", "%s needs {'op': one of %s, 'value': ...}" % (where, list(OPS)))
        return False
    if c["op"] in ("in", "not in") and not isinstance(c["value"], list):
        refuse("BAD_REQUEST", "%s: 'in' needs a list of values" % where)
        return False
    if c["op"] not in ("in", "not in") and not isinstance(c["value"], (int, float, str)):
        refuse("BAD_REQUEST", "%s: the value must be a number or a string" % where)
        return False
    return True


def plan_request(req):
    """Check a request against the policy. Returns (plan or None, refusals)."""
    refusals = []

    def refuse(code, message, suggestion=""):
        item = {"code": code, "message": message}
        if suggestion:
            item["suggestion"] = suggestion
        if item not in refusals:
            refusals.append(item)

    if not isinstance(req, dict):
        refuse("BAD_REQUEST", "the request must be a JSON object")
        return None, refusals
    extra = sorted(set(req) - ALLOWED_KEYS)
    if extra:
        refuse("BAD_REQUEST", "unknown keys: %s" % ", ".join(extra))

    q = str(req.get("question") or "")
    for code in screen_question(q):
        refuse(code, POLICY["refusal_codes"][code], SUGGESTIONS.get(code, ""))

    years = req.get("years")
    if years is not None:
        if isinstance(years, list) and len(set(years)) > 1:
            refuse("POOLED_YEARS", POLICY["refusal_codes"]["POOLED_YEARS"],
                   "Ask for one year at a time; differences between years are not yet supported.")
        elif isinstance(years, list) and len(years) == 1 and "year" not in req:
            req = dict(req, year=years[0])
    year = req.get("year")
    if not isinstance(year, int) or isinstance(year, bool) or year not in YEAR_FILES:
        if not any(r["code"] == "POOLED_YEARS" for r in refusals):
            refuse("UNSUPPORTED_YEAR", "year must be one of %s" % sorted(YEAR_FILES))
        year = None
    yy = _yy(year) if year else "{YY}"

    stat = str(req.get("stat") or "").lower()
    if stat in ("median", "percentile", "quantile", "quartile"):
        refuse("UNSUPPORTED_STAT", POLICY["refusal_codes"]["UNSUPPORTED_STAT"])
    elif stat in ("max", "min", "maximum", "minimum", "list", "records", "rows", "top", "bottom"):
        refuse("RECORD_LEVEL", POLICY["refusal_codes"]["RECORD_LEVEL"])
    elif stat not in STATS:
        refuse("BAD_REQUEST", "stat must be one of %s" % list(STATS))

    w_req = req.get("weight")
    if req.get("unweighted") or (isinstance(w_req, str) and w_req.strip().lower() in ("none", "unweighted", "1", "")):
        refuse("UNWEIGHTED", POLICY["refusal_codes"]["UNWEIGHTED"])
        w_req = None

    used = []          # (role, variable)
    variable = None
    if stat != "count":
        if not req.get("variable"):
            if stat in STATS:
                refuse("BAD_REQUEST", "a %s needs 'variable'" % stat)
        else:
            variable = expand(req["variable"], yy)
            used.append(("analysis", variable))
    condition = None
    if stat == "proportion":
        if _check_condition(req.get("condition"), "condition", refuse):
            condition = {"op": req["condition"]["op"], "value": req["condition"]["value"]}
    elif req.get("condition") is not None:
        refuse("BAD_REQUEST", "'condition' is only used with stat 'proportion'")

    domain = []
    dom = req.get("domain") or []
    if not isinstance(dom, list):
        refuse("BAD_REQUEST", "'domain' must be a list of conditions")
        dom = []
    for i, c in enumerate(dom):
        if not isinstance(c, dict) or not c.get("variable"):
            refuse("BAD_REQUEST", "domain[%d] needs a 'variable'" % i)
            continue
        if _check_condition(c, "domain[%d]" % i, refuse):
            v = expand(c["variable"], yy)
            domain.append({"variable": v, "op": c["op"], "value": c["value"]})
            used.append(("domain", v))

    by = None
    if req.get("by") is not None:
        b = req["by"]
        if not isinstance(b, dict) or not b.get("variable") or not isinstance(b.get("levels"), list) or not b["levels"]:
            refuse("BAD_REQUEST", "'by' needs {'variable': ..., 'levels': [listed values]}")
        elif len(b["levels"]) > MAX_LEVELS:
            refuse("TOO_MANY_LEVELS", POLICY["refusal_codes"]["TOO_MANY_LEVELS"])
        else:
            labels = b.get("labels") or {}
            by = {"variable": expand(b["variable"], yy), "levels": list(b["levels"]),
                  "labels": {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}}
            used.append(("by", by["variable"]))

    compare = None
    if req.get("compare") is not None:
        c = req["compare"]
        if by is None or not isinstance(c, list) or len(c) != 2 or c[0] not in by["levels"] or c[1] not in by["levels"] or c[0] == c[1]:
            refuse("BAD_REQUEST", "'compare' needs two different levels listed in 'by'")
        else:
            compare = [c[0], c[1]]

    for role, v in used:
        if v in ID_NAMES or v.startswith(ID_PREFIXES):
            refuse("RECORD_LEVEL", "%s is an identifier; %s" % (v, POLICY["refusal_codes"]["RECORD_LEVEL"]))
        elif GEO_NAME_RE.search(v):
            refuse("GEO_BELOW_REGION", "%s: %s" % (v, POLICY["refusal_codes"]["GEO_BELOW_REGION"]))
        elif v in DESIGN_NAMES or re.match(r"^(PERWT|SAQWT|DIABW|FAMWT|FAMWGT)", v):
            refuse("DESIGN_VARIABLE", "%s: %s" % (v, POLICY["refusal_codes"]["DESIGN_VARIABLE"]))

    subs = sorted(set(subsample(v) for _, v in used) - {"FULL"})
    if len(subs) > 1:
        refuse("MIXED_SUBSAMPLES", POLICY["refusal_codes"]["MIXED_SUBSAMPLES"])
        sub = "FULL"
    else:
        sub = subs[0] if subs else "FULL"
    weight = SUBSAMPLE_WEIGHT[sub].replace("{YY}", yy)
    if w_req is not None and expand(w_req, yy) != weight:
        refuse("WRONG_WEIGHT", "%s requested, but these variables need %s. %s" % (
            expand(w_req, yy), weight, POLICY["refusal_codes"]["WRONG_WEIGHT"]))

    if refusals:
        return None, refusals
    names = sorted(set(v for _, v in used))
    plan = {"year": year, "yy": yy, "puf": YEAR_FILES[year].upper(), "stat": stat,
            "variable": variable, "condition": condition, "domain": domain, "by": by,
            "compare": compare, "weight": weight, "subsample": sub, "variables": names,
            "question": q}
    return plan, []


def plan_columns(plan):
    cols = [plan["weight"], "VARSTR", "VARPSU"]
    for v in plan["variables"]:
        for c in columns_for(v, plan["yy"]):
            if c not in cols:
                cols.append(c)
    return cols


def check_layout(plan, layout):
    """Refusals for variables missing from the layout or character variables in a mean/total."""
    refusals = []
    for c in plan_columns(plan):
        if c not in layout:
            refusals.append({"code": "UNKNOWN_VARIABLE",
                             "message": "%s is not in the %d file layout (%s)" % (c, plan["year"], plan["puf"])})
    v = plan["variable"]
    if not refusals and plan["stat"] in ("mean", "total") and v not in DERIVED and layout[v][3]:
        refusals.append({"code": "NOT_NUMERIC", "message": "%s is a character variable" % v})
    return refusals


# ---------------------------------------------------------------- layer 3: disclosure

def disclose(stat, value, se, n):
    """AHRQ's MEPS-HC summary-table rule. value is a proportion on the 0-1 scale for 'proportion'."""
    reasons = []
    if n < MIN_N:
        reasons.append("unweighted sample size %d is under %d" % (n, MIN_N))
    bad = value is None or se is None or math.isnan(value) or math.isnan(se)
    if bad:
        reasons.append("the estimate is undefined (empty domain)")
        rse = math.inf
    else:
        if se == 0:
            reasons.append("the standard error is 0")
        rse = se / abs(value) if value else math.inf
        keep_small_p = stat == "proportion" and 0 < value < 0.1 and rse < (0.1 / value - 1) / 1.96
        if rse > RSE_SUPPRESS and not keep_small_p:
            reasons.append("relative standard error %.2f is over %.2f" % (rse, RSE_SUPPRESS))
    if reasons:
        return {"status": "suppressed", "reasons": reasons, "rse": None if math.isinf(rse) else rse}
    if rse > RSE_FLAG:
        return {"status": "flagged", "reasons": ["relative standard error %.2f is over %.2f: potentially "
                                                 "unreliable (AHRQ asterisk)" % (rse, RSE_FLAG)], "rse": rse}
    return {"status": "ok", "reasons": [], "rse": rse}


# ---------------------------------------------------------------- layer 2: execution

def domain_difference(design, y, dom_a, dom_b, kind):
    """Difference A - B of two domain means ('mean') or totals ('total') from one design, with the
    linearized SE that keeps their covariance (both domains share strata and PSUs)."""
    vals = [(w, y(r), bool(dom_a(r)), bool(dom_b(r))) for w, _, _, r in design.rows]
    if kind == "mean":
        wa = math.fsum(w for w, _, a, _ in vals if a)
        wb = math.fsum(w for w, _, _, b in vals if b)
        ma = math.fsum(w * v for w, v, a, _ in vals if a) / wa
        mb = math.fsum(w * v for w, v, _, b in vals if b) / wb
        z = [(w * (v - ma) / wa if a else 0.0) - (w * (v - mb) / wb if b else 0.0) for w, v, a, b in vals]
        diff = ma - mb
    else:
        z = [w * v * (1.0 if a else 0.0) - w * v * (1.0 if b else 0.0) for w, v, a, b in vals]
        diff = math.fsum(z)
    se = math.sqrt(design._variance(z))
    return diff, se


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def receipt_id(receipt):
    body = {k: v for k, v in receipt.items() if k != "receipt_id"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def execute(plan, records, provenance=None):
    """Run an allowed plan on records (dicts with plan_columns(plan)). Returns a response dict with
    status 'ok' (results + receipt) or 'refused' (refusals, no numbers)."""
    yy = plan["yy"]
    stat = plan["stat"]
    var = plan["variable"]
    design = me.Design(records, weight=plan["weight"])
    base = [(c["variable"], c["op"], c["value"]) for c in plan["domain"]]

    def in_base(r):
        return all(compare_op(get_value(r, v, yy), op, val) for v, op, val in base)

    def has_value(r):
        return stat == "count" or not is_missing(get_value(r, var, yy))

    def level_domain(level):
        if plan["by"] is None:
            return in_base
        bv = plan["by"]["variable"]
        return lambda r: in_base(r) and compare_op(get_value(r, bv, yy), "==", level)

    if stat == "proportion":
        cop, cval = plan["condition"]["op"], plan["condition"]["value"]

        def y(r):
            return 1.0 if compare_op(get_value(r, var, yy), cop, cval) else 0.0
    elif stat == "count":
        def y(r):
            return 1.0
    else:
        def y(r):
            x = get_value(r, var, yy)
            return 0.0 if is_missing(x) else float(x)

    levels = plan["by"]["levels"] if plan["by"] else [None]
    results, refusals, domains = [], [], {}
    for level in levels:
        dom_all = level_domain(level)
        n_all = w_all = n_miss = 0
        w_miss = 0.0
        for w, _, _, r in design.rows:
            if dom_all(r):
                n_all += 1
                w_all += w
                if not has_value(r):
                    n_miss += 1
                    w_miss += w
        if stat == "total" and n_miss:
            refusals.append({"code": "MISSING_IN_TOTAL", "message": "%d people in the domain%s have no valid %s. %s" % (
                n_miss, "" if level is None else " (level %r)" % level, var, POLICY["refusal_codes"]["MISSING_IN_TOTAL"]),
                "suggestion": "add {'variable': '%s', 'op': '>=', 'value': 0} to the domain, or ask for a mean" % var})
            continue

        def dom(r, dom_all=dom_all):
            return dom_all(r) and has_value(r)
        domains[level if level is not None else "_all"] = dom
        e = design.estimate(y, dom)
        if stat in ("count", "total"):
            value, se = e["total"], e["se_total"]
        else:
            value, se = e["mean"], e["se_mean"]
        dec = disclose(stat, value, se, e["n"])
        res = {"level": level, "label": (plan["by"]["labels"].get(str(level), str(level)) if plan["by"] else "All"),
               "n": e["n"], "n_domain_before_missing": n_all, "n_excluded_missing": n_miss,
               "weighted_share_excluded": (w_miss / w_all) if w_all else None,
               "population_weighted": _clean(e["pop"]), "disclosure": dec}
        if dec["status"] == "suppressed":
            # 0.1.1: no weighted quantity of a suppressed cell leaves the module (for a count the
            # weighted population is the suppressed estimate itself).
            res.update({"value": None, "se": None, "ci95": None, "population_weighted": None,
                        "weighted_share_excluded": None})
        else:
            res.update({"value": value, "se": se, "ci95": [value - 1.96 * se, value + 1.96 * se],
                        "asterisk": dec["status"] == "flagged"})
            if stat == "proportion":
                res["percent"] = value * 100.0
        results.append(res)
    if refusals:
        return {"status": "refused", "refusals": refusals, "guard": __version__}

    comparison = None
    if plan["compare"]:
        a, b = plan["compare"]
        ra = next(r for r in results if r["level"] == a)
        rb = next(r for r in results if r["level"] == b)
        if ra["value"] is None or rb["value"] is None:
            comparison = {"a": a, "b": b, "status": "suppressed",
                          "reasons": ["a compared cell is suppressed, so the difference is not shown"]}
        else:
            kind = "total" if stat in ("count", "total") else "mean"
            diff, se = domain_difference(design, y, domains[a], domains[b], kind)
            zstat = diff / se if se > 0 else None
            comparison = {"a": a, "b": b, "status": "ok", "difference": diff, "se": se,
                          "ci95": [diff - 1.96 * se, diff + 1.96 * se], "z": zstat,
                          "p_two_sided_normal": math.erfc(abs(zstat) / math.sqrt(2)) if zstat is not None else None,
                          "note": "A minus B; the SE keeps the covariance of the two domains. A descriptive "
                                  "difference, not a causal effect."}

    receipt = {
        "receipt_version": 1,
        "software": {"meps_guard": __version__, "meps_est": me.__version__,
                     "python": platform.python_version()},
        "plan": plan,
        "data": dict(provenance or {}, survey="MEPS Household Component, full-year consolidated public-use file",
                     year=plan["year"], puf=plan["puf"]),
        "design": {"weight": plan["weight"], "strata": "VARSTR", "psu": "VARPSU",
                   "records_read": design.n_records, "records_positive_weight": len(design.rows),
                   "records_weight_le_0_left_out": design.n_nonpositive, "strata_count": design.n_strata,
                   "psu_count": design.n_psu, "singleton_strata": len(design.singleton_strata),
                   "degrees_of_freedom": design.n_psu - design.n_strata,
                   "variance": "Taylor-series linearization, with-replacement first stage; domains keep the "
                               "full design (no subsetting)"},
        "policy": {"disclosure_rule": POLICY["disclosure_rule"], "missing_values": POLICY["missing_values"],
                   "checks_passed": sorted(POLICY["refusal_codes"])},
        "results": results,
        "comparison": comparison,
        "limitations": LIMITATIONS,
    }
    receipt = json.loads(_canonical(receipt))
    receipt["receipt_id"] = receipt_id(receipt)
    return {"status": "ok", "receipt_id": receipt["receipt_id"], "stat": stat, "results": receipt["results"],
            "comparison": comparison, "receipt": receipt}


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


LABEL_RE = re.compile(r"^\s*(?:LABEL\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([\"'])(.*?)\2", re.M)


def parse_labels(text):
    """Variable labels from SAS LABEL statements, if the programming statements have them."""
    return {m.group(1).upper(): m.group(3).strip() for m in LABEL_RE.finditer(text)}
