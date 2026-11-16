#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
kline_pattern_nextbar_full_20261020.py — 形态族 next-bar 全量检验（屠龙回测铁律口径）

背景：10/20 复核发现初轮「族级零达标」用的是 T 收盘入场口径, 而屠龙回测铁律=next-bar
（信号T收盘确认→T+1执行）。next-bar 口径下 morning_star/bull_engulf 翻转达标
（+2.97pp/58.9%、+2.32pp/56.8%）, 本件把 11 形态在 next-bar 口径下全量重检并补：
  ①对照组拆分(形态约束的净增量):
    ctrl_2d_rev  = 任意「前阴后阳」(无包含/趋势约束)        → bull/bear_engulf 对照
    ctrl_3d_star = 任意「阴-小实体-阳」(无跳空/中点/趋势)    → 早晨/黄昏之星对照
  ②分年正超额(next-bar)
  ③2026 单年 regime(屠龙已知双引擎失效环境)
  ④放量交叉(next-bar)
口径：nb5/nb20 = T+1收盘 → T+1+N收盘；基线=同宇宙 next-bar 全样本。
数据：dragon_quant_qfq.db。输出 stdout + reports/KLINE_PATTERN_NEXTBAR_20261020.md
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOCK_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT = Path(r"D:\zcodesj\reports\KLINE_PATTERN_NEXTBAR_20261020.md")
CUTOFF = "2026-10-10"

