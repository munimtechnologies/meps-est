# test_meps_tables.py - tests for meps_tables.py. Apache-2.0. Made with AI agents working in MV2's name.
import io
import math
import os
import tempfile
import unittest

import meps_est as me
import meps_tables as mt


def person(age, inscov, insurc, region=3, marry=1, racethx=2, racev1x=1, sex=1, w=1000.0, h=1, p=1):
    return {"PERWT22F": w, "VARSTR": h, "VARPSU": p, "AGE22X": age, "AGE42X": -1, "AGE31X": -1,
            "REGION22": region, "REGION42": -1, "REGION31": -1, "MARRY22X": marry, "MARRY42X": -1,
            "MARRY31X": -1, "RACETHX": racethx, "RACEV1X": racev1x, "SEX": sex,
            "INSCOV22": inscov, "INSURC22": insurc}


HEADER = ("Year,stat_group,stat_var,stat_label,row_group,row_var,row_label,rowLevels,col_group,col_var,"
          "col_label,colLevels,value,se,value_rd,se_rd,asterisk,Percent,sample_size,caption\n")


def ref_line(stat, rv, rl, cv, cl, value, se, n, star=""):
    def q(s):
        return '"%s"' % s if "," in s else s
    return "2022,G,%s,L,,%s,,%s,Insurance coverage,%s,,%s,%r,%r,0,0,%s,FALSE,%d,cap\n" % (
        stat, rv, q(rl), cv, q(cl), value, se, star, n)


class DeriveTests(unittest.TestCase):
    def test_cut(self):
        b, l = mt.AGE_CUTS[0]
        self.assertEqual(mt.cut_label(0, b, l), "Under 5")
        self.assertEqual(mt.cut_label(4, b, l), "Under 5")
        self.assertEqual(mt.cut_label(5, b, l), "5-17")
        self.assertEqual(mt.cut_label(64, b, l), "45-64")
        self.assertEqual(mt.cut_label(65, b, l), "65+")
        self.assertIsNone(mt.cut_label(None, b, l))

    def test_age_sets_and_fallback(self):
        r = mt.derive(person(18, 1, 1), 2022)
        self.assertEqual(r["rows"]["agegrps"], {"18-44", "18-64", "18"})
        rec = person(-1, 1, 1)
        rec["AGE42X"] = 70
        r = mt.derive(rec, 2022)
        self.assertEqual(r["agelast"], 70)
        self.assertEqual(r["rows"]["agegrps"], {"65+"})
        self.assertTrue(r["subset"]["ins_ge65"])
        self.assertFalse(r["subset"]["ins_lt65"])

    def test_race(self):
        self.assertEqual(mt.race_code(1, 1), 1)
        self.assertEqual(mt.race_code(4, 4), 5)
        self.assertEqual(mt.race_code(5, 6), 4)
        self.assertEqual(mt.race_code(5, 2), 0)  # falls to "Missing", as in AHRQ's recode
        r = mt.derive(person(30, 1, 1, racethx=5, racev1x=2, sex=2), 2022)
        self.assertEqual(r["rows"]["race"], {"Missing"})
        self.assertEqual(r["rows"]["racesex"], {"Female, Missing"})

    def test_married_region_negative(self):
        r = mt.derive(person(10, 2, 2, region=-1, marry=6), 2022)
        self.assertEqual(r["rows"]["region"], {"Missing"})
        self.assertEqual(r["rows"]["married"], {"Inapplicable (age < 16)"})

    def test_insurc_7_and_8_same_label(self):
        a = mt.derive(person(70, 3, 7), 2022)["cols"]["ins_ge65"]
        b = mt.derive(person(70, 3, 8), 2022)["cols"]["ins_ge65"]
        self.assertEqual(a, b)


