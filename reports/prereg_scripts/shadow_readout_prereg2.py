# -*- coding: utf-8 -*-
"""shadow_readout_prereg2.py — PREREG#2 影子读出脚本（判据冻结版，2026-11-10）。

对应 PREREG: reports/BUYGENE_RULE_PREREG_20261110.md (三条件正向规则, n_trials=39)
读出条件: 中期检查=满20交易日(仅描述); 终局=n合格>=12 或 满120交易日(先到)。
判据G1-G4: 仅在 n>=12 时裁决; n<12 且满120日 → 信号密度不足归档。
纪律: 冻结后不再修改; 早跑结构化退出不输出伪结论。运行: python -I shadow_readout_prereg2.py
"""
import hashlib
import ipaddress
import json
import math
import os
import socket
import sys
import urllib.request
from collections import defaultdict
from urllib.parse import urlparse

sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day

WS = os.path.dirname(os.path.abspath(__file__))
MCP_HOST = "stockbuddy.qq.com"
MCP_URL = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
SHADOW_START = "20261110"
N_TRIALS = 39
MIN_QUAL = 12
MAX_TDAYS = 120
MID_TDAYS = 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("redirect blocked: " + str(newurl))


_OPENER = urllib.request.build_opener(_NoRedirect)


def _guard_url():
    p = urlparse(MCP_URL)
    if p.scheme != "https" or (p.hostname or "") != MCP_HOST:
        raise RuntimeError("MCP URL 越界: " + MCP_URL)
    for _fam, _t, _p, _cn, sa in socket.getaddrinfo(MCP_HOST, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(sa[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise RuntimeError("MCP 主机解析到受限地址: " + str(ip))


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


def fetch_shadow_fills():
    out = []
    for month in ("202611", "202612", "202701", "202702", "202703", "202704"):
        offset = 0
        while True:
            d = rpc("portfolio_paper_history",
                    {"range": "history", "month": month, "offset": offset, "limit": 100})
            data = d.get("data", {})
            for r in data.get("records", []):
                if (r.get("status") == "已成交"
                        and r.get("filledAt", "")[:10].replace("-", "") >= SHADOW_START):
                    out.append(r)
            if not data.get("hasMore"):
                break
            offset += 100
    out.sort(key=lambda x: x.get("filledAt", ""))
    return out


def market_of(code):
    return "sh" if code[0] in "695" else "sz"


cache = {}
def daily(code):
    if code not in cache:
        rows = read_day(market_of(code), code)
        cache[code] = ([str(r[0]) for r in rows], [r[4] for r in rows],
                       [r[6] for r in rows]) if rows else None
    return cache[code]


def feats_at(code, bd):
    dd = daily(code)
    if dd is None:
        return None
    dates, closes, vols = dd
    bd = bd.replace("-", "")
    idx = None
    for i, d in enumerate(dates):
        if d <= bd:
            idx = i
        else:
            break
    if idx is None or idx < 60:
        return None
    v20 = vols[idx - 19: idx + 1]
    v250 = vols[max(0, idx - 249): idx + 1]
    volratio = (sum(v20) / len(v20)) / (sum(v250) / len(v250)) if sum(v250) > 0 else 1.0
    thr = 19.0 if code.startswith(("300", "301")) else 9.5
    li60 = sum(1 for j in range(max(1, idx - 59), idx + 1)
               if closes[j - 1] > 0 and (closes[j] / closes[j - 1] - 1) * 100 >= thr)
    pre20 = (closes[idx] / closes[idx - 20] - 1) * 100
    return pre20, li60, volratio


def psr(rets):
    n = len(rets)
    m = sum(rets) / n
    sd = (sum((x - m) ** 2 for x in rets) / max(1, n - 1)) ** 0.5
    if sd == 0:
        return 0.0
    sr = m / sd
    g1 = sum((x - m) ** 3 for x in rets) / n / sd ** 3
    g2 = sum((x - m) ** 4 for x in rets) / n / sd ** 4
    denom = math.sqrt(max(1e-12, 1 - g1 * sr + (g2 - 1) / 4 * sr * sr))
    return 0.5 * (1 + math.erf((sr * math.sqrt(n - 1) / denom) / math.sqrt(2)))


def main():
    _guard_url()
    print("sha256:", hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest())
    print("PREREG: reports/BUYGENE_RULE_PREREG_20261110.md | n_trials=39 | G1-G4 冻结")
    fills = fetch_shadow_fills()
    print("影子期新成交(已成交, >=2026-11-10):", len(fills), "笔")
    if not fills:
        print("影子期尚无成交, 不判读。")
        return 0
    sh = read_day("sh", "000001")
    tdays = sum(1 for r in sh if str(r[0]) >= SHADOW_START)

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

    pos = rpc("portfolio_paper_positions")
    cur_price = {}
    try:
        for c in pos.get("data", {}).get("positions", []):
            cur_price[str(c.get("code", ""))[-6:]] = float(c.get("currentPrice") or 0)
    except Exception:
        pass

    rows = []
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
        ft = feats_at(t["code"], t["entry"])
        if ft is None:
            skipped += 1
            continue
        pre20, li60, volratio = ft
        rows.append({"ret": ret, "entry": t["entry"], "pre20": pre20,
                     "li60": li60, "volratio": volratio,
                     "qual3": pre20 <= -10 and li60 >= 1 and volratio >= 2.0,
                     "qual2": pre20 <= -10 and li60 >= 1})

    q3 = [r for r in rows if r["qual3"]]
    q2 = [r for r in rows if r["qual2"]]
    print(f"\n影子回合: {len(rows)} 有效(跳过{skipped}) | 交易日已过 {tdays}/{MAX_TDAYS}")
    print(f"三条件合格: {len(q3)} 回合 | 双条件: {len(q2)} 回合")

    if tdays < MID_TDAYS and len(rows) < 1:
        print("未满中期检查窗口, 描述略。")
        return 0
    if tdays >= MID_TDAYS:
        print("== 中期检查(仅描述, 不裁决) ==")
        if q3:
            print(f"三条件组均值 {sum(r['ret'] for r in q3)/len(q3):+.2f}% (n={len(q3)})")
        if q2:
            print(f"双条件组均值 {sum(r['ret'] for r in q2)/len(q2):+.2f}% (n={len(q2)})")
        oth = [r for r in rows if not r["qual2"]]
        if oth:
            print(f"其余组均值 {sum(r['ret'] for r in oth)/len(oth):+.2f}% (n={len(oth)})")
        bym = defaultdict(list)
        for r in rows:
            bym[r["entry"][:7]].append((r["qual2"], r["ret"]))
        for m in sorted(bym):
            rq = [x[1] for x in bym[m] if x[0]]
            ot = [x[1] for x in bym[m] if not x[0]]
            if len(rq) >= 3 and len(ot) >= 3:
                sp = sum(rq) / len(rq) - sum(ot) / len(ot)
                print(f"  {m}: 规则差 {sp:+.2f}pp (G4监控: {'正常' if sp >= 0 else '跌破0—映射漂移告警'})")

    final_due = tdays >= MAX_TDAYS
    if not final_due and len(q3) < MIN_QUAL:
        print(f"\n终局条件未满足(需 n3>={MIN_QUAL} 或 交易日>={MAX_TDAYS}), 不裁决。")
        return 0
    if len(q3) < MIN_QUAL:
        print(f"\n== 终局: 信号密度不足(120交易日 n3={len(q3)}<{MIN_QUAL}) → 不可验证, 归档 ==")
        return 0

    print("\n== 终局裁决 (n3=%d) ==" % len(q3))
    r3 = [r["ret"] for r in q3]
    roth = [r["ret"] for r in rows if not r["qual3"]]
    m3 = sum(r3) / len(r3)
    mo = sum(roth) / len(roth) if roth else 0.0
    spread = m3 - mo
    sr = m3 / (sum((x - m3) ** 2 for x in r3) / max(1, len(r3) - 1)) ** 0.5
    sr_null = math.sqrt(2 * math.log(N_TRIALS) / len(r3))
    p = psr(r3)
    bym = defaultdict(list)
    for r in rows:
        bym[r["entry"][:7]].append((r["qual3"], r["ret"]))
    months = []
    for m in sorted(bym):
        rq = [x[1] for x in bym[m] if x[0]]
        ot = [x[1] for x in bym[m] if not x[0]]
        if len(rq) >= 1 and len(ot) >= 3:
            months.append(sum(rq) / len(rq) - sum(ot) / len(ot))
    g1 = spread >= 3.0
    g2 = p >= 0.95
    g3 = sr >= sr_null
    g4 = all(x >= 0 for x in months) if months else False
    print(f"G1 差{spread:+.2f}pp(需>=+3): {'过' if g1 else 'fail'}")
    print(f"G2 PSR={p:.3f}(需>=0.95): {'过' if g2 else 'fail'}")
    print(f"G3 SR={sr:.3f} vs 噪声基线{sr_null:.3f}: {'过' if g3 else 'fail'}")
    print(f"G4 月度差全>=0: {'过' if g4 else 'fail'} ({['%+.1f' % x for x in months]})")
    verdict = "PASS" if (g1 and g2 and g3 and g4) else "FAIL"
    print(f"== PREREG#2 终局判定: {verdict} ==")
    print("下一步: 结果写入 reports/BUYGENE_RULE_PREREG_RESULT.md + m7 校验; PASS仍须主人批准后才可谈接线。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
