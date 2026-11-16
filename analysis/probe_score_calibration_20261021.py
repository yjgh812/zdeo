# -*- coding: utf-8 -*-
"""只读探针(20261021): 生产MASTER/HIST全市场score → 上涨概率经验校准检验(框架诊断2/诊断3)

目的: 回答"score分位 → 次日/5日上涨频率"映射是否存在、是否单调、判别力(AUC)与经济差(高减低收益差)。
口径: score日T, next-bar基准T+1收盘; ret1=close[T+2]/close[T+1]-1 (与训练标签 Ref($close,-2)/Ref($close,-1)-1 同构);
      ret5=close[T+6]/close[T+1]-1。价格源=dragon_quant_qfq.db 只读(宪法唯一价格源)。
输入: reports/daily_signals/full_score_YYYYMMDD.csv (25个交易日)。
输出: stdout 表格 + analysis/score_calibration_probe_20261021_result.json。全程只读, 不写任何系统库。
"""
import glob
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
SCORE_GLOB = os.path.join(BASE, r"reports\daily_signals\full_score_*.csv")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_JSON = os.path.join(BASE, r"analysis\score_calibration_probe_20261021_result.json")


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def daily_auc(score, label):
    """单日内 Mann-Whitney AUC = P(分数高者标签为1)。label: bool。"""
    pos = score[label]
    neg = score[~label]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    ranks = pd.Series(np.concatenate([pos, neg])).rank().values
    r_pos = ranks[: len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def main():
    files = sorted(glob.glob(SCORE_GLOB))
    if len(files) < 5:
        print(f"[FAIL] score文件不足: {len(files)}")
        sys.exit(2)
    frames = []
    for f in files:
        d = os.path.basename(f)[11:19]  # full_score_YYYYMMDD.csv
        df = pd.read_csv(f, usecols=["code", "master", "hist"])
        df["date"] = d
        frames.append(df)
    sc = pd.concat(frames, ignore_index=True)
    sc["code6"] = sc["code"].str[-6:]
    n_days = sc["date"].nunique()
    print(f"[load] score文件 {len(files)} 个, 日期 {sc['date'].min()}→{sc['date'].max()} 共{n_days}日, 行数 {len(sc)}")

    # qfq 收盘: 只取覆盖区间 + 前后各留足前瞻窗口
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    q = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date BETWEEN ? AND ?",
        con,
        params=(str(int(sc["date"].min()) - 1), str(int(sc["date"].max()) + 30)),
    )
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    pv = q.pivot(index="code", columns="date", values="close")
    print(f"[qfq] 交易日 {cal[0]}→{cal[-1]} 共{len(cal)}日, 股票 {pv.shape[0]} 只")

    # 逐score日拼 next-bar 前瞻收益
    rows = []
    for d, g in sc.groupby("date"):
        i = pos.get(d)
        if i is None:
            continue
        need = [i + 1, i + 2, i + 6]
        if need[-1] >= len(cal):
            continue  # 前瞻窗口不完整, 丢弃
        c1 = pv[cal[need[0]]]
        ret1 = pv[cal[need[1]]] / c1 - 1
        ret5 = pv[cal[need[2]]] / c1 - 1
        gg = g.set_index("code6")
        common = gg.index.intersection(c1.index)
        r = pd.DataFrame(
            {
                "master": gg.loc[common, "master"],
                "hist": gg.loc[common, "hist"],
                "ret1": ret1.loc[common],
                "ret5": ret5.loc[common],
            }
        ).dropna()
        r["date"] = d
        # 日内横截面分位(跨日score不可比, 只用日内相对位置), 1=最低分, 10=最高分
        for col in ("master", "hist"):
            _pct = r[col].rank(pct=True)  # 排名分位法: 并列同分位, 无qcut重边问题
            r[col + "_dec"] = np.minimum(10, np.ceil(_pct * 10)).astype(int).clip(lower=1)
        r["combo_pct"] = 0.5 * (
            r["master"].rank(pct=True) + r["hist"].rank(pct=True)
        )
        rows.append(r)
    allr = pd.concat(rows, ignore_index=True)
    print(f"[join] 有效样本(分数+前瞻收益齐) {len(allr)} 行, 覆盖 {allr['date'].nunique()} 个score日")

    base1 = (allr["ret1"] > 0).mean()
    base5 = (allr["ret5"] > 0).mean()
    print(f"[基准率] 样本窗内 ret1>0 占比 {base1:.1%}, ret5>0 占比 {base5:.1%}")

    result = {
        "probe": "score_calibration",
        "date": "2026-10-21",
        "score_days": int(allr["date"].nunique()),
        "score_window": [str(allr["date"].min()), str(allr["date"].max())],
        "n_samples": int(len(allr)),
        "base_rate_ret1_up": round(float(base1), 4),
        "base_rate_ret5_up": round(float(base5), 4),
        "bins": {},
        "auc": {},
        "spread_top_bottom_ret1": {},
        "monotonic": {},
    }

    for col in ("master", "hist", "combo_pct"):
        key = col
        # master/hist 十分位
        if col != "combo_pct":
            bins = []
            for dec in range(1, 11):
                gsub = allr[allr[col + "_dec"] == dec]
                n = len(gsub)
                k1 = int((gsub["ret1"] > 0).sum())
                lo, hi = wilson_ci(k1, n)
                bins.append(
                    {
                        "decile": dec,
                        "n": n,
                        "win_rate_ret1": round(k1 / n, 4),
                        "wilson95": [round(lo, 4), round(hi, 4)],
                        "mean_ret1": round(float(gsub["ret1"].mean()), 5),
                        "median_ret1": round(float(gsub["ret1"].median()), 5),
                        "win_rate_ret5": round(float((gsub["ret5"] > 0).mean()), 4),
                        "mean_ret5": round(float(gsub["ret5"].mean()), 5),
                    }
                )
            wr = [b_["win_rate_ret1"] for b_ in bins]
            result["bins"][key] = bins
            result["monotonic"][key] = bool(
                all(wr[i] <= wr[i + 1] for i in range(len(wr) - 1))
                or all(wr[i] >= wr[i + 1] for i in range(len(wr) - 1))
            )
            result["spread_top_bottom_ret1"][key] = round(
                bins[-1]["mean_ret1"] - bins[0]["mean_ret1"], 5
            )
        # AUC: 逐日算再平均
        aucs = allr.groupby("date").apply(
            lambda g: daily_auc(g[col], g["ret1"] > 0), include_groups=False
        )
        result["auc"][key] = {
            "mean": round(float(aucs.mean()), 4),
            "std": round(float(aucs.std()), 4),
            "n_days": int(aucs.notna().sum()),
        }

    # combo 按五分位(仅呈现用)
    _cq = np.minimum(5, np.ceil(allr["combo_pct"].rank(pct=True) * 5)).astype(int).clip(lower=1)
    allr["combo_q5"] = _cq
    q5 = []
    for qq in range(1, 6):
        gsub = allr[allr["combo_q5"] == qq]
        n = len(gsub)
        k1 = int((gsub["ret1"] > 0).sum())
        lo, hi = wilson_ci(k1, n)
        q5.append(
            {
                "quintile": qq,
                "n": n,
                "win_rate_ret1": round(k1 / n, 4),
                "wilson95": [round(lo, 4), round(hi, 4)],
                "mean_ret1": round(float(gsub["ret1"].mean()), 5),
            }
        )
    result["combo_quintile5"] = q5
    wr5 = [x["win_rate_ret1"] for x in q5]
    result["monotonic"]["combo_q5"] = bool(
        all(wr5[i] <= wr5[i + 1] for i in range(len(wr5) - 1))
    )

    # === B12替代: 时序切分(fit → embargo2日 → eval)样本外校准检验 ===
    dates_u = sorted(allr["date"].unique())
    n_eval = min(9, max(4, len(dates_u) // 3))
    eval_days = set(dates_u[-n_eval:])
    fit_days = set(dates_u[: len(dates_u) - n_eval - 2])  # embargo=2日(标签期限1+缓冲1)
    calib = {
        "fit_days": len(fit_days),
        "embargo_days": 2,
        "eval_days": len(eval_days),
        "eval_window": [dates_u[-n_eval], dates_u[-1]],
    }
    for col, dec_col in (("master", "master_dec"), ("hist", "hist_dec")):
        fitm = allr[allr["date"].isin(fit_days)].assign(up=lambda d: d["ret1"] > 0)
        ev = allr[allr["date"].isin(eval_days)].copy()
        mapping = fitm.groupby(dec_col)["up"].mean()
        base_fit = float(fitm["up"].mean())
        ev["p_map"] = ev[dec_col].map(mapping).fillna(base_fit)
        y = (ev["ret1"] > 0).astype(int)
        brier_model = float(((ev["p_map"] - y) ** 2).mean())
        brier_base = float(((y.mean() - y) ** 2).mean())
        ece = float(sum(
            len(g) / len(ev) * abs(float(g["p_map"].iloc[0]) - (g["ret1"] > 0).mean())
            for _, g in ev.groupby(dec_col)
        ))
        calib[col] = {
            "n_eval": int(len(ev)),
            "brier_model": round(brier_model, 5),
            "brier_base_rate": round(brier_base, 5),
            "brier_improvement": round(brier_base - brier_model, 5),
            "ece": round(ece, 5),
            "verdict": "Brier优于基准" if brier_model < brier_base else "Brier不优于基准(无增量信息)",
        }
    result["calibration_oos"] = calib

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT_JSON}")

    print("\n=== MASTER score 十分位 → 次日(ret1)上涨频率 / 5日(ret5) ===")
    for b in result["bins"]["master"]:
        print(
            f"  D{b['decile']:>2}  n={b['n']:>5}  胜率1日={b['win_rate_ret1']:.1%} "
            f"[{b['wilson95'][0]:.1%},{b['wilson95'][1]:.1%}]  均值={b['mean_ret1']:+.3%} "
            f"中位={b['median_ret1']:+.3%}  胜率5日={b['win_rate_ret5']:.1%}  均值5日={b['mean_ret5']:+.3%}"
        )
    print("\n=== HIST score 十分位 ===")
    for b in result["bins"]["hist"]:
        print(
            f"  D{b['decile']:>2}  n={b['n']:>5}  胜率1日={b['win_rate_ret1']:.1%}  "
            f"均值={b['mean_ret1']:+.3%}  胜率5日={b['win_rate_ret5']:.1%}  均值5日={b['mean_ret5']:+.3%}"
        )
    print("\n=== 组合分位(combo)五分位 ===")
    for b in q5:
        print(f"  Q{b['quintile']}  n={b['n']:>6}  胜率1日={b['win_rate_ret1']:.1%}  均值={b['mean_ret1']:+.3%}")
    print("\n=== AUC(逐日均值) ===")
    for k, v in result["auc"].items():
        print(f"  {k}: {v['mean']} ±{v['std']} ({v['n_days']}日)")
    print("\n=== 高分位-低分位 次日均收益差 ===")
    for k, v in result["spread_top_bottom_ret1"].items():
        print(f"  {k}: {v:+.3%}")
    print("\n=== 单调性 ===")
    print(f"  {result['monotonic']}")


if __name__ == "__main__":
    main()
