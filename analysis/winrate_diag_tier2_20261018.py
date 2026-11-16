# -*- coding: utf-8 -*-
"""winrate_diag_tier2_20261018.py — 胜率方案第二档诊断件 (2026-10-18, 四项诊断性回测·纯分析零接线)

D1 rank剂量曲线: 两腿Top20实现池内按score排名切 N=3/5/8/10/15/20, 交易胜率剂量单调性
D2 负先验前置过滤诊断: 爆量态(vr120>=1.5∧ret20>=20%)与乖离MA20>=20% 两维在买点的分布,
   被过滤组vs保留组的实现胜率对比 (其余三禁区急追/裸突破/贴死底无钉死量化定义, 如实不实现)
D3 分年胜率×盈亏比分解: 直答「2026负年=胜率下降还是盈亏比恶化」
D4 边界同分检查: 每期N边界处score同分频率
附发现: 两腿回测逐笔标签均为「Top20」——与材料①§7「生产bp_ep=Top15」存在N口径漂移(如实入档)。
口径: 交易胜率=盈亏比例%>0(含止损费用, 交易轨); 诊断性回测不接线, 接线须各自prereg。
输出: analysis/winrate_diag_tier2_20261018_out.md + .json
"""
import json
import re
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
CSV_DIR = r"D:\zcodesj\reports\backtest_full_20261016_eod"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_MD = r"D:\zcodesj\analysis\winrate_diag_tier2_20261018_out.md"
OUT_JSON = r"D:\zcodesj\analysis\winrate_diag_tier2_20261018_out.json"
NS = [3, 5, 8, 10, 15, 20]


def load_arm(fn, pat):
    df = pd.read_csv(CSV_DIR + "\\" + fn, encoding="utf-8-sig")
    pcts = pd.to_numeric(df["盈亏比例%"], errors="coerce")
    df = df[pcts.notna()].copy()
    df["pct"] = pcts.loc[df.index]
    df["score"] = df["买入决策理由"].str.extract(pat)[0].astype(float)
    df["date"] = df["买入日期"].astype(str)
    df["code6"] = df["股票代码"].astype(str).str[-6:].str.zfill(6)
    df["win"] = (df["pct"] > 0).astype(int)
    return df


def rank_dose(df):
    """每期按score降序排名, 切N的剂量曲线(交易胜率+均值pct)"""
    df = df.sort_values(["date", "score"], ascending=[True, False]).reset_index(drop=True)
    df["rank"] = df.groupby("date").cumcount() + 1
    rows = []
    for n in NS:
        sub = df[df["rank"] <= n]
        rows.append({"N": n, "n_trades": int(len(sub)),
                     "win": round(float(sub["win"].mean()) * 100, 2),
                     "mean_pct": round(float(sub["pct"].mean()), 2),
                     "med_pct": round(float(sub["pct"].median()), 2)})
    return rows


