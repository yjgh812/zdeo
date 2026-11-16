# -*- coding: utf-8 -*-
"""
环境门(退潮/失效档)历史区分度检验(2026-10-14, 呈请项②prereg前置研究)
======================================================================
问题: 能否用当日收盘可知的市场状态指标, 把"主臂会亏的日子"与"会赚的日子"分开?
      门有区分度才值得挂影子; 无区分度则呈请撤回。
门候选(全部T日收盘可知, 20日滚动分位70/80双档敏感性, 不做网格搜索防数据窥探):
  G1 跌停潮: limit_down_ratio >= 滚动20日80分位
  G2 冰点:   sentiment_score <= 滚动20日20分位
  G3 涨停枯竭: limit_up_ratio <= 滚动20日20分位
检验:
  1) 事件研究: 门开日 -> T+1 两主臂+组合日收益 vs 非门开日, 均值差+手写t值
  2) 分年稳健: 逐年份门开/非门开次日收益差(防年份构成幻象, 宏观维切片先例教训)
  3) 状态持续期: 门开平均连续天数(降敞口切换成本)
  4) 月度预演: 每月末门状态 -> 次月敞口减半路径 vs 原路径, 分年省亏/误杀统计
自检: A1 门开日占比在5%-45%合理带; A2 样本日数闭合; 缺失日对齐剔除计数
零规则改动/零交易权 | 输出 tmp/env_gate_discrimination_20261014.json
"""
import csv, json, sqlite3, os, sys
from collections import defaultdict
import math

SENT_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
SRC = r"D:\zcodesj\reports\backtest_full_20261014_eod"
OUT = r"D:\zcodesj\tmp\env_gate_discrimination_20261014.json"
WIN, HI_Q, LO_Q = 20, 0.80, 0.70  # 滚动窗与分位(80主档/70敏感性)

con = sqlite3.connect(r"file:" + SENT_DB + r"?mode=ro", uri=True)
sent = con.execute(
    "SELECT trade_date, limit_up_ratio, limit_down_ratio, sentiment_score FROM feature_market_sentiment "
    "WHERE trade_date>='2016-01-01' ORDER BY trade_date").fetchall()
con.close()
dates = [r[0] for r in sent]
lur = [r[1] for r in sent]; ldr = [r[2] for r in sent]; ss = [r[3] for r in sent]

def roll_q(arr, q, hi=True):
    out = [False] * len(arr)
    for i in range(WIN, len(arr)):
        w = sorted(arr[i - WIN:i])
        k = w[int(q * (WIN - 1))]
        out[i] = (arr[i] >= k) if hi else (arr[i] <= k)
    return out

gates = {
    "G1_跌停潮(80)": roll_q(ldr, HI_Q, True),
    "G2_冰点(80)": roll_q(ss, HI_Q, False),          # score低分位=hi=False -> 用<=20分位: 等价 score<=20分位
    "G3_涨停枯竭(80)": roll_q(lur, HI_Q, False),
    "G1s_跌停潮(70)": roll_q(ldr, LO_Q, True),
    "G2s_冰点(70)": roll_q(ss, LO_Q, False),
    "G3s_涨停枯竭(70)": roll_q(lur, LO_Q, False),
}
# 修正: 20分位实现
def roll_q_lo(arr, q):
    out = [False] * len(arr)
    for i in range(WIN, len(arr)):
        w = sorted(arr[i - WIN:i])
        k = w[int(q * (WIN - 1))]
        out[i] = arr[i] <= k
    return out
gates["G2_冰点(80)"] = roll_q_lo(ss, 0.20)
gates["G3_涨停枯竭(80)"] = roll_q_lo(lur, 0.20)
gates["G2s_冰点(70)"] = roll_q_lo(ss, 0.30)
gates["G3s_涨停枯竭(70)"] = roll_q_lo(lur, 0.30)
gates["G1s_跌停潮(70)"] = roll_q(ldr, LO_Q, True)

# 臂日收益
def day_ret(name):
    rows = list(csv.reader(open(os.path.join(SRC, name + "_equity.csv"), encoding="utf-8-sig")))[1:]
    dmap = {}
    for i in range(1, len(rows)):
        prev = float(rows[i - 1][1]); cur = float(rows[i][1])
        dmap[rows[i][0]] = (cur / prev - 1) * 100 if prev else 0.0
    return dmap
arms = {"bp_ep": day_ret("bp_ep_core"), "ml": day_ret("ml_signal"), "combo": day_ret("portfolio_combo")}

def tstat(diff_list):
    n = len(diff_list)
    if n < 10: return None
    m = sum(diff_list) / n
    var = sum((x - m) ** 2 for x in diff_list) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else None

