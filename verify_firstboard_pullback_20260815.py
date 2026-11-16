#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
verify_firstboard_pullback_20260815.py — 首板缩量回调本地验证（华安研报结论复核）
================================================================================
研报: 华安证券2026-03《涨停板背后的Alpha：首板回调策略》
  换手板首板 + T+1缩量回调(量比<0.3x) → 次日平均+7.44% / 胜率91.7%
  放量回调(量比>1.0) → -0.71%；底部首板收益 = 高位首板10倍

本地验证口径（主人2026-08-15任务指定）:
  - 涨停判定: 不复权昨收 × 1.1(主板60/00) / 1.2(创业板30/科创板68) 四舍五入到分，
    对比当日 high/close（收盘涨停=close==涨停价；一字板=open=high=close=涨停价）
  - 首板: T日收盘涨停 且 T-1日非收盘涨停
  - 量比: T+1成交量/T日成交量（vol 全部取主库 stock_daily_hsj，原始口径）
  - 买入: T+2开盘(qfq)  持有1/5/10个交易日: ret_N = qfq_close(T+2+N)/qfq_open(T+2)-1
  - 底部首板: 前20日qfq涨幅<0（宽松）/<-10%（严格）
  - 成本: 滑点双边乘法 (1-s)^2, s∈{0.1%, 0.3%}
  - 数据: qfq=D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db.stock_daily_meta_qfq
          原始价/量=dragon_quant.db.stock_daily_hsj（hsj与qfq均有指数串码污染，
          以 stock_info 非空名称白名单过滤，只保留 60/00/30/68 真实个股）
  - 样本: 2021-01-01 ~ 2026-08-14，剔除ST（现名，历史ST状态不可得，见报告局限）

用法: python verify_firstboard_pullback_20260815.py
输出: 控制台全表 + D:\\zcodesj\\reports\\FIRSTBOARD_PULLBACK_VERIFY_20260815.md
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT = Path(r"D:\zcodesj\reports\FIRSTBOARD_PULLBACK_VERIFY_20260815.md")

DATE_START, DATE_END = 20210101, 20260814   # 事件窗口
DATE_LOAD_FROM = 20201101                   # 预热缓冲(20日回看+T-1)
BOARD_PREFIXES = ("60", "00", "30", "68")
LIMIT_FACTOR = {"30": 1.2, "68": 1.2, "60": 1.1, "00": 1.1}
VR_GROUPS = [("<0.3x", 0.0, 0.3), ("0.3-1.0x", 0.3, 1.0000001), (">1.0x", 1.0000001, np.inf)]
HOLD_NS = [1, 5, 10]
SLIPPAGES = [0.0, 0.001, 0.003]

# 指数串码防御①: stock_info 中带指数特征名的 000xxx（如"上证金融""沪深300"）
INDEX_NAME_PAT = re.compile(r"上证|深证|中证|国证|指数|沪深|成指|CES|全指|央视|百发")


def log(msg: str):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def half_up_cents(prev_close: pd.Series, factor: float) -> pd.Series:
    """交易所涨停价: round(昨收×factor, 2) 四舍五入到分(ROUND_HALF_UP, 向量化近似)"""
    cents = prev_close * factor * 100.0
    base = np.floor(cents)
    frac = cents - base
    return (base + (frac >= 0.5 - 1e-7).astype(float)) / 100.0


def load_data() -> tuple[pd.DataFrame, dict]:
    """返回合并(qfq收益口径 + hsj原始价量)的个股日表, 按code,date排序"""
    conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    info = pd.read_sql_query(
        "SELECT code, name, is_st FROM stock_info WHERE name IS NOT NULL AND name != ''", conn)
    qfq = pd.read_sql_query(
        f"""SELECT code, date, open, close FROM stock_daily_meta_qfq
            WHERE date >= {DATE_LOAD_FROM} AND date <= {DATE_END}
              AND substr(code,1,2) IN ('60','00','30','68')""",
        sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60))
    raw = pd.read_sql_query(
        f"""SELECT code, date, open, high, close, vol FROM stock_daily_hsj
            WHERE date >= {DATE_LOAD_FROM} AND date <= {DATE_END}
              AND substr(code,1,2) IN ('60','00','30','68')""", conn)
    conn.close()

    info["code"] = info["code"].astype(str).str.zfill(6)
    st = set(info.loc[(info["is_st"] == 1) | info["name"].str.upper().str.contains("ST"), "code"])
    named_idx = set(info.loc[info["name"].str.contains(INDEX_NAME_PAT), "code"])
    valid = set(info["code"]) - st - named_idx   # 指数: NULL名剔除 + 命名指数模式剔除
    name_map = dict(zip(info["code"], info["name"]))

    for df in (qfq, raw):
        df["code"] = df["code"].astype(str).str.zfill(6).str[:6]
        df["date"] = df["date"].astype(int)
    qfq = qfq[qfq["code"].isin(valid)].drop_duplicates(["code", "date"], keep="last")
    raw = raw[raw["code"].isin(valid)].drop_duplicates(["code", "date"], keep="last")

    df = qfq.merge(raw, on=["code", "date"], suffixes=("_qfq", ""))
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["high"] >= df["close"]) & (df["vol"] > 0)]
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    meta = {"n_rows": len(df), "n_codes": df["code"].nunique(), "st_excluded": len(st),
            "idx_excluded": len(named_idx), "name_map": name_map}
    return df, meta


