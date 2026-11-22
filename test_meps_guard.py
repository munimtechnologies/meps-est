# test_meps_guard.py - tests for meps_guard.py 0.1.1 and meps_mcp.py 0.3.0 on synthetic MEPS-shaped files.
# Copyright 2026 Munim, Inc. Apache-2.0. Made with AI agents working in MV2's name.
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import meps_est as me
import meps_guard as mg
import meps_mcp as mm

HERE = os.path.dirname(os.path.abspath(__file__))

# name, width, decimals, is_char
SPEC = [("DUPERSID", 8, 0, True), ("PERWT22F", 12, 4, False), ("SAQWT22F", 12, 4, False),
        ("DIABW22F", 12, 4, False), ("VARSTR", 4, 0, False), ("VARPSU", 2, 0, False),
        ("AGE22X", 4, 0, False), ("AGE42X", 4, 0, False), ("AGE31X", 4, 0, False),
        ("REGION22", 3, 0, False), ("REGION42", 3, 0, False), ("REGION31", 3, 0, False),
        ("SEX", 2, 0, False), ("INSCOV22", 3, 0, False), ("INSURC22", 3, 0, False),
        ("TOTEXP22", 8, 0, False), ("ADGENH42", 3, 0, False), ("DSA1C53", 3, 0, False),
        ("DDNWRK22", 4, 0, False), ("GRP", 2, 0, False)]


def synthetic(n=200):
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
        if i % 37 == 5:   # end-of-year age missing: AHRQ falls back to AGE42X
            r["AGE22X"] = -1
        recs.append(r)
    return recs


def layout_text():
    lines, pos = ["INPUT"], 1
    for name, width, dec, is_char in SPEC:
        lines.append("  @%d %s %s%d.%s" % (pos, name, "$" if is_char else "", width, dec if dec else ""))
        pos += width
    lines.append(";")
    lines.append('LABEL TOTEXP22 ="TOTAL HEALTH CARE EXP 22"')
    lines.append('  AGE22X ="AGE AS OF 12/31/22 (EDITED/IMPUTED)"')
    return "\n".join(lines) + "\n"


def data_text(recs):
    out = []
    for r in recs:
        parts = []
        for name, width, dec, is_char in SPEC:
            v = r[name]
            if is_char:
                parts.append(str(v).ljust(width))
            elif dec:
                parts.append(("%" + str(width) + "." + str(dec) + "f") % v)
            else:
                parts.append(("%" + str(width) + "d") % v)
        out.append("".join(parts))
    return "\r\n".join(out) + "\r\n"


def write_files(td, recs):
    with open(os.path.join(td, "h243su.txt"), "w", encoding="latin-1") as fh:
        fh.write(layout_text())
    with zipfile.ZipFile(os.path.join(td, "h243dat.zip"), "w") as zf:
        zf.writestr("h243.dat", data_text(recs))


UNINS_BY_REGION = {"year": 2022, "stat": "proportion", "variable": "INSURC{yy}",
                   "condition": {"op": "==", "value": 3},
                   "domain": [{"variable": "AGELAST", "op": "<", "value": 65}],
                   "by": {"variable": "REGIONX", "levels": [1, 2, 3, 4],
                          "labels": {"1": "Northeast", "2": "Midwest", "3": "South", "4": "West"}},
                   "question": "What share of people under 65 were uninsured all year, by region?"}
UNINS_ALL = {k: v for k, v in UNINS_BY_REGION.items() if k != "by"}
UNINS_ALL["question"] = "What share of people under 65 were uninsured all year?"


def codes(refusals):
    return sorted(set(r["code"] for r in refusals))


