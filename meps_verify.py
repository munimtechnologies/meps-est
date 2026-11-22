#!/usr/bin/env python3
# meps_verify.py - blocks a written answer unless every number in it is backed by an analysis receipt.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""Answer verifier for guarded MEPS estimates (the output-side safeguard).

meps_guard checks a request before anything is computed and gives every allowed answer an
analysis receipt. This module checks the other end: the sentences an agent finally writes for a
person. It BLOCKS the answer (it does not warn) when

    UNGROUNDED_NUMBER      a number matches no quantity in any supplied receipt at the precision
                           it is written with (11.85% matches 0.11849; 12% matches 0.1185;
                           $7,400 matches 7,432.85 because "7,400" is rounded to hundreds)
    NO_RECEIPT             the answer states numbers but no receipt was supplied
    RECEIPT_NOT_CITED      a number comes from a receipt whose id (first 8+ hex characters) the
                           answer never cites
    UNKNOWN_RECEIPT_CITED  the answer cites an id that matches no supplied receipt
    RECEIPT_TAMPERED       a receipt's receipt_id is not the SHA-256 of its canonical body (its
                           numbers are then not used for grounding)
    SUPPRESSED_CITED       a sentence names a suppressed cell (e.g. "Northeast") next to an
                           estimate without saying it is suppressed, or a number matches only
                           quantities of suppressed cells
    CAVEAT_MISSING         a number matches only cells flagged under AHRQ's rule (RSE > 30%) and
                           its sentence has no caveat (asterisk, "unreliable", "caution", "RSE"...)
    CAUSAL_LANGUAGE        causal wording ("causes", "effect of", "leads to", "would reduce"...)
                           that is not negated ("not a causal effect" is allowed)
    INDIVIDUAL_ADVICE      advice to the reader ("you should switch...", "your risk...")

Not checked, by design (stated so nobody relies on it): bare integers 0-10, four-digit years
1990-2035 without a $ sign or comma, the confidence level in "95% CI", and the disclosure
thresholds (60 people, 30% and 50% RSE, 1.96) in a sentence that talks about them. Numbers
written as words ("one in five") are not read. The sign of a number is not checked (a
difference may be written either way round).

    python meps_verify.py --answer answer.txt --receipt r1.json [--receipt r2.json ...]

prints a JSON verdict and exits 0 (pass) or 2 (blocked).
"""
__version__ = "0.1.0"

import argparse
import hashlib
import json
import math
import re
import sys

CODES = {
    "UNGROUNDED_NUMBER": "A number in the answer matches no quantity in the supplied receipts.",
    "NO_RECEIPT": "The answer states numbers but no analysis receipt was supplied.",
    "RECEIPT_NOT_CITED": "A number comes from a receipt whose id the answer does not cite.",
    "UNKNOWN_RECEIPT_CITED": "The answer cites a receipt id that matches no supplied receipt.",
    "RECEIPT_TAMPERED": "The receipt_id is not the SHA-256 of the receipt body; its numbers are not used.",
    "SUPPRESSED_CITED": "An estimate is given for a cell that AHRQ's disclosure rule suppresses.",
    "CAVEAT_MISSING": "A flagged (RSE > 30%) estimate is quoted without a reliability caveat.",
    "CAUSAL_LANGUAGE": "Survey tabulations show associations; causal wording is blocked.",
    "INDIVIDUAL_ADVICE": "Population estimates are not advice for an individual.",
}

SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}

NUM_RE = re.compile(
    r"(?<![\w.,])(?<![A-Za-z]-)"
    r"(?P<neg>[-−])?(?P<dollar>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\w)"
    r"(?P<unit>\s?(?:%|percentage[ -]points?\b|per ?cent\b|pp\b|points?\b)"
    r"|\s(?:thousand|million|billion|trillion)\b)?",
    re.I)
HEX_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8,64}(?![0-9A-Za-z])")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b")
VERSION_RE = re.compile(r"\bv?\d+\.\d+\.\d+\b")
URL_RE = re.compile(r"https?://\S+")
SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'*])|\n+")

CONF_RE = re.compile(r"\s*\*?\s*(?:CI\b|C\.I\.|conf|credib|interval|uncertainty)", re.I)
THRESH_CTX_RE = re.compile(r"(relative standard error|\bRSE\b|standard error|suppress|asterisk|reliab|"
                           r"sample size|fewer than|disclosure)", re.I)
CAVEAT_RE = re.compile(r"(\*|unreliab|imprecis|caution|relative standard error|\bRSE\b|small sample|"
                       r"interpret with care|uncertain|not precise|less precise|wide (confidence )?interval|"
                       r"flagged)", re.I)
SUPP_WORD_RE = re.compile(r"(suppress|not shown|not reported|withheld|too few|unavailable|not available|"
                          r"cannot be reported|can't be reported|not enough (people|respondents|sample))", re.I)
CAUSAL_RE = re.compile(
    r"\b(effects? of|causal(ly)?|impacts? of|causes?|caused by|causing|leads? to|led to|leading to|"
    r"because of|driven by|attributable to|result(s|ed)? in (a |an )?(higher|lower|more|less|greater|"
    r"increase|decrease|reduction)|(would|will|could) (reduce|increase|lower|raise|cut|improve))\b", re.I)
NEG_RE = re.compile(r"\b(not|no|never|cannot|can't|doesn't|isn't|aren't|without|nor)\b", re.I)
ADVICE_RE = re.compile(
    r"\b(you (should|must|need to|ought to) (get|buy|switch|enroll|choose|take|stop|start|see|sign|move|"
    r"apply|drop|avoid|change|cancel)|(we|i) (would )?recommend|my advice|"
    r"your (risk|chances?|odds|premium|costs?|diagnosis|prognosis)\b)", re.I)

EST_FIELDS = ("value", "se", "ci_low", "ci_high", "population_weighted", "weighted_share_excluded", "rse",
              "difference", "z", "p_two_sided_normal")


# ---------------------------------------------------------------- receipts

def canonical(obj):
    """The same canonical JSON that meps_guard hashes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def compute_receipt_id(receipt):
    """SHA-256 of the canonical receipt body (without receipt_id, and without the 'request' that the
    MCP server adds when it writes a receipt file)."""
    body = {k: v for k, v in receipt.items() if k not in ("receipt_id", "request")}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def normalize(r):
    """Accept a bare receipt or a full meps_guard.execute() / meps_estimate response."""
    if isinstance(r, dict) and isinstance(r.get("receipt"), dict):
        return r["receipt"]
    return r


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and
                                                                          (math.isnan(v) or math.isinf(v)))


