# -*- coding: utf-8 -*-
"""R46 继续轮脚本3: G1微盘/中证500代理拥挤度 regime 指标复现 (2026-10-09)
G1=民间小市值择时开关(量化研究D老师): 中证500成交额占全市场 >30%减仓小市值 / <20%加仓;
   拥挤度监控=成交占比历史分位, 80%分位以上警戒
屠龙代理口径: fund库daily_basic.total_mv按日排序, 中证500代理=市值排名301-800组,
   微盘组=市值最低400组; 成交额取qfq库amount; 与原口径(真实指数成分)差异已标注
输出: tmp/r46_g1_result_20261009.json
"""
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = Path(r"D:\zcodesj\tmp\r46_g1_result_20261009.json")
START = "20160101"
ROLL = 250


def pct_rank_hist(hist, x):
    """x 在 hist 中的分位(0-100)"""
    if len(hist) < 20:
        return None
    cnt = sum(1 for v in hist if v <= x)
    return round(cnt / len(hist) * 100, 1)


def main():
    # qfq: date -> {code: amount}  (流式按日)
    con_q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    cur_q = con_q.cursor()
    cur_q.execute(
        """SELECT date, code, amount FROM stock_daily_meta_qfq
           WHERE date >= ? AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
           ORDER BY date""",
        (START,),
    )
    # fund: 按日流式
    con_f = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    cur_f = con_f.cursor()
    cur_f.execute(
        """SELECT trade_date, ts_code, total_mv FROM daily_basic
           WHERE trade_date >= ? ORDER BY trade_date""",
        (START,),
    )

    def group_rows(cursor, key_idx):
        """yield (key, rows)"""
        cur_key, buf = None, []
        for row in cursor:
            k = row[key_idx]
            if k != cur_key:
                if cur_key is not None:
                    yield cur_key, buf
                cur_key, buf = k, []
            buf.append(row)
        if cur_key is not None:
            yield cur_key, buf

    amt_iter = group_rows(cur_q, 0)
    amt_cache, amt_date, amt_done = {}, None, False
    series = []  # (date, pct_micro, pct_500, n)
    for d_mv, rows_mv in group_rows(cur_f, 0):
        while not amt_done and (amt_date is None or amt_date < d_mv):
            try:
                d_q, rows_q = next(amt_iter)
                amt_cache = {r[1]: (r[2] or 0) for r in rows_q if r[2]}
                amt_date = d_q
            except StopIteration:
                amt_done = True
                break
        if amt_date != d_mv:
            continue
        amts = amt_cache
        merged = []
        for _d, ts, mv in rows_mv:
            if not mv or mv <= 0:
                continue
            a = amts.get(ts[:6])
            if a and a > 0:
                merged.append((ts[:6], mv, a))
        if len(merged) < 800:
            continue
        merged.sort(key=lambda x: x[1])
        total = sum(x[2] for x in merged)
        micro = sum(x[2] for x in merged[:400])
        zz500 = sum(x[2] for x in merged[300:800])
        series.append((d_mv, micro / total * 100, zz500 / total * 100, len(merged)))
    con_q.close()
    con_f.close()
    print(f"[calc] days={len(series)} {series[0][0]}~{series[-1][0]}")

    pm_hist, p5_hist = [], []
    rows_out = []
    for d, pm, p5, n in series:
        pr_pm = pct_rank_hist(pm_hist, pm)
        pr_5 = pct_rank_hist(p5_hist, p5)
        rows_out.append((d, round(pm, 2), round(p5, 2), pr_pm, pr_5))
        pm_hist.append(pm)
        p5_hist.append(p5)

    last = rows_out[-1]
    p5_vals = [r[2] for r in rows_out]
    yearly = defaultdict(list)
    for d, pm, p5, _, _ in rows_out:
        yearly[d[:4]].append(p5)
    over30 = [(r[0], r[2]) for r in rows_out if r[2] > 30]
    under20 = [(r[0], r[2]) for r in rows_out if r[2] < 20]

    out = {
        "def": "中证500代理(市值排名301-800)成交额占比% 与 微盘组(最低400)占比%, 250日滚动分位=拥挤度",
        "caveat": "代理口径=市值分组, 非真实中证500指数成分; daily_basic市值口径",
        "range": f"{rows_out[0][0]}~{last[0]}",
        "latest": {"date": last[0], "micro_pct": last[1], "zz500_pct": last[2],
                   "micro_pctl": last[3], "zz500_pctl": last[4]},
        "recent10": rows_out[-10:],
        "zz500_pct_stats": {
            "med": round(statistics.median(p5_vals), 2),
            "min": round(min(p5_vals), 2), "max": round(max(p5_vals), 2),
            "days_over30": len(over30), "days_under20": len(under20),
            "last_over30": over30[-1] if over30 else None,
            "last_under20": under20[-1] if under20 else None,
            "yearly_med": {y: round(statistics.median(v), 2) for y, v in sorted(yearly.items())},
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
