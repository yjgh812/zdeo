# -*- coding: utf-8 -*-
"""利润一半落袋 影子记账月更（选项A 影子记账实验例行化，观测层零交易权）
主人 2026-10-17 选「a」批准选项A + 「按建议继续」批准月度例行与晨报呈现（SOP一百四十四⑬）。
忠实利弗莫尔原义=资金管理非止盈触发：每笔盈利平仓后已实现利润×50% 标记「落袋锁定」，
不回流再投入基数。一阶反事实（cash-drag 近似，无重模拟）：
  NAV_落袋(t) = NAV_实际(t) − Σ L_i×(NAV(t)/NAV(t_i)−1)
数据源：recon_westock_2026_trades.json（westock 对账全量成交）+ qfq 日线收盘估值
+ vol_target_shadow.jsonl total_assets 两锚（10/02 假期快照校准/10/14 独立校验）。
全史重算幂等；参数锚定于脚本常量（锚值/50% 比例变更须主人批准并改本文件留痕）。
边界：纯核算影子层零交易权，禁止接交易闸门/交易 cron/executor；不改变任何仓位计算与卖出规则。

用法:
  python analysis/profit_banking_shadow_monthly.py            # 全史重算+台账原子覆盖+快照+简报
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

TRADES = r"D:\zcodesj\data\recon_westock_2026_trades.json"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(r"D:\zcodesj\data\profit_banking_shadow_ledger.jsonl")
STATE = Path(r"D:\zcodesj\data\profit_banking_shadow_state.json")
BRIEF = Path(r"D:\zcodesj\data\profit_banking_shadow_brief_latest.txt")
ANCHOR_CAL = ("20261002", 879890.16)   # 校准锚（假期快照）
ANCHOR_VAL = ("20261014", 854062.80)   # 独立校验锚
BANK_RATIO = 0.5                        # 利润落袋比例（利弗莫尔原义一半）


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


trades = [t for t in json.load(open(TRADES, encoding="utf-8")) if t.get("status") == "已成交"]
trades.sort(key=lambda t: t["filledAt"])

conn = sqlite3.connect(r"file:" + DB + "?mode=ro", uri=True)
px = pd.read_sql("SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20260701'", conn)
conn.close()
px = px[px["close"] > 0]
px["date"] = px["date"].astype(str)
close_map = {(r.code, r.date): float(r.close) for r in px.itertuples()}
trade_days = sorted(px["date"].unique())
day_index = {d: i for i, d in enumerate(trade_days)}


def last_close(code6, datestr):
    i = day_index.get(datestr)
    cand = trade_days[: (i + 1) if i is not None else len(trade_days)]
    for d in reversed(cand):
        p = close_map.get((code6, d))
        if p:
            return p
    return None


# ---- pass1: 按日推演 FIFO ----
by_day = {}
for t in trades:
    by_day.setdefault(t["filledAt"][:10].replace("-", ""), []).append(t)

cash_ex = 0.0
lots = {}
sell_events = []
daily_banked = {}
daily_state = {}

for d in trade_days:
    for t in by_day.get(d, []):
        code6 = t["code"][2:]
        qty = int(float(t["quantity"]))
        price = float(t["price"])
        amt = float(t["amount"])
        if t["direction"] == "buy":
            cash_ex -= amt
            lots.setdefault(code6, []).append({"qty": qty, "price": price})
        else:
            cash_ex += amt
            remaining = qty
            cost = 0.0
            sold = 0
            while remaining > 0 and lots.get(code6):
                lot = lots[code6][0]
                take = min(remaining, lot["qty"])
                cost += take * lot["price"]
                sold += take
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] == 0:
                    lots[code6].pop(0)
            realized = sold * price - cost
            banked = realized * BANK_RATIO if realized > 0 else 0.0
            sell_events.append({"date": d, "code": code6, "name": t.get("name", ""),
                                "qty": sold, "sell_price": price,
                                "realized_pnl": round(realized, 2),
                                "banked_50pct": round(banked, 2),
                                "is_win": bool(realized > 0)})
            if banked > 0:
                daily_banked[d] = daily_banked.get(d, 0.0) + banked
    hval = 0.0
    for c6, ls in lots.items():
        p = last_close(c6, d)
        if p:
            for lot in ls:
                hval += lot["qty"] * p
    daily_state[d] = (cash_ex, hval)

# ---- 锚校准初始现金 ----
a_date, a_total = ANCHOR_CAL
asof = max((d for d in trade_days if d <= a_date), default=None)
cash_asof, hval_asof = daily_state[asof]
init_cash = a_total - hval_asof - cash_asof

# ---- pass2: NAV 序列 + 反事实 ----
nav_hist = {}
for d in trade_days:
    cash_ex, hval = daily_state[d]
    nav_hist[d] = cash_ex + init_cash + hval

lock_legs = []
pool = 0.0
pool_by_day = {}
for d in sorted(daily_banked):
    pool += daily_banked[d]
    pool_by_day[d] = pool
    lock_legs.append({"date": d, "L": round(daily_banked[d], 2), "pool_after": round(pool, 2),
                      "nav_at_event": round(nav_hist[d], 2)})

series = []
for d in trade_days:
    nav = nav_hist[d]
    drag = 0.0
    for leg in lock_legs:
        if d >= leg["date"] and leg["nav_at_event"] > 0:
            drag += leg["L"] * (nav / leg["nav_at_event"] - 1)
    series.append({"date": d, "nav_actual": round(nav, 2),
                   "nav_banked": round(nav - drag, 2),
                   "lock_pool": pool_by_day.get(d, 0.0)})
s = pd.DataFrame(series).set_index("date")
s.index = pd.to_datetime(s.index)
nav_a_s = s["nav_actual"].astype(float)
nav_b_s = s["nav_banked"].astype(float)


def metrics(x, label):
    ret = x.pct_change().dropna()
    yrs = len(x) / 244.0
    cagr = (x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 and x.iloc[0] > 0 else 0.0
    dd = (x / x.cummax() - 1).min()
    vol = float(ret.std() * np.sqrt(244)) if len(ret) > 1 else 0.0
    return {"label": label, "days": int(len(x)),
            "ret_total_pct": round(float(x.iloc[-1] / x.iloc[0] - 1) * 100, 2),
            "cagr_ann_pct": round(float(cagr) * 100, 2),
            "maxdd_pct": round(float(dd) * 100, 2),
            "vol_ann_pct": round(vol * 100, 2)}


m_actual = metrics(nav_a_s, "实际路径")
m_banked = metrics(nav_b_s, "落袋纪律路径")

val_check = {}
for ad, total in (ANCHOR_CAL, ANCHOR_VAL):
    asof_d = max((d for d in trade_days if d <= ad), default=None)
    if asof_d:
        ce, hv = daily_state[asof_d]
        val_check[ad] = {"asof_used": asof_d, "recon_nav": round(ce + init_cash + hv, 2),
                         "anchor": total, "diff": round(ce + init_cash + hv - total, 2)}

win_events = [e for e in sell_events if e["is_win"]]
R = {
 "meta": {"window": f"{trade_days[0]}~{trade_days[-1]}", "trade_days": len(trade_days),
          "sell_events": len(sell_events), "win_sells": len(win_events),
          "realized_pnl_total": round(sum(e["realized_pnl"] for e in sell_events), 2),
          "locked_pool_total": round(sum(e["banked_50pct"] for e in sell_events), 2),
          "init_cash_calibrated": round(init_cash, 2),
          "recon_trades_used": len(trades),
          "note": "锚10/02为假期快照按≤10/02最后交易日估值校准; 10/14为独立校验点; 一阶反事实无重模拟"},
 "anchor_check": val_check,
 "metrics_actual": m_actual,
 "metrics_banked": m_banked,
 "delta": {"cagr_pp": round(m_banked["cagr_ann_pct"] - m_actual["cagr_ann_pct"], 2),
           "maxdd_improve_pp": round(m_actual["maxdd_pct"] - m_banked["maxdd_pct"], 2),
           "vol_improve_pp": round(m_actual["vol_ann_pct"] - m_banked["vol_ann_pct"], 2)},
 "lock_legs": lock_legs,
}
json.dump(R, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tmp = LEDGER.with_suffix(".jsonl.tmp")
pd.DataFrame(sell_events).to_json(tmp, orient="records", lines=True, force_ascii=False)
os.replace(tmp, LEDGER)

brief = (
    f"利润一半落袋 影子简报 生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M}\n"
    f"窗口 {R['meta']['window']}（{R['meta']['trade_days']}交易日, 下行/上行 regime 如实标注: 窗口总收益 {m_actual['ret_total_pct']}%）\n"
    f"锁定池累计={R['meta']['locked_pool_total']}（盈利平仓利润×50%, {len(lock_legs)}条锁定腿）\n"
    f"落袋路径 vs 实际路径: 总收益 {m_banked['ret_total_pct']}% vs {m_actual['ret_total_pct']}%"
    f"（差{round(m_banked['ret_total_pct'] - m_actual['ret_total_pct'], 2)}pp） | "
    f"MaxDD {m_banked['maxdd_pct']}% vs {m_actual['maxdd_pct']}%（改善{R['delta']['maxdd_improve_pp']}pp） | "
    f"波动 {m_banked['vol_ann_pct']}% vs {m_actual['vol_ann_pct']}%\n"
    f"纪律价值=行为约束(兑现盈利抑制回吐)非收益增强; 单regime窗口结论不外推; 零卖出动作\n"
)
atomic_write_text(BRIEF, brief)
print(brief)
print(json.dumps({"anchor_check": R["anchor_check"], "delta": R["delta"]}, ensure_ascii=False))