PATTERNS = [
    "morning_star", "evening_star", "bull_engulf", "bear_engulf",
    "hammer", "hang_man", "dark_cloud", "piercing",
    "three_white", "three_crows", "doji",
    "ctrl_2d_rev", "ctrl_3d_star",
]


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = pd.DataFrame(cn.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "ORDER BY code, date").fetchall(),
        columns=["code", "date", "open", "high", "low", "close", "volume"])
    cn.close()
    g = df.groupby("code", sort=False)
    o, c, h, l, v = df["open"], df["close"], df["high"], df["low"], df["volume"]
    pc = g["close"].shift(1)
    o1, c1 = g["open"].shift(1), g["close"].shift(1)
    o2, c2 = g["open"].shift(2), g["close"].shift(2)
    b1, b2 = c1 - o1, c2 - o2
    body = c - o
    ret2 = c2 / g["close"].shift(3) - 1
    body1_ret = b1 / c2
    c12 = g["close"].shift(12)
    c11 = g["close"].shift(11)
    v5 = g["volume"].transform(lambda s: s.shift(1).rolling(5).mean())
    nb5 = g["close"].shift(-6) / c1 - 1
    nb20 = g["close"].shift(-21) / c1 - 1
    rownum = g.cumcount()
    year = df["date"].str.slice(0, 4)
    date_dt = pd.to_datetime(df["date"])
    valid = nb5.notna() & nb20.notna() & (rownum >= 60) & (date_dt <= pd.Timestamp(CUTOFF))

    codes = {x for x in set(df["code"].unique()) if not x.startswith(("68", "4", "8", "92"))}
    try:
        cn2 = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
        codes -= {r[0] for r in cn2.execute("SELECT code FROM stock_info WHERE is_st=1").fetchall()}
        cn2.close()
    except Exception as e:
        print(f"[WARN] ST过滤失败({e})", flush=True)
    uni = df["code"].isin(codes) & valid

    trend_dn2, trend_up2 = c2 < c12, c2 > c12
    trend_dn1, trend_up1 = c1 < c11, c1 > c11
    small1 = body1_ret.abs() <= 0.005
    M = {}
    M["morning_star"] = (trend_dn2 & (b2 < 0) & (ret2 <= -0.015) & small1
                         & (pd.concat([o1, c1], axis=1).max(axis=1) <= c2)
                         & (body > 0) & (c >= (o2 + c2) / 2))
    M["evening_star"] = (trend_up2 & (b2 > 0) & (ret2 >= 0.015) & small1
                         & (pd.concat([o1, c1], axis=1).min(axis=1) >= c2)
                         & (body < 0) & (c <= (o2 + c2) / 2))
    M["bull_engulf"] = (trend_dn1 & (b1 < 0) & (body > 0) & (o <= c1) & (c >= o1))
    M["bear_engulf"] = (trend_up1 & (b1 > 0) & (body < 0) & (o >= c1) & (c <= o1))
    lo_sh = pd.concat([o, c], axis=1).min(axis=1) - l
    up_sh = h - pd.concat([o, c], axis=1).max(axis=1)
    hammer_shape = (lo_sh >= 2 * body.abs()) & (up_sh <= 0.3 * body.abs()) \
        & (pd.concat([o, c], axis=1).min(axis=1) >= l + 0.6 * (h - l))
    M["hammer"] = trend_dn1 & hammer_shape
    M["hang_man"] = trend_up1 & hammer_shape
    M["dark_cloud"] = (trend_up1 & (b1 > 0) & (o > c1) & (body < 0)
                       & (c < (o1 + c1) / 2) & (c > o1))
    M["piercing"] = (trend_dn1 & (b1 < 0) & (o < c1) & (body > 0)
                     & (c > (o1 + c1) / 2) & (c < o1))
    r1, r1_1, r1_2 = c / pc - 1, c1 / c2 - 1, c2 / g["close"].shift(3) - 1
    M["three_white"] = ((body > 0) & (b1 > 0) & (b2 > 0) & (r1 >= 0.005) & (r1_1 >= 0.005)
                        & (r1_2 >= 0.005) & (c > c1) & (c1 > c2))
    M["three_crows"] = ((body < 0) & (b1 < 0) & (b2 < 0) & (r1 <= -0.005) & (r1_1 <= -0.005)
                        & (r1_2 <= -0.005) & (c < c1) & (c1 < c2))
    rng = (h - l).where((h - l) > 0)
    M["doji"] = (body.abs() <= 0.1 * rng).fillna(False)
    M["ctrl_2d_rev"] = (b1 < 0) & (body > 0)                       # 对照: 前阴后阳无约束
    M["ctrl_3d_star"] = (b2 < 0) & small1 & (body > 0)             # 对照: 阴-小体-阳无约束

    volx = v >= v5 * 1.5
    b20 = nb20[uni]
    bm_all = b20.median() * 100
    win_all = (b20 > 0).mean() * 100
    year_base = b20.groupby(year[uni].values).median()

    L = []
    L.append("# 形态族 next-bar 全量检验 [2026-10-20]（屠龙回测铁律口径修正轮）")
    L.append("")
    L.append("> ⚠️ 研究呈现零交易权。口径=T+1收盘→T+21收盘(next-bar, 屠龙铁律)；"
             "宇宙=剔科创/北交/次新60日/当前ST；费前口径未扣双边费用。")
    L.append("")
    L.append(f"next-bar基线: n={len(b20):,} fwd20中位 {bm_all:+.2f}% 胜率 {win_all:.1f}%")
    L.append("")
    L.append("|形态|n|fwd20中位|超额|胜率|分年正超额|2026单年超额|放量∧n|放量超额|判定(初)|")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for name in PATTERNS:
        m = M[name] & uni
        e = nb20[m]
        if len(e) < 30:
            L.append(f"|{name}|{m.sum()}|—|—|—|—|—|—|—|n<30|")
            continue
        med = e.median() * 100
        ex = med - bm_all
        win = (e > 0).mean() * 100
        yr_e = pd.DataFrame({"y": year[m].values, "f": e.values})
        pos = tot = 0
        e26 = None
        for yy, gy in yr_e.groupby("y"):
            bm = year_base.get(yy)
            if bm is None or pd.isna(bm) or len(gy) < 10:
                continue
            tot += 1
            d = gy["f"].median() * 100 - bm * 100
            pos += int(d > 0)
            if yy == "2026":
                e26 = d
        mx = nb20[M[name] & volx & uni]
        exx = (mx.median() * 100 - bm_all) if len(mx) >= 30 else None
        vd = "✅达标候选" if (ex >= 1.5 and win >= 52) else "❌无边际"
        L.append(f"|{name}|{len(e):,}|{med:+.2f}%|{ex:+.2f}pp|{win:.1f}%|{pos}/{tot}|"
                 f"{'%+.2fpp' % e26 if e26 is not None else '—'}|"
                 f"{len(mx) if len(mx) >= 30 else '—'}|"
                 f"{('%+.2fpp' % exx) if exx is not None else '—'}|{vd}|")
    L.append("")
    L.append("## 形态净增量拆分（形态 vs 其无约束对照, next-bar fwd20 中位超额差）")
    for pat, ctl in (("morning_star", "ctrl_3d_star"), ("evening_star", "ctrl_3d_star"),
                     ("bull_engulf", "ctrl_2d_rev"), ("bear_engulf", "ctrl_2d_rev")):
        mp, mc = M[pat] & uni, M[ctl] & uni
        if mp.sum() < 30 or mc.sum() < 30:
            continue
        dp = nb20[mp].median() * 100 - nb20[mc].median() * 100
        wp = (nb20[mp] > 0).mean() * 100 - (nb20[mc] > 0).mean() * 100
        L.append(f"- {pat} vs {ctl}: 形态净增量 {dp:+.2f}pp / 胜率差 {wp:+.1f}pp "
                 f"(n {mp.sum():,} vs {mc.sum():,})")
    L.append("")

    out = "\n".join(L)
    REPORT.write_text(out, encoding="utf-8")
    print(out)
    print(f"[OK] {REPORT}")


if __name__ == "__main__":
    main()
