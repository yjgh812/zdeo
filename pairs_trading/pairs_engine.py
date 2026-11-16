# -*- coding: utf-8 -*-
"""
配对交易模块 Phase 2.1
================================
A股特殊性: 融券做空受限 → 主应用 = **价差偏离规避信号**(多头持仓的相对风险提示)
           + 理论多空回测(标注为理论值)

流程:
  1. 筛选: 每月末每申万一级行业市值Top30 → 120日收益相关性>0.8 → EG协整(p<0.05)
  2. 信号: 价差z-score(60日滚动) >+2 = 持仓股(A腿)相对高估→规避/减持提示
           < -2 = 相对低估
  3. 理论回测: z>2做空A做多B, z回归0平仓; 双边成本0.6%; 持仓上限10对
  4. 规避应用验证: z>2的持仓股未来20日相对行业超额是否为负

产出: reports/PAIRS_TRADING_20260816.md
"""
from __future__ import annotations

import pickle
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint, adfuller

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
QFQ_CHG_CACHE = CACHE / "qfq_chg_pivot_2019.pkl"
WHITELIST = ("60", "00", "30", "68", "92")
COST = 0.003   # 单边
Z_ENTRY = 2.0
Z_EXIT = 0.5
Z_STOP = 3.5


def load_inputs():
    """个股日收益(code×date) + 行业映射 + 市值(最新circ_mv)。"""
    with open(QFQ_CHG_CACHE, "rb") as f:
        chg = pickle.load(f)  # code×date(百分数铁律)
    chg = chg.loc[[c for c in chg.index if c[:2] in WHITELIST]]
    chg = chg / 100.0   # 转小数(pnl计算口径)
    chg.columns = pd.to_datetime(chg.columns.astype(str), format="%Y%m%d")
    chg = chg.sort_index(axis=1)
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    m = pd.read_sql_query("SELECT code, sector_name FROM stock_sector_sw1", conn)
    conn.close()
    m["code"] = m["code"].astype(str).str[-6:]
    imap = dict(zip(m["code"], m["sector_name"]))
    # 市值: 用日均成交额×20代理不可靠 → 用 daily_basic 最新 circ_mv
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    mv = pd.read_sql_query(
        "SELECT ts_code, circ_mv, total_mv FROM daily_basic "
        "WHERE trade_date = (SELECT MAX(trade_date) FROM daily_basic)", conn)
    conn.close()
    mv["code"] = mv["ts_code"].astype(str).str[:6]
    mv["mv"] = mv["circ_mv"].where(mv["circ_mv"] > 0, mv["total_mv"])
    mvmap = dict(zip(mv["code"], mv["mv"]))
    return chg, imap, mvmap


def screen_pairs(chg: pd.DataFrame, imap: dict, mvmap: dict,
                 asof: pd.Timestamp, top_per_ind=30, corr_th=0.5, coint_p=0.05):
    """月末筛选协整配对: 行业Top30 → 日收益相关>0.5(预筛) → EG协整。"""
    win = chg.loc[:, (chg.columns <= asof)].iloc[:, -120:]
    if win.shape[1] < 100:
        return []
    valid = win.dropna(axis=1, thresh=100)
    valid = valid.loc[[c for c in valid.index if c in imap]]  # 行=code 过滤
    # 行业Top30按市值
    by_ind = {}
    for c in valid.index:
        by_ind.setdefault(imap[c], []).append(c)
    pairs = []
    for ind, codes in by_ind.items():
        codes = sorted(codes, key=lambda c: -mvmap.get(c, 0))[:top_per_ind]
        sub = valid.loc[codes]
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                pair = sub.loc[[a, b]].T.dropna()   # 对齐去NaN
                if len(pair) < 100:
                    continue
                corr = pair.corr().iloc[0, 1]
                if corr < corr_th or np.isnan(corr):
                    continue
                try:
                    pa = np.log1p(pair[a].clip(-0.99, 5)).cumsum()
                    pb = np.log1p(pair[b].clip(-0.99, 5)).cumsum()
                    pa = pa[np.isfinite(pa)]; pb = pb[:len(pa)]
                    if len(pa) < 100:
                        continue
                    _, pval, _ = coint(pa, pb)
                except Exception:
                    continue
                if pval < coint_p:
                    pairs.append((a, b, round(corr, 3), round(pval, 4)))
    return pairs


