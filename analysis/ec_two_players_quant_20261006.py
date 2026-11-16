# -*- coding: utf-8 -*-
"""素衣不染尘+大师兄擒妖 买卖×K线双口径验证(与二池三方对照)
数据: data/ec_two_players_trades_20261006.json (sy585 262行 / dsx615 83行, fullCode结构化)
口径: 与 ec_event_quant/二池完全同式 — pos60桶 + 10日高回撤深度U型 + 卖后f5。
诚实注记: 素衣2023=AI主线年, 大师兄2023-03=AI主线月, fwd绝对值受市况影响;
核心读数=pos60分布与回撤深度U型(跨选手结构性指标)。
"""
import json
import re
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SRC = r"D:\zcodesj\data\ec_two_players_trades_20261006.json"
ACTIONS = {"买入", "卖出", "减仓"}


def collect(d, key):
    events = []
    for r in d[key]:
        act = r["act"] if r["act"] in ACTIONS else (r["stock"] if r["stock"] in ACTIONS else None)
        if act not in ("买入", "卖出", "减仓"):
            continue
        m = re.search(r"fullCode=(?:sh|sz)(\d{6})", r.get("u") or "")
        dm = re.search(r"dateNum=(\d{8})", r.get("u") or "")
        if m and dm:
            events.append((int(dm.group(1)), m.group(1), act))
    seen = set()
    uniq = []
    for e in events:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


def metrics(conn, code, d0):
    hist = conn.execute(
        "SELECT date,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date", (code,)).fetchall()
    dates = [int(x[0]) for x in hist]
    if d0 not in dates:
        return None
    i = dates.index(d0)
    out = {"chg": hist[i][3]}
    if i >= 59:
        w = [x[1] for x in hist[i - 59:i]]
        lo, hi = min(w), max(w)
        if hi > lo:
            out["pos60"] = round((hist[i][1] - lo) / (hi - lo) * 100, 1)
    if i >= 20:
        hi10 = max(x[1] for x in hist[i - 10:i])
        if hi10 > 0:
            out["dd10"] = round((hist[i][1] / hi10 - 1) * 100, 2)
    for k in (5, 20):
        if i + k < len(hist):
            out[f"f{k}"] = round((hist[i + k][1] / hist[i][1] - 1) * 100, 2)
    return out


def med(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def win(v):
    return sum(1 for x in v if x > 0) / len(v) * 100 if v else None


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    for key, lab in (("sy585", "素衣不染尘(2023年赛)"), ("dsx615", "大师兄擒妖(2023-03)")):
        ev = collect(d, key)
        buys, sells = [], []
        for d0, code, act in ev:
            m = metrics(conn, code, d0)
            if not m:
                continue
            if act == "买入":
                buys.append({**m, "date": d0, "code": code})
            else:
                if "f5" in m:
                    sells.append(m)
        print(f"\n========== {lab} ==========")
        print(f"买入可定位={len(buys)}  卖出可定位={len(sells)}")
        # pos60桶
        print("-- pos60桶(买入) --")
        for b, lo, hi in (("<=25深位", -1, 25), ("25-40G", 25, 40), ("40-80中", 40, 80), (">80高", 80, 999)):
            sub = [e for e in buys if e.get("pos60") is not None and lo < e["pos60"] <= hi]
            f5 = [e["f5"] for e in sub if "f5" in e]
            if not sub:
                print(f"  {b:8s} n=0")
                continue
            line = f"  {b:8s} n={len(sub):3d}"
            if f5:
                line += f"  f5 med={med(f5):+.2f}% win={win(f5):.0f}%"
            f20 = [e["f20"] for e in sub if "f20" in e]
            if f20:
                line += f"  f20 med={med(f20):+.2f}%"
            print(line)
        na = [e for e in buys if e.get("pos60") is None]
        if na:
            print(f"  pos60不可算 n={len(na)}")
        # 回撤深度U型
        print("-- 回撤深度(10日高,买入) --")
        for lab2, lo, hi in (("创新高(≥0)", 0.01, 999), ("浅回撤0~-3%", -3, 0), ("中回撤-3~-8%", -8, -3), ("深回撤<-8%", -99, -8)):
            sub = [e for e in buys if e.get("dd10") is not None and lo <= e["dd10"] < hi]
            f5 = [e["f5"] for e in sub if "f5" in e]
            if not sub:
                print(f"  {lab2:12s} n=0")
                continue
            line = f"  {lab2:12s} n={len(sub):3d}"
            if f5:
                line += f"  f5 med={med(f5):+.2f}% win={win(f5):.0f}%"
            print(line)
        # 卖出侧+买日特征
        if sells:
            f5 = [e["f5"] for e in sells]
            print(f"-- 卖后f5: n={len(f5)} med={med(f5):+.2f}% win(卖飞方向)={win(f5):.0f}%")
        chgs = [e["chg"] for e in buys if e["chg"] is not None]
        if chgs:
            print(f"-- 买入日涨跌: 中位={med(chgs):+.2f}%  绿盘占比={sum(1 for c in chgs if c<0)/len(chgs)*100:.0f}%")
    conn.close()


if __name__ == "__main__":
    main()
