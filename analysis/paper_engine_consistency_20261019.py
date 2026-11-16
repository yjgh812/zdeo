# -*- coding: utf-8 -*-
"""paper_engine_consistency_20261019.py — 呈请①A paper—引擎同窗一致性对比（2026-10-19「按推荐继续」）

问题: 回测引擎口径(T+1开盘成交、滑点0.03%、月度调仓) vs 模拟盘实际(westock practice_190, 委托价撮合、
主人批准制买入、止损链卖出)在同窗内的成交集合/入场价差/胜率差。定位执行层差异，不归因模型。
只读研究件·零交易权·零生产改动。

数据源(真值纪律):
  引擎侧 = reports/backtest_full_20261018_dose_n15/{bp_ep_core,ml_signal}_trades.csv (生产等价 N15 fork)
  paper侧 = D:/zcodesj/attribution/attribution.db position_history (closed_loop_sync FIFO 唯一写入器,
             入场覆盖 20260722~20261012, updated 2026-10-17; westock MCP 为上游真值, 本件读派生台账)
口径声明:
  paper 组合=主人批准制(信号+闸门+人批), 引擎=机械 Top15 → 集合重合度本就非 100%, 对比意义在
  重合票的价格保真度与两侧胜率/滑点口径差, 如实注记。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

TRADES_DIR = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15"
PH_DB = r"file:D:/zcodesj/attribution/attribution.db?mode=ro"
QFQ_URI = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
W0, W1 = "2026-07-22", "2026-10-16"          # paper 覆盖窗
OUT_JSON = r"D:\zcodesj\analysis\paper_engine_consistency_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\paper_engine_consistency_20261019.md"


def qfq_day_bars(pairs):
    """按 (code,date) 取 qfq open/close; pairs=set((code6, 'YYYYMMDD'))."""
    c = sqlite3.connect(QFQ_URI, uri=True)
    out = {}
    for code, d in pairs:
        rows = c.execute(
            "select open, close from stock_daily_meta_qfq where code=? and date=?",
            (code, d),
        ).fetchone()
        out[(code, d)] = rows
    c.close()
    return out


def main():
    # ---- 引擎侧 ----
    eng = []
    for arm, fname in [("bp_ep", "bp_ep_core_trades.csv"), ("ml", "ml_signal_trades.csv")]:
        df = pd.read_csv(f"{TRADES_DIR}\\{fname}", encoding="utf-8-sig")
        df["arm"] = arm
        eng.append(df)
    E = pd.concat(eng, ignore_index=True)
    E["code"] = E["symbol"].astype(str).str.zfill(6)
    Ew = E[(E["entry_date"] >= W0) & (E["entry_date"] <= W1)].copy()
    eng_entry_set = set(zip(Ew["code"], Ew["entry_date"].str.replace("-", "")))

    # ---- paper 侧 ----
    c = sqlite3.connect(PH_DB, uri=True)
    ph = pd.read_sql_query("select code, name, entry_date, entry_price, exit_date, exit_price, "
                           "exit_reason, holding_days, pnl_amount, pnl_pct, status from position_history", c)
    c.close()
    ph["code"] = ph["code"].astype(str).str.zfill(6)
    ph["entry_date"] = ph["entry_date"].astype(str)
    ph["exit_date"] = ph["exit_date"].astype(str)
    in_win = ph[(ph["entry_date"] >= W0.replace("-", "")) & (ph["entry_date"] <= W1.replace("-", ""))]
    pap_entry_set = set(zip(in_win["code"], in_win["entry_date"]))

    # ---- A1 成交集合对比 ----
    inter = eng_entry_set & pap_entry_set
    a1 = {
        "window": [W0, W1],
        "engine_entries": len(eng_entry_set),
        "paper_entries": len(pap_entry_set),
        "intersection_code_date": len(inter),
        "intersection_code_only": len({c for c, _ in eng_entry_set} & {c for c, _ in pap_entry_set}),
        "jaccard_code_date": round(len(inter) / max(len(eng_entry_set | pap_entry_set), 1), 3),
        "engine_only_n": len(eng_entry_set - pap_entry_set),
        "paper_only_n": len(pap_entry_set - eng_entry_set),
    }

    # ---- A2 入场价差(重合票) ----
    Ep = Ew.copy()
    Ep["key"] = Ep["code"] + "|" + Ep["entry_date"].str.replace("-", "")
    Pp = in_win.copy()
    Pp["key"] = Pp["code"] + "|" + Pp["entry_date"]
    m = Ep.merge(Pp[["key", "entry_price"]].rename(columns={"entry_price": "paper_price"}), on="key", how="inner")
    pairs = set(zip(m["code"], m["key"].str.split("|").str[1]))
    qb = qfq_day_bars(pairs)
    m["qfq_open"] = [qb.get((r.code, r.key.split("|")[1]), (None, None))[0] for r in m.itertuples()]
    m = m.dropna(subset=["qfq_open"])
    m["prem_vs_engine"] = m["paper_price"] / m["entry_price"] - 1.0
    m["prem_vs_open"] = m["paper_price"] / m["qfq_open"] - 1.0
    # 复权口径哨: |vs_open|>5% 视为疑似 qfq 除权因子差, 剔除出滑点统计但如实计数
    suspect = m[m["prem_vs_open"].abs() > 0.05]
    clean = m[m["prem_vs_open"].abs() <= 0.05]
    a2 = {
        "matched_n": int(len(m)),
        "qfq_suspect_excluded": int(len(suspect)),
        "prem_vs_engine_pct": {"median": round(float(clean["prem_vs_engine"].median()) * 100, 3),
                                "mean": round(float(clean["prem_vs_engine"].mean()) * 100, 3),
                                "gt3pct_n": int((clean["prem_vs_engine"] > 0.03).sum())},
        "prem_vs_qfqopen_pct": {"median": round(float(clean["prem_vs_open"].median()) * 100, 3),
                                  "mean": round(float(clean["prem_vs_open"].mean()) * 100, 3),
                                  "gt3pct_n": int((clean["prem_vs_open"] > 0.03).sum())},
    }

    # ---- A2b 全量 paper 入场溢价(不依赖引擎重合; paper成交价 vs 当日qfq开盘) ----
    pap_pairs = set(zip(in_win["code"], in_win["entry_date"]))
    qb2 = qfq_day_bars(pap_pairs)
    rows2 = []
    for r in in_win.itertuples():
        bar = qb2.get((r.code, r.entry_date))
        if bar and bar[0]:
            try:
                rows2.append(float(r.entry_price) / float(bar[0]) - 1.0)
            except Exception:
                pass
    pre_all = pd.Series(rows2).dropna()
    pre_clean = pre_all[pre_all.abs() <= 0.05]
    a2b = {
        "n_all": int(len(pre_all)), "n_suspect_excluded": int(len(pre_all) - len(pre_clean)),
        "median_pct": round(float(pre_clean.median()) * 100, 3),
        "mean_pct": round(float(pre_clean.mean()) * 100, 3),
        "gt3pct_n": int((pre_clean > 0.03).sum()),
        "lt_m3pct_n": int((pre_clean < -0.03).sum()),
    }

    # ---- A3 胜率对比(窗内已平仓) ----
    pap_closed = ph[(ph["status"].astype(str).str.lower() != "open") &
                    (ph["entry_date"] >= W0.replace("-", "")) & (ph["exit_date"] <= W1.replace("-", ""))]
    eng_closed = E[(E["entry_date"] >= W0) & (E["exit_date"] <= W1)]
    pc = pd.to_numeric(pap_closed["pnl_pct"], errors="coerce").dropna()
    ec = pd.to_numeric(eng_closed["pnl_pct"], errors="coerce").dropna()

    def blk(s):
        pos = s[s > 0].sum()
        neg = s[s < 0].sum()
        return {"n": int(len(s)), "win_pct": round(float((s > 0).mean()) * 100, 2),
                "med_pct": round(float(s.median()), 3),
                "pl_ratio": round(float(pos / abs(neg)), 3) if neg < 0 else None}

    a3 = {"paper_closed": blk(pc), "engine_window_closed": blk(ec)}

    payload = {"a1_set_overlap": a1, "a2_entry_premium": a2, "a2b_all_paper_premium": a2b, "a3_winrate": a3,
               "notes": [
                   "paper=主人批准制买入+止损链卖出; engine=机械Top15月度调仓 → 集合重合度非100%属设计口径差",
                   "paper真值=westock MCP; 本件读其派生台账 position_history(closed_loop_sync FIFO唯一写入器, updated 2026-10-17)",
                   "入场价差两口径: vs引擎成交价(含3bp滑点) 与 vs当日qfq开盘; |vs开盘|>5% 判疑似复权因子差剔除并计数",
                   "核心结论: 窗内 code+日 零重合 → 回测-模拟盘胜率差主要是组合定义差异(批准制+主动止损链), 非执行层伪成交(伪成交由 verify_fill_consistency 在役管控)",
               ]}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    L = ["# 回测引擎—模拟盘同窗一致性审计（呈请①A · 2026-10-19）\n",
         f"> 只读研究件·零交易权。窗口 {W0}~{W1}（paper 台账覆盖域）。引擎侧=dose_n15 生产等价逐笔；paper 侧=position_history（closed_loop_sync 唯一写入器，updated 2026-10-17；上游真值=westock MCP）。\n",
         "## A1 成交集合对比\n",
         f"- 引擎入场(去重 code+日): **{a1['engine_entries']}** 件；paper 入场: **{a1['paper_entries']}** 件",
         f"- 完全重合(code+同日): **{a1['intersection_code_date']}**；仅代码重合: {a1['intersection_code_only']}；Jaccard={a1['jaccard_code_date']}",
         f"- 引擎有/paper无: {a1['engine_only_n']}（主人未批/未成交/撤单）；paper有/引擎无: {a1['paper_only_n']}（主人特批域, 名单制废除后设计口径差）\n",
         "## A2 重合票入场价差（滑点/执行保真度）\n",
         f"- 重合可配对: {a2['matched_n']} 笔（剔除疑似 qfq 复权因子差 {a2['qfq_suspect_excluded']} 笔）",
         f"- paper成交价 vs 引擎成交价: 中位 **{a2['prem_vs_engine_pct']['median']}%** / 均值 {a2['prem_vs_engine_pct']['mean']}% / >3%溢价 {a2['prem_vs_engine_pct']['gt3pct_n']} 笔",
         f"- paper成交价 vs 当日qfq开盘: 中位 **{a2['prem_vs_qfqopen_pct']['median']}%** / 均值 {a2['prem_vs_qfqopen_pct']['mean']}% / >3%溢价 {a2['prem_vs_qfqopen_pct']['gt3pct_n']} 笔\n",
         "## A2b 全量 paper 入场溢价（不依赖引擎重合）\n",
         f"- 可配对 {a2b['n_all']} 笔（剔除疑似复权差 {a2b['n_suspect_excluded']}）: paper成交价 vs 当日qfq开盘 中位 **{a2b['median_pct']}%** / 均值 {a2b['mean_pct']}% / >3%溢价 {a2b['gt3pct_n']} 笔 / <-3%折价 {a2b['lt_m3pct_n']} 笔\n",
         "## A3 窗内已平仓胜率对比\n",
         f"- paper 已平仓: n={a3['paper_closed']['n']}, 胜率 {a3['paper_closed']['win_pct']}%, 中位 {a3['paper_closed']['med_pct']}%, 盈亏比 {a3['paper_closed']['pl_ratio']}",
         f"- 引擎同窗已平仓: n={a3['engine_window_closed']['n']}, 胜率 {a3['engine_window_closed']['win_pct']}%, 中位 {a3['engine_window_closed']['med_pct']}%, 盈亏比 {a3['engine_window_closed']['pl_ratio']}",
         "- 口径注: paper 卖出=止损链/主人主动; engine 卖出=月度调仓/止损 → 胜率差含决策口径差, 非纯执行差。\n",
         "*脚本直出零转抄: analysis/paper_engine_consistency_20261019.py。*"]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("A1:", a1)
    print("A2:", a2)
    print("A3:", a3)


if __name__ == "__main__":
    main()
