#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""limit_up_process_features_20261114.py — 打板过程特征研究（纯只读，零交易权）。
2026-11-14 主人「继续」令开工。回答第二十轮精化的问题：
  过程特征（分钟级封板质量）能否把可执行腿（T+1开盘买→T+2开盘卖）筛出正期望子集？

数据源：
  - minute_archive.db bars1（1分钟K, 20260107~20261113, 4.1亿行, PK前缀(code,day)查询）
  - dragon_quant_meta.db stock_daily_meta（日线原始价, 用于事件/结局/涨停价）
口径（与 step0/第二十轮实测同源）：
  - 涨停事件 = 收盘涨停（close >= 涨停价-0.001, 涨停价=round(前收×(1+thr),2)）
  - thr: 30前缀=0.198（窗口全在2020-08-24后）, 其余=0.1；剔除 4/8/92/68
  - 上市年龄>=60交易日（按每股全史首日计）
  - gap = T+1开盘/T收盘-1；hold = T+2开盘/T+1开盘-1（可执行腿, T+1合规, 费前）
  - 形态: 一字=o==c==limit & low>=limit-0.005；T字=o==c==limit & low<limit-0.005；换手板=o<limit
特征（全部来自1分钟K）：
  - first_seal_rel: 首封分钟（自9:30起, 第1根=931）；
  - open_count: 封→开转换次数；
  - final_run_rel: 最后连续密封段起点（回溯到最后一次开板后未再开的起点）
  - seal_dur_ratio: 首封后密封分钟/首封后总分钟
  - vol_after_seal: 首封后成交量/全日量
  - vr: 当日量/昨日量（日线）
限制(如实)：
  - 窗口=分钟归档覆盖（~210交易日），非全史；事件级统计不独立（同日相关）
  - bars1 某日缺失→特征缺失行，覆盖率如实报告
  - hold 假设 T+1 开盘价成交（对一字开盘乐观，真实可执行更差）
  - 封单金额/撤单维度不在分钟K内（需tick/L2），本轮 N/A
