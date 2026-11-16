#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
kline_pattern_event_study_morningstar_20261019.py — 「早晨之星+放量」全史事件研究

背景（2026-10-19）：主人转贴 workbuddy 侧 K线形态选股盘点的示例命题
「昨日出现早晨之星且成交量放大至5日均量1.5倍以上、排除ST」，
按屠龙纪律先跑全史事件研究回答"有无统计边际"，有边际才谈进漏斗初筛层。

形态定义（经典口径参数化，如实声明）：
  T-2 长阴：close[T-2] < open[T-2] 且 (close-open)/prev_close ≤ -1.5%
  T-1 星线：|close-open|/prev_close ≤ 0.5% 且 max(open,close) ≤ close[T-2]
  T   阳线：close > open 且 close ≥ (open[T-2]+close[T-2])/2（深入前阴实体中点）
  量能：volume[T] ≥ 前5日均量(=T-1..T-5) × 1.5
  趋势前置：close[T-2] < close[T-12]（近10日下跌，经典"跌势中"）

宇宙（屠龙口径）：剔科创(68)/北交(4,8,92开头)/次新(上市前60交易日)/当前ST(stock_info.is_st)。
历史逐日ST无法标记，用当前ST名单过滤——口径限制如实声明。

度量：fwd5/fwd20 = T收盘 → T+N收盘（qfq前复权口径直接算），对照=同宇宙全样本基线。
数据：dragon_quant_qfq.db stock_daily_meta_qfq（1192万行, 唯一真源）。
输出：stdout + reports/KLINE_PATTERN_MORNINGSTAR_20261019.md
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOCK_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT = Path(r"D:\zcodesj\reports\KLINE_PATTERN_MORNINGSTAR_20261019.md")
EVENT_DATE_CUTOFF = "2026-10-10"   # 保留 fwd20 完整窗口


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = cn.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "ORDER BY code, date"
    ).fetchall()
    cn.close()
    df = pd.DataFrame(df, columns=["code", "date", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype("float64")
    print(f"loaded rows={len(df)} codes={df['code'].nunique()}", flush=True)

    g = df.groupby("code", sort=False)
    pc = g["close"].shift(1)
    o2, c2 = g["open"].shift(2), g["close"].shift(2)
    o1, c1 = g["open"].shift(1), g["close"].shift(1)
    body = df["close"] - df["open"]
    body1, body2 = c1 - o1, c2 - o2
    ret2 = (c2 / g["close"].shift(3) - 1)   # T-2 阴线实体幅度(相对T-3收盘)
    body1_ret = body1 / c2                  # 星线实体幅度
    v5 = g["volume"].transform(lambda s: s.shift(1).rolling(5).mean())
    c12 = g["close"].shift(12)
    fwd5 = g["close"].shift(-5) / df["close"] - 1
    fwd20 = g["close"].shift(-20) / df["close"] - 1
    rownum = g.cumcount()

    cond_long_black = (body2 < 0) & (ret2 <= -0.015)
    cond_star = (body1_ret.abs() <= 0.005) & (pd.concat([o1, c1], axis=1).max(axis=1) <= c2)
    cond_sun = (body > 0) & (df["close"] >= (o2 + c2) / 2)
    cond_vol = df["volume"] >= v5 * 1.5
    cond_downtrend = c2 < c12
    base_mask = (
        cond_long_black & cond_star & cond_sun & cond_vol & cond_downtrend
        & fwd5.notna() & fwd20.notna() & (rownum >= 60)
        & (pd.to_datetime(df["date"]) <= pd.Timestamp(EVENT_DATE_CUTOFF))
    )

    # 宇宙过滤：科创/北交 + 当前ST
    codes_all = set(df["code"].unique())
    drop_prefix = ("68", "4", "8", "92")
    codes_universe = {c for c in codes_all if not c.startswith(drop_prefix)}
    try:
        cn2 = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
        st_rows = cn2.execute("SELECT code, is_st FROM stock_info WHERE is_st=1").fetchall()
        cn2.close()
        st_codes = {r[0] for r in st_rows}
    except Exception as e:
        print(f"[WARN] ST 过滤失败({e}), 仅按板块过滤", flush=True)
        st_codes = set()
    codes_universe -= st_codes
    base_mask &= df["code"].isin(codes_universe)

    ev = df[base_mask].copy()
    ev["fwd5"], ev["fwd20"] = fwd5[base_mask], fwd20[base_mask]
    ev["year"] = ev["date"].str[:4]
    print(f"events n={len(ev)}", flush=True)

    # 全样本基线（同宇宙, 同有效窗口）
    valid = base_mask.index  # 仅对齐用; 基线用全表宇宙内非NaN行
    uni = df[df["code"].isin(codes_universe) & fwd5.notna() & fwd20.notna()
             & (pd.to_datetime(df["date"]) <= pd.Timestamp(EVENT_DATE_CUTOFF)) & (rownum >= 60)].copy()
    uni["fwd5"], uni["fwd20"] = fwd5[uni.index], fwd20[uni.index]
    uni["year"] = uni["date"].str[:4]

    def stats(s):
        return (len(s), s.median() * 100, s.mean() * 100, (s > 0).mean() * 100)

    n_e, m5_e, mu5_e, w5_e = stats(ev["fwd5"])
    _, m20_e, mu20_e, w20_e = stats(ev["fwd20"])
    n_b, m5_b, mu5_b, w5_b = stats(uni["fwd5"])
    _, m20_b, mu20_b, w20_b = stats(uni["fwd20"])

    L = []
    L.append("# K线形态事件研究 · 早晨之星+放量1.5倍 [2026-10-19]")
    L.append("")
    L.append("> ⚠️ 研究呈现零交易权。形态口径：T-2长阴(≤-1.5%)→T-1星线(|实体|≤0.5%且≤前收)→"
             "T阳线深入前阴实体中点，量能≥5日均量×1.5，近10日下跌前置。宇宙=剔科创/北交/次新60日/"
             "当前ST(qfq库无逐日ST标记,用当前名单,口径限制如实声明)。对照=同宇宙全样本。")
    L.append("")
    L.append("## 总体（fwd 中位数 / 均值 / 胜率）")
    L.append("|口径|n|fwd5 中位|fwd5 均值|fwd5 胜率|fwd20 中位|fwd20 均值|fwd20 胜率|")
    L.append("|---|---|---|---|---|---|---|---|")
    L.append(f"|**事件**|{n_e}|{m5_e:+.2f}%|{mu5_e:+.2f}%|{w5_e:.1f}%|{m20_e:+.2f}%|{mu20_e:+.2f}%|{w20_e:.1f}%|")
    L.append(f"|基线|{n_b}|{m5_b:+.2f}%|{mu5_b:+.2f}%|{w5_b:.1f}%|{m20_b:+.2f}%|{mu20_b:+.2f}%|{w20_b:.1f}%|")
    L.append(f"|**超额(事件-基线)**||**{m5_e - m5_b:+.2f}pp**||**{w5_e - w5_b:+.1f}pp**|"
             f"**{m20_e - m20_b:+.2f}pp**||**{w20_e - w20_b:+.1f}pp**|")
    L.append("")
    L.append("## 分年（事件 n / fwd20 中位 / 胜率）")
    L.append("|年份|n|fwd20 中位|fwd20 胜率|基线中位|超额|")
    L.append("|---|---|---|---|---|---|")
    for y, gy in ev.groupby("year"):
        by = uni[uni["year"] == y]["fwd20"]
        if len(by) == 0:
            continue
        n_y, m_y, _, w_y = stats(gy["fwd20"])
        mb = by.median() * 100
        L.append(f"|{y}|{n_y}|{m_y:+.2f}%|{w_y:.1f}%|{mb:+.2f}%|{m_y - mb:+.2f}pp|")
    L.append("")
    pos_years = 0
    tot_years = 0
    for y, gy in ev.groupby("year"):
        by = uni[uni["year"] == y]["fwd20"]
        if len(by) == 0:
            continue
        tot_years += 1
        if gy["fwd20"].median() > by.median():
            pos_years += 1
    L.append(f"## 判读（对照屠龙门槛：事件级 pass 参考 med≥+1.5% 且 win≥52%，分年多数为正）")
    L.append(f"- fwd20 中位超额 {m20_e - m20_b:+.2f}pp、胜率 {w20_e:.1f}%（基线 {w20_b:.1f}%）、"
             f"分年正超额 {pos_years}/{tot_years}")
    verdict = ("无边际：不立项, 记录后可归档" if (m20_e - m20_b) < 1.5 or w20_e < 52
               else "边际待深化：可再拆量能档位/位置分层后呈请")
    L.append(f"- 初判：**{verdict}**（最终判读以主agent复核为准）")
    L.append("")

    out = "\n".join(L)
    REPORT.write_text(out, encoding="utf-8")
    print(out)
    print(f"[OK] {REPORT}")


if __name__ == "__main__":
    main()
