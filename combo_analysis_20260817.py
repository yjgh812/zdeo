# -*- coding: utf-8 -*-
"""组合回测绩效补算 — round3产物分析 (2026-08-17晚)
问题: bp+ep(163.6%/0.65) 与 ml_signal(217.6%/0.91) 组合是否改善风险调整收益?
"""
import numpy as np
import pandas as pd

BASE = r"D:/zcodesj/reports/tulong_backtest_round3"


def stats(nav: pd.Series, freq=244):
    ret = nav.pct_change().dropna()
    if len(ret) < 20:
        return None
    yrs = len(ret) / freq
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1
    vol = ret.std() * np.sqrt(freq)
    sharpe = ret.mean() / ret.std() * np.sqrt(freq) if ret.std() > 0 else 0.0
    dd = (nav / nav.cummax() - 1).min()
    return dict(cagr=cagr, vol=vol, sharpe=sharpe, mdd=dd, end=nav.iloc[-1])


def load(name, col="value"):
    df = pd.read_csv(f"{BASE}/{name}", encoding="utf-8-sig", parse_dates=["date"])
    return df.set_index("date")[col].astype(float)


nav = {
    "bp_ep": load("bp_ep_core_equity.csv"),
    "ml": load("ml_signal_equity.csv"),
    "combo": load("portfolio_combo_equity.csv", col="equity"),
}
# v375/p2也顺带对比
for nm, f in (("v375", "v375_signal_equity.csv"), ("p2", "p2_signal_equity.csv")):
    try:
        nav[nm] = load(f)
    except Exception:
        pass

print("== 全期(各自起点) ==")
for k, v in nav.items():
    s = stats(v)
    if s:
        print(f"{k:6s} {v.index[0].date()}~{v.index[-1].date()} 终值{s['end']:>10.0f} "
              f"CAGR{s['cagr']*100:>7.2f}% vol{s['vol']*100:>6.2f}% "
              f"Sharpe{s['sharpe']:>6.2f} MDD{s['mdd']*100:>7.2f}%")

# 重叠期 2020-01-01 起 (ml信号起点)
st = "2020-01-01"
sub = {k: v[v.index >= st] for k, v in nav.items()}
print(f"\n== 重叠期 {st}~ (ml可用后) ==")
for k, v in sub.items():
    s = stats(v)
    if s:
        print(f"{k:6s} 终值{s['end']:>10.0f} CAGR{s['cagr']*100:>7.2f}% "
              f"Sharpe{s['sharpe']:>6.2f} MDD{s['mdd']*100:>7.2f}%")

# 相关矩阵(重叠期)
rets = pd.DataFrame({k: v.pct_change() for k, v in sub.items()}).dropna(how="all")
common = ["bp_ep", "ml", "v375", "p2"]
have = [c for c in common if c in rets.columns]
corr = rets[have].corr()
print("\n== 日收益相关矩阵(2020+) ==")
print(corr.round(3).to_string())

# 50/50再平衡对比(月度) vs combo实际
bpep_r = sub["bp_ep"].resample("ME").last().pct_change()
ml_r = sub["ml"].resample("ME").last().pct_change()
both = pd.concat([bpep_r, ml_r], axis=1, keys=["bp_ep", "ml"]).dropna()
ew = (0.5 * both["bp_ep"] + 0.5 * both["ml"])
ew_nav = (1 + ew).cumprod()
s = stats(ew_nav, freq=12)
print(f"\n== 50/50月度再平衡(2020+) ==")
print(f"CAGR{s['cagr']*100:.2f}% Sharpe{s['sharpe']:.2f} MDD{s['mdd']*100:.2f}% 终值{s['end']:.3f}")

# combo实际权重反推: combo日收益 = w*bpep + w*ml?
r = pd.DataFrame({"combo": sub["combo"].pct_change(),
                  "bp_ep": sub["bp_ep"].pct_change(),
                  "ml": sub["ml"].pct_change()}).dropna()
if len(r) > 100:
    x = np.column_stack([r["bp_ep"].values, r["ml"].values, np.ones(len(r))])
    y = r["combo"].values
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    print(f"\ncombo收益回归: w_bpep={beta[0]:.3f} w_ml={beta[1]:.3f} alpha/日={beta[2]*1e4:.2f}bp")
