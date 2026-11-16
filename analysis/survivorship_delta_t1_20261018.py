# -*- coding: utf-8 -*-
"""survivorship_delta_t1_20261018.py — T1 家族幸存者敞口对比 (2026-10-18, 第五批待勾选项落地)

问题: wb qfq 主库零退市段(V4/P1)——T1 低位缩量破跌停族(激活中 prereg, 10/20 起 OOS)的
样内基线(n=7130/med20+6.31/68.8)跑在活股源上。回填完成后(first run 2026-10-18:
data/delisted_segments_baostock.db, 254码/428775bars), 本件量化幸存者偏差的实际影响:
  面板A(活股, wb qfq, sina口径) — 现行基线
  面板D(退市段, baostock raw×backAdjustFactor, 域内自洽) — 补回人群
  合并 = A + D (逐码分段自洽: 每只股票的全部收益只在其自身数据段内计算, 不跨源拼接)

口径(与 low_repair_family_arm_compare_20261017.py 逐位同源, D 面板价格用后复权=raw×back因子):
  T1 = 盘中触跌停(low_pct<=-9.7/-19.5创业板20cm)未封死 + pos250<=0.3 + vr20<1.5, 同票5日去重
  fwd20 = adj_close.shift(-20)/adj_close - 1 (notna截尾; 退市前不足20日的事件按截尾语义剔除, 如实计数)
对抗前沿子域: 退市段事件的「距最后bar≤120日」切片(退市边缘的缩量修复是否失效=假说最严苛考验)。
输出: analysis/survivorship_delta_t1_20261018_out.md 幂等直出。零交易权纯研究。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
WB_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DEL_DB = r"D:\zcodesj\data\delisted_segments_baostock.db"
ANCHOR = r"D:\zcodesj\analysis\low_repair_family_arm_compare_result_20261017.json"
OUT_MD = r"D:\zcodesj\analysis\survivorship_delta_t1_20261018_out.md"


def build_panel(df, source_label):
    """arm_compare 同口径指标(输入列: code,date,open,high,low,close,volume,change_pct)"""
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
    gem20 = (df["code"].str[-6:].str.startswith(("300", "301"))) & (df["date"] >= "20200824")
    thr = pd.Series(np.where(gem20, 19.5, 9.7), index=df.index)
    lo_pct = df["low"] / df["prev_close"] * 100 - 100
    dt_close = (df["change_pct"] <= -thr) & (df["close"] <= df["low"] + 1e-9)
    df["fwd20"] = g["close"].transform(lambda s: s.shift(-20) / s - 1)
    base = df.dropna(subset=["vr20", "pos250", "change_pct", "prev_close"]).copy()
    base = base[base["fwd20"].notna()]
    lo_b = lo_pct.reindex(base.index)
    thr_b = thr.reindex(base.index)
    dtc_b = dt_close.reindex(base.index).fillna(False)
    t1 = base[(base["pos250"] <= 0.3) & (lo_b <= -thr_b)
              & ~dtc_b & (base["vr20"] < 1.5)].copy()
    t1["lo_pct"] = lo_b.reindex(t1.index)
    t1["gem20"] = gem20.reindex(t1.index)
    t1["source"] = source_label
    return t1


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


def stat(sub):
    s = sub["fwd20"].dropna()
    return {"n": int(len(s)), "med20": round(float(s.median()) * 100, 2), "win20": round(float((s > 0).mean()) * 100, 1)}


def main():
    # ---- 面板A: wb 活股源 ----
    con = sqlite3.connect("file:" + WB_DB.replace("\\", "/") + "?mode=ro", uri=True)
    dfA = pd.read_sql(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>='20160101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
        "AND substr(code,1,2) NOT IN ('42','83','87','92')", con)
    con.close()
    t1A = dedup5(build_panel(dfA, "A活股wb"))

    # ---- 面板D: baostock 退市段(raw×back因子域内自洽) ----
    con = sqlite3.connect("file:" + DEL_DB.replace("\\", "/") + "?mode=ro", uri=True)
    raw = pd.read_sql("SELECT code,date,open,high,low,close,volume,amount,pctchg "
                      "FROM stock_daily_delisted_raw WHERE date>='20160101'", con)
    fac = pd.read_sql("SELECT code,date,backAdjustFactor FROM adjust_factor", con)
    con.close()
    raw = raw.sort_values(["code", "date"]).reset_index(drop=True)
    fac = fac.sort_values(["code", "date"]).reset_index(drop=True)
    raw["dt"] = pd.to_datetime(raw["date"])  # 退市库k-data日期实测带横线(2017-01-03)
    fac["dt"] = pd.to_datetime(fac["date"])
    merged = pd.merge_asof(raw.sort_values("dt"), fac.rename(columns={"dt": "fdt"}).sort_values("fdt"),
                           left_on="dt", right_on="fdt", by="code",
                           direction="backward", suffixes=("", "_f"))
    merged["backAdjustFactor"] = merged["backAdjustFactor"].fillna(1.0)
    for c in ("open", "high", "low", "close"):
        merged[c] = merged[c] * merged["backAdjustFactor"]
    merged = merged.rename(columns={"pctchg": "change_pct"})
    t1D = dedup5(build_panel(merged, "D退市段baostock"))
    t1D["code6"] = t1D["code"].str[-6:]

    # 锚核: 面板A 必须复现基线
    anchor = json.load(open(ANCHOR, encoding="utf-8"))
    a_t1 = anchor.get("T1_低位缩量破跌停", {})
    sA = stat(t1A)
    assert abs(sA["n"] - a_t1.get("n", 0)) <= a_t1.get("n", 0) * 0.02, f"面板A n={sA['n']} vs 锚{a_t1.get('n')} 漂移超2%"
    assert abs(sA["med20"] - a_t1.get("fwd20", {}).get("med", 0)) <= 0.3, "面板A med20 漂移超0.3pp"
    print(f"[锚核] 面板A n={sA['n']} med20={sA['med20']} win={sA['win20']} — 同口径复现 PASS")

    sD = stat(t1D)
    combined = pd.concat([t1A, t1D], ignore_index=True)
    sC = stat(combined)
    # 对抗前沿: 退市段距最后bar≤120日的边缘事件
    last_bar = t1D.groupby("code")["rowid"].transform("max")
    frontier = t1D[last_bar - t1D["rowid"] <= 120]
    sF = stat(frontier)

    md = ["# T1 家族幸存者敞口对比(V4-P1 回填后量化)", "",
          "> 脚本 analysis/survivorship_delta_t1_20261018.py 直出 | 面板A=wb活股源(现行基线) 面板D=baostock退市段(2026-10-18回填, 域内后复权自洽)",
          "> 口径: T1 与 arm_compare 逐位同源; 合并=A+D 逐码分段自洽不跨源拼接; fwd20 notna 截尾", "",
          "## 总表", "",
          "| 面板 | n | med20 | win20 |", "|---|---|---|---|",
          f"| A 活股(现行基线) | {sA['n']} | {sA['med20']:+.2f}% | {sA['win20']}% |",
          f"| D 退市段(补回人群) | {sD['n']} | {sD['med20']:+.2f}% | {sD['win20']}% |",
          f"| **A+D 合并(无幸存者偏差口径)** | **{sC['n']}** | **{sC['med20']:+.2f}%** | **{sC['win20']}%** |",
          "", f"退市段贡献事件 {sD['n']} 个（占合并 {sD['n'] / max(sC['n'], 1) * 100:.1f}%），涉及退市码 {t1D['code6'].nunique()} 只。", "",
          "## 对抗前沿: 退市段「距最后bar≤120日」边缘切片", "",
          f"- n={sF['n']} / med20={sF['med20']:+.2f}% / win20={sF['win20']}%"
          + ("" if sF["n"] < 30 else "（n≥30 可读）"),
          "- 语义: 退市边缘的低位缩量破跌停是否还修得回来——假说最严苛考验；n<30 则只作方向注记。", "",
          "## 判定与边界", "",
          f"- 幸存者偏差方向: 合并 med20 {sC['med20']:+.2f}% vs 活股口径 {sA['med20']:+.2f}% → "
          f"差 {(sC['med20'] - sA['med20']):+.2f}pp（负=活股口径高估基线, 正=退市人群反而更优）。",
          "- 本对比仅覆盖 T1 事件族; A池格全样本(vec引擎)的幸存者敞口重跑属后续项(引擎扩展工作量另估)。",
          "- 退市段口径=baostock 域内自洽(后复权), 与 sina 段未拼接; 因子表 843 条覆盖复权事件。",
          "- T1 prereg(OOS 10/20 起)判据不变——本件为敞口量化, 不改任何冻结判据。"]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[OK] A:{sA} D:{sD} 合并:{sC} 边缘:{sF}")
    print(f"[OK] 已直出 {OUT_MD}")


if __name__ == "__main__":
    sys.exit(main())
