# -*- coding: utf-8 -*-
"""观察臂结算器(E臂加速赶顶+驱动力14样本库, 零买入纯价格跟踪) 20261026 — 只读零交易权。
口径与买入臂完全可比: 入场=20261026开盘, 出场=20交易日收盘或-8%止损首触, 毛口径(零成本,
观察样本无成交; 与买入臂对比时买入臂看净口径=毛-0.2%)。
用法: python analysis/obs_settle.py  (11/20后运行; 未到期则显示当前浮动)
输出: data/obs_settle_latest.json + stdout
"""
import glob
import json
import sqlite3
import sys
from datetime import datetime

import numpy as np

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
ENTRY_D = 20261026
OUT = "data/obs_settle_latest.json"


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


def load_samples():
    samples = []
    arms = json.load(open("analysis/type_sample_arms_20261026.json", encoding="utf-8"))
    pred = arms["predictions"]
    for x in arms["arms"].get("E_加速赶顶观察", []):
        samples.append({"code": x["code"], "name": x["name"], "group": "E_加速赶顶",
                        "predict": pred.get("E_加速赶顶观察", "-2.15%"), "close0": x["close"]})
    for r in json.load(open("analysis/driver_sample_library_20261026.json", encoding="utf-8")):
        samples.append({"code": r["code"], "name": r["name"], "group": f"库_{r['driver_type']}",
                        "predict": r.get("phase_exp", "?"), "close0": r.get("close")})
    return samples


def main():
    con = sqlite3.connect(QFQ, uri=True)
    qfq_max = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    rows = []
    for s in load_samples():
        hist = con.execute(
            "SELECT date,open,close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date>=20261001 ORDER BY date",
            (f"%{s['code']}",)).fetchall()
        hd = {int(d): (o, c) for d, o, c in hist}
        if ENTRY_D not in hd:
            rows.append({**s, "status": "无入场日数据"})
            continue
        entry = hd[ENTRY_D][0]
        if entry <= 0:
            rows.append({**s, "status": "入场价异常"})
            continue
        dates = sorted(d for d in hd if d >= ENTRY_D)
        stop = entry * 0.92
        px, how = None, "持有中"
        for k, d in enumerate(dates):
            c = hd[d][1]
            if c <= stop:
                px, how = c, f"止损@{d}"
                break
            if k >= 19:
                px, how = c, "20日到期"
                break
        if px is None:
            c_now = hd[dates[-1]][1] if dates else entry
            rows.append({**s, "status": f"持有中(第{len(dates)}日)",
                         "float": round(c_now / entry - 1, 4)})
        else:
            rows.append({**s, "status": how, "ret": round(px / entry - 1, 4)})
    con.close()
    # 分组聚合(已到期样本)
    groups = {}
    for r in rows:
        if "ret" in r:
            g = groups.setdefault(r["group"], [])
            g.append(r["ret"])
    agg = {k: {"n": len(v), "mean": round(float(np.mean(v)), 4),
               "win": round(float(np.mean([x > 0 for x in v])), 4)} for k, v in groups.items()}
    out = {"settled_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "qfq_max": qfq_max,
           "entry_date": ENTRY_D, "rows": rows, "group_agg": agg}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[观察结算] qfq最新={qfq_max} (入场{ENTRY_D}, 到期约20261120)")
    for k, v in agg.items():
        print(f"  {k:<18} n={v['n']} 均值={v['mean']:+.2%} 胜率={v['win']:.0%}")
    pend = sum(1 for r in rows if "ret" not in r)
    print(f"  (未到期/无数据 {pend} 只)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
