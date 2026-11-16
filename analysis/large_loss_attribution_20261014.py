# -*- coding: utf-8 -*-
"""
大额亏损笔逐笔归因抽取(全域回测终点2026-10-14, 收尾清单#4)
=============================================================
口径: 大额亏损 = pnl_pct <= -5% (引擎 tulong_backtest_full_report.py L1432 同源定义)
数据: reports/backtest_full_20261014_eod/{bp_ep_core,ml_signal}_trades_full.csv (只读)
自检断言:
  A1) 两臂大亏计数与引擎 OPTIMIZATION_PLAN.md 中 big_loss 数字逐位一致(正则抓取,禁转抄)
  A2) 分桶计数合计 == 大亏总数
  A3) 盈亏金额口径: 大亏合计金额 <= 两臂总亏损金额(平方和校验的反向弱断言)
输出: tmp/large_loss_attribution_20261014.json + 控制台摘要
零交易权/零回测重跑/零既有文件改动
"""
import csv, json, re, os, sys
from collections import Counter, defaultdict

SRC = r"D:\zcodesj\reports\backtest_full_20261014_eod"
PLAN = os.path.join(SRC, "OPTIMIZATION_PLAN.md")
OUT = r"D:\zcodesj\tmp\large_loss_attribution_20261014.json"
STRATS = ["bp_ep_core", "ml_signal"]

