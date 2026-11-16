# -*- coding: utf-8 -*-
"""L-4 因子衰减月报（只读报告层，直跑制不挂调度；生产动作=0）
冻结口径(2026-09-09 立项, L-4 是优化清单唯一低成本运维项):
- 决策点=自然月末最后交易日(2016-01~最近完整月); 结果=fwd20(next-bar 兼容 close→close)
- 因子①bp_ep_score=PIT(ep,bp)各50%秩合成(pit_avail_date≤决策日取最新report_date; close≤30宇宙=宪法)
- 因子②ml_prob=stock_ml_scores_v22(2020+起, 样本内口径注脚)
- 月度截面 Spearman IC → 全期 IC/ICIR + 滚动6月 IC/ICIR + 分年
- 衰减旗标(冻结): ①滚动6月均值IC≤0 →「降权提示」②|滚动6月ICIR|<0.5×|全期ICIR| →「关注」; 仅报告不动作
用法: python factor_decay_monthly_report.py [--asof YYYYMM]
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

OUT_DIR = r"D:\zcodesj\reports\factor_decay_monthly"
os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def month_ends(tdays, asof=None):
    s = pd.Series(tdays)
    key = s.str[:6]
    me = s.groupby(key).max()
    if asof:
        me = me[me.index <= asof]
    return me.tolist()


def main():
    asof = None
    if len(sys.argv) > 2 and sys.argv[1] == "--asof":
        asof = sys.argv[2]

    # ---- 价格 ----
    db = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
    px = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20160101'",
        db)
    db.close()
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    px["fwd20"] = px.groupby("code")["close"].shift(-20) / px["close"] - 1.0
    tdays = np.sort(px["date"].unique())
    mes = month_ends(tdays, asof)
    log(f"月末决策点: {len(mes)} 个 ({mes[0]}~{mes[-1]})")

    close_map = px.set_index(["code", "date"])["close"]
    fwd_map = px.set_index(["code", "date"])["fwd20"]

    # ---- PIT bp/ep 可用性展开 ----
    db = sqlite3.connect(r"D:\zcodesj\factor_pit.db")
    pit = pd.read_sql(
        "SELECT code, report_date, pit_avail_date, ep, bp FROM stock_factors_fundamental_pit_v2",
        db)
    db.close()
    pit = pit.sort_values(["code", "report_date"]).reset_index(drop=True)
    log(f"PIT rows={len(pit):,}")

    rows = []
    for t in mes:
        avail = pit[pit["pit_avail_date"] <= int(t)]
        latest = avail.groupby("code").tail(1)
        sub = latest[["code", "ep", "bp"]].copy()
        sub["close"] = [close_map.get((c, t), np.nan) for c in sub["code"]]
        sub = sub.dropna(subset=["close", "ep", "bp"])
        sub = sub[sub["close"] <= 30]
        if len(sub) < 200:
            continue
        sub["bp_ep_score"] = (sub["ep"].rank(pct=True) + sub["bp"].rank(pct=True)) / 2
        sub["fwd"] = [fwd_map.get((c, t), np.nan) for c in sub["code"]]
        sub = sub.dropna(subset=["fwd"])
        rows.append({"t": t, "ic_bpep": float(sub["bp_ep_score"].corr(sub["fwd"], method="spearman")),
                     "n": int(len(sub))})
    ic_df = pd.DataFrame(rows)
    log(f"bp_ep 月度IC点: {len(ic_df)}")

    # ---- ml_prob ----
    db = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
    ml = pd.read_sql("SELECT code, date, ml_prob FROM stock_ml_scores_v22", db)
    db.close()
    ml_rows = []
    for t in mes:
        if t < "20200102":
            continue
        m = ml[ml["date"] == int(t)].copy()  # ml 表 date 为 int64
        m["close"] = [close_map.get((c, t), np.nan) for c in m["code"]]
        m = m.dropna(subset=["close"])
        m = m[m["close"] <= 30]
        m["fwd"] = [fwd_map.get((c, t), np.nan) for c in m["code"]]
        m = m.dropna(subset=["ml_prob", "fwd"])
        if len(m) < 200:
            continue
        ml_rows.append({"t": t, "ic_ml": float(m["ml_prob"].corr(m["fwd"], method="spearman")),
                        "n": int(len(m))})
    ml_df = pd.DataFrame(ml_rows)
    log(f"ml 月度IC点: {len(ml_df)}")

    def metrics(df, col):
        s = df[col].dropna()
        full_mean = float(s.mean())
        full_ir = float(s.mean() / s.std(ddof=1)) if len(s) > 2 else None
        t6 = s.tail(6)
        m6 = float(t6.mean())
        ir6 = float(t6.mean() / t6.std(ddof=1)) if len(t6) > 2 and t6.std(ddof=1) > 0 else None
        flags = []
        if m6 <= 0:
            flags.append("降权提示(滚动6月IC≤0)")
        if full_ir and ir6 is not None and abs(ir6) < 0.5 * abs(full_ir):
            flags.append("关注(滚动ICIR<0.5×全期)")
        return {"n_months": int(len(s)), "full_ic": round(full_mean, 4),
                "full_icir": round(full_ir, 3) if full_ir is not None else None,
                "roll6_ic": round(m6, 4), "roll6_icir": round(ir6, 3) if ir6 is not None else None,
                "last6_values": [round(float(x), 4) for x in t6.tolist()], "flags": flags}

    R = {"asof_month": mes[-1][:6],
         "bp_ep_score": metrics(ic_df, "ic_bpep"),
         "ml_prob_2020p": metrics(ml_df, "ic_ml"),
         "yearly": {}}
    for df, col, key in [(ic_df, "ic_bpep", "bp_ep"), (ml_df, "ic_ml", "ml")]:
        y = df.assign(year=df["t"].str[:4]).groupby("year")[col].mean()
        for yy, v in y.items():
            R["yearly"].setdefault(yy, {})[f"{key}_ic"] = round(float(v), 4)

    with open(os.path.join(OUT_DIR, f"factor_decay_{R['asof_month']}.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)

    b, m = R["bp_ep_score"], R["ml_prob_2020p"]
    md = ["# L-4 因子衰减月报（只读报告层，不触发生产动作）", "",
          f"> 生成 2026-09-09 | asof={R['asof_month']} | 口径冻结见脚本头 | 结果=fwd20 月末截面",
          f"> 因子①bp_ep_score（PIT ep/bp 秩合成，close≤30）：{b['n_months']} 个月度点",
          f"> 因子②ml_prob v22（2020+，**样本内口径注脚**）：{m['n_months']} 个月度点", "",
          "## 指标", "",
          "| 因子 | 全期IC | 全期ICIR | 滚动6月IC | 滚动6月ICIR | 旗标 |", "|---|---|---|---|---|---|",
          f"| bp_ep_score | {b['full_ic']} | {b['full_icir']} | {b['roll6_ic']} | {b['roll6_icir']} | {', '.join(b['flags']) or '无'} |",
          f"| ml_prob | {m['full_ic']} | {m['full_icir']} | {m['roll6_ic']} | {m['roll6_icir']} | {', '.join(m['flags']) or '无'} |", "",
          "## 分年月均 IC", "", "| 年 | bp_ep | ml |", "|---|---|---|"]
    for yy in sorted(R["yearly"]):
        md.append(f"| {yy} | {R['yearly'][yy].get('bp_ep_ic','-')} | {R['yearly'][yy].get('ml_ic','-')} |")
    md += ["", "## 使用边界", "",
           "- 本报告=监测层，旗标不触发任何生产降权（降权动作须主人裁决+prereg）。ml 历史数字含样本内口径（round11）。", "",
           "---", "*风险提示：历史统计不代表未来。*"]
    with open(os.path.join(OUT_DIR, "FACTOR_DECAY_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log(f"verdict bp_ep flags={b['flags']} | ml flags={m['flags']}")
    log("OK -> " + OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
