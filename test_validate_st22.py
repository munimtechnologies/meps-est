# test_validate_st22.py - online validation: meps_est against AHRQ's official 2022 MEPS-HC insurance table.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""Pre-registered check meps-st22 (docs/preregistrations/prereg-meps-st22.md, written 2026-11-17 before
any 2022 record or the full reference CSV was read). It downloads the 2022 full-year consolidated file
(HC-243, about 6 MB) from meps.ahrq.gov and AHRQ's official summary-table CSV
(github.com/HHS-AHRQ/MEPS-summary-tables, formatted_tables/hc_ins/DY2022.csv, made in R with the
survey package), recomputes every cell meps_tables can define and compares: value to a relative 1e-8,
SE to a relative 1e-6, and the unweighted sample size exactly. Each test is one pre-registered
prediction; a failure message prints the numbers so the grade can be read from the test log.

Needs network. Set MEPS_OFFLINE=1 to skip. Downloads are cached in MEPS_CACHE (default: a
meps-est-cache folder in the system temp directory)."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest

import meps_est as me
import meps_tables as mt

BASE = "https://meps.ahrq.gov/mepsweb"
DAT = BASE + "/data_files/pufs/h243/h243dat.zip"
SU = BASE + "/data_stats/download_data/pufs/h243/h243su.txt"
CSV = "https://raw.githubusercontent.com/HHS-AHRQ/MEPS-summary-tables/main/formatted_tables/hc_ins/DY2022.csv"
CACHE = os.environ.get("MEPS_CACHE") or os.path.join(tempfile.gettempdir(), "meps-est-cache")


def _short(m):
    return "L%d %s %s|%s|%s v %.12g/%.12g se %.8g/%.8g n %s/%s" % (
        m["line"], m["stat"], m["row_var"], m["row"], m["col"], m["ours_value"], m["ref_value"],
        m["ours_se"], m["ref_se"], m["ours_n"], m["ref_n"])


@unittest.skipIf(os.environ.get("MEPS_OFFLINE") == "1", "MEPS_OFFLINE=1")
class AhrqSummaryTable2022(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dest = os.path.join(CACHE, "2022")
        with contextlib.redirect_stdout(io.StringIO()):
            me._fetch([DAT, SU, CSV], dest)
        with open(os.path.join(dest, "h243su.txt"), encoding="latin-1") as fh:
            layout = me.parse_layout(fh.read())
        want = mt.columns(2022)
        cls.missing_columns = [c for c in want if c not in layout]
        t0 = time.time()
        raw = list(me.read_records(os.path.join(dest, "h243dat.zip"), layout, want))
        recs = [mt.derive(r, 2022) for r in raw]
        cls.ref = mt.read_reference(os.path.join(dest, "DY2022.csv"))
        cls.rep = mt.reproduce(recs, cls.ref)
        cls.seconds = time.time() - t0
        cls.records_in_file = len(raw)
        cls.zero_only = mt.zero_only_psus(raw, 2022)
        t = cls.rep["main_totals"]
        cls.summary = ("records %d; design %s; main %s; mismatches %d; cells %d; %.0f s" % (
            len(raw), json.dumps(cls.rep["design"], sort_keys=True), json.dumps(t, sort_keys=True),
            cls.rep["mismatch_count"], cls.rep["cells_computed"], cls.seconds))

    def _mm(self, k=3):
        return " | ".join(_short(m) for m in self.rep["mismatches_first200"][:k])

    def test_G2_layout_has_all_columns(self):
        self.assertEqual(self.missing_columns, [], "G2: columns not in layout")

    def test_P1_total_population_by_insurance(self):
        rows = [(i + 2, r) for i, r in enumerate(self.ref) if r.get("stat_var") == "totPOP"
                and r.get("row_var") == "ind" and r.get("col_var") == "insurance" and mt._num(r.get("value")) is not None]
        lines = set(ln for ln, _ in rows)
        bad = [m for m in self.rep["mismatches_first200"] if m["line"] in lines]
        n_bad = [(ln, r.get("sample_size")) for ln, r in rows if mt._num(r.get("sample_size")) != 21747]
        self.assertEqual(len(rows), 3, "P1: %d Total x insurance totPOP rows" % len(rows))
        self.assertEqual(bad, [], "P1: " + " | ".join(_short(m) for m in bad))
        self.assertEqual(n_bad, [], "P1: sample sizes %r" % n_bad)

    def _share(self, num, den, floor, name):
        t = self.rep["main_totals"]
        self.assertGreater(t[den], 0, "%s: nothing compared; %s" % (name, self.summary))
        self.assertGreaterEqual(t[num] / t[den], floor, "%s: %d of %d. %s. first: %s" % (
            name, t[num], t[den], self.summary, self._mm()))

    def test_P2_values(self):
        self._share("value_ok", "compared", 0.99, "P2")

    def test_P3_standard_errors(self):
        self._share("se_ok", "compared", 0.99, "P3")

    def test_P4_sample_sizes(self):
        self._share("n_ok", "n_checked", 0.99, "P4")

    def test_P5_asterisk_rule(self):
        s = self.rep["asterisk_rule_rse_gt_0.30"]
        self.assertGreater(s["rows"], 0)
        self.assertGreaterEqual(s["agree"] / s["rows"], 0.95, "P5: %d of %d agree; first %s" % (
            s["agree"], s["rows"], json.dumps(s["disagree_first50"][:2])))

    def test_P6_no_zero_weight_only_psus(self):
        self.assertEqual(self.zero_only, 0, "P6: zero-weight-only PSUs")

    def test_P7_time(self):
        # P7 also names peak memory <= 1 GB, which a unit test cannot measure; only the time half is checked.
        self.assertLess(self.seconds, 600.0, "P7: %.0f s" % self.seconds)


if __name__ == "__main__":
    unittest.main()
