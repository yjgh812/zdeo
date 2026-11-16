#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""limit_up_flow_features_20261114.py — 封板吃单流特征研究（纯只读，方向无关，协议安全）。
2026-11-14 主人「继续」令第二段。承接 limit_up_process_features_20261114.py：
  方向字段协议核对未通过（sealed日85%分钟为正号，与主动买卖假设矛盾）→ 关闭主动买卖路径；
  改用无方向特征：密封分钟内的分钟成交量序列（=涨停价上的吃单流，方向无关、协议安全）。

数据源：minute_archive.db bars1（1分钟K，20260107~20261113）+ stock_daily_meta（日线）
事件口径：与 limit_up_process_features_20261114.py 同源
特征（封板后吃单流，方向无关）：
  - flow_decay = 末段密封段最后10分钟量 / 前10分钟量（吃单流衰减）
  - flow_cv: 末段密封分钟量变异系数（吃单节奏均匀度）
  - flow_burst = 末段密封分钟最大分钟量 / 密封分钟量均值（突发大吃单）
  - final_run_len: 末段密封段分钟数
问题：流特征能否把换手板（可买）的可执行腿 hold 筛出正期望子集？
输出: reports/limit_up_flow_features_20261114.md + LIMIT_UP_FLOW_20261114/result.json
退出码: 0正常 / 3数据源不可用
限制(如实)：事件级统计非独立；hold 假设 T+1 开盘成交（乐观）；样本内发现统计；
  1分钟K里密封分钟量=涨停价成交吃单流（撤单/挂单维度 N/A）。
