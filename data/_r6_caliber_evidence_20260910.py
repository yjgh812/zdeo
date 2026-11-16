# -*- coding: utf-8 -*-
"""R6 收在高点闸门 · 历史证据研究（2026-09-10，只读事件研究，R32_RECHECK 先例同款）。

命题: R6 拒绝「买入价位于区间位置≥70%」的追高形态。执行层 --close 传拟买价、
--high/--low 传行情快照区间——盘前批次(如 9/10 00:45 手工批)实际判定的是
「拟买价在 T-1 完成区间的位置」。本脚本检验该口径是否有证据支持:

  主检验: T-1 收盘区间位置 pos = (close-low)/(high-low) ≥ 0.70 的股票, 次日买入
          持 5/10 日的收益是否显著劣于 pos<0.70 组。

口径: qfq 主库 stock_daily_meta_qfq (open/high/low/close), 全市场主板(剔 688/689/8/4/920/43),
      T-1 收盘 ≤30 元(白名单铁律), fwd = T+1 开盘买(次日 open) → T+5/T+10 收盘卖,
      无前视(信号用 T 收盘区间, 入场 T+1 open)。
输出: data/_r6_caliber_evidence_20260910.json + 控制台摘要。
只读零写入(除本 json)。
"""
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\data\_r6_caliber_evidence_20260910.json"


def board_ok(code: str) -> bool:
    return not code.startswith(("688", "689", "8", "4", "920", "43"))


def main() -> int:
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True, timeout=60)
    print("[1/4] 加载全表日线...", flush=True)
    rows = con.execute(
        "SELECT code, date, open, high, low, close FROM stock_daily_meta_qfq "
        "ORDER BY code, date").fetchall()
    con.close()
    print(f"      共 {len(rows)} 行", flush=True)

    # 逐码向量化太慢, 用 pandas 分组
    import pandas as pd
    df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close"])
    df = df[df["code"].map(lambda c: board_ok(str(c)[-6:]))].copy()
    df["code"] = df["code"].str[-6:]
    df["date"] = df["date"].astype(str)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] > df["low"]) & (df["low"] > 0)].copy()

    print("[2/4] 计算 T 日区间位置 + fwd 收益...", flush=True)
    df = df.sort_values(["code", "date"])
    df["pos"] = (df["close"] - df["low"]) / (df["high"] - df["low"])
    # T+1 open 买入, T+5 / T+10 close 卖出 (同码内 shift)
    g = df.groupby("code")
    df["buy_open"] = g["open"].shift(-1)
    df["fwd5"] = g["close"].shift(-5) / df["buy_open"] - 1
    df["fwd10"] = g["close"].shift(-10) / df["buy_open"] - 1

    # 白名单铁律: T-1 收盘 ≤30 元
    df = df[df["close"] <= 30].copy()
    df = df.dropna(subset=["fwd5"])

    def day_cluster_t(a: pd.Series, b: pd.Series, n_samples: int = 4000) -> float:
        """日聚簇 Welch t 近似: 随机抽样限内存。"""
        import numpy as np
        sa = a.sample(min(len(a), n_samples), random_state=42)
        sb = b.sample(min(len(b), n_samples), random_state=42)
        va, vb = sa.var(ddof=1), sb.var(ddof=1)
        na, nb = len(sa), len(sb)
        if va + vb == 0:
            return 0.0
        se = (va / na + vb / nb) ** 0.5
        return (sa.mean() - sb.mean()) / se

    hi = df[df["pos"] >= 0.70]
    lo = df[df["pos"] < 0.70]
    stats = {}
    for label, col in (("fwd5", "fwd5"), ("fwd10", "fwd10")):
        h = hi[col].dropna(); l = lo[col].dropna()
        stats[label] = {
            "hi_n": int(len(h)), "hi_mean": round(float(h.mean()) * 100, 4),
            "hi_winrate": round(float((h > 0).mean()) * 100, 2),
            "lo_n": int(len(l)), "lo_mean": round(float(l.mean()) * 100, 4),
            "lo_winrate": round(float((l > 0).mean()) * 100, 2),
            "excess_pp": round(float(h.mean() - l.mean()) * 100, 4),
            "t_approx": round(day_cluster_t(h, l), 3),
        }
        print(f"      {label}: pos≥0.7 n={stats[label]['hi_n']} 均值{stats[label]['hi_mean']}% "
              f"胜率{stats[label]['hi_winrate']}% | pos<0.7 n={stats[label]['lo_n']} "
              f"均值{stats[label]['lo_mean']}% 胜率{stats[label]['lo_winrate']}% | "
              f"超额{stats[label]['excess_pp']}pp t≈{stats[label]['t_approx']}", flush=True)

    # 分位单调性: pos 五分位 → fwd5
    print("[3/4] pos 五分位 → fwd5 单调性...", flush=True)
    df["pos_bin"] = pd.qcut(df["pos"], 5, labels=False, duplicates="drop")
    quint = df.groupby("pos_bin", observed=True)["fwd5"].agg(["count", "mean"])
    stats["quintile_fwd5"] = {
        str(int(i)): {"n": int(r["count"]), "mean_pct": round(float(r["mean"]) * 100, 4)}
        for i, r in quint.iterrows()}
    for i, r in quint.iterrows():
        print(f"      Q{i}: n={int(r['count'])} fwd5均值 {r['mean']*100:+.4f}%", flush=True)

    # 分年诊断
    print("[4/4] 分年诊断(hi超额pp)...", flush=True)
    df["year"] = df["date"].str[:4]
    by_year = {}
    for y, sub in df.groupby("year"):
        h = sub[sub["pos"] >= 0.70]["fwd5"]; l = sub[sub["pos"] < 0.70]["fwd5"]
        if len(h) > 200 and len(l) > 200:
            by_year[y] = {
                "hi_mean": round(float(h.mean()) * 100, 4),
                "lo_mean": round(float(l.mean()) * 100, 4),
                "excess_pp": round(float(h.mean() - l.mean()) * 100, 4),
                "hi_n": int(len(h)), "lo_n": int(len(l))}
    stats["by_year"] = by_year
    for y, r in by_year.items():
        print(f"      {y}: hi={r['hi_mean']:+.3f}% lo={r['lo_mean']:+.3f}% "
              f"超额={r['excess_pp']:+.3f}pp (hi n={r['hi_n']})", flush=True)

    stats["window"] = {"min_date": str(df["date"].min()), "max_date": str(df["date"].max()),
                       "rows": int(len(df)), "filter": "主板+close≤30元+T+1开盘入场"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"落盘: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
