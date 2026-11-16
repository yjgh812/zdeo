# -*- coding: utf-8 -*-
"""PD-028① 退出改制影子回放 (PREREG_EXIT_T1_20261003 激活版, 主人10/3裁决)
A臂=现行实际执行(trade_records真实买卖, 现价mark); B臂=信号退出制反事实(同入场):
  出场=首触 {S-5: close<=成本*0.92} 或 {爆量末段代理: volr5>=2.0 且 段内ret>=15% 且 pos60>=80}
  执行=信号日收盘(T1: 即时优先); 代理定义偏离C3引擎处已注记(引擎历史化后对齐)。
判据(PRD-028①预钉死): 影子>=60交易日, B-A>=+5pp或回撤改善>=3pp; 满窗反向=FAIL。
产出: reports/t1_ab_shadow_YYYYMMDD.csv + 控制台
"""
import json
import sqlite3

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ATT = r"D:\zcodesj\attribution\attribution.db"
POOL = r"D:\zcodesj\data\swing_phase_holdings_latest.json"
OUT = r"D:\zcodesj\reports\t1_ab_shadow_20261003.csv"

hold = {r["code"]: r["name"] for r in json.load(open(POOL, encoding="utf-8"))["records"]}
conn = sqlite3.connect(f"file:{ATT}?mode=ro", uri=True)
fills = pd.read_sql(
    "SELECT code,name,trade_date,action,price,volume FROM trade_records "
    "WHERE action IN ('BUY','SELL') ORDER BY trade_date",
    conn)
conn.close()
fills["code6"] = fills["code"].str[-6:]
fills = fills[fills["code6"].isin(hold)]
print("fills(池内):", len(fills), "codes:", fills["code6"].nunique())

qcon = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
rows_out = []
summary = {"A": [], "B": [], "hold_days": []}
for code6, grp in fills.groupby("code6"):
    buys = grp[grp["action"] == "BUY"]
    sells = grp[grp["action"] == "SELL"]
    if buys.empty:
        continue
    entry_d, entry_p = int(buys.iloc[0]["trade_date"]), float(buys.iloc[0]["price"])
    # 加权成本(多笔买入)
    entry_p = float((buys["price"] * buys["volume"]).sum() / buys["volume"].sum())
    bars = pd.read_sql(
        "SELECT date,close,volume FROM stock_daily_meta_qfq WHERE code=? AND date>=? "
        "ORDER BY date", qcon, params=[code6, entry_d])
    if len(bars) < 10:
        continue
    bars = bars.reset_index(drop=True)
    bars["vol5"] = bars["volume"].rolling(5, min_periods=5).mean().shift(1)
    bars["ret"] = bars["close"].pct_change()
    cost = entry_p
    seg_anchor = entry_p  # 段锚=成本(入场), 段内ret以锚计
    exit_i, exit_px, exit_reason = None, None, ""
    for i, r in bars.iterrows():
        if i == 0:
            continue
        c = r["close"]
        if c <= cost * 0.92:
            exit_i, exit_px, exit_reason = i, c, "S-5"
            break
        volr = r["volume"] / r["vol5"] if r["vol5"] else 0
        seg_ret = c / seg_anchor - 1
        pos_ret20 = bars["close"].iloc[max(0, i - 20):i + 1]
        pos60prox = seg_ret >= 0.15 and c >= bars["close"].iloc[: i + 1].tail(60).max() * 0.98
        if volr >= 2.0 and seg_ret >= 0.15 and c >= bars["close"].iloc[: i + 1].tail(60).max() * 0.95:
            exit_i, exit_px, exit_reason = i, c, "爆量末段代理"
            break
    last_close = float(bars.iloc[-1]["close"])
    if exit_i is None:
        b_ret = last_close / cost - 1
        b_note = f"未触发,现价mark({len(bars)}日)"
        a_days = len(bars)
    else:
        b_ret = exit_px / cost - 1
        b_note = f"{exit_reason}@{bars.iloc[exit_i]['date']}"
        a_days = exit_i
    # A臂: 现行实际(全部SELL填充重放 + 现价mark残余)
    invested, realized = 0.0, 0.0
    cur_sh = 0
    for _, f in grp.sort_values("trade_date").iterrows():
        px, v = float(f["price"]), float(f["volume"])
        if f["action"] == "BUY":
            invested += px * v
            cur_sh += v
        else:
            realized += px * v
            cur_sh -= v
    cur_sh = max(cur_sh, 0)
    a_ret = (realized + cur_sh * last_close) / invested - 1 if invested else float("nan")
    rows_out.append({
        "code": code6, "name": hold.get(code6, ""), "entry": entry_d, "cost": round(cost, 3),
        "A_ret%": round(a_ret * 100, 2),
        "B_ret%": round(b_ret * 100, 2), "B_exit": b_note,
        "diff%": round((b_ret - a_ret) * 100, 2), "bars": len(bars),
    })
    if exit_i is not None:
        summary["A"].append(a_ret)
        summary["B"].append(b_ret)
        summary["hold_days"].append(exit_i)

qcon.close()
out = pd.DataFrame(rows_out).sort_values("diff%")
out.to_csv(OUT, index=False, encoding="utf-8-sig")
print("=== 已触发B臂出场的持仓 ===")
trig = out[out["B_exit"].str.contains("S-5|爆量", na=False)]
print(trig.to_string(index=False))
n = len(summary["B"])
if n:
    A, B = np.array(summary["A"]) * 100, np.array(summary["B"]) * 100
    print(f"=== 触发子集(n={n}): A中位={np.median(A):+.2f}% B中位={np.median(B):+.2f}% B-A中位={np.median(B)-np.median(A):+.2f}pp ===")
print(f"全池均值: A={out['A_ret%'].mean():+.2f}% B={out['B_ret%'].mean():+.2f}% diff={out['diff%'].mean():+.2f}pp")
print("saved", OUT)
