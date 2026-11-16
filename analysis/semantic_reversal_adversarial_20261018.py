# -*- coding: utf-8 -*-
"""semantic_reversal_adversarial_20261018.py — 语义反转假说对抗性检验·第一轮 (2026-10-18, DeepSeek外部审计吸收项V9)

假说: 「放量≠确认, 低位+缩量才正格」跨6域零反例(量价学习收官的核心元规律)。
DeepSeek F-2 批评: 6域若同源同窗, "零反例"可能只是同一数据的不同切片; 应主动找理论反例。
本件=对抗第一轮(有界): 对正格最强代表 **T1 低位缩量破跌停**(样内 n≈7130, fwd20 med≈+6.31/win≈68.8,
10/17 臂级对照激活依据) 做**子域压力测试**——若某子域 med20<0 且 n≥30, 即假说需加限定域(反例候选);
若全部子域为正, 假说强度升级为"经子域对抗检验"。

七分域(全部事先钉死, 无后选): ①前5日跌幅分桶(阴跌 vs 急跌) ②vr20 枯竭深度分桶
③价格带(≤3元壳股域=对抗候选) ④创业板20cm时代 vs 其他 ⑤次日开盘溢价符号(承接 vs 弃)
⑥触及深度(贴线 vs 深水) ⑦年度(已知2023失效年复核)
口径: 与 low_repair_family_arm_compare_20261017.py 逐位同源(抽取/去重/fwd 全复刻),
交叉核对锚=analysis/low_repair_family_arm_compare_result_20261017.json 的 T1 med20/n。
⚠️ 幸存者敞口注记: qfq=wb 活股源(2026-10-18 V4 发现), 退役段未回填——量级前测 0.6% 事件级。
输出: analysis/semantic_reversal_adversarial_20261018_out.md (幂等直出) 零交易权纯研究。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ANCHOR = r"D:\zcodesj\analysis\low_repair_family_arm_compare_result_20261017.json"
OUT_MD = r"D:\zcodesj\analysis\semantic_reversal_adversarial_20261018_out.md"

conn = sqlite3.connect(r"file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20160101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
conn.close()
df = df[(df["volume"] > 0) & (df["open"] > 0)].copy()
df["date"] = df["date"].astype(str)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
df["ma20v"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
df["vr20"] = df["volume"] / df["ma20v"]
df["min250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).min())
df["max250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).max())
df["pos250"] = (df["close"] - df["min250"]) / (df["max250"] - df["min250"])
df["prev_close"] = g["close"].shift(1)
df["rowid"] = df.groupby("code").cumcount()
gem20 = (df["code"].str.startswith(("300", "301"))) & (df["date"] >= "20200824")
thr = np.where(gem20, 19.5, 9.7)
hi_pct = df["high"] / df["prev_close"] * 100 - 100
lo_pct = df["low"] / df["prev_close"] * 100 - 100
df["lo_pct"] = lo_pct
df["dt_touch"] = lo_pct <= -thr
df["dt_close"] = (df["change_pct"] <= -thr) & (df["close"] <= df["low"] + 1e-9)
df["close5"] = g["close"].shift(5)
df["ret5d"] = df["close"] / df["close5"] - 1  # 事件前5日累计(阴跌 vs 急跌判据)
for k in (5, 20):
    df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
df["open1"] = g["open"].shift(-1)
df["open_prem"] = df["open1"] / df["close"] - 1

base = df.dropna(subset=["vr20", "pos250", "change_pct", "ret5d"]).copy()
base = base[base["fwd20"].notna()]
t1 = base[base["pos250"] <= 0.3]  # 触跌停未封+缩量条件在下一段合取
t1 = t1[t1["dt_touch"] & ~t1["dt_close"] & (t1["vr20"] < 1.5)].copy()


def dedup5(sub):
    keep = np.zeros(len(sub), dtype=bool)
    last = {}
    codes = sub["code"].to_numpy()
    rows = sub["rowid"].to_numpy()
    for i in range(len(sub)):
        c = codes[i]
        if rows[i] - last.get(c, -10**9) >= 5:
            keep[i] = True
            last[c] = rows[i]
    return sub[keep]


t1 = dedup5(t1)

# 交叉核对锚: 与 arm_compare 结果 json 的 T1 对齐
anchor = json.load(open(ANCHOR, encoding="utf-8"))
a_t1 = anchor.get("T1_低位缩量破跌停", {})
a_med = a_t1.get("fwd20", {}).get("med")
a_n = a_t1.get("n")
my_med = round(float(t1["fwd20"].median()) * 100, 2)
assert a_n and abs(len(t1) - a_n) <= a_n * 0.02, f"n 漂移超2%: 本件{len(t1)} vs 锚{a_n} — 抽取口径漂移, 结果作废"
assert a_med is not None and abs(my_med - a_med) <= 0.3, f"med20 漂移: 本件{my_med} vs 锚{a_med}"
print(f"[锚核] T1 n={len(t1)} (锚{a_n}) med20={my_med}% (锚{a_med}) — 同口径复现 PASS")


def stat(sub, label):
    s = sub["fwd20"].dropna()
    med = float(s.median()) * 100 if len(s) else None
    win = float((s > 0).mean()) * 100 if len(s) else None
    m5 = sub["fwd5"].dropna()
    med5 = float(m5.median()) * 100 if len(m5) else None
    adverse = med is not None and med < 0 and len(s) >= 30
    return {"label": label, "n": int(len(s)), "med5": round(med5, 2) if med5 is not None else None,
            "med20": round(med, 2) if med is not None else None,
            "win20": round(win, 1) if win is not None else None, "adverse": adverse}


cuts = [
    ("①前5日跌幅分桶", [
        ("深阴跌 ret5d≤-15%", t1["ret5d"] <= -0.15),
        ("中阴跌 -15%<ret5d≤-8%", (t1["ret5d"] > -0.15) & (t1["ret5d"] <= -0.08)),
        ("急跌型 ret5d>-8%", t1["ret5d"] > -0.08),
    ]),
    ("②vr20 枯竭深度", [
        ("极端枯竭 vr<0.5", t1["vr20"] < 0.5),
        ("深枯竭 0.5≤vr<1.0", (t1["vr20"] >= 0.5) & (t1["vr20"] < 1.0)),
        ("缩量上沿 1.0≤vr<1.5", t1["vr20"] >= 1.0),
    ]),
    ("③价格带(壳股域对抗)", [
        ("≤3元 壳股域", t1["close"] <= 3),
        ("3-6元", (t1["close"] > 3) & (t1["close"] <= 6)),
        (">6元", t1["close"] > 6),
    ]),
    ("④板块/制度域", [
        ("创业板20cm时代", gem20[t1.index]),
        ("非20cm域(主板全史+创业板旧制)", ~gem20[t1.index]),
    ]),
    ("⑤次日开盘承接(执行层语义)", [
        ("高开承接 prem>0", t1["open_prem"] > 0),
        ("低开弃 prem≤0", t1["open_prem"] <= 0),
    ]),
    ("⑥触及深度", [
        ("深水触跌 lo≤-(thr+2pp)", t1["lo_pct"] <= -(np.where(gem20[t1.index], 19.5, 9.7) + 2)),
        ("贴线触及(其余)", t1["lo_pct"] > -(np.where(gem20[t1.index], 19.5, 9.7) + 2)),
    ]),
]

md = ["# 语义反转假说对抗性检验·第一轮: T1 家族子域压力测试(V9)", "",
      "> 脚本 analysis/semantic_reversal_adversarial_20261018.py 直出 | T1 抽取与 low_repair_family_arm_compare_20261017.py 逐位同源",
      f"> 交叉核对锚: n={len(t1)}(锚{a_n}) / med20={my_med}%(锚{a_med}) 断言通过 | 数据=wb qfq活股源(幸存者敞口未回填, 量级前测0.6%事件级, 见 V4 复核)",
      "> 判据(事先钉死): 子域 med20<0 且 n≥30 → 对抗性反例候选(假说须加限定域); 全部子域为正 → 强度升级", "",
      "## 总体", "",
      f"T1 全域(去重后): n={len(t1)} / med20={my_med}% / win20={round(float((t1['fwd20']>0).mean())*100,1)}% / med5={round(float(t1['fwd5'].median())*100,2)}%", "",
      "## 七分域压力测试", "", "| 分域 | 子域 | n | med5 | med20 | win20 | 反例候选 |", "|---|---|---|---|---|---|---|"]
adversaries = []
for dom, subs in cuts:
    for name, mask in subs:
        if not isinstance(mask, pd.Series):
            mask = pd.Series(np.asarray(mask, dtype=bool), index=t1.index)
        sub = t1[mask.fillna(False)]
        r = stat(sub, name)
        flag = "🔴" if r["adverse"] else ""
        if r["adverse"]:
            adversaries.append(f"{dom}/{name}")
        md.append(f"| {dom} | {name} | {r['n']} | {r['med5']}% | {r['med20']}% | {r['win20']}% | {flag} |")
    md.append("|---|---|---|---|---|---|---|")

year = t1["date"].str[:4]
md += ["", "⑦年度复核(2023 已知失效年):"]
for y in sorted(year.unique()):
    r = stat(t1[year == y], y)
    md.append(f"- {y}: n={r['n']} med20={r['med20']}% win20={r['win20']}%")
md += ["", "## 判定", ""]
if adversaries:
    md.append(f"**对抗性反例候选 {len(adversaries)} 个子域**: " + "; ".join(adversaries))
    md.append("")
    md.append("- 假说须加限定域——反例子域即限定条件候选(如『壳股域排除』『阴跌深度阈值』),")
    md.append("- 任何限定化属假说修订, 须主人裁决 + 涉接线须 prereg 新指纹; 本件零交易权只呈现。")
else:
    md.append("**全部子域为正——假说本轮对抗存活**, 强度从「跨6域零反例」升级为「跨6域零反例+T1 七分域子域对抗存活」;")
    md.append("- 完整对抗仍缺: 其余5域(中板/炸板/反包/尾盘/调占量)的同强度子域压力测试, 属后续轮候选(呈请);")
    md.append("- 幸存者敞口统一注记: 回填退市段后须复跑确认反例面不变。")
md += ["", "## 结论边界", "",
       "- 分桶为事后探索性切片(分桶界事先钉死但子域选择本身是搜索)——反例候选须在新样本/prereg 框架下复核, 禁直接当定谳;",
       "- 本件不改低位修复族 prereg(10/20 起 OOS)任何判据; 分域信息仅供执行参考与假说加固。"]
with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write("\n".join(md) + "\n")
print(f"[OK] 反例候选 {len(adversaries)}: {adversaries if adversaries else '无(假说存活)'}")
print(f"[OK] 已直出 {OUT_MD}")
