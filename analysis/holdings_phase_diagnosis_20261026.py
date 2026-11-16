# -*- coding: utf-8 -*-
"""存量21只持仓×阶段学诊断 20261026 — 只读零交易权(呈报件, 卖出须主人令)。
每只持仓: 阶段(15状态机) + 阶段层20日预期 + 毒性/强度旗标:
  红旗(呈报卖出考虑)=加速赶顶/高位滞涨/高位回落(负或近零预期+高位)
  绿旗(持有)=超跌探底/阴跌/筑底/低位启动(强或正预期)
  灰旗=中性区。
输出: analysis/holdings_phase_diagnosis_20261026.json + stdout
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/holdings_phase_diagnosis_20261026.json"
ASOF = 20261023

NAMES = {("低","大跌"):"超跌探底",("低","跌"):"阴跌",("低","平"):"筑底横盘",("低","涨"):"低位启动",
         ("低","强涨"):"低位急拉",("中","大跌"):"破位下跌",("中","跌"):"中位回调",("中","平"):"中位震荡",
         ("中","涨"):"温和上行",("中","强涨"):"主升段",("高","跌"):"高位回落",("高","大跌"):"高位崩落",
         ("高","平"):"高位滞涨",("高","涨"):"高位续涨",("高","强涨"):"加速赶顶"}
PHASE_EXP = {"超跌探底":"+6.94%","破位下跌":"+4.58%","低位急拉":"+2.86%","阴跌":"+1.46%",
             "中位回调":"+1.34%","低位启动":"+1.01%","高位续涨":"+0.88%","中位震荡":"+0.49%",
             "筑底横盘":"+0.52%","温和上行":"+0.47%","高位滞涨":"+0.37%","主升段":"+0.20%",
             "高位回落":"+0.16%","加速赶顶":"-2.15%","高位崩落":"+3.03%"}
RED = {"加速赶顶", "高位滞涨", "高位回落", "高位崩落"}
GREEN = {"超跌探底", "阴跌", "筑底横盘", "低位启动", "低位急拉", "破位下跌"}


def main():
    from stop_loss_executor import _fetch_positions_via_mcp
    pos = _fetch_positions_via_mcp()
    if not pos:
        print("[FATAL] 持仓拉取失败")
        return 2
    con = sqlite3.connect(QFQ, uri=True)
    rows = []
    for p in pos:
        code = p["code"][-6:]
        gg = pd.read_sql_query(
            "SELECT date,close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date>=20250601 ORDER BY date",
            con, params=(f"%{code}",))
        if len(gg) < 60:
            rows.append({"code": code, "name": p["name"], "phase": "数据不足", "flag": "灰",
                         "pnl_pct": p["float_pnl_pct"], "mv": p["quantity"] * p["current_price"]})
            continue
        c = gg["close"].to_numpy(float)
        s = pd.Series(c)
        mn = s.rolling(250, min_periods=120).min().iloc[-1]
        mx = s.rolling(250, min_periods=120).max().iloc[-1]
        posi = (c[-1] - mn) / (mx - mn) * 100 if mx > mn else 50.0
        mom20 = c[-1] / c[-21] - 1 if len(c) >= 21 else 0.0
        pl = "低" if posi < 25 else ("中" if posi < 70 else "高")
        ml = ("大跌" if mom20 < -0.15 else "跌" if mom20 < -0.04 else
              "平" if mom20 < 0.04 else "涨" if mom20 < 0.15 else "强涨")
        phase = NAMES.get((pl, ml), "?")
        flag = "红" if phase in RED else ("绿" if phase in GREEN else "灰")
        rows.append({"code": code, "name": p["name"], "close": round(float(c[-1]), 2),
                     "pos250": round(float(posi), 1), "mom20": round(float(mom20), 3),
                     "phase": phase, "phase_exp": PHASE_EXP.get(phase, "?"), "flag": flag,
                     "pnl_pct": p["float_pnl_pct"],
                     "mv_wan": round(p["quantity"] * p["current_price"] / 1e4, 1)})
    con.close()
    rows.sort(key=lambda r: ("红" if r["flag"] == "红" else "绿" if r["flag"] == "绿" else "灰", r["code"]))
    json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_red = sum(1 for r in rows if r["flag"] == "红")
    n_green = sum(1 for r in rows if r["flag"] == "绿")
    print(f"[持仓阶段诊断] 基准{ASOF} | 红(呈报卖出考虑)={n_red} 绿(强相位持有)={n_green} 灰(中性)={len(rows)-n_red-n_green}\n")
    print(f"{'旗':<3}{'代码':<8}{'名称':<7}{'pos':>6}{'mom20':>8}  阶段        阶段预期    浮盈%   市值万")
    for r in rows:
        print(f"{r['flag']:<3}{r['code']:<8}{r['name']:<7}{str(r.get('pos250','')):>6}"
              f"{('%+.0f%%' % (r['mom20']*100)) if isinstance(r.get('mom20'), float) else '':>8}"
              f"  {r['phase']:<10}{r.get('phase_exp',''):>8}   {r['pnl_pct']:>6.1f}  {r.get('mv_wan',''):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
