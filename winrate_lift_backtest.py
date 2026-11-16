# -*- coding: utf-8 -*-
"""winrate_lift_backtest.py — 胜率提升双实验回测（PREREG_WINRATE_LIFT_20260915 冻结判据）。

实验2: 强势股回踩形式 vs 突破追入形式, T+5 胜率/期望对照
实验4: 固定-8%止损 vs 2×ATR(14)止损, 期望/最大单笔亏损对照
口径: qfq 真主库, T日收盘信号→T+1开盘买→T+5收盘卖(实验4含止损/20日上限), 无未来函数。
样本: 沪深主板+创业板, 剔688/689/北证, 上市<60交易日剔除, 2016-01-01 起。
产出: data/PREREG_WINRATE_LIFT_result.json
"""
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT = r"D:/zcodesj/data/PREREG_WINRATE_LIFT_result.json"
START = "20160101"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def is_target(code6: str) -> bool:
    return not code6.startswith(("688", "689", "8", "920", "430"))


def main():
    t0 = time.time()
    con = sqlite3.connect("file:" + QFQ + "?mode=ro", uri=True)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq ORDER BY code")]
    print(f"[load] {len(codes)} codes")

    # 每股内存聚合（避免逐股查询开销: 一次按 code 分组拉取）
    exp2 = {"chase": [], "pullback": []}
    exp2_year = defaultdict(lambda: {"chase": [], "pullback": []})
    exp4 = {"fixed": [], "atr": []}

    n_done = 0
    for code in codes:
        c6 = code[-6:]
        if not is_target(c6):
            continue
        rows = con.execute(
            "SELECT date,open,high,low,close FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>=? ORDER BY date ASC", (code, "20150601")).fetchall()
        if len(rows) < 120:
            continue
        dates = [r[0] for r in rows]
        o = [r[1] for r in rows]
        h = [r[2] for r in rows]
        l = [r[3] for r in rows]
        c = [r[4] for r in rows]
        n = len(rows)
        # 上市<60交易日内不产生信号
        start_i = 60
        # 预计算 ATR14 (Wilder 简化: 14日 TR 均值, 滚动窗口)
        trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
               for i in range(1, n)]
        atr = [None] * n
        for i in range(14, n):
            atr[i] = sum(trs[i - 14:i]) / 14.0  # T 日收盘后可得

        def buy_i(i):
            """T 日信号 → T+1 开盘买。返回 entry index 或 None"""
            if i + 1 >= n or o[i + 1] <= 0:
                return None
            return i + 1

        for i in range(max(start_i, 20), n - 6):
            d = dates[i]
            if d < START:
                continue
            ma5 = sum(c[i - 4:i + 1]) / 5
            ma10 = sum(c[i - 9:i + 1]) / 10
            ma20 = sum(c[i - 19:i + 1]) / 20
            if not (ma5 > ma10 > ma20 and c[i] > ma20):
                continue
            ret20 = c[i] / c[i - 20] - 1 if c[i - 20] > 0 else 0
            if ret20 <= 0:
                continue
            hi20_prev = max(h[i - 20:i]) if i >= 20 else None
            hi20_5 = max(h[i - 25:i - 5]) if i >= 25 else None
            new_high_today = hi20_prev is not None and c[i] >= hi20_prev
            new_high_5d = hi20_prev is not None and max(c[i - 5:i]) >= hi20_prev if hi20_prev else False
            # ---- 实验2 ----
            if new_high_today:
                j = buy_i(i)
                if j is not None and j + 5 < n:
                    ret = (c[j + 5] / o[j] - 1) * 100
                    exp2["chase"].append(ret)
                    exp2_year[d[:4]]["chase"].append(ret)
            elif (new_high_5d and c[i] < c[i - 1] and l[i] > ma20 and hi20_5):
                j = buy_i(i)
                if j is not None and j + 5 < n:
                    ret = (c[j + 5] / o[j] - 1) * 100
                    exp2["pullback"].append(ret)
                    exp2_year[d[:4]]["pullback"].append(ret)
            # ---- 实验4 (入场=突破组同款, 对照两种止损) ----
            if new_high_today:
                j = buy_i(i)
                if j is None:
                    continue
                entry = o[j]
                if entry <= 0 or atr[i] is None or atr[i] <= 0:
                    continue
                stop_fix = entry * 0.92
                stop_atr = entry - 2 * atr[i]
                res = {}
                for tag, stop in (("fixed", stop_fix), ("atr", stop_atr)):
                    ret = None
                    for k in range(j, min(j + 20, n)):
                        if l[k] <= stop:
                            ret = (stop / entry - 1) * 100  # 盘中触发按stop价
                            break
                    if ret is None:
                        k = min(j + 20, n - 1)
                        ret = (c[k] / entry - 1) * 100
                    res[tag] = ret
                exp4["fixed"].append(res["fixed"])
                exp4["atr"].append(res["atr"])
        n_done += 1
        if n_done % 800 == 0:
            print(f"[progress] {n_done}/{len(codes)} codes, e2 chase={len(exp2['chase'])} pb={len(exp2['pullback'])}, e4={len(exp4['fixed'])}")

    con.close()

    def summarize(arr):
        if not arr:
            return {"n": 0}
        w = sum(1 for x in arr if x > 0)
        return {"n": len(arr), "win_rate": round(w / len(arr) * 100, 2),
                "mean": round(statistics.mean(arr), 3),
                "median": round(statistics.median(arr), 3),
                "worst": round(min(arr), 2)}

    e2 = {"chase": summarize(exp2["chase"]), "pullback": summarize(exp2["pullback"]),
          "year_consistency": {}}
    yrs = sorted(exp2_year.keys())
    same_dir = 0
    for y in yrs:
        yc, yp = exp2_year[y]["chase"], exp2_year[y]["pullback"]
        if len(yc) >= 30 and len(yp) >= 30:
            wc = sum(1 for x in yc if x > 0) / len(yc)
            wp = sum(1 for x in yp if x > 0) / len(yp)
            e2["year_consistency"][y] = {"chase_wr": round(wc * 100, 1),
                                         "pullback_wr": round(wp * 100, 1),
                                         "diff_pp": round((wp - wc) * 100, 2)}
            if wp > wc:
                same_dir += 1
    e2["year_consistent_ratio"] = round(same_dir / len(yrs), 2) if yrs else None
    verdict2 = None
    if e2["chase"]["n"] >= 500 and e2["pullback"]["n"] >= 500:
        diff = e2["pullback"]["win_rate"] - e2["chase"]["win_rate"]
        e2["win_rate_diff_pp"] = round(diff, 2)
        verdict2 = ("PASS" if diff >= 5 and e2["pullback"]["mean"] > 0 else
                    "PASS_WEAK" if diff >= 2 and e2["pullback"]["mean"] > -1 else "FAIL")
    else:
        e2["win_rate_diff_pp"] = None
        verdict2 = "INSUFFICIENT"

    e4 = {"fixed": summarize(exp4["fixed"]), "atr": summarize(exp4["atr"])}
    verdict4 = None
    if e4["fixed"]["n"] >= 500:
        diff_m = e4["atr"]["mean"] - e4["fixed"]["mean"]
        worst_worsen = (e4["fixed"]["worst"] - e4["atr"]["worst"]) > 3
        e4["mean_diff"] = round(diff_m, 3)
        e4["worst_worsen_gt3pp"] = worst_worsen
        verdict4 = ("PASS" if diff_m >= 1 and not worst_worsen else
                    "PASS_WEAK" if diff_m >= 0.5 else "FAIL")
    else:
        verdict4 = "INSUFFICIENT"

    result = {
        "prereg": "PREREG_WINRATE_LIFT_20260915",
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_min": round((time.time() - t0) / 60, 1),
        "codes_scanned": n_done,
        "exp2_pullback_form": {**e2, "verdict": verdict2},
        "exp4_stop_atr2": {**e4, "verdict": verdict4},
        "note": "T+1开盘买/T+5收盘卖(实验4:止损或20日上限); qfq; 全部判据见 PREREG_WINRATE_LIFT_20260915.json",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k != "prereg"}, ensure_ascii=False, indent=1))
    print(f"[done] -> {OUT}")


if __name__ == "__main__":
    main()
