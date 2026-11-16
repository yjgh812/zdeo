# -*- coding: utf-8 -*-
"""shadow_track_20261109.py — 判定层否决影子仓(2026-11-09建, 主人裁决A体系配套)
背景: 11/09开盘判定层否决9只(8只缠论三卖+1只R22数据打架), 当场实况=被否决组均值+1.06%
(5涨3跌)疑似错杀 → 影子仓纸面跟踪5个交易日, 与已买12只对照, 用实弹数据裁决
「判定层否决挡亏损还是挡利润」。
用法: python shadow_track_20261109.py --init   (一次性: 抓今日开盘价建影子仓)
      python shadow_track_20261109.py --update (每日收盘后: 记录当日收盘与影子收益)
产出: data/shadow_track_20261109.json (滚动更新)
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
SHADOW = os.path.join(ROOT, "data", "shadow_track_20261109.json")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"

# 11/09被判定层/闸门拒的9只(种子: 当场实况+1.06%)
SEED = [
    ("300931", "通用电梯", "缠论三卖"), ("600410", "华胜天成", "缠论三卖"),
    ("000595", "新能股份", "缠论三卖"), ("001258", "立新能源", "缠论三卖"),
    ("600156", "华升股份", "缠论三卖"), ("600207", "安彩高科", "缠论三卖"),
    ("300286", "安科瑞", "缠论三卖"), ("300360", "炬华科技", "缠论三卖"),
    ("600959", "江苏有线", "R22双口径"),
]
T0 = 20261109  # 基准日(否决日开盘价=影子买入基准)


def westock_open_prices():
    """实时桥抓今日开盘价(基准)"""
    import urllib.request as ur
    cfgp = r"C:\Users\Administrator\.zcode\cli\config.json"
    ws = json.load(open(cfgp, encoding="utf-8")).get("mcp", {}).get("servers", {}).get("westock-mcp", {})
    tok = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    codes = ",".join(("sz%s" if c[0] in "03" else "sh%s") % c for c, _n, _r in SEED)

    def rpc(payload):
        body = json.dumps(payload).encode()
        h = {"Authorization": "Bearer " + tok, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = ur.Request(url, data=body, headers=h, method="POST")
        with ur.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8")
    rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "shadow", "version": "1.0"}}})
    rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "data_quote", "arguments": {"codes": codes}}})
    opens = {}
    for line in raw.splitlines():
        if line.startswith("data:") and line.strip() != "data:":
            d = json.loads(line[5:].strip())
            for c in d.get("result", {}).get("content", []):
                if c.get("type") == "text":
                    obj = json.loads(c["text"])
                    rows = (obj.get("data", {}) or {})
                    rows = rows.get("data", rows) if isinstance(rows, dict) else rows
                    if isinstance(rows, dict):
                        for k, v in rows.items():
                            try:
                                opens[str(k)[-6:]] = (float(v.get("open", 0) or 0),
                                                       float(v.get("price", 0) or 0))
                            except Exception:
                                pass
    return opens


def init():
    opens = westock_open_prices()
    items = []
    for c6, nm, why in SEED:
        op, _px = opens.get(c6, (0, 0))
        items.append({"code": c6, "name": nm, "reason": why, "base_open": round(op, 2),
                      "closes": {}})
    json.dump({"T0": T0, "items": items}, open(SHADOW, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("影子仓初始化: %d只" % len(items))
    for it in items:
        print("  %s %s 基准开盘%.2f (%s)" % (it["code"], it["name"], it["base_open"], it["reason"]))


def update():
    d = json.load(open(SHADOW, encoding="utf-8"))
    con = sqlite3.connect(QFQ, uri=True)
    codes = [it["code"] for it in d["items"]]
    q = ",".join("?" * len(codes))
    rows = con.execute(
        "select code,date,close from stock_daily_meta_qfq where date>=? "
        "and substr(code,-6) in (%s) order by code,date" % q,
        [str(d["T0"])] + codes).fetchall()
    con.close()
    g = defaultdict(dict)
    for code, dd, c in rows:
        g[str(code)[-6:]][int(dd)] = float(c or 0)
    for it in d["items"]:
        ser = g.get(it["code"], {})
        for dd, c in ser.items():
            if c > 0:
                it["closes"][str(dd)] = round(c, 2)
    last = max(ser) if ser else 0
    # 对照摘要
    print("==== 影子仓更新(基准日%d) ====" % d["T0"])
    for it in d["items"]:
        base = it["base_open"]
        lastc = it["closes"].get(str(last), 0)
        if base > 0 and lastc > 0:
            print("  %s %s 基准%.2f 最新%.2f (%s) %+.2f%%" % (
                it["code"], it["name"], base, lastc,
                ("D%d" % (len(it["closes"]))), (lastc / base - 1) * 100))
    json.dump(d, open(SHADOW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if "--init" in sys.argv:
        init()
    elif "--update" in sys.argv:
        update()
    else:
        print("usage: shadow_track_20261109.py --init | --update")