def build_events(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", sort=False)
    prev_close = g["close"].shift(1)
    limit_px = pd.Series(np.nan, index=df.index)
    for pfx, f in LIMIT_FACTOR.items():
        m = df["code"].str[:2] == pfx
        limit_px[m] = half_up_cents(prev_close[m], f)

    df["limit_px"] = limit_px
    df["is_limit"] = df["close"] >= df["limit_px"] - 1e-6
    df["is_limit_prev"] = g["is_limit"].shift(1).fillna(False)
    df["is_yizi"] = (df["is_limit"] & (df["open"] >= df["limit_px"] - 1e-6)
                     & (df["high"] <= df["limit_px"] + 1e-6))     # open=high=close=涨停价
    df["first_board"] = df["is_limit"] & ~df["is_limit_prev"]

    # T+k 前视列（按个股行序, 停牌自动跳到下一有行交易日）
    df["vol_next1"] = g["vol"].shift(-1)
    df["vratio"] = df["vol_next1"] / df["vol"]
    df["pullback"] = g["close_qfq"].shift(-1) < df["close_qfq"]    # T+1回调(qfq)
    df["open_t2"] = g["open_qfq"].shift(-2)                        # T+2开盘买入
    for n in HOLD_NS:
        df[f"exit_{n}"] = g["close_qfq"].shift(-(2 + n))           # T+2+N收盘
        df[f"ret_{n}"] = df[f"exit_{n}"] / df["open_t2"] - 1        # 持有N日收益(qfq)
    # 指数串码防御②: 数学不可能界硬帽——T+2开盘→T+2+N收盘共 N+2 个日涨跌幅上限,
    # 超界只可能是混源/复权错误数据(如000038的0.64→4310), 置NaN剔除而非截尾
    fmax = df["code"].str[:2].map({"30": 1.2, "68": 1.2, "60": 1.1, "00": 1.1})
    for n in HOLD_NS:
        cap = fmax ** (n + 3) - 1.0        # n+3: 留开盘跳空极值路径余量
        df.loc[df[f"ret_{n}"].abs() > cap, f"ret_{n}"] = np.nan
    df["ret20"] = df["close_qfq"] / g["close_qfq"].shift(20) - 1   # 前20日涨幅(qfq)

    ev = df[df["first_board"] & ~df["is_yizi"] & (df["date"] >= DATE_START)].copy()
    return ev


def stats_block(ev: pd.DataFrame, hold: int, slip: float = 0.0) -> dict:
    r = ev[f"ret_{hold}"].dropna()
    if slip:
        r = (1 + r) * (1 - slip) ** 2 - 1
    if len(r) == 0:
        return {"n": 0, "mean": np.nan, "median": np.nan, "win": np.nan}
    return {"n": int(len(r)), "mean": float(r.mean()), "median": float(r.median()),
            "win": float((r > 0).mean())}


def main() -> int:
    t0 = time.time()
    log("=" * 70)
    log("首板缩量回调本地验证（华安研报复核） 2021-01-01 ~ 2026-08-14")
    log("=" * 70)

    df, meta = load_data()
    log(f"个股日表: {meta['n_rows']:,}行 × {meta['n_codes']}股（ST剔除{meta['st_excluded']}, "
        f"命名指数剔除{meta['idx_excluded']}, NULL名指数已剔除; 收益超不可能界置NaN）")

    ev = build_events(df)
    n_limit = int(df[(df["date"] >= DATE_START)]["is_limit"].sum())
    n_first = int(df[(df["date"] >= DATE_START)]["first_board"].sum())
    n_yizi = int(df[(df["date"] >= DATE_START) & df["first_board"]]["is_yizi"].sum())
    ev_v = ev.dropna(subset=["vratio"])
    log(f"涨停日 {n_limit:,} | 首板 {n_first:,} | 其中一字板 {n_yizi:,} | "
        f"换手板首板 {len(ev):,} | 有量比 {len(ev_v):,}")

    lines: list[str] = []
    A = lines.append
    A(f"# 首板缩量回调本地验证报告（华安研报复核）")
    A("")
    A(f"- 生成: {datetime.now().isoformat(timespec='seconds')} | 脚本: "
      f"`D:\\zcodesj\\verify_firstboard_pullback_20260815.py`（可复跑）")
    A(f"- 事件窗: 2021-01-01 ~ 2026-08-14 | 个股日表 {meta['n_rows']:,}行 × "
      f"{meta['n_codes']}股（60/00/30/68，剔除ST与指数串码）")
    A(f"- 样本: 涨停日{n_limit:,} → 首板{n_first:,}（一字板{n_yizi:,}剔除）→ "
      f"换手板首板{len(ev):,}（可算量比{len(ev_v):,}）")
    A("- 口径: 涨停=不复权昨收×1.1/1.2四舍五入到分; 量比=T+1量/T量(hsj原始vol); "
      "T+2开盘(qfq)买入; 收益全用qfq")
    A("")

    # ── 主表: 量比分组 × 持有期 ──
    A("## 1. 量比分组 × 持有期（毛收益）")
    A("")
    A("| 量比组 | 持有1日 N/均值/中位/胜率 | 持有5日 | 持有10日 |")
    A("|:--|:--|:--|:--|")
    vr_stats = {}
    for gname, lo, hi in VR_GROUPS:
        sub = ev_v[(ev_v["vratio"] >= lo) & (ev_v["vratio"] < hi)]
        cells = []
        for n in HOLD_NS:
            s = stats_block(sub, n)
            vr_stats[(gname, n)] = s
            cells.append(f"{s['n']} / {s['mean']*100:+.2f}% / {s['median']*100:+.2f}% / "
                         f"{s['win']*100:.1f}%")
        A(f"| {gname} | " + " | ".join(cells) + " |")
    A("")

    # ── 成本敏感性（持有1日为主） ──
    A("## 2. 成本敏感性（滑点双边乘法）")
    A("")
    A("| 量比组 | 毛收益 | 滑点0.1% | 滑点0.3% |")
    A("|:--|:--|:--|:--|")
    for gname, lo, hi in VR_GROUPS:
        sub = ev_v[(ev_v["vratio"] >= lo) & (ev_v["vratio"] < hi)]
        cells = []
        for s in SLIPPAGES:
            st_ = stats_block(sub, 1, s)
            cells.append(f"{st_['mean']*100:+.2f}% (胜率{st_['win']*100:.1f}%)")
        A(f"| {gname}·持有1日 | " + " | ".join(cells) + " |")
    A("")

    # ── 回调条件（研报语义含"回调"，加价格回调过滤稳健性） ──
    A('## 3. 加「T+1价格回调」过滤（T+1收盘<T收盘, qfq）稳健性')
    A("")
    A("| 量比组 | 持有1日 N/均值/胜率 | 持有5日 | 持有10日 |")
    A("|:--|:--|:--|:--|")
    for gname, lo, hi in VR_GROUPS:
        sub = ev_v[(ev_v["vratio"] >= lo) & (ev_v["vratio"] < hi) & ev_v["pullback"]]
        cells = []
        for n in HOLD_NS:
            s = stats_block(sub, n)
            cells.append(f"{s['n']} / {s['mean']*100:+.2f}% / {s['win']*100:.1f}%")
        A(f"| {gname} | " + " | ".join(cells) + " |")
    A("")

    # ── 底部 vs 高位 ──
    A("## 4. 底部首板 vs 高位首板（前20日qfq涨幅）")
    A("")
    A("| 分组 | 定义 | N | 持有1日均值 | 胜率 | 持有5日均值 | 持有10日均值 |")
    A("|:--|:--|--:|:--|:--|:--|:--|")
    for label, desc, cond in [
        ("底部(宽松)", "前20日涨幅<0", ev_v["ret20"] < 0),
        ("高位(对照A)", "前20日涨幅≥0", ev_v["ret20"] >= 0),
        ("底部(严格)", "前20日涨幅<-10%", ev_v["ret20"] < -0.10),
        ("高位(对照B)", "前20日涨幅≥-10%", ev_v["ret20"] >= -0.10),
    ]:
        sub = ev_v[cond]
        s1, s5, s10 = (stats_block(sub, n) for n in HOLD_NS)
        A(f"| {label} | {desc} | {s1['n']} | {s1['mean']*100:+.2f}% | {s1['win']*100:.1f}% | "
          f"{s5['mean']*100:+.2f}% | {s10['mean']*100:+.2f}% |")
    A("")
    A("注: 对照A与底部(宽松)配对、对照B与底部(严格)配对。")
    A("")

    # 底部×缩量交叉
    A("## 5. 底部 × 缩量交叉（底部首板且量比<0.3x）")
    A("")
    A("| 分组 | N | 持有1日均值 | 胜率 | 持有5日均值 | 持有10日均值 |")
    A("|:--|--:|:--|:--|:--|:--|")
    for label, cond in [
        ("底部(20日<0)·量比<0.3x", (ev_v["ret20"] < 0) & (ev_v["vratio"] < 0.3)),
        ("高位(20日≥0)·量比<0.3x", (ev_v["ret20"] >= 0) & (ev_v["vratio"] < 0.3)),
        ("底部(20日<-10%)·量比<0.3x", (ev_v["ret20"] < -0.10) & (ev_v["vratio"] < 0.3)),
    ]:
        sub = ev_v[cond]
        s1, s5, s10 = (stats_block(sub, n) for n in HOLD_NS)
        A(f"| {label} | {s1['n']} | {s1['mean']*100:+.2f}% | {s1['win']*100:.1f}% | "
          f"{s5['mean']*100:+.2f}% | {s10['mean']*100:+.2f}% |")
    A("")

    # ── 年度分布（量比<0.3组, 持有1日） ──
    A("## 6. 年度分布（量比<0.3x · 持有1日, 毛收益）")
    A("")
    A("| 年份 | N | 均值 | 中位 | 胜率 |")
    A("|:--|--:|:--|:--|:--|")
    sub = ev_v[ev_v["vratio"] < 0.3].copy()
    sub["year"] = (sub["date"] // 10000).astype(int)
    for y, gdf in sub.groupby("year"):
        s = stats_block(gdf, 1)
        A(f"| {y} | {s['n']} | {s['mean']*100:+.2f}% | {s['median']*100:+.2f}% | "
          f"{s['win']*100:.1f}% |")
    A("")

    # ── 底部(严格)首板年度分解（本地唯一有实义的信号, 供模拟盘建议） ──
    A("## 6b. 底部严格首板（前20日<-10%）年度分解（不限量比, 毛收益）")
    A("")
    A("| 年份 | N | 1日均值/胜率 | 5日均值/胜率 | 10日均值/胜率 |")
    A("|:--|--:|:--|:--|:--|")
    subb = ev_v[ev_v["ret20"] < -0.10].copy()
    subb["year"] = (subb["date"] // 10000).astype(int)
    for y, gdf in subb.groupby("year"):
        c1, c5, c10 = (stats_block(gdf, n) for n in HOLD_NS)
        A(f"| {y} | {c1['n']} | {c1['mean']*100:+.2f}% / {c1['win']*100:.0f}% | "
          f"{c5['mean']*100:+.2f}% / {c5['win']*100:.0f}% | {c10['mean']*100:+.2f}% / "
          f"{c10['win']*100:.0f}% |")
    A("")

    # ── 与研报对照 ──
    A("## 7. 与华安研报数字对照")
    A("")
    A("| 指标 | 研报 | 本地(2021-2026) | 复现? |")
    A("|:--|:--|:--|:--|")
    s = vr_stats[("<0.3x", 1)]
    A(f"| 缩量<0.3x 次日均值 | +7.44% | {s['mean']*100:+.2f}% | "
      f"{'是' if s['mean'] > 0.05 else '否'} |")
    A(f"| 缩量<0.3x 胜率 | 91.7% | {s['win']*100:.1f}% | "
      f"{'是' if s['win'] > 0.80 else '否'} |")
    s_hi = vr_stats[(">1.0x", 1)]
    A(f"| 放量>1.0x 均值 | -0.71% | {s_hi['mean']*100:+.2f}% | "
      f"{'是' if s_hi['mean'] < 0 else '否'} |")
    bot = stats_block(ev_v[ev_v["ret20"] < 0], 1)
    hi_ = stats_block(ev_v[ev_v["ret20"] >= 0], 1)
    ratio = bot["mean"] / hi_["mean"] if hi_["mean"] and hi_["mean"] != 0 else np.nan
    A(f"| 底部/高位 首板倍数 | ~10倍 | {ratio:.1f}倍 "
      f"(底部{bot['mean']*100:+.2f}% vs 高位{hi_['mean']*100:+.2f}%) | "
      f"{'是' if ratio and ratio > 5 else '否'} |")
    A("")

    # ── 近期样本（铁律: 代码带名称） ──
    nm = meta["name_map"]
    A("## 8. 近期样本抽查（量比<0.3x 换手板首板, 最近15例, 名称经stock_info）")
    A("")
    samp = (ev_v[ev_v["vratio"] < 0.3].sort_values("date").tail(15))
    A("| 日期 | 代码 | 名称 | 涨停价 | T+1量比 | 持有1日 | 持有5日 |")
    A("|:--|:--|:--|--:|--:|--:|--:|")
    for _, r in samp.iterrows():
        r1 = r["ret_1"] * 100 if pd.notna(r["ret_1"]) else np.nan
        r5 = r["ret_5"] * 100 if pd.notna(r["ret_5"]) else np.nan
        A(f"| {int(r['date'])} | {r['code']} | {nm.get(r['code'], '?')} | "
          f"{r['limit_px']:.2f} | {r['vratio']:.2f} | "
          f"{('%+.2f%%' % r1) if pd.notna(r1) else '-'} | "
          f"{('%+.2f%%' % r5) if pd.notna(r5) else '-'} |")
    A("")

    # ── 判定与建议 ──
    A("## 9. 判定与建议")
    A("")
    A("**判定: 本地数据（2021-01~2026-08, 58,648例换手板首板）不复现研报核心结论。**")
    A("")
    A("1. 缩量<0.3x 组 T+2买入持有1日: 均值 **+0.58% / 胜率53.3%**（研报 +7.44% / 91.7%）, "
      "数量级差距; 扣0.3%滑点后均值≈0（-0.03%）, 5日/10日显著为负（-2.1%/-2.6%, 胜率34-36%）。")
    A("2. 加「T+1价格回调」过滤后反而更差（-1.80% / 34.9%）, 与研报语义方向相反。")
    A("3. 放量>1.0x 组 +0.24%（研报 -0.71%）, 方向不符但两者都接近零。")
    A("4. **唯一方向性复现: 底部首板 ≈ 高位首板 9.7倍**（底部+0.80% vs 高位+0.08%）; "
      "底部严格（20日<-10%）1日+1.58%/胜率58.3%、10日+4.30%。但年度分解显示强市依赖: "
      "2024年（924行情, N=1387）10日+8.58%/80%贡献大头, 2023/2025年趋近于零, "
      "2026年至8月已转负（10日-0.19%）。")
    A("5. 底部×缩量交叉样本过小（32-56例）, 不具统计意义。")
    A("")
    A("**建议: 不进模拟盘观察清单（维持P3纸面即可）。** 理由: 研报headline数字不可复现; "
      "本地可见的最优切片（底部严格首板）胜率58%但年度不稳定且2026年失效, "
      "58,648例的全样本均值在扣成本后无正期望。若主人仍要保留观察点, 仅建议以**纸面记录**"
      "「底部严格首板（前20日<-10%）」10日持有口径（不发单、不占组合）, 观察满一个季度再议。")
    A("")
    A("### 局限（如实）")
    A("")
    A("- ST按当前名称剔除, 历史ST状态不可得（2021-2026间戴帽/摘帽未动态处理）。")
    A("- 研报口径未知（样本期或为2013-2020打板生态/是否含ST北交/量比是否用成交额）, "
      "数字对照仅按主人任务口径执行。")
    A("- T+2开盘买入未过滤一字开盘（开盘即涨停无法成交的样本仍计入, 高估可达性）。")
    A("- 收益超板块涨跌幅不可能界（1.1/1.2^(N+3)）的混源数据已置NaN剔除（含指数串码如000038）, "
      "正常真实极端行情（如九安医疗+117%/10日）保留。")
    A("- qfq收益含停牌跳空; 量比基于hsj原始vol, 单位一致性依赖源库。")
    A("")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    log(f"报告落盘: {REPORT}  (耗时 {time.time()-t0:.0f}s)")

    # 控制台摘要
    print("\n===== 摘要（量比分组 × 持有期, 毛收益） =====")
    for gname, _, _ in VR_GROUPS:
        row = [f"{gname}: "]
        for n in HOLD_NS:
            s = vr_stats[(gname, n)]
            row.append(f"{n}日[{s['n']}]{s['mean']*100:+.2f}%/胜率{s['win']*100:.0f}%")
        print("  " + "  ".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
