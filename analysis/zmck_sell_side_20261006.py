# -*- coding: utf-8 -*-
"""案例④卖出侧配对定量: SELL事件卖后fwd5/10/20(配对检验铁律:买卖同锁)
口径: 卖出日收盘起算(他多为涨停日/强势日卖→fwd基线本身偏高,若仍为负=卖对得更狠);
另有「卖出后该票是否创20日新高」粗标。输出CSV+聚合。
"""
import csv
import sqlite3
from pathlib import Path

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SRC = Path(r"D:\zcodesj\reports\zmck_charts_20261006\zmck_trade_events_v1_20261006.csv")
OUT = Path(r"D:\zcodesj\reports\zmck_charts_20261006\zmck_sell_side_20261006.csv")


def main():
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    rows = [r for r in csv.DictReader(SRC.open(encoding="utf-8-sig"))
            if r["action"] == "SELL" and r["code"]]
    out = []
    for r in rows:
        code, d = r["code"], int(r["date"])
        rec = {"date": d, "code": code, "name": r["name"], "note": r["note"][:40],
               "f5": None, "f10": None, "f20": None, "high20_after": None}
        hist = conn.execute(
            "SELECT date,close,high FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            (code,)).fetchall()
        dates = [int(x[0]) for x in hist]
        if d not in dates:
            rec["note"] += "|no_bar"
            out.append(rec)
            continue
        i = dates.index(d)
        for k, f in ((5, "f5"), (10, "f10"), (20, "f20")):
            if i + k < len(hist):
                rec[f] = round((hist[i + k][1] / hist[i][1] - 1) * 100, 2)
        if i + 20 < len(hist):
            hi20 = max(x[2] for x in hist[i + 1:i + 21])
            rec["high20_after"] = round((hi20 / hist[i][1] - 1) * 100, 2)
        out.append(rec)
    conn.close()
    with OUT.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    ok = [r for r in out if r["f20"] is not None]
    print(f"SELL事件={len(out)}  可算fwd={len(ok)}")
    for f in ("f5", "f10", "f20"):
        xs = sorted(r[f] for r in ok if r[f] is not None)
        if xs:
            win = sum(1 for x in xs if x > 0) / len(xs) * 100
            print(f"{f}: n={len(xs)} win(卖后涨=卖飞方向)={win:.0f}% med={xs[len(xs)//2]:+.2f}%")
    hi = [r["high20_after"] for r in ok if r["high20_after"] is not None]
    if hi:
        made_high = sum(1 for x in hi if x > 3)
        print(f"卖后20日内冲高>3%: {made_high}/{len(hi)}")
    print("\n逐笔:")
    for r in ok:
        print(f"  {r['date']} {r['name']}: f5={r['f5']} f20={r['f20']} 高20={r['high20_after']} | {r['note']}")
    print(f"CSV -> {OUT}")


if __name__ == "__main__":
    main()
