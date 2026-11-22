#!/usr/bin/env python3
# meps_est.py - read MEPS public-use ASCII files and compute design-based survey estimates.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""Design-based estimates from AHRQ's Medical Expenditure Panel Survey (MEPS) in plain Python.

Standard library only. It reads a MEPS Household Component public-use file in its ASCII form
(the .dat inside the hNNNdat.zip download), using the column positions in AHRQ's own SAS
programming statements (hNNNsu.txt), and computes weighted means, totals, proportions, domain
(subpopulation) estimates and ratios with Taylor-series linearization standard errors, the
method of SAS PROC SURVEYMEANS (STRATUM VARSTR; CLUSTER VARPSU; WEIGHT PERWTyyF), R's survey
package and Stata's svy prefix:

    V = sum_h n_h/(n_h - 1) * sum_i (z_hi - zbar_h)^2,  z_hi = sum over PSU i of w_j * u_j

with u_j = y_j for a total, (y_j - mean)/W for a mean, d_j*(...) inside a domain (the whole
sample design is kept, as SAS's DOMAIN statement does) and (y_j - R x_j)/X for a ratio. Records
with a weight <= 0 are left out, as SAS does. Strata with one PSU add nothing to the variance.

Commands:
    fetch      download MEPS files (skips files already present)
    layout     list the columns the SAS statements define
    workshop   reproduce AHRQ's MEPS workshop Exercise 1 tables for one year
    compare    compare workshop output with published reference figures
"""
__version__ = "0.1.0"

import argparse
import contextlib
import decimal
import io
import json
import math
import os
import re
import sys
import urllib.request
import zipfile

LAYOUT_RE = re.compile(r"@(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\$?)(\d+)\.(\d*)")


def parse_layout(text):
    """Column specs {NAME: (start, width, decimals, is_char)} from SAS INPUT statements."""
    cols = {}
    for m in LAYOUT_RE.finditer(text):
        start, name, dollar, width, dec = m.groups()
        name = name.upper()
        spec = (int(start), int(width), int(dec or 0), dollar == "$")
        if name in cols and cols[name] != spec:
            raise ValueError("conflicting layout for %s: %r vs %r" % (name, cols[name], spec))
        cols[name] = spec
    return cols


def parse_value(field, dec, is_char):
    """One fixed-width field. Numbers follow the SAS w.d informat: an explicit decimal point
    wins; otherwise d implied decimals are applied. Blank or '.' is missing (None)."""
    if is_char:
        return field.strip()
    s = field.strip()
    if not s or s == ".":
        return None
    if "." in s or "e" in s or "E" in s:
        return float(s)
    v = int(s)
    return v / (10 ** dec) if dec else v


@contextlib.contextmanager
def open_data(path):
    """A text handle on a MEPS .dat file, or on the single .dat inside a .zip."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".dat")]
            if len(names) != 1:
                names = [n for n in zf.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise ValueError("expected one data file in %s, found %r" % (path, zf.namelist()))
            with zf.open(names[0]) as raw:
                yield io.TextIOWrapper(raw, encoding="latin-1", newline="")
    else:
        with open(path, encoding="latin-1", newline="") as fh:
            yield fh


def read_records(path, layout, keep):
    """Yield one dict per record with only the columns in keep."""
    keep = [k.upper() for k in keep]
    missing = [k for k in keep if k not in layout]
    if missing:
        raise KeyError("not in layout: " + ", ".join(missing))
    specs = [(k,) + tuple(layout[k]) for k in keep]
    need = max(s + w - 1 for _, s, w, _, _ in specs)
    with open_data(path) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            if len(line) < need:
                raise ValueError("record %d has %d characters, need %d" % (lineno, len(line), need))
            rec = {}
            for name, start, width, dec, is_char in specs:
                rec[name] = parse_value(line[start - 1:start - 1 + width], dec, is_char)
            yield rec


class Design:
    """A stratified, clustered sample with weights (first-stage, with-replacement variance)."""

    def __init__(self, records, weight="PERWT", strata="VARSTR", psu="VARPSU"):
        self.n_records = 0
        self.n_nonpositive = 0
        self.rows = []
        for r in records:
            self.n_records += 1
            w = r.get(weight)
            if w is None or w <= 0:
                self.n_nonpositive += 1
                continue
            self.rows.append((float(w), r[strata], r[psu], r))
        groups = {}
        for _, h, p, _ in self.rows:
            groups.setdefault(h, set()).add(p)
        self.n_strata = len(groups)
        self.n_psu = sum(len(s) for s in groups.values())
        self.singleton_strata = sorted(h for h, s in groups.items() if len(s) == 1)
        self.population = math.fsum(w for w, _, _, _ in self.rows)

    def _variance(self, z):
        psu_sums = {}
        for (_, h, p, _), zj in zip(self.rows, z):
            d = psu_sums.setdefault(h, {})
            d[p] = d.get(p, 0.0) + zj
        v = 0.0
        for h in psu_sums:
            vals = list(psu_sums[h].values())
            n = len(vals)
            if n < 2:
                continue
            m = math.fsum(vals) / n
            v += n / (n - 1.0) * math.fsum((x - m) ** 2 for x in vals)
        return v

    def _columns(self, y, domain):
        ys, ds = [], []
        for _, _, _, r in self.rows:
            d = 1 if domain is None or domain(r) else 0
            val = y(r) if callable(y) else r[y]
            if val is None:
                if d:
                    raise ValueError("missing analysis value inside the domain")
                val = 0.0
            ys.append(float(val))
            ds.append(d)
        return ys, ds

    def estimate(self, y, domain=None):
        """Mean, total and their standard errors of y (a column name or a function of a record),
        over the whole sample or a domain (a function of a record returning True/False)."""
        ys, ds = self._columns(y, domain)
        ws = [w for w, _, _, _ in self.rows]
        W = math.fsum(w * d for w, d in zip(ws, ds))
        Y = math.fsum(w * d * v for w, d, v in zip(ws, ds, ys))
        n = sum(ds)
        mean = Y / W if W else float("nan")
        var_total = self._variance([w * d * v for w, d, v in zip(ws, ds, ys)])
        var_mean = self._variance([w * d * (v - mean) / W for w, d, v in zip(ws, ds, ys)]) if W else float("nan")
        return {"n": n, "pop": W, "mean": mean, "se_mean": math.sqrt(var_mean),
                "total": Y, "se_total": math.sqrt(var_total)}

    def ratio(self, y, x, domain=None):
        """Ratio of totals Y/X with its linearized standard error."""
        ys, ds = self._columns(y, domain)
        xs, _ = self._columns(x, domain)
        ws = [w for w, _, _, _ in self.rows]
        Y = math.fsum(w * d * v for w, d, v in zip(ws, ds, ys))
        X = math.fsum(w * d * v for w, d, v in zip(ws, ds, xs))
        R = Y / X
        var = self._variance([w * d * (a - R * b) / X for w, d, a, b in zip(ws, ds, ys, xs)])
        return {"ratio": R, "se_ratio": math.sqrt(var), "total_y": Y, "total_x": X}


# ---------------------------------------------------------------- AHRQ workshop Exercise 1

SERVICES = {
    "total": ["TOTEXP"],
    "ip": ["IPDEXP", "IPFEXP"],
    "amb": ["OBVEXP", "OPDEXP", "OPFEXP", "ERDEXP", "ERFEXP"],
    "rx": ["RXEXP"],
    "dv": ["DVTEXP"],
    "hh": ["HHAEXP", "HHNEXP", "OTHEXP", "VISEXP"],
}


def exercise_columns(year):
    yy = "%02d" % (year % 100)
    cols = ["PERWT%sF" % yy, "VARSTR", "VARPSU", "AGE%sX" % yy, "AGE42X", "AGE31X"]
    for parts in SERVICES.values():
        cols += [p + yy for p in parts]
    return cols


def derive(rec, year):
    """The workshop program's derived variables: service sums, any-expense flags, AGE, AGECAT."""
    yy = "%02d" % (year % 100)
    out = {"PERWT": rec["PERWT%sF" % yy], "VARSTR": rec["VARSTR"], "VARPSU": rec["VARPSU"]}
    for svc, parts in SERVICES.items():
        v = math.fsum(rec[p + yy] for p in parts)
        out[svc] = v
        out["x_" + svc] = 1 if v > 0 else 0
    age = None
    for k in ("AGE%sX" % yy, "AGE42X", "AGE31X"):
        if rec[k] is not None and rec[k] >= 0:
            age = rec[k]
            break
    out["age"] = age
    out["agecat"] = None if age is None else (1 if age <= 64 else 2)
    return out


def workshop(year, data_path, layout_text):
    layout = parse_layout(layout_text)
    recs = [derive(r, year) for r in read_records(data_path, layout, exercise_columns(year))]
    d = Design(recs)
    fig = {"design": {"records": d.n_records, "used": d.n_records - d.n_nonpositive,
                      "nonpositive": d.n_nonpositive, "strata": d.n_strata, "psus": d.n_psu,
                      "singleton_strata": len(d.singleton_strata), "pop": d.population}}
    for svc in SERVICES:
        fig["flag." + svc] = d.estimate("x_" + svc)
        fig["exp.%s.all" % svc] = d.estimate(svc)
        fig["exp.%s.with" % svc] = d.estimate(svc, lambda r, s=svc: r["x_" + s] == 1)
        for a in (1, 2):
            fig["exp.%s.age%d" % (svc, a)] = d.estimate(svc, lambda r, a=a: r["agecat"] == a)
            fig["exp.%s.with.age%d" % (svc, a)] = d.estimate(
                svc, lambda r, s=svc, a=a: r["x_" + s] == 1 and r["agecat"] == a)
        if svc != "total":
            fig["share." + svc] = d.ratio(svc, "total")
    return fig


# ---------------------------------------------------------------- comparison with references

def tolerance(ref):
    """Half a unit in the last printed digit of a reference written as a string."""
    exp = decimal.Decimal(ref.replace(",", "")).as_tuple().exponent
    return 0.5 * (10.0 ** exp)


def compare(ours, refs):
    """refs: {figure: {stat: "printed string"}}. Returns rows and counts."""
    rows = []
    for key, stats in refs.items():
        for stat, ref in stats.items():
            got = ours.get(key, {}).get(stat)
            if got is None:
                rows.append({"figure": key, "stat": stat, "ref": ref, "ours": None, "match": False})
                continue
            want = float(decimal.Decimal(ref.replace(",", "")))
            tol = tolerance(ref)
            diff = got - want
            ok = abs(diff) <= tol * (1 + 1e-9) + 1e-12 * abs(want)
            rows.append({"figure": key, "stat": stat, "ref": ref, "ours": got, "diff": diff,
                         "tol": tol, "rel": (diff / want) if want else None, "match": ok})
    return rows, sum(r["match"] for r in rows), len(rows)


# ---------------------------------------------------------------- command line

def _fetch(urls, dest):
    os.makedirs(dest, exist_ok=True)
    for url in urls:
        name = os.path.join(dest, url.rstrip("/").rsplit("/", 1)[-1])
        if os.path.exists(name) and os.path.getsize(name) > 0:
            print("have %s (%d bytes)" % (name, os.path.getsize(name)))
            continue
        req = urllib.request.Request(url, headers={"User-Agent": "meps-est/%s (Python urllib)" % __version__})
        with urllib.request.urlopen(req, timeout=300) as resp, open(name + ".part", "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(name + ".part", name)
        print("fetched %s (%d bytes)" % (name, os.path.getsize(name)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--dest", required=True)
    f.add_argument("urls", nargs="+")
    lay = sub.add_parser("layout")
    lay.add_argument("--layout", required=True)
    lay.add_argument("names", nargs="*")
    w = sub.add_parser("workshop")
    w.add_argument("--year", type=int, required=True)
    w.add_argument("--data", required=True, help="hNNNdat.zip or the .dat inside it")
    w.add_argument("--layout", required=True, help="hNNNsu.txt (AHRQ's SAS programming statements)")
    w.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--ours", required=True)
    c.add_argument("--refs", required=True)
    c.add_argument("--key", required=True, help="top-level key of the reference file to use")
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        _fetch(a.urls, a.dest)
    elif a.cmd == "layout":
        with open(a.layout, encoding="latin-1") as fh:
            cols = parse_layout(fh.read())
        print("%d columns, record length %d" % (len(cols), max(s + w_ - 1 for s, w_, _, _ in cols.values())))
        for n in a.names:
            print(n.upper(), cols.get(n.upper()))
    elif a.cmd == "workshop":
        with open(a.layout, encoding="latin-1") as fh:
            text = fh.read()
        fig = workshop(a.year, a.data, text)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"meps_est": __version__, "year": a.year, "figures": fig}, fh, indent=1, sort_keys=True)
        print("year %d: %s" % (a.year, json.dumps(fig["design"], sort_keys=True)))
    elif a.cmd == "compare":
        with open(a.ours, encoding="utf-8") as fh:
            ours = json.load(fh)["figures"]
        with open(a.refs, encoding="utf-8") as fh:
            refs = json.load(fh)[a.key]["figures"]
        rows, ok, total = compare(ours, refs)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"key": a.key, "matched": ok, "compared": total, "rows": rows}, fh, indent=1)
        for r in rows:
            if not r["match"]:
                print("MISMATCH %s %s ref %s ours %r" % (r["figure"], r["stat"], r["ref"], r["ours"]))
        print("%s: %d of %d figures match to the printed precision" % (a.key, ok, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