输出: reports/limit_up_process_features_20261114.md + LIMIT_UP_PROCESS_20261114/result.json
退出码: 0正常 / 3数据源不可用
"""
import json, os, sqlite3, sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
import pandas as pd

RAW_DB = "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db" + "?mode=ro"
MIN_DB = "file:" + r"D:\zcodesj\data\minute_archive.db" + "?mode=ro"
REPORT_DIR = r"D:\zcodesj\reports"
OUT_DIR = os.path.join(REPORT_DIR, "LIMIT_UP_PROCESS_20261114")
OUT_MD = REPORT_DIR + r"\limit_up_process_features_20261114.md"
WIN_START, WIN_END = 20260107, 20261113
FB_MIN_BARS = 60

def _ro(path):
    try:
        return sqlite3.connect(path, uri=True, timeout=60)
    except Exception as e:
        print(f"[PF] [FAIL-CLOSED] 数据源不可用({path}): {e}")
        sys.exit(3)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = _ro(RAW_DB)
    print("[PF] 加载日线...", flush=True)
    df = pd.read_sql(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta "
        "WHERE date>=20250901", con)
    firsts = pd.read_sql(
        "SELECT code,MIN(date) AS first_date FROM stock_daily_meta GROUP BY code", con)
    con.close()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "open"])
    df = df[(df["close"] > 0) & (df["open"] > 0)]
    df = df[~df["code"].astype(str).str.startswith(("4", "8", "92", "68"))]
    df["date"] = df["date"].astype(int)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    g = df.groupby("code", sort=False)
    df["pc"] = g["close"].shift(1)
    df["pvol"] = g["volume"].shift(1)
    df["o1"] = g["open"].shift(-1)
    df["c1"] = g["close"].shift(-1)
    df["o2"] = g["open"].shift(-2)
    df["nb"] = g.cumcount()
    # 每股全史首日（上市年龄）
    firsts["first_date"] = firsts["first_date"].astype(int)
    fmap = dict(zip(firsts["code"], firsts["first_date"]))
    df["first_date"] = df["code"].map(fmap)
    df["age_ok"] = (df["date"] - df["first_date"]) >= 0  # 占位，下面用n_bars口径
    df["age_ok"] = df["nb"] >= FB_MIN_BARS
    thr = np.where(df["code"].str.startswith("30"), 0.198, 0.1)
    df["thr"] = thr
    df["limit_price"] = np.round(df["pc"] * (1 + df["thr"]), 2)
    df["sealed"] = (df["close"] >= df["limit_price"] - 0.001) & df["pc"].notna()
    df["streak"] = 0
    raw_i = df["sealed"].astype(int)
    cs = raw_i.groupby(df["code"]).cumsum()
    df["streak"] = cs - cs.where(raw_i == 0).groupby(df["code"]).ffill().fillna(0)
    df["gap"] = df["o1"] / df["close"] - 1.0
    df["r1"] = df["c1"] / df["close"] - 1.0
    df["hold"] = np.where(df["o2"].fillna(0) > 0, df["o2"] / df["o1"] - 1.0, np.nan)
    df["vr"] = df["volume"] / df["pvol"]

    ev = df[df["sealed"] & df["age_ok"]
            & (df["date"] >= WIN_START) & (df["date"] <= WIN_END)].copy()
    ev["form"] = np.select(
        [ev["open"] >= ev["limit_price"] - 0.005,
         ev["open"] >= ev["limit_price"] - 0.005],
        [np.where(ev["low"] >= ev["limit_price"] - 0.005, "一字", "T字"),
         np.where(ev["low"] >= ev["limit_price"] - 0.005, "一字", "T字")],
        default="换手板")
    ev["promoted"] = (ev["c1"] / ev["close"] - 1.0) >= ev["thr"] - 0.002
    print(f"[PF] 事件域内涨停事件 n={len(ev)}", flush=True)
    # ---- 分钟级特征提取 ----
    import time as _time
    print("[PF] 提取分钟级封板特征...", flush=True)
    mcon = _ro(MIN_DB)
    feats = []
    ev_list = ev[["code", "date", "limit_price"]].values.tolist()
    t0 = _time.time()
    for i, (code, day, limit_price) in enumerate(ev_list):
        if i % 2000 == 0:
            print(f"[PF] 进度 {i}/{len(ev_list)} 耗时{_time.time()-t0:.0f}s", flush=True)
        rows = mcon.execute(
            "SELECT tmin,c,vol FROM bars1 WHERE code=? AND day=? ORDER BY tmin",
            (code, day)).fetchall()
        if not rows or len(rows) < 100:
            feats.append({"code": code, "date": day, "has_bars": 0})
            continue
        c_arr = np.array([r[1] for r in rows])
        t_arr = np.array([r[0] for r in rows])
        v_arr = np.array([r[2] for r in rows])
        is_seal = c_arr >= limit_price - 0.001
        if not is_seal.any():
            feats.append({"code": code, "date": day, "has_bars": 0})
            continue
        first_i = int(np.where(is_seal)[0][0])
        d = np.diff(is_seal.astype(int))
        open_count = int((d == -1).sum())
        j = len(is_seal) - 1
        while j >= 0 and is_seal[j]:
            j -= 1
        final_i = j + 1
        f = {
            "code": code, "date": day, "has_bars": 1,
            "first_seal_rel": int(t_arr[first_i] - 570),
            "open_count": open_count,
            "final_run_rel": int(t_arr[final_i] - 570),
            "seal_dur_ratio": float(is_seal[first_i:].mean()),
            "vol_after_seal": float(v_arr[first_i:].sum() / v_arr.sum()),
            "n_bars_1m": len(rows),
        }
        feats.append(f)
    mcon.close()
    fdf = pd.DataFrame(feats)
    cov = fdf["has_bars"].mean()
    print(f"[PF] 分钟数据覆盖率 {cov*100:.1f}% "
          f"(缺失 {int((fdf['has_bars']==0).sum())}/{len(fdf)})", flush=True)
    ev = ev.merge(fdf, on=["code", "date"], how="left")
    has = ev["has_bars"] == 1
    ev = ev[has].copy()
    print(f"[PF] 有分钟数据的涨停事件 n={len(ev)}", flush=True)

    # ---- 分析与输出 ----
    L = []
    J = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L.append(f"# 打板过程特征研究（分钟级封板质量 × 可执行腿） {now}")
    L.append("")
    L.append(f"- 窗口：分钟归档覆盖 {WIN_START}~{WIN_END}（~210交易日）")
    L.append(f"- 事件：窗口内收盘涨停 & 上市≥60日 & 分钟数据齐全，n={len(ev)}"
             f"（分钟覆盖率 {cov*100:.1f}%）")
    L.append("- 口径与 step0 同源：gap=T+1开盘/T收盘-1；hold=T+2开盘/T+1开盘-1"
             "（可执行腿，费前，一字开盘按成交假设=乐观）")
    L.append("- 特征：first_seal_rel（首封分钟，931→1）、open_count（封→开次数）、"
             "final_run_rel（末段密封起点）、seal_dur_ratio、vol_after_seal")
    L.append("")
    J["meta"] = {"n": len(ev), "coverage": round(cov, 4),
                 "window": [WIN_START, WIN_END]}

    def blk(name):
        L.append("")
        L.append(f"## {name}")
        L.append("")
        L.append("| 桶 | n | 晋级率 | gap均值% | gap中位% | hold均值% | hold中位% |")
        L.append("|---|---|---|---|---|---|---|")

    def line(lab, sub):
        sub = sub.dropna(subset=["hold"])
        if len(sub) == 0:
            L.append(f"| {lab} | 0 | - | - | - | - | - |")
            return
        L.append(f"| {lab} | {len(sub)} | {sub['promoted'].mean()*100:.1f}% "
                 f"| {sub['gap'].mean()*100:+.2f}% | {sub['gap'].median()*100:+.2f}% "
                 f"| {sub['hold'].mean()*100:+.2f}% | {sub['hold'].median()*100:+.2f}% |")

    def cell(sub, key):
        rr = {"n": int(len(sub.dropna(subset=["hold"]))),
              "prom": round(float(sub["promoted"].mean()), 4),
              "gap_mean": round(float(sub["gap"].mean() * 100), 3),
              "gap_med": round(float(sub["gap"].median() * 100), 3),
              "hold_mean": round(float(sub["hold"].mean() * 100), 3),
              "hold_med": round(float(sub["hold"].median() * 100), 3)}
        return rr

    # A. 全体
    blk("A 全体事件")
    line("全体", ev)
    J["A_overall"] = cell(ev, "all")
    # B. 首封时段
    blk("B 首封时段（分钟，9:30起算）")
    bins = [(0, 30, "≤30分(10:00前)"), (30, 120, "30-120分(上午盘中)"),
            (120, 240, "120-240分(午后)"), (240, 999, ">240分(尾盘)")]
    JB = {}
    for lo, hi, lab in bins:
        sub = ev[(ev["first_seal_rel"] > lo) & (ev["first_seal_rel"] <= hi)]
        line(lab, sub)
        JB[lab] = cell(sub, lab)
    J["B_by_seal_time"] = JB
    # C. 开板次数
    blk("C 开板次数")
    JC = {}
    for oc, lab in [(0, "0炸(未开板)"), (1, "1炸"), (2, "2炸"), (3, "3炸")]:
        sub = ev[ev["open_count"] == oc]
        line(lab, sub)
        JC[lab] = cell(sub, lab)
    sub = ev[ev["open_count"] >= 4]
    line("≥4炸", sub)
    JC["≥4炸"] = cell(sub, "≥4炸")
    J["C_by_open_count"] = JC
    # D. 形态
    blk("D 形态（一字/T字/换手板）")
    JD = {}
    for lab in ("一字", "T字", "换手板"):
        sub = ev[ev["form"] == lab]
        line(lab, sub)
        JD[lab] = cell(sub, lab)
    J["D_by_form"] = JD
    # E. 缩量二板专项（vr<1, streak==2）
    L.append("")
    L.append("## E 缩量二板专项（streak=2 & 量比<1）——检验45.7%三板率是否集中在不可执行形态")
    L.append("")
    L.append("| 组 | n | 三板率 | gap均值% | hold均值% |")
    L.append("|---|---|---|---|---|")
    JE = {}
    relay = ev[(ev["streak"] == 2) & (ev["vr"] < 1)]
    for lab in ("一字", "T字", "换手板"):
        sub = relay[relay["form"] == lab]
        sub2 = sub.dropna(subset=["hold"])
        L.append(f"| 缩量二板×{lab} | {len(sub)} | {sub['promoted'].mean()*100:.1f}% "
                 f"| {sub['gap'].mean()*100:+.2f}% | {sub2['hold'].mean()*100:+.2f}% |")
        JE[lab] = {"n": len(sub),
                   "prom": round(float(sub["promoted"].mean()), 4),
                   "gap": round(float(sub["gap"].mean() * 100), 3),
                   "hold": round(float(sub2["hold"].mean() * 100), 3)}
    for oc, lab in [(0, "0炸"), (1, "1炸"), (2, "≥2炸")]:
        sub = relay[relay["open_count"] >= oc] if oc == 2 else relay[relay["open_count"] == oc]
        sub2 = sub.dropna(subset=["hold"])
        L.append(f"| 缩量二板×{lab} | {len(sub)} | {sub['promoted'].mean()*100:.1f}% "
                 f"| {sub['gap'].mean()*100:+.2f}% | {sub2['hold'].mean()*100:+.2f}% |")
        JE[lab] = {"n": len(sub),
                   "prom": round(float(sub["promoted"].mean()), 4),
                   "gap": round(float(sub["gap"].mean() * 100), 3),
                   "hold": round(float(sub2["hold"].mean() * 100), 3)}
    J["E_lowvol_2ndboard"] = JE
    # F. hold正期望子集搜索（诚实目标：找不到就如实说）
    L.append("")
    L.append("## F 可执行腿正期望子集搜索（hold均值>0的组合，n≥100）")
    L.append("")
    L.append("| 特征组合 | n | hold均值% | hold中位% | 晋级率 |")
    L.append("|---|---|---|---|---|")
    JF = {}
    cand = []
    for lab, sub in [("首封≤30分", ev[ev["first_seal_rel"] <= 30]),
                     ("首封≤30分 & 0炸", ev[(ev["first_seal_rel"] <= 30)
                                            & (ev["open_count"] == 0)]),
                     ("首封≤60分 & 0炸", ev[(ev["first_seal_rel"] <= 60)
                                            & (ev["open_count"] == 0)]),
                     ("换手板 & 0炸 & 首≤60", ev[(ev["form"] == "换手板")
                                                 & (ev["open_count"] == 0)
                                                 & (ev["first_seal_rel"] <= 60)]),
                     ("换手板 & 0炸", ev[(ev["form"] == "换手板")
                                         & (ev["open_count"] == 0)]),
                     ("首封≤30 & vr<1", ev[(ev["first_seal_rel"] <= 30)
                                           & (ev["vr"] < 1)]),
                     ("首封≤30 & vr≥1", ev[(ev["first_seal_rel"] <= 30)
                                           & (ev["vr"] >= 1)]),
                     ("缩量二板 & 换手板", ev[(ev["streak"] == 2) & (ev["vr"] < 1)
                                              & (ev["form"] == "换手板")]),
                     ("0炸 & 首≤30 & vr<1", ev[(ev["open_count"] == 0)
                                               & (ev["first_seal_rel"] <= 30)
                                               & (ev["vr"] < 1)]),
                     ("二板 & 0炸 & 首≤30", ev[(ev["streak"] == 2)
                                               & (ev["open_count"] == 0)
                                               & (ev["first_seal_rel"] <= 30)])]:
        sub2 = sub.dropna(subset=["hold"])
        if len(sub2) < 100:
            continue
        rr = {"n": len(sub2),
              "hold_mean": round(float(sub2["hold"].mean() * 100), 3),
              "hold_med": round(float(sub2["hold"].median() * 100), 3),
              "prom": round(float(sub["promoted"].mean()), 4)}
        JF[lab] = rr
        cand.append((rr["hold_mean"], lab, rr))
    cand.sort(reverse=True)
    for hm, lab, rr in cand:
        L.append(f"| {lab} | {rr['n']} | {rr['hold_mean']:+.2f}% | "
                 f"{rr['hold_med']:+.2f}% | {rr['prom']*100:.1f}% |")
    if not cand:
        L.append("| (无满足n≥100的组合) | - | - | - | - |")
    J["F_hold_positive_search"] = JF
    best = cand[0] if cand else None
    if best:
        L.append("")
        L.append(f"- hold均值最高组合：**{best[1]}**（n={best[2]['n']}，"
                 f"{best[2]['hold_mean']:+.2f}%，中位 {best[2]['hold_med']:+.2f}%）"
                 f"——是否为真信号须前瞻影子验证，本轮为样本内发现统计")
    else:
        L.append("")
        L.append("- **未找到 hold 均值为正的组合（n≥100）**——过程特征未分离出可执行正期望子集")

    # ---- 写出 ----
    L.append("")
    L.append("## 边界与限制（如实）")
    L.append("")
    L.append("- 窗口为分钟归档覆盖（~210交易日），非全史；早期年代分钟数据缺失维度无法验证")
    L.append("- 事件级统计非独立（同日事件相关）；关键结论未做日聚簇显著性检验（后续prereg若立再做）")
    L.append("- hold 假设 T+1 开盘价成交（一字开盘乐观）；一字开盘实际不可买入，真实可执行更差")
    L.append("- 封单金额/撤单/买卖方向维度需 tick/L2（tdx 分笔方向字段未过协议核对），本轮 N/A")
    L.append("- 本轮为样本内发现统计（发现即样本内），任何用于决策的特征组合须先注册前瞻影子验证")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    jpath = OUT_DIR + r"\result.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(J, f, ensure_ascii=False, indent=1)
    print(f"[PF] 完成 -> {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
