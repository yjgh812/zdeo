# -*- coding: utf-8 -*-
"""诊断⑤ 边界同分组内 入选 vs 未入选 事后对比 (2026-10-18 继续令; 外部方案审计B第二档·序3-1 深化)

问题: 生产 Top15 边界同分时由 pandas 帧顺序(无语义顺位)决定谁入选。若帧序对同分组
成员的事后表现无差别 → tie-break 真中性(草案 tie-break 无先验收益); 若帧序隐性偏向
(入选者系统性更好/更差) → tie-break 有真实修正空间。
方法: fork codedump 逐期 dump 边界同分组名单(sel/nonsel); 对全部组内票用 qfq 算
统一口径 fwd20(=T+1 开盘买 → 第20个交易日收盘, 与引擎 next-bar 同构, 不含费用/止损,
纯信号对比); 每期算 sel均值-nonsel均值 配对差, 期级汇总。
口径: 毛收益无费用; 样本=2026-10-18 基线的 119 个调仓期; 结果为诊断产出非接线依据。
"""
import json
import sqlite3
from bisect import bisect_left

import numpy as np
import pandas as pd

QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
CD = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15_cd\dose_boundary_codes_top15.csv"
OUT_JSON = r"D:\zcodesj\analysis\tied_group_afterdiag_20261018_out.json"
OUT_MD = r"D:\zcodesj\analysis\tied_group_afterdiag_20261018_out.md"

con = sqlite3.connect(QFQ, uri=True)
cur = con.cursor()
_cache = {}


def series(code6):
    if code6 not in _cache:
        cur.execute("SELECT date, open, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code6,))
        rows = cur.fetchall()
        _cache[code6] = ([r[0] for r in rows], rows)
    return _cache[code6]


def fwd20(code6, sel_date):
    """sel_date=信号日(YYYY-MM-DD); T+1 开盘买, 持有20个交易日收盘卖; 数据不足返回 None。"""
    d8 = sel_date.replace("-", "")
    dates, rows = series(code6)
    i = bisect_left(dates, d8)
    if i >= len(dates) or dates[i] != d8:
        i = i - 1 if i > 0 else 0
    entry = i + 1
    if entry >= len(dates):
        return None
    exit_i = entry + 19
    if exit_i >= len(rows) or not rows[entry][1] or not rows[exit_i][2]:
        return None
    op, cl = rows[entry][1], rows[exit_i][2]
    if not op:
        return None
    return (cl / op - 1.0) * 100


def main():
    t = pd.read_csv(CD)
    events = t[t["n_nonsel"] > 0]
    pair_diffs, all_sel, all_nonsel, skipped = [], [], [], 0
    per_date = []
    for _, r in events.iterrows():
        sel = [c for c in str(r["codes_sel"]).split(";") if c]
        non = [c for c in str(r["codes_nonsel"]).replace(" ", "").split(";") if c]
        fs = [f for f in (fwd20(c, r["date"]) for c in sel) if f is not None]
        fn = [f for f in (fwd20(c, r["date"]) for c in non) if f is not None]
        skipped += (len(sel) - len(fs)) + (len(non) - len(fn))
        all_sel.extend(fs)
        all_nonsel.extend(fn)
        if fs and fn:
            pair_diffs.append(float(np.mean(fs)) - float(np.mean(fn)))
            per_date.append(dict(date=r["date"], sel=[round(x, 2) for x in fs],
                                 nonsel=[round(x, 2) for x in fn],
                                 diff=round(float(np.mean(fs)) - float(np.mean(fn)), 2)))

    def summ(xs):
        if not xs:
            return dict(n=0)
        return dict(n=len(xs), mean=round(float(np.mean(xs)), 3), med=round(float(np.median(xs)), 3),
                    win=round(float(np.mean([x > 0 for x in xs])) * 100, 1))

    d = np.array(pair_diffs) if pair_diffs else np.array([0.0])
    out = dict(
        n_dates_total=int(len(t)), n_dates_with_nonsel=int(len(events)),
        n_sel_leg=summ(all_sel), n_nonsel_leg=summ(all_nonsel),
        skipped_no_data=skipped,
        paired=dict(n=len(pair_diffs), mean_diff=round(float(d.mean()), 3),
                    med_diff=round(float(np.median(d)), 3),
                    pos_dates=int((d > 0).sum()), neg_dates=int((d < 0).sum()),
                    zero_dates=int((d == 0).sum()),
                    ci_boot=[round(x, 3) for x in np.percentile(
                        np.random.default_rng(42).choice(d, (2000, len(d)), replace=True).mean(axis=1),
                        [2.5, 97.5])] if len(pair_diffs) >= 5 else None),
        per_date=per_date)

    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lines = ["# 诊断⑤ 边界同分组内 入选 vs 未入选 事后对比 (2026-10-18 继续令)", "",
             "> 口径: fwd20=T+1开盘买→第20交易日收盘卖(qfq毛收益, 无费用无止损, 纯信号对比);",
             f"> 期数: {out['n_dates_total']} 期中 {out['n_dates_with_nonsel']} 期存在边界同分未入选票; 数据不足跳过 {skipped} 腿。", ""]
    lines.append(f"- 入选腿: {json.dumps(out['n_sel_leg'], ensure_ascii=False)}")
    lines.append(f"- 未入选腿: {json.dumps(out['n_nonsel_leg'], ensure_ascii=False)}")
    lines.append(f"- 期级配对差(sel均-非sel均): {json.dumps(out['paired'], ensure_ascii=False)}")
    lines.append("")
    lines.append("| 日期 | 入选腿fwd20% | 未入选腿fwd20% | 差pp |")
    lines.append("|---|---|---|---|")
    for p in per_date:
        lines.append(f"| {p['date']} | {p['sel']} | {p['nonsel']} | {p['diff']} |")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("OUT:", OUT_MD)
    print(json.dumps({k: v for k, v in out.items() if k != "per_date"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
