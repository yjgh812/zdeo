# -*- coding: utf-8 -*-
"""One-off: fetch 30 CB bond details from westock MCP HTTP endpoint (same source
as mcp__westock-mcp__data_bond), extract core fields, write 20260918_westock.jsonl.
Read-only query. Batch = 4 codes per tools/call (matches task instruction).
"""
import json, sys, time, urllib.request, urllib.error
from datetime import date

CFG = r"C:/Users/Administrator/.zcode/cli/config.json"
CAND = r"D:/zcodesj/data/cb_review/20260918_candidates.json"
OUT = r"D:/zcodesj/data/cb_review/20260918_westock.jsonl"
ASOF = date(2026, 9, 18)

def load_cfg():
    cfg = json.load(open(CFG, encoding="utf-8"))
    srv = cfg["mcp"]["servers"]["westock-mcp"]
    return srv["url"], srv["headers"]["Authorization"]

URL, AUTH = load_cfg()
H = {"Authorization": AUTH, "Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}

_id = [0]
def rpc(method, params=None, session=None, notify=False):
    _id[0] += 1
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        body["id"] = _id[0]
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), headers=H)
    if session:
        req.add_header("mcp-session-id", session)
    with urllib.request.urlopen(req, timeout=60) as r:
        sid = r.headers.get("mcp-session-id")
        raw = r.read().decode("utf-8", "replace")
        ctype = r.headers.get("Content-Type", "")
    msg = None
    if "event-stream" in ctype:
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    m = json.loads(line[5:].strip())
                    if not notify and m.get("id") == _id[0]:
                        msg = m
                except Exception:
                    pass
    else:
        try:
            msg = json.loads(raw)
        except Exception:
            msg = None
    return msg, sid

def init_session():
    msg, sid = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                  "clientInfo": {"name": "cb-review-fetch", "version": "1.0"}})
    if not msg or "result" not in msg:
        raise RuntimeError("initialize failed: %r" % (msg,))
    try:
        rpc("notifications/initialized", session=sid, notify=True)
    except Exception:
        pass
    return sid

def call_bond(codes, session):
    last = None
    for attempt in range(3):
        try:
            msg, _ = rpc("tools/call", {"name": "data_bond",
                                        "arguments": {"codes": ",".join(codes)}}, session=session)
            if not msg or "result" not in msg:
                last = RuntimeError("no result: %r" % (msg,))
                time.sleep(2); continue
            res = msg["result"]
            if res.get("isError"):
                last = RuntimeError("isError: %r" % res)
                time.sleep(2); continue
            payload = None
            for c in res.get("content", []):
                if c.get("type") == "text":
                    payload = json.loads(c["text"]); break
            if payload is None:
                last = RuntimeError("no text content")
                time.sleep(2); continue
            return payload.get("data", {})
        except Exception as e:
            last = e; time.sleep(3)
    raise last

def prefix(cb_code):
    p3 = cb_code[:3]
    if p3 in ("110", "111", "113", "118"):
        return "sh" + cb_code
    if p3 in ("127", "128", "123"):
        return "sz" + cb_code
    return ("sh" if cb_code[:2] == "11" else "sz") + cb_code

def pairs_from_candidates():
    cand = json.load(open(CAND, encoding="utf-8"))["top_candidates"]
    return [(c["cb_code"], prefix(c["cb_code"]), c["cb_name"], c.get("double_low")) for c in cand]

def pairs_from_snapshot(codes):
    """Batch2: name/double_low(bond_price+conversion_premium) from cb_snapshot as_of date."""
    snap = r"D:/zcodesj/data/cb_snapshot/20260918.jsonl"
    by = {}
    for l in open(snap, encoding="utf-8"):
        r = json.loads(l)
        by[str(r.get("code", "")).zfill(6)] = r
    pairs = []
    for c in codes:
        r = by.get(c)
        if not r:
            continue
        dl = None
        if r.get("bond_price") is not None and r.get("conversion_premium") is not None:
            dl = r["bond_price"] + r["conversion_premium"]
        pairs.append((c, prefix(c), r.get("name"), dl))
    return pairs

def fetch_and_write(pairs, mode):
    session = init_session()
    rows, errors = [], []
    for i in range(0, len(pairs), 4):
        batch = pairs[i:i+4]
        codes = [b[1] for b in batch]
        try:
            data = call_bond(codes, session)
        except Exception as e:
            for b in batch:
                errors.append({"cb_code": b[0], "error": "call_failed:%s" % e})
            continue
        items = data.get("data") or []
        got = {}
        for it in items:
            got[it.get("symbol")] = it.get("data")
        for b in batch:
            cb, full, name, dl = b
            d = got.get(full)
            if not d:
                msg = data.get("errors") or "no_data"
                errors.append({"cb_code": cb, "error": str(msg)[:200]})
                continue
            rows.append({
                "cb_code": cb, "code_full": d.get("code", full), "cb_name": name,
                "rating": d.get("latestCompanyRating"), "rating_issue": d.get("issueCompanyRating"),
                "size_yuan": d.get("size"), "due_date": d.get("dueDate"),
                "last_trade_date": d.get("lastTradeDate"), "trade_end_date": d.get("tradeEndDate"),
                "ytm": d.get("ytm"), "double_low_westock": d.get("doubleLow"),
            })
    with open(OUT, mode, encoding="utf-8") as f:
        for r in rows + errors:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows, errors

def main():
    if "--append" in sys.argv:
        arg = [a for a in sys.argv if a.startswith("--codes=")]
        codes = [c.strip().zfill(6) for c in arg[0].split("=", 1)[1].split(",") if c.strip()]
        pairs = pairs_from_snapshot(codes)
        mode = "a"
        print("batch2 codes=%d resolved=%d" % (len(codes), len(pairs)))
    else:
        pairs = pairs_from_candidates()
        mode = "w"
    rows, errors = fetch_and_write(pairs, mode)

    # summary (this batch only)
    ok = [r for r in rows]
    print("success=%d fail=%d total=%d" % (len(ok), len(errors), len(pairs)))
    dist = {}
    for r in ok:
        dist[r["rating"]] = dist.get(r["rating"], 0) + 1
    print("rating_dist:", json.dumps(dist, ensure_ascii=False, sort_keys=True))
    lt1y, delisted = [], []
    for r in ok:
        try:
            y, m, dd = map(int, r["due_date"].split("-"))
            due = date(y, m, dd)
            if (due - ASOF).days < 365:
                lt1y.append("%s %s due=%s" % (r["cb_code"], r["cb_name"], r["due_date"]))
        except Exception:
            pass
        if r["last_trade_date"]:
            delisted.append("%s %s lastTrade=%s" % (r["cb_code"], r["cb_name"], r["last_trade_date"]))
    print("remain<1y(asof 2026-09-18):", "; ".join(lt1y) or "none")
    print("delisted_or_past_last_trade:", "; ".join(delisted) or "none")
    cdl = {b[0]: b[3] for b in pairs}
    devs = []
    for r in ok:
        if r["double_low_westock"] is not None and cdl.get(r["cb_code"]) is not None:
            dev = abs(r["double_low_westock"] - cdl[r["cb_code"]])
            if dev > 1:
                devs.append("%s %s westock=%.3f cand=%.3f dev=%.3f" % (
                    r["cb_code"], r["cb_name"], r["double_low_westock"], cdl[r["cb_code"]], dev))
    print("double_low_dev>1:", "; ".join(devs) or "none")
    if errors:
        print("errors:", json.dumps(errors, ensure_ascii=False))

if __name__ == "__main__":
    main()
