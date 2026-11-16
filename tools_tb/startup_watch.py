# -*- coding: utf-8 -*-
"""启动观察监控器 v1.1（持久版, 2026-10-03）— 屠龙顶底工具链·买侧
全市场底部安静区扫描(pos<=20∧量缩>=15%, 剔ST/退/仙股) + 当日启动触发 + 距启动量倍数
输出: data/startup_watch_latest.json
"""
import json, sqlite3, sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/zcodesj")
from stock_name_lookup import get_name

DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
OUT = r"D:/zcodesj/data/startup_watch_latest.json"


def market_layer(con, last):
    low = tot = 0
    recent2 = con.execute("select distinct date from stock_daily_meta_qfq order by date desc limit 2").fetchone()[0]
    for (code,) in con.execute("select distinct code from stock_daily_meta_qfq where (code like '60%' or code like '00%' or code like '30%')"):
        rows = con.execute("select date,close from stock_daily_meta_qfq where code=? and date<=? order by date desc limit 250",
                           (code, last)).fetchall()
        if len(rows) < 250 or rows[0][0] < recent2: continue
        c = np.array([r[1] for r in rows], float)
        lo_, hi_ = c.min(), c.max()
        if hi_ > lo_ and (c[0]-lo_)/(hi_-lo_)*100 < 40: low += 1
        if hi_ > lo_: tot += 1
    try:
        erp = sqlite3.connect(r"file:D:/zcodesj/erp_history.db?mode=ro", uri=True).execute(
            "select erp_pct_5y from erp_daily order by date desc limit 1").fetchone()[0]
    except Exception:
        erp = None
    return {"date": last, "低区%": round(low/tot*100, 1), "ERP分位%": round(erp, 1) if erp else None,
            "双票": "亮" if (low/tot*100 >= 60 and erp and erp >= 80) else "未亮"}


def main():
    con = sqlite3.connect(DB, uri=True)
    last = con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0]
    mkt = market_layer(con, last)
    print("=== 市场层 ===")
    print(json.dumps(mkt, ensure_ascii=False))
    fired, near, quiet_n = [], [], 0
    for (code,) in con.execute("select distinct code from stock_daily_meta_qfq where (code like '60%' or code like '00%' or code like '30%')"):
        rows = con.execute("select date,close,volume from stock_daily_meta_qfq where code=? and date<=? order by date desc limit 260",
                           (code, last)).fetchall()
        if len(rows) < 260: continue
        rows = rows[::-1]
        c = np.array([r[1] for r in rows], float)
        v = np.array([r[2] for r in rows], float)
        cs, vs = pd.Series(c), pd.Series(v)
        lo250, hi250 = cs.iloc[-250:].min(), cs.iloc[-250:].max()
        if hi250 <= lo250: continue
        pos = (c[-1]-lo250)/(hi250-lo250)*100
        v20 = vs.iloc[-20:].mean(); v120 = vs.iloc[-120:].mean()
        if v120 <= 0 or pos > 20 or v20/v120 > 0.85: continue
        nm = get_name(code) or ""
        if ("ST" in nm) or ("退" in nm) or c[-1] < 2.0: continue
        quiet_n += 1
        r1 = c[-1]/c[-2]-1
        need_mult = 2.0*v20/v[-1] if v[-1] > 0 else 99
        rec = {"code": code, "name": nm or code, "pos": round(pos, 0), "shrink": round(v20/v120, 2),
               "need_mult": round(need_mult, 1), "r1%": round(r1*100, 1), "px": round(c[-1], 2)}
        if r1 >= 0.03 and v[-1] >= 2.0*v20: fired.append(rec)
        else: near.append(rec)
    con.close()
    near.sort(key=lambda x: x["need_mult"])
    print(f"\n=== 安静区总数: {quiet_n} | 当日启动触发: {len(fired)} ===")
    for r in fired:
        tag = " ⚠️>30元只研究" if r["px"] > 30 else ""
        print(f"  {r['name']} {r['code']} @ {r['px']} 当日+{r['r1%']}% pos={r['pos']}%{tag}")
    print("--- 最接近启动 Top15 ---")
    for r in near[:15]:
        print(f"  {r['name']:<6}{r['code']} pos={r['pos']:>3}% 量缩{r['shrink']:.2f} 距启动需量x{r['need_mult']:.1f} @{r['px']}")
    json.dump({"mkt": mkt, "quiet_n": quiet_n, "fired": fired, "near_top": near[:50]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