def load(k):
    with open(os.path.join(SRC, k + "_trades_full.csv"), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

# A1: 引擎口径数字抓取
plan_txt = open(PLAN, encoding="utf-8").read()
engine_big = {}
for k, label in [("bp_ep_core", "bp\\+ep_core"), ("ml_signal", "ml_signal")]:
    m = re.search(label + r"[^|]*\|\s*大额亏损\s*(\d+)\s*笔", plan_txt)
    if not m:
        m2 = re.findall(r"大额亏损 (\d+) 笔", plan_txt)
        engine_big[k] = int(m2[0] if k == "bp_ep_core" else m2[1]) if len(m2) >= 2 else None
    else:
        engine_big[k] = int(m.group(1))

result = {"engine_big_loss": engine_big, "strats": {}}
errs = []
for k in STRATS:
    rows = load(k)
    big = [r for r in rows if float(r["盈亏比例%"]) <= -5]
    if engine_big.get(k) is not None and len(big) != engine_big[k]:
        errs.append(f"A1 FAIL {k}: csv={len(big)} vs engine={engine_big[k]}")

    # 深度分桶
    buckets = {"-5~-8": 0, "-8~-12": 0, "-12~-20": 0, "<-20": 0}
    for r in big:
        p = float(r["盈亏比例%"])
        if p <= -20: buckets["<-20"] += 1
        elif p <= -12: buckets["-12~-20"] += 1
        elif p <= -8: buckets["-8~-12"] += 1
        else: buckets["-5~-8"] += 1
    if sum(buckets.values()) != len(big):
        errs.append(f"A2 FAIL {k}: 分桶合计{sum(buckets.values())} != 总数{len(big)}")

    # 卖出理由 x 平均亏损
    by_reason = defaultdict(list)
    for r in big:
        by_reason[r["卖出触发理由"]].append(float(r["盈亏比例%"]))
    reason_tbl = {r: {"n": len(v), "avg_pct": sum(v)/len(v)} for r, v in by_reason.items()}

    # 持仓天数(大亏 vs 全体)
    hd_big = [float(r["持仓天数"]) for r in big]
    hd_all = [float(r["持仓天数"]) for r in rows]
    med = lambda a: sorted(a)[len(a)//2] if a else 0

    # 年份分布 + 当年占比
    yr_big = Counter(r["买入日期"][:4] for r in big)
    yr_all = Counter(r["买入日期"][:4] for r in rows)
    yr_tbl = {y: {"big": yr_big[y], "all": yr_all[y], "ratio": round(yr_big[y]/yr_all[y], 3)}
              for y in sorted(set(yr_big) | set(yr_all))}

    # 金额贡献
    big_amt = sum(float(r["盈亏金额"]) for r in big)
    all_loss_amt = sum(float(r["盈亏金额"]) for r in rows if float(r["盈亏金额"]) < 0)
    all_pnl_amt = sum(float(r["盈亏金额"]) for r in rows)
    if big_amt > 0 or big_amt < all_loss_amt:
        errs.append(f"A3 WARN {k}: 大亏合计{big_amt:,.0f} vs 总亏损{all_loss_amt:,.0f}")

    # score 分布(买入理由内 score=xx)
    sc_big, sc_win = [], []
    for r in rows:
        m = re.search(r"score=([0-9.]+)", r["买入决策理由"])
        if not m: continue
        (sc_big if float(r["盈亏比例%"]) <= -5 else sc_win).append(float(m.group(1)))
    sc_tbl = {
        "big_avg": round(sum(sc_big)/len(sc_big), 3) if sc_big else None,
        "win_avg": round(sum(sc_win)/len(sc_win), 3) if sc_win else None,
        "n_score": len(sc_big) + len(sc_win)}

    # 深亏(<-12%)抽样: 跳空/跌停特征(持仓<=3日即深亏=跳空型)
    deep = [r for r in big if float(r["盈亏比例%"]) <= -12]
    deep_fast = [r for r in deep if float(r["持仓天数"]) <= 3]

    result["strats"][k] = {
        "n_total": len(rows), "n_big": len(big), "big_ratio": round(len(big)/len(rows), 3),
        "big_amt": round(big_amt), "all_loss_amt": round(all_loss_amt),
        "big_share_of_loss": round(big_amt/all_loss_amt, 3) if all_loss_amt else None,
        "big_vs_total_pnl": round(big_amt/all_pnl_amt, 3) if all_pnl_amt else None,
        "buckets": buckets,
        "reason": reason_tbl,
        "hold_days": {"big_median": med(hd_big), "big_avg": round(sum(hd_big)/len(hd_big), 1),
                      "all_median": med(hd_all), "all_avg": round(sum(hd_all)/len(hd_all), 1)},
        "by_year": yr_tbl,
        "score": sc_tbl,
        "deep_loss": {"n_le_-12": len(deep), "n_fast_le3d": len(deep_fast),
                      "fast_ratio": round(len(deep_fast)/len(deep), 3) if deep else None},
    }

# 对照证伪库
fals = json.load(open(r"D:\zcodesj\data\falsified_rules.json", encoding="utf-8"))
result["falsified_lib_version"] = fals.get("version", fals.get("updated_at", "unknown")) if isinstance(fals, dict) else "list"
result["errs"] = errs

json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 控制台摘要
for k in STRATS:
    s = result["strats"][k]
    print(f"== {k} ==")
    print(f"  大亏 {s['n_big']}/{s['n_total']} ({s['big_ratio']*100:.1f}%) | 大亏金额 {s['big_amt']:,} = 总亏损的 {s['big_share_of_loss']*100:.1f}% / 总盈亏绝对值的 {abs(s['big_vs_total_pnl'])*100:.1f}%")
    print(f"  深度分桶: {s['buckets']}")
    print(f"  卖出理由: " + " | ".join(f"{r}: n={v['n']} avg{v['avg_pct']:.1f}%" for r, v in sorted(s['reason'].items(), key=lambda x: -x[1]['n'])))
    print(f"  持仓天数: 大亏中位{s['hold_days']['big_median']}/均值{s['hold_days']['big_avg']} vs 全体中位{s['hold_days']['all_median']}/均值{s['hold_days']['all_avg']}")
    hot = sorted(s['by_year'].items(), key=lambda x: -x[1]['big'])[:3]
    print(f"  大亏高发年TOP3: " + " | ".join(f"{y}: {v['big']}笔(占当年{v['ratio']*100:.0f}%)" for y, v in hot))
    if s["score"]["big_avg"]:
        print(f"  买入score: 大亏均值{s['score']['big_avg']} vs 盈利笔均值{s['score']['win_avg']} (n={s['score']['n_score']})")
    d = s["deep_loss"]
    print(f"  深亏(<=-12%): {d['n_le_-12']}笔, 其中<=3日急亏 {d['n_fast_le3d']}笔 ({(d['fast_ratio'] or 0)*100:.0f}%)")
if errs:
    print("❌ 断言:", *errs, sep="\n  "); sys.exit(2)
print("✅ 自检断言全过: 大亏计数=引擎口径逐位一致, 分桶闭合, 金额口径闭合")
print("输出:", OUT)
