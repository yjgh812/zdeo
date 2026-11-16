# -*- coding: utf-8 -*-
"""ER效率比验证轮(2026-10-07, 只读纯描述性, 零接线零交易权)
背景: R44轮呈报候选A——用Kaufman ER把10/7"动量甜区=震荡偏强段"的文字regime标注参数化。
三查: 精确关键词证伪库0/prereg 0/记忆1条REVIEW(MOM-GATE disposition方案B在位, 本研究为
甜区regime标注的描述性复核与其并存, 不立项MOM-GATE信号, 不接闸门仓位)。

验证面:
  A 指数层: vipdoc sh000001全史 ER10 三分位分组 × fwd1/5/20 (中位/胜率/均值) + 分年
  B 个股层: qfq库2015-2026全市场, ret20动量 vs fwd20 截面rank IC, 按上证ER10分组
            (低ER=震荡 / 中 / 高ER=趋势) —— 检验震荡市动量IC是否更高(甜区参数化复现)
产出: tmp/er_efficiency_result_20261007.json
"""
import json
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = Path(r"D:\zcodesj\tmp\er_efficiency_result_20261007.json")

ER_WIN = 10
START, END = "20150101", "20261231"


def read_vipdoc_full():
    rows = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append({"date": str(d), "close": c / 100.0})
    return pd.DataFrame(rows)


def er_series(close: np.ndarray, win: int) -> np.ndarray:
    """Kaufman ER: |C_t - C_{t-win}| / sum(|daily change| in win), 前win日为NaN。"""
    n = len(close)
    er = np.full(n, np.nan)
    dif = np.abs(np.diff(close))  # dif[i]=|C_{i+1}-C_i|
    for t in range(win, n):
        denom = dif[t - win:t].sum()
        if denom > 0:
            er[t] = abs(close[t] - close[t - win]) / denom
    return er


def fwd_stats(df: pd.DataFrame, horizons=(1, 5, 20)) -> dict:
    out = {}
    for h in horizons:
        fwd = (df["close"].shift(-h) / df["close"] - 1) * 100.0
        v = fwd.dropna()
        out[f"fwd{h}"] = {"med": round(float(v.median()), 3),
                          "mean": round(float(v.mean()), 3),
                          "win": round(float((v > 0).mean() * 100), 1),
                          "n": int(len(v))}
    return out


def part_a_index():
    df = read_vipdoc_full()
    close = df["close"].to_numpy()
    df["er"] = er_series(close, ER_WIN)
    q30, q70 = df["er"].quantile([0.30, 0.70])
    df["grp"] = np.where(df["er"] <= q30, "lowER_osc",
                np.where(df["er"] >= q70, "highER_trend", "mid"))
    res = {"thresholds": {"q30": round(float(q30), 4), "q70": round(float(q70), 4)},
           "groups": {}}
    for g, sub in df.groupby("grp"):
        res["groups"][g] = fwd_stats(sub)
    # 分年: 高ER组逐年
    yearly = {}
    df["year"] = df["date"].str[:4]
    for y, sub in df[df["grp"] == "highER_trend"].groupby("year"):
        fwd20 = (sub["close"].shift(-20) / sub["close"] - 1) * 100.0
        v = fwd20.dropna()
        if len(v) >= 5:
            yearly[y] = {"med": round(float(v.median()), 2), "win": round(float((v > 0).mean() * 100), 1), "n": int(len(v))}
    res["highER_yearly_fwd20"] = yearly
    return res


def part_b_momentum_ic():
    con = sqlite3.connect(QFQ_DB)
    q = f"""SELECT code, date, close FROM stock_daily_meta_qfq
            WHERE date >= '{START}' AND date <= '{END}' AND close > 0"""
    df = pd.read_sql(q, con)
    con.close()
    piv = df.pivot(index="date", columns="code", values="close").sort_index()
    ret20 = piv / piv.shift(20) - 1.0
    fwd20 = piv.shift(-20) / piv - 1.0
    # 市场ER(上证)
    idx = read_vipdoc_full()
    idx["er"] = er_series(idx["close"].to_numpy(), ER_WIN)
    er_map = dict(zip(idx["date"], idx["er"]))

    common = [d for d in piv.index if d in er_map and not np.isnan(er_map[d])]
    print(f"[B] 截面日 {len(common)} (含ER)")
    ics, ers, dates_used = [], [], []
    for d in common:
        x = ret20.loc[d].dropna()
        y = fwd20.loc[d].reindex(x.index).dropna()
        if len(x) < 200:
            continue
        y = y.reindex(x.dropna().index)
        x = x.reindex(y.index)
        if len(x) < 200:
            continue
        ic = x.corr(y, method="spearman")
        if not np.isnan(ic):
            ics.append(ic)
            ers.append(er_map[d])
            dates_used.append(d)
    s = pd.Series(ics)
    s.index = pd.Index(ers, name="er")
    dates = pd.Index(dates_used)
    er_arr = s.index.to_numpy()
    q30 = float(np.nanquantile(er_arr, 0.30))
    q70 = float(np.nanquantile(er_arr, 0.70))
    grp = np.where(er_arr <= q30, "lowER_osc", np.where(er_arr >= q70, "highER_trend", "mid"))
    res = {"n_days": int(len(s)), "thresholds": {"q30": round(q30, 4), "q70": round(q70, 4)},
           "overall": {"ic_mean": round(float(s.mean()), 4),
                       "icir": round(float(s.mean() / s.std()), 3),
                       "ic_pos_pct": round(float((s > 0).mean() * 100), 1)},
           "groups": {}}
    for g in ("lowER_osc", "mid", "highER_trend"):
        sub = s[grp == g]
        res["groups"][g] = {"ic_mean": round(float(sub.mean()), 4),
                            "icir": round(float(sub.mean() / sub.std()), 3),
                            "ic_pos_pct": round(float((sub > 0).mean() * 100), 1),
                            "n_days": int(len(sub))}
    # 分年: 低ER组逐年IC（纯布尔mask位置对齐——ER float做index有重复标签, .loc按标签取会膨胀导致维度崩）
    s_pos = pd.Series(ics)  # 位置index, 与 ers/dates_used 一一对应
    er_arr = np.asarray(ers, dtype=float)
    yearly = {}
    yr = pd.Series([d[:4] for d in dates_used])
    mask_lo = np.asarray(grp) == "lowER_osc"
    for y in sorted(yr.unique()):
        m = ((yr == y).to_numpy()) & mask_lo
        sub = s_pos[m]
        if len(sub) >= 20:
            yearly[y] = {"lowER_ic": round(float(sub.mean()), 4), "n": int(len(sub))}
    res["lowER_yearly_ic"] = yearly
    return res


def main():
    print("=== A 指数层: 上证ER10分组×前向 ===")
    a = part_a_index()
    print(json.dumps(a, ensure_ascii=False, indent=1)[:1200])
    print("\n=== B 个股层: ret20动量IC × 市场ER分组 ===")
    b = part_b_momentum_ic()
    print(json.dumps(b, ensure_ascii=False, indent=1)[:1500])
    OUT.write_text(json.dumps({"A_index": a, "B_momentum_ic": b},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[done] -> {OUT}")


if __name__ == "__main__":
    main()
