#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行限价锚回测（响应主人「不能按经验武断判断,要全部测试后再说结论」）
样本=executor台账全部70笔BUY实际提交价(8/26-9/4), 模拟三锚+VWAP参考:
- B1 前收锚: limit=prev_close*1.005
- B2 开盘内锚: limit=min(prev_close*1.010, open*0.995)
- C 回落锚: limit=open*0.995
- VWAP参考(乐观近似,盘中不可严格执行): limit=max(prev*1.005, vwap)
成交判定(保守): limit >= 当日low 视为成交于limit价; 否则未成交。
指标: 节省pp均值(成交笔)/未成交率/未成交票T+5收益(机会成本)/综合期望。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    recs = []
    with open(r"D:\zcodesj\data\executor_orders.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("action") == "BUY" and r.get("price") and r.get("code") and r.get("trade_date"):
                recs.append(r)
    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, open, high, low, close, volume, amount "
                     "from stock_daily_meta_qfq where date >= '20260801'", con)
    con.close()
    piv_o = px.pivot_table(index="date", columns="code", values="open")
    piv_h = px.pivot_table(index="date", columns="code", values="high")
    piv_l = px.pivot_table(index="date", columns="code", values="low")
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_v = px.pivot_table(index="date", columns="code", values="volume")
    piv_a = px.pivot_table(index="date", columns="code", values="amount")
    qd = list(piv_c.index)
    prev_c = piv_c.shift(1)
    import bisect
    fwd5 = piv_c.shift(-5)

    rules = {
        "B1_前收x1.005": lambda T, c: prev_c.loc[T, c] * 1.005,
        "B2_开盘内锚": lambda T, c: min(prev_c.loc[T, c] * 1.010, piv_o.loc[T, c] * 0.995),
        "C_回落0.5%": lambda T, c: piv_o.loc[T, c] * 0.995,
        "VWAP参考(乐观)": lambda T, c: max(prev_c.loc[T, c] * 1.005,
                                          piv_a.loc[T, c] / (piv_v.loc[T, c] * 100)),
    }
    out = {"n_buys": 0, "rules": {}}
    saves = {k: [] for k in rules}
    misses = {k: [] for k in rules}
    used = 0
    for r in recs:
        code, T, p0 = r["code"], r["trade_date"], float(r["price"])
        if T not in piv_o.index or code not in piv_o.columns:
            continue
        if any(pd.isna(x) for x in (piv_o.loc[T, code], piv_l.loc[T, code],
                                    prev_c.loc[T, code], piv_a.loc[T, code], piv_v.loc[T, code])):
            continue
        i = bisect.bisect_left(qd, T)
        f5 = fwd5.loc[T, code] if (i + 5 < len(qd) and code in fwd5.columns) else np.nan
        lo = piv_l.loc[T, code]
        used += 1
        for name, fn in rules.items():
            lim = fn(T, code)
            if pd.isna(lim) or lim <= 0:
                continue
            if lim >= lo:  # 触及限价=>成交于限价
                saves[name].append((p0 - lim) / p0 * 100)
            else:
                misses[name].append({"code": code, "name": r.get("name", ""),
                                     "fwd5": (None if pd.isna(f5) else
                                              (f5 / piv_c.loc[T, code] - 1) * 100)})
    out["n_buys"] = used
    for name in rules:
        s, m = saves[name], misses[name]
        f5s = [x["fwd5"] for x in m if x["fwd5"] is not None]
        fill_rate = len(s) / (len(s) + len(m)) if (len(s) + len(m)) else 0
        mean_save = float(np.mean(s)) if s else 0.0
        miss_f5 = float(np.mean(f5s)) if f5s else None
        # 综合期望: 成交率*节省均值 - 未成交率*未成交票T+5均值(正收益=真实机会成本)
        exp = fill_rate * mean_save - (1 - fill_rate) * (miss_f5 or 0.0)
        out["rules"][name] = {
            "n_fill": len(s), "n_miss": len(m), "fill_rate_pct": round(fill_rate * 100, 1),
            "save_mean_pp": round(mean_save, 3),
            "miss_f5_mean_pct": None if miss_f5 is None else round(miss_f5, 2),
            "expect_pp": round(exp, 3),
            "miss_examples": [{"code": x["code"], "name": x["name"],
                               "fwd5": None if x["fwd5"] is None else round(x["fwd5"], 2)}
                              for x in m[:5]],
        }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    json.dump(out, open(r"D:\zcodesj\results\limit_anchor_test_20260905.json", "w",
                        encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