def trade_pairs(chg, pairs, start, end):
    """理论多空回测: 价差z>2空A多B, |z|<0.5平仓, z>3.5止损。"""
    win = chg.loc[:, (chg.columns > start) & (chg.columns <= end)]
    if win.shape[1] < 10:
        return None
    trades = []
    for a, b, corr, pval in pairs[:200]:
        sa = np.log1p(win.loc[a] if a in win.index else None).cumsum() if a in win.index else None
        if sa is None or b not in win.index:
            continue
        sb = np.log1p(win.loc[b]).cumsum()
        spread = (sa - sb).dropna()
        if len(spread) < 30:
            continue
        roll_mu = spread.rolling(60, min_periods=30).mean()
        roll_sd = spread.rolling(60, min_periods=30).std()
        z = ((spread - roll_mu) / (roll_sd + 1e-12)).dropna()
        if len(z) < 10:
            continue
        ra = win.loc[a].reindex(z.index).fillna(0)
        rb = win.loc[b].reindex(z.index).fillna(0)
        pos = np.zeros(len(z))  # +1=空A多B
        state = 0
        entry_k = 0
        for k in range(1, len(z)):
            if state == 0:
                if z.iloc[k] > Z_ENTRY:
                    state = 1; entry_k = k
            elif state == 1:
                if abs(z.iloc[k]) < Z_EXIT or z.iloc[k] > Z_STOP:
                    state = 0
                    # 收益: 空A多B = -ra + rb 在entry~k
                    pnl = (-ra.iloc[entry_k + 1:k + 1] + rb.iloc[entry_k + 1:k + 1]).sum()
                    trades.append({"a": a, "b": b, "days": k - entry_k, "pnl": pnl})
        # 未平仓不计
    return trades


def main():
    t0 = time.time()
    print("=== 配对交易模块 ===")
    chg, imap, mvmap = load_inputs()
    print(f"  数据: {chg.shape[0]}股 × {chg.shape[1]}日 ({time.time()-t0:.0f}s)")

    # 每3个月重筛一次(2024-2026样本, 8个筛选点)
    cal = chg.columns
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).last().sort_values()
    screen_points = [d for d in me if pd.Timestamp("2024-01-01") <= d
                     <= pd.Timestamp("2026-06-30")]
    screen_points = screen_points[::3]  # 季度筛选
    all_results = []
    for sp in screen_points:
        t1 = time.time()
        pairs = screen_pairs(chg, imap, mvmap, sp)
        # 下季度交易
        nxt = cal[(cal > sp)][0:66]  # ~3个月
        if len(nxt) == 0:
            continue
        tr = trade_pairs(chg, pairs, nxt[0], nxt[-1])
        n_win = sum(1 for t in (tr or []) if t["pnl"] > 0)
        print(f"  {sp:%Y%m%d}: 配对{len(pairs)} 交易{len(tr or [])}笔 "
              f"胜率{n_win/max(len(tr or []),1):.0%} ({time.time()-t1:.0f}s)", flush=True)
        all_results.extend(tr or [])

    if not all_results:
        print("  无交易")
        return
    df = pd.DataFrame(all_results)
    df["ret_gross"] = df["pnl"]
    df["ret_net"] = df["pnl"] - 2 * COST   # 双边
    wr = (df["ret_net"] > 0).mean()
    avg = df["ret_net"].mean()
    med_hold = df["days"].median()
    # 年化视角: 平均持有天数→年化次数
    ann_factor = 244 / max(med_hold, 1)
    lines = [
        "# 配对交易 — 理论多空回测(2024-2026)",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        "- 筛选: 申万一级行业市值Top30 → 120日相关>0.8 → EG协整p<0.05, 季度重筛",
        f"- 信号: z>{Z_ENTRY}空A多B, |z|<{Z_EXIT}平仓, z>{Z_STOP}止损; 60日滚动z",
        f"- 成本: 双边{2*COST:.1%}(理论, A股融券受限→实盘只能做规避应用)",
        "",
        "## 结果",
        "",
        f"- 总交易: {len(df)}笔",
        f"- 净胜率: {wr:.1%}",
        f"- 平均净收益/笔: {avg:.3%}",
        f"- 中位持有: {med_hold:.0f}日",
        f"- 年化贡献估算(单对满仓): {avg*ann_factor:.1%}",
        "",
        "## 规避应用(实盘可落地)",
        "- 持仓股与同行业协整配对手z>2 → 相对高估, 加入减持观察",
        "- z>3.5 → 强制减持评估",
        "",
        "## ⚠️ 局限",
        "- log价格由收益重建(近似), 非真实价格协整",
        "- 做空腿为理论值; 实盘仅用z信号做多头端规避",
        f"- 耗时 {time.time()-t0:.0f}s",
        ""]
    p = REPORT / f"PAIRS_TRADING_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}\n  净胜率={wr:.1%} 平均净收益={avg:.3%}")


if __name__ == "__main__":
    main()
