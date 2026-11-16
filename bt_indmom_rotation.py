# -*- coding: utf-8 -*-
"""L3 第二站·行业动量轮动 quick test (侦察级, 2.7年样本, 判据降档如实标注)
信号: 上月末行业 ret20 (feature_industry_momentum 118 通达信行业截面)
持有: Top5 行业等权, 行业收益=池内个股等权月收益(qfq), 月频, 成本双边0.3%
判据(侦察级): 月胜率>=55% / 年化>等权全行业基线+成本 / MDD<=15% / NWt>=1.5
产物: reports/l3_cb_doublelow/../l3_indmom/QUICKTEST_RESULT.md (reports/l3_indmom/)
"""
import sys, os, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
MAP_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\reports\l3_indmom\QUICKTEST_RESULT.md"
TOPN = 5
COST = 0.003  # 双边

def ro(p): return sqlite3.connect(f"file:{p}?mode=ro", uri=True)

# 行业截面(月末 ret20): fim 同行业个股同值, 取 distinct; 映射直接用 fim 自带 ts_code->industry
f = ro(FUND)
fim = pd.read_sql("SELECT ts_code, trade_date, industry, industry_ret20 FROM feature_industry_momentum", f)
f.close()
fim["month"] = fim["trade_date"].astype(str).map(lambda s: f"{s[:4]}-{s[5:7]}" if "-" in s else f"{s[:4]}-{s[4:6]}")
fim["code6"] = fim["ts_code"].astype(str).str.split(".").str[0].str[-6:]  # '300001.SZ'→'300001'; 纯数字行='其他'行业后续被剔
fim = fim[~fim["code6"].str.startswith(("8", "4", "688"))]
fim = fim[fim["industry"] != "其他"]  # 指数/未分类垃圾桶行业(纯数字ts_code行)
fim = fim[(fim["industry_ret20"] > -60) & (fim["industry_ret20"] < 60) & fim["industry_ret20"].notna()]  # 坏值清洗(冻结)
sig = fim.groupby(["month", "industry"])["industry_ret20"].median().reset_index()
sig = sig.pivot(index="month", columns="industry", values="industry_ret20")
print(f"[SIG] {sig.shape[0]} 月 x {sig.shape[1]} 行业")

# 个股月收益(qfq) + 行业映射
q = ro(QFQ)
qfq = pd.read_sql("SELECT code, date, close FROM stock_daily_meta_qfq WHERE date>='20231101'", q)
q.close()
qfq["code"] = qfq["code"].astype(str).str.zfill(6).str[-6:]
qfq["month"] = qfq["date"].astype(str).map(lambda s: f"{s[:4]}-{s[5:7]}" if "-" in s else f"{s[:4]}-{s[4:6]}")
last = qfq.groupby(["code", "month"])["close"].last().reset_index()
mret = last.pivot(index="month", columns="code", values="close").pct_change()
print(f"[QFQ] 月收益 {mret.shape}")

m = ro(MAP_DB)
m.close()
# fim 自带映射: 每月 ts_code->industry(取该月最新截面)
fim_monthly_map = {mo: g.drop_duplicates("code6").set_index("code6")["industry"] for mo, g in fim.groupby("month")}

months = [m for m in sig.index if m in mret.index]
months.sort()
# 需要映射覆盖: 等权全行业基线(每行业等权再等权)
def industry_month_returns(month):
    if month not in mret.index: return None
    row = mret.loc[month].dropna()
    c2i = fim_monthly_map.get(month)
    if c2i is None: return None
    by_ind = {}
    for code, r in row.items():
        ind = c2i.get(code)
        if ind is not None and isinstance(ind, str):
            by_ind.setdefault(ind, []).append(r)
    return {k: float(np.mean(v)) for k, v in by_ind.items() if len(v) >= 3}

rets_top, rets_base = [], []
months_used = []
for i in range(1, len(months)):
    sig_m, hold_m = months[i-1], months[i]
    row = sig.loc[sig_m].dropna()
    if len(row) < TOPN + 5: continue
    ir = industry_month_returns(hold_m)
    if not ir: continue
    top = row.nlargest(TOPN).index
    vals = [ir[k] for k in top if k in ir]
    if len(vals) < TOPN: continue
    rets_top.append(np.mean(vals) - COST)
    rets_base.append(np.mean(list(ir.values())))
    months_used.append(hold_m)
rets_top = np.array(rets_top); rets_base = np.array(rets_base)
print(f"[BT] {len(rets_top)} 月 ({months_used[0]}~{months_used[-1]})")

def stats(r):
    eq = np.cumprod(1 + r); yrs = len(r) / 12.0
    ann = (eq[-1] ** (1 / yrs) - 1) * 100
    peak = np.maximum.accumulate(eq); mdd = float(np.min(eq / peak - 1)) * 100
    shp = (np.mean(r) * 12 - 0.03) / (np.std(r) * np.sqrt(12)) if np.std(r) > 0 else 0
    return ann, mdd, shp, float(np.mean(r > 0) * 100)

def nw_t(x, lag=3):
    x = np.asarray(x, float); n = len(x)
    e = x - x.mean(); s = (e @ e) / n
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * ((e[l:] @ e[:-l]) / n)
    return x.mean() / ((s / n) ** 0.5) if (s / n) > 0 else float("nan")

aT, mT, sT, wT = stats(rets_top)
aB, mB, sB, wB = stats(rets_base)
tT = nw_t(rets_top - rets_base)
gate = (wT >= 55 and aT > aB and abs(mT) <= 15 and tT >= 1.5)
L = [f"# L3 第二站·行业动量轮动 quick test (侦察级 2.7 年样本)\n"]
A = L.append
A(f"> 生成 {__import__('time').strftime('%F %T')} | 样本 {months_used[0]}~{months_used[-1]} ({len(rets_top)}月) | 118 通达信行业, 上月末 ret20 Top5 等权, 月频, 成本双边0.3%")
A("> 🔴侦察级声明: 样本仅2.7年, 分年7/9判据不适用(降档为月胜率/NWt>=1.5); 全史版(申万/880映射)待侦察级有苗头再投\n")
A(f"- **Top5动量轮动**: 年化 {aT:+.2f}% MDD {mT:.2f}% 夏普 {sT:.2f} 月胜率 {wT:.0f}%")
A(f"- 等权全行业基线: 年化 {aB:+.2f}% MDD {mB:.2f}% 夏普 {sB:.2f} 月胜率 {wB:.0f}%")
A(f"- 超额: {aT-aB:+.2f}pp/年 | 超额NWt={tT:.2f}")
A(f"- 侦察级判据(胜率>=55%/年化>基线/MDD<=15%/NWt>=1.5): **{'PASS(有苗头,值得投全史版)' if gate else 'FAIL(终止)'}**")
A("\n## 风险与盲区\n")
A("- 2.7年样本覆盖2024牛市+2025+2026结构市, 未覆盖完整熊段; 行业=通达信118类(非申万31); ret20信号与ETF可实现性之间有跟踪差; 幸存者偏差低(行业指数天然幸存)")
A("- 已知先验冲突: 赢家画像/方向层三位一体判「主线是方向」为选股层铁律, 本策略是独立轮动腿非选股, 但若提产须主人裁决与主线判定的关系")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print(f"[DONE] {OUT}")
print(f"Top5: {aT:+.1f}%/mdd{mT:.1f}%/win{wT:.0f}% | 基线: {aB:+.1f}% | 超额t={tT:.2f} | {'PASS' if gate else 'FAIL'}")
