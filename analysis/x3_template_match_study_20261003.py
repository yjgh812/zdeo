# -*- coding: utf-8 -*-
"""X3事件研究: 历史模板形态匹配的后验收益 (她「类推万兴科技180302」思想的量化)
模板=5日收盘的4个日收益向量; 全市场滚动4收益窗算相关; corr>=0.99为新事件(每股10日去重);
对照: 正模板(君正/模塑启动前) vs 负模板(建元信托崩盘前) vs 基线。
自检: 模板股自身窗口corr必须=1.0。
"""
import sqlite3

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,close FROM stock_daily_meta_qfq "
    "WHERE date>='20190101' AND date<='20260930' "
    "AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
    conn)
conn.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
df["r"] = df.groupby("code")["close"].pct_change()
for i in range(1, 5):
    df[f"r{i}"] = df.groupby("code")["r"].shift(4 - i)
df["f20"] = df.groupby("code")["close"].shift(-20) / df["close"] - 1
df = df.dropna(subset=["r1", "r2", "r3", "r4"])
print("rows", len(df))

# 同一行内4分量向量范数(此前误用跨行rolling=混叠bug, 自检抓出)
df["rsum"] = df["r1"] + df["r2"] + df["r3"] + df["r4"]
df["r2sum"] = df["r1"] ** 2 + df["r2"] ** 2 + df["r3"] ** 2 + df["r4"] ** 2
df["den_r"] = np.sqrt(df["r2sum"] - df["rsum"] ** 2 / 4)


def corr_to(tpl):
    tb = np.mean(tpl)
    dt = tpl - tb
    st = np.sqrt((dt * dt).sum())
    num = np.zeros(len(df))
    for i in range(1, 5):
        num += df[f"r{i}"].fillna(0) * (tpl[i - 1] - tb)
    return num / (df["den_r"] * st).replace([np.inf], np.nan)


TEMPLATES = {
    "君正启动前(正)": ("601216", [20190715, 20200716, 20200717, 20200720, 20200721]),
    "模塑启动前(正)": ("000700", [20200106, 20200107, 20200108, 20200109, 20200110]),
    "建元崩盘前(负)": ("600816", [20190221, 20190225, 20190226, 20190227, 20190228]),
}

results = {}
for name, (code, dates) in TEMPLATES.items():
    sub = df[(df["code"] == code) & (df["date"].isin(dates))].sort_values("date")
    if len(sub) < 5:
        print(name, "模板取数失败", len(sub))
        continue
    tpl = sub["r"].tail(4).to_numpy()  # 4个日收益(第2~5根)
    c = corr_to(tpl)
    self_corr = float(c[(df["code"] == code) & (df["date"] == dates[-1])].iloc[0])
    ev = df[(c >= 0.99) & (df["f20"].notna())].copy()
    ev["c"] = c
    # 每股10日去重
    ev = ev.sort_values(["code", "date"])
    keep = []
    last = {}
    for code_, d_ in zip(ev["code"], ev["date"]):
        if code_ not in last or d_ - last[code_] >= 10:
            keep.append(True)
            last[code_] = d_
        else:
            keep.append(False)
    ev = ev[np.array(keep)]
    s = ev["f20"] * 100
    results[name] = (len(ev), s.mean(), s.median(), (s > 0).mean() * 100)
    print(f"{name}: 自检corr={self_corr:.4f} 事件n={len(ev)} "
          f"f20 mean={s.mean():+.2f}% median={s.median():+.2f}% win={(s>0).mean()*100:.1f}%")
    ev[["code", "date", "f20"]].to_csv(
        rf"D:\zcodesj\reports\x3_tpl_{name[:2]}_events.csv", index=False, encoding="utf-8-sig")

base = df[df["f20"].notna()]["f20"] * 100
print(f"基线(全市场全部日): n={len(base)} f20 mean={base.mean():+.2f}% median={base.median():+.2f}% win={(base>0).mean()*100:.1f}%")