"""
import json, os, sqlite3, sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
import pandas as pd

RAW_DB = "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db" + "?mode=ro"
MIN_DB = "file:" + r"D:\zcodesj\data\minute_archive.db" + "?mode=ro"
REPORT_DIR = r"D:\zcodesj\reports"
OUT_DIR = os.path.join(REPORT_DIR, "LIMIT_UP_FLOW_20261114")
OUT_MD = REPORT_DIR + r"\limit_up_flow_features_20261114.md"
WIN_START, WIN_END = 20260107, 20261113
FB_MIN_BARS = 60

def _ro(path):
    try:
        return sqlite3.connect(path, uri=True)
    except Exception as e:
        print(f"[FLOW] [FAIL-CLOSED] 数据源不可用({path}): {e}")
        sys.exit(3)
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = _ro(RAW_DB)
    print("[FLOW] 加载日线...", flush=True)
    df = pd.read_sql(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta "
        "WHERE date>=20250901", con)
    con.close()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "open", "volume"])
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
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
    con2 = _ro(RAW_DB)
    firsts_full = pd.read_sql(
        "SELECT code,MIN(date) AS fd FROM stock_daily_meta WHERE date<20260107 GROUP BY code",
        con2)
    con2.close()
    fmap = dict(zip(firsts_full["code"], firsts_full["fd"]))
    df["fd"] = df["code"].map(fmap).fillna(20200101)
    df["fd"] = df["fd"].astype(int)
    thr = np.where(df["code"].str.startswith("30"), 0.198, 0.1)
    df["thr"] = thr
    df["limit_price"] = np.round(df["pc"] * (1 + df["thr"]), 2)
    df["sealed"] = (df["close"] >= df["limit_price"] - 0.001) & df["pc"].notna()
    raw_i = df["sealed"].astype(int)
    cs = raw_i.groupby(df["code"]).cumsum()
    df["streak"] = cs - cs.where(raw_i == 0).groupby(df["code"]).ffill().fillna(0)
    df["gap"] = df["o1"] / df["close"] - 1.0
    df["r1"] = df["c1"] / df["close"] - 1.0
    df["hold"] = np.where(df["o2"].fillna(0) > 0, df["o2"] / df["o1"] - 1.0, np.nan)
    df["vr"] = df["volume"] / df["pvol"]
    ev = df[df["sealed"] & (df["nb"] >= FB_MIN_BARS)
            & ((df["date"] - df["fd"]) >= 90)
            & (df["date"] >= WIN_START) & (df["date"] <= WIN_END)].copy()
    ev["form"] = np.where(
        ev["open"] >= ev["limit_price"] - 0.005,
        np.where(ev["low"] >= ev["limit_price"] - 0.005, "一字", "T字"),
        "换手板")
    ev["promoted"] = (ev["c1"] / ev["close"] - 1.0) >= ev["thr"] - 0.002
    print(f"[FLOW] 事件 n={len(ev)}", flush=True)
    # ---- bars1 逐事件提取吃单流特征 ----
    import time as _time
    print("[FLOW] 提取密封段吃单流特征...", flush=True)
    mcon = _ro(MIN_DB)
    feats = []
    ev_list = ev[["code", "date", "limit_price"]].values.tolist()
    t0 = _time.time()
    for i, (code, day, limit_price) in enumerate(ev_list):
        if i % 2000 == 0:
            print(f"[FLOW] 进度 {i}/{len(ev_list)} 耗时{_time.time()-t0:.0f}s", flush=True)
        rows = mcon.execute(
            "SELECT tmin,c,vol FROM bars1 WHERE code=? AND day=? ORDER BY tmin",
            (code, day)).fetchall()
        if not rows or len(rows) < 100:
            feats.append({"code": code, "date": day, "has_bars": 0})
            continue
        c_arr = np.array([r[1] for r in rows])
        t_arr = np.array([r[0] for r in rows])
        v_arr = np.array([r[2] for r in rows], dtype=float)
        is_seal = c_arr >= limit_price - 0.001
        if not is_seal.any():
            feats.append({"code": code, "date": day, "has_bars": 0})
            continue
        sealed_idx = np.where(is_seal)[0]
        first_i = int(sealed_idx[0])
        j = len(is_seal) - 1
        while j >= 0 and is_seal[j]:
            j -= 1
        final_i = j + 1
        # 末段密封段分钟量序列
        seg = v_arr[final_i:]
        seg = seg[seg > 0]
        f = {"code": code, "date": day, "has_bars": 1,
             "final_run_len": int(len(is_seal) - final_i)}
        if len(seg) >= 6:
            f10 = float(seg[:10].sum())
            l10 = float(seg[-10:].sum())
            f["flow_first10"] = f10
            f["flow_last10"] = l10
            f["flow_decay"] = l10 / f10 if f10 > 0 else np.nan
            f["flow_cv"] = float(seg.std() / seg.mean()) if seg.mean() > 0 else np.nan
            f["flow_burst"] = float(seg.max() / seg.mean()) if seg.mean() > 0 else np.nan
        else:
            f["flow_decay"] = np.nan
            f["flow_cv"] = np.nan
            f["flow_burst"] = np.nan
        feats.append(f)
    mcon.close()
    fdf = pd.DataFrame(feats)
    cov = fdf["has_bars"].mean()
    print(f"[FLOW] 分钟覆盖率 {cov*100:.1f}% (缺失 {int((fdf['has_bars']==0).sum())}/{len(fdf)})",
          flush=True)
    ev = ev.merge(fdf, on=["code", "date"], how="left")
    ev = ev[ev["has_bars"] == 1].copy()
    print(f"[FLOW] 有效事件 n={len(ev)}", flush=True)
    # ---- 分析与输出 ----
    L = []
    J = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L.append(f"# 封板吃单流特征研究（方向无关） {now}")
    L.append("")
    L.append(f"- 窗口：{WIN_START}~{WIN_END}（分钟归档覆盖）；事件 n={len(ev)}")
    L.append("- 口径与 process_features 同源；hold=T+2开盘/T+1开盘-1（可执行腿，费前，乐观假设）")
    L.append("- 特征全部来自密封段分钟量序列（方向无关，协议安全）")
    L.append("")
    J["meta"] = {"n": len(ev), "window": [WIN_START, WIN_END]}

    def blk(name):
        L.append("")
        L.append(f"## {name}")
        L.append("")
        L.append("| 桶 | n | 晋级率 | gap均值% | hold均值% | hold中位% |")
        L.append("|---|---|---|---|---|---|")

    def line(lab, sub):
        sub = sub.dropna(subset=["hold"])
        if len(sub) == 0:
            L.append(f"| {lab} | 0 | - | - | - | - |")
            return None
        L.append(f"| {lab} | {len(sub)} | {sub['promoted'].mean()*100:.1f}% "
                 f"| {sub['gap'].mean()*100:+.2f}% | {sub['hold'].mean()*100:+.2f}% "
                 f"| {sub['hold'].median()*100:+.2f}% |")
        return {"n": len(sub),
                "prom": round(float(sub["promoted"].mean()), 4),
                "gap": round(float(sub["gap"].mean() * 100), 3),
                "hold_mean": round(float(sub["hold"].mean() * 100), 3),
                "hold_med": round(float(sub["hold"].median() * 100), 3)}

    # A. 换手板×流衰减分位
    blk("A 换手板（可买）×吃单流衰减分位")
    hu = ev[ev["form"] == "换手板"].copy()
    hu = hu[hu["flow_decay"].notna()].copy()
    hu["decay_q"] = pd.qcut(hu["flow_decay"], 4,
                            labels=["Q1最快衰减", "Q2", "Q3", "Q4不衰反增"])
    JA = {}
    for q in ("Q1最快衰减", "Q2", "Q3", "Q4不衰反增"):
        JA[q] = line(q, hu[hu["decay_q"] == q])
    J["A_huanshou_decay"] = JA
    # B. 换手板×吃单节奏(cv) —— qcut 直接建在 hu 上（NaN 安全），组合搜索共用
    blk("B 换手板×吃单节奏变异系数")
    hu["cv_q"] = pd.qcut(hu["flow_cv"], 4,
                         labels=["Q1最均匀", "Q2", "Q3", "Q4最爆发"])
    JB = {}
    for q in ("Q1最均匀", "Q2", "Q3", "Q4最爆发"):
        JB[q] = line(q, hu[hu["cv_q"] == q])
    J["B_huanshou_cv"] = JB
    # C. 换手板×突发大吃单
    blk("C 换手板×突发大吃单强度")
    hu["burst_q"] = pd.qcut(hu["flow_burst"], 4,
                            labels=["Q1", "Q2", "Q3", "Q4最大突发"])
    JC = {}
    for q in ("Q1", "Q2", "Q3", "Q4最大突发"):
        JC[q] = line(q, hu[hu["burst_q"] == q])
    J["C_huanshou_burst"] = JC
    # D. 末段密封时长
    blk("D 换手板×末段密封时长")
    hu4 = hu[hu["final_run_len"] > 0].copy()
    hu4["run_b"] = pd.cut(hu4["final_run_len"], [0, 30, 120, 240, 999],
                          labels=["≤30分", "30-120分", "120-240分", ">240分"])
    JD = {}
    for lab in ("≤30分", "30-120分", "120-240分", ">240分"):
        JD[lab] = line(lab, hu4[hu4["run_b"] == lab])
    J["D_final_run"] = JD
    # E. 组合正期望搜索（n≥100）
    L.append("")
    L.append("## E 组合正期望搜索（hold均值>0，n≥100）")
    L.append("")
    L.append("| 组合 | n | hold均值% | hold中位% | 晋级率 |")
    L.append("|---|---|---|---|---|")
    JE = {}
    cand = []
    combos = [
        ("换手板 & 流不衰(Q4)", hu["decay_q"] == "Q4不衰反增"),
        ("换手板 & 流均匀(Q1)", hu["cv_q"] == "Q1最均匀"),
        ("换手板 & 末段>240分", hu["final_run_len"] > 240),
        ("换手板 & 末段>240 & 不衰", (hu["final_run_len"] > 240)
         & (hu["decay_q"] == "Q4不衰反增")),
        ("换手板 & vr<1 & 不衰", (hu["vr"] < 1)
         & (hu["decay_q"] == "Q4不衰反增")),
        ("换手板 & vr<1 & 均匀", (hu["vr"] < 1)
         & (hu["cv_q"] == "Q1最均匀")),
    ]
    for lab, mask in combos:
        sub = hu[mask].dropna(subset=["hold"])
        if len(sub) < 100:
            continue
        rr = {"n": len(sub),
              "hold_mean": round(float(sub["hold"].mean() * 100), 3),
              "hold_med": round(float(sub["hold"].median() * 100), 3),
              "prom": round(float(sub["promoted"].mean()), 4)}
        JE[lab] = rr
        cand.append((rr["hold_mean"], lab, rr))
    cand.sort(reverse=True)
    for hm, lab, rr in cand:
        L.append(f"| {lab} | {rr['n']} | {rr['hold_mean']:+.2f}% | "
                 f"{rr['hold_med']:+.2f}% | {rr['prom']*100:.1f}% |")
    if not cand:
        L.append("| (无满足n≥100的组合) | - | - | - | - |")
    J["E_combo_search"] = JE
    best = cand[0] if cand else None
    if best:
        L.append("")
        L.append(f"- hold均值最高：**{best[1]}**（n={best[2]['n']}，{best[2]['hold_mean']:+.2f}%，"
                 f"中位 {best[2]['hold_med']:+.2f}%）——样本内发现统计，前瞻须影子验证")
    else:
        L.append("")
        L.append("- **未找到 hold 均值为正的组合（n≥100）**——流特征未分离出可执行正期望子集")
    L.append("")
    L.append("## 边界与限制（如实）")
    L.append("")
    L.append("- 窗口=分钟归档覆盖（~210交易日）；事件级统计非独立；未做日聚簇显著性检验")
    L.append("- hold 对一字开盘按成交假设=乐观；密封分钟量不含撤单/挂单维度（N/A）")
    L.append("- 本轮为样本内发现统计，任何用于决策的组合须先注册前瞻影子验证")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    jpath = OUT_DIR + r"\result.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(J, f, ensure_ascii=False, indent=1)
    print(f"[FLOW] 完成 -> {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
