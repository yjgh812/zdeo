# -*- coding: utf-8 -*-
"""L3 可转债双低轮动 quick test (方案书 reports/l3_cb_doublelow/QUICKTEST_PLAN.md 冻结口径)
双低=价格+转股溢价率; Top10 等权; 月频(自然月首个报价日); next-bar 次日收盘成交;
两口径并列: A=纯轮动 / B=单券-8%止损(持有期内日收盘触发, 次日收盘卖出, 现金至下个调仓日);
判据: 年化>股票腿(50/50)且>10% / 分年7-9正 / MDD<=15% / NWt>=2 / 两口径同向。产物: QUICKTEST_RESULT.md
"""
import sys, os, json, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"D:\zcodesj\data\cb_hist.db"
MASTER = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
BT7 = r"D:\zcodesj\reports\drift_check_20260924a_0924end"
OUT = r"D:\zcodesj\reports\l3_cb_doublelow\QUICKTEST_RESULT.md"
START, END = "2018-01-01", "2026-09-24"
TOPN = 10
PRICE_CAP = 130.0
MIN_ISSUE = 0.3   # 亿元 (方案书3000万)
SEASONING_DAYS = 20      # 上市满20个报价日
CONV_SEASON_DAYS = 150   # 转股期近似: 上市后~5个月(数据无精确转股开始日, 标注近似)
STOP_PCT = -8.0

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
v = pd.read_sql("SELECT * FROM cb_value", con)
meta = pd.read_sql("SELECT * FROM cb_meta", con)
con.close()
v["date"] = v["date"].astype(str)
v = v[(v["date"] >= START) & (v["date"] <= END)].dropna(subset=["close", "conv_premium"])
v = v[v["close"] > 0]

# 正股 ST 剔除
mcon = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
si = pd.read_sql("SELECT code, is_st FROM stock_info", mcon)
mcon.close()
si["code"] = si["code"].astype(str).str.zfill(6).str[-6:]
st_set = set(si.loc[pd.to_numeric(si["is_st"], errors="coerce").fillna(0) != 0, "code"])
meta["stock_code"] = meta["stock_code"].astype(str).str.zfill(6).str[-6:]
st_bonds = set(meta.loc[meta["stock_code"].isin(st_set), "code"])

# meta 索引
meta_i = meta.set_index("code")
first_seen = v.groupby("code")["date"].min().to_dict()
n0 = len(v)
v = v[~v["code"].isin(st_bonds)]
v = v.merge(meta[["code", "issue_size", "name"]], on="code", how="left")
print(f"数据: {len(v)} 行 ({n0} 原始), 券 {v['code'].nunique()} 只, ST剔除 {len(st_bonds)} 正股对应券")

# 月度调仓日 = 每自然月首个有数据的交易日(全域报价日历)
all_days = sorted(v["date"].unique())
month_first = {}
for d in all_days:
    month_first.setdefault(d[:7], d)
reb_days = sorted(month_first.values())
print(f"调仓日 {len(reb_days)} 个: {reb_days[0]} ~ {reb_days[-1]}")

# 每券按日期排序的序列(供 next-bar 与止损路径)
v = v.sort_values(["code", "date"])
grp = {c: g[["date", "close"]].reset_index(drop=True) for c, g in v.groupby("code")}

def next_close(code, d):
    g = grp.get(code)
    if g is None: return None, None
    idx = g["date"].searchsorted(d, side="right")
    if idx >= len(g): return None, None
    return g.iloc[idx]["date"], float(g.iloc[idx]["close"])

def select_top(day):
    day_df = v[v["date"] == day].copy()
    if len(day_df) == 0:
        return []
    day_df["dbl"] = day_df["close"] + day_df["conv_premium"]
    day_df["age_days"] = (pd.Timestamp(day) - pd.to_datetime(day_df["code"].map(first_seen), format="%Y-%m-%d")).dt.days
    day_df["quotes"] = day_df["code"].map(v[v["date"] <= day].groupby("code").size())
    m = day_df  # v 已含 issue_size/name(main 阶段 merge)
    m = m[(m["close"] <= PRICE_CAP)
          & (m["quotes"] >= SEASONING_DAYS)
          & (m["age_days"] >= CONV_SEASON_DAYS)
          & (m["issue_size"] >= MIN_ISSUE)
          & m["dbl"].notna()]
    m = m.nsmallest(TOPN, "dbl")
    return [(r["code"], float(r["close"]), str(r["name"])) for _, r in m.iterrows()]

def run(arm):
    """arm='A' 纯轮动 / arm='B' -8%止损"""
    eq = 100000.0
    curve = []  # (调仓日, eq_after_period)
    monthly = []
    for i, day in enumerate(reb_days[:-1]):
        picks = select_top(day)
        if not picks:
            monthly.append(0.0); curve.append((day, eq)); continue
        next_day = reb_days[i + 1]
        # 买入 = day 次日收盘; 卖出 = next_day 次日收盘
        rets = []
        for code, c0, nm in picks:
            bd, bc = next_close(code, day)
            if bc is None: continue
            if arm == "A":
                sd, sc = next_close(code, next_day) or (None, None)
                if sc is None: sc = bc
            else:
                g = grp[code]
                j = g["date"].searchsorted(bd, side="left")
                sd, sc = None, None
                for k in range(j, len(g)):
                    d_k, c_k = g.iloc[k]["date"], float(g.iloc[k]["close"])
                    if d_k > next_day: break
                    if c_k / bc - 1 <= STOP_PCT / 100 and k + 1 < len(g):
                        sd, sc = g.iloc[k + 1]["date"], float(g.iloc[k + 1]["close"])  # 次日收盘卖出
                        break
                if sc is None:
                    sd2, sc2 = next_close(code, next_day)
                    sd, sc = (sd2 or next_day), (sc2 if sc2 is not None else bc)
            rets.append(sc / bc - 1.0)
        r = float(np.mean(rets)) if rets else 0.0
        eq *= (1 + r)
        monthly.append(r); curve.append((day, eq))
    return np.array(monthly), curve

