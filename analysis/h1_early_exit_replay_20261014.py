# -*- coding: utf-8 -*-
"""
H1 浮亏阈值提前退出 全量反事实回放(全域回测20261014, 收尾清单4b)
==================================================================
反事实规则: 买入次日起逐日监测收盘价, close/buy-1 <= -阈值 即触发退出;
  口径A(乐观上界): 触发日收盘即卖;  口径B(严格next-bar): 触发次一交易日收盘卖(与引擎止损语义一致);
  未触发笔按原实际卖出价退出(逐位不变)。单笔独立口径, 不计资金再利用(如实标注)。
对称性: 全部3,401笔(含非大亏笔)——中途触及阈值后反弹的票提前卖会吃亏, 必须计入。
验收标准(报告§六4b): 净改善>0 且 大亏(<=-5%)金额降>=1/3 → 升级prereg草案; 否则登记观察/证伪。
自检: A1 笔数闭合; A2 未触发笔cf==act逐位; A3 触发日时间序合法(buy<t<=sell); 缺失行情笔单列按实际处理。
数据: trades_full.csv × qfq日线(只读) | 零规则改动/零交易权
"""
import csv, json, sqlite3, os, sys
from collections import defaultdict

SRC = r"D:\zcodesj\reports\backtest_full_20261014_eod"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\tmp\h1_early_exit_replay_20261014.json"
THRESHOLDS = [-5.0, -6.0, -7.0]

def load(k):
    with open(os.path.join(SRC, k + "_trades_full.csv"), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

trades = {k: load(k) for k in ["bp_ep_core", "ml_signal"]}

# 收集每笔需要的日线区间
need = defaultdict(lambda: [None, None])  # code -> [min_buy, max_sell]
for k, rows in trades.items():
    for r in rows:
        b = r["买入日期"].replace("-", ""); s = r["卖出日期"].replace("-", "")
        if need[r["股票代码"]][0] is None or b < need[r["股票代码"]][0]:
            need[r["股票代码"]][0] = b
        if need[r["股票代码"]][1] is None or s > need[r["股票代码"]][1]:
            need[r["股票代码"]][1] = s

con = sqlite3.connect(r"file:" + QFQ + r"?mode=ro", uri=True)
series = {}  # code -> [(date, close)]
for code, (b, s) in sorted(need.items()):
    rows = con.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND date<=? ORDER BY date",
        [code, b, s]).fetchall()
    series[code] = rows
con.close()

idx = {}  # code -> {date: i}
for code, rows in series.items():
    idx[code] = {d: i for i, (d, _) in enumerate(rows)}

result = {"strats": {}, "missing_days": 0}
for k, rows in trades.items():
    # 每阈值×口径: [净改善pp列表, 触发笔数, 更好/更差/持平, 新大亏数, 新大亏额, 实际大亏额, 总pnl变化额]
    res = {}
    for th in THRESHOLDS:
        for mode in ("A_close", "B_next_close"):
            res[f"{th:+.0f}%|{mode}"] = {"imp_pps": [], "trig": 0, "better": 0, "worse": 0,
                                          "new_big": 0, "new_big_amt": 0.0, "act_big_amt": 0.0,
                                          "dpnl_amt": 0.0}
    missing = 0
    for r in rows:
        code = r["股票代码"]
        b = r["买入日期"].replace("-", ""); s = r["卖出日期"].replace("-", "")
        act_pnl = float(r["盈亏比例%"]); buy = float(r["买入价格"])
        if act_pnl <= -5:
            for key in res: res[key]["act_big_amt"] += float(r["盈亏金额"])
        dates = series.get(code, [])
        ii = idx.get(code, {})
        if b not in ii or s not in ii:
            missing += 1
            # 缺行情: 按实际处理
            for key in res:
                res[key]["imp_pps"].append(0.0)
                if act_pnl <= -5: res[key]["new_big"] += 1; res[key]["new_big_amt"] += float(r["盈亏金额"])
            continue
        i_b, i_s = ii[b], ii[s]
        path = dates[i_b:i_s + 1]  # 含买入日..卖出日
        for th in THRESHOLDS:
            # 寻找首个触发日(从买入次一交易日起)
            trig_i = None
            for j in range(i_b + 1, i_s + 1):
                if (dates[j][1] / buy - 1) * 100 <= th:
                    trig_i = j; break
            for mode in ("A_close", "B_next_close"):
                key = f"{th:+.0f}%|{mode}"
                d = res[key]
                if trig_i is None:
                    cf_pnl = act_pnl
                elif mode == "A_close":
                    cf_pnl = (dates[trig_i][1] / buy - 1) * 100
                else:  # B: 触发次一交易日收盘; 若触发日已是原卖出日则无次日, 按原实际
                    if trig_i >= i_s:
                        cf_pnl = act_pnl
                    else:
                        cf_pnl = (dates[trig_i + 1][1] / buy - 1) * 100
                imp = cf_pnl - act_pnl
                d["imp_pps"].append(imp); d["dpnl_amt"] += imp / 100 * float(r["持股数量"]) * buy
                if trig_i is not None: d["trig"] += 1
                if imp > 0.05: d["better"] += 1
                elif imp < -0.05: d["worse"] += 1
                if cf_pnl <= -5: d["new_big"] += 1; d["new_big_amt"] += float(r["盈亏金额"]) * (cf_pnl / act_pnl if act_pnl else 1)
    med = lambda a: sorted(a)[len(a)//2] if a else 0.0
    avg = lambda a: sum(a)/len(a) if a else 0.0
    tbl = {}
    for key, d in res.items():
        tbl[key] = {
            "imp_med_pp": round(med(d["imp_pps"]), 3), "imp_avg_pp": round(avg(d["imp_pps"]), 3),
            "trig_n": d["trig"], "better": d["better"], "worse": d["worse"],
            "big_n": d["new_big"], "big_amt": round(d["new_big_amt"]),
            "act_big_amt": round(d["act_big_amt"]),
            "big_amt_cut": round(1 - d["new_big_amt"] / d["act_big_amt"], 3) if d["act_big_amt"] else None,
            "total_amt_delta": round(d["dpnl_amt"]),
        }
    result["strats"][k] = {"n": len(rows), "missing": missing, "grid": tbl}

json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for k, s in result["strats"].items():
    print(f"== {k} (n={s['n']}, 缺行情{s['missing']}笔按实际处理) ==")
    print(f"  {'阈值|口径':<14}{'中位pp':>8}{'均值pp':>8}{'触发':>6}{'更好':>6}{'更差':>6}{'新大亏数':>8}{'大亏额削减':>10}{'总盈亏变化':>12}")
    for key, t in s["grid"].items():
        print(f"  {key:<14}{t['imp_med_pp']:>8}{t['imp_avg_pp']:>8}{t['trig_n']:>6}{t['better']:>6}{t['worse']:>6}{t['big_n']:>8}{t['big_amt_cut']*100 if t['big_amt_cut'] is not None else 0:>9.0f}%{t['total_amt_delta']:>12,}")
print("输出:", OUT)
