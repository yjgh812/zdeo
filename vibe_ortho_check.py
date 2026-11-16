# -*- coding: utf-8 -*-
"""五维权重正交化验证：对有历史的维度算相关矩阵+VIF（Baker-Wurgler 正交化思路）。
只读；产物仅供决策，不写任何生产表。"""
import sqlite3, numpy as np, pandas as pd, sys
sys.path.insert(0, r"D:\zcodesj")
import sentiment_composite as sc

HSJ = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
IDX = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
# 1. 情绪复合历史
senti = pd.read_csv(r"D:\zcodesj\_cache\sentiment_history.csv", dtype={"date":str})
senti = senti[["date","score"]].set_index("date")["score"].astype(float); senti.index = senti.index.astype(int)
# 2. 量能比历史（amt 当日/前5日均）
con = sqlite3.connect(f"file:{HSJ}?mode=ro", uri=True, timeout=300)
rows = con.execute("SELECT date, SUM(amt) FROM stock_daily_hsj WHERE date>=20250701 AND close>0 AND substr(code,1,2) IN ('60','00','30','68','92') GROUP BY date").fetchall()
con.close()
amt = pd.Series({int(d): a for d,a in rows}).sort_index()
vol = amt / amt.shift(1).rolling(5).mean()
# 3. 指数动能历史
con = sqlite3.connect(f"file:{IDX}?mode=ro", uri=True, timeout=120)
res = {}
for code in ["000001","399001","399006"]:
    rows = con.execute("SELECT date, close FROM index_daily WHERE index_code=? AND date>=20250101 ORDER BY date", (code,)).fetchall()
    s = pd.Series({int(d): c for d,c in rows}).sort_index()
    close = s
    ma5 = s.rolling(5).mean(); ma20 = s.rolling(20).mean()
    trend = np.where((close>ma5)&(ma5>ma20), 100, np.where(close>ma5, 60, np.where(close>ma20, 40, 0)))
    pct5 = close.pct_change(5)*100
    mom = np.where(pct5>=2,100, np.where(pct5>=0,60, np.where(pct5>=-2,30,0)))
    res[code] = pd.Series(0.6*trend + 0.4*mom, index=s.index).dropna()
con.close()
idxk = 0.30*res["000001"] + 0.35*res["399001"] + 0.35*res["399006"]
df = pd.concat({"sentiment": senti, "volume": vol, "index": idxk}, axis=1, join="inner").dropna()
df = df[df.index >= 20250801]
print(f"样本: {len(df)} 个交易日 ({df.index.min()}-{df.index.max()})")
print("\n=== 相关矩阵 ===")
corr = df.corr()
print(corr.round(3).to_string())
print("\n=== VIF（>5 提示共线，>10 严重）===")
inv = np.linalg.inv(corr.to_numpy())
for i, c in enumerate(df.columns):
    print(f"  {c}: VIF={inv[i,i]:.2f}")
print("\n=== 若两两相关>0.7 → 重复计分需降权 ===")
for i in range(len(df.columns)):
    for j in range(i+1, len(df.columns)):
        r = corr.iloc[i,j]
        flag = " 🔴重复" if abs(r) > 0.7 else (" 🟡偏强" if abs(r) > 0.5 else "")
        print(f"  {df.columns[i]} vs {df.columns[j]}: r={r:.3f}{flag}")
