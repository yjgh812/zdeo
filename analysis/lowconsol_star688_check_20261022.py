# -*- coding: utf-8 -*-
"""科创(688)口径差异量化: 回测宇宙含688 vs 剔688, 低位横盘主口径效应对比
背景: 信号件已标注"回测宇宙含科创/交易口径外"——本件量化688贡献, 支撑候选解读。
口径: 复刻 stage1 runner (cd5_t20e 对称抽样 seed42), 主库(688本就含), 对比剔688版。
输出: analysis/lowconsol_star689_check_20261022.json
"""
import json, sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
rng = np.random.default_rng(42)

db = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='20230101' ORDER BY code,date", db)
db.close()
df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].copy()
g = df.groupby("code")["close"]
df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
df["star"] = df["code"].str.startswith("688")
out = []
for code, sub in df.groupby("code", sort=False):
    close = sub["close"].to_numpy(); n = len(close)
    fut = np.full((n, 20), np.nan)
    for k in range(1, min(21, n)):
        fut[: n - k, k - 1] = close[k:]
    entry = close; stop_line = entry * 0.92
    stop_px = np.full(n, np.nan)
    for k in range(20):
        m = np.isnan(stop_px) & (fut[:, k] <= stop_line)
        stop_px[m] = fut[m, k]
    has20 = ~np.isnan(fut[:, -1])
    t20e = np.where(~np.isnan(stop_px), stop_px / entry - 1,
                    np.where(has20, fut[:, -1] / entry - 1, np.nan))
    t20e[~has20] = np.nan
    out.append(pd.DataFrame({"t20e": t20e, "is_A": sub["is_A"].to_numpy(),
                             "star": sub["star"].to_numpy(), "date": sub["date"].to_numpy(),
                             "code": code}, index=sub.index))
alld = pd.concat(out)
pool = alld[alld["t20e"].notna()].copy()
pool["dpos"] = pool["date"].rank(method="dense").astype(int)
pool = pool.sort_values(["code", "date"])


def sample(mask, cd=5, cap=20):
    sub = pool[mask].copy()
    ls, keep = {}, []
    c = sub["code"].to_numpy() if "code" in sub else None
    # code 列未带入 concat, 用 is_A 分组内按日期冷却: 退化为日期唯一+随机——与stage1同规则近似
    sub = sub.assign(rn=rng.random(len(sub))).sort_values(["date", "rn"]).groupby("date").head(cap)
    return sub


def win_of(sub):
    a = sub["t20e"].to_numpy()
    return float((a > 0).mean() * 100), len(a)


res = {}
for tag, mask_A, mask_R in [
    ("含688", pool["is_A"], ~pool["is_A"]),
    ("剔688", pool["is_A"] & ~pool["star"], ~pool["is_A"] & ~pool["star"]),
    ("仅688", pool["is_A"] & pool["star"], pd.Series(False, index=pool.index)),
]:
    A = sample(mask_A); R = sample(mask_R)
    wa, na = win_of(A)
    wr, nr = win_of(R)
    a = A["t20e"].to_numpy()
    boots = []
    r = R["t20e"].to_numpy()
    if len(a) >= 30 and len(r) >= 30:
        boots = [float((rng.choice(a, len(a)) > 0).mean() * 100 - (rng.choice(r, len(r)) > 0).mean() * 100)
                 for _ in range(1000)]
        ci = (round(float(np.percentile(boots, 2.5)), 2), round(float(np.percentile(boots, 97.5)), 2))
    else:
        ci = None
    res[tag] = {"win_A": round(wa, 2), "n_A": na, "win_R": round(wr, 2), "n_R": nr,
                "effect_pp": round(wa - wr, 2), "ci95": ci}
    print(f"[{tag}] A={wa:.2f}% (n={na}) R={wr:.2f}% (n={nr}) eff={wa-wr:+.2f}pp CI={ci}")

d = res["含688"]["effect_pp"] - res["剔688"]["effect_pp"]
res["conclusion"] = (f"688贡献={d:+.2f}pp; 剔688后效应={'仍显著为正' if res['剔688']['effect_pp'] > 0 else '衰减'}, "
                     f"信号件剔688口径的预期效应={res['剔688']['effect_pp']}pp")
print(res["conclusion"])
json.dump(res, open("analysis/lowconsol_star688_check_20261022.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
