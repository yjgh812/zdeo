# -*- coding: utf-8 -*-
"""趋势乙: 温斯坦阶段4卖出侧验证 — 2026-10-07 研究侧零接线
事件=收盘跌破150日线 且 150日线处于下行(MA150<MA150[5日前]) = 温斯坦阶段4进入信号(卖出侧)。
卖出侧评估: 事件后个股/指数 fwd5/10/20/60 —— fwd中位显著为负=卖对(继续跌);为正=卖飞。
对照臂: 跌破但150日线仍上行(假跌破组)。
判据跑前冻结: 个股事件n>=100; 卖对=fwd20中位<0且win<45; 卖飞=fwd20中位>0; 中间=无增量。
数据: qfq库抽样800只(seed 20261007,屠龙宇宙,<=30元,剔次新) + vipdoc sh000001。
先例参照: 屠龙日线敏感卖出线五例证伪(破MA5/MA20/MACD死叉/反向K线按线卖=卖飞); 150日线大级别未测。
"""
import json
import random
import sqlite3
import struct
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
OUT = Path(r"D:\zcodesj\tmp\trend_b_sellside_result_20261007.json")

PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
SAMPLE_N, SEED, START, MAX_PRICE = 800, 20261007, "20100101", 30.0
FWD = [5, 10, 20, 60]
COOLDOWN = 20  # 同股两次跌破事件间隔


def med_win(vals):
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(vals), "med": round(s[len(s) // 2], 2),
            "win": round(sum(1 for v in vals if v > 0) * 100.0 / len(vals), 1)}


def part_stock():
    con = sqlite3.connect(QFQ_DB, uri=True, timeout=60)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE substr(code,1,3) IN (%s)"
        % ",".join("'%s'" % p for p in PREFIX))]
    random.seed(SEED)
    sample = random.sample(codes, min(SAMPLE_N, len(codes)))
    ev_down, ev_fake = [], []
    for c in sample:
        rows = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND close IS NOT NULL ORDER BY date",
            (c, "20080101")).fetchall()
        if len(rows) < 220:
            continue
        closes = [r[1] for r in rows]
        dates = [r[0] for r in rows]
        ma, s = [], 0.0
        for i2, v in enumerate(closes):
            s += v
            if i2 >= 150:
                s -= closes[i2 - 150]
                ma.append(s / 150.0)
            else:
                ma.append(None)
        last_i = -999
        for i2 in range(155, len(closes) - 60):
            if closes[i2] > MAX_PRICE:
                continue
            m_now, m_5 = ma[i2], ma[i2 - 5]
            if m_now is None or m_5 is None:
                continue
            crossed = closes[i2] < m_now and closes[i2 - 1] >= ma[i2 - 1]
            if not crossed or i2 - last_i <= COOLDOWN:
                continue
            fw = {f"fwd{h}": (closes[i2 + h] / closes[i2] - 1) * 100.0 for h in FWD}
            rec = {"code": c, "date": dates[i2], **fw}
            if m_now < m_5:
                ev_down.append(rec)
            else:
                ev_fake.append(rec)
            last_i = i2
    con.close()

    out = {"sample": len(sample), "seed": SEED}
    for name, evs in (("down_ma150_break", ev_down), ("fake_break_upslope", ev_fake)):
        d = {}
        for h in FWD:
            d[f"fwd{h}"] = med_win([e[f"fwd{h}"] for e in evs])
        # 分年(fwd20)
        by = {}
        for e in evs:
            by.setdefault(e["date"][:4], []).append(e["fwd20"])
        d["yearly_fwd20"] = {y: med_win(v) for y, v in sorted(by.items())}
        out[name] = d
        print(f"[乙-股] {name}: n={d['fwd20']['n']} fwd20 med={d['fwd20']['med']} "
              f"win={d['fwd20']['win']} fwd60 med={d['fwd60']['med']}")
    return out


def part_index():
    rows = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append((str(d), c / 100.0))
    closes = [r[1] for r in rows]
    dates = [r[0] for r in rows]
    n = len(closes)
    ma, s = [], 0.0
    for i2, v in enumerate(closes):
        s += v
        if i2 >= 150:
            s -= closes[i2 - 150]
            ma.append(s / 150.0)
        else:
            ma.append(None)
    ev_down, ev_fake = [], []
    last_i = -999
    for i2 in range(155, n - 60):
        m_now, m_5 = ma[i2], ma[i2 - 5]
        if m_now is None or m_5 is None:
            continue
        crossed = closes[i2] < m_now and closes[i2 - 1] >= ma[i2 - 1]
        if not crossed or i2 - last_i <= COOLDOWN:
            continue
        fw = {f"fwd{h}": (closes[i2 + h] / closes[i2] - 1) * 100.0 for h in FWD}
        (ev_down if m_now < m_5 else ev_fake).append({"date": dates[i2], **fw})
        last_i = i2
    out = {}
    for name, evs in (("down_ma150_break", ev_down), ("fake_break_upslope", ev_fake)):
        d = {f"fwd{h}": med_win([e[f"fwd{h}"] for e in evs]) for h in FWD}
        d["recent"] = [e for e in evs if e["date"] >= "20150101"]
        d["recent_fwd20"] = med_win([e["fwd20"] for e in d["recent"]])
        out[name] = d
        print(f"[乙-指] {name}: n={d['fwd20']['n']} fwd20 med={d['fwd20']['med']} "
              f"win={d['fwd20']['win']} | 2015后 n={d['recent_fwd20']['n']} med={d['recent_fwd20']['med']}")
    out["down_events_dates"] = [e["date"] for e in ev_down]
    return out


def main():
    t0 = time.time()
    st = part_stock()
    idx = part_index()
    # 判据(冻结)
    g = {}
    s20 = st["down_ma150_break"]["fwd20"]
    g["stock_sellside"] = ("SELL_VALID" if (s20["n"] >= 100 and s20["med"] < 0 and s20["win"] < 45)
                           else ("SELL_FLY" if s20["med"] > 0 else "NO_EDGE"))
    i20 = idx["down_ma150_break"]["fwd20"]
    g["index_sellside"] = ("SELL_VALID" if (i20["med"] < 0 and i20["win"] < 45)
                           else ("SELL_FLY" if i20["med"] > 0 else "NO_EDGE"))
    result = {"meta": {"script": "analysis/trend_b_sellside_20261007.py",
                       "run_at": time.strftime("%Y-%m-%d %H:%M:%S")},
              "gates": g, "stock": st, "index": idx}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[GATES] {g}")
    print(f"saved -> {OUT} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
