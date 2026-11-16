# -*- coding: utf-8 -*-
"""
A股跳空缺口回补实证（无前视、qfq 数据）
- 向上跳空缺口：当日 low > 昨日 high（昨高-今低之间无价格重叠）
- 统计: 形成后 N=3/5/10/20 交易日内是否回补(最低价<=缺口下沿=昨日high)
- 分年度 + 全样本
"""
import sqlite3, pandas as pd, numpy as np
pd.set_option('display.width', 200)

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

# 取一个代表性样本：沪深300成分(000300) + 随机200只，2016-2026
# 先探测表数据范围
cnt = con.execute("SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT code) FROM stock_daily_meta_qfq").fetchone()
print("qfq库: rows=%s date=[%s,%s] codes=%s" % cnt)

# 样本：均匀抽样1000只代码（避免全市场计算过重）
codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
rng = np.random.default_rng(42)
sample = rng.choice(codes, size=min(1000, len(codes)), replace=False)
print("抽样股票数:", len(sample))

ph = ",".join("?" * len(sample))
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,amount FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20160101' ORDER BY code,date",
    con, params=list(sample))
con.close()
print("读入行数:", len(df))
df["date"] = df["date"].astype(str)
df = df.sort_values(["code","date"]).reset_index(drop=True)

g = df.groupby("code", group_keys=False)
df["prev_high"] = g["high"].shift(1)
df["prev_close"] = g["close"].shift(1)
df["prev_date"] = g["date"].shift(1)

# 向上跳空缺口: 今 low > 昨 high
gap = df[df["low"] > df["prev_high"]].copy()
gap["gap_size_pct"] = (gap["low"] / gap["prev_high"] - 1) * 100
gap["gap_lower"] = gap["prev_high"]  # 缺口下沿 = 昨日最高价
print("向上跳空事件数:", len(gap), " 平均缺口幅度:%.2f%%" % gap["gap_size_pct"].mean())

# 计算回补：后续N日内最低价<=缺口下沿
def fill_stat(events, lookback):
    rows = []
    for (code, edate), ev in events.groupby(["code","date"]):
        seq = df[(df["code"]==code) & (df["date"]>edate)].head(lookback)
        if len(seq)==0: continue
        filled = (seq["low"].min() <= ev["gap_lower"].iloc[0])
        # 回补后/窗口内后续表现(相对缺口形成日收盘)
        base = ev["close"].iloc[0]
        fwd = seq["close"].iloc[-1]/base - 1
        rows.append((code, edate, filled, fwd))
    r = pd.DataFrame(rows, columns=["code","date","filled","fwd"])
    return r

gap["year"] = gap["date"].str[:4]
for N in [3,5,10,20]:
    res = fill_stat(gap, N)
    print(f"\n=== 缺口形成后 {N} 日内回补 ===")
    print("总体回补率: %.1f%%  (n=%d)" % (res["filled"].mean()*100, len(res)))
    yr = res.groupby(gap.set_index(["code","date"]).loc[res.set_index(["code","date"]).index,"year"])["filled"]
    # 简化: 按事件年
    res2 = res.copy(); res2["year"] = res2["date"].str[:4]
    print(res2.groupby("year")["filled"].agg(["count","mean"]).round(3).to_string())
    print("窗口末收益均值: %.3f%%  回补组:%.3f%%  未回补组:%.3f%%" % (
        res["fwd"].mean()*100,
        res[res["filled"]]["fwd"].mean()*100,
        res[~res["filled"]]["fwd"].mean()*100))
