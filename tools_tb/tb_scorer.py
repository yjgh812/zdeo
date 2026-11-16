# -*- coding: utf-8 -*-
"""
顶底打分器 v2.1（持久版, 2026-10-03）— 屠龙顶底工具链·查询端
双层四规则: TB_TOP_v2/TB_BOT_v2(极值级) + TB_SW_TOP_v2/TB_SW_BOT_v2(波段级), 各5特征命中>=4
特征库: data/tb_feature_lib_v2_20261003.json (指纹 tblib-v2.1-complete-labeling-886k)
用法: python tools_tb/tb_scorer.py --code 600519 [--date 20260930]
语义: 区域预警非择日; 信号不亮≠没顶; 不构成交易信号
"""
import argparse, json, sqlite3, sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LIB = json.load(open(r"D:/zcodesj/data/tb_feature_lib_v2_20261003.json", encoding="utf-8"))
DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)


def score(code, date=None):
    con = sqlite3.connect(DB, uri=True)
    if date:
        rows = con.execute("select date,close,volume from stock_daily_meta_qfq where code=? and date<=? "
                           "order by date desc limit 320", (code, date)).fetchall()[::-1]
    else:
        rows = con.execute("select date,close,volume from stock_daily_meta_qfq where code=? "
                           "order by date desc limit 320", (code,)).fetchall()[::-1]
    con.close()
    if len(rows) < 250:
        return {"error": f"数据不足 {len(rows)}<250"}
    c = np.array([r[1] for r in rows], float)
    v = np.array([r[2] for r in rows], float)
    cs, vs = pd.Series(c), pd.Series(v)
    c250 = c[-250:]                                   # 严格250日窗(pos修复: 勿用全量320)
    lo250, hi250 = c250.min(), c250.max()
    pos = (c[-1]-lo250)/(hi250-lo250)*100 if hi250 > lo250 else 50.0
    v120m = vs.iloc[-120:].mean(); v20m = vs.iloc[-20:].mean()
    vr120 = v[-1]/vs.iloc[-121:-1].mean() if len(vs) > 121 and vs.iloc[-121:-1].mean() > 0 else np.nan
    vr20 = v[-1]/vs.iloc[-21:-1].mean() if len(vs) > 21 and vs.iloc[-21:-1].mean() > 0 else np.nan
    vol20d = v20m/v120m if v120m > 0 else np.nan
    ret120 = (c[-1]/c[-121]-1)*100 if len(c) > 121 else np.nan
    ret60 = (c[-1]/c[-61]-1)*100 if len(c) > 61 else np.nan
    ma20v = cs.iloc[-20:].mean(); ma60v = cs.iloc[-60:].mean()
    b20 = (c[-1]/ma20v-1)*100; b60 = (c[-1]/ma60v-1)*100
    up = c[1:] > c[:-1]
    cu = cd = 0
    for u in up[::-1]:
        if u: cu += 1
        else: break
    for i in range(len(up)-1, -1, -1):
        if not up[i]: cd += 1
        else: break
    f2 = {"pos250": pos, "ret60": ret60, "ret120": ret120, "b20": b20, "b60": b60,
          "vr20": vr20, "vr120": vr120, "vol20d": vol20d, "consec_up": cu, "consec_dn": cd}
    out = {"code": code, "asof": rows[-1][0],
           "features": {k: round(vv, 2) if isinstance(vv, float) else vv for k, vv in f2.items()}}
    for rn in ("TB_TOP_v2", "TB_BOT_v2", "TB_SW_TOP_v2", "TB_SW_BOT_v2"):
        hits = 0
        for cond in LIB["rules_v2"][rn]["features"]:
            fv, op, tv = f2.get(cond[0]), cond[1], cond[2]
            if fv is None or not np.isfinite(fv):
                continue
            if op == ">=" and fv >= tv: hits += 1
            if op == "<=" and fv <= tv: hits += 1
        out[rn] = {"hits": hits, "need": 4, "fire": hits >= 4}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    print(json.dumps(score(a.code, a.date), ensure_ascii=False, indent=1))
