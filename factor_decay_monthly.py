# -*- coding: utf-8 -*-
"""L-4 因子衰减监控月报（优化清单长期项，只读报告层）
数据源: attribution.db factor_daily_ic（日频 IC/ICIR/decay_alert, 6 因子）
产物: reports/factor_decay_monthly_{YYYYMM}.md
分级: healthy=近月ICIR>=0.3且critical占比<10% | watch=近月ICIR 0~0.3或critical占比10~30% | decay=近月ICIR<0或IC均值跌破全史50%或critical占比>=30%
边界: 只读零接线零信号权; cron 挂载待主人裁决; bp_ep 为生产白名单双因子, 衰减仅注记不拦截。
验收(优化清单原文): 月度报告产出且与 health_check_daily 不冲突。
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(r"D:\zcodesj")
DB = BASE / "attribution" / "attribution.db"
OUT_DIR = BASE / "reports"

GRADE_ICON = {"healthy": "🟢", "watch": "🟡", "decay": "🔴"}


def load() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql("SELECT * FROM factor_daily_ic ORDER BY date", con)
    finally:
        con.close()
    if df.empty:
        raise SystemExit("factor_daily_ic 为空, 无法产出月报 (数据源不可用, 不做推演)")
    df["month"] = df["date"].str[:6]
    return df


def grade(ic_mean_recent, ic_mean_all, icir_recent, crit_ratio):
    if pd.isna(icir_recent) or pd.isna(ic_mean_recent):
        return "watch"
    if icir_recent < 0 or ic_mean_recent < 0.5 * ic_mean_all or crit_ratio >= 0.30:
        return "decay"
    if icir_recent < 0.3 or crit_ratio >= 0.10:
        return "watch"
    return "healthy"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=None, help="截至月份 YYYYMM, 默认数据中最后一个完整/当前月")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    df = load()
    last_month = args.month or df["month"].max()
    sub = df[df["month"] <= last_month]
    all_mean = sub.groupby("factor_name")["ic_1d"].mean()
    recent = sub[sub["month"] == last_month]

    rows = []
    for fac, g in sub.groupby("factor_name"):
        rg = recent[recent["factor_name"] == fac]
        ic_all = all_mean[fac]
        ic_recent = rg["ic_1d"].mean()
        icir_recent = (rg["ic_1d"].mean() / rg["ic_1d"].std()) if len(rg) >= 5 and rg["ic_1d"].std() > 0 else float("nan")
        crit_ratio = (rg["decay_alert"] == "critical").mean() if len(rg) else float("nan")
        pos_ratio = (rg["ic_1d"] > 0).mean() if len(rg) else float("nan")
        g_grade = grade(ic_recent, ic_all, icir_recent, crit_ratio)
        rows.append({
            "factor": fac, "grade": g_grade,
            "ic_recent": ic_recent, "ic_all": ic_all, "retention": (ic_recent / ic_all) if ic_all else float("nan"),
            "icir_recent": icir_recent, "pos_ratio": pos_ratio, "crit_days": int((rg["decay_alert"] == "critical").sum()),
            "n_days": len(rg), "n_stocks_med": rg["n_stocks"].median(),
        })

    out = OUT_DIR / f"factor_decay_monthly_{last_month}.md"
    L = []
    A = L.append
    A(f"# L-4 因子衰减监控月报 · {last_month}")
    A("")
    A(f"> 生成: {datetime.now():%Y-%m-%d %H:%M} | 数据源: attribution.db factor_daily_ic（{df['date'].min()}~{df['date'].max()}, {len(df)} 行, {df['factor_name'].nunique()} 因子）")
    A("> 口径: 近月=当月已结算日; 全史=表内全窗口; 分级=healthy🟢(近月ICIR>=0.3且critical<10%) / watch🟡(ICIR 0~0.3或critical 10~30%) / decay🔴(ICIR<0 或 IC跌破全史50% 或 critical>=30%)")
    A("> 边界: 只读报告层, 零接线零信号权; bp_ep=生产白名单双因子, 衰减仅注记不拦截; cron 挂载待主人裁决")
    A("")
    A("## 本月因子健康度")
    A("")
    A("| 因子 | 分级 | 近月IC均值 | 全史IC均值 | 保留率 | 近月ICIR | IC>0占比 | critical天数/样本日 | 日均覆盖股数 |")
    A("|---|---|---|---|---|---|---|---|---|")
    order = {"decay": 0, "watch": 1, "healthy": 2}
    for r in sorted(rows, key=lambda x: (order[x["grade"]], x["factor"])):
        ret = f"{r['retention']*100:.0f}%" if pd.notna(r["retention"]) else "N/A"
        icir = f"{r['icir_recent']:.2f}" if pd.notna(r["icir_recent"]) else "N/A"
        pos = f"{r['pos_ratio']*100:.0f}%" if pd.notna(r["pos_ratio"]) else "N/A"
        A(f"| {r['factor']} | {GRADE_ICON[r['grade']]} {r['grade']} | {r['ic_recent']:+.4f} | {r['ic_all']:+.4f} | {ret} | {icir} | {pos} | {r['crit_days']}/{r['n_days']} | {r['n_stocks_med']:.0f} |")
    A("")

    decayed = [r for r in rows if r["grade"] == "decay"]
    A("## 衰减注记（只读, 不拦截）")
    A("")
    if decayed:
        for r in decayed:
            note = "（生产白名单双因子之一, 名单质量风险提示: 若连续两月 decay 建议提请主人评估名单供给权重）" if r["factor"] == "bp_ep" else ""
            A(f"- 🔴 **{r['factor']}**: 近月 IC 保留率 {(r['retention']*100 if pd.notna(r['retention']) else 0):.0f}%, ICIR {r['icir_recent']:.2f}, critical {r['crit_days']}/{r['n_days']} 日 {note}")
    else:
        A("- 本月无 decay 级因子。")
    watches = [r for r in rows if r["grade"] == "watch"]
    if watches:
        A("- 🟡 watch 级: " + ", ".join(f"{r['factor']}(ICIR {r['icir_recent']:.2f})" if pd.notna(r['icir_recent']) else f"{r['factor']}(样本不足)" for r in watches))
    A("")

    A("## 全窗口月度趋势（ic_1d 月均值）")
    A("")
    piv = sub.pivot_table(index="month", columns="factor_name", values="ic_1d", aggfunc="mean")
    A("| 月份 | " + " | ".join(sorted(piv.columns)) + " |")
    A("|---|" + "---|" * len(piv.columns))
    for m, row in piv.iterrows():
        cells = " | ".join(f"{row[c]:+.4f}" if pd.notna(row[c]) else "N/A" for c in sorted(piv.columns))
        A(f"| {m} | {cells} |")
    A("")
    A("## 风险提示")
    A("")
    A("本报告为描述性监控统计, 不构成因子调整依据; 因子权重/名单规则改动须过 wfa_gate 一票制+预注册; 历史因子表现不代表未来。")
    A("")

    out.write_text("\n".join(L), encoding="utf-8")
    print(f"OK -> {out}")
    for r in sorted(rows, key=lambda x: (order[x["grade"]], x["factor"])):
        print(f"  {GRADE_ICON[r['grade']]} {r['factor']}: ICIR={r['icir_recent']:.2f} retention={(r['retention']*100 if pd.notna(r['retention']) else float('nan')):.0f}%")


if __name__ == "__main__":
    main()
