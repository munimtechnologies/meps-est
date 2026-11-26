# test_validate_ex1.py - online validation: meps_est against AHRQ's published MEPS workshop outputs.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""Pre-registered check meps-ex1 (docs/preregistrations/prereg-meps-ex1.md, written 2026-11-17 before
any MEPS record was read). It downloads the 2016 (HC-192) and 2015 (HC-181) full-year consolidated
public-use files from meps.ahrq.gov (about 11 MB each), recomputes AHRQ's workshop Exercise 1a/1b
figures with meps_est, and compares them with the figures AHRQ printed (reference_ex1.json; tolerance
half a unit in the last printed digit). Each test below is one pre-registered prediction; a failure
message prints the numbers so the grade can be read from the test log.

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

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://meps.ahrq.gov/mepsweb"
YEARS = {
    2016: ("ex1a_2016", BASE + "/data_files/pufs/h192dat.zip", BASE + "/data_stats/download_data/pufs/h192/h192su.txt"),
    2015: ("ex1b_2015", BASE + "/data_files/pufs/h181dat.zip", BASE + "/data_stats/download_data/pufs/h181/h181su.txt"),
}
RAW_OUTPUTS = {
    "ex1a_2016": "https://raw.githubusercontent.com/HHS-AHRQ/MEPS/master/SAS/workshop_exercises/exercise_1a/Exercise1a_OUTPUT.TXT",
    "ex1b_2015": "https://raw.githubusercontent.com/HHS-AHRQ/MEPS/master/SAS/workshop_exercises/exercise_1b/Exercise1b_OUTPUT.TXT",
}
CACHE = os.environ.get("MEPS_CACHE") or os.path.join(tempfile.gettempdir(), "meps-est-cache")
POINT = ("n", "pop", "mean", "total", "ratio", "records", "used", "nonpositive", "strata", "psus")
SE = ("se_mean", "se_total", "se_ratio")


def _fmt(rows, raw, limit=6):
    out = []
    for r in rows[:limit]:
        ref = r["ref"]
        in_raw = None if raw is None else (ref in raw or (abs(float(ref)) >= 1000 and "." not in ref and
                                                          format(float(ref), ",.0f") in raw))
        out.append("%s.%s ref=%s ours=%s ref_in_raw=%s" % (r["figure"], r["stat"], ref,
                                                         "None" if r["ours"] is None else "%.10g" % r["ours"], in_raw))
    return "; ".join(out) + (" (+%d more)" % (len(rows) - limit) if len(rows) > limit else "")


@unittest.skipIf(os.environ.get("MEPS_OFFLINE") == "1", "MEPS_OFFLINE=1")
class AhrqWorkshopExercise1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "reference_ex1.json"), encoding="utf-8") as fh:
            refs = json.load(fh)
        cls.rows, cls.raw, cls.seconds = {}, {}, {}
        for year, (key, dat, su) in sorted(YEARS.items()):
            dest = os.path.join(CACHE, str(year))
            with contextlib.redirect_stdout(io.StringIO()):
                me._fetch([dat, su], dest)
            with open(os.path.join(dest, su.rsplit("/", 1)[-1]), encoding="latin-1") as fh:
                layout_text = fh.read()
            t0 = time.time()
            fig = me.workshop(year, os.path.join(dest, dat.rsplit("/", 1)[-1]), layout_text)
            cls.seconds[key] = time.time() - t0
            cls.rows[key], _, _ = me.compare(fig, refs[key]["figures"])
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    me._fetch([RAW_OUTPUTS[key]], os.path.join(CACHE, "ref-" + key))
                with open(os.path.join(CACHE, "ref-" + key, RAW_OUTPUTS[key].rsplit("/", 1)[-1]),
                          encoding="latin-1") as fh:
                    cls.raw[key] = fh.read()
            except Exception:  # the raw printout is only used to label transcription errors
                cls.raw[key] = None

    def _check(self, name, key, keep):
        rows = [r for r in self.rows[key] if keep(r)]
        bad = [r for r in rows if not r["match"]]
        self.assertTrue(rows, "%s: nothing compared" % name)
        self.assertEqual(bad, [], "%s %s: %d of %d match; %s" % (
            name, key, len(rows) - len(bad), len(rows), _fmt(bad, self.raw.get(key))))

    def test_P1_2016_design(self):
        self._check("P1", "ex1a_2016", lambda r: r["figure"] == "design")

    def test_P2_2016_figures(self):
        self._check("P2", "ex1a_2016", lambda r: r["figure"] != "design")

    def test_P3_2015_design(self):
        self._check("P3", "ex1b_2015", lambda r: r["figure"] == "design")

    def test_P4_2015_point_estimates(self):
        self._check("P4", "ex1b_2015", lambda r: r["figure"] != "design" and r["stat"] in POINT)

    def test_P5_2015_standard_errors(self):
        self._check("P5", "ex1b_2015", lambda r: r["stat"] in SE)

    def test_P6_overall_at_least_98_percent(self):
        rows = self.rows["ex1a_2016"] + self.rows["ex1b_2015"]
        ok = sum(r["match"] for r in rows)
        self.assertEqual(len(rows), 230, "P6: compared %d stats, pre-registered 230" % len(rows))
        self.assertGreaterEqual(ok / len(rows), 0.98, "P6: %d of %d match" % (ok, len(rows)))

    def test_P7_time(self):
        # P7 also names peak memory <= 1 GB, which a unit test cannot measure; only the time half is checked.
        for key, s in self.seconds.items():
            self.assertLess(s, 300.0, "P7: workshop %s took %.1f s" % (key, s))


if __name__ == "__main__":
    unittest.main()