class PolicyTests(unittest.TestCase):
    def test_allowed_plan(self):
        plan, ref = mg.plan_request(UNINS_BY_REGION)
        self.assertEqual(ref, [])
        self.assertEqual(plan["weight"], "PERWT22F")
        self.assertEqual(plan["variables"], ["AGELAST", "INSURC22", "REGIONX"])
        self.assertEqual(mg.plan_columns(plan)[:3], ["PERWT22F", "VARSTR", "VARPSU"])
        self.assertIn("AGE42X", mg.plan_columns(plan))

    def test_subsample_weights(self):
        base = {"year": 2022, "stat": "proportion", "condition": {"op": "in", "value": [4, 5]}}
        plan, ref = mg.plan_request(dict(base, variable="ADGENH42"))
        self.assertEqual((ref, plan["weight"], plan["subsample"]), ([], "SAQWT22F", "SAQ"))
        plan, ref = mg.plan_request(dict(base, variable="K6SUM42"))
        self.assertEqual(plan["weight"], "SAQWT22F")
        plan, ref = mg.plan_request(dict(base, variable="DSA1C53"))
        self.assertEqual(plan["weight"], "DIABW22F")
        plan, ref = mg.plan_request(dict(base, variable="SAQELIG"))
        self.assertEqual(plan["weight"], "PERWT22F")
        _, ref = mg.plan_request(dict(base, variable="ADGENH42", weight="PERWT{yy}F"))
        self.assertEqual(codes(ref), ["WRONG_WEIGHT"])
        plan, ref = mg.plan_request(dict(base, variable="ADGENH42", weight="saqwt22f"))
        self.assertEqual(ref, [])
        _, ref = mg.plan_request(dict(base, variable="ADGENH42",
                                      domain=[{"variable": "DSA1C53", "op": ">", "value": 0}]))
        self.assertIn("MIXED_SUBSAMPLES", codes(ref))

    def test_refusal_codes(self):
        ok = {"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}"}
        cases = [
            (dict(ok, question="What is mean spending in each state?"), "GEO_BELOW_REGION"),
            (dict(ok, question="Mean spending in California"), "GEO_BELOW_REGION"),
            (dict(ok, question="spending by county"), "GEO_BELOW_REGION"),
            (dict(ok, domain=[{"variable": "STATEFIP", "op": "==", "value": 6}]), "GEO_BELOW_REGION"),
            (dict(ok, question="What is the effect of private insurance on spending?"), "CAUSAL_CLAIM"),
            (dict(ok, question="Does being uninsured cause lower spending?"), "CAUSAL_CLAIM"),
            (dict(ok, stat="max"), "RECORD_LEVEL"),
            (dict(ok, by={"variable": "DUPERSID", "levels": ["P0000001"]}), "RECORD_LEVEL"),
            (dict(ok, question="List all respondents over 90"), "RECORD_LEVEL"),
            (dict(ok, question="Who are the highest spenders?"), "RECORD_LEVEL"),
            (dict(ok, question="Should I buy private insurance?"), "INDIVIDUAL_ADVICE"),
            (dict(ok, years=[2021, 2022]), "POOLED_YEARS"),
            (dict(ok, unweighted=True), "UNWEIGHTED"),
            (dict(ok, weight="none"), "UNWEIGHTED"),
            (dict(ok, stat="median"), "UNSUPPORTED_STAT"),
            (dict(ok, year=2018), "UNSUPPORTED_YEAR"),
            (dict(ok, domain=[{"variable": "VARPSU", "op": "==", "value": 1}]), "DESIGN_VARIABLE"),
            (dict(ok, variable="PERWT{yy}F"), "DESIGN_VARIABLE"),
            (dict(ok, by={"variable": "AGELAST", "levels": list(range(61))}), "TOO_MANY_LEVELS"),
            (dict(ok, color="blue"), "BAD_REQUEST"),
            (dict(ok, compare=[1, 2]), "BAD_REQUEST"),
            (dict(ok, stat="proportion"), "BAD_REQUEST"),
            ("not an object", "BAD_REQUEST"),
            # 0.1.1 question screen
            (dict(ok, question="Mean spending among Californians"), "GEO_BELOW_REGION"),
            (dict(ok, question="Compare spending in rural and urban areas"), "GEO_BELOW_REGION"),
            (dict(ok, question="Why do women spend more than men?"), "CAUSAL_CLAIM"),
            (dict(ok, question="Who paid the most out of pocket?"), "RECORD_LEVEL"),
            (dict(ok, question="Is my spending normal for my age?"), "INDIVIDUAL_ADVICE"),
        ]
        for req, code in cases:
            plan, ref = mg.plan_request(req)
            self.assertIsNone(plan, req)
            self.assertIn(code, codes(ref), req)

    def test_no_false_positives(self):
        ok = {"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}"}
        for q in ("Mean health care spending in the United States by region",
                  "Average days of work missed due to illness among adults",
                  "Mean spending for people with high blood pressure, 2022",
                  "How does mean spending differ between men and women?",
                  "Mean spending among people living in the United States with diabetes",
                  "Share of adults who delayed care because of cost",
                  "Number of people affected by asthma who had an ER visit",
                  "Do people with Medicaid have lower out-of-pocket costs than people with private insurance?",
                  "Spending among the top 5 percent of spenders"):
            plan, ref = mg.plan_request(dict(ok, question=q))
            self.assertEqual(ref, [], q)

    def test_screen_question(self):
        self.assertEqual(mg.screen_question(""), [])
        self.assertEqual(mg.screen_question(None), [])
        self.assertEqual(mg.screen_question("Mean spending among Californians"), ["GEO_BELOW_REGION"])
        self.assertEqual(mg.screen_question("Why do women spend more?"), ["CAUSAL_CLAIM"])
        self.assertEqual(mg.screen_question("Who paid the most out of pocket?"), ["RECORD_LEVEL"])
        self.assertEqual(mg.screen_question("Is my spending normal?"), ["INDIVIDUAL_ADVICE"])
        self.assertEqual(mg.screen_question("Spending   in\n NYC"), ["GEO_BELOW_REGION"])

    def test_single_year_list(self):
        plan, ref = mg.plan_request({"years": [2021], "stat": "count"})
        self.assertEqual(ref, [])
        self.assertEqual(plan["weight"], "PERWT21F")


class DisclosureTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(mg.disclose("mean", 100.0, 10.0, 500)["status"], "ok")
        self.assertEqual(mg.disclose("mean", 100.0, 10.0, 59)["status"], "suppressed")
        self.assertEqual(mg.disclose("mean", 100.0, 0.0, 500)["status"], "suppressed")
        self.assertEqual(mg.disclose("total", 100.0, 60.0, 500)["status"], "suppressed")
        self.assertEqual(mg.disclose("total", 100.0, 35.0, 500)["status"], "flagged")
        self.assertEqual(mg.disclose("proportion", 0.01, 0.006, 500)["status"], "flagged")   # small-p exception
        self.assertEqual(mg.disclose("proportion", 0.01, 0.05, 500)["status"], "suppressed")
        self.assertEqual(mg.disclose("mean", 0.01, 0.006, 500)["status"], "suppressed")      # not a proportion
        self.assertEqual(mg.disclose("mean", float("nan"), float("nan"), 0)["status"], "suppressed")
        self.assertEqual(mg.disclose("proportion", 0.0, 0.0, 500)["status"], "suppressed")


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.recs = synthetic()

    def run_req(self, req):
        plan, ref = mg.plan_request(req)
        self.assertEqual(ref, [], req)
        return plan, mg.execute(plan, self.recs, {"data_sha256": "x"})

    def test_proportion_matches_design(self):
        plan, out = self.run_req(UNINS_BY_REGION)
        self.assertEqual(out["status"], "ok")
        d = me.Design(self.recs, weight="PERWT22F")
        for res in out["results"]:
            lev = res["level"]
            e = d.estimate(lambda r: 1.0 if r["INSURC22"] == 3 else 0.0,
                           lambda r: mg.get_value(r, "AGELAST", "22") < 65 and r["REGION22"] == lev)
            self.assertEqual(res["n"], e["n"])
            if res["value"] is not None:
                self.assertAlmostEqual(res["value"], e["mean"], places=12)
                self.assertAlmostEqual(res["se"], e["se_mean"], places=12)
        _, out = self.run_req(UNINS_ALL)
        res = out["results"][0]
        e = d.estimate(lambda r: 1.0 if r["INSURC22"] == 3 else 0.0,
                       lambda r: mg.get_value(r, "AGELAST", "22") < 65)
        self.assertGreaterEqual(res["n"], 60)
        self.assertNotEqual(res["disclosure"]["status"], "suppressed")
        self.assertAlmostEqual(res["value"], e["mean"], places=12)
        self.assertAlmostEqual(res["se"], e["se_mean"], places=12)
        self.assertAlmostEqual(res["percent"], 100 * e["mean"], places=10)
        self.assertAlmostEqual(res["population_weighted"], e["pop"], places=6)

    def test_reserved_never_in_domain(self):
        _, out = self.run_req({"year": 2022, "stat": "count",
                               "domain": [{"variable": "AGE22X", "op": "<", "value": 65}]})
        want = sum(1 for r in self.recs if r["PERWT22F"] > 0 and 0 <= r["AGE22X"] < 65)
        have_neg = sum(1 for r in self.recs if r["PERWT22F"] > 0 and r["AGE22X"] == -1)
        self.assertGreater(have_neg, 0)
        self.assertEqual(out["results"][0]["n"], want)

    def test_agelast_falls_back(self):
        r = [x for x in self.recs if x["AGE22X"] == -1][0]
        self.assertEqual(mg.get_value(r, "AGELAST", "22"), r["AGE42X"])

    def test_missing_in_total_refused_and_mean_excludes(self):
        plan, ref = mg.plan_request({"year": 2022, "stat": "total", "variable": "DDNWRK{yy}"})
        out = mg.execute(plan, self.recs)
        self.assertEqual(out["status"], "refused")
        self.assertEqual(codes(out["refusals"]), ["MISSING_IN_TOTAL"])
        self.assertNotIn("results", out)
        _, out = self.run_req({"year": 2022, "stat": "total", "variable": "DDNWRK{yy}",
                               "domain": [{"variable": "DDNWRK{yy}", "op": ">=", "value": 0}]})
        self.assertEqual(out["status"], "ok")
        _, out = self.run_req({"year": 2022, "stat": "mean", "variable": "DDNWRK{yy}"})
        res = out["results"][0]
        miss = sum(1 for r in self.recs if r["PERWT22F"] > 0 and r["DDNWRK22"] == -1)
        self.assertEqual(res["n_excluded_missing"], miss)
        self.assertEqual(res["n"] + miss, res["n_domain_before_missing"])
        vals = [(r["PERWT22F"], r["DDNWRK22"]) for r in self.recs if r["PERWT22F"] > 0 and r["DDNWRK22"] >= 0]
        self.assertAlmostEqual(res["value"], sum(w * v for w, v in vals) / sum(w for w, _ in vals), places=10)

    def test_saq_weight_used(self):
        plan, out = self.run_req({"year": 2022, "stat": "proportion", "variable": "ADGENH42",
                                  "condition": {"op": "in", "value": [4, 5]}})
        self.assertEqual(out["receipt"]["design"]["weight"], "SAQWT22F")
        adults = sum(1 for r in self.recs if r["SAQWT22F"] > 0)
        self.assertEqual(out["results"][0]["n"], adults)

    def test_suppressed_has_no_numbers(self):
        _, out = self.run_req({"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}",
                               "domain": [{"variable": "AGELAST", "op": "==", "value": 7}]})
        res = out["results"][0]
        self.assertEqual(res["disclosure"]["status"], "suppressed")
        for k in ("value", "se", "ci95", "population_weighted", "weighted_share_excluded"):
            self.assertIsNone(res[k], k)

    def test_suppressed_count_does_not_leak(self):
        # 0.1.0 returned population_weighted for a suppressed count: the suppressed number itself.
        _, out = self.run_req({"year": 2022, "stat": "count",
                               "domain": [{"variable": "AGELAST", "op": "==", "value": 7}]})
        res = out["results"][0]
        self.assertEqual(res["disclosure"]["status"], "suppressed")
        self.assertIsNone(res["value"])
        self.assertIsNone(res["population_weighted"])
        self.assertIsNone(res["weighted_share_excluded"])
        pop = sum(r["PERWT22F"] for r in self.recs if r["PERWT22F"] > 0 and mg.get_value(r, "AGELAST", "22") == 7)
        self.assertGreater(pop, 0)
        text = json.dumps(out["receipt"])
        self.assertNotIn(repr(float(pop)), text)

    def test_difference_disjoint_strata(self):
        _, out = self.run_req({"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}",
                               "by": {"variable": "GRP", "levels": [1, 2]}, "compare": [1, 2]})
        a, b = out["results"]
        c = out["comparison"]
        self.assertEqual(c["status"], "ok")
        self.assertAlmostEqual(c["difference"], a["value"] - b["value"], places=9)
        self.assertAlmostEqual(c["se"] ** 2 / (a["se"] ** 2 + b["se"] ** 2), 1.0, places=10)

    def test_difference_overlapping_domains(self):
        d = me.Design(self.recs, weight="PERWT22F")
        y = lambda r: float(r["TOTEXP22"])
        diff, se = mg.domain_difference(d, y, lambda r: True, lambda r: True, "mean")
        self.assertAlmostEqual(diff, 0.0, places=9)
        self.assertAlmostEqual(se, 0.0, places=9)
        diff, se = mg.domain_difference(d, y, lambda r: True, lambda r: r["SEX"] == 2, "total")
        e = d.estimate(y, lambda r: r["SEX"] == 1)
        self.assertAlmostEqual(diff, e["total"], places=6)
        self.assertAlmostEqual(se, e["se_total"], places=6)

    def test_receipt(self):
        _, out1 = self.run_req(UNINS_BY_REGION)
        _, out2 = self.run_req(UNINS_BY_REGION)
        rc = out1["receipt"]
        self.assertEqual(out1["receipt_id"], out2["receipt_id"])
        self.assertEqual(mg.receipt_id(rc), rc["receipt_id"])
        json.dumps(rc, allow_nan=False)
        for key in ("plan", "data", "design", "policy", "results", "limitations", "software"):
            self.assertIn(key, rc)
        self.assertEqual(rc["software"]["meps_guard"], "0.1.1")
        self.assertEqual(rc["design"]["degrees_of_freedom"], 4)
        tampered = json.loads(json.dumps(rc))
        tampered["results"][0]["n"] += 1
        self.assertNotEqual(mg.receipt_id(tampered), rc["receipt_id"])

    def test_labels(self):
        self.assertEqual(mg.parse_labels(layout_text())["TOTEXP22"], "TOTAL HEALTH CARE EXP 22")


