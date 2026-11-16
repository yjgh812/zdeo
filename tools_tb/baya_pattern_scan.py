# -*- coding: utf-8 -*-
"""百亚模式扫描器 v1.1（持久版, 2026-10-03）— 屠龙顶底工具链·买侧观察
百亚起飞前夕形态: 年内回撤>=35% + pos250<=15% + 低位>=90天 + pre5>=1.2∧vol20d>=0.95 + ret20<10% + <=20元
输出: data/baya_pattern_scan_latest.json
"""
import json, sqlite3, sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/zcodesj")
from stock_name_lookup import get_name

DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
OUT = r"D:/zcodesj/data/baya_pattern_scan_latest.json"


def main():
    con = sqlite3.connect(DB, uri=True)
    last = con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0]
    recent2 = con.execute("select distinct date from stock_daily_meta_qfq order by date desc limit 2").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "select distinct code from stock_daily_meta_qfq where (code like '60%' or code like '00%' or code like '30%')")]
    hits, scanned = [], 0
    for code in codes:
        rows = con.execute("select date,close,volume from stock_daily_meta_qfq where code=? and date<=? order by date desc limit 420",
                           (code, last)).fetchall()
        if len(rows) < 420 or rows[0][0] < recent2: continue
        nm = get_name(code) or ""
        if "ST" in nm or "退" in nm: continue
        rows = rows[::-1]
        c = np.array([r[1] for r in rows], float)
        v = np.array([r[2] for r in rows], float)
        n = len(c)
        cs, vs = pd.Series(c), pd.Series(v)
        if c[-1] > 20 or c[-1] < 2: continue
        scanned += 1
        lo250, hi250 = cs.iloc[-250:].min(), cs.iloc[-250:].max()
        if hi250 <= lo250: continue
        pos = (c[-1]-lo250)/(hi250-lo250)*100
        dd1y = (c[-1]/hi250-1)*100
        if pos > 15 or dd1y > -35: continue
        pos_series = (cs-cs.rolling(250,min_periods=250).min())/(cs.rolling(250,min_periods=250).max()-cs.rolling(250,min_periods=250).min())*100
        pos_arr = pos_series.values
        low_days = 0
        for k in range(n-1, 249, -1):
            if np.isfinite(pos_arr[k]) and pos_arr[k] < 20: low_days += 1
            else: break
        if low_days < 90: continue
        v120 = np.mean(v[-120:])
        pre5 = np.mean(v[-5:])/v120
        vol20d = np.mean(v[-20:])/v120
        ret20 = (c[-1]/c[-21]-1)*100
        if pre5 < 1.2 or vol20d < 0.95 or ret20 >= 10: continue
        hits.append({"code": code, "name": nm, "px": round(c[-1], 2), "pos": round(pos, 0),
                     "dd1y": round(dd1y, 0), "low_days": low_days, "pre5": round(pre5, 2),
                     "vol20d": round(vol20d, 2), "ret20": round(ret20, 1)})
    con.close()
    hits.sort(key=lambda x: -x["pre5"])
    print(f"扫描 {scanned} 只 | 百亚前夕形态命中 {len(hits)} 只")
    for h in hits:
        print(f"  {h['name']:<6}{h['code']:<8}价{h['px']:>6} pos={h['pos']}% 回撤{h['dd1y']}% 低位{h['low_days']}天 pre5={h['pre5']} vol20d={h['vol20d']}")
    json.dump({"scanned": scanned, "hits": hits}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