class ReproduceTests(unittest.TestCase):
    def setUp(self):
        raw = [person(30, 1, 1, h=1, p=1, w=1000), person(40, 2, 2, h=1, p=2, w=3000),
               person(70, 2, 4, h=2, p=1, w=2000, sex=2), person(3, 3, 3, h=2, p=2, w=4000, sex=2),
               person(50, 1, 1, w=0.0, h=2, p=2)]
        self.recs = [mt.derive(r, 2022) for r in raw]
        self.design = me.Design(self.recs)

    def test_total_and_pct_against_hand(self):
        # people with any private: only the 1000-weight person (zero weight is dropped)
        e = self.design.estimate(lambda r: 1.0 if r["cols"]["insurance"] == "Any private, all ages" else 0.0)
        self.assertAlmostEqual(e["total"], 1000.0)
        self.assertAlmostEqual(e["mean"], 0.1)
        # stratum 1 PSU sums (1000, 0), stratum 2 (0, 0): var = 2 * (500^2 + 500^2)
        self.assertAlmostEqual(e["se_total"], math.sqrt(2 * 2 * 500.0 ** 2))

    def test_reproduce_matches_its_own_numbers(self):
        e_tot = self.design.estimate(lambda r: 1.0 if r["cols"]["insurance"] == "Public only, all ages" else 0.0)
        e_f = self.design.estimate(lambda r: 1.0 if r["cols"]["insurance"] == "Public only, all ages" else 0.0,
                                   lambda r: "Female" in r["rows"]["sex"])
        text = HEADER
        text += ref_line("totPOP", "ind", "Total", "insurance", "Public only, all ages",
                         e_tot["total"] / 1000, e_tot["se_total"] / 1000, 4)
        text += ref_line("pctPOP", "sex", "Female", "insurance", "Public only, all ages",
                         e_f["mean"] * 100, e_f["se_mean"] * 100, 2, star="*")
        text += ref_line("totPOP", "poverty", "Poor", "insurance", "Uninsured, all ages", 1.0, 1.0, 9)
        text += ref_line("totPOP", "sex", "Male", "insurance", "Uninsured, all ages", 5.0, 1.0, 2)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "DY2022.csv")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            ref = mt.read_reference(p)
        rep = mt.reproduce(self.recs, ref)
        t = rep["main_totals"]
        self.assertEqual(t["compared"], 3)
        self.assertEqual(t["value_ok"], 2)   # the Male/Uninsured line is deliberately wrong
        self.assertEqual(t["n_ok"], 3)
        self.assertEqual(rep["not_attempted"], {"totPOP|poverty|insurance": 1})
        self.assertEqual(rep["mismatch_count"], 1)
        self.assertEqual(rep["mismatches_first200"][0]["row"], "Male")

    def test_subset_domain_count(self):
        e = self.design.estimate(lambda r: 1.0 if r["cols"]["ins_lt65"] == "<65, Uninsured" else 0.0,
                                 lambda r: r["subset"]["ins_lt65"])
        self.assertEqual(e["n"], 3)
        self.assertAlmostEqual(e["mean"], 4000.0 / 8000.0)

    def test_zero_only_psus(self):
        raw = [person(30, 1, 1, h=1, p=1), person(30, 1, 1, h=1, p=2, w=0.0)]
        self.assertEqual(mt.zero_only_psus(raw, 2022), 1)

    def test_suppress_rule(self):
        big = {"n": 500, "mean": 0.5, "se_mean": 0.01, "total": 1e6, "se_total": 1e5}
        self.assertFalse(mt.suppress_rule("pctPOP", big))
        self.assertFalse(mt.suppress_rule("totPOP", big))
        self.assertTrue(mt.suppress_rule("totPOP", dict(big, n=59)))
        self.assertTrue(mt.suppress_rule("totPOP", dict(big, se_total=6e5)))   # RSE 0.6
        self.assertTrue(mt.suppress_rule("pctPOP", dict(big, se_mean=0.0)))
        # small percentage: 0.01 with RSE 0.6 < (0.1/0.01 - 1)/1.96 = 4.59 is kept
        self.assertFalse(mt.suppress_rule("pctPOP", dict(big, mean=0.01, se_mean=0.006)))
        # but not when its RSE is above that bound
        self.assertTrue(mt.suppress_rule("pctPOP", dict(big, mean=0.01, se_mean=0.05)))

    def test_rel_diff(self):
        self.assertEqual(mt.rel_diff(0.0, 0.0), 0.0)
        self.assertAlmostEqual(mt.rel_diff(101.0, 100.0), 0.01)


if __name__ == "__main__":
    unittest.main()
