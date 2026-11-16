# -*- coding: utf-8 -*-
"""证伪复核批次6 · 独立重实现

P) ENGINE-B2-PULLBACK-R31 (R31回踩提示器事件研究)
   原档口径(SRC逐字): qfq 2013-2026, next-bar 20日, 冷却10日去重, n=16,706;
   信号=MA20上行 + ret20>=8% + 贴线(|c/ma20-1|<=3%) + 缩量;
   原档结果: mean +0.37% vs 全池基准 +0.86% -> 超额-0.49pp; NW(5) t=+0.27;
   vs 强趋势非贴线对照 增量+0.14pp/t1.1; 分年正占比43%; 胜率43.9%/中位-1.74%
   自由参数注记(档案未细化, 取常见口径并在caveat声明):
     MA20上行=ma20>ma20前5日; 缩量=vol<前5日均量; next-bar=次开入场/第20根收盘出场; 基准=全池同结算口径

数据: wb 单一真源只读
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def load_panel(start):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND substr(code,1,3) IN (%s)" % ph,
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def part_p():
    df = load_panel("20120601")
    ev_rows, base_rows, ctrl_rows = [], [], []
    for code, g in df.groupby("code", sort=False):
        n = len(g)
        if n < 280:
            continue
        o = g["open"].to_numpy(float); c = g["close"].to_numpy(float)
        v = g["volume"].to_numpy(float); d = g["date"].to_numpy()
        s = pd.Series(c)
        ma20 = s.rolling(20).mean().to_numpy()
        ma20_up = ma20 > np.concatenate([np.full(5, np.nan), ma20[:-5]])
        ret20 = c / s.shift(20).to_numpy() - 1.0
        vma5 = pd.Series(v).shift(1).rolling(5, min_periods=5).mean().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            tie = np.abs(c / ma20 - 1.0) <= 0.03
            shrink = v < vma5
        f20 = np.full(n, np.nan)
        for i in range(n - 21):
            j = i + 1
            if o[j] > 0 and np.isfinite(o[j]):
                f20[i] = c[i + 20] / o[j] - 1.0
        strong = ma20_up & (ret20 >= 0.08)
        sig = strong & tie & shrink
        last_sig = -99
        for i in range(30, n - 21):
            if d[i] < "20130101" or not np.isfinite(f20[i]):
                continue
            if sig[i]:
                if i - last_sig >= 10:  # 逐票10日冷却(原档口径)
                    last_sig = i
                    ev_rows.append((d[i], f20[i]))
                    ctrl_rows.append((d[i], f20[i]))
            elif strong[i] and i % 4 == 0:
                ctrl_rows.append((d[i], f20[i]))
            if i % 7 == 0:
                base_rows.append((d[i], f20[i]))
    ev = pd.DataFrame(ev_rows, columns=["date", "f20"])
    base = pd.DataFrame(base_rows, columns=["date", "f20"])
    ctrl = pd.DataFrame(ctrl_rows, columns=["date", "f20"])
    # 冷却10日去重(按股票内已在循环前处理? 简化: 按日序全局冷却近似会跨票误伤, 改为逐票内去重于收集时跳过)
    em = ev.groupby("date")["f20"].mean()
    bm = base.groupby("date")["f20"].mean()
    evm = float(ev["f20"].mean()); bsem = float(base["f20"].mean())
    ex = evm - bsem
    # 日聚簇 t (事件臂 vs 基准臂差近似=两臂日均值差的NW简算, 此处用日聚簇单臂t+差值报告)
    t_ev = float(em.mean() / (em.std(ddof=1) / np.sqrt(len(em)))) if len(em) > 2 else float("nan")
    pair = (em - bm).dropna()
    t_pair = float(pair.mean() / (pair.std(ddof=1) / np.sqrt(len(pair)))) if len(pair) > 2 else float("nan")
    ev["year"] = ev["date"].str[:4]
    yearly = ev.groupby("year")["f20"].mean()
    cm = ctrl.groupby("date")["f20"].mean()
    pairc = (em - cm).dropna()
    t_ctrl = float(pairc.mean() / (pairc.std(ddof=1) / np.sqrt(len(pairc)))) if len(pairc) > 2 else float("nan")
    res = {
        "id": "ENGINE-B2-PULLBACK-R31", "mode": "independent_reimpl",
        "n_events": int(len(ev)),
        "event": {"mean_pct": round(evm * 100, 3), "median_pct": round(float(ev["f20"].median()) * 100, 3),
                  "win": round(float((ev["f20"] > 0).mean()), 4)},
        "baseline_mean_pct": round(bsem * 100, 3),
        "excess_pp": round(ex * 100, 3),
        "day_cluster_t_event": round(t_ev, 2),
        "excess_daily_paired_t": round(t_pair, 2),
        "ctrl_strong_nontie_increment_pp": round(float(pairc.mean()) * 100, 3),
        "ctrl_paired_t": round(t_ctrl, 2),
        "yearly_mean_pct": {k: round(float(v2) * 100, 3) for k, v2 in yearly.items()},
        "yearly_positive_share": round(float((yearly > 0).mean()), 3),
        "caveats": ["MA20上行=ma20高于5日前; 缩量=vol<前5日均量(shift1); 冷却10日为逐票内事件节流未复刻(原档在票内去重), "
                    "事件数与原16,706的可比性受此影响; 基准=全池每7日抽样同结算口径"],
    }
    p = os.path.join(HERE, "reaudit_r31_result.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("P)", json.dumps({k2: res[k2] for k2 in ["n_events", "event", "baseline_mean_pct", "excess_pp",
                                                    "day_cluster_t_event", "excess_daily_paired_t",
                                                    "ctrl_strong_nontie_increment_pp", "ctrl_paired_t",
                                                    "yearly_positive_share"]}, ensure_ascii=False))
    return res


if __name__ == "__main__":
    part_p()
    print("done")
