# -*- coding: utf-8 -*-
"""涨停次日开盘短持有族 v0 探索 (2026-10-12, 主人「继续」令, 零交易权探索件)
问题: 涨停确认次日开盘入场, 持有1/3/5日收盘出, 净期望(含0.45%单回合费)几何?
分层: 首板/连板 × 同行业聚簇(同花顺三级) × 全日涨停家数情绪档(40/61, R37/PD-034口径族) × 开盘gap × 分年。
定位: 突破族九口径证伪覆盖域之外的独立事件族(无位置/箱体/二波条件); 探索样内, 正向也仅够呈请prereg影子, 接线须主人裁决。
口径: qfq zc单源(数据至20261009) next-bar(T+1开盘入→T+k收盘出) 净费0.45%; 宇宙00/30/60剔科创北交+现名ST/退(历史ST态不可得=近似);
      涨停判据=change_pct>=9.5(00/60)/>=19.5(30); 基线=同宇宙等权同窗(全部行, 含当日涨停票占比小, 口径如实注记);
      显著性=日聚簇t(按事件日聚合均值后再t)。
计数纪律: tmp生成器直出+自检断言。运行: python analysis/zt_nextopen_short_hold_study_20261012.py
"""
import sys, time, json
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
FEE = 0.0045
# 全量模式: argv[1]=qfq库URI(默认zc单源); wb=workbuddyspace活副本(更新更快, 只读, 分叉呈请未裁决)
DB = sys.argv[1] if len(sys.argv) > 1 else "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FULL_HISTORY = "--full" in sys.argv  # 全史(不加载数据下界)
DBW = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
OUT = r"D:\zcodesj\tmp\zt_nextopen_study_20261012" + ("_full.md" if FULL_HISTORY else ".md")
KS = (1, 3, 5)

t0 = time.time()
def log(*a): print(*a, flush=True)

# ---- 行业+名称 (wb industry_map_ths) ----
import sqlite3
cw = sqlite3.connect(DBW, uri=True)
imd = pd.read_sql("SELECT code,name,industry FROM industry_map_ths", cw)
cw.close()
imd["code"] = imd["code"].astype(str).str.zfill(6)
st_codes = set(imd.loc[imd["name"].str.contains("ST|退", na=False), "code"])
ind = dict(zip(imd["code"], imd["industry"]))

# ---- qfq 主表 ----
con = sqlite3.connect(DB, uri=True, timeout=60)
where = "" if FULL_HISTORY else " WHERE date>=20170101"
df = pd.read_sql(f"SELECT code,date,open,close,change_pct FROM stock_daily_meta_qfq{where}", con, )
con.close()
df = df[df["code"].str[:2].isin(("00", "30", "60"))].copy()
df["date"] = df["date"].astype(int)
df = df[~df["code"].isin(st_codes)]
df = df.sort_values(["code", "date"], kind="stable").reset_index(drop=True)
assert len(df) > 9_000_000, len(df)

g = df.groupby("code", sort=False)
thr = np.where(df["code"].str[:2] == "30", 19.5, 9.5)
df["zt"] = df["change_pct"] >= thr
df["prev_zt"] = g["zt"].shift(1).fillna(False)
df["next_open"] = g["open"].shift(-1)
df["age"] = g.cumcount()
for k in KS:
    df[f"fwd{k}"] = df["close"].groupby(df["code"]).shift(-k) / df["next_open"] - 1 - FEE
    df[f"fwd{k}"] = df[f"fwd{k}"].where((df[f"fwd{k}"].abs() < 0.9) & (df["next_open"] > 0))  # 零价/毛刺护栏

# ---- 事件集 ----
ev = df[(df["zt"]) & (df["age"] >= 60) & df["next_open"].notna()].copy()
for k in KS:
    ev = ev[ev[f"fwd{k}"].notna()]
ev["first_board"] = ~ev["prev_zt"]
ev["gap"] = ev["next_open"] / ev["close"] - 1
ev["ind"] = ev["code"].map(ind).fillna("未知")
ev["year"] = ev["date"] // 10000

# 聚簇: 同日同行业涨停家数(在全部zt行上计数, 不限age)
zt_all = df[df["zt"]].copy()
zt_all["ind"] = zt_all["code"].map(ind).fillna("未知")
cl = zt_all.groupby(["date", "ind"]).size().rename("cl_cnt")
day_zt = zt_all.groupby("date").size().rename("day_zt")
ev = ev.merge(cl, left_on=["date", "ind"], right_index=True, how="left")
ev = ev.merge(day_zt, left_on="date", right_index=True, how="left")

