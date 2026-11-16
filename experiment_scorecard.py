# -*- coding: utf-8 -*-
"""experiment_scorecard.py — 实验臂12只成绩单(主人令「每只100股验证能不能涨」2026-11-09建)
口径: 实验臂买入台账(20261109, 12只×100股, source=dip_*_v1) × qfq收盘逐日
产出: data/experiment_scorecard_20261109.json + txt成绩单
  逐只: 买入成本/每日收盘/当日浮盈/D+5收益/是否触发收盘-8%止损
  分组: 四法(起涨/首板/回踩)各自胜率与均值 vs 验证胜率对照
  判据: 5交易日窗(11/09买入→11/13? 按交易日序11/09买→11/13,11/16间取第5交易日=11/13)
用法: python experiment_scorecard.py [--asof YYYYMMDD]  (缺省=库内最新交易日)
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_JSON = os.path.join(ROOT, "data", "experiment_scorecard_20261109.json")
OUT_TXT = os.path.join(ROOT, "data", "experiment_scorecard_latest.txt")

ARM = {
    "301046": ("能辉科技", "起涨型v2", "dip_up_v1"),
    "300931": ("通用电梯", "起涨型v2", "dip_up_v1"),
    "600410": ("华胜天成", "起涨型v2", "dip_up_v1"),
    "301265": ("华新科技", "起涨型v2", "dip_up_v1"),
    "000595": ("新能股份", "底部反转首板", "dip_fb_v1"),
    "001258": ("立新能源", "底部反转首板", "dip_fb_v1"),
    "600156": ("华升股份", "底部反转首板", "dip_fb_v1"),
    "600207": ("安彩高科", "底部反转首板", "dip_fb_v1"),
    "300286": ("安科瑞", "回踩确认型", "dip_ret_v1"),
    "300360": ("炬华科技", "回踩确认型", "dip_ret_v1"),
    "600959": ("江苏有线", "回踩确认型", "dip_ret_v1"),
    "300030": ("阳普医疗", "回踩确认型", "dip_ret_v1"),
}
BUY_DATE = 20261109
BUY_PX = {"301046": 28.09, "300931": 14.59, "600410": 12.51, "301265": 22.38,
          "000595": 5.63, "001258": 9.91, "600156": 7.12, "600207": 4.98,
          "300286": 18.50, "300360": 11.58, "600959": 3.19, "300030": 6.96}


def main():
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--asof", default="")
    a = ap.parse_args()
    con = sqlite3.connect(QFQ, uri=True)
    codes = list(ARM)
    q = ",".join("?" * len(codes))
    rows = con.execute(
        "select code,date,close from stock_daily_meta_qfq where date>=20261109 "
        "and substr(code,-6) in (%s) order by code,date" % q, codes).fetchall()
    con.close()
    g = defaultdict(dict)
    for code, d, c in rows:
        g[str(code)[-6:]][int(d)] = float(c or 0)
    # 交易日序(买入后)
    all_d = sorted({dd for c6 in codes for dd in g[c6]})
    tds = [dd for dd in all_d if dd >= BUY_DATE]
    asof = int(a.asof) if a.asof else (tds[-1] if tds else BUY_DATE)
    hold_idx = {dd: i for i, dd in enumerate(tds)}
    items = []
    for c6, (nm, method, src) in ARM.items():
        ent = BUY_PX[c6]
        ser = g[c6]
        closes = {dd: ser[dd] for dd in tds if dd in ser and ser[dd] > 0}
        n_hold = len([dd for dd in closes if dd >= BUY_DATE])
        d5 = tds[4] if len(tds) > 4 else (tds[-1] if tds else BUY_DATE)
        c5 = closes.get(d5, 0)
        stop_hit = any(c > 0 and c <= ent * 0.92 for c in closes.values())
        r5 = (c5 / ent - 1.0) if c5 > 0 else None
        items.append({"code": c6, "name": nm, "method": method, "source": src,
                      "ent_px": ent, "closes": {str(k): v for k, v in closes.items()},
                      "n_hold_days": n_hold, "d5_date": d5,
                      "d5_ret": round(r5 * 100, 2) if r5 is not None else None,
                      "stop_hit": stop_hit,
                      "last_ret": round((closes.get(max(closes), ent) / ent - 1) * 100, 2)
                      if closes else None})
    groups = defaultdict(list)
    for it in items:
        groups[it["method"]].append(it)
    lines = ["==== 实验臂成绩单(买入日%d, 现至%s) ====" % (BUY_DATE, asof)]
    summary = {}
    for m in ("起涨型v2", "底部反转首板", "回踩确认型"):
        its = [x for x in items if x["method"] == m]
        r5s = [x["d5_ret"] for x in its if x["d5_ret"] is not None]
        last = [x["last_ret"] for x in its if x["last_ret"] is not None]
        stops = sum(1 for x in its if x["stop_hit"])
        lines.append("---- %s (验证胜率: %s) ----" % (
            m, {"起涨型v2": "77.5%(黄金带)/65%严格/0.3%次档",
                "底部反转首板": "53%", "回踩确认型": "共振带约50%"}[m]))
        for x in its:
            lines.append("  %s %s 买入%.2f 现浮%+.2f%% %s | D5(%d)=%s" % (
                x["code"], x["name"], x["ent_px"], x["last_ret"] or 0,
                "🛑止损触发" if x["stop_hit"] else "持仓中",
                x["d5_date"], ("%+.2f%%" % x["d5_ret"]) if x["d5_ret"] is not None else "未到"))
        if last:
            lines.append("  小计: 现浮盈均值%+.2f%% | 止损触发%d/%d" % (
                float(np.mean(last)), stops, len(its)))
        summary[m] = {"n": len(its), "stops": stops,
                      "last_avg": round(float(np.mean(last)), 2) if last else None,
                      "d5_avg": round(float(np.mean(r5s)), 2) if r5s else None}
    json.dump({"asof": asof, "items": items, "summary": summary},
              open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    lines.append("==== 四法对照汇总 ====")
    for m, s in summary.items():
        lines.append("%s: %d只 现浮均值%+.2f%% 止损%d D5均值%s" % (
            m, s["n"], s["last_avg"] or 0, s["stops"],
            ("%+.2f%%" % s["d5_avg"]) if s["d5_avg"] is not None else "未到"))
    txt = "\n".join(lines)
    open(OUT_TXT, "w", encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