def main():
    bp = load_arm("bp_ep_core_trades_full.csv", r"score=([0-9.]+)")
    ml = load_arm("ml_signal_trades_full.csv", r"ml_prob=([0-9.]+)")

    # 附发现: 每期持仓数分布(标签Top20 vs 生产Top15)
    per_date_bp = bp.groupby("date").size()
    md_hdr = ["# 胜率第二档诊断件（D1剂量/D2前置过滤/D3分年分解/D4边界同分）", "",
              "> 脚本 analysis/winrate_diag_tier2_20261018.py 直出 | 口径=交易轨(盈亏比例%>0, 含止损费用) | 诊断性回测零接线",
              "> 🔴附带发现: 两腿回测逐笔标签均为「Top20」, 与材料①§7「生产bp_ep=Top15」存在N口径漂移(见D0)", ""]
    cnt = per_date_bp.value_counts()
    md_hdr.append(f"- bp_ep 每期持仓数分布: {dict(sorted(cnt.items()))} (共{per_date_bp.index.nunique()}期)")
    cnt_m = ml.groupby("date").size().value_counts()
    md_hdr.append(f"- ml 每期持仓数分布: {dict(sorted(cnt_m.items()))} (共{ml['date'].nunique()}期)")

    # D1 剂量曲线
    d1_bp, d1_ml = rank_dose(bp), rank_dose(ml)

    # D2 前置过滤诊断: qfq面板算买点 vr120/ret20/bias20
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    panel = pd.read_sql(
        "SELECT code,date,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>='20150601' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
        "AND substr(code,1,2) NOT IN ('42','83','87','92')", con)
    con.close()
    panel = panel[(panel["volume"] > 0)].copy()
    panel["date"] = panel["date"].astype(str)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    g = panel.groupby("code", sort=False)
    panel["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    panel["ma120v"] = g["volume"].transform(lambda s: s.shift(1).rolling(120, min_periods=90).mean())
    panel["close20a"] = g["close"].transform(lambda s: s.shift(20))
    panel["vr120"] = panel["volume"] / panel["ma120v"]
    panel["ret20"] = panel["close"] / panel["close20a"] - 1
    panel["bias20"] = panel["close"] / panel["ma20"] - 1
    panel["code6"] = panel["code"].str[-6:]
    ind = panel.set_index(["code6", "date"])[["vr120", "ret20", "bias20"]]

    diag_rows = []
    diag_detail = {}
    for arm, d in (("bp_ep", bp), ("ml", ml)):
        keys = list(zip(d["code6"], d["date"].str.replace("-", "", regex=False)))
        d = d.assign(k=[f"{a}|{b}" for a, b in keys])
        ind_l = ind.copy()
        ind_l.index = [f"{a}|{b}" for a, b in ind_l.index]
        sub = ind_l.reindex(d["k"])
        d = d.assign(vr120=sub["vr120"].to_numpy(),
                     ret20=sub["ret20"].to_numpy(), bias20=sub["bias20"].to_numpy())
        has = d["vr120"].notna() & d["bias20"].notna()
        dd = d[has].copy()
        dd["爆量态"] = (dd["vr120"] >= 1.5) & (dd["ret20"] >= 0.20)
        dd["乖离20"] = dd["bias20"] >= 0.20
        dd["负先验任一"] = dd["爆量态"] | dd["乖离20"]
        kept = dd[~dd["负先验任一"]]
        filtered = dd[dd["负先验任一"]]
        diag_rows.append({"arm": arm, "coverage": round(float(has.mean()) * 100, 1),
                          "n_kept": int(len(kept)), "win_kept": round(float(kept["win"].mean()) * 100, 2),
                          "n_filtered": int(len(filtered)), "win_filtered": round(float(filtered["win"].mean()) * 100, 2),
                          "爆量_rate": round(float(dd["爆量态"].mean()) * 100, 1),
                          "乖离_rate": round(float(dd["乖离20"].mean()) * 100, 1)})
        diag_detail[arm] = {"kept_win": round(float(kept["win"].mean()) * 100, 2),
                            "filtered_win": round(float(filtered["win"].mean()) * 100, 2) if len(filtered) else None}

    # D3 分年胜率×盈亏比分解
    d3 = {}
    for arm, d in (("bp_ep", bp), ("ml", ml)):
        d = d.assign(year=d["date"].str[:4])
        rows = []
        for y, sub in d.groupby("year"):
            wins = sub[sub["pct"] > 0]["pct"]
            losses = sub[sub["pct"] <= 0]["pct"]
            rows.append({"year": y, "n": int(len(sub)),
                         "win": round(float(sub["win"].mean()) * 100, 1),
                         "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
                         "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
                         "net_mean": round(float(sub["pct"].mean()), 2)})
        d3[arm] = rows

    # D4 边界同分: 每期rank15处与rank14的score相等频率
    ties = {}
    for arm, d in (("bp_ep", bp), ("ml", ml)):
        d = d.sort_values(["date", "score"], ascending=[True, False]).reset_index(drop=True)
        d["rank"] = d.groupby("date").cumcount() + 1
        prev15 = d[(d["rank"] == 14)].set_index("date")["score"]
        t15v = d[(d["rank"] == 15)].set_index("date")["score"]
        common = t15v.index.intersection(prev15.index)
        tie15 = round(float((t15v.loc[common] == prev15.loc[common]).mean()) * 100, 1) if common.size else None
        ties[arm] = {"边界15同分率%": tie15,
                     "score小数位样例": f"{t15v.iloc[0]:.6f}" if len(t15v) else None}

    out = {"d0_label_finding": "回测标签Top20 vs 生产Top15 N口径漂移(如实入档, 详见D1剂量表)",
           "d1_bp_ep": d1_bp, "d1_ml": d1_ml, "d2": diag_rows, "d2_detail": diag_detail,
           "d3_yearly": d3, "d4_ties": ties}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    md = md_hdr + ["", "## D1 rank剂量曲线（实现池内按score切N, 交易胜率/均值pct）", "",
                   "**bp_ep**", "", "| N | n | 交易胜率 | 均值pct | 中位pct |", "|---|---|---|---|---|"]
    for r in d1_bp:
        md.append(f"| {r['N']} | {r['n_trades']} | {r['win']}% | {r['mean_pct']:+.2f}% | {r['med_pct']:+.2f}% |")
    md += ["", "**ml**", "", "| N | n | 交易胜率 | 均值pct | 中位pct |", "|---|---|---|---|---|"]
    for r in d1_ml:
        md.append(f"| {r['N']} | {r['n_trades']} | {r['win']}% | {r['mean_pct']:+.2f}% | {r['med_pct']:+.2f}% |")
    md += ["", "## D2 负先验前置过滤诊断（爆量态/乖离MA20≥20% 两维, 买点截面）", "",
           "| 臂 | 买点指标覆盖 | 保留n | 保留胜率 | 被滤n | 被滤胜率 | 爆量占比 | 乖离占比 |", "|---|---|---|---|---|---|---|---|"]
    for r in diag_rows:
        md.append(f"| {r['arm']} | {r['coverage']}% | {r['n_kept']} | {r['win_kept']}% | {r['n_filtered']} | {r['win_filtered']}% | {r['爆量_rate']}% | {r['乖离_rate']}% |")
    md += ["", "## D3 分年胜率×盈亏比分解（直答: 2026负年=胜率还是盈亏比）", "",
           "**bp_ep**", "", "| 年 | n | 胜率 | 平均盈利 | 平均亏损 | 净均值 |", "|---|---|---|---|---|---|"]
    for r in d3["bp_ep"]:
        md.append(f"| {r['year']} | {r['n']} | {r['win']}% | {r['avg_win']}% | {r['avg_loss']}% | {r['net_mean']:+.2f}% |")
    md += ["", "**ml**", "", "| 年 | n | 胜率 | 平均盈利 | 平均亏损 | 净均值 |", "|---|---|---|---|---|---|"]
    for r in d3["ml"]:
        md.append(f"| {r['year']} | {r['n']} | {r['win']}% | {r['avg_win']}% | {r['avg_loss']}% | {r['net_mean']:+.2f}% |")
    md += ["", "## D4 边界同分检查", ""]
    for arm, t in ties.items():
        md.append(f"- {arm}: 边界15与rank14同分频率 {t['边界15同分率%']}%（score小数位样例 {t['score小数位样例']}）")
    md += ["", "## 判读边界", "",
           "- 本件=诊断性回测零接线: D1剂量/D2过滤若显示可取, **接线均须各自 prereg + 主人裁决**(分析自由≠接线自由);",
           "- D1 为实现池内 rank 切片(每期≤20只已选池), 不能外推池外(如 Top30);",
           "- D2 仅实现两维可量化禁区(急追/裸突破/贴死底无钉死量化定义, 如实不实现); 实盘R闸已在执行层拦截部分负先验, 本诊断为回测口径;",
           "- D0 附带发现(N口径漂移)须主人知悉: 回测基线数字的N口径与生产配置可能不一致, 建议核对回测引擎selection参数。"]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[OK] D1 bp_ep: " + " / ".join(f"N{r['N']}={r['win']}%" for r in d1_bp))
    print(f"[OK] D1 ml: " + " / ".join(f"N{r['N']}={r['win']}%" for r in d1_ml))
    print(f"[OK] D2: " + json.dumps(diag_rows, ensure_ascii=False)[:200])
    print(f"[OK] 已直出 {OUT_MD}")


if __name__ == "__main__":
    sys.exit(main())
