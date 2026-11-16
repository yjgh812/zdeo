# -*- coding: utf-8 -*-
"""
双引擎质量同降监测·可行性分析（阈值段2016-2025，2026不碰——协议：2026只验证一次）
质量指标 Q(t) = 最近3期调仓批次等权平均已实现收益（月频，与生产调仓节奏同构）
双失效 = Q_bp < 扩展窗口P30 且 Q_ml < P30（分位仅用截至t-1历史，无前视）
假设降档：触发期起组合收益×0.70（现金闲置），复位恢复1.0
数据：10/18 fork L4 bp_ep trades(2016起) + ml trades(2020起)。只读。
"""
import csv, json, math
from collections import defaultdict

BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
OUT = "D:/zcodesj/analysis/dualengine_gate_feasibility_20261019_out.json"

def monthly_quality(path):
    """按买入批次聚合：每期=同买入日全部笔的等权平均收益%（净口径含成本）"""
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    by = defaultdict(list)
    for r in rows:
        by[r["买入日期"]].append(float(r["盈亏比例%"]))
    seq = []
    for bd in sorted(by):
        v = by[bd]
        seq.append({"date": bd, "year": bd[:4], "q": sum(v) / len(v), "n": len(v)})
    return seq

bp = monthly_quality(f"{BASE}/bp_ep_core_trades_full.csv")
ml = monthly_quality(f"{BASE}/ml_signal_trades_full.csv")
print(f"bp_ep期数={len(bp)} (2016起) ml期数={len(ml)} (2020起)")

# 对齐共同期（按买入日期匹配；bp月度/ ml月度，日期可能差几天→按年月匹配）
bp_by_ym = {x["date"][:7]: x for x in bp}
ml_by_ym = {x["date"][:7]: x for x in ml}
common = sorted(set(bp_by_ym) & set(ml_by_ym))
print(f"共同月数={len(common)} ({common[0]}~{common[-1]})")

# 扩展窗口分位（无前视）：对每月t，用截至前一个月的历史分布算P30/P50
# 滚动质量：最近3期平均
def roll3(seq_by_ym, ym_list):
    out = {}
    for i, ym in enumerate(ym_list):
        w = [seq_by_ym[y]["q"] for y in ym_list[max(0, i-2):i+1]]
        out[ym] = sum(w) / len(w)
    return out

Qbp = roll3(bp_by_ym, common)
Qml = roll3(ml_by_ym, common)

events = []  # (ym, Qbp, Qml, dual_fail, pct_bp, pct_ml)
for i, ym in enumerate(common):
    hist_bp = sorted(Qbp[y] for y in common[:i])   # 截至 t-1（不含当月）
    hist_ml = sorted(Qml[y] for y in common[:i])
    if len(hist_bp) < 24:  # 两年热身后才开始判分位
        continue
    def pct(v, hist):
        return sum(1 for x in hist if x < v) / len(hist) * 100
    pb = pct(Qbp[ym], hist_bp)
    pm = pct(Qml[ym], hist_ml)
    dual = pb < 30 and pm < 30
    events.append({"ym": ym, "Qbp": round(Qbp[ym], 2), "Qml": round(Qml[ym], 2),
                   "pct_bp": round(pb, 1), "pct_ml": round(pm, 1), "dual_fail": dual})

# 连续2期双失效→触发；连续2期任一恢复P50→复位
triggered, state = [], "normal"
for i, e in enumerate(events):
    prev = events[i-1] if i > 0 else None
    if state == "normal":
        if e["dual_fail"] and prev and prev["dual_fail"]:
            state = "reduced"
            triggered.append(e["ym"])
    else:
        if e["pct_bp"] >= 50 or e["pct_ml"] >= 50:
            if prev and (prev["pct_bp"] >= 50 or prev["pct_ml"] >= 50):
                state = "normal"
                triggered.append(e["ym"] + "*复位")

# 假设降档回放：触发期间每期批次收益×0.7
reduced_set = set()
state = "normal"
for i, e in enumerate(events):
    prev = events[i-1] if i > 0 else None
    if state == "normal":
        if e["dual_fail"] and prev and prev["dual_fail"]:
            state = "reduced"
            reduced_set.add(e["ym"])
    else:
        reduced_set.add(e["ym"])
        if e["pct_bp"] >= 50 or e["pct_ml"] >= 50:
            if prev and (prev["pct_bp"] >= 50 or prev["pct_ml"] >= 50):
                state = "normal"

# 组合=bp_ep与ml等权月批次（近似独立账户各50%）
combo = []
for ym in common:
    r = 0.5 * bp_by_ym[ym]["q"] + 0.5 * ml_by_ym[ym]["q"]
    combo.append({"ym": ym, "r": r, "reduced": ym in reduced_set})

# 复利对比（每期收益%→乘积）
eq_n, eq_r = 100.0, 100.0
peak_n, peak_r = 100.0, 100.0
mdd_n, mdd_r = 0.0, 0.0
for c in combo:
    mult = 0.70 if c["reduced"] else 1.0
    eq_n *= (1 + c["r"] / 100)
    eq_r *= (1 + c["r"] / 100 * mult)
    peak_n = max(peak_n, eq_n); peak_r = max(peak_r, eq_r)
    mdd_n = max(mdd_n, (peak_n - eq_n) / peak_n * 100)
    mdd_r = max(mdd_r, (peak_r - eq_r) / peak_r * 100)

# 分年
years = sorted({c["ym"][:4] for c in combo})
by_year = {}
for y in years:
    seg = [c for c in combo if c["ym"][:4] == y]
    rn = 100.0; rr = 100.0
    for c in seg:
        mult = 0.70 if c["reduced"] else 1.0
        rn *= (1 + c["r"] / 100)
        rr *= (1 + c["r"] / 100 * mult)
    by_year[y] = {"normal": round(rn - 100, 2), "reduced": round(rr - 100, 2),
                  "n_months": len(seg), "reduced_months": sum(1 for c in seg if c["reduced"])}

result = {
    "segment": "2016-2025阈值段(2026未触碰,热身24期后起判)",
    "common_months": len(common),
    "dual_fail_events": [e for e in events if e["dual_fail"]],
    "state_transitions": triggered,
    "reduced_months_total": len(reduced_set),
    "equity": {"normal": round(eq_n - 100, 2), "reduced": round(eq_r - 100, 2)},
    "mdd": {"normal": round(mdd_n, 2), "reduced": round(mdd_r, 2)},
    "by_year": by_year,
}
json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n=== 可行性结果（2016-2025阈值段） ===")
print(f"共同月数: {len(common)}, 热身后可判: {len(events)}")
print(f"双失效月: {len(result['dual_fail_events'])} 次")
for e in result["dual_fail_events"]:
    print(f"  {e['ym']}: Qbp={e['Qbp']}%({e['pct_bp']}分位) Qml={e['Qml']}%({e['pct_ml']}分位)")
print(f"状态切换: {triggered}")
print(f"降档月总数: {len(reduced_set)}")
print(f"全段复利收益: 原样={result['equity']['normal']}% 降档={result['equity']['reduced']}%")
print(f"最大回撤: 原样={result['mdd']['normal']}% 降档={result['mdd']['reduced']}%")
print("分年（normal% / 降档% / 降档月数）:")
for y, v in by_year.items():
    print(f"  {y}: {v['normal']} / {v['reduced']} / {v['reduced_months']}/{v['n_months']}")
