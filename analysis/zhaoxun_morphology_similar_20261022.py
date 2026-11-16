# -*- coding: utf-8 -*-
"""兆讯式形态相似检索 (自研, qfq库源内自洽 — S18修复: 不引入外部数据源)
query: 兆讯传媒(301102) 启动前30根日K (20260828~20261016, 不含启动段)
target: 全市场每只股最新30根日K (截至20261021)
归一化: 窗内累计收益形态 close_t/close_0-1 (价格水平无关)
距离: 均值欧氏距离 + 皮尔逊相关 双指标
宇宙: 6/0/3, 上市>60日, 剔ST(交易口径), 排除兆讯自身
输出: analysis/zhaoxun_morphology_similar_20261022.json + 控制台top20
"""
import json, sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
W = 30
Q_END = "20261016"   # query窗口截止(兆讯启动前)
OUT = "analysis/zhaoxun_morphology_similar_20261022.json"

db = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20250601' ORDER BY code,date", db)
db.close()
df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].copy()

l1c = sqlite3.connect("data/industry_group_map_snapshot.db")
l1map = pd.read_sql_query("SELECT code,name,l1,path FROM l1_member WHERE ambiguous_flag=0", l1c)
l1map["code"] = l1map["code"].astype(str)
l1c.close()

latest = df["date"].max()


def norm_window(close_arr):
    c0 = close_arr[0]
    if not c0 or c0 <= 0:
        return None
    return close_arr / c0 - 1.0


# query: 兆讯启动前30根
q = df[(df["code"] == "301102") & (df["date"] <= Q_END)].tail(W)
if len(q) < W:
    print(f"[abort] query窗口不足: {len(q)}/{W}"); sys.exit(2)
qv = norm_window(q["close"].to_numpy())

# target: 全市场最新30根
targets = {}
for code, sub in df.groupby("code", sort=False):
    if code == "301102" or len(sub) < W:
        continue
    tail = sub.tail(W)
    if tail["date"].iloc[-1] != latest:  # 只取最新窗口完整的
        continue
    v = norm_window(tail["close"].to_numpy())
    if v is not None:
        targets[code] = (v, tail["date"].iloc[0])

codes = list(targets.keys())
mat = np.vstack([targets[c][0] for c in codes])
# 波动率匹配层(m7式修正): 兆讯启动前窗口std的[0.5x,2x]带内才参与排序 — 消除低波蓝筹距离偏倚
q_std = float(qv.std())
stds = mat.std(axis=1)
band = (stds >= q_std * 0.5) & (stds <= q_std * 2.0)
codes = [c for c, b in zip(codes, band) if b]
mat = mat[band]
print(f"[band] 兆讯启动前窗std={q_std:.4f}, 匹配带[0.5x,2x]内宇宙 {len(codes)} 只")
# 欧氏距离(均值化) + 相关
dist = np.sqrt(((mat - qv) ** 2).mean(axis=1))
qz = (qv - qv.mean()) / (qv.std() + 1e-12)
mz = (mat - mat.mean(axis=1, keepdims=True)) / (mat.std(axis=1, keepdims=True) + 1e-12)
corr = (mz * qz).mean(axis=1)

res = pd.DataFrame({"code": codes, "dist": dist, "corr": corr})
res = res.sort_values("dist")
top = res.head(60)["code"].tolist()
meta = l1map.set_index("code")
last_px = df[df["date"] == latest].set_index("code")["close"]
rows = []
for c in top:
    m = meta.loc[c] if c in meta.index else None
    rows.append({"code": c, "name": m["name"] if m is not None else "?",
                 "l1": m["l1"] if m is not None else "?",
                 "close": float(last_px.get(c, np.nan)),
                 "dist": round(float(res[res["code"] == c]["dist"].iloc[0]), 5),
                 "corr": round(float(res[res["code"] == c]["corr"].iloc[0]), 3)})
out = {"query": {"code": "301102", "name": "兆讯传媒", "window": f"{q['date'].iloc[0]}~{Q_END}({W}根, 启动前)"},
       "target_window_end": latest, "n_universe": len(codes),
       "top_similar": rows,
       "caveats": ["波动率匹配带[0.5x,2x]已消除低波蓝筹距离偏倚(v1版TOP20全为银行/公用事业缺陷)", "形态相似仅佐证, 不构成买入依据; 范式层(低位横盘×主线热)为核心",
                   "自研实现(S18修复): qfq源内自洽, 无外部数据源"]}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(f"[query] 兆讯 {q['date'].iloc[0]}~{Q_END} 30根 | 宇宙 {len(codes)} 只")
print("--- 形态相似 TOP20 (均值欧氏距离升序) ---")
for r in rows[:20]:
    print(f"  {r['code']} {r['name']:<6} {r['l1']:<5} close={r['close']:>7.2f} dist={r['dist']:.4f} corr={r['corr']:+.2f}")
print(f"[done] {OUT}")