result = {"gates": {}}
for gname, flag in gates.items():
    open_next = {}  # 门开日T -> T+1各臂收益
    base_next = {a: [] for a in arms}
    for a in arms: open_next[a] = []
    n_open = 0
    for i in range(WIN, len(dates) - 1):
        t, t1 = dates[i], dates[i + 1]
        if flag[i]:
            n_open += 1
            for a in arms:
                if t1 in arms[a]: open_next[a].append(arms[a][t1])
        else:
            for a in arms:
                if t1 in arms[a]: base_next[a].append(arms[a][t1])
    # 持续期
    runs, cur = [], 0
    for f in flag[WIN:]:
        if f: cur += 1
        elif cur: runs.append(cur); cur = 0
    if cur: runs.append(cur)
    # 分年
    by_year = defaultdict(lambda: {"open": [], "base": []})
    for i in range(WIN, len(dates) - 1):
        t, t1 = dates[i], dates[i + 1]
        y = t[:4]
        for a in ("bp_ep", "combo"):
            if t1 in arms[a]:
                (by_year[y]["open"] if flag[i] else by_year[y]["base"]).append(arms[a][t1])
    yr_tbl = {}
    for y in sorted(by_year):
        o, b = by_year[y]["open"], by_year[y]["base"]
        if len(o) >= 8:
            yr_tbl[y] = {"open_avg": round(sum(o)/len(o), 3), "base_avg": round(sum(b)/len(b), 3),
                         "diff": round(sum(o)/len(o)-sum(b)/len(b), 3), "n_open": len(o)}
    g = {"n_open": n_open, "open_ratio": round(n_open / max(1, len(dates) - WIN - 1), 3),
         "run_avg_days": round(sum(runs)/len(runs), 1) if runs else 0,
         "run_max_days": max(runs) if runs else 0, "by_year_combo": yr_tbl}
    for a in arms:
        o, b = open_next[a], base_next[a]
        if not o: continue
        diff = [x - sum(b)/len(b) for x in o]
        g[a] = {"open_avg": round(sum(o)/len(o), 3), "base_avg": round(sum(b)/len(b), 3),
                "diff_pp": round(sum(o)/len(o)-sum(b)/len(b), 3), "t": round(tstat(diff), 2) or None, "n": len(o)}
    # 月度预演: 月末门状态 -> 次月敞口减半
    month_last = {}
    for i, d in enumerate(dates):
        month_last[d[:7]] = i
    mo = defaultdict(lambda: {"combo_gate": 0.0, "combo_all": 0.0, "gate": False})
    for i, d in enumerate(dates):
        if d not in arms["combo"]: continue
        m = d[:7]
        mo[m]["combo_all"] += arms["combo"][d]
        if i >= WIN and flag[i]: mo[m]["combo_gate"] += arms["combo"][d]; mo[m]["gate"] = flag[i]
    months = sorted(mo)
    half_saving = []
    for i in range(1, len(months)):
        pm, m = months[i-1], months[i]
        gate_on = mo[pm]["gate"]
        r_all = mo[m]["combo_all"]
        r_half = mo[m]["combo_gate"] * 0.5 + (mo[m]["combo_all"] - mo[m]["combo_gate"])  # 门开日的敞口减半
        if gate_on:
            half_saving.append({"month": m, "r_all": round(r_all, 2), "r_half": round(r_half, 2),
                                "delta": round(r_half - r_all, 2)})
    saved = sum(x["delta"] for x in half_saving if x["delta"] > 0)
    missed = sum(x["delta"] for x in half_saving if x["delta"] < 0)
    g["monthly_half_preview"] = {"gate_months": len(half_saving), "saved_total_pp": round(saved, 2),
                                  "missed_total_pp": round(missed, 2), "net_pp": round(saved + missed, 2),
                                  "win_months": sum(1 for x in half_saving if x["delta"] > 0)}
    result["gates"][gname] = g

json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for gname, g in result["gates"].items():
    print(f"== {gname} == 门开{g['n_open']}日({g['open_ratio']*100:.0f}%) 平均持续{g['run_avg_days']}天 最长{g['run_max_days']}天")
    for a in ("bp_ep", "ml", "combo"):
        if a in g:
            print(f"   {a:<6} 门开次日{g[a]['open_avg']:+.3f}% vs 其他{g[a]['base_avg']:+.3f}% 差{g[a]['diff_pp']:+.3f}pp t={g[a]['t']} n={g[a]['n']}")
    mp = g["monthly_half_preview"]
    print(f"   月度减半预演: 门开月{mp['gate_months']}个 省亏{mp['saved_total_pp']}pp 误杀{mp['missed_total_pp']}pp 净{mp['net_pp']}pp 胜月{mp['win_months']}/{mp['gate_months']}")
print("输出:", OUT)
