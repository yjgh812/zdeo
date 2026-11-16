# -*- coding: utf-8 -*-
"""立项第②步(20261022): 稳态三因子 + 20日止损路径 TopN 模拟验证。

PREREG(跑前写死, 不可事后改):
  宇宙   = csi300(与模型训练宇宙一致; 含科创成分, 与模型对齐优先于交易宇宙边界, 特此声明)
  因子   = CORR60/STD60/MAX60(qlib Alpha158, 修复后数据), 倾向 = 三者负向(低相关/低波/低最大)
  打分   = tilt = -[rank_pct(CORR60)+rank_pct(STD60)+rank_pct(MAX60)], 取 Top20 等权
  调仓   = 每20个交易日; entry = 调仓日次一交易日收盘
  路径   = 持有至 entry 后第20根bar收盘; 期间任一日收盘 <= entry×0.92 即以该日收盘止损(镜像gauge t20e)
  基准   = 同期同路径 等权全宇宙(候选集全体)
  窗口   = OOS判定窗 2024-01→2025-12(因子选择基于2026数据, 此窗为真样本外);
           2026 窗单独列示(样本内, 不参与判定)
  通过线 = OOS 每 cohort 平均超额 ≥ +1.0%(20日) ≈ 年化+12.5%, 且止损率 < 60%
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

FEAT = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\master_feat.parquet"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FACTORS = ["CORR60", "STD60", "MAX60"]
N_PICK = 20
HOLD = 20
STOP = 0.92
OUT = r"D:\zcodesj\analysis\steady3_topn_sim_20261022_result.json"


def main():
    feat = pd.read_parquet(FEAT, columns=FACTORS)
    feat = feat[feat.index.get_level_values("datetime") >= "2023-10-01"]

    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    q = pd.read_sql_query("SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20230601' ORDER BY code,date", con)
    con.close()
    q = q[q["code"].str[0].isin(["6", "0", "3"])]
    cal = sorted(q["date"].unique())
    posd = {d: i for i, d in enumerate(cal)}
    closes = q.pivot(index="date", columns="code", values="close")  # code6 × date(str)

    # 因子表: instrument(SH600000) → code6, 日期对齐到 qfq 日历(字符串)
    f = feat.reset_index()
    f["code"] = f["instrument"].str[-6:]
    f["d"] = f["datetime"].astype(str).str[:10].str.replace("-", "")
    f = f[f["d"].isin(posd)]

    # 调仓日: 日历上每20个交易日
    win_all = [d for d in cal if "20240101" <= d <= "20260930"]
    rebal = win_all[::HOLD]

    cohorts = []
    for t in rebal:
        i = posd[t]
        if i + 2 >= len(cal):
            continue
        ft = f[f["d"] == t]
        if len(ft) < 60:
            continue
        r = ft[FACTORS].rank(pct=True)
        ft = ft.assign(tilt=-(r["CORR60"] + r["STD60"] + r["MAX60"]))
        cand = ft.sort_values("tilt", ascending=False)
        entry_d = cal[i + 1]
        picks, skipped = [], 0
        for row in cand.itertuples():
            if len(picks) >= N_PICK:
                break
            c6 = row.code
            if c6 not in closes.columns:
                skipped += 1
                continue
            e = closes.at[entry_d, c6] if entry_d in closes.index else np.nan
            if not (e == e) or e <= 0:
                skipped += 1
                continue
            picks.append((c6, float(e)))
        if len(picks) < 10:
            continue
        # 路径: entry日=cal[i+1]; 持有至 +HOLD 根bar(相对entry日), 止损检查自 entry+1 bar
        start_i = i + 1
        end_i = min(start_i + HOLD, len(cal) - 1)
        rets, stops, holds = [], [], []
        for c6, e in picks:
            exit_px, exit_i, stopped = e, start_i, False
            for k in range(1, end_i - start_i + 1):
                di = start_i + k
                px = closes.at[cal[di], c6] if cal[di] in closes.index else np.nan
                if px != px:
                    continue
                if px <= e * STOP:
                    exit_px, exit_i, stopped = px, di, True
                    break
                exit_px, exit_i = px, di
            rets.append(exit_px / e - 1)
            holds.append(exit_i - start_i)
            stops.append(stopped)
        # 基准: 全候选等权同路径(取cand全体可交易者, 上限300)
        base_rets = []
        for row in cand.itertuples():
            c6 = row.code
            if c6 not in closes.columns:
                continue
            e = closes.at[entry_d, c6] if entry_d in closes.index else np.nan
            if not (e == e) or e <= 0:
                continue
            exit_px, exit_i, stopped = e, start_i, False
            for k in range(1, end_i - start_i + 1):
                di = start_i + k
                px = closes.at[cal[di], c6] if cal[di] in closes.index else np.nan
                if px != px:
                    continue
                if px <= e * STOP:
                    exit_px, exit_i, stopped = px, di, True
                    break
                exit_px, exit_i = px, di
            base_rets.append(exit_px / e - 1)
        cohorts.append({
            "entry": entry_d,
            "n": len(rets),
            "mean_ret": float(np.mean(rets)),
            "win": float(np.mean(np.array(rets) > 0)),
            "stop_rate": float(np.mean(stops)),
            "avg_hold": float(np.mean(holds)),
            "base_mean": float(np.mean(base_rets)) if base_rets else np.nan,
            "excess": float(np.mean(rets) - np.mean(base_rets)) if base_rets else np.nan,
        })

    cd = pd.DataFrame(cohorts)
    oos = cd[(cd["entry"] >= "20240101") & (cd["entry"] <= "20251231")]
    is26 = cd[cd["entry"] >= "20260101"]

    def summ(w, name):
        if len(w) == 0:
            return {"n_cohorts": 0}
        ann_excess = w["excess"].mean() * (250 / HOLD)
        return {
            "n_cohorts": int(len(w)),
            "mean_ret_20d": round(w["mean_ret"].mean(), 5),
            "mean_excess_20d": round(w["excess"].mean(), 5),
            "ann_excess": round(float(ann_excess), 4),
            "win": round(w["win"].mean(), 4),
            "stop_rate": round(w["stop_rate"].mean(), 4),
            "avg_hold": round(w["avg_hold"].mean(), 1),
            "pass": bool(w["excess"].mean() >= 0.01 and w["stop_rate"].mean() < 0.60),
        }

    result = {
        "probe": "steady3_topn", "date": "2026-10-22",
        "prereg": "cosi300(声明:与模型宇宙对齐)/三因子负向/Top20/20bar/×0.92收盘止损/等权全宇宙基准",
        "pass_line": "OOS 20日平均超额 ≥ +1.0% 且 止损率<60%",
        "OOS_2024_2025": summ(oos, "OOS"),
        "IS_2026": summ(is26, "IS"),
        "cohorts": cohorts,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}\n")
    for tag, w in (("OOS 2024-2025(判定窗)", oos), ("IS 2026(样本内)", is26)):
        s = summ(w, tag)
        if s["n_cohorts"] == 0:
            print(f"{tag}: 无cohort")
            continue
        print(f"{tag}: cohorts={s['n_cohorts']} 20日均收={s['mean_ret_20d']:+.2%} "
              f"超额={s['mean_excess_20d']:+.2%}(年化{s['ann_excess']:+.1%}) "
              f"胜率={s['win']:.0%} 止损率={s['stop_rate']:.0%} 均持={s['avg_hold']}日 "
              f"→ {'✅通过' if s['pass'] else '❌不通过'}")
    print("\n逐cohort(OOS):")
    for _, r in oos.iterrows():
        print(f"  entry {r['entry']}: 组合{r['mean_ret']:+.2%} 基准{r['base_mean']:+.2%} "
              f"超额{r['excess']:+.2%} 止损{r['stop_rate']:.0%}")


if __name__ == "__main__":
    sys.exit(main())
