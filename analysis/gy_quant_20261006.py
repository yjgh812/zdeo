# -*- coding: utf-8 -*-
"""归因(千万级分歧低吸) 买卖×K线双口径验证: 757半年赛 artifact
口径同四方对照: pos60桶 + 10日高回撤深度 + 卖后f5 + 买入日涨跌。
"""
import json
import re
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ART = (r"C:\Users\Administrator\.zcode\cli\artifacts"
       r"\sess_3c36e6a1-f539-4b81-9364-0c59613a7474"
       r"\call_77dcdb9b79274d7185e42544-tool-result-4a80b462-b821-4451-bccf-a263b0453c2a.json")
ACTIONS = {"买入", "卖出", "减仓", "加仓"}


def med(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def win(v):
    return sum(1 for x in v if x > 0) / len(v) * 100 if v else None


def main():
    raw = open(ART, encoding='utf-8').read()
    i = raw.find('{"seq":')
    obj, _ = json.JSONDecoder(strict=False).raw_decode(raw[i:])
    rows = obj["rows"]
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    events = []
    for r in rows:
        act = r["act"] if r["act"] in ACTIONS else (r["stock"] if r["stock"] in ACTIONS else None)
        if act not in ACTIONS:
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
    buys, sells = [], []
    nodata = 0
    for d0, code, act in uniq:
        hist = conn.execute(
            "SELECT date,close,volume,change_pct FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date", (code,)).fetchall()
        dates = [int(x[0]) for x in hist]
        if d0 not in dates:
            nodata += 1
            continue
        i = dates.index(d0)
        m = {"chg": hist[i][2]}
        if i >= 59:
            w = [x[1] for x in hist[i - 59:i]]
            lo, hi = min(w), max(w)
            if hi > lo:
                m["pos60"] = round((hist[i][1] - lo) / (hi - lo) * 100, 1)
        if i >= 20:
            hi10 = max(x[1] for x in hist[i - 10:i])
            if hi10 > 0:
                m["dd10"] = round((hist[i][1] / hi10 - 1) * 100, 2)
        for k in (5, 20):
            if i + k < len(hist):
                m[f"f{k}"] = round((hist[i + k][1] / hist[i][1] - 1) * 100, 2)
        if act in ("买入", "加仓"):
            buys.append(m)
        elif act in ("卖出", "减仓") and "f5" in m:
            sells.append(m)
    conn.close()
    print(f"归因(757半年赛): 动作去重={len(uniq)} 买入/加仓可定位={len(buys)} 卖出可定位={len(sells)} 库外={nodata}")
    print("-- pos60桶(买入/加仓) --")
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
    na = sum(1 for e in buys if e.get("pos60") is None)
    if na:
        print(f"  pos60不可算 n={na}")
    print("-- 回撤深度(10日高,买入/加仓) --")
    for lab, lo, hi in (("创新高(≥0)", 0.01, 999), ("浅回撤0~-3%", -3, 0), ("中回撤-3~-8%", -8, -3), ("深回撤<-8%", -99, -8)):
        sub = [e for e in buys if e.get("dd10") is not None and lo <= e["dd10"] < hi]
        f5 = [e["f5"] for e in sub if "f5" in e]
        if not sub:
            print(f"  {lab:12s} n=0")
            continue
        line = f"  {lab:12s} n={len(sub):3d}"
        if f5:
            line += f"  f5 med={med(f5):+.2f}% win={win(f5):.0f}%"
        print(line)
    if sells:
        f5 = [e["f5"] for e in sells]
        print(f"-- 卖后f5: n={len(f5)} med={med(f5):+.2f}% win(卖飞方向)={win(f5):.0f}%")
    chgs = [e["chg"] for e in buys if e["chg"] is not None]
    if chgs:
        print(f"-- 买入日涨跌: 中位={med(chgs):+.2f}%  绿盘占比={sum(1 for c in chgs if c<0)/len(chgs)*100:.0f}%")


if __name__ == "__main__":
    main()
