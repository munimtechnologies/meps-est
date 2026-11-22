#!/usr/bin/env python3
# meps_mcp.py - a Model Context Protocol (MCP) server for guarded MEPS estimates, stdio transport.
# Copyright 2026 Munim, Inc. Licensed under the Apache License, Version 2.0 (Apache-2.0).
# Made with AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com).
"""MCP server (JSON-RPC 2.0, one message per line on stdin/stdout), standard library only.

    python meps_mcp.py --data-dir DIR [--receipts-dir DIR]

DIR holds AHRQ's public-use files as downloaded: hNNNdat.zip (or hNNN.dat) and hNNNsu.txt,
e.g. h243dat.zip and h243su.txt for 2022. Tools:

    meps_policy          the safeguards, refusal codes and disclosure rule
    meps_find_variables  search a year's file layout for variable names (and labels if present)
    meps_check_request   run only the request policy (no data needed)
    meps_estimate        policy -> design-based estimate -> AHRQ disclosure rule -> analysis receipt
                         (0.2.0: the user's question in words is required)
    meps_verify_answer   (0.2.0) check the text an agent is about to show against the receipts:
                         every number must come from a cited receipt, suppressed cells stay
                         suppressed, flagged cells carry a caveat, no causal wording or advice
    meps_final_answer    (0.3.0) the last step: returns the draft only if it passes the verifier
                         AND stays inside the descriptive answer frame (meps_frame); otherwise a
                         rendered descriptive answer built from the receipts; otherwise nothing

Every number returned by meps_estimate comes with a receipt (kept for the session, and written
to --receipts-dir when given).
"""
__version__ = "0.3.0"

import argparse
import json
import os
import re
import sys

import meps_est as me
import meps_frame as mf
import meps_guard as mg
import meps_verify as mv

SERVER_NAME = "meps-guard"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
RID_RE = re.compile(r"^[0-9a-f]{8,64}$")

REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "The user's question in words (checked for causal, "
                     "geographic, record-level and personal-advice wording). Required by meps_estimate."},
        "year": {"type": "integer", "enum": sorted(mg.YEAR_FILES)},
        "stat": {"type": "string", "enum": list(mg.STATS),
                 "description": "mean or total of a numeric variable; proportion of people meeting 'condition'; "
                                "count of people in the domain (weighted)."},
        "variable": {"type": "string", "description": "MEPS variable name; '{yy}' is replaced by the 2-digit "
                     "year (e.g. TOTEXP{yy}). Derived: AGELAST, REGIONX."},
        "condition": {"type": "object", "description": "For proportion: {'op': '==', 'value': 3} or "
                      "{'op': 'in', 'value': [4, 5]}."},
        "domain": {"type": "array", "items": {"type": "object"},
                   "description": "Conditions that define the subpopulation, e.g. [{'variable': 'AGELAST', "
                                  "'op': '>=', 'value': 65}]."},
        "by": {"type": "object", "description": "{'variable': 'REGIONX', 'levels': [1,2,3,4], 'labels': "
               "{'1': 'Northeast'}}: one estimate per listed level."},
        "compare": {"type": "array", "description": "Two levels of 'by' to compare (A minus B)."},
        "weight": {"type": "string", "description": "Optional; the guard chooses the documented weight and "
                   "refuses a different one."},
    },
    "required": ["year", "stat"],
}
ESTIMATE_SCHEMA = dict(REQUEST_SCHEMA, required=["year", "stat", "question"])

RECEIPT_IDS = {"type": "array", "items": {"type": "string"},
               "description": "Receipt ids (or their first 8+ hex characters) the answer relies on. "
                              "If omitted, every receipt from this session is used."}
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "The exact text you intend to show the user."},
        "receipt_ids": RECEIPT_IDS,
    },
    "required": ["answer"],
}
FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "draft": {"type": "string", "description": "Your draft answer. Plain descriptive English; numbers "
                  "copied from the receipts; cite the receipt id."},
        "receipt_ids": RECEIPT_IDS,
    },
    "required": ["draft"],
}

