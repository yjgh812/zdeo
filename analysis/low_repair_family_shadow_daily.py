# -*- coding: utf-8 -*-
"""LOW_DRYUP_REPAIR_FAMILY 低位枯竭修复族影子日更(观测层零交易权)
对应 PREREG_LOW_DRYUP_REPAIR_FAMILY_20261017.json, 主人 2026-10-17「按推荐继续」批准激活。
主判据臂=T1低位缩量破跌停(臂级对照 2026-10-17 选优, T2/T3=观察臂只记录不判定);
事件定义与收益口径与 analysis/low_repair_family_arm_compare_20261017.py 逐位一致;
参数单一真源=data/PREREG_LOW_DRYUP_REPAIR_FAMILY_20261017.json params 字段
(缺字段即 fail-closed 退出, 禁止回退硬编码)。
数据源=dragon_quant_qfq.db stock_daily_meta_qfq(唯一回测价格源)。
幂等: 全史重算+台账原子覆盖, 重跑安全;
台账含样内参考层(in_sample=true, date<activation_since)与 OOS 影子层。
O1 低位强承接=外部观察件(5分钟域, 不在本 runner, 见 analysis/intraday_stageA_20261017.py)。
边界: 纯观测影子层零交易权, 禁止接交易闸门/交易 cron/executor;
OOS 验收门(LOCKED)=激活日 20261020 起 60 个交易日或 T1 OOS 到期 n>=30 先到,
pass=T1 OOS fwd20 med>=+1.5% 且 win>=52%; kill_switch=T1 OOS 前 10 个到期事件 med20 全<0 提请废案。

用法:
  python analysis/low_repair_family_shadow_daily.py            # 全史重算+台账原子覆盖+快照
  python analysis/low_repair_family_shadow_daily.py --report   # 同上并打印简报
  python analysis/low_repair_family_shadow_daily.py --prereg=X.json  # 指定prereg(fail-closed测试用)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_LOW_DRYUP_REPAIR_FAMILY_20261017.json")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(r"D:\zcodesj\data\low_repair_family_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\low_repair_family_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\low_repair_family_shadow_brief_latest.txt")


def load_prereg(path: Path) -> dict:
    """参数单一真源: prereg params 字段; 缺字段 fail-closed。"""
    p = json.loads(path.read_text(encoding="utf-8"))
    if p.get("status") != "ACTIVE":
        raise SystemExit(f"[FAIL-CLOSED] prereg status={p.get('status')} 非ACTIVE")
    pa = p.get("params") or {}
    need = ["pos_max", "judge_arms", "observe_arms", "activation_since",
            "vr_max_T1", "vr_max_T2", "vr_max_T3", "board_k_T3",
            "dedup_days", "holding_horizons", "stop_sim_pct",
            "universe_exclude_prefix"]
    for k in need:
        if k not in pa:
            raise SystemExit(f"[FAIL-CLOSED] prereg params 缺字段 {k}")
    if pa["entry"] != "signal_day_close(持有语义)":
        raise SystemExit("[FAIL-CLOSED] prereg entry 非 signal_day_close(持有语义)")
    if pa["judge_arms"] != ["T1"]:
        raise SystemExit("[FAIL-CLOSED] judge_arms 与 LOCKED 定义不符")
    return {"pa": pa, "since": int(pa["activation_since"]), "name": p.get("name")}


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    args = sys.argv[1:]
    prereg_path = PREREG
    report = False
    for a in args:
        if a == "--report":
            report = True
        elif a.startswith("--prereg="):
            prereg_path = Path(a.split("=", 1)[1])
    cfg = load_prereg(prereg_path)
    pa = cfg["pa"]

    conn = sqlite3.connect(r"file:" + DB + "?mode=ro", uri=True)
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
    df["zt_close"] = (df["change_pct"] >= thr) & (df["close"] >= df["high"] - 1e-9)
    df["dt_close"] = (df["change_pct"] <= -thr) & (df["close"] <= df["low"] + 1e-9)
    hi_pct = df["high"] / df["prev_close"] * 100 - 100
    lo_pct = df["low"] / df["prev_close"] * 100 - 100
    df["zt_touch"] = hi_pct >= thr
    df["dt_touch"] = lo_pct <= -thr
    df["yizi"] = df["zt_close"] & ((df["high"] - df["low"]) / df["close"] < 0.005)
    zt_i = df["zt_close"].astype(int)
    brk = (zt_i != zt_i.shift(1)) | (df["code"] != df["code"].shift(1))
    gid = brk.cumsum()
    df["board_k"] = zt_i.groupby([df["code"], gid]).cumsum() * zt_i
    HZ = [int(x) for x in pa["holding_horizons"]]
    for k in HZ:
        df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
    df["open1"] = g["open"].shift(-1)
    df["open_prem"] = df["open1"] / df["close"] - 1

    pos_ok = df["pos250"] <= float(pa["pos_max"])
    vr_max = {"T1": float(pa["vr_max_T1"]), "T2": float(pa["vr_max_T2"]), "T3": float(pa["vr_max_T3"])}
    masks = {
        "T1": df["dt_touch"] & ~df["dt_close"] & pos_ok & (df["vr20"] < vr_max["T1"]),
        "T2": df["zt_touch"] & ~df["zt_close"] & pos_ok & (df["vr20"] < vr_max["T2"]),
        "T3": df["zt_close"] & (df["board_k"] == int(pa["board_k_T3"])) & pos_ok
              & (df["vr20"] < vr_max["T3"]) & ~df["yizi"],
    }
    DD = int(pa["dedup_days"])
    STOP = float(pa["stop_sim_pct"])

    codes_arr = df["code"].to_numpy()
    dates_arr = df["date"].to_numpy()
    closes = df["close"].to_numpy()
    lows = df["low"].to_numpy()
    fwds = {k: df[f"fwd{k}"].to_numpy() for k in HZ}
    bounds = {}
    start = 0
    for j in range(1, len(codes_arr) + 1):
        if j == len(codes_arr) or codes_arr[j] != codes_arr[start]:
            bounds[codes_arr[start]] = (start, j)
            start = j
    since_s = str(cfg["since"])

    rows = []
    for arm in ("T1", "T2", "T3"):
        sub = df[masks[arm]]
        if len(sub) == 0:
            continue
        keep = np.zeros(len(sub), dtype=bool)
        last = {}
        scodes = sub["code"].to_numpy()
        srows = sub["rowid"].to_numpy()
        for i in range(len(sub)):
            c = scodes[i]
            if srows[i] - last.get(c, -10**9) >= DD:
                keep[i] = True
                last[c] = srows[i]
        sub = sub[keep]
        for _, r in sub.iterrows():
            # 全局行索引 = 码起始位置 + 码内rowid(df按(code,date)排序, rowid=码内cumcount)
            i = bounds[r["code"]][0] + int(r["rowid"])
            e = bounds[r["code"]][1]
            vr = r["vr20"]
            ps = r["pos250"]
            op = r["open_prem"]
            rec = {"code": r["code"], "arm": arm, "event_date": int(dates_arr[i]),
                   "in_sample": bool(dates_arr[i] < since_s),
                   "vr20": round(float(vr), 3) if np.isfinite(vr) else None,
                   "pos250": round(float(ps), 3) if np.isfinite(ps) else None,
                   "open_prem": round(float(op) * 100, 2) if np.isfinite(op) else None,
                   "stop_hit": False, "net20": None}
            for k in HZ:
                rec[f"fwd{k}"] = round(float(fwds[k][i]) * 100, 3) if i + k < e and np.isfinite(fwds[k][i]) else None
            hit = False
            for jj in range(i + 1, min(i + 61, e)):
                if lows[jj] <= closes[i] * (1 + STOP):
                    hit = True
                    break
            rec["stop_hit"] = hit
            f20 = rec.get("fwd20")
            rec["net20"] = STOP * 100 if hit else f20
            rows.append(rec)

    led = pd.DataFrame(rows)
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    led.to_json(tmp, orient="records", lines=True, force_ascii=False)
    os.replace(tmp, LEDGER)

    snap = {"run": "low_repair_family_shadow", "prereg": PREREG.name,
            "judge_arms": pa["judge_arms"], "observe_arms": pa["observe_arms"],
            "activation_since": cfg["since"], "total_rows": int(len(led)),
            "arms": {}}
    for arm in ("T1", "T2", "T3"):
        a = led[led["arm"] == arm]
        ins = a[a["in_sample"] & a["fwd20"].notna()]
        oos = a[(~a["in_sample"]) & a["fwd20"].notna()]
        arm_rec = {
            "in_sample_n": int(len(ins)),
            "in_sample_med20": round(float(ins["fwd20"].median()), 2) if len(ins) else None,
            "in_sample_win20": round(float((ins["fwd20"] > 0).mean()) * 100, 1) if len(ins) else None,
            "oos_n_matured": int(len(oos)),
            "oos_med20": round(float(oos["fwd20"].median()), 2) if len(oos) else None,
            "oos_win20": round(float((oos["fwd20"] > 0).mean()) * 100, 1) if len(oos) else None,
            "oos_open_n": int(((~a["in_sample"]) & a["fwd20"].isna()).sum()),
        }
        if arm == "T1" and len(oos) >= 30:
            arm_rec["gate_pass_now"] = bool(oos["fwd20"].median() >= 1.5 and (oos["fwd20"] > 0).mean() >= 0.52)
        snap["arms"][arm] = arm_rec
    atomic_write_text(SNAPSHOT, json.dumps(snap, ensure_ascii=False, indent=1))

    t1 = snap["arms"]["T1"]
    brief = (
        f"LOW_DRYUP_REPAIR_FAMILY 影子简报 生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M}\n"
        f"prereg={PREREG.name} judge_arms={pa['judge_arms']} observe_arms={pa['observe_arms']}\n"
        f"T1(主判据): 样内 n={t1['in_sample_n']} med20={t1['in_sample_med20']} win={t1['in_sample_win20']} | "
        f"OOS 到期 n={t1['oos_n_matured']} med20={t1['oos_med20']} win={t1['oos_win20']} 未到期={t1['oos_open_n']}\n"
        f"T2(观察): 样内 n={snap['arms']['T2']['in_sample_n']} OOS 到期 n={snap['arms']['T2']['oos_n_matured']}\n"
        f"T3(观察): 样内 n={snap['arms']['T3']['in_sample_n']} OOS 到期 n={snap['arms']['T3']['oos_n_matured']}\n"
        f"验收门(LOCKED): 激活20261020起60交易日或T1 OOS n>=30; pass=med>=+1.5%且win>=52%; kill=前10事件med全负\n"
    )
    atomic_write_text(BRIEF, brief)

    if report:
        print(brief)
        print("snapshot:", json.dumps(snap["arms"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