def _candidates(rec):
    """Every quantity a receipt can back: (value, kind, field, label, status, est)."""
    rid = rec.get("receipt_id", "")
    plan = rec.get("plan") or {}
    prop = plan.get("stat") == "proportion"
    out = []

    def add(v, kind, field, label="", status="info", est=False):
        if _num(v):
            out.append({"value": float(v), "kind": kind, "field": field, "label": label or "",
                        "status": status, "est": est, "receipt": rid})

    add(plan.get("year"), "plan", "plan.year")
    for c in plan.get("domain") or []:
        vals = c.get("value") if isinstance(c.get("value"), list) else [c.get("value")]
        for v in vals:
            add(v, "plan", "plan.domain")
    cond = plan.get("condition") or {}
    for v in (cond.get("value") if isinstance(cond.get("value"), list) else [cond.get("value")]):
        add(v, "plan", "plan.condition")
    for v in ((plan.get("by") or {}).get("levels") or []):
        add(v, "plan", "plan.by")
    design = rec.get("design") or {}
    for f in ("records_read", "records_positive_weight", "records_weight_le_0_left_out", "strata_count",
              "psu_count", "singleton_strata", "degrees_of_freedom"):
        add(design.get(f), "design", "design." + f)

    for res in rec.get("results") or []:
        label = str(res.get("label") or "")
        status = ((res.get("disclosure") or {}).get("status")) or "ok"
        for f in ("n", "n_domain_before_missing", "n_excluded_missing"):
            add(res.get(f), "count", f, label)
        ci = res.get("ci95") or [None, None]
        pairs = [("value", res.get("value")), ("se", res.get("se")), ("ci_low", ci[0]), ("ci_high", ci[1])]
        for f, v in pairs:
            add(v, "raw", f, label, status, True)
            if prop and _num(v):
                add(v * 100.0, "pct", f, label, status, True)
        add(res.get("population_weighted"), "raw", "population_weighted", label, status, True)
        share = res.get("weighted_share_excluded")
        add(share, "raw", "weighted_share_excluded", label, status, True)
        if _num(share):
            add(share * 100.0, "pct", "weighted_share_excluded", label, status, True)
        rse = (res.get("disclosure") or {}).get("rse")
        add(rse, "raw", "rse", label, status, True)
        if _num(rse):
            add(rse * 100.0, "pct", "rse", label, status, True)

    comp = rec.get("comparison") or {}
    if comp.get("status") == "ok":
        lab = "%s vs %s" % (comp.get("a"), comp.get("b"))
        ci = comp.get("ci95") or [None, None]
        for f, v in (("difference", comp.get("difference")), ("se", comp.get("se")), ("ci_low", ci[0]),
                     ("ci_high", ci[1])):
            add(v, "raw", f, lab, "ok", True)
            if prop and _num(v):
                add(v * 100.0, "pct", f, lab, "ok", True)
        add(comp.get("z"), "raw", "z", lab, "ok", True)
        add(comp.get("p_two_sided_normal"), "raw", "p_two_sided_normal", lab, "ok", True)
    return out


