# test_meps_frame.py - tests for meps_frame.py 0.1.0 on receipts made by meps_guard 0.1.1 from synthetic files.
# Copyright 2026 Munim, Inc. Apache-2.0. Made with AI agents working in MV2's name.
import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest

import meps_frame as mf
import meps_guard as mg
import meps_verify as mv


def synthetic(n=200):
    """The same synthetic MEPS-shaped records as test_meps_guard.py."""
    recs = []
    for i in range(n):
        h = i // 50 + 1
        p = (i // 25) % 2 + 1
        age = (i * 7) % 90
        sex = 1 + i % 2
        inscov = 3 if i % 7 == 0 else (2 if i % 3 == 0 else 1)
        w = 1000.0 + (i * 53) % 900 + 0.25 * (i % 4)
        r = {"DUPERSID": "P%07d" % i, "PERWT22F": 0.0 if i == n - 1 else w,
             "SAQWT22F": 0.0 if age < 18 else round(w * 0.9, 4), "DIABW22F": 0.0,
             "VARSTR": h, "VARPSU": p, "AGE22X": age, "AGE42X": age, "AGE31X": age,
             "REGION22": 1 + (i // 3) % 4, "REGION42": 1 + (i // 3) % 4, "REGION31": 1 + (i // 3) % 4,
             "SEX": sex, "INSCOV22": inscov, "INSURC22": (inscov if age < 65 else 4),
             "TOTEXP22": (i * 137) % 5000 + (500 if sex == 2 else 0),
             "ADGENH42": -1 if age < 18 else 1 + i % 5, "DSA1C53": -1,
             "DDNWRK22": -1 if i % 4 == 0 else i % 10, "GRP": 1 if h <= 2 else 2}
        if i % 37 == 5:
            r["AGE22X"] = -1
        recs.append(r)
    return recs


REGION_LABELS = {"1": "Northeast", "2": "Midwest", "3": "South", "4": "West"}
UNINS_ALL = {"year": 2022, "stat": "proportion", "variable": "INSURC{yy}", "condition": {"op": "==", "value": 3},
             "domain": [{"variable": "AGELAST", "op": "<", "value": 65}],
             "question": "What share of people under 65 were uninsured all year?"}
UNINS_BY_REGION = dict(UNINS_ALL, by={"variable": "REGIONX", "levels": [1, 2, 3, 4], "labels": REGION_LABELS},
                       question="What share of people under 65 were uninsured all year, by region?")
MEAN_BY_GRP = {"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}", "by": {"variable": "GRP", "levels": [1, 2]},
               "compare": [1, 2], "question": "Mean total spending in two groups"}
COUNT_UNDER65 = {"year": 2022, "stat": "count", "domain": [{"variable": "AGELAST", "op": "<", "value": 65}],
                 "question": "How many people were under 65?"}
SUPPRESSED_MEAN = {"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}",
                   "domain": [{"variable": "AGELAST", "op": "==", "value": 7}],
                   "question": "Mean spending at age 7"}

RECS = synthetic()


def receipt(req):
    plan, refusals = mg.plan_request(req)
    assert refusals == [], refusals
    out = mg.execute(plan, RECS, {"data_sha256": "x"})
    assert out["status"] == "ok", out
    return out["receipt"]


def codes(findings):
    return sorted(set(f["code"] for f in findings))


def benign(rc):
    res = rc["results"][0]
    star = "*" if res.get("asterisk") else ""
    lo, hi = res["ci95"][0] * 100.0, res["ci95"][1] * 100.0
    return ("In 2022, %.1f%%%s of people under 65 were uninsured all year (95%% CI %.1f%% to %.1f%%; receipt %s)."
            % (res["percent"], star, lo, hi, rc["receipt_id"][:12]))


class VocabularyTests(unittest.TestCase):
    def test_no_denied_word_in_vocabulary(self):
        for cat, words in mf.DENY.items():
            self.assertEqual(sorted(set(words) & mf.VOCAB), [], cat)

    def test_vocabulary_is_lowercase_ascii(self):
        for w in mf.VOCAB:
            self.assertTrue(w.isascii() and w == w.lower() and w.isalpha(), w)


class RenderTests(unittest.TestCase):
    def check_rendered(self, rcs):
        text = mf.render_answer(rcs)
        self.assertTrue(text)
        v = mv.verify_answer(text, rcs)
        self.assertEqual(v["status"], "pass", (text, v["findings"]))
        f = mf.check_frame(text, rcs)
        self.assertEqual(f["status"], "pass", (text, f["findings"]))
        out = mf.finalize_answer(text, rcs)
        self.assertEqual(out["status"], "pass")
        self.assertEqual(out["answer"], text)
        self.assertEqual(out["source"], "draft")
        self.assertIn(mf.FIXED_SCOPE, text)
        for rc in rcs:
            self.assertIn("Receipt %s." % rc["receipt_id"][:12], text)
        return text

    def test_proportion(self):
        rc = receipt(UNINS_ALL)
        text = self.check_rendered([rc])
        res = rc["results"][0]
        self.assertIn("%.1f%%" % res["percent"], text)
        if res.get("asterisk"):
            self.assertIn(mf.FIXED_FLAG, text)

    def test_by_region_with_suppressed_cells(self):
        rc = receipt(UNINS_BY_REGION)
        text = self.check_rendered([rc])
        for res in rc["results"]:
            if res["disclosure"]["status"] == "suppressed":
                self.assertIn("%s: suppressed under AHRQ's disclosure rule; no estimate is shown." % res["label"], text)
                self.assertIn(mf.FIXED_SUPP, text)

    def test_mean_with_comparison(self):
        rc = receipt(MEAN_BY_GRP)
        text = self.check_rendered([rc])
        self.assertIn("level 1 minus level 2", text)
        if rc["comparison"]["status"] == "ok":
            self.assertIn(mf.FIXED_DIFF, text)

    def test_count_and_suppressed_mean_and_two_receipts(self):
        a, b = receipt(COUNT_UNDER65), receipt(SUPPRESSED_MEAN)
        self.check_rendered([a])
        text = self.check_rendered([a, b])
        self.assertIn(" people (95% CI ", text)
        self.assertIn("All: suppressed under AHRQ's disclosure rule", text)

    def test_label_outside_vocabulary_is_not_echoed(self):
        labels = dict(REGION_LABELS, **{"4": "Paid less thanks to plans"})
        rc = receipt(dict(UNINS_BY_REGION, by={"variable": "REGIONX", "levels": [1, 2, 3, 4], "labels": labels}))
        text = self.check_rendered([rc])
        self.assertIn("level 4", text)
        self.assertNotIn("thanks", text)
        self.assertIn("Northeast", text)

    def test_no_valid_receipts_renders_nothing(self):
        rc = copy.deepcopy(receipt(UNINS_ALL))
        rc["results"][0]["n"] += 1
        self.assertEqual(mf.render_answer([rc]), "")
        self.assertEqual(mf.render_answer([]), "")


class FinalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc = receipt(UNINS_ALL)
        cls.good = benign(cls.rc)
        cls.rendered = mf.render_answer([cls.rc])

    def decide(self, draft):
        return mf.finalize_answer(draft, [self.rc])

    def test_benign_draft_passes_unchanged(self):
        out = self.decide(self.good)
        self.assertEqual(out["status"], "pass", out["findings"])
        self.assertEqual(out["answer"], self.good)

    def test_causal_paraphrases_are_replaced(self):
        for extra in ("Being uninsured all year lowers spending.",
                      "Coverage translates into fewer uninsured people.",
                      "People under 65 are uninsured owing to cost.",
                      "Losing coverage pushes people out of care.",
                      "Coverage is the reason spending differs.",
                      "Insurance causes lower spending."):
            out = self.decide(self.good + " " + extra)
            self.assertEqual(out["status"], "replaced", extra)
            self.assertEqual(out["answer"], self.rendered)
            self.assertIn("OFF_FRAME_WORDING", codes(out["findings"]), extra)

    def test_frame_catches_what_the_word_list_misses(self):
        for extra in ("Coverage translates into fewer uninsured people.",
                      "If you are under 65, it is worth buying coverage.",
                      "Coverage саuses lower spending."):
            draft = self.good + " " + extra
            self.assertEqual(mv.verify_answer(draft, [self.rc])["status"], "pass", extra)
            self.assertEqual(mf.check_frame(draft, [self.rc])["status"], "blocked", extra)
        off = mf.check_frame(self.good + " Coverage саuses lower spending.", [self.rc])["off_frame"]
        self.assertIn("non_english", off)

    def test_advice_is_replaced(self):
        for extra in ("If you are under 65, it is worth buying coverage.",
                      "We recommend that people under 65 enroll.",
                      "Your odds of being uninsured are low."):
            out = self.decide(self.good + " " + extra)
            self.assertEqual(out["status"], "replaced", extra)
            self.assertIn("advice", mf.check_frame(self.good + " " + extra, [self.rc])["off_frame"], extra)

    def test_number_words_are_blocked(self):
        draft = "About one in five people under 65 were uninsured all year (receipt %s)." % self.rc["receipt_id"][:12]
        self.assertEqual(mv.verify_answer(draft, [self.rc])["status"], "pass")
        f = mf.check_frame(draft, [self.rc])
        self.assertEqual(f["status"], "blocked")
        self.assertIn("quantity_words", f["off_frame"])
        self.assertEqual(self.decide(draft)["status"], "replaced")

    def test_other_language_and_links_are_replaced(self):
        res = self.rc["results"][0]
        es = "En 2022, el %.1f%% de las personas menores de 65 no tenía seguro (receipt %s)." % (
            res["percent"], self.rc["receipt_id"][:12])
        self.assertEqual(self.decide(es)["status"], "replaced")
        f = mf.check_frame(self.good + " See https://example.org/page.", [self.rc])
        self.assertIn("link", f["off_frame"])

    def test_fixed_sentences_may_be_repeated(self):
        draft = self.good + "\n" + mf.FIXED_SCOPE
        self.assertEqual(self.decide(draft)["status"], "pass")

    def test_wrong_number_is_replaced(self):
        draft = self.good.replace("In 2022, ", "In 2022, 99.9% or ")
        out = self.decide(draft)
        self.assertEqual(out["status"], "replaced")
        self.assertIn("UNGROUNDED_NUMBER", codes(out["findings"]))

    def test_tampered_receipt_blocks_everything(self):
        bad = copy.deepcopy(self.rc)
        bad["results"][0]["n"] += 1
        out = mf.finalize_answer(self.good, [bad])
        self.assertEqual(out["status"], "blocked")
        self.assertIsNone(out["answer"])
        out = mf.finalize_answer(self.good, [self.rc, bad])
        self.assertEqual(out["status"], "blocked")
        self.assertIsNone(out["answer"])

    def test_no_receipts_blocks(self):
        out = mf.finalize_answer("About 41.5% were uninsured.", [])
        self.assertEqual(out["status"], "blocked")
        self.assertIsNone(out["answer"])
        self.assertIn("NO_RECEIPT", codes(out["findings"]))


class CLITests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.rc = receipt(UNINS_ALL)
        self.rp = os.path.join(self.td, "r.json")
        with open(self.rp, "w", encoding="utf-8") as fh:
            json.dump(self.rc, fh)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def run_cli(self, text, rp=None):
        dp = os.path.join(self.td, "draft.txt")
        with open(dp, "w", encoding="utf-8") as fh:
            fh.write(text)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mf.main(["--draft", dp, "--receipt", rp or self.rp])
        return code, json.loads(buf.getvalue())

    def test_exit_codes(self):
        code, out = self.run_cli(benign(self.rc))
        self.assertEqual((code, out["status"]), (0, "pass"))
        code, out = self.run_cli(benign(self.rc) + " Insurance lowers spending.")
        self.assertEqual((code, out["status"]), (1, "replaced"))
        bad = copy.deepcopy(self.rc)
        bad["results"][0]["n"] += 1
        bp = os.path.join(self.td, "bad.json")
        with open(bp, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        code, out = self.run_cli(benign(self.rc), bp)
        self.assertEqual((code, out["status"]), (2, "blocked"))


if __name__ == "__main__":
    unittest.main()