def stats(rets, curve, label):
    eq = np.cumprod(1 + rets)
    yrs = len(rets) / 12.0
    ann = (eq[-1] ** (1 / yrs) - 1) * 100 if yrs > 0 else float("nan")
    peak = np.maximum.accumulate(eq); mdd = float(np.min(eq / peak - 1)) * 100
    vol = np.std(rets) * np.sqrt(12) * 100
    sharpe = (np.mean(rets) * 12 - 0.03) / (np.std(rets) * np.sqrt(12)) if np.std(rets) > 0 else 0
    by_year = {}
    for (d, _), r in zip(curve, rets):
        y = d[:4]
        by_year.setdefault(y, []).append(r)
    yearly = {y: (np.prod(1 + np.array(rs)) - 1) * 100 for y, rs in by_year.items()}
    pos_y = sum(1 for x in yearly.values() if x > 0)
    win = sum(1 for r in rets if r > 0) / len(rets) * 100
    return dict(label=label, n=len(rets), ann=ann, mdd=mdd, vol=vol, sharpe=sharpe,
                win=win, yearly=yearly, pos_y=pos_y, neg_y=len(yearly) - pos_y)

mA, cA = run("A")
mB, cB = run("B")
sA, sB = stats(mA, cA, "A 纯轮动"), stats(mB, cB, "B 单券-8%止损")

# 股票腿基线(50/50 同期月度)
combo = pd.read_csv(os.path.join(BT7, "portfolio_combo_equity.csv"), encoding="utf-8-sig")
combo["date"] = combo["date"].astype(str)
combo = combo[(combo["date"] >= START) & (combo["date"] <= END)]
cm = combo.groupby(combo["date"].str[:7])["equity"].last()
c_ret = cm.pct_change().dropna().values
sC = stats(c_ret, list(zip(sorted(cm.index), c_ret)), "50/50股票组合(同期)")

def nw_t(x, lag=3):
    x = np.asarray(x, float); n = len(x)
    e = x - x.mean(); g0 = (e @ e) / n; s = g0
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * ((e[l:] @ e[:-l]) / n)
    return x.mean() / ((s / n) ** 0.5) if (s / n) > 0 else float("nan")

tA, tB = nw_t(mA), nw_t(mB)
L = []
A = L.append
A("# 可转债双低轮动 Quick Test 结果 (方案书冻结口径)\n")
A(f"> 生成 {__import__('time').strftime('%F %T')} | 样本 {START}~{END} | 调仓 {len(mA)} 月 | 双低=价格+转股溢价率, Top10 等权, next-bar 次日收盘成交")
A("> 过滤: 价格≤130 / 上市≥20报价日 / 转股期近似(上市150日) / 发行规模≥0.3亿(剩余规模历史缺, 近似标注) / 正股ST剔 / 成本未计(万1双边约-0.2pp/月周转1次=-2.4pp年化, 判据按扣后口径复核)\n")
for s, t in [(sA, tA), (sB, tB)]:
    A(f"## 口径{s['label']}\n")
    A(f"- 年化 **{s['ann']:+.2f}%** | MDD {s['mdd']:.2f}% | 波动 {s['vol']:.1f}% | 夏普 {s['sharpe']:.2f} | 月胜率 {s['win']:.0f}% | NWt={t:.2f}")
    ys = " / ".join(f"{y}:{x:+.0f}" for y, x in s['yearly'].items())
    A(f"- 分年({s['pos_y']}正{s['neg_y']}负): {ys}\n")
A(f"## 基线: {sC['label']}\n")
A(f"- 年化 {sC['ann']:+.2f}% | MDD {sC['mdd']:.2f}% | 分年 {sC['pos_y']}正{sC['neg_y']}负\n")
gate_a = sA["ann"] > sC["ann"] and sA["ann"] > 10 + 2.4 and sA["pos_y"] >= 7 and abs(sA["mdd"]) <= 15 and tA >= 2
gate_b = sB["ann"] > sC["ann"] and sB["ann"] > 10 + 2.4 and sB["pos_y"] >= 7 and abs(sB["mdd"]) <= 15 and tB >= 2
A("## 判据(含成本扣减后年化>10+2.4pp)\n")
A(f"- 口径A: {'PASS' if gate_a else 'FAIL'} | 口径B: {'PASS' if gate_b else 'FAIL'} → 总判定: **{'PASS 进grilling' if (gate_a or gate_b) else 'FAIL 终止入falsified'}**")
A("\n## 盲区与风险(如实)\n")
A("- 剩余规模历史缺用发行规模近似; 转股开始日用上市150日近似; 强赎状态未逐券建模(价格≤130带规避但不完全); 未计大额冲击; 幸存者偏差=列表含退市券(1059只)已部分对冲")
A("- 2023信用个案(搜特/蓝盾)若入选会体现在分年数据中, 如实保留")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("[DONE]", OUT)
print(f"A: ann={sA['ann']:+.1f}% mdd={sA['mdd']:.1f}% t={tA:.2f} | B: ann={sB['ann']:+.1f}% mdd={sB['mdd']:.1f}% t={tB:.2f} | 50/50: {sC['ann']:+.1f}%")
