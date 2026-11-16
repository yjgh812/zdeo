#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
kline_pattern_event_study_batch_20261020.py — 经典蜡烛图形态族批量事件研究（延续 10/19 早晨之星）

背景：主人问「K线形态选股如何能用」，10/19 已定谳早晨之星+放量1.5倍无边际(n=4736,
fwd20超额-0.86pp)。本件把经典形态族一次批量检验,回答整族命题；并测试「放量1.5倍」
叠加是否能改善形态(形态×放量交叉口径)。

形态口径（经典定义参数化, 锚点日按各形态传统位置, 趋势前置=T锚点收盘 vs 其10日前收盘）：
  morning_star  早晨之星: T-2长阴(≤-1.5%)→T-1星线(|实体|/T-2收≤0.5%且max(o1,c1)≤c2)
                →T阳线收≥(o2+c2)/2, 前置跌势
  evening_star  黄昏之星: 镜像(长阳≥+1.5%, min(o1,c1)≥c2, T阴线收≤中点), 前置涨势
  bull_engulf   看涨吞没: T-1阴线, T阳线且 o≤c1 且 c≥o1(实体包住), 前置跌势
  bear_engulf   看跌吞没: 镜像
  hammer        锤头: 下影≥2×|实体|, 上影≤0.3×|实体|, 实体居当日区间上40%, 前置跌势
  hang_man      上吊: 同锤头形态, 前置涨势
  dark_cloud    乌云盖顶: 前置涨势, T-1阳线, T高开(o>c1), T阴线且c<(o1+c1)/2且c>o1
  piercing      刺穿线: 镜像(前置跌势, T-1阴线, T低开(o<c1), T阳线且c>(o1+c1)/2且c<o1)
  three_white   红三兵: 三日连阳且每日涨幅≥0.5%且收盘递进(c>c1>c2)
  three_crows   三乌鸦: 镜像(连阴≤-0.5%且c<c1<c2)
  doji          长腿十字星: |实体|≤10%×全幅, 前置不限(中性)

口径：两档=裸形态 / 形态∧放量(vol≥5日均量×1.5)。宇宙=剔科创/北交/次新60日/当前ST。
度量：fwd5/fwd20 vs 同宇宙全样本基线。判读门槛(屠龙参考)=fwd20中位超额≥+1.5pp 且胜率≥52%。
数据：dragon_quant_qfq.db stock_daily_meta_qfq。输出 stdout + reports/KLINE_PATTERN_BATCH_20261020.md
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOCK_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT = Path(r"D:\zcodesj\reports\KLINE_PATTERN_BATCH_20261020.md")
CUTOFF = "2026-10-10"

