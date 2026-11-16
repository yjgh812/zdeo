# -*- coding: utf-8 -*-
"""R46 继续轮脚本2: F2情绪周期三确认窗 + G2日历风控月 复现 (2026-10-09)
F2=民间情绪周期闭式参数复现(巨拳手绘图解): ①主跌期"三天必见新低" ②主升期"三天必创新高"
  ③放量阳线共振方向=潜在主线(指数层版; 行业层含在G1外简化为指数层, 标注口径)
G2=鳄鱼"日历风控月"主张: 全市场等权 1-2月/4月 vs 其他月份 (2016-2026)
口径: qfq库全市场等权指数(剔科创/北交/B股, 60/00/30开头), 收盘价口径
输出: tmp/r46_f2_g2_result_20261009.json
"""
import json
import statistics
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = Path(r"D:\zcodesj\tmp\r46_f2_g2_result_20261009.json")
START = "20160101"
W = 60


def med(xs):
    return round(statistics.median(xs), 5) if xs else None


def mean(xs):
    return round(sum(xs) / len(xs), 5) if xs else None


def main():
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute(
        """SELECT date, AVG(open), AVG(close), SUM(amount), COUNT(*), AVG(change_pct)
           FROM stock_daily_meta_qfq
           WHERE date >= ? AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
           GROUP BY date ORDER BY date""",
        (START,),
    )
    dates, opens, closes, amounts, rets = [], [], [], [], []
    for d, o, c, amt, n, pct in cur:
        dates.append(d)
        opens.append(o)
        closes.append(c)
        amounts.append(amt or 0)
        rets.append((pct or 0) / 100)
    con.close()
    n_days = len(dates)
    print(f"[load] eq-index days={n_days} {dates[0]}~{dates[-1]}")

    # ---- F2-a: 主跌确认窗: 创60日新低且回撤>=10% -> 后3日内是否见更低收盘 ----
    dd_hits, dd_years = [], defaultdict(list)
    # ---- F2-b: 主升确认窗: 创60日新高且反弹>=10% -> 后3日内是否见更高收盘 ----
    up_hits, up_years = [], defaultdict(list)
    for i in range(W, n_days - 3):
        win = closes[i - W:i]
        hi, lo = max(win), min(win)
        dd = (hi - closes[i]) / hi
        rb = (closes[i] - lo) / lo
        if closes[i] < min(win) and dd >= 0.10:
            hit = any(closes[i + k] < closes[i] for k in (1, 2, 3))
            dd_hits.append(hit)
            dd_years[dates[i][:4]].append(1 if hit else 0)
        if closes[i] > max(win) and rb >= 0.10:
            hit = any(closes[i + k] > closes[i] for k in (1, 2, 3))
            up_hits.append(hit)
            up_years[dates[i][:4]].append(1 if hit else 0)

    # ---- F2-c: 指数放量阳线 -> fwd20 (vs 全样本基线, 含分年) ----
    pa_fwd, other_fwd = [], []
    base_fwd = []
    pa_years = defaultdict(list)
    for i in range(20, n_days - 20):
        f20 = closes[i + 20] / closes[i] - 1
        base_fwd.append(f20)
        vol_ok = amounts[i] >= 1.5 * (sum(amounts[i - 20:i]) / 20)
        up_ok = closes[i] > opens[i]
        if vol_ok and up_ok:
            pa_fwd.append(f20)
            pa_years[dates[i][:4]].append(f20 * 100)
        else:
            other_fwd.append(f20)

    # ---- G2: 日历月 (等权几何月收益) ----
    monthly = {}
    for d, r in zip(dates, rets):
        k = d[:6]
        monthly[k] = monthly.get(k, 1.0) * (1 + r)
    monthly = {k: (v - 1) * 100 for k, v in monthly.items()}  # %
    by_month = defaultdict(list)
    for k, v in monthly.items():
        by_month[int(k[4:6])].append((k, v))
    cal = {}
    for m in range(1, 13):
        vs = [v for _, v in sorted(by_month.get(m, []))]
        cal[f"{m:02d}"] = {"n": len(vs), "med": med(vs), "mean": mean(vs),
                           "win": round(sum(1 for x in vs if x > 0) / len(vs), 3) if vs else None}
    hi224 = [v for k, v in monthly.items() if int(k[4:6]) in (1, 2, 4)]
    rest = [v for k, v in monthly.items() if int(k[4:6]) not in (1, 2, 4)]
    jan_apr = {k: round(v, 2) for k, v in sorted(monthly.items()) if int(k[4:6]) in (1, 2, 4)}

    out = {
        "f2a_drawdown_3day_newlow": {
            "def": "创60日新低且回撤>=10%的事件日后3日内再创新低(收盘口径)比例",
            "n_events": len(dd_hits),
            "hit_ratio": round(sum(dd_hits) / len(dd_hits), 4) if dd_hits else None,
            "yearly": {y: round(sum(v) / len(v), 3) for y, v in sorted(dd_years.items())},
        },
        "f2b_rally_3day_newhigh": {
            "def": "创60日新高且反弹>=10%的事件日后3日内再创新高(收盘口径)比例",
            "n_events": len(up_hits),
            "hit_ratio": round(sum(up_hits) / len(up_hits), 4) if up_hits else None,
            "yearly": {y: round(sum(v) / len(v), 3) for y, v in sorted(up_years.items())},
        },
        "f2c_volume_surge_up_bar": {
            "def": "等权指数放量阳线(amount>=1.5x20日均 且 close>open)后fwd20 vs 其余日",
            "n_pa": len(pa_fwd),
            "fwd20_pa_med": med(pa_fwd), "fwd20_pa_mean": mean(pa_fwd),
            "fwd20_other_med": med(other_fwd),
            "fwd20_base_med": med(base_fwd),
            "pa_win": round(sum(1 for x in pa_fwd if x > 0) / len(pa_fwd), 4) if pa_fwd else None,
            "pa_yearly_med": {y: med(v) for y, v in sorted(pa_years.items())},
        },
        "g2_calendar_month": {
            "def": "全市场等权月收益% by 月份, 2016-2026",
            "by_month": cal,
            "jan_feb_apr_med": med(hi224), "rest_med": med(rest),
            "jan_feb_apr_mean": mean(hi224), "rest_mean": mean(rest),
            "jan_feb_apr_detail": jan_apr,
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
