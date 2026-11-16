#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_cb_fallback.py — 可转债双低影子 stale兜底: westock单源实弹组装(20260925演练)

⛔ TERMINATED 2026-09-25: 主人令「我们不做可转债」——已终止, 禁止运行。裁决=data/CB_DOUBLELOW_TERMINATED_20260925.json

背景: xiaoshi convertible_bonds 源站持续失败(最新期冻结0918), runbook§2兜底路径=westock实时行情组装。
双源拼接: 宇宙/名称/正股名取xiaoshi最近快照(保ST过滤), 价格/双低/转股价值/转股起始日取westock data_quote
(休市日=最近收盘价, 数据日以data_quote返回time为准)。
产出: data/cb_snapshot/<数据日YYYYMMDD>.jsonl(首行meta标注_source) + data/cb_review/<数据日>_westock.jsonl(双低前60评级复核)
用法: python fetch_cb_fallback.py            # 宇宙=xiaoshi最新快照
"""
import json, os, sys, time, urllib.request
from datetime import date

ROOT = r"D:\zcodesj"
CFG = r"C:/Users/Administrator/.zcode\cli\config.json".replace("\\", "/")
SNAP_DIR = os.path.join(ROOT, "data", "cb_snapshot")
REVIEW_DIR = os.path.join(ROOT, "data", "cb_review")
QUOTE_BATCH = 20
BOND_BATCH = 4
REVIEW_TOP = 60


def load_cfg():
    cfg = json.load(open(CFG, encoding="utf-8"))
    srv = cfg["mcp"]["servers"]["westock-mcp"]
    return srv["url"], srv["headers"]["Authorization"]


URL, AUTH = load_cfg()
H = {"Authorization": AUTH, "Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}


class Ws:
    def __init__(self):
        self._id = 0
        self.sid = None
        self._init()

    def _rpc(self, method, params=None, notify=False):
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._id
        req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), headers=H)
        if self.sid:
            req.add_header("mcp-session-id", self.sid)
        with urllib.request.urlopen(req, timeout=60) as r:
            self.sid = r.headers.get("mcp-session-id") or self.sid
            raw = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
        msg = None
        if "event-stream" in ctype:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    try:
                        m = json.loads(line[5:].strip())
                        if not notify and m.get("id") == self._id:
                            msg = m
                    except Exception:
                        pass
        else:
            try:
                msg = json.loads(raw)
            except Exception:
                msg = None
        return msg

    def _init(self):
        msg = self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "cb-fallback", "version": "1.0"}})
        if not msg or "result" not in msg:
            raise RuntimeError(f"westock initialize失败: {msg}")
        try:
            self._rpc("notifications/initialized", notify=True)
        except Exception:
            pass
        return self.sid

    def call(self, tool, args, retries=5):
        last = None
        for attempt in range(retries):
            try:
                msg = self._rpc("tools/call", {"name": tool, "arguments": args})
                if not msg or "result" not in msg:
                    last = RuntimeError(f"no result: {msg}"); time.sleep(2); continue
                res = msg["result"]
                if res.get("isError"):
                    txt = str(res)
                    last = RuntimeError(f"isError: {txt[:150]}")
                    time.sleep(12 if "限频" in txt else 3)  # westock服务限频专项退避
                    continue
                for c in res.get("content", []):
                    if c.get("type") == "text":
                        return json.loads(c["text"])
                last = RuntimeError("no text content"); time.sleep(2)
            except Exception as e:
                last = e; time.sleep(3)
        raise last


def load_universe():
    """xiaoshi最新快照(含stale)取宇宙: code/name/stock_code/stock_name。"""
    snaps = sorted(f for f in os.listdir(SNAP_DIR) if f.endswith(".jsonl"))
    if not snaps:
        raise RuntimeError("无任何本地快照, 宇宙不可得")
    path = os.path.join(SNAP_DIR, snaps[-1])
    uni = {}
    for l in open(path, encoding="utf-8"):
        r = json.loads(l)
        if "code" not in r:
            continue
        code = str(r.get("code", "")).zfill(6)
        uni[code] = {"name": r.get("name") or "", "stock_code": r.get("stock_code") or "",
                     "stock_name": r.get("stock_name") or "",
                     "conv_start_x": r.get("conversion_start_date") or ""}
    print(f"[UNIVERSE] {snaps[-1]}: {len(uni)}只 (xiaoshi宇宙, stale可接受=代码池慢变)")
    return uni


def prefix(code6):
    return ("sh" if code6[:3] in ("110", "111", "113", "118") else "sz") + code6


def main():
    uni = load_universe()
    codes = [(c, prefix(c)) for c in sorted(uni)]
    ws = Ws()

    # 1) data_quote 批量拉价
    quotes, bad_time = {}, []
    for i in range(0, len(codes), QUOTE_BATCH):
        batch = codes[i:i + QUOTE_BATCH]
        payload = ws.call("data_quote", {"codes": ",".join(b[1] for b in batch)})
        data = payload.get("data") or {}
        for full, q in data.items():
            px = q.get("price")
            if px is None or px <= 0:
                continue
            t = str(q.get("time") or "")
            data_day = t.replace("-", "")[:8]
            if data_day and data_day != "20260924":
                bad_time.append(f"{full}:{t}")
            quotes[full] = q
        print(f"[QUOTE] {i + len(batch)}/{len(codes)} 有价={len(quotes)}")
        time.sleep(3)  # westock限频退避: 批间节流
    if bad_time:
        print(f"[WARN] time≠20260924: {bad_time[:5]}{'...' if len(bad_time) > 5 else ''}")
    data_days = {str(q.get('time') or '').replace('-', '')[:8] for q in quotes.values()}
    data_day = "20260924" if data_days == {"20260924"} or not data_days else sorted(data_days)[-1]
    print(f"[DATA-DAY] {data_day} (westock time口径)")

    # 2) 组装兼容快照(双源拼接; 118科创板转债结构性剔除=屠龙铁律禁科创板,主人9/23令)
    rows = [{"_source": "westock_fallback", "_as_of": time.strftime("%Y-%m-%d"),
             "_data_day": data_day, "_note": "xiaoshi宇宙+westock价格 拼接兜底"}]
    built, drop118 = 0, 0
    for code6, full in codes:
        q = quotes.get(full)
        if not q:
            continue
        if code6.startswith("118"):
            drop118 += 1
            continue
        px = float(q["price"]); dl = q.get("bond_double_low")
        prem = round(float(dl) - px, 2) if dl is not None else None
        u = uni[code6]
        rows.append({"code": code6, "name": u["name"], "bond_price": px,
                     "conversion_premium": prem, "conversion_value": q.get("bond_equity_value"),
                     "conversion_start_date": q.get("bond_convert_start_date") or u["conv_start_x"],
                     "stock_code": u["stock_code"], "stock_name": u["stock_name"],
                     "credit_rating": None, "issue_size": None,
                     "_westock_due": q.get("bond_due_date"), "_westock_ytm": q.get("bond_ytm")})
        built += 1
    out_snap = os.path.join(SNAP_DIR, f"{data_day}.jsonl")
    with open(out_snap, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[SNAP] {out_snap} 数据行={built}(+meta1, 弃科创板118={drop118}只)")

    # 3) 双低初排 + 前60 data_bond 评级复核
    ranked = sorted([r for r in rows[1:] if r["bond_price"] and r["conversion_premium"] is not None],
                    key=lambda r: r["bond_price"] + r["conversion_premium"])[:REVIEW_TOP]
    review, errors = [], []
    for i in range(0, len(ranked), BOND_BATCH):
        batch = ranked[i:i + BOND_BATCH]
        try:
            payload = ws.call("data_bond", {"codes": ",".join(prefix(r["code"]) for r in batch)})
            items = (payload.get("data") or {}).get("data") or []
            got = {it.get("symbol"): it.get("data") for it in items}
        except Exception as e:
            for r in batch:
                errors.append({"cb_code": r["code"], "error": str(e)[:150]})
            continue
        for r in batch:
            d = got.get(prefix(r["code"]))
            if not d:
                errors.append({"cb_code": r["code"], "error": "no_data"})
                continue
            review.append({"cb_code": r["code"], "code_full": d.get("code"),
                           "cb_name": r["name"], "issuer": d.get("issuer"),
                           "rating": d.get("latestCompanyRating"),
                           "rating_issue": d.get("issueCompanyRating"),
                           "size_yuan": d.get("size"), "due_date": d.get("dueDate"),
                           "last_trade_date": d.get("lastTradeDate"),
                           "trade_end_date": d.get("tradeEndDate"),
                           "ytm": d.get("ytm"), "double_low_westock": d.get("doubleLow")})
        print(f"[BOND] {i + len(batch)}/{len(ranked)} 成功={len(review)}")
        time.sleep(3)
    out_rev = os.path.join(REVIEW_DIR, f"{data_day}_westock.jsonl")
    with open(out_rev, "w", encoding="utf-8") as f:
        for r in review + errors:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 4) 摘要
    dist = {}
    for r in review:
        dist[r.get("rating")] = dist.get(r.get("rating"), 0) + 1
    lt1y = [r["cb_code"] for r in review if r.get("due_date") and r["due_date"].replace("-", "")
            <= str(int(data_day[:4]) + 1) + data_day[4:]]
    print(f"[DONE] 快照={built}只 复核={len(review)}/{len(ranked)}失败{len(errors)} 评级分布={json.dumps(dist, ensure_ascii=False)}")
    print(f"[DONE] 复核内剩余<1年={len(lt1y)}只 输出={out_snap} + {out_rev}")
    top10 = ranked[:10]
    for r in top10:
        print(f"  {r['code']} {r['name']:<6} 双低{r['bond_price'] + r['conversion_premium']:.1f} 价{r['bond_price']:.2f}")


if __name__ == "__main__":
    sys.exit(main())