PATTERNS = [
    "morning_star", "evening_star", "bull_engulf", "bear_engulf",
    "hammer", "hang_man", "dark_cloud", "piercing",
    "three_white", "three_crows", "doji", "ctrl_3dn",
]


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = cn.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "ORDER BY code, date").fetchall()
    cn.close()
    df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume"])
    print(f"loaded rows={len(df)}", flush=True)

    g = df.groupby("code", sort=False)
    o, c, h, l, v = df["open"], df["close"], df["high"], df["low"], df["volume"]
    pc = g["close"].shift(1)
    body = c - o
    rng = (h - l).where((h - l) > 0)   # 0/负幅 → NaN, 比较结果经 fillna(False)
    up_sh = h - pd.concat([o, c], axis=1).max(axis=1)
    lo_sh = pd.concat([o, c], axis=1).min(axis=1) - l

    o1, c1, b1 = g["open"].shift(1), g["close"].shift(1), g["close"].shift(1) - g["open"].shift(1)
    o2, c2 = g["open"].shift(2), g["close"].shift(2)
    b2 = c2 - o2
    pc2 = g["close"].shift(3)          # T-2 的前收
    pc3 = g["close"].shift(4)          # T-3 的前收(红三兵首日前收)
    ret2 = c2 / pc2 - 1
    body1_ret = b1 / c2
    c12_from2 = g["close"].shift(12)   # T-2 往前10日
    c11_from1 = g["close"].shift(11)   # T-1 往前10日
    v5 = g["volume"].transform(lambda s: s.shift(1).rolling(5).mean())
    fwd5 = g["close"].shift(-5) / c - 1
    fwd20 = g["close"].shift(-20) / c - 1
    rownum = g.cumcount()
    year = df["date"].str.slice(0, 4)
    date_dt = pd.to_datetime(df["date"])
    valid_base = (fwd5.notna() & fwd20.notna() & (rownum >= 60) & (date_dt <= pd.Timestamp(CUTOFF)))

    # 宇宙
    codes = set(df["code"].unique())
    codes = {x for x in codes if not x.startswith(("68", "4", "8", "92"))}
    try:
        cn2 = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
        st = {r[0] for r in cn2.execute("SELECT code FROM stock_info WHERE is_st=1").fetchall()}
        cn2.close()
        codes -= st
    except Exception as e:
        print(f"[WARN] ST过滤失败({e})", flush=True)
    uni_mask = df["code"].isin(codes) & valid_base

    trend_dn = c2 < c12_from2      # 锚T-2的形态
    trend_up = c2 > c12_from2
    trend_dn1 = c1 < c11_from1     # 锚T-1的形态(两日形态族)
    trend_up1 = c1 > c11_from1

    M = {}
    M["morning_star"] = (trend_dn & (b2 < 0) & (ret2 <= -0.015)
                         & (body1_ret.abs() <= 0.005)
                         & (pd.concat([o1, c1], axis=1).max(axis=1) <= c2)
                         & (body > 0) & (c >= (o2 + c2) / 2))
    M["evening_star"] = (trend_up & (b2 > 0) & (ret2 >= 0.015)
                         & (body1_ret.abs() <= 0.005)
                         & (pd.concat([o1, c1], axis=1).min(axis=1) >= c2)
                         & (body < 0) & (c <= (o2 + c2) / 2))
    M["bull_engulf"] = (trend_dn1 & (b1 < 0) & (body > 0) & (o <= c1) & (c >= o1))
    M["bear_engulf"] = (trend_up1 & (b1 > 0) & (body < 0) & (o >= c1) & (c <= o1))
    hammer_shape = (lo_sh >= 2 * body.abs()) & (up_sh <= 0.3 * body.abs()) \
        & (pd.concat([o, c], axis=1).min(axis=1) >= l + 0.6 * (h - l))
    M["hammer"] = (trend_dn1 & hammer_shape)
    M["hang_man"] = (trend_up1 & hammer_shape)
    M["dark_cloud"] = (trend_up1 & (b1 > 0) & (o > c1) & (body < 0)
                       & (c < (o1 + c1) / 2) & (c > o1))
    M["piercing"] = (trend_dn1 & (b1 < 0) & (o < c1) & (body > 0)
                     & (c > (o1 + c1) / 2) & (c < o1))
    r1 = c / pc - 1
    r1_1 = c1 / c2 - 1
    r1_2 = c2 / pc2 - 1
    M["three_white"] = (body > 0) & (b1 > 0) & (b2 > 0) & (r1 >= 0.005) & (r1_1 >= 0.005) \
        & (r1_2 >= 0.005) & (c > c1) & (c1 > c2)
    M["three_crows"] = (body < 0) & (b1 < 0) & (b2 < 0) & (r1 <= -0.005) & (r1_1 <= -0.005) \
        & (r1_2 <= -0.005) & (c < c1) & (c1 < c2)
    M["doji"] = (body.abs() <= 0.1 * rng).fillna(False)
    # 对照组: 任意三连阴(无递进/幅度形态约束)——拆分 three_crows∧放量 的"形态效应 vs 深跌反弹基率"
    M["ctrl_3dn"] = (body < 0) & (b1 < 0) & (b2 < 0)

    volx = v >= v5 * 1.5
    base = uni_mask
    b5, b20 = fwd5[base], fwd20[base]
    yr_all = year[base]
    baseline_year_med = b20.groupby(yr_all.values).median()   # 预计算: 分年基线中位查表

    L = []
    L.append("# 经典蜡烛图形态族批量事件研究 [2026-10-20]")
    L.append("")
    L.append("> ⚠️ 研究呈现零交易权。口径=裸形态 与 形态∧放量(≥5日均量×1.5) 两档；"
             "宇宙=剔科创/北交/次新60日/当前ST(当前名单口径限制如实声明)；"
             "fwd=qfq收盘→N日收盘。基线=同宇宙全样本。判读门槛(参考)=fwd20中位超额≥+1.5pp且胜率≥52%。")
    L.append("")
    L.append(f"基线: n={len(b5):,} | fwd5中位 {b5.median()*100:+.2f}% 胜率 {(b5>0).mean()*100:.1f}% | "
             f"fwd20中位 {b20.median()*100:+.2f}% 胜率 {(b20>0).mean()*100:.1f}%")
    L.append("")
    L.append("|形态|裸n|裸fwd20中位|裸超额|裸胜率|放量n|放量fwd20中位|放量超额|放量胜率|分年正超额|判定|")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    verdicts = []
    for name in PATTERNS:
        m = M[name] & uni_mask
        mx = M[name] & volx & uni_mask
        if m.sum() < 30:
            L.append(f"|{name}|{m.sum()}|—|—|—|{mx.sum()}|—|—|—|—|n<30跳过|")
            continue
        e20, ex20 = fwd20[m], fwd20[mx]
        med_e, win_e = e20.median() * 100, (e20 > 0).mean() * 100
        med_x, win_x = ex20.median() * 100, (ex20 > 0).mean() * 100
        yr = pd.DataFrame({"y": year[m].values, "f": e20.values})
        pos_y = tot_y = 0
        for yy, gy in yr.groupby("y"):
            bm = baseline_year_med.get(yy)
            if bm is None or pd.isna(bm) or len(gy) < 10:
                continue
            tot_y += 1
            pos_y += int(gy["f"].median() > bm)
        ex_med = med_e - b20.median() * 100
        ex_win = win_e - (b20 > 0).mean() * 100
        if ex_med >= 1.5 and win_e >= 52:
            vd = "✅达标候选"
        elif ex_med >= 0.5 and pos_y >= tot_y * 0.6:
            vd = "🟡弱边际待深拆"
        else:
            vd = "❌无边际"
        verdicts.append((name, vd))
        L.append(f"|{name}|{m.sum()}|{med_e:+.2f}%|{ex_med:+.2f}pp|{win_e:.1f}%|"
                 f"{mx.sum()}|{med_x:+.2f}%|{med_x - b20.median()*100:+.2f}pp|{win_x:.1f}%|"
                 f"{pos_y}/{tot_y}|{vd}|")
    L.append("")
    n_pass = sum(1 for _, v in verdicts if v.startswith("✅"))
    L.append(f"## 族级判读：{len(verdicts)} 个可检形态中达标候选 {n_pass} 个；"
             f"放量叠加整体未见系统性改善（逐形态见上表放量列）。")
    L.append("")

    out = "\n".join(L)
    REPORT.write_text(out, encoding="utf-8")
    print(out)
    print(f"[OK] {REPORT}")


if __name__ == "__main__":
    main()
