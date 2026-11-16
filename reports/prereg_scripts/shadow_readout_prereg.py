# -*- coding: utf-8 -*-
"""shadow_readout_prereg.py — PREREG 影子读出脚本（判据冻结版，2026-11-09）。

对应 PREREG: reports/BUYPOINT_FILTER_PREREG_PROPOSAL_20261109.md (H2, 判据G5~G7冻结)
规则: 自2026-11-10起新开回合, 按买前20日趋势分桶, 满足读出条件后一次性判G5~G7。
纪律: 本脚本冻结后不再修改; 早跑(读出条件未满足)时结构化退出, 不输出伪结论。
运行: python -I shadow_readout_prereg.py
"""
import hashlib
import ipaddress
import json
import os
import socket
import sys
import urllib.request
from collections import defaultdict

sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day

WS = os.path.dirname(os.path.abspath(__file__))
MCP_HOST = "stockbuddy.qq.com"
MCP_URL = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("redirect blocked: " + str(newurl))

_OPENER = urllib.request.build_opener(_NoRedirect)

def _guard_url():
    """SSRF 边界: 协议/主机白名单 + 解析IP阻断私网(2026-11-09 加固, 语义无变化)。"""
    from urllib.parse import urlparse
    p = urlparse(MCP_URL)
    if p.scheme != "https" or (p.hostname or "") != MCP_HOST:
        raise RuntimeError("MCP URL 越界: " + MCP_URL)
    for fam, _t, _p, _cn, sa in socket.getaddrinfo(MCP_HOST, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(sa[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise RuntimeError("MCP 主机解析到受限地址: " + str(ip))

BASELINE_FILE = os.path.join(WS, "fills_202607_202611.json")
PREREG = r"D:\zcodesj\reports\BUYPOINT_FILTER_PREREG_PROPOSAL_20261109.md"
SHADOW_START = "20261110"
MIN_TDAYS = 20
MIN_ROUNDS = 30

def _token():
    cfgp = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Administrator"),
                        ".zcode", "cli", "config.json")
    cfg = json.load(open(cfgp, encoding="utf-8"))
    ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
    return (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")

def rpc(name, arguments):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}).encode()
    h = {"Authorization": "Bearer " + _token(), "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(MCP_URL, data=body, headers=h, method="POST")
    with _OPENER.open(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    for line in raw.splitlines():
        if line.startswith("data:") and line.strip() != "data:":
            d = json.loads(line[5:].strip())
            if "result" in d:
                for c in d["result"].get("content", []):
                    if c.get("type") == "text":
                        return json.loads(c["text"])
    raise RuntimeError("no content")

def fetch_new_fills():
    out = []
    for month in ("202611", "202612", "202701"):
        offset = 0
        while True:
            d = rpc("portfolio_paper_history",
                    {"range": "history", "month": month, "offset": offset, "limit": 100})
            data = d.get("data", {})
            recs = data.get("records", [])
            for r in recs:
                if (r.get("status") == "已成交"
                        and (r.get("filledAt", "")[:10].replace("-", "") >= SHADOW_START)):
                    out.append(r)
            if not data.get("hasMore"):
                break
            offset += 100
    out.sort(key=lambda x: x.get("filledAt", ""))
    return out

def market_of(code):
    if code[0] in "695":
        return "sh"
    return "sz"

def main():
    _guard_url()
    print("sha256:", hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest())
    fills = fetch_new_fills()
    print("影子期新成交(已成交, >=2026-11-10):", len(fills), "笔")
    if not fills:
        print("影子期尚无成交, 不判读。")
        return 0
    rows = read_day("sh", "000001")
    tdays = sum(1 for r in rows if str(r[0]) > "20261109")
    # 回合重建(与trip_pattern_check.py同源逻辑)
    by_code = defaultdict(list)
    for f in fills:
        by_code[str(f.get("code", ""))[-6:]].append(f)
    trips = []
    for code, fl in by_code.items():
        shares = 0.0
        trip = None
        for f in fl:
            d = f.get("filledAt", "")[:10]
            q = float(f.get("quantity") or 0)
            amt = float(f.get("amount") or 0)
            if f.get("direction") == "buy":
                if shares <= 0:
                    trip = {"code": code, "entry": d, "shares": 0.0, "cost": 0.0,
                            "realized": 0.0, "sold_cost": 0.0, "closed": False}
                    trips.append(trip)
                shares += q
                trip["shares"] = shares
                trip["cost"] += amt
            else:
                if shares <= 0 or trip is None:
                    continue
                avg = trip["cost"] / trip["shares"]
                trip["realized"] += amt - avg * q
                trip["sold_cost"] += avg * q
                trip["cost"] -= avg * q
                shares -= q
                trip["shares"] = shares
                if shares <= 0:
                    trip["closed"] = True
    positions = rpc("portfolio_paper_positions")
    cur_price = {}
    try:
        for c in positions.get("data", {}).get("positions", []):
            cur_price[str(c.get("code", ""))[-6:]] = float(c.get("currentPrice") or 0)
    except Exception:
        pass
    rows_ok = []
    skipped = 0
    for t in trips:
        if t.get("closed"):
            ret = t["realized"] / t["sold_cost"] * 100 if t["sold_cost"] > 0 else 0.0
        else:
            px = cur_price.get(t["code"])
            if not px or t["cost"] <= 0:
                skipped += 1
                continue
            ret = (px * t["shares"] - t["cost"]) / t["cost"] * 100
        mrows = read_day(market_of(t["code"]), t["code"])
        if not mrows:
            skipped += 1
            continue
        dates = [str(r[0]) for r in mrows]
        closes = [r[4] for r in mrows]
        bd = t["entry"].replace("-", "")
        idx = None
        for i, d in enumerate(dates):
            if d <= bd:
                idx = i
            else:
                break
        if idx is None or idx < 20:
            skipped += 1
            continue
        seg = closes[max(0, idx - 250): idx + 1]
        lo = min(seg)
        hi = max(seg)
        if hi <= lo:
            skipped += 1
            continue
        pre20 = (closes[idx] / closes[idx - 20] - 1) * 100
        rows_ok.append((ret, pre20))
    print(f"影子回合: {len(rows_ok)} 有效 / 跳过 {skipped} | 交易日已过 {tdays}/{MIN_TDAYS}")
    if tdays < MIN_TDAYS and len(rows_ok) < MIN_ROUNDS:
        print(f"读出条件未满足(需交易日>={MIN_TDAYS}或回合>={MIN_ROUNDS}), 不判读。")
        return 0
    dip = [r[0] for r in rows_ok if -10 < r[1] <= 0]
    keep = [r[0] for r in rows_ok if not (-10 < r[1] <= 0)]
    if len(dip) < 5 or len(keep) < 5:
        print(f"分组样本不足(阴跌{len(dip)}/留存{len(keep)}, 各需>=5), 顺延读出。")
        return 0
    g5 = (sum(keep) / len(keep)) - (sum(dip) / len(dip))
    g6 = sum(1 for x in keep if x > 0) / len(keep) * 100
    g7 = sum(keep) / len(keep)
    print(f"留存组(非阴跌) n={len(keep)} 平均{g7:+.2f}% 胜率{g6:.0f}%")
    print(f"阴跌组 n={len(dip)} 平均{sum(dip)/len(dip):+.2f}%")
    v5 = g5 >= 1.0
    v6 = g6 > 40.0
    v7 = g7 > 0.0
    print(f"G5 差距{g5:+.2f}pp (需>=+1.0): {'过' if v5 else '不过'}")
    print(f"G6 留存胜率{g6:.0f}% (需>40): {'过' if v6 else '不过'}")
    print(f"G7 留存平均{g7:+.2f}% (需>0): {'过' if v7 else '不过'}")
    verdict = "PASS" if (v5 and v6 and v7) else "FAIL"
    print(f"== H2 影子读出判定: {verdict} ==")
    print("下一步: 结果写入 reports/BUYPOINT_FILTER_PREREG_RESULT_20261208.md 并 m7 校验。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
