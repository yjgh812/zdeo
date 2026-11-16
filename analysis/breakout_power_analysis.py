# -*- coding: utf-8 -*-
"""突破主线实验·统计功效分析(主人模拟盘裁判框架·功效评估 20261027) — 零交易权。
启动前必算: 各判据在 alpha=0.05 / power=0.80 下所需最少笔数, 以及 60 日重放实测的
宇宙基线 t20e 分布(次日开盘入场, T+20收盘或先触-8%收盘止损, 净成本按0.35%往返折减)。
三态判决边界: 按观测胜率的 Wilson 95% CI 与基线 p0±5pp 比较 →
  PASS   = CI下界 >= p0+5pp(确认有意义优势)
  FAIL   = CI上界 <  p0-5pp(确认有意义劣于基线=可证伪)
  其余   = INDETERMINATE(定性观察, 不得用作统计判决)
输出: data/breakout_power_latest.json
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
COST_RT = 0.0035  # 往返净成本折减0.35%(佣金+印花+中档滑点), 框架口径
Z_A, Z_B = 1.959964, 0.841621  # alpha=0.05双侧 / power=0.80


def wilson_ci(k, n, z=1.959964):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, ctr - half), min(1.0, ctr + half))


def load(days):
    con = sqlite3.connect(QFQ, uri=True)
    latest = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    win_start = (datetime.strptime(str(latest), "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
    df = pd.read_sql_query(
        f"SELECT code,date,open,close,volume FROM stock_daily_meta_qfq WHERE date>='{win_start}' ORDER BY code,date", con)
    con.close()
    df["code6"] = df["code"].map(lambda r: ("".join(ch for ch in str(r) if ch.isdigit()))[-6:])
    return latest, df


def baseline_t20e(df, replay_days):
    """宇宙基线: 重放窗内每个可评估信号日, 全宇宙(0/3/6剔688,2~30元,有量)次日开盘入场t20e净收益。"""
    uni = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
    uni = uni[(uni["close"] > 0) & (uni["volume"] > 0) & (uni["close"] <= 30)]
    C = uni.pivot_table(index="date", columns="code6", values="close").sort_index()
    O = uni.pivot_table(index="date", columns="code6", values="open").sort_index()
    V = uni.pivot_table(index="date", columns="code6", values="volume").sort_index()
    days = list(C.index)
    rets = []
    for i, d in enumerate(days[:-21]):
        if d not in set(replay_days):
            continue
        entry = O.iloc[i + 1]
        block = C.iloc[i + 1:i + 21]
        valid = entry.notna() & (V.loc[d].notna()) & (V.loc[d] > 0)
        cols_ok = valid.index[valid]
        e = entry[cols_ok].to_numpy(float)
        if len(e) == 0:
            continue
        stop_line = e * 0.92
        stop_mask = block[cols_ok].to_numpy(float) <= stop_line[None, :]
        any_stop = stop_mask.any(axis=0)
        first = np.where(any_stop, stop_mask.argmax(axis=0), block.shape[0] - 1)
        blocksub = block[cols_ok]
        arr = blocksub.to_numpy(float)
        exit_px = arr[first, np.arange(arr.shape[1])]
        ret = exit_px / e - 1 - COST_RT
        rets.append(ret[np.isfinite(ret)])
    allr = np.concatenate(rets) if rets else np.array([])
    return allr


def main():
    latest, df = load(days=250)
    td = sorted(df["date"].unique().tolist())
    evaluable = td[-80:-21]  # 信号日需后看21根K线
    r = baseline_t20e(df, set(evaluable))
    mu0, sd0 = float(np.mean(r)), float(np.std(r, ddof=1))
    p0 = float(np.mean(r > 0))
    n_obs = len(r)
    print(f"宇宙基线t20e(净成本0.35%): 样本={n_obs} | 均值={mu0*100:.2f}% | sigma={sd0*100:.2f}% | 胜率p0={p0*100:.1f}%")
    print("\n[表A] 检出超额均值delta所需笔数(单样本, alpha0.05双侧/power0.80):")
    table_a = {}
    for dpp in (0.5, 1.0, 2.0, 3.0, 5.0):
        delta = dpp / 100
        n = int(np.ceil(((Z_A + Z_B) * sd0 / delta) ** 2))
        table_a[f"{dpp}pp"] = n
        print(f"  delta=+{dpp}pp → n≈{n}")
    print("\n[表B] 双比例胜率差所需每组笔数(基线p0=%.2f):" % p0)
    table_b = {}
    for dpp in (5, 10, 19):
        p1 = min(0.95, p0 + dpp / 100)
        pbar = (p0 + p1) / 2
        n = int(np.ceil(((Z_A * np.sqrt(2 * pbar * (1 - pbar)) + Z_B * np.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) / (dpp / 100)) ** 2))
        table_b[f"+{dpp}pp"] = {"per_group": n, "total": n * 2}
        print(f"  Δ=+{dpp}pp → 每组n≈{n} (两组共{n*2})")
    print("\n[表C] 三态判决边界(规则组观测胜率→Wilson95%CI vs p0±5pp):")
    table_c = {}
    for n in (15, 30, 60, 100, 160, 250, 500):
        pass_lo = None
        fail_hi = None
        for k in range(n + 1):
            lo, hi = wilson_ci(k, n)
            if lo >= p0 + 0.05 and pass_lo is None:
                pass_lo = k
            if hi < p0 - 0.05:
                fail_hi = k
        table_c[str(n)] = {"pass_min_wins": pass_lo, "fail_max_wins": fail_hi}
        ps = f"{pass_lo}/{n}={pass_lo/n*100:.0f}%" if pass_lo is not None else "n内不可达"
        fs = f"{fail_hi}/{n}={fail_hi/n*100:.0f}%" if fail_hi is not None else "n内不可达"
        print(f"  n={n:>3}: PASS需胜≥{ps} | FAIL需胜≤{fs} | 其余=INDETERMINATE")
    out = {"asof": latest, "baseline": {"n": n_obs, "mu": round(mu0, 5), "sigma": round(sd0, 5), "win_rate": round(p0, 4),
                                        "cost_rt": COST_RT},
           "table_a_n_for_excess": table_a, "table_b_n_per_group": table_b,
           "table_c_three_outcome": table_c,
           "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(out, open("data/breakout_power_latest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写 data/breakout_power_latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
