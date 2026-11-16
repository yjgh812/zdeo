# -*- coding: utf-8 -*-
"""买卖区域研判引擎 v1 (观测层, 零交易权) — PD关联: 主人2026-10-03 Agent规格落地
区域优先级互斥分类(每日一区, 防重复计数):
  卖出侧: PQ破位清仓 > DJ下跌中继 > TP止盈兑现 > WK趋势走坏
  买入侧: H追高风险(禁开仓) > B突破确认 > G黄金低吸 > P回踩加仓
  其余: X观望
判定全部用T日及以前完成数据(fwd仅评估用, 无前视)。qfq口径注记: 价格为复权价,
实际价过滤(≤20/≤30元)在个股演示层用真源价补, 全市场回测层如实声明未滤。
SMC: PinBar/吞没已实现; 订单流/流动性陷阱无tick数据=不适用(如实标注)。
"""
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def load(start="20210301", end="20260918"):
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
        conn, params=[start, end])
    conn.close()
    df["date"] = df["date"].astype(int)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def build_features(df):
    g = df.groupby("code", sort=False)
    for w in (5, 10, 20, 60):
        df[f"ma{w}"] = g["close"].transform(lambda s, w=w: s.rolling(w, min_periods=w).mean())
    df["vol20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=10).mean().shift(1))
    df["volr20"] = df["volume"] / df["vol20"]
    df["shrink2"] = g["volr20"].transform(lambda s: ((s < 0.85).rolling(2).min() > 0).astype(bool))  # 近2日缩量
    df["ret20"] = g["close"].transform(lambda s: s.pct_change(20)) * 100
    df["low60"] = g["low"].transform(lambda s: s.rolling(60, min_periods=40).min().shift(1))
    df["high60"] = g["high"].transform(lambda s: s.rolling(60, min_periods=40).max().shift(1))
    df["pos60"] = (df["close"] - df["low60"]) / (df["high60"] - df["low60"]) * 100
    df["newlow2"] = g.apply(lambda s: (s["close"] <= s["close"].rolling(20, min_periods=10).min()).rolling(2).max()
                            .reset_index(drop=True)).values if False else None
    # 简化: 近2日曾创20日新低
    df["min20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=10).min())
    df["nearnewlow"] = (df["close"] <= df["min20"] * 1.01)
    df["nl2"] = g["nearnewlow"].transform(lambda s: (s.rolling(2).max() > 0).astype(bool))
    # K线形态
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    lowshadow = np.minimum(df["open"], df["close"]) - df["low"]
    upshadow = df["high"] - np.maximum(df["open"], df["close"])
    df["pin_bull"] = (lowshadow >= 0.55 * rng) & (lowshadow >= 2 * body)
    df["pin_bear"] = (upshadow >= 0.55 * rng) & (upshadow >= 2 * body)
    prev_o = g["open"].shift(1)
    prev_c = g["close"].shift(1)
    df["eng_bull"] = (df["close"] > df["open"]) & (prev_c < prev_o) & (df["close"] >= prev_o) & (df["open"] <= prev_c)
    df["eng_bear"] = (df["close"] < df["open"]) & (prev_c > prev_o) & (df["close"] <= prev_o) & (df["open"] >= prev_c)
    # 均线结构
    df["bull_align"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (df["close"] > df["ma20"])
    df["bear_align"] = (df["ma5"] < df["ma10"]) & (df["ma10"] < df["ma20"]) & (df["close"] < df["ma20"])
    df["below_ma20_2"] = g.apply(lambda s: ((s["close"] < s["ma20"]).rolling(2).min())
                                 .reset_index(drop=True)).values if False else None
    df["b20"] = df["close"] < df["ma20"]
    df["b20_2"] = g["b20"].transform(lambda s: (s.rolling(2).min() > 0).astype(bool))
    df["below_ma5_2"] = g.apply(lambda s: ((s["close"] < s["ma5"]).rolling(2).min())
                                .reset_index(drop=True)).values if False else None
    df["b5"] = df["close"] < df["ma5"]
    df["b5_2"] = g["b5"].transform(lambda s: (s.rolling(2).min() > 0).astype(bool))
    # 突破
    df["break60"] = (df["close"] > df["high60"]) & (df["high60"].notna())
    df["gene10"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(10).max().shift(1))
    df["bar_n"] = g.cumcount()
    for k in (5, 10, 20):
        df[f"f{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k)) / df["close"] - 1
    return df


ZONE_NAMES = {
    "PQ": "破位清仓区", "DJ": "下跌中继观望区", "TP": "止盈兑现区", "WK": "趋势走坏减仓区",
    "H": "追高风险区(禁开仓)", "B": "突破确认区", "G": "黄金低吸区", "P": "回踩加仓区", "X": "观望区",
}


def classify(df):
    z = np.full(len(df), "X", dtype="<U2")
    c = df
    # 卖出侧(优先)
    pq = c["b20_2"].fillna(False) & (c["volr20"] >= 1.2)
    dj = c["bear_align"].fillna(False) & (c["volr20"] < 0.85) & (c["pos60"] <= 45) & ~c["nl2"].fillna(True)
    tp = (c["pos60"] >= 85) & (((c["volr20"] >= 1.5) & (c["change_pct"].abs() < 1)) | c["pin_bear"] | c["eng_bear"])
    wk = c["b5_2"].fillna(False) & (c["ma5"] < c["ma10"])
    # 买入侧
    h = (c["ret20"] >= 20) & ((c["close"] / c["ma20"] - 1) >= 0.15)
    brk = c["break60"].fillna(False) & (c["volr20"] >= 1.5) & c["bull_align"].fillna(False)
    gold = (c["pos60"] <= 40) & c["shrink2"].fillna(False) & ~c["nl2"].fillna(True) & \
        ((c["close"] <= c["low60"] * 1.05) | (c["close"] <= c["ma20"] * 1.03))
    pb = c["bull_align"].fillna(False) & (c["volr20"] < 0.8) & \
        ((c["low"] <= c["ma10"] * 1.02) | (c["low"] <= c["ma20"] * 1.02)) & (c["close"] >= c["ma20"] * 0.98)
    for mask, code in [(pq, "PQ"), (dj, "DJ"), (tp, "TP"), (wk, "WK"),
                       (h, "H"), (brk, "B"), (gold, "G"), (pb, "P")]:
        z[np.where(mask.fillna(False).values)[0]] = code
    df["zone"] = z
    df["zone_new"] = df.groupby("code")["zone"].transform(lambda s: s != s.shift(1))
    return df


def stats(ev, label):
    out = [f"{label:22s} n={len(ev):7d}"]
    for k in (5, 10, 20):
        s = ev[f"f{k}"].dropna() * 100
        if len(s) == 0:
            continue
        # 盈亏比: 平均盈利/平均亏损
        gp = s[s > 0].mean() if (s > 0).any() else np.nan
        gl = abs(s[s < 0].mean()) if (s < 0).any() else np.nan
        rr = gp / gl if gl and gl > 0 else np.nan
        out.append(f"f{k}: win={(s>0).mean()*100:4.1f}% med={s.median():+6.2f} RR={rr:4.1f}")
    print(" | ".join(out))


def main():
    df = build_features(load())
    df = df[df["bar_n"] >= 60].reset_index(drop=True)
    df = classify(df)
    print(f"rows={len(df)} stocks={df['code'].nunique()} window=2021-09~2026-09")
    ev = df[(df["zone_new"]) & df["f20"].notna()]
    print("=== 5年实测: 各区域(新进入事件) fwd胜率/中位/盈亏比 ===")
    order = ["G", "B", "P", "H", "TP", "WK", "PQ", "DJ", "X"]
    for zc in order:
        sub = ev[ev["zone"] == zc]
        stats(sub, ZONE_NAMES[zc])
    base = df[df["f20"].notna()]
    stats(base, "基线(全部日)")
    ev[["code", "date", "zone", "pos60", "volr20", "ret20", "f5", "f10", "f20"]].to_csv(
        r"D:\zcodesj\reports\zone_engine_events_20261003.csv", index=False, encoding="utf-8-sig")
    print("saved events csv", len(ev))
    # 分年稳健: 关键三区
    print("=== 分年(G/B/H 三区 f20 win%) ===")
    for zc in ("G", "B", "H"):
        sub = ev[ev["zone"] == zc].copy()
        sub["year"] = (sub["date"] // 10000).astype(int)
        line = ZONE_NAMES[zc] + ": "
        for y, ss in sub.groupby("year"):
            s = ss["f20"].dropna() * 100
            line += f"{y}={((s>0).mean()*100):.0f}%({len(s)}) "
        print(line)


if __name__ == "__main__":
    sys.exit(main())