# ---------------------------------------------------------------- numbers in text

def _mask(text, spans):
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _tokens(masked):
    toks = []
    for m in NUM_RE.finditer(masked):
        num = m.group("num")
        unit = (m.group("unit") or "").strip().lower()
        digits = num.replace(",", "")
        dec = len(digits.split(".")[1]) if "." in digits else 0
        x = float(digits)
        mult = SCALE.get(unit, 1.0)
        if unit in SCALE or unit == "":
            kind = "raw"
        elif unit.startswith("point"):
            kind = "any"
        else:
            kind = "pct"
        if dec:
            tol = 0.5 * 10.0 ** -dec
        else:
            intpart = digits.lstrip("0") or "0"
            sig = intpart.rstrip("0")
            zeros = len(intpart) - len(sig)
            tol = 0.5 * 10.0 ** zeros if (len(sig) >= 2 and zeros >= 1) else 0.5
        value = x * mult
        toks.append({"text": m.group(0).strip(), "start": m.start(), "end": m.end(), "value": value,
                     "tol": tol * mult + 1e-12 * (abs(value) + 1.0), "kind": kind, "dollar": bool(m.group("dollar")),
                     "comma": "," in num, "dec": dec, "mult": mult, "negative": bool(m.group("neg"))})
    return toks


def _exempt(tok, after, sentence):
    v, plain = tok["value"], (not tok["dollar"] and not tok["comma"] and tok["dec"] == 0 and tok["mult"] == 1)
    if tok["kind"] == "raw" and plain:
        if 1990 <= v <= 2035:
            return "year"
        if v <= 10:
            return "small integer"
    if tok["kind"] in ("pct", "any") and tok["dec"] == 0 and v in (90.0, 95.0, 99.0) and CONF_RE.match(after):
        return "confidence level"
    if THRESH_CTX_RE.search(sentence):
        if tok["kind"] in ("pct", "any") and tok["dec"] == 0 and v in (30.0, 50.0):
            return "disclosure threshold"
        if tok["kind"] == "raw" and not tok["dollar"] and ((plain and v == 60.0) or (tok["dec"] == 2 and v == 1.96)):
            return "disclosure threshold"
    return None


def _match(tok, cands):
    hits = []
    for c in cands:
        if tok["kind"] == "pct" and c["kind"] != "pct":
            continue
        if tok["kind"] == "raw" and c["kind"] == "pct":
            continue
        if abs(abs(tok["value"]) - abs(c["value"])) <= tok["tol"]:
            hits.append(c)
    return hits


