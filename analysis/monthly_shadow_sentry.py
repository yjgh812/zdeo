# -*- coding: utf-8 -*-
"""月度影子哨兵：回测↔模拟盘对账（呈请A）+ 组合风险观测（呈请C）
影子观测层，零交易权，零生产接线。
- westock 真值由调用方（主会话/cron 会话）经 MCP 获取后以 CLI 传入，脚本不碰 token；
- 模拟盘窗口收益 = totalProfitRate 快照链式比率（对入金具有鲁棒性，口径声明见报告）；
- 回测窗口收益 = canonical 全史 equity 曲线切片（自动发现最新 backtest_full_*_eod，数据截至日如实标注）；
- 执行差异 = data/a1_slippage_ledger.jsonl 窗口内逐笔滑点统计 vs 引擎 0.03% 假设；
- 风险观测 = bp_ep/ml 窗口日收益相关性 + 组合窗口 VaR95/CVaR95/MDD（含全史对照）。
状态快照: data/monthly_shadow_sentry_state.json
报告: reports/monthly_shadow_sentry_<YYYYMM>.md
fail-closed: 缺参数/缺真值/缺 equity 文件 → exit 2 结构化错误。
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = r"D:/zcodesj"
STATE = os.path.join(ROOT, "data", "monthly_shadow_sentry_state.json")
LEDGER = os.path.join(ROOT, "data", "a1_slippage_ledger.jsonl")
REPORTS = os.path.join(ROOT, "reports")
ENGINE_SLIPPAGE_PP = 0.03  # 引擎固定滑点 0.03%（单边）


def fail(msg):
    print(f"[monthly_shadow_sentry][FAIL-CLOSED] {msg}")
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", required=True, help="本次运行日期 YYYY-MM-DD")
    ap.add_argument("--sim-total-assets", required=True, help="westock MCP totalAssets（真值）")
    ap.add_argument("--sim-profit-rate", required=True, help="westock MCP totalProfitRate（%%）")
    ap.add_argument("--sim-initial-cash", default="", help="westock MCP initialCash（口径记录用）")
    ap.add_argument("--window-start", default="", help="窗口起点 YYYY-MM-DD；缺省=上次快照日")
    ap.add_argument("--window-end", default="", help="窗口终点 YYYY-MM-DD；缺省=run-date")
    args = ap.parse_args()

    try:
        ta_now = float(args.sim_total_assets)
        rate_now = float(args.sim_profit_rate)
    except ValueError:
        fail(f"模拟盘真值参数不可解析: {args.sim_total_assets} / {args.sim_profit_rate}")

    run_date = args.run_date
    win_end = args.window_end or run_date
    win_start = args.window_start

    # ---- 状态快照链 ----
    prev = None
    if os.path.exists(STATE):
        with open(STATE, "r", encoding="utf-8") as f:
            prev = json.load(f)
    if not win_start:
        if prev and prev.get("run_date"):
            win_start = prev["run_date"]
        else:
            fail("无 --window-start 且无上次快照：首期必须显式传入窗口起点")
    if win_start >= win_end:
        fail(f"窗口非法: start={win_start} >= end={win_end}")

    d_start, d_end = pd.Timestamp(win_start), pd.Timestamp(win_end)

    # ---- 回测 equity 自动发现（优先纯日期全史目录，防 win 窗口版排序靠后被误选） ----
    eod_dirs = sorted(glob.glob(os.path.join(REPORTS, "backtest_full_*_eod")))
    if not eod_dirs:
        fail("未找到任何 backtest_full_*_eod 产物目录")
    full_dirs = [d for d in eod_dirs if re.search(r"backtest_full_\d{8}_eod$", os.path.basename(d))]
    eod = (full_dirs or eod_dirs)[-1]
    eod_is_full = bool(full_dirs)

    def load_equity(name):
        p = os.path.join(eod, f"{name}_equity.csv")
        if not os.path.exists(p):
            fail(f"缺 equity 文件: {p}")
        df = pd.read_csv(p, encoding="utf-8-sig")
        val_col = "value" if "value" in df.columns else ("equity" if "equity" in df.columns else None)
        if "date" not in df.columns or val_col is None:
            fail(f"equity 文件列头异常（需 date+value/equity）: {p} 实际={list(df.columns)}")
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").set_index("date")[val_col].astype(float)

    eq_combo = load_equity("portfolio_combo")
    eq_bp = load_equity("bp_ep_core")
    eq_ml = load_equity("ml_signal")
    bt_thru = eq_combo.index.max()

    def win_ret(series):
        s = series[(series.index >= d_start) & (series.index <= d_end)]
        if len(s) < 2:
            return None, 0, None
        return s.iloc[-1] / s.iloc[0] - 1.0, len(s), s.index.max()

    ret_combo, n_combo, eff_end_combo = win_ret(eq_combo)
    ret_bp, n_bp, _ = win_ret(eq_bp)
    ret_ml, n_ml, _ = win_ret(eq_ml)

    # ---- 模拟盘窗口收益（快照链式） ----
    sim_rate_window = None
    injection_flag = ""
    if prev and prev.get("profit_rate") is not None:
        r_prev = float(prev["profit_rate"]) / 100.0
        if (1 + r_prev) == 0:
            fail("上次快照 profit_rate=-100，比率链式不可计算")
        sim_rate_window = (1 + rate_now / 100.0) / (1 + r_prev) - 1.0
        if prev.get("total_assets") and float(prev["total_assets"]) > 0:
            ta_growth = ta_now / float(prev["total_assets"]) - 1.0
            if abs(ta_growth) > 0.20 and abs(sim_rate_window) < 0.05:
                injection_flag = (
                    f"⚠️ 总资产变化 {ta_growth*100:+.1f}% 但盈亏率仅 {sim_rate_window*100:+.2f}%："
                    f"窗口内疑似有入金/出金或基线重算，链式比率口径存疑，对账结论按 N/A 处理"
                )
                sim_rate_window = None
    sim_since_baseline = rate_now / 100.0

    # ---- 滑点账本 ----
    slip_lines = []
    if os.path.exists(LEDGER):
        rows = []
        with open(LEDGER, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        win_rows = [r for r in rows if d_start <= pd.Timestamp(str(r.get("trade_date", ""))) <= d_end]
        if win_rows:
            sp = [float(r.get("slippage_pp", 0) or 0) for r in win_rows]
            abs_sp = [abs(x) for x in sp]
            slip_lines = [
                f"窗口内成交笔数 {len(win_rows)}｜|滑点|均值 {np.mean(abs_sp):.3f}pp｜最大 {max(abs_sp):.3f}pp｜零滑点占比 {(np.array(abs_sp)==0).mean()*100:.1f}%",
                f"口径注记：账本记录=委托价 vs 成交价（执行纪律口径），非相对次日开盘基准；与引擎 0.03% 单边滑点不可直接等比对账，基准差部分记 N/A",
                f"引擎假设 {ENGINE_SLIPPAGE_PP}pp vs 账本实测（委托价口径）均值 {np.mean(abs_sp):.3f}pp",
            ]
        else:
            slip_lines = [f"窗口内无滑点账本记录（账本总行数 {len(rows)}）"]
    else:
        slip_lines = ["滑点账本文件不存在，成本差异分类记 N/A"]

    # ---- 风险观测（呈请C） ----
    risk_lines = []
    rb = eq_bp[(eq_bp.index >= d_start) & (eq_bp.index <= d_end)].pct_change().dropna()
    rm = eq_ml[(eq_ml.index >= d_start) & (eq_ml.index <= d_end)].pct_change().dropna()
    common = rb.index.intersection(rm.index)
    if len(common) >= 20:
        corr_w = rb.loc[common].corr(rm.loc[common])
        risk_lines.append(f"两腿窗口日收益相关性（共同 {len(common)} 日）: {corr_w:+.3f}")
    else:
        risk_lines.append(f"两腿窗口相关性: N/A（共同交易日 {len(common)} < 20）")
    corr_full = eq_bp.pct_change().dropna().corr(eq_ml.pct_change().dropna())
    risk_lines.append(f"两腿全史日收益相关性（对照）: {corr_full:+.3f}")
    rc = eq_combo[(eq_combo.index >= d_start) & (eq_combo.index <= d_end)].pct_change().dropna()
    if len(rc) >= 20:
        var95 = float(np.percentile(rc, 5))
        cvar95 = float(rc[rc <= var95].mean())
        peak = eq_combo[(eq_combo.index >= d_start) & (eq_combo.index <= d_end)].cummax()
        dd = (eq_combo[(eq_combo.index >= d_start) & (eq_combo.index <= d_end)] / peak - 1.0).min()
        rc_full = eq_combo.pct_change().dropna()
        var95_f = float(np.percentile(rc_full, 5))
        cvar95_f = float(rc_full[rc_full <= var95_f].mean())
        peak_f = eq_combo.cummax()
        dd_f = (eq_combo / peak_f - 1.0).min()
        risk_lines += [
            f"组合窗口（{len(rc)} 日）: VaR95={var95*100:.2f}%｜CVaR95={cvar95*100:.2f}%｜窗口内 MDD={dd*100:.2f}%",
            f"组合全史（对照，{len(rc_full)} 日）: VaR95={var95_f*100:.2f}%｜CVaR95={cvar95_f*100:.2f}%｜MDD={dd_f*100:.2f}%",
        ]
    else:
        risk_lines.append(f"组合窗口风险指标: N/A（窗口交易日 {len(rc)} < 20），仅全史对照可用")

    # ---- 组装报告 ----
    label = d_end.strftime("%Y%m")
    out_path = os.path.join(REPORTS, f"monthly_shadow_sentry_{label}.md")
    L = []

    def A(s=""):
        L.append(s)
    A(f"# 月度影子哨兵｜回测↔模拟盘对账 + 组合风险观测（{win_start} ~ {win_end}）")
    A()
    A(f"> 生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}｜脚本 analysis/monthly_shadow_sentry.py 直出（禁人工转抄）｜"
      f"回测产物源：{os.path.basename(eod)}{'（全史版）' if eod_is_full else '（⚠️窗口版，无纯日期全史目录可用）'}｜影子观测层，零交易权")
    A()
    A("## 一、模拟盘侧（westock MCP 真值，由调用方传入）")
    A()
    A(f"| 项 | 值 |")
    A(f"|---|---|")
    A(f"| 本次读数总资产 | {ta_now:,.2f} 元 |")
    A(f"| 本次读数盈亏率（自账户基线） | {rate_now:+.2f}% |")
    A(f"| 基线 initialCash | {args.sim_initial_cash or 'N/A'} |")
    if sim_rate_window is not None:
        A(f"| **窗口收益（快照链式 {win_start}→{win_end}）** | **{sim_rate_window*100:+.2f}%** |")
    else:
        A(f"| 窗口收益（快照链式） | **N/A**（{'首期无上次快照' if not prev else '口径异常旗触发'}） |")
    A(f"| 自基线累计（对照） | {sim_since_baseline*100:+.2f}% |")
    if injection_flag:
        A(f"| 口径旗 | {injection_flag} |")
    A()
    A("## 二、回测侧（canonical 全史 equity 曲线切片）")
    A()
    A(f"回测数据截至 **{bt_thru.strftime('%Y-%m-%d')}**")
    A()
    A("| 曲线 | 窗口收益 | 窗口内样本点 |")
    A("|---|---|---|")
    for nm, r, n in [("组合 50/50（portfolio_combo）", ret_combo, n_combo),
                     ("bp_ep 腿", ret_bp, n_bp),
                     ("ml 腿", ret_ml, n_ml)]:
        A(f"| {nm} | {'N/A（样本<2）' if r is None else f'{r*100:+.2f}%'} | {n} |")
    if bt_thru < d_end:
        A(f"⚠️ 窗口末端 { (d_end - bt_thru).days } 个自然日超出回测数据截至日，上述收益按数据截至日截断计算")
    A()
    A("## 三、差异三分类")
    A()
    A("1. **信号差异**：N/A——模拟盘承载混合验证流程（admit 闸门+R 闸+主人裁决+实验），并非回测主组合的机械执行；"
      "机械口径前向对照属 prereg「主组合前向影子臂」（DRAFT 待批），本哨兵不承担该职责；")
    A(f"2. **执行差异**：{'；'.join(slip_lines)}")
    A("3. **成本差异**：见上条账本实测 vs 引擎假设对比；毛/净差距的完全归因需前向影子臂投运后方可量化")
    A()
    A("## 四、组合风险观测（呈请C）")
    A()
    for ln in risk_lines:
        A(f"- {ln}")
    A()
    A("## 五、口径声明与置信度")
    A()
    A("- westock totalProfitRate 的入金调整语义未文档化：链式比率在无入金窗口下等价于真实 TWR；"
      "疑似入金由总资产/盈亏率背离自动打旗，打旗后窗口对账记 N/A；")
    A("- 回测窗口收益来自全史 equity 曲线切片（canonical 引擎直出产物，只读），非窗口重跑；口径与全史报告一致（qfq+next-bar+PIT+成本内含）；")
    A("- 本哨兵为影子观测层：不下单、不改生产文件、不接 executor；结论仅呈报主人裁决参考。")
    A()
    A("**置信度**：模拟盘读数=westock 实时真值（高）；回测切片=canonical 产物（高）；"
      "窗口差异归因=分类框架内可量化项有限（首期多为 N/A，前向影子臂投运后补全）（中）。")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # ---- 快照落盘（临时文件+替换，防截断） ----
    snap = {
        "run_date": run_date,
        "total_assets": ta_now,
        "profit_rate": rate_now,
        "initial_cash": args.sim_initial_cash,
        "window": [win_start, win_end],
        "report": out_path,
        "eod_source": os.path.basename(eod),
    }
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)

    print(f"[monthly_shadow_sentry][OK] report={out_path}")
    print(f"[monthly_shadow_sentry][OK] state updated: prev={'yes' if prev else 'first-run'}, window=[{win_start},{win_end}]")
    if sim_rate_window is not None:
        print(f"[monthly_shadow_sentry] sim window={sim_rate_window*100:+.2f}% | bt combo={ret_combo*100 if ret_combo is not None else float('nan'):+.2f}%")


if __name__ == "__main__":
    main()
