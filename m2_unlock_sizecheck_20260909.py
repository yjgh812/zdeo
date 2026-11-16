# -*- coding: utf-8 -*-
"""M-2 小盘混杂排除检验（prereg=PREREG_M2_UNLOCK_SIZECHECK_20260909，判据跑前冻结）
窗口=2020-2026.09（daily_basic 完整覆盖段）；超额基准=同日同流通市值五分位桶。
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

OUT_DIR = r"D:\zcodesj\reports\M2_UNLOCK_SIZECHECK_20260909"
os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def main():
    # ---- 事件 ----
    db = sqlite3.connect(r"D:\zcodesj\event_calendar.db")
    ev = pd.read_sql(
        "SELECT float_date, code, ratio FROM restricted_release_hist_em WHERE ratio IS NOT NULL",
        db)
    db.close()
    ev["fd"] = ev["float_date"].str.replace("-", "", regex=False)

    # ---- 价格 + fwd10 ----
    db = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
    px = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20160101'",
        db)
    db.close()
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    px["fwd10"] = px.groupby("code")["close"].shift(-10) / px["close"] - 1.0
    tdays = np.sort(px["date"].unique())
    tpos = {d: i for i, d in enumerate(tdays)}
    ev = ev[ev["fd"].isin(tpos)].copy()
    ev["t0"] = tdays[ev["fd"].map(tpos).to_numpy() - 10]
    ev = ev.merge(px[["code", "date", "fwd10"]].rename(
        columns={"date": "t0", "fwd10": "fwd_ev"}), on=["code", "t0"], how="left")
    ev = ev.dropna(subset=["fwd_ev"]).copy()
    ev = ev[ev["t0"] >= "20200101"].copy()
    log(f"事件(2020+)={len(ev):,}")

    # ---- daily_basic 市值 ----
    db = sqlite3.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
    mv = pd.read_sql(
        "SELECT ts_code, trade_date, circ_mv FROM daily_basic WHERE trade_date >= '20200101' AND circ_mv IS NOT NULL",
        db)
    db.close()
    mv["code"] = mv["ts_code"].str[:6]
    mv = mv[["code", "trade_date", "circ_mv"]].rename(columns={"trade_date": "t0"})
    mv = mv.drop_duplicates(["code", "t0"])
    log(f"mv rows={len(mv):,}")

    # ---- 全市场 fwd10 + 市值桶基准 ----
    sub = px[px["date"] >= "20200101"][["code", "date", "fwd10"]].merge(
        mv.rename(columns={"t0": "date"}), on=["code", "date"], how="inner")
    sub = sub.dropna(subset=["fwd10", "circ_mv"])
    sub["szq"] = sub.groupby("date")["circ_mv"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    log(f"mv∪fwd 覆盖行={len(sub):,}")
    bucket_base = sub.groupby(["date", "szq"])["fwd10"].mean()

    # ---- 事件 join 市值桶 ----
    ev = ev.merge(mv, on=["code", "t0"], how="inner")
    log(f"事件有市值匹配={len(ev):,}")
    sub_idx = sub.set_index(["code", "date"])["szq"]
    ev["szq"] = [sub_idx.get((c, d), np.nan) for c, d in zip(ev["code"], ev["t0"])]
    ev = ev.dropna(subset=["szq"]).copy()
    ev["szq"] = ev["szq"].astype(int)
    ev["base"] = [bucket_base.get((d, q), np.nan) for d, q in zip(ev["t0"], ev["szq"])]
    ev = ev.dropna(subset=["base"]).copy()
    ev["ex"] = ev["fwd_ev"] - ev["base"]
    log(f"事件(size-adjusted)={len(ev):,}")

    R = {"meta": {"events_2020p_raw": None, "events_size_adj": int(len(ev))}}

    def gate(df, tag):
        df = df.copy()
        df["q"] = pd.qcut(df["ratio"].rank(method="first"), 5, labels=False)
        quint = {int(k): round(float(v), 5) for k, v in df.groupby("q")["ex"].mean().items()}
        d5 = df[df["q"] == 4].groupby("t0")["ex"].mean()
        d1 = df[df["q"] == 0].groupby("t0")["ex"].mean()
        sp = (d5 - d1).dropna()
        t1 = float(sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp)))) if len(sp) >= 30 else None
        qs = [quint[i] for i in range(5)]
        mono = sum(1 for i in range(4) if qs[4 - i] <= qs[3 - i] + 1e-12)
        # ratio 与市值桶的分布（混杂结构描述）
        big = df[df["q"] == 4]["szq"].value_counts(normalize=True).sort_index()
        R[tag] = {"quintile_mean_ex": quint,
                  "q5_minus_q1_daily_t": round(t1, 2) if t1 is not None else None,
                  "daily_n": int(len(sp)), "monotone": mono,
                  "q5_size_bucket_share": {int(k): round(float(v), 3) for k, v in big.items()},
                  "g1_pass": bool(t1 is not None and t1 <= -2.0 and mono >= 3)}
        log(f"[{tag}] quint={quint} | Q5−Q1日度t={t1} ({len(sp)}日) | 单调{mono}/4 | Q5市值桶占比={R[tag]['q5_size_bucket_share']}")

    gate(ev, "size_adjusted_2020p")
    R["verdict"] = ("PASS_解禁特异性维持(混杂已部分排除,选股层prereg可开工)"
                    if R["size_adjusted_2020p"]["g1_pass"]
                    else "FAIL_降级(小盘效应/交互,选股层prereg不开工)")
    log("verdict: " + R["verdict"])

    with open(os.path.join(OUT_DIR, "sizecheck_result.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)
    md = ["# M-2 小盘混杂排除检验（prereg 跑前冻结，窗口 2020-2026.09）", "",
          f"> 生成 2026-09-09 | prereg=data/PREREG_M2_UNLOCK_SIZECHECK_20260909.json | 事件(size-adjusted) {len(ev):,}",
          "> 口径：超额基准=同日同流通市值五分位桶等权均值；ratio 五分位重跑 Q5−Q1 日度聚类 t", "",
          "## 结果", "",
          f"- 五分位 size-adjusted 超额: {R['size_adjusted_2020p']['quintile_mean_ex']}",
          f"- Q5−Q1 日度聚类 t = {R['size_adjusted_2020p']['q5_minus_q1_daily_t']}（{R['size_adjusted_2020p']['daily_n']} 日）",
          f"- 单调性 {R['size_adjusted_2020p']['monotone']}/4",
          f"- Q5（高比例解禁）事件的市值桶占比: {R['size_adjusted_2020p']['q5_size_bucket_share']}", "",
          f"## verdict = {R['verdict']}", "",
          "---", "*风险提示：历史统计不代表未来；2016-2019 无市值数据不可检（如实声明）；本检验为 stage-2 混杂复核非生产准入。*"]
    with open(os.path.join(OUT_DIR, "SIZECHECK_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log("OK -> " + OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
