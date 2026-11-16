# -*- coding: utf-8 -*-
"""重训后验证探针(20261021) — 干净数据新模型的判别力+校准一锤定音(B12重判)。

自动发现本次重训产物 pred_test_2026102{1,2}_seed*.pkl(可分批运行, 已落盘种子即评):
  ① 判别力: 对修复后 qfq 库的 fwd1/5/10/20 日均秩IC(逐日横截面);
  ② 校准: 时序切分 fit(前60%) → embargo2日 → eval(后40%), 十分位映射 →
     eval 期 Brier vs 基准率(=无增量信息判定线) + ECE;
判定(预注册):
  AUC/IC 维度: fwd1 秩IC ≥0.03 且校准 Brier 优于基准 → 概率链重启候选;
  仍全面 ≤0.02 → 与旧模型同判(概率化维持禁止), 但训练数据已干净, 结论升级为终审。
"""
import glob
import json
import pickle
import sqlite3
import sys

import numpy as np
import pandas as pd
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\post_retrain_probe_20261021_result.json"


def load_preds():
    files = sorted(glob.glob(r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_2026102*.pkl"))
    if not files:
        print("[exit] 本轮重训尚无产物")
        sys.exit(0)
    print(f"[in] {len(files)} 个种子产物")
    frames = []
    for f in files:
        s = pickle.load(open(f, "rb")).rename(f"s{len(frames)}")
        frames.append(s)
    ens = pd.concat(frames, axis=1).mean(axis=1).rename("pred")
    return ens, len(frames)


def main():
    ens, n_seed = load_preds()
    df = ens.rename("pred").reset_index()
    df["dt"] = df["datetime"].astype(str).str[:10].str.replace("-", "")
    df["code"] = df["instrument"].str[-6:]
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    codes = ",".join(f"'{c}'" for c in df["code"].unique())
    q = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({codes}) AND date>='20251201' ORDER BY code,date",
        con)
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    q["i"] = q["date"].map(pos)
    closes = q.pivot(index="i", columns="code", values="close").sort_index()
    m = df.copy()
    m["i"] = m["dt"].map(pos)
    m = m.dropna(subset=["i"]); m["i"] = m["i"].astype(int)
    m = m.set_index(["code", "i"])
    fwd = {}
    for k in (1, 5, 10, 20):
        fwd[f"f{k}"] = (closes.shift(-k) / closes - 1).stack().swaplevel().rename_axis(["code", "i"]).rename(f"f{k}")
    j = pd.concat([m["pred"]] + list(fwd.values()), axis=1, join="inner").dropna()
    j = j.reset_index()
    j["ym"] = j["i"].map(lambda i: cal[i][:6])
    print(f"对齐样本 n={len(j)}")
    ics = {}
    for col in [c for c in j.columns if c.startswith("f")]:
        ic = j.groupby("i").apply(lambda g: g["pred"].corr(g[col], method="spearman"),
                                  include_groups=False).replace([np.inf, -np.inf], np.nan).dropna()
        ics[col] = {"mean": round(float(ic.mean()), 4), "std": round(float(ic.std()), 4),
                    "pos_days": f"{(ic > 0).mean():.0%}"}
        print(f"  {col}: 日均秩IC={ic.mean():+.4f} ±{ic.std():.4f} 正占比={(ic > 0).mean():.0%}")

    # 时序切分校准(fwd1为判定目标; fit→embargo2→eval)
    dates_u = sorted(j["i"].unique())
    n_eval = max(4, len(dates_u) // 3)
    eval_days = set(dates_u[-n_eval:])
    fit_days = set(dates_u[: len(dates_u) - n_eval - 2])
    fitm = j[j["i"].isin(fit_days)].copy()
    ev = j[j["i"].isin(eval_days)].copy()
    fitm["up"] = (fitm["f1"] > 0).astype(int)
    ev["up"] = (ev["f1"] > 0).astype(int)
    fitm["dec"] = (fitm["pred"].rank(pct=True) * 10).apply(np.ceil).astype(int).clip(1, 10)
    mapping = fitm.groupby("dec")["up"].mean()
    base_fit = float(fitm["up"].mean())
    ev["dec"] = (ev["pred"].rank(pct=True) * 10).apply(np.ceil).astype(int).clip(1, 10)
    ev["p_map"] = ev["dec"].map(mapping).fillna(base_fit)
    brier_model = float(((ev["p_map"] - ev["up"]) ** 2).mean())
    brier_base = float(((ev["up"].mean() - ev["up"]) ** 2).mean())
    ece = float(sum(len(g) / len(ev) * abs(float(g["p_map"].iloc[0]) - g["up"].mean())
                    for _, g in ev.groupby("dec")))
    print(f"\n[校准·fit{len(fit_days)}日→embargo2→eval{len(eval_days)}日 n={len(ev)}]")
    print(f"  Brier 模型={brier_model:.5f} vs 基准率={brier_base:.5f} → "
          f"{'优于基准(有增量信息!)' if brier_model < brier_base else '不优于基准(无增量信息)'}")
    print(f"  ECE={ece:.4f}")

    result = {"probe": "post_retrain", "date": "2026-10-21", "n_seeds": n_seed,
              "n_eval_samples": int(len(j)), "horizon_ic": ics,
              "calibration": {"brier_model": round(brier_model, 5), "brier_base": round(brier_base, 5),
                              "ece": round(ece, 4),
                              "verdict": "优于基准" if brier_model < brier_base else "不优于基准"}}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}")


if __name__ == "__main__":
    sys.exit(main())