TOOLS = [
    {"name": "meps_policy",
     "description": "Return the safeguards this server enforces: refusal codes, AHRQ's disclosure rule for "
                    "MEPS-HC tables, missing-value handling, supported years and derived variables.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "meps_find_variables",
     "description": "Search one year's MEPS-HC full-year file layout for variable names matching a pattern "
                    "(case-insensitive regular expression).",
     "inputSchema": {"type": "object", "properties": {
         "year": {"type": "integer", "enum": sorted(mg.YEAR_FILES)},
         "pattern": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
         "required": ["year", "pattern"]}},
    {"name": "meps_check_request",
     "description": "Check an analysis request against the policy without running it. Returns the "
                    "normalized plan (weight chosen, variables) or the refusals with their codes.",
     "inputSchema": REQUEST_SCHEMA},
    {"name": "meps_estimate",
     "description": "Design-based estimate from the MEPS-HC public-use file (Taylor-series SEs with VARSTR/"
                    "VARPSU). Refuses what the data cannot support, suppresses cells under AHRQ's rule, and "
                    "returns an analysis receipt with a SHA-256 receipt_id for every answer. Pass the user's "
                    "question in words.",
     "inputSchema": ESTIMATE_SCHEMA},
    {"name": "meps_verify_answer",
     "description": "Check the answer text before showing it: blocks numbers not found in a cited receipt, "
                    "estimates for suppressed cells, flagged (RSE > 30%) estimates without a caveat, causal "
                    "wording and individual advice. status 'pass' or 'blocked' with findings.",
     "inputSchema": VERIFY_SCHEMA},
    {"name": "meps_final_answer",
     "description": "The last step before showing anything. Returns 'answer': your draft if it passes the "
                    "verifier and uses only the descriptive answer vocabulary (status 'pass'); otherwise a "
                    "descriptive answer rendered from the receipts (status 'replaced'); otherwise null "
                    "(status 'blocked'). Show exactly 'answer' and nothing else.",
     "inputSchema": FINAL_SCHEMA},
]


class ToolError(Exception):
    pass


class Server:
    def __init__(self, data_dir, receipts_dir=None):
        self.data_dir = data_dir
        self.receipts_dir = receipts_dir
        self._layouts = {}
        self._hashes = {}
        self._session = {}          # receipt_id -> receipt, for this server process

    # -------------------------------------------------------------- data
    def _files(self, year):
        h = mg.YEAR_FILES[year]
        layout = os.path.join(self.data_dir, h + "su.txt")
        data = None
        for name in (h + "dat.zip", h + ".dat", h + "dat.dat"):
            p = os.path.join(self.data_dir, name)
            if os.path.exists(p):
                data = p
                break
        if data is None or not os.path.exists(layout):
            raise ToolError("MEPS files for %d are not in the data folder: need %s and %s (from %s and %s)" % (
                year, h + "dat.zip", h + "su.txt", mg.DATA_URL.format(h=h), mg.LAYOUT_URL.format(h=h)))
        return data, layout

    def layout(self, year):
        if year not in self._layouts:
            _, lp = self._files(year)
            with open(lp, encoding="latin-1") as fh:
                text = fh.read()
            self._layouts[year] = (me.parse_layout(text), mg.parse_labels(text))
        return self._layouts[year]

    def _sha(self, path):
        key = (path, os.path.getsize(path), os.path.getmtime(path))
        if key not in self._hashes:
            self._hashes[key] = mg.file_sha256(path)
        return self._hashes[key]

    # -------------------------------------------------------------- receipts
    def _find_receipt(self, rid):
        rid = str(rid or "").strip().lower()
        if not RID_RE.match(rid):
            raise ToolError("receipt id %r must be 8-64 hexadecimal characters" % rid[:70])
        hits = {k: v for k, v in self._session.items() if k.startswith(rid)}
        if self.receipts_dir and os.path.isdir(self.receipts_dir):
            for name in os.listdir(self.receipts_dir):
                stem = name[:-5] if name.endswith(".json") else None
                if stem and stem.startswith(rid) and stem not in hits and RID_RE.match(stem):
                    with open(os.path.join(self.receipts_dir, name), encoding="utf-8") as fh:
                        hits[stem] = json.load(fh)
        if not hits:
            raise ToolError("no receipt with id %s in this session or the receipts folder" % rid)
        if len(hits) > 1:
            raise ToolError("receipt id %s is ambiguous (%d receipts start with it)" % (rid, len(hits)))
        return list(hits.values())[0]

    def _receipts(self, args):
        ids = args.get("receipt_ids")
        if ids is None or ids == []:
            return list(self._session.values())
        if isinstance(ids, list):
            return [self._find_receipt(i) for i in ids]
        raise ToolError("'receipt_ids' must be a list of strings")

    # -------------------------------------------------------------- tools
    def tool_policy(self, args):
        return mg.POLICY

    def tool_find(self, args):
        year = args.get("year")
        if year not in mg.YEAR_FILES:
            raise ToolError("year must be one of %s" % sorted(mg.YEAR_FILES))
        try:
            rx = re.compile(str(args.get("pattern", "")), re.I)
        except re.error as exc:
            raise ToolError("bad pattern: %s" % exc)
        limit = min(int(args.get("limit") or 50), 200)
        layout, labels = self.layout(year)
        hits = []
        for name in sorted(layout):
            label = labels.get(name, "")
            if rx.search(name) or (label and rx.search(label)):
                hits.append({"name": name, "label": label, "type": "character" if layout[name][3] else "numeric"})
        return {"year": year, "matches": len(hits), "variables": hits[:limit]}

    def tool_check(self, args):
        plan, refusals = mg.plan_request(args)
        if refusals:
            return {"status": "refused", "refusals": refusals, "guard": mg.__version__}
        return {"status": "allowed", "plan": plan, "columns": mg.plan_columns(plan)}

    def tool_estimate(self, args):
        if not isinstance(args, dict) or not str(args.get("question") or "").strip():
            return {"status": "refused", "guard": mg.__version__,
                    "refusals": [{"code": "QUESTION_REQUIRED",
                                  "message": mg.POLICY["refusal_codes"]["QUESTION_REQUIRED"]}]}
        plan, refusals = mg.plan_request(args)
        if refusals:
            return {"status": "refused", "refusals": refusals, "guard": mg.__version__}
        layout, _ = self.layout(plan["year"])
        refusals = mg.check_layout(plan, layout)
        if refusals:
            return {"status": "refused", "refusals": refusals, "guard": mg.__version__}
        data, lp = self._files(plan["year"])
        records = list(me.read_records(data, layout, mg.plan_columns(plan)))
        prov = {"data_file": os.path.basename(data), "data_sha256": self._sha(data),
                "layout_file": os.path.basename(lp), "layout_sha256": self._sha(lp),
                "data_source": mg.DATA_URL.format(h=mg.YEAR_FILES[plan["year"]]),
                "layout_source": mg.LAYOUT_URL.format(h=mg.YEAR_FILES[plan["year"]])}
        out = mg.execute(plan, records, prov)
        out["request"] = args
        if out["status"] == "ok":
            self._session[out["receipt_id"]] = out["receipt"]
            if self.receipts_dir:
                os.makedirs(self.receipts_dir, exist_ok=True)
                with open(os.path.join(self.receipts_dir, out["receipt_id"] + ".json"), "w", encoding="utf-8") as fh:
                    json.dump(dict(out["receipt"], request=args), fh, indent=1, sort_keys=True)
        return out

    def tool_verify(self, args):
        answer = args.get("answer") if isinstance(args, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise ToolError("'answer' must be the non-empty text you intend to show")
        receipts = self._receipts(args)
        verdict = mv.verify_answer(answer, receipts)
        verdict["receipts_used"] = sorted(str(r.get("receipt_id", ""))[:12] for r in receipts if isinstance(r, dict))
        verdict["server"] = {"meps_mcp": __version__, "meps_guard": mg.__version__}
        return verdict

    def tool_final(self, args):
        draft = args.get("draft") if isinstance(args, dict) else None
        if not isinstance(draft, str) or not draft.strip():
            raise ToolError("'draft' must be the non-empty answer you would like to show")
        receipts = self._receipts(args)
        out = mf.finalize_answer(draft, receipts)
        out["receipts_used"] = sorted(str(r.get("receipt_id", ""))[:12] for r in receipts if isinstance(r, dict))
        out["server"] = {"meps_mcp": __version__, "meps_guard": mg.__version__, "meps_frame": mf.__version__}
        return out

    # -------------------------------------------------------------- JSON-RPC
    def handle(self, msg):
        """One JSON-RPC message in, one response dict out (None for notifications)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                    "error": {"code": -32600, "message": "invalid request"}}
        method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if "id" not in msg:
            return None
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": SERVER_NAME, "version": __version__},
                          "instructions": "Guarded estimates from AHRQ's MEPS-HC public-use files. Call "
                                          "meps_policy first; pass the user's question to meps_estimate; "
                                          "every answer has a receipt_id. Before showing anything, call "
                                          "meps_final_answer with your draft and show exactly the 'answer' "
                                          "it returns (nothing if it is null). meps_verify_answer explains "
                                          "what is wrong with a draft. Refusals and blocks are deliberate: "
                                          "explain them to the user, do not work around them."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments") or {}
                fn = {"meps_policy": self.tool_policy, "meps_find_variables": self.tool_find,
                      "meps_check_request": self.tool_check, "meps_estimate": self.tool_estimate,
                      "meps_verify_answer": self.tool_verify, "meps_final_answer": self.tool_final}.get(name)
                if fn is None:
                    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool %r" % name}}
                try:
                    payload = fn(args)
                    is_error = False
                except ToolError as exc:
                    payload, is_error = {"status": "error", "message": str(exc)}, True
                text = json.dumps(payload, sort_keys=True, default=str)
                result = {"content": [{"type": "text", "text": text}], "structuredContent": json.loads(text),
                          "isError": is_error}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}
        except Exception as exc:  # report, never crash the server
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "%s: %s" % (type(exc).__name__, exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self, fin, fout):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            else:
                resp = self.handle(msg)
            if resp is not None:
                fout.write(json.dumps(resp, separators=(",", ":"), default=str) + "\n")
                fout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--receipts-dir")
    a = ap.parse_args(argv)
    Server(a.data_dir, a.receipts_dir).serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
