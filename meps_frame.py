#!/usr/bin/env python3
# meps_frame.py - the descriptive answer frame for guarded MEPS estimates (an allowlist, not a denylist).
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""Structural backstop for causal wording, personal advice and unverifiable quantities.

meps_verify blocks answers whose numbers do not trace to a receipt, and it blocks causal wording
and advice with word lists. Word lists do not generalize: a blind test of the question screen
(meps-blind, 2026-11-19) predicted that paraphrases slip through. This module closes that gap the
other way round, with an ALLOWLIST:

    render_answer(receipts)        a deterministic, purely descriptive answer built only from the
                                   receipts (every number traceable, suppressed cells say so,
                                   flagged cells carry AHRQ's asterisk and a caveat).
    check_frame(text, receipts)    every word of a free-text draft must come from a fixed
                                   descriptive vocabulary (VOCAB) or from the variable names in the
                                   receipts; anything else (causal verbs, modals, "you/your",
                                   number words, other languages, URLs) is OFF_FRAME_WORDING.
    finalize_answer(draft, receipts)
                                   what an agent may show: the draft if it passes meps_verify AND
                                   the frame; otherwise the rendered answer (if it passes both);
                                   otherwise nothing. A warning never substitutes for blocking.

Known limits, stated so nobody relies on them: direction words ("higher", "lower", "fewer") are
allowed but are not checked against the numbers; the frame cannot stop every implied causal
reading of two descriptive sentences placed side by side; non-English answers are always replaced
by the English rendered answer.

    python meps_frame.py --draft draft.txt --receipt r1.json [--receipt r2.json ...]

prints the JSON decision and exits 0 (draft passed), 1 (replaced by the rendered answer) or
2 (blocked: nothing may be shown).
"""
__version__ = "0.1.0"

import argparse
import json
import re
import sys

import meps_verify as mv

OPS_WORDS = {"==": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "in": "in", "not in": "not in"}

# Fixed sentences the renderer uses verbatim. A draft may repeat them exactly; they are checked by
# meps_verify like everything else but are exempt from the vocabulary (they say "not causes").
FIXED_SCOPE = ("These are survey-weighted descriptive estimates for the U.S. civilian noninstitutionalized "
               "population from AHRQ's Medical Expenditure Panel Survey; they describe associations, not "
               "causes, and are not advice for any individual.")
FIXED_DIFF = "This is a descriptive difference, not a causal effect."
FIXED_FLAG = ("* marks an estimate with a relative standard error over 30% (AHRQ's flag for potentially "
              "unreliable estimates).")
FIXED_SUPP = ("Suppressed cells are not shown under AHRQ's disclosure rule (fewer than 60 people in the "
              "sample, or a relative standard error over 50%).")
FIXED = (FIXED_SCOPE, FIXED_DIFF, FIXED_FLAG, FIXED_SUPP)

# The descriptive vocabulary. Written from the renderer's needs and MEPS's own subject matter
# BEFORE any held-out answer set was written (see prereg-meps-frame.md). Deliberately absent:
# causal verbs and nouns, modals and conditionals, second/first person, recommendation verbs,
# number words and vague quantifiers (which meps_verify cannot ground).
VOCAB = frozenset("""
a an the of in on at for to from by with within among between across and or but as than that this these
those which who whom whose what when where there it its they their them each every all both either neither
any some no not none nor only also about around approximately roughly over under above below up down per
out into while whereas same similar different difference differences other another more less fewer higher
highest lower lowest larger largest smaller smallest greater greatest least
is are was were be been has have had do does did reported report reports reporting answered stated
spent spend spending paid pay paying covered cover used use using visited visit received filled
lived living aged show shows shown showed describe describes described represent represents represented
compare compared comparing comparison includes include included including excluding excluded exclude
observed measured weighted counted ranged range ranges differed differs cannot refused refuses supported
unsupported available unavailable
percent percentage percentages point points pp share shares proportion proportions rate rates mean average
averaged median total totals sum count counts number numbers people persons person adults adult children
child population populations civilian noninstitutionalized household households family families national
nationally nationwide united states u s america year years annual annually yearly full whole calendar
interval intervals confidence ci se standard error errors relative rse unweighted weighted weight weights
sample sampled size sizes survey surveys respondents respondent design strata stratum psu psus variance
linearization taylor series estimate estimated estimates estimator statistic statistics statistically
significant significantly value values level levels category categories group groups domain domains
subgroup subgroups row rows table tables cell cells column columns receipt receipts id ids file files data
public puf meps medical expenditure expenditures panel ahrq agency healthcare research quality component
consolidated disclosure rule rules suppressed suppression withheld unreliable reliable reliability
precise imprecise precision caution care interpret interpreted interpretation asterisk flagged flag
potentially caveat note noted limitation limitations descriptive description association associations
associated correlation correlated minus plus
health insurance insured uninsured coverage private public medicare medicaid tricare chip program programs
plan plans employer employment employed unemployed work workers worker job self income poverty poor near
low middle high line federal expense expenses cost costs dollars dollar payment payments payer payers
source sources pocket premium premiums prescription prescriptions drug drugs medicine medicines
medications office visits outpatient inpatient hospital hospitals stay stays emergency room department
dental dentist vision home care provider providers physician physicians doctor doctors nurse services
service condition conditions chronic diabetes asthma hypertension cancer heart disease diseases arthritis
depression anxiety mental physical status rated excellent very good fair limitation activity activities
difficulty disability functional age ages older younger old young senior seniors elderly infants infant
teen teens adolescents sex male female males females men women boys girls race ethnicity hispanic latino
non white black asian native pacific islander multiple races region regions northeast midwest south west
census education educational degree school college bachelor marital married widowed divorced separated
never single
january february march april june july august september october november december
""".split())

# Words a free-text draft may never use, whatever VOCAB says (checked by the tests, and used to
# name the category of an off-frame word in findings).
DENY = {
    "causal": ("cause", "causes", "caused", "causing", "causal", "causally", "effect", "effects", "impact",
               "impacts", "affect", "affects", "affected", "influence", "influences", "lead", "leads", "led",
               "drive", "drives", "driven", "drove", "result", "results", "resulted", "because", "since",
               "therefore", "thus", "hence", "due", "so", "why", "reason", "reasons", "make", "makes", "made",
               "help", "helps", "helped", "reduce", "reduces", "reduced", "increase", "increases",
               "increased", "decrease", "decreases", "lowers", "lowered", "raise", "raises", "raised",
               "improve", "improves", "worsen", "worsens", "protect", "protects", "prevent", "prevents",
               "save", "saves", "explain", "explains", "contribute", "contributes", "means", "attributable",
               "responsible", "thanks", "owing", "if", "would", "will", "could", "can", "may", "might", "must",
               "should", "shall", "translates", "yields", "produces", "triggers", "boosts", "cuts"),
    "advice": ("you", "your", "yours", "yourself", "we", "our", "us", "i", "me", "my", "recommend",
               "recommends", "recommended", "advise", "advice", "consider", "choose", "switch", "enroll",
               "buy", "sign", "apply", "get", "best", "worst", "better", "worse", "ideal", "risk", "risks",
               "chance", "chances", "odds", "benefit", "benefits", "harm", "smart", "wise"),
    "quantity_words": ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                       "eleven", "twelve", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
                       "eighty", "ninety", "hundred", "thousand", "million", "billion", "trillion", "half",
                       "halves", "quarter", "quarters", "third", "thirds", "fifth", "fifths", "tenth",
                       "majority", "minority", "most", "many", "few", "several", "nearly", "almost", "vast",
                       "bulk", "handful", "plurality", "double", "twice", "triple"),
}

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _plain_words(s):
    return [w.lower() for w in WORD_RE.findall(str(s or ""))]


def _ident_words(rec):
    """Word tokens of the identifiers in a receipt's plan (variable names, weight, file), which the
    guard has already checked against AHRQ's file layout."""
    plan = rec.get("plan") or {}
    names = list(plan.get("variables") or []) + [plan.get("variable"), plan.get("weight"), plan.get("puf")]
    for c in plan.get("domain") or []:
        names.append(c.get("variable"))
    by = plan.get("by") or {}
    names.append(by.get("variable"))
    out = set()
    for n in names:
        if isinstance(n, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_{}]*", n):
            out.update(_plain_words(n))
    return out


def _valid(receipts):
    good = []
    for r in receipts or []:
        r = mv.normalize(r)
        if isinstance(r, dict) and r.get("receipt_id") and mv.compute_receipt_id(r) == str(r["receipt_id"]).lower():
            good.append(r)
    return good


# ---------------------------------------------------------------- rendering

def _fmt(x):
    return "{:,.0f}".format(x) if abs(x) >= 100 else "{:,.2f}".format(x)


def _value_text(v):
    if isinstance(v, list):
        return ", ".join(_value_text(x) for x in v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _cond(variable, op, value):
    return "%s %s %s" % (variable, OPS_WORDS.get(op, op), _value_text(value))


def _display(label, level, extra_words):
    words = _plain_words(label)
    if label and not re.search(r"\d", label) and words and all(w in VOCAB or w in extra_words for w in words):
        return label
    if level is None:
        return "All people in the domain"
    return "level %s" % _value_text(level)


def _header(rec):
    plan = rec.get("plan") or {}
    stat, var = plan.get("stat"), plan.get("variable")
    if stat == "proportion":
        c = plan.get("condition") or {}
        what = "share of people with %s" % _cond(var, c.get("op"), c.get("value"))
    elif stat == "mean":
        what = "mean of %s per person" % var
    elif stat == "total":
        what = "total of %s" % var
    else:
        what = "weighted number of people"
    dom = plan.get("domain") or []
    among = ("among people with " + " and ".join(_cond(c.get("variable"), c.get("op"), c.get("value"))
                                                 for c in dom)) if dom else "among all people"
    by = plan.get("by")
    by_text = (", by %s" % by.get("variable")) if by else ""
    return "MEPS %s full-year consolidated file (%s): %s, %s%s, weighted with %s." % (
        plan.get("year"), plan.get("puf"), what, among, by_text, plan.get("weight"))


def _row(res, stat, extra_words):
    disp = _display(str(res.get("label") or ""), res.get("level"), extra_words)
    status = (res.get("disclosure") or {}).get("status")
    if status == "suppressed" or res.get("value") is None:
        return "%s: suppressed under AHRQ's disclosure rule; no estimate is shown." % disp, "suppressed"
    star = "*" if status == "flagged" else ""
    note = "; relative standard error over 30%, interpret with caution" if star else ""
    lo, hi = res["ci95"]
    n = "{:,}".format(int(res["n"]))
    if stat == "proportion":
        line = "%s: %.1f%%%s (95%% CI %.1f%% to %.1f%%; SE %.1f percentage points; n = %s%s)." % (
            disp, res["value"] * 100.0, star, lo * 100.0, hi * 100.0, res["se"] * 100.0, n, note)
    else:
        unit = " people" if stat == "count" else ""
        line = "%s: %s%s%s (95%% CI %s to %s; SE %s; n = %s%s)." % (
            disp, _fmt(res["value"]), unit, star, _fmt(lo), _fmt(hi), _fmt(res["se"]), n, note)
    return line, ("flagged" if star else "ok")


def _comparison(rec, extra_words):
    comp = rec.get("comparison")
    if not comp:
        return []
    plan = rec.get("plan") or {}
    labels = {r.get("level"): r.get("label") for r in rec.get("results") or []}
    a = _display(str(labels.get(comp.get("a")) or ""), comp.get("a"), extra_words)
    b = _display(str(labels.get(comp.get("b")) or ""), comp.get("b"), extra_words)
    if comp.get("status") != "ok":
        return ["%s minus %s: not shown (a compared cell is suppressed)." % (a, b)]
    lo, hi = comp["ci95"]
    if plan.get("stat") == "proportion":
        line = "%s minus %s: %.1f percentage points (95%% CI %.1f to %.1f percentage points; SE %.1f " \
               "percentage points)." % (a, b, comp["difference"] * 100.0, lo * 100.0, hi * 100.0, comp["se"] * 100.0)
    else:
        line = "%s minus %s: %s (95%% CI %s to %s; SE %s)." % (
            a, b, _fmt(comp["difference"]), _fmt(lo), _fmt(hi), _fmt(comp["se"]))
    return [line, FIXED_DIFF]


def render_answer(receipts):
    """A purely descriptive answer from valid receipts (one line per sentence). Returns '' if none."""
    lines, statuses = [], set()
    for rec in _valid(receipts):
        extra = _ident_words(rec)
        stat = (rec.get("plan") or {}).get("stat")
        lines.append(_header(rec))
        for res in rec.get("results") or []:
            line, st = _row(res, stat, extra)
            lines.append(line)
            statuses.add(st)
        lines.extend(_comparison(rec, extra))
        lines.append("Receipt %s." % rec["receipt_id"][:12])
    if not lines:
        return ""
    if "flagged" in statuses:
        lines.append(FIXED_FLAG)
    if "suppressed" in statuses:
        lines.append(FIXED_SUPP)
    lines.append(FIXED_SCOPE)
    return "\n".join(lines)


# ---------------------------------------------------------------- the frame

def _category(word):
    for cat, words in DENY.items():
        if word in words:
            return cat
    if not word.isascii():
        return "non_english"
    if word in ("http", "https", "www", "com", "org", "gov"):
        return "link"
    return "outside_vocabulary"


def check_frame(text, receipts):
    """Every word must be in VOCAB, a single ASCII letter other than 'i', or a word of a receipt's
    variable names. Returns {'status': 'pass'|'blocked', 'findings': [...], 'off_frame': {...}}."""
    text = str(text or "")
    good = _valid(receipts)
    ids = [str(r["receipt_id"]).lower() for r in good]
    extra = set()
    for r in good:
        extra |= _ident_words(r)
    body = text
    for f in FIXED:
        body = body.replace(f, " ")
    spans = []
    for m in mv.HEX_RE.finditer(body):
        if any(i.startswith(m.group(0).lower()) for i in ids):
            spans.append(m.span())
    spans += [m.span() for m in mv.NUM_RE.finditer(body)]
    masked = mv._mask(body, spans)
    off = {}
    for w in _plain_words(masked):
        if w in VOCAB or w in extra or (len(w) == 1 and w.isascii() and w != "i"):
            continue
        off.setdefault(_category(w), [])
        if w not in off[_category(w)]:
            off[_category(w)].append(w)
    findings = []
    if off:
        listed = "; ".join("%s: %s" % (k, ", ".join(v[:12])) for k, v in sorted(off.items()))
        findings.append({"code": "OFF_FRAME_WORDING",
                         "message": "words outside the descriptive answer frame (%s)" % listed})
    return {"status": "blocked" if findings else "pass", "findings": findings, "off_frame": off,
            "frame": __version__}


def finalize_answer(draft, receipts):
    """Decide what may be shown. status 'pass' (the draft), 'replaced' (the rendered answer) or
    'blocked' (answer is None)."""
    receipts = list(receipts or [])
    v = mv.verify_answer(draft, receipts)
    f = check_frame(draft, receipts)
    draft_findings = v["findings"] + f["findings"]
    base = {"frame": __version__, "verifier": mv.__version__}
    if not draft_findings:
        return dict(base, status="pass", answer=draft, source="draft", findings=[])
    good = _valid(receipts)
    if not good or len(good) != len(receipts):
        return dict(base, status="blocked", answer=None, source=None, findings=draft_findings,
                    reason="no valid receipts to render from" if not good else "a receipt failed its hash check")
    rendered = render_answer(good)
    v2 = mv.verify_answer(rendered, receipts)
    f2 = check_frame(rendered, receipts)
    if v2["findings"] or f2["findings"]:
        return dict(base, status="blocked", answer=None, source=None, findings=draft_findings,
                    reason="TEMPLATE_FAILED", template_findings=v2["findings"] + f2["findings"])
    return dict(base, status="replaced", answer=rendered, source="template", findings=draft_findings)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Show a MEPS answer only if it stays inside the descriptive frame.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--draft", required=True, help="text file with the draft answer ('-' for stdin)")
    ap.add_argument("--receipt", action="append", default=[], help="receipt JSON file (repeatable)")
    a = ap.parse_args(argv)
    if a.draft == "-":
        text = sys.stdin.read()
    else:
        with open(a.draft, encoding="utf-8") as fh:
            text = fh.read()
    receipts = []
    for p in a.receipt:
        with open(p, encoding="utf-8") as fh:
            receipts.append(json.load(fh))
    out = finalize_answer(text, receipts)
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return {"pass": 0, "replaced": 1}.get(out["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
