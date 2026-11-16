# -*- coding: utf-8 -*-
"""案例④著名刺客事件表×K线机械定量: 61笔image-verified事件逐笔映射qfq
口径: pos60[T-1]=前一日60日区间位置(无前视,与深位首板研究同式); volr=当日量/前5日均量;
is_zt=change_pct>=9.5; gene10=前10日有涨停; fwd*=事件日收盘起算。
桶: <=25深位 / 25-40 G带 / 40-80 中高 / >80 追高H。
注意: 楼层发帖日≈交易日(±1日误差,桶级统计可容忍); qfq不覆盖段标N/A。
输出: reports/zmck_charts_20261006/zmck_event_quant_20261006.csv + 控制台聚合
"""
import csv
import sqlite3
from pathlib import Path

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SRC = Path(r"D:\zcodesj\reports\zmck_charts_20261006\zmck_trade_events_v1_20261006.csv")
OUT = Path(r"D:\zcodesj\reports\zmck_charts_20261006\zmck_event_quant_20261006.csv")


def bucket(pos):
    if pos is None:
        return "N/A"
    if pos <= 25:
        return "<=25深位"
    if pos <= 40:
        return "25-40G"
    if pos <= 80:
        return "40-80中高"
    return ">80追高"


def main():
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    rows = list(csv.DictReader(SRC.open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        if r["action"] == "PLAN" or not r["code"]:
            continue
        code, d = r["code"], int(r["date"])
        rec = {"date": d, "code": code, "name": r["name"], "action": r["action"],
               "pos60p": None, "volr": None, "is_zt": None, "gene10": None,
               "f5": None, "f10": None, "f20": None, "bucket": "N/A", "note": ""}
        hist = conn.execute(
            "SELECT date,close,volume,change_pct FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date", (code,)).fetchall()
        dates = [int(x[0]) for x in hist]
        if d not in dates:
            near = min(dates, key=lambda x: abs(x - d)) if dates else None
            reason = "no_data" if not dates else (
                "pre_qfq_coverage" if d < dates[0] else (
                    "post_coverage_end" if d > dates[-1] else
                    f"suspended_or_gap(nearest={near})"))
            rec["note"] = reason
            out.append(rec)
            continue
        i = dates.index(d)
        if i >= 59:
            w = [x[1] for x in hist[i - 59:i]]
            lo, hi = min(w), max(w)
            if hi > lo:
                rec["pos60p"] = round((hist[i][1] - lo) / (hi - lo) * 100, 1)
        v5 = sum(x[2] for x in hist[i - 5:i]) / 5 if i >= 5 else None
        if v5:
            rec["volr"] = round(hist[i][2] / v5, 2)
        rec["is_zt"] = bool(hist[i][3] is not None and hist[i][3] >= 9.5)
        rec["gene10"] = any(x[3] is not None and x[3] >= 9.5 for x in hist[max(0, i - 10):i])
        for k, f in ((5, "f5"), (10, "f10"), (20, "f20")):
            if i + k < len(hist):
                rec[f] = round((hist[i + k][1] / hist[i][1] - 1) * 100, 2)
        rec["bucket"] = bucket(rec["pos60p"])
        if rec["pos60p"] is None:
            rec["bucket"] = "pos不可算(<60bar)"
        out.append(rec)
    conn.close()
    with OUT.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    buys = [r for r in out if r["action"] in ("BUY", "HOLD")]
    withdata = [r for r in buys if r["pos60p"] is not None]
    print(f"事件(有code)={len(out)}  BUY/HOLD={len(buys)}  其中qfq可定位={len(withdata)}")
    print("\n=== 他的入场×我们的地图(pos60[T-1]桶) ===")
    for b in ("<=25深位", "25-40G", "40-80中高", ">80追高"):
        sub = [r for r in withdata if r["bucket"] == b]
        line = f"{b:8s} n={len(sub):3d}"
        for f in ("f5", "f10", "f20"):
            xs = [r[f] for r in sub if r[f] is not None]
            if xs:
                win = sum(1 for x in xs if x > 0) / len(xs) * 100
                srt = sorted(xs)
                line += f"  {f}: win={win:.0f}% med={srt[len(srt)//2]:+.2f}%"
        print(line)
    newstk = [r for r in buys if r["bucket"] == "pos不可算(<60bar)"]
    print(f"\npos不可算(<60bar;含真IPO次新与2016库起点截断两种成因)={len(newstk)}: ", "、".join(r['name'] for r in newstk))
    other = [r for r in buys if r["bucket"] == "N/A"]
    for r in other:
        print(f"  库外/停牌: {r['date']} {r['name']} {r['note']}")
    zt = [r for r in buys if r["is_zt"]]
    print(f"\n涨停日入场(打板/排板)占比: {len(zt)}/{len(buys)} = {len(zt)/len(buys)*100:.0f}%")
    volrs = sorted(r["volr"] for r in buys if r["volr"])
    if volrs:
        print(f"volr: med={volrs[len(volrs)//2]}  p25={volrs[len(volrs)//4]}  p75={volrs[len(volrs)*3//4]}")
    gene = [r for r in buys if r["gene10"]]
    print(f"近10日有涨停(基因票)占比: {len(gene)}/{len(buys)}")
    nofwd = [r for r in withdata if r["f20"] is None]
    print(f"f20不足段(数据尾/停牌): {len(nofwd)} 笔")
    print(f"CSV -> {OUT}")


if __name__ == "__main__":
    main()