# 基线: 同宇宙等权同窗(全部有效行)
base = {}
for k in KS:
    base[k] = df[df[f"fwd{k}"].notna()].groupby("date")[f"fwd{k}"].mean()
for k in KS:
    ev[f"ex{k}"] = ev[f"fwd{k}"] - ev["date"].map(base[k])

assert len(ev) > 30_000, len(ev)
assert ev["year"].nunique() >= 7

def stat(sub, k, tag):
    """净均值/胜率/日聚簇t/超额均值/超额日聚簇t"""
    if len(sub) < 30:
        return None
    d = sub.groupby("date")[f"ex{k}"].mean()
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 and d.std(ddof=1) > 0 else float("nan")
    de = sub[f"ex{k}"]
    return {"tag": tag, "n": len(sub), "net_med": float(sub[f"fwd{k}"].median() * 100),
            "net_mean": float(sub[f"fwd{k}"].mean() * 100), "win": float((sub[f"fwd{k}"] > 0).mean() * 100),
            "ex_mean": float(de.mean() * 100),
            "t_day": float((sub.groupby('date')[f'fwd{k}'].mean().mean()) /
                           (sub.groupby('date')[f'fwd{k}'].mean().std(ddof=1) / np.sqrt(sub['date'].nunique()))) if sub['date'].nunique() > 2 else np.nan,
            "t_ex_day": float(t), "days": int(sub["date"].nunique())}

out = ["# 涨停次日开盘短持有族 v0 探索 (20261012)\n",
       f"库={DB.split('/')[-1].split('?')[0]} | 数据至{int(df['date'].max())} | "
       f"事件总数(首板+连板, age>=60): {len(ev):,} | 年份: {ev['year'].min()}-{ev['year'].max()} | 净费0.45% | 基线=同宇宙等权同窗\n"]

def table(rows, title):
    out.append(f"\n## {title}\n\n| 层 | n | 净med% | 净mean% | 胜率% | 超额mean% | 日聚簇t(净) | 日聚簇t(超额) | 事件日数 |\n|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r is None:
            continue
        out.append(f"| {r['tag']} | {r['n']:,} | {r['net_med']:+.2f} | {r['net_mean']:+.2f} | {r['win']:.1f} | {r['ex_mean']:+.2f} | {r['t_day']:+.2f} | {r['t_ex_day']:+.2f} | {r['days']} |")

for k in KS:
    rows = [stat(ev, k, f"全体(k={k})"),
            stat(ev[ev["first_board"]], k, "首板"),
            stat(ev[~ev["first_board"]], k, "连板(前日亦涨停)")]
    fb = ev[ev["first_board"]]
    rows += [stat(fb[fb["cl_cnt"] == 1], k, "首板|孤立(同行业仅此1家)"),
             stat(fb[(fb["cl_cnt"] >= 2) & (fb["cl_cnt"] <= 4)], k, "首板|聚簇2-4"),
             stat(fb[fb["cl_cnt"] >= 5], k, "首板|聚簇>=5")]
    rows += [stat(ev[ev["day_zt"] < 40], k, "情绪弱档(<40家)"),
             stat(ev[(ev["day_zt"] >= 40) & (ev["day_zt"] < 61)], k, "情绪中档(40-60)"),
             stat(ev[ev["day_zt"] >= 61], k, "情绪强档(>=61)")]
    gb = pd.cut(ev["gap"], [-1, 0.01, 0.03, 0.06, 1], labels=["gap<=1%", "1-3%", "3-6%", ">6%"])
    for lab in gb.cat.categories:
        rows.append(stat(ev[gb == lab], k, f"gap|{lab}"))
    table(rows, f"持有{k}日")

# 分年(首板, k=1/3)
for k in (1, 3):
    fb = ev[ev["first_board"]]
    rows = [stat(fb[fb["year"] == y], k, str(y)) for y in sorted(fb["year"].unique())]
    table(rows, f"首板分年(k={k})")

md = "\n".join(out)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(md)
log(md[:3000])
log(f"\n[完成] {time.time()-t0:.0f}s -> {OUT}")
