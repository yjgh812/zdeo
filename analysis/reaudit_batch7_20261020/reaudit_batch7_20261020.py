# -*- coding: utf-8 -*-
"""证伪复核批次7 · 独立重实现两件 + claim-vs-artifact 批量六件

Q) BOX-BREAKOUT-LOWPOS (箱体定义=scout脚本逐字照抄的 box_breakout_lowpos_backtest_20260904):
   pos250<=0.35 & 60日振幅<=25% & c>前60日max & vol>=1.2*vma20 & c<=30; 当日即买(f20=c[t+20]/c[t])
   原档: 22,215信号日 聚簇-0.83%/t-3.70
R) EBB-SIGNAL-NO-EDGE (round1设计逐字):
   A 涨停家数<=0.75*MA5且<90; B 跌停(change_pct<-9)家数>=max(20,2*MA5); C 前日最高板>=5且今日<=前日-2;
   对象=T日boards>=2封板票T+1平均收益 - 全市场T+1中位
   原档: 三信号超额全反向 +3.02/+2.22/+2.74pp (7/7年)
S) claim-vs-artifact 批量: PEAD/SELECTION-CHECKLIST/TIMING-CONDMATRIX/PORTRAIT-REGIME-GATE/
   CHIP-MANUAL-3GATE/SKILL-SWEEP 六条, 验证档案声明数字在 result 工件中可寻
"""
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ZTDB = r"D:\zcodesj\data\zt_pool_history.db"
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def load_panel(start, cols="code,date,close,volume,change_pct"):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT %s FROM stock_daily_meta_qfq WHERE date>=? AND substr(code,1,3) IN (%s)" % (cols, ph),
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def jdump(obj, name):
    p = os.path.join(HERE, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", p)


def part_q():
    df = load_panel("20150101")
    rows = []
    for code, g in df.groupby("code", sort=False):
        n = len(g)
        if n < 320:
            continue
        c = g["close"].to_numpy(float); v = g["volume"].to_numpy(float); d = g["date"].to_numpy()
        s = pd.Series(c)
        mn250 = s.rolling(250, min_periods=250).min().to_numpy()
        mx250 = s.rolling(250, min_periods=250).max().to_numpy()
        prev = s.shift(1)
        box_mx = prev.rolling(60, min_periods=60).max().to_numpy()
        box_mn = prev.rolling(60, min_periods=60).min().to_numpy()
        vma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            pos = (c - mn250) / np.where(mx250 > mn250, mx250 - mn250, np.nan)
            amp = box_mx / box_mn - 1.0
            sig = ((pos <= 0.35) & (amp <= 0.25) & (c > box_mx) & (v >= 1.2 * vma20)
                   & (c <= 30.0) & ~np.isnan(pos) & ~np.isnan(box_mx) & ~np.isnan(vma20))
        f20 = np.full(n, np.nan)
        f20[:-20] = c[20:] / c[:-20] - 1.0
        for i in np.where(sig)[0]:
            if d[i] >= "20160101" and i + 20 < n and np.isfinite(f20[i]):
                rows.append((d[i], f20[i] - 0.002))
    ev = pd.DataFrame(rows, columns=["date", "f20"])
    dm = ev.groupby("date")["f20"].mean()
    t = float(dm.mean() / (dm.std(ddof=1) / np.sqrt(len(dm)))) if len(dm) > 2 else float("nan")
    res = {"id": "BOX-BREAKOUT-LOWPOS", "mode": "independent_reimpl",
           "n_signal_days": int(len(ev)),
           "mean_pct": round(float(ev["f20"].mean()) * 100, 3),
           "win": round(float((ev["f20"] > 0).mean()), 4),
           "day_cluster_t": round(t, 2),
           "yearly_mean_pct": {k: round(float(v) * 100, 3)
                               for k, v in ev.assign(y=ev["date"].str[:4]).groupby("y")["f20"].mean().items()},
           "caveats": ["当日即买=突破日收盘入场, f20含0.2%成本; 逐字口径来自scout脚本内嵌box定义"]}
    jdump(res, "reaudit_box_result.json")
    print("Q) n_days=%d mean=%+.3f%% win=%.3f t=%.2f" % (
        len(ev), ev["f20"].mean() * 100, (ev["f20"] > 0).mean(), t))
    return res


def part_r():
    con = sqlite3.connect("file:%s?mode=ro" % ZTDB, uri=True)
    ztp = pd.read_sql("SELECT date,code,boards FROM zt_pool WHERE zt_stat='zt'", con)
    con.close()
    ztp = ztp[ztp["code"].str[:3].isin(PREFIXES)].copy()
    zt_days = ztp
    zt_count = zt_days.groupby("date")["code"].count().to_dict()
    boards_max = zt_days.groupby("date")["boards"].max().to_dict()
    hi_pool = defaultdict(list)
    for d, c in ztp[ztp["boards"] >= 2][["date", "code"]].itertuples(index=False):
        hi_pool[d].append(c)
    cal = sorted(zt_count)
    df = load_panel("20191201")
    dt_count = df[df["change_pct"] < -9].groupby("date")["code"].count().to_dict()
    med_all = df.groupby("date")["change_pct"].median().to_dict()
    px = df.set_index(["code", "date"])["change_pct"]
    cp = {d: i for i, d in enumerate(cal)}

    ma5_zt, ma5_dt = {}, {}
    for i, d in enumerate(cal):
        w = cal[max(0, i - 5):i]
        if len(w) == 5:
            ma5_zt[d] = sum(zt_count.get(x, 0) for x in w) / 5
            ma5_dt[d] = sum(dt_count.get(x, 0) for x in w) / 5
    sigs = {"A": [], "B": [], "C": []}
    for i, d in enumerate(cal):
        if i < 6:
            continue
        zt_n = zt_count.get(d, 0)
        if d in ma5_zt and zt_n <= 0.75 * ma5_zt[d] and zt_n < 90:
            sigs["A"].append(d)
        dt_n = dt_count.get(d, 0)
        if d in ma5_dt and dt_n >= max(20, 2 * ma5_dt[d]):
            sigs["B"].append(d)
        if i > 0:
            pv = cal[i - 1]
            if boards_max.get(pv, 0) >= 5 and boards_max.get(d, 0) <= boards_max.get(pv, 0) - 2:
                sigs["C"].append(d)
    out = {}
    for k, sl in sigs.items():
        gaps, years = [], defaultdict(list)
        for d in sl:
            j = cp.get(d)
            if j is None or j + 1 >= len(cal):
                continue
            nd = cal[j + 1]
            codes = hi_pool.get(d, [])
            rets = [px.get((cc, nd)) for cc in codes]
            rets = [x for x in rets if x is not None and not (isinstance(x, float) and np.isnan(x))]
            if len(rets) < 3:
                continue
            base = med_all.get(nd)
            if base is None:
                continue
            gap = sum(rets) / len(rets) - base
            gaps.append(gap)
            years[nd[:4]].append(gap)
        ydir = {y: ("+" if sum(v) / len(v) > 0 else "-") for y, v in sorted(years.items())}
        out[k] = {"n_triggers": len(sl), "n_used": len(gaps),
                  "mean_excess_pp": round(float(np.mean(gaps)), 2) if gaps else None,
                  "yearly_sign": ydir}
    res = {"id": "EBB-SIGNAL-NO-EDGE", "mode": "independent_reimpl_round1",
           "signals": out,
           "caveats": ["round1口径=T日高位票T+1 vs 全市场中位(对象选择偏差恰为原档第一轮发现); "
                       "round2修正(次日仍在池T+2, -0.32pp噪声级)未复算; 原档A/B/C触发数393/246/244(2020-2026全史)"]}
    jdump(res, "reaudit_ebb_result.json")
    print("R)", json.dumps(out, ensure_ascii=False))
    return res


def part_s():
    targets = [
        ("PEAD-PREG", ["attribution/pead_prereg_result_20260917.json"]),
        ("SELECTION-CHECKLIST", glob.glob("attribution/selection_checklist*.json")),
        ("TIMING-CONDMATRIX-STAGE1", ["data/timing_condmatrix_v1_results_20260914.json"]),
        ("PORTRAIT-REGIME-GATE", glob.glob("attribution/*regime_gate*.json")),
        ("CHIP-MANUAL-3GATE", glob.glob("reports/CHIP_MANUAL_3GATE_V13_RESULT*.json")),
        ("SKILL-SWEEP-STAGE1", ["_cache/skill_sweep_20260904.json"]),
    ]
    out = []
    for rid, paths in targets:
        found = None
        for p in paths:
            if p and os.path.exists(p):
                found = p
                break
        if found is None:
            out.append({"id": rid, "artifact": None, "status": "artifact_missing"})
            continue
        blob = open(found, encoding="utf-8", errors="replace").read()
        out.append({"id": rid, "artifact": found, "status": "artifact_ok",
                    "size_chars": len(blob)})
    # 数字寻回: 在对应 falsified_rules reason 中的关键数字是否出现于工件
    fr = json.load(open(r"D:\zcodesj\data\falsified_rules.json", encoding="utf-8"))
    import re
    for item in out:
        if item["status"] != "artifact_ok":
            continue
        rid = item["id"]
        reason = str(fr["rules"][rid].get("reason", ""))
        nums = re.findall(r"-?\d+\.\d+", reason)[:8]
        blob = open(item["artifact"], encoding="utf-8", errors="replace").read()
        hit = [x for x in nums if x in blob]
        item["claimed_numbers"] = nums
        item["numbers_found_in_artifact"] = hit
        item["coverage"] = "%d/%d" % (len(hit), len(nums))
    jdump({"items": out}, "reaudit_claim_vs_artifact_batch.json")
    print("S)", [(x["id"], x["status"], x.get("coverage", "")) for x in out])
    return out


if __name__ == "__main__":
    part_q()
    part_r()
    part_s()
    print("done")
