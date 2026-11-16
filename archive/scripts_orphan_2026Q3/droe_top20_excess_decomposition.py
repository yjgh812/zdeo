"""droe_Top20 vs 等权≤30基准：逐年超额分解 + 近12月逐月超额（衰减预警）"""
import pandas as pd, numpy as np, pickle

# droe_Top20 日收益（8/15 DSR 检验产物）
r = pd.read_csv("D:/zcodesj/reports/droe_pit_Top20_le30_pit_returns.csv",
                parse_dates=[0], index_col=0).iloc[:, 0]

# 等权≤30基准 nav（factor_combo 系列 pkl，统一口径 qfq 2018-2026）
navs = pickle.load(open("D:/zcodesj/reports/factor_combo_navs_20160104_20260731.pkl", "rb"))
bench_nav = navs["基准_等权≤30元"]
bench = bench_nav.pct_change().dropna()
bench.index = pd.to_datetime(bench.index)

# 对齐
common = r.index.intersection(bench.index)
r, b = r.loc[common], bench.loc[common]
print(f"对齐: {len(common)} 交易日 ({common[0].date()} ~ {common[-1].date()})\n")

# ── 逐年分解 ──
print("=" * 72)
print("droe_Top20 vs 等权≤30基准  逐年分解")
print("=" * 72)
print(f"{'年份':<6}{'策略':>8}{'基准':>8}{'超额':>8}{'策略SR':>8}{'超额SR':>8}{'策略MDD':>9}{'超额MDD':>9}")
print("-" * 72)
excess = r - b  # 近似日超额
for yr in range(2018, 2027):
    rs, bs, es = r.loc[str(yr)], b.loc[str(yr)], excess.loc[str(yr)]
    if len(rs) < 20:
        continue
    pr = (1 + rs).prod() - 1
    pb = (1 + bs).prod() - 1
    pe = (1 + es).prod() - 1
    sr_s = rs.mean() / rs.std() * np.sqrt(252) if rs.std() > 0 else 0
    sr_e = es.mean() / es.std() * np.sqrt(252) if es.std() > 0 else 0
    nav_s, nav_e = (1 + rs).cumprod(), (1 + es).cumprod()
    mdd_s = (nav_s / nav_s.cummax() - 1).min()
    mdd_e = (nav_e / nav_e.cummax() - 1).min()
    flag = "  ⚠️" if pe < 0 else ""
    print(f"{yr:<6}{pr*100:>7.1f}%{pb*100:>7.1f}%{pe*100:>7.1f}%{sr_s:>8.2f}{sr_e:>8.2f}"
          f"{mdd_s*100:>8.1f}%{mdd_e*100:>8.1f}%{flag}")

# 全期
pr = (1 + r).prod() - 1; pb = (1 + b).prod() - 1; pe = (1 + excess).prod() - 1
sr_s = r.mean() / r.std() * np.sqrt(252)
sr_e = excess.mean() / excess.std() * np.sqrt(252)
print("-" * 72)
print(f"{'全期':<6}{pr*100:>7.1f}%{pb*100:>7.1f}%{pe*100:>7.1f}%{sr_s:>8.2f}{sr_e:>8.2f}")

# ── 近12月逐月超额 ──
print()
print("=" * 72)
print("近12个月逐月超额（衰减预警）")
print("=" * 72)
m_ret = (1 + r).resample("ME").prod() - 1
m_ben = (1 + b).resample("ME").prod() - 1
m_exc = m_ret - m_ben
last12 = m_exc.tail(12)
win = (last12 > 0).sum()
print(f"近12月超额胜率: {win}/12")
neg_streak = 0; cur = 0
for v in m_exc.values:
    cur = cur + 1 if v < 0 else 0
    neg_streak = max(neg_streak, cur)
print(f"最长连续负超额月数: {neg_streak}")
for d, v in last12.items():
    bar = "█" * int(abs(v) * 200)
    print(f"  {d.strftime('%Y-%m')}  {'+' if v>=0 else '-'}{abs(v)*100:5.2f}%  {bar}")

# 近6月/近12月滚动超额年化
r6 = excess.tail(126); r12 = excess.tail(252)
a6 = (1 + r6).prod() ** (252 / len(r6)) - 1
a12 = (1 + r12).prod() ** (252 / len(r12)) - 1
print(f"\n近6月超额年化: {a6*100:+.2f}%   近12月超额年化: {a12*100:+.2f}%")
print("判定: " + ("⚠️ 近12月超额为负，因子疑似衰减" if a12 < 0 else "✅ 近12月超额为正，无明显衰减迹象"))