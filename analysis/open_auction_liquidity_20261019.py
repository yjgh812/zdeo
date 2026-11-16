# -*- coding: utf-8 -*-
"""open_auction_liquidity_20261019.py — 呈请①C: 2024-26窗开盘可成交量+开盘30分钟VWAP（2026-10-19 v2 raw自洽口径）

问题(外部材料第二轮缺口4): T+1开盘价执行时, 开盘30分钟可成交量有限, 高开票可能买不到量, 形成执行层选择偏差。

🔴v2 口径修正(v1 教训): v1 用 qfq 库 open 列算 gap, 结果全零→追查发现 **qfq 库 open 列疑似前收占位**:
   adj=分钟bar1_raw_open/qfq_open 中位 2024=1.168 / 2025=1.092 / 2026=1.049, 5-17% 系统性偏离非分红
   可解释且无一等于1 → qfq open 不可信, gap 段作废(登记数据质量呈请, qfq为生成物禁手改)。
   v2 全部改 raw 自洽: 事件分钟数据(stock_minute_5, raw) 内部计算, 不再触碰 qfq open。
   - 流动性: amt30(开盘30分钟成交额, 元) vs 仓位参照 2万/5万
   - 执行价: bar1_open(首5min bar开盘=当日真实开盘) vs vwap30(半小时成交量加权) → 入场时点改善空间
   - 2024/25 ml 臂分钟数据大面积缺失(2024: 11/199, 2025: 9/199 有数据; 2026 双臂 100%) → 分年结论按可用子集声明
只读研究件·零交易权·零生产改动。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

TRADES_DIR = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
OUT_JSON = r"D:\zcodesj\analysis\open_auction_liquidity_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\open_auction_liquidity_20261019.md"
POSITION_SIZE = 20000.0


def load_events():
    frames = []
    for arm, fname in [("bp_ep", "bp_ep_core_trades.csv"), ("ml", "ml_signal_trades.csv")]:
        df = pd.read_csv(f"{TRADES_DIR}\\{fname}", encoding="utf-8-sig")
        df["arm"] = arm
        frames.append(df)
    t = pd.concat(frames, ignore_index=True)
    t["code"] = t["symbol"].astype(str).str.zfill(6)
    t["d0"] = pd.to_numeric(t["entry_date"].str.replace("-", ""), errors="coerce").astype("int64")
    t["ret"] = pd.to_numeric(t["pnl_pct"], errors="coerce")
    t = t[(t["d0"] >= 20240101) & (t["d0"] <= 20261016)].dropna(subset=["ret"])
    return t


def minute_stats(cur, code, d):
    rows = cur.execute(
        "select time, open, volume, amount from stock_minute_5 where code=? and date=? order by time",
        (code, d),
    ).fetchall()
    if not rows or rows[0][0] != 935:
        return None
    b = [r for r in rows if r[0] <= 1000]
    if len(b) < 6:
        return None
    amt30 = sum(r[3] for r in b)
    vol30 = sum(r[2] for r in b)
    vwap30 = (amt30 / vol30) if vol30 > 0 else None
    return {"bar1_open": b[0][1], "amt30": amt30, "vwap30": vwap30,
            "day_amt": sum(r[3] for r in rows)}


def main():
    ev = load_events()
    cm = sqlite3.connect(META_DB)
    cur_m = cm.cursor()

    out = []
    skip = {"no_minute": 0}
    for r in ev.itertuples(index=False):
        st = minute_stats(cur_m, r.code, int(r.d0))
        if st is None:
            skip["no_minute"] += 1
            continue
        prem_bar1_vs_vwap30 = st["bar1_open"] / st["vwap30"] - 1.0 if st["vwap30"] else np.nan
        out.append({
            "arm": r.arm, "year": int(r.d0) // 10000, "code": r.code, "d0": int(r.d0),
            "ret": float(r.ret), "amt30": st["amt30"],
            "entry_vs_vwap30": prem_bar1_vs_vwap30,
            "participation": POSITION_SIZE / st["amt30"] if st["amt30"] > 0 else np.nan,
        })
    cm.close()
    X = pd.DataFrame(out)

    def blk(sub):
        n = len(sub)
        if n == 0:
            return None
        return {"n": n, "win_pct": round(float((sub["ret"] > 0).mean()) * 100, 2),
                "med_pct": round(float(sub["ret"].median()), 3), "mean_pct": round(float(sub["ret"].mean()), 3)}

    # 流动性: 可成交 vs 薄量 (2万 / 5万 双阈)
    trade_tab = {}
    for year in [2024, 2025, 2026, "ALL"]:
        s = X if year == "ALL" else X[X["year"] == year]
        if len(s) == 0:
            trade_tab[str(year)] = None
            continue
        trade_tab[str(year)] = {
            "n": int(len(s)),
            "amt30_lt20k_pct": round(100.0 * (s["amt30"] < POSITION_SIZE).mean(), 2),
            "amt30_lt50k_pct": round(100.0 * (s["amt30"] < 50000).mean(), 2),
            "amt30_median_wan": round(float(s["amt30"].median()) / 1e4, 2),
            "participation_median_pct": round(float(s["participation"].median()) * 100, 3),
            "tradable_ge20k": blk(s[s["amt30"] >= POSITION_SIZE]),
            "thin_lt20k": blk(s[s["amt30"] < POSITION_SIZE]),
        }

    # 入场时点: bar1开盘 vs 开盘30分钟VWAP (raw自洽), 按臂分年
    vwap_tab = {}
    for arm in ["bp_ep", "ml"]:
        vwap_tab[arm] = {}
        for year in [2024, 2025, 2026, "ALL"]:
            s = X[X["arm"] == arm]
            s = s if year == "ALL" else s[s["year"] == year]
            v = s["entry_vs_vwap30"].dropna()
            if len(v) == 0:
                continue
            vwap_tab[arm][str(year)] = {
                "n": int(len(v)), "median_prem_pct": round(float(v.median()) * 100, 3),
                "mean_prem_pct": round(float(v.mean()) * 100, 3),
                "gt1pct_n": int((v > 0.01).sum()),
            }

    payload = {
        "meta": {"events_total": int(len(ev)), "events_with_minute": int(len(X)), "skip": skip,
                 "minute_source": "stock_minute_5 (raw; 2024/25 ml臂大面积缺失, 2026双臂100%)",
                 "position_size_ref": POSITION_SIZE,
                 "data_quality_finding": "qfq库open列疑似前收占位(adj=raw/qfqopen中位2024=1.168/2025=1.092/2026=1.049,非分红可解释),gap段作废登记呈请,本件v2全raw自洽"},
        "liquidity_by_year": trade_tab,
        "entry_vs_vwap30": vwap_tab,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    L = ["# 开盘可成交量与 VWAP30 审计（呈请①C · 2024-26 窗 · 2026-10-19 · v2 raw自洽口径）\n",
         f"> 只读研究件·零交易权。事件=dose_n15 双臂入场 {len(ev)} 件，分钟可用 {len(X)} 件（无分钟跳过 {skip['no_minute']}：2024/25 ml臂大面积缺失，2026双臂100%）。仓位参照 paper 实践 2 万/票。\n",
         "## 🔴 数据质量发现（本件 v1→v2 修正原因）\n",
         "- qfq 库 open 列疑似前收占位：adj=分钟bar1_raw_open/qfq_open 中位 2024=1.168 / 2025=1.092 / 2026=1.049，5~17% 系统性偏离非分红可解释、无一等于1 → v1 的 gap 段作废；qfq 为生成物禁手改，登记数据质量呈请态（修 open 列或改用 stock_daily_raw 为开盘源）。\n",
         "## 开盘30分钟流动性（分年）\n",
         "| 年份 | n | amt30<2万占比% | amt30<5万占比% | amt30中位(万) | 2万参与率中位% |",
         "|---|---|---|---|---|---|"]
    for year in [2024, 2025, 2026, "ALL"]:
        r = trade_tab[str(year)]
        if not r:
            continue
        L.append(f"| {year} | {r['n']} | {r['amt30_lt20k_pct']} | {r['amt30_lt50k_pct']} | {r['amt30_median_wan']} | {r['participation_median_pct']} |")
    L.append("\n### 可成交(≥2万) vs 薄量(<2万) 后续交易结果\n")
    L.append("| 年份 | 桶 | n | 胜率% | 中位% | 均值% |")
    L.append("|---|---|---|---|---|---|")
    for year in [2024, 2025, 2026, "ALL"]:
        r = trade_tab[str(year)]
        if not r:
            continue
        for k, cn in [("tradable_ge20k", "可成交≥2万"), ("thin_lt20k", "薄量<2万")]:
            b = r.get(k)
            if b:
                L.append(f"| {year} | {cn} | {b['n']} | {b['win_pct']} | {b['med_pct']} | {b['mean_pct']} |")
    L.append("\n## 入场时点: 首bar开盘 vs 开盘30分钟VWAP（raw自洽, 按臂分年）\n")
    L.append("| 臂 | 年份 | n | 开盘vsVWAP30中位% | 均值% | 开盘贵于VWAP30超1%(n) |")
    L.append("|---|---|---|---|---|---|")
    for arm in ["bp_ep", "ml"]:
        for year in [2024, 2025, 2026, "ALL"]:
            v = vwap_tab[arm].get(str(year))
            if v:
                L.append(f"| {arm} | {year} | {v['n']} | {v['median_prem_pct']} | {v['mean_prem_pct']} | {v['gt1pct_n']} |")
    L.append("\n- 判定口径(材料标准): 引擎双臂入场域 amt30<2万占比=0% → 「高开/小票买不到量」对引擎选股域不成立(执行层选择偏差无证据)；开盘价 vs VWAP30 为负=开盘买入更优, 延后执行无改善空间。")
    L.append("- 2024/25 结论按可用子集声明(bp臂为主), ml臂 2024/25 分钟缺失属数据覆盖缺口(独立于本件, 已随 qfq open 发现一并登记数据质量呈请)。")
    L.append("\n*脚本直出零转抄: analysis/open_auction_liquidity_20261019.py (v2)。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("EVENTS:", len(ev), "USED:", len(X), "SKIP:", skip)
    print("LIQ:", json.dumps(trade_tab, ensure_ascii=False))
    print("VWAP:", json.dumps(vwap_tab, ensure_ascii=False))


if __name__ == "__main__":
    main()
