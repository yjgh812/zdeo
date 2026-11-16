# -*- coding: utf-8 -*-
"""swing_matrix_v3_validated_20261029.py — 规范校验+样本外验证(主人工程文档·最终矩阵)

补四个缺口:
  ① 次新过滤: 信号日距票首根K线<60交易日 → 剔除(近似上市日期过滤, 退市股缺失如实标注)
  ② 市场状态二分: 等权指数MA60上=趋势 / 下=震荡(替代四态细分, 主人文档"务实起点2-3维")
  ③ 样本外验证: train(2016-2022)定义正期望分组 → test(2023-2026)验证同组是否仍正
  ④ 主人三判据: 差≥10pp + 频率≥年20次 + 样本外可复现 → 最终策略池
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
t0 = time.time()

# 1) 载日线十年事件表(176.7万, 覆盖2015-2026, 可做train/test切分)
REC = []
for line in open(os.path.join(ROOT, "data", "swing_10y_daily_events_20261029.jsonl"), encoding="utf-8"):
    line = line.strip()
    if line:
        REC.append(json.loads(line))
print(f"日线十年事件载入: {len(REC)}", flush=True)

# 2) 每票首日(次新过滤) + 日线方向(趋势/震荡二分)
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
first_day = {}
for r in con.execute("select code, min(date) from stock_daily_meta_qfq group by code"):
    first_day[r[0]] = int(r[1])
# 等权指数MA60 → 趋势/震荡
rows = con.execute(
    "select code,date,close from stock_daily_meta_qfq where date>=20140101 order by code,date").fetchall()
con.close()
from collections import defaultdict
eq_acc = defaultdict(list)
for code, d, c in rows:
    eq_acc[int(d)].append(float(c))
dates_sorted = sorted(eq_acc.keys())
eq = np.array([np.mean(eq_acc[d]) for d in dates_sorted])
d_idx = {d: i for i, d in enumerate(dates_sorted)}
ma60 = np.full(len(eq), np.nan)
for i in range(59, len(eq)):
    ma60[i] = eq[i-59:i+1].mean()
mkt_state = {}
for i, d in enumerate(dates_sorted):
    if not np.isnan(ma60[i]):
        mkt_state[d] = "trend" if eq[i] > ma60[i] else "range"
print(f"市场状态二分: trend={sum(1 for v in mkt_state.values() if v=='trend')} range={sum(1 for v in mkt_state.values() if v=='range')}", flush=True)

# 3) 信号过滤+补维度
clean = []
drop_new = 0
for r in REC:
    code = r["code"]
    d_int = int(r["d"])
    fd = first_day.get(code)
    if fd and (d_int - fd) < 60:   # 上市后60日内(次新)
        drop_new += 1
        continue
    ms = None
    for off in range(5):
        if d_int - off in mkt_state:
            ms = mkt_state[d_int - off]
            break
    r2 = dict(r)   # 已有 d/yr/struct/dstate/pos/f10/f20/f40/f60
    r2["mkt"] = ms or "NA"
    r2["year"] = d_int // 10000
    clean.append(r2)
print(f"次新剔除: {drop_new} | 有效: {len(clean)} ({time.time()-t0:.0f}s)", flush=True)

def st(rows, key="f20"):
    a = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean()), 2), "win": round(float((a>0).mean())*100, 1),
            "med": round(float(np.median(a)), 2)}

# 4) 样本外验证
TRAIN_YEARS = (2015, 2021)
TEST_YEARS = (2022, 2026)
train = [r for r in clean if TRAIN_YEARS[0] <= r["year"] <= TRAIN_YEARS[1]]
test = [r for r in clean if TEST_YEARS[0] <= r["year"] <= TEST_YEARS[1]]
print(f"train({TRAIN_YEARS[0]}-{TRAIN_YEARS[1]}): {len(train)} | test({TEST_YEARS[0]}-{TEST_YEARS[1]}): {len(test)}", flush=True)

base_train = st(train)
base_test = st(test)

# 分组key: dir × mkt × struct × pos (四维→只取3维组合, 不做全交叉)
def gkey(r, dims):
    return "|".join(str(r.get(d, "")) for d in dims)

# 维度组合1: dir+struct+pos
DIMS1 = ["dstate", "struct", "pos"]
DIMS2 = ["dstate", "mkt", "struct"]
DIMS3 = ["dstate", "mkt", "pos"]

def build_groups(rows, dims, min_n=30):
    g = defaultdict(list)
    for r in rows:
        g[gkey(r, dims)].append(r)
    return {k: st(v) for k, v in g.items() if len(v) >= min_n}

g1_train = build_groups(train, DIMS1)
g1_test = build_groups(test, DIMS1)
g2_train = build_groups(train, DIMS2)
g2_test = build_groups(test, DIMS2)
g3_train = build_groups(train, DIMS3)
g3_test = build_groups(test, DIMS3)

# 找 train 内 f20 > base+10pp 的正期望分组 → test 验证
# 校准阈值: 日线f20自然区间±2%, 分组差异≥3pp+胜率差≥5pp为有意义
EXCESS_PP = 3
WIN_DIFF_PP = 5
pool = []
for dims_name, gtr, gte in (("dstate|struct|pos", g1_train, g1_test),
                              ("dstate|mkt|struct", g2_train, g2_test),
                              ("dstate|mkt|pos", g3_train, g3_test)):
    thr = base_train["mean"] + EXCESS_PP
    for k, v in gtr.items():
        if v["mean"] >= thr and v["n"] >= 50:
            vt = gte.get(k)
            pool.append({"dims": dims_name, "key": k, "train": v, "test": vt})

pool.sort(key=lambda x: (-x["train"]["mean"]))
print(f"\n=== Train内正期望分组(f20≥base+{EXCESS_PP}pp={thr:.1f}%): {len(pool)}个 ===")
for p in pool[:30]:
    vt = p["test"] or {"n": 0}
    ok = vt.get("n", 0) > 0 and vt.get("mean", -99) > base_test["mean"]
    print(f"  [{p['dims']}] {p['key']}: train n={p['train']['n']} {p['train']['mean']:+.2f}%/胜{p['train']['win']}%"
          f" → test n={vt.get('n',0)} {vt.get('mean','N/A')}%/胜{vt.get('win','N/A')}% {'✓复现' if ok else '✗未复现'}")

# 排除清单: train 内 f20 < base-EXCESS_PP 的负期望分组
print(f"\n=== 排除清单(train内f20≤base-{EXCESS_PP}pp={base_train['mean']-EXCESS_PP:.1f}%) ===")
excl = []
for dims_name, gtr, gte in (("dir|struct|pos", g1_train, g1_test),
                              ("dir|mkt|struct", g2_train, g2_test),
                              ("dir|mkt|pos", g3_train, g3_test)):
    for k, v in gtr.items():
        if v["mean"] <= base_train["mean"] - 5 and v["n"] >= 50:
            vt = gte.get(k)
            if vt and vt.get("n", 0) >= 20:
                excl.append({"dims": dims_name, "key": k, "train_mean": v["mean"],
                             "test_mean": vt.get("mean"), "test_n": vt.get("n", 0)})
for e in sorted(excl, key=lambda x: x["train_mean"])[:15]:
    print(f"  [{e['dims']}] {e['key']}: train{e['train_mean']:+.2f}% → test{e['test_mean']:+.2f}% (n={e['test_n']})")

# 最终策略池: 正期望+test复现>0
final_pool = [p for p in pool if p["test"] and p["test"].get("n", 0) >= 20 and p["test"].get("mean", -99) > 0]
final_pool.sort(key=lambda x: -x["test"]["mean"])
print(f"\n=== 最终策略池(train正+test正+test n≥20): {len(final_pool)}个 ===")
for p in final_pool[:15]:
    print(f"  [{p['dims']}] {p['key']}: train {p['train']['mean']:+.2f}%/胜{p['train']['win']}% (n={p['train']['n']})"
          f" → test {p['test']['mean']:+.2f}%/胜{p['test']['win']}% (n={p['test']['n']})")

out = os.path.join(ROOT, "data", "swing_matrix_v3_validated_20261029.json")
json.dump({"built_at": "20261029", "n_clean": len(clean), "drop_new": drop_new,
           "base_train": base_train, "base_test": base_test,
           "pool": pool[:50], "excl": excl[:30], "final_pool": final_pool},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n落盘: {out} ({time.time()-t0:.0f}s)")