def answer_for(res, rid, pct=None):
    pct = res["percent"] if pct is None else pct
    lo, hi = res["ci95"][0] * 100.0, res["ci95"][1] * 100.0
    return ("In 2022, %.1f%% of people under 65 were uninsured all year (95%% CI %.1f%% to %.1f%%; "
            "receipt %s; interpret with care if marked *)." % (pct, lo, hi, rid[:12]))


class MCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        write_files(cls.td, synthetic())
        cls.receipts = os.path.join(cls.td, "receipts")
        cls.server = mm.Server(cls.td, cls.receipts)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def call(self, name, args, mid=1, server=None):
        resp = (server or self.server).handle({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                                               "params": {"name": name, "arguments": args}})
        self.assertEqual(resp["id"], mid)
        return resp["result"]

    def test_initialize_and_list(self):
        r = self.server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "t", "version": "0"}}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(r["result"]["serverInfo"]["version"], "0.3.0")
        self.assertIn("meps_verify_answer", r["result"]["instructions"])
        self.assertIn("meps_final_answer", r["result"]["instructions"])
        r = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r["result"]["protocolVersion"], mm.PROTOCOL_VERSIONS[0])
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        r = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        self.assertEqual(names, ["meps_policy", "meps_find_variables", "meps_check_request", "meps_estimate",
                                 "meps_verify_answer", "meps_final_answer"])
        for t in r["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")
        est = [t for t in r["result"]["tools"] if t["name"] == "meps_estimate"][0]
        self.assertIn("question", est["inputSchema"]["required"])
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"], -32601)
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 4, "method": "ping"})["result"], {})

    def test_estimate_and_receipt_file(self):
        res = self.call("meps_estimate", UNINS_BY_REGION)
        self.assertFalse(res["isError"])
        sc = res["structuredContent"]
        self.assertEqual(sc["status"], "ok")
        self.assertEqual(json.loads(res["content"][0]["text"])["receipt_id"], sc["receipt_id"])
        path = os.path.join(self.receipts, sc["receipt_id"] + ".json")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(sc["receipt"]["data"]["data_sha256"], mg.file_sha256(os.path.join(self.td, "h243dat.zip")))
        self.assertEqual(sc["receipt"]["design"]["records_read"], 200)
        self.assertEqual(sc["receipt"]["design"]["records_weight_le_0_left_out"], 1)

    def test_question_required(self):
        for args in ({"year": 2022, "stat": "count"}, {"year": 2022, "stat": "count", "question": "   "}):
            sc = self.call("meps_estimate", args)["structuredContent"]
            self.assertEqual(sc["status"], "refused")
            self.assertEqual(codes(sc["refusals"]), ["QUESTION_REQUIRED"])
            self.assertNotIn("results", sc)
        sc = self.call("meps_check_request", {"year": 2022, "stat": "count"})["structuredContent"]
        self.assertEqual(sc["status"], "allowed")

    def test_refusals_and_errors(self):
        res = self.call("meps_estimate", {"year": 2022, "stat": "mean", "variable": "TOTEXP{yy}",
                                          "question": "What is the effect of insurance on spending?"})
        self.assertEqual(res["structuredContent"]["status"], "refused")
        self.assertNotIn("results", res["structuredContent"])
        res = self.call("meps_estimate", {"year": 2022, "stat": "mean", "variable": "NOSUCH{yy}",
                                          "question": "Mean of a variable"})
        self.assertEqual(codes(res["structuredContent"]["refusals"]), ["UNKNOWN_VARIABLE"])
        res = self.call("meps_estimate", {"year": 2022, "stat": "mean", "variable": "DUPERSID",
                                          "question": "Mean of a variable"})
        self.assertEqual(codes(res["structuredContent"]["refusals"]), ["RECORD_LEVEL"])
        res = self.call("meps_estimate", {"year": 2021, "stat": "count", "question": "How many people?"})
        self.assertTrue(res["isError"])
        r = self.server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_find_and_check_and_policy(self):
        sc = self.call("meps_find_variables", {"year": 2022, "pattern": "^AGE"})["structuredContent"]
        self.assertEqual([v["name"] for v in sc["variables"]], ["AGE22X", "AGE31X", "AGE42X"])
        sc = self.call("meps_check_request", UNINS_BY_REGION)["structuredContent"]
        self.assertEqual(sc["status"], "allowed")
        sc = self.call("meps_policy", {})["structuredContent"]
        self.assertIn("GEO_BELOW_REGION", sc["refusal_codes"])
        self.assertIn("QUESTION_REQUIRED", sc["refusal_codes"])
        self.assertEqual(sc["guard_version"], "0.1.1")

    def test_verify_answer_tool(self):
        sc = self.call("meps_estimate", UNINS_ALL)["structuredContent"]
        self.assertEqual(sc["status"], "ok")
        rid, res = sc["receipt_id"], sc["results"][0]
        self.assertNotEqual(res["disclosure"]["status"], "suppressed")
        good = answer_for(res, rid)
        v = self.call("meps_verify_answer", {"answer": good, "receipt_ids": [rid[:12]]})
        self.assertFalse(v["isError"])
        self.assertEqual(v["structuredContent"]["status"], "pass", v["structuredContent"]["findings"])
        self.assertEqual(v["structuredContent"]["receipts_used"], [rid[:12]])
        v = self.call("meps_verify_answer", {"answer": good})          # whole session
        self.assertEqual(v["structuredContent"]["status"], "pass", v["structuredContent"]["findings"])
        bad = answer_for(res, rid, pct=99.9)
        v = self.call("meps_verify_answer", {"answer": bad, "receipt_ids": [rid]})
        self.assertEqual(v["structuredContent"]["status"], "blocked")
        self.assertIn("UNGROUNDED_NUMBER", codes(v["structuredContent"]["findings"]))
        v = self.call("meps_verify_answer", {"answer": good + " Insurance causes lower spending.",
                                             "receipt_ids": [rid]})
        self.assertIn("CAUSAL_LANGUAGE", codes(v["structuredContent"]["findings"]))
        # a fresh server finds the receipt in the receipts folder
        fresh = mm.Server(self.td, self.receipts)
        v = self.call("meps_verify_answer", {"answer": good, "receipt_ids": [rid]}, server=fresh)
        self.assertEqual(v["structuredContent"]["status"], "pass", v["structuredContent"]["findings"])
        # errors: unknown id, malformed id, empty answer; empty session with numbers is blocked
        self.assertTrue(self.call("meps_verify_answer", {"answer": good, "receipt_ids": ["deadbeef00"]})["isError"])
        self.assertTrue(self.call("meps_verify_answer", {"answer": good, "receipt_ids": ["../x"]})["isError"])
        self.assertTrue(self.call("meps_verify_answer", {"answer": "  "})["isError"])
        empty = mm.Server(self.td, None)
        v = self.call("meps_verify_answer", {"answer": "About 41.5% were uninsured."}, server=empty)
        self.assertEqual(v["structuredContent"]["status"], "blocked")
        self.assertIn("NO_RECEIPT", codes(v["structuredContent"]["findings"]))

    def test_final_answer_tool(self):
        sc = self.call("meps_estimate", UNINS_ALL)["structuredContent"]
        rid, res = sc["receipt_id"], sc["results"][0]
        star = "*" if res.get("asterisk") else ""
        plain = ("In 2022, %.1f%%%s of people under 65 were uninsured all year (95%% CI %.1f%% to %.1f%%; "
                 "receipt %s)." % (res["percent"], star, res["ci95"][0] * 100.0, res["ci95"][1] * 100.0, rid[:12]))
        out = self.call("meps_final_answer", {"draft": plain, "receipt_ids": [rid[:12]]})
        self.assertFalse(out["isError"])
        fa = out["structuredContent"]
        self.assertEqual((fa["status"], fa["answer"]), ("pass", plain), fa["findings"])
        self.assertEqual(fa["server"]["meps_mcp"], "0.3.0")
        # a causal paraphrase the word list misses: replaced by the rendered descriptive answer
        fa = self.call("meps_final_answer", {"draft": plain + " Coverage translates into fewer uninsured people.",
                                             "receipt_ids": [rid]})["structuredContent"]
        self.assertEqual(fa["status"], "replaced", fa)
        self.assertIn("Receipt %s." % rid[:12], fa["answer"])
        self.assertNotIn("translates", fa["answer"])
        # no receipts at all: nothing may be shown
        empty = mm.Server(self.td, None)
        fa = self.call("meps_final_answer", {"draft": "About 41.5% were uninsured."}, server=empty)["structuredContent"]
        self.assertEqual(fa["status"], "blocked")
        self.assertIsNone(fa["answer"])
        self.assertTrue(self.call("meps_final_answer", {"draft": " "})["isError"])

    def test_stdio_subprocess(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "meps_estimate", "arguments": UNINS_BY_REGION}}]
        text = "\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"
        p = subprocess.run([sys.executable, os.path.join(HERE, "meps_mcp.py"), "--data-dir", self.td],
                           input=text, capture_output=True, text=True, timeout=120, cwd=HERE)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
        self.assertEqual([o.get("id") for o in out], [1, 2, None])
        self.assertEqual(out[1]["result"]["structuredContent"]["status"], "ok")
        self.assertEqual(out[2]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
