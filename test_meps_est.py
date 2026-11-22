# test_meps_est.py - tests for meps_est.py. Apache-2.0. Made with AI agents working in MV2's name.
import io
import json
import math
import os
import statistics
import tempfile
import unittest
import zipfile

import meps_est as m

LAYOUT = """
  INFILE IN LRECL=24;
  INPUT
    @1      DUID      5.0
    @6      PID       3.0
    @9      DUPERSID $8.0
    @17     PERWT16F  6.2
    @23     VARSTR    1.0
    @24     VARPSU    1.0
  ;
"""


class LayoutTests(unittest.TestCase):
    def test_parse(self):
        cols = m.parse_layout(LAYOUT)
        self.assertEqual(cols["DUID"], (1, 5, 0, False))
        self.assertEqual(cols["DUPERSID"], (9, 8, 0, True))
        self.assertEqual(cols["PERWT16F"], (17, 6, 2, False))
        self.assertEqual(len(cols), 6)

    def test_conflict(self):
        with self.assertRaises(ValueError):
            m.parse_layout("@1 A 2.0\n@3 A 2.0")

    def test_values(self):
        self.assertEqual(m.parse_value("  -1", 0, False), -1)
        self.assertAlmostEqual(m.parse_value("012345", 2, False), 123.45)
        self.assertAlmostEqual(m.parse_value(" 12.5 ", 6, False), 12.5)  # explicit point wins
        self.assertIsNone(m.parse_value("   ", 0, False))
        self.assertEqual(m.parse_value(" ab ", 0, True), "ab")

    def test_read_zip(self):
        lines = ["10001101" + "10001101" + "001234" + "1" + "2",
                 "10001102" + "10001102" + " 56.78" + "2" + "1"]
        with tempfile.TemporaryDirectory() as td:
            z = os.path.join(td, "h999dat.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("h999.dat", "\r\n".join(lines) + "\r\n")
            recs = list(m.read_records(z, m.parse_layout(LAYOUT), ["DUPERSID", "PERWT16F", "VARSTR", "VARPSU"]))
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["DUPERSID"], "10001101")
        self.assertAlmostEqual(recs[0]["PERWT16F"], 12.34)
        self.assertAlmostEqual(recs[1]["PERWT16F"], 56.78)
        self.assertEqual((recs[1]["VARSTR"], recs[1]["VARPSU"]), (2, 1))

    def test_short_record(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.dat")
            with open(p, "w") as fh:
                fh.write("123\n")
            with self.assertRaises(ValueError):
                list(m.read_records(p, m.parse_layout(LAYOUT), ["VARPSU"]))


def recs(values, strata=None, psus=None, weights=None):
    out = []
    for i, v in enumerate(values):
        out.append({"PERWT": 1.0 if weights is None else weights[i],
                    "VARSTR": 1 if strata is None else strata[i],
                    "VARPSU": i if psus is None else psus[i], "y": v})
    return out


class EstimateTests(unittest.TestCase):
    def test_srs_closed_form(self):
        ys = [3.0, 7.0, 1.0, 9.0, 4.0, 6.0]
        d = m.Design(recs(ys))
        e = d.estimate("y")
        s = statistics.stdev(ys)
        n = len(ys)
        self.assertAlmostEqual(e["mean"], sum(ys) / n)
        self.assertAlmostEqual(e["se_mean"], s / math.sqrt(n))
        self.assertAlmostEqual(e["total"], sum(ys))
        self.assertAlmostEqual(e["se_total"], math.sqrt(n) * s)
        self.assertEqual(e["n"], n)

    def test_nonpositive_weights_dropped(self):
        d = m.Design(recs([1.0, 2.0, 50.0], weights=[1.0, 1.0, 0.0]))
        self.assertEqual((d.n_records, d.n_nonpositive), (3, 1))
        self.assertAlmostEqual(d.estimate("y")["mean"], 1.5)

    def test_stratified_by_hand(self):
        # stratum 1: PSUs a (w2,y1),(w2,y3) and b (w1,y5); stratum 2: PSUs c (w4,y2), d (w4,y6)
        rows = [{"PERWT": 2, "VARSTR": 1, "VARPSU": "a", "y": 1},
                {"PERWT": 2, "VARSTR": 1, "VARPSU": "a", "y": 3},
                {"PERWT": 1, "VARSTR": 1, "VARPSU": "b", "y": 5},
                {"PERWT": 4, "VARSTR": 2, "VARPSU": "c", "y": 2},
                {"PERWT": 4, "VARSTR": 2, "VARPSU": "d", "y": 6}]
        e = m.Design(rows).estimate("y")
        # total: z = (8, 5) in stratum 1 and (8, 24) in stratum 2
        v1 = 2 / 1 * ((8 - 6.5) ** 2 + (5 - 6.5) ** 2)
        v2 = 2 / 1 * ((8 - 16) ** 2 + (24 - 16) ** 2)
        self.assertAlmostEqual(e["total"], 45.0)
        self.assertAlmostEqual(e["se_total"], math.sqrt(v1 + v2))
        W, mean = 13.0, 45.0 / 13.0
        za = (2 * (1 - mean) + 2 * (3 - mean)) / W
        zb = (1 * (5 - mean)) / W
        zc = (4 * (2 - mean)) / W
        zd = (4 * (6 - mean)) / W
        vm = 2 * ((za - (za + zb) / 2) ** 2 + (zb - (za + zb) / 2) ** 2) + \
            2 * ((zc - (zc + zd) / 2) ** 2 + (zd - (zc + zd) / 2) ** 2)
        self.assertAlmostEqual(e["mean"], mean)
        self.assertAlmostEqual(e["se_mean"], math.sqrt(vm))

    def test_domain_keeps_design(self):
        rows = recs([1.0, 2.0, 3.0, 4.0, 10.0, 20.0], strata=[1, 1, 1, 2, 2, 2], psus=[1, 2, 3, 1, 2, 3])
        d = m.Design(rows)
        dom = d.estimate("y", lambda r: r["y"] >= 3)
        self.assertEqual(dom["n"], 4)
        self.assertAlmostEqual(dom["mean"], 37.0 / 4)
        # domain total variance = total variance of y*indicator over the full design
        full = m.Design([dict(r, y=(r["y"] if r["y"] >= 3 else 0.0)) for r in rows]).estimate("y")
        self.assertAlmostEqual(dom["se_total"], full["se_total"])
        # and differs from treating the domain as its own sample
        sub = m.Design([r for r in rows if r["y"] >= 3]).estimate("y")
        self.assertNotAlmostEqual(dom["se_total"], sub["se_total"])

    def test_singleton_stratum_adds_nothing(self):
        rows = recs([1.0, 2.0, 100.0], strata=[1, 1, 2], psus=[1, 2, 1])
        d = m.Design(rows)
        self.assertEqual(d.singleton_strata, [2])
        e = d.estimate("y")
        self.assertAlmostEqual(e["se_total"], math.sqrt(2 * ((1 - 1.5) ** 2 + (2 - 1.5) ** 2)))

    def test_ratio_of_identical_is_one(self):
        rows = recs([1.0, 2.0, 3.0, 4.0])
        r = m.Design(rows).ratio("y", "y")
        self.assertAlmostEqual(r["ratio"], 1.0)
        self.assertAlmostEqual(r["se_ratio"], 0.0)

    def test_missing_in_domain_raises(self):
        rows = recs([1.0, None])
        with self.assertRaises(ValueError):
            m.Design(rows).estimate("y")


class WorkshopTests(unittest.TestCase):
    def test_derive(self):
        rec = {"PERWT16F": 5.0, "VARSTR": 1, "VARPSU": 1, "AGE16X": -1, "AGE42X": 70, "AGE31X": 69}
        for parts in m.SERVICES.values():
            for p in parts:
                rec[p + "16"] = 0
        rec["TOTEXP16"] = 10
        rec["RXEXP16"] = 10
        out = m.derive(rec, 2016)
        self.assertEqual((out["age"], out["agecat"], out["x_total"], out["x_rx"], out["x_ip"]), (70, 2, 1, 1, 0))

    def test_columns(self):
        cols = m.exercise_columns(2015)
        self.assertIn("PERWT15F", cols)
        self.assertIn("ERFEXP15", cols)
        self.assertIn("AGE15X", cols)

    def test_tolerance_and_compare(self):
        self.assertAlmostEqual(m.tolerance("0.85"), 0.005)
        self.assertAlmostEqual(m.tolerance("1617531007314"), 0.5)
        self.assertAlmostEqual(m.tolerance("1.5999074E12"), 5e4)
        self.assertAlmostEqual(m.tolerance("429.1720"), 5e-5)
        rows, ok, n = m.compare({"a": {"mean": 0.8512, "se_mean": 0.003594}},
                                {"a": {"mean": "0.85", "se_mean": "0.00359"}, "b": {"n": "3"}})
        self.assertEqual((ok, n), (2, 3))
        self.assertFalse(rows[2]["match"])

    def test_reference_file_parses(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "reference_ex1.json"), encoding="utf-8") as fh:
            refs = json.load(fh)
        count = 0
        for key in ("ex1a_2016", "ex1b_2015"):
            for fig, stats in refs[key]["figures"].items():
                for stat, val in stats.items():
                    self.assertIsInstance(val, str)
                    m.tolerance(val)
                    count += 1
        self.assertGreater(count, 100)


if __name__ == "__main__":
    unittest.main()