def _sentences(text):
    spans, start = [], 0
    for m in SPLIT_RE.finditer(text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _causal_hits(text):
    hits = []
    for m in CAUSAL_RE.finditer(text):
        before = " ".join(text[max(0, m.start() - 60):m.start()].split()[-5:])
        if NEG_RE.search(before):
            continue
        hits.append(m.group(0))
    return hits


# ---------------------------------------------------------------- verdict

def verify_answer(text, receipts):
    """Check an answer against receipts. Returns {"status": "pass" | "blocked", "findings": [...], ...}."""
    text = str(text or "")
    receipts = [normalize(r) for r in (receipts or [])]
    findings = []

    def find(code, message, sentence=""):
        item = {"code": code, "message": message}
        if sentence:
            item["sentence"] = sentence.strip()[:300]
        if item not in findings:
            findings.append(item)

    good, known_ids = [], []
    for r in receipts:
        rid = str(r.get("receipt_id") or "").lower() if isinstance(r, dict) else ""
        if rid:
            known_ids.append(rid)
        if not isinstance(r, dict) or not rid or compute_receipt_id(r) != rid:
            find("RECEIPT_TAMPERED", "receipt %s: %s" % (rid[:12] or "(no id)", CODES["RECEIPT_TAMPERED"]))
            continue
        good.append(r)
    cands = [c for r in good for c in _candidates(r)]

    # receipt citations
    cited, mask_spans = set(), [m.span() for m in URL_RE.finditer(text)]
    mask_spans += [m.span() for m in DATE_RE.finditer(text)] + [m.span() for m in VERSION_RE.finditer(text)]
    for m in HEX_RE.finditer(text):
        tok = m.group(0).lower()
        owners = [rid for rid in known_ids if rid.startswith(tok)]
        has_letter, has_digit = re.search(r"[a-f]", tok), re.search(r"\d", tok)
        if owners:
            cited.update(owners)
            mask_spans.append(m.span())
        elif has_letter and has_digit:
            mask_spans.append(m.span())
            find("UNKNOWN_RECEIPT_CITED", "%s: %s" % (tok, CODES["UNKNOWN_RECEIPT_CITED"]))
    masked = _mask(text, mask_spans)

    sents = _sentences(masked)

    def sentence_of(pos):
        for a, b in sents:
            if a <= pos < b:
                return a, b
        return 0, len(masked)

    numbers, missing_cites = [], []
    all_toks = _tokens(masked)
    for tok in all_toks:
        a, b = sentence_of(tok["start"])
        sent = text[a:b]
        reason = _exempt(tok, masked[tok["end"]:tok["end"] + 30], sent)
        hits = [] if reason else _match(tok, cands)
        tok.update({"sentence": (a, b), "exempt": reason, "matches": hits})
        numbers.append({"text": tok["text"], "value": tok["value"], "exempt": reason,
                        "grounded_to": [{"receipt": h["receipt"][:12], "field": h["field"], "label": h["label"],
                                         "status": h["status"]} for h in hits[:6]]})
        if reason:
            continue
        if not hits:
            find("UNGROUNDED_NUMBER", "'%s': %s" % (tok["text"], CODES["UNGROUNDED_NUMBER"]), sent)
            continue
        owners = set(h["receipt"] for h in hits)
        if not owners & cited:
            key = sorted(o[:12] for o in owners)
            if key not in missing_cites:
                missing_cites.append(key)
                find("RECEIPT_NOT_CITED", "'%s' comes from receipt %s, which the answer does not cite" % (
                    tok["text"], " or ".join(key)), sent)
        if all(h["status"] == "suppressed" for h in hits):
            find("SUPPRESSED_CITED", "'%s' matches only quantities of suppressed cells" % tok["text"], sent)
        elif all(h["status"] == "flagged" for h in hits) and not CAVEAT_RE.search(sent):
            find("CAVEAT_MISSING", "'%s': %s" % (tok["text"], CODES["CAVEAT_MISSING"]), sent)

    if not receipts and any(not n["exempt"] for n in numbers):
        find("NO_RECEIPT", CODES["NO_RECEIPT"])

    # suppressed cells named next to an estimate
    supp_labels = sorted(set(
        str(res.get("label") or "") for r in good for res in (r.get("results") or [])
        if ((res.get("disclosure") or {}).get("status") == "suppressed")))
    supp_labels = [s for s in supp_labels if len(s) >= 3 and re.search(r"[A-Za-z]", s) and s.lower() != "all"]
    for a, b in sents:
        sent = text[a:b]
        named = [s for s in supp_labels if re.search(r"\b%s\b" % re.escape(s), sent, re.I)]
        if not named or SUPP_WORD_RE.search(sent):
            continue
        # estimate-type numbers in this sentence: ungrounded, or grounded to some estimate quantity
        est = [t for t in all_toks if t["sentence"] == (a, b) and not t["exempt"]
               and (not t["matches"] or any(h["est"] for h in t["matches"]))]
        if not est:
            continue
        for s in named:
            backed = any(h["est"] and h["status"] != "suppressed" and h["label"].lower() == s.lower()
                         for t in est for h in t["matches"])
            if not backed:
                find("SUPPRESSED_CITED", "'%s' is suppressed under AHRQ's rule, but the sentence gives it an "
                     "estimate without saying so" % s, sent)

    for phrase in _causal_hits(text):
        find("CAUSAL_LANGUAGE", "'%s': %s" % (phrase, CODES["CAUSAL_LANGUAGE"]))
    for m in ADVICE_RE.finditer(text):
        find("INDIVIDUAL_ADVICE", "'%s': %s" % (m.group(0), CODES["INDIVIDUAL_ADVICE"]))

    return _finish(text, findings, numbers, cited, receipts, good)


def _finish(text, findings, numbers, cited, receipts, good):
    status = "blocked" if findings else "pass"
    return {"status": status, "verifier": __version__, "findings": findings, "numbers": numbers,
            "receipts_supplied": len(receipts), "receipts_valid": len(good),
            "receipts_cited": sorted(c[:12] for c in cited),
            "policy": "Every finding blocks the answer; a warning never substitutes for blocking."}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Block an answer unless every number is backed by a MEPS receipt.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--answer", required=True, help="text file with the answer ('-' for stdin)")
    ap.add_argument("--receipt", action="append", default=[], help="receipt JSON file (repeatable)")
    a = ap.parse_args(argv)
    if a.answer == "-":
        text = sys.stdin.read()
    else:
        with open(a.answer, encoding="utf-8") as fh:
            text = fh.read()
    receipts = []
    for p in a.receipt:
        with open(p, encoding="utf-8") as fh:
            receipts.append(json.load(fh))
    verdict = verify_answer(text, receipts)
    json.dump(verdict, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
