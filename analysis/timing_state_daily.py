# -*- coding: utf-8 -*-
"""择时状态机日更: T-1涨停家数定档 → 档位台账 + 晨会简报(观测层零交易权)
对应 PD-034 / PREREG_TIMING_STATE_20261007(指纹a064a8d5109f39c2), 判据预钉死
(60交易日 强档f5med>=+0.5pp / win>=55% / n>=30)。
计算口径与裸检验 reports/timing_state_machine_20261007.md 一致, 禁止改参数。

用法:
  python analysis/timing_state_daily.py                # 扫描库内最新交易日(幂等)
  python analysis/timing_state_daily.py --date=20260930
  python analysis/timing_state_daily.py --backfill 20190101 20260930   # 热身(in_sample)
  python analysis/timing_state_daily.py --report       # 生成晨会简报
幂等: date主键, 重跑覆盖同行; 影子窗=20261008起, 之前日期in_sample=true。
边界: 纯观测层, 禁止任何下单/卖出/接闸动作; R闸候选须主人第二道裁决。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
LEDGER = Path(r"D:\zcodesj\data\timing_state_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\timing_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\timing_state_brief_latest.txt")
SHADOW_START = 20261008
SHADOW_DAYS = 60
GATES = {"f5_med_min": 0.5, "f5_win_min": 55.0, "n_min": 30}


def gear(n: float) -> str:
    if n >= 80:
        return "超强>=80"
    if n >= 61:
        return "强档61-79"
    if n >= 40:
        return "中档40-60"
    return "弱档<40"


def load_frame() -> pd.DataFrame:
    conn = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql(
        "SELECT date,change_pct FROM stock_daily_meta_qfq "
        "WHERE (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
        conn)
    conn.close()
    df["date"] = df["date"].astype(int)
    return df.sort_values("date").reset_index(drop=True)


def compute_daily(df: pd.DataFrame) -> pd.DataFrame:
    zt = df.groupby("date")["change_pct"].apply(lambda s: (s >= 9.5).sum())
    mkt = zt.to_frame().rename(columns={"change_pct": "zt_cnt"})
    mkt["date"] = mkt.index.astype(int)
    mkt = mkt.sort_index().reset_index(drop=True)
    ew = df.groupby("date")["change_pct"].mean().rename("ew_ret")
    mkt = mkt.merge(ew, left_on="date", right_index=True, how="left")
    mkt["f5"] = mkt["ew_ret"].rolling(5).sum()
    mkt["f20"] = mkt["ew_ret"].rolling(20).sum()
    mkt["gear"] = mkt["zt_cnt"].apply(gear)           # 当日档(供次日引用)
    mkt["gear_prev"] = mkt["zt_cnt"].shift(1).apply(gear)  # T-1档=买入判定档
    return mkt


def load_ledger() -> dict:
    rows = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["date"]] = r
    return rows


def upsert(rows: dict, rec: dict) -> bool:
    d = rec["date"]
    if rows.get(d) == rec:
        return False
    rows[d] = rec
    return True


def save_ledger(rows: dict) -> None:
    tmp = LEDGER.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for d in sorted(rows):
            f.write(json.dumps(rows[d], ensure_ascii=False) + "\n")
    tmp.replace(LEDGER)


def make_rec(r: pd.Series, in_sample: bool) -> dict:
    return {
        "date": int(r["date"]),
        "zt_cnt": int(r["zt_cnt"]),
        "gear_prev": r["gear_prev"],
        "gear_today": r["gear"],
        "ew_ret": round(float(r["ew_ret"]), 4) if pd.notna(r["ew_ret"]) else None,
        "f5": round(float(r["f5"]), 4) if pd.notna(r["f5"]) else None,
        "f20": round(float(r["f20"]), 4) if pd.notna(r["f20"]) else None,
        "in_sample": in_sample,
    }


def evaluate_gates(rows: dict) -> dict:
    shadow = [r for r in rows.values()
              if r["date"] >= SHADOW_START and not r["in_sample"] and r["f5"] is not None]
    strong = [r for r in shadow if r["gear_prev"] in ("超强>=80", "强档61-79")]
    out = {"shadow_days": len(shadow), "strong_n": len(strong),
           "f5_med": None, "f5_win": None,
           "verdict": "未满窗" if len(shadow) < SHADOW_DAYS else "满窗",
           "gate_detail": ""}
    if strong:
        f5 = np.array([r["f5"] for r in strong])
        out["f5_med"] = round(float(np.median(f5)), 2)
        out["f5_win"] = round(float((f5 > 0).mean() * 100), 1)
    ready = len(shadow) >= SHADOW_DAYS
    g1 = out["f5_med"] is not None and out["f5_med"] >= GATES["f5_med_min"]
    g2 = out["f5_win"] is not None and out["f5_win"] >= GATES["f5_win_min"]
    g3 = len(strong) >= GATES["n_min"]
    out["gate_detail"] = f"G1(f5med>=+0.5):{g1} G2(win>=55):{g2} G3(n>=30):{g3}"
    if ready:
        out["verdict"] = "三门全过->呈请R闸候选" if (g1 and g2 and g3) else "任一门不过->归档"
    return out


def write_brief(rows: dict, gates: dict) -> str:
    last = max(rows.values(), key=lambda r: r["date"])
    trend = [rows[d] for d in sorted(rows)[-3:]]
    lines = [
        f"【择时状态 {last['date']}】昨日涨停{last['zt_cnt']}家 → 今日档位: {last['gear_today']}",
        f"判定: " + {"超强>=80": "可以买(最强档)", "强档61-79": "可以买", "中档40-60": "谨慎限购(仅已验证最强信号)",
                  "弱档<40": "禁买(新开仓停止)"}[last["gear_today"]],
        "近3日: " + " | ".join(f"{t['date']} {t['gear_today']}({t['zt_cnt']}家)" for t in trend),
        f"影子窗: {gates['shadow_days']}/{SHADOW_DAYS}日 强档n={gates['strong_n']} "
        f"f5med={gates['f5_med']}pp win={gates['f5_win']}% [{gates['verdict']}] {gates['gate_detail']}",
        "(观测层零交易权; 弱档禁买接闸须主人第二道裁决)",
    ]
    text = "\n".join(lines)
    BRIEF.write_text(text, encoding="utf-8")
    SNAPSHOT.write_text(json.dumps(
        {"latest": last, "gates": gates, "generated": last["date"]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return text


def main() -> int:
    args = [a for a in sys.argv[1:] if a.startswith("--")]
    df = load_frame()
    mkt = compute_daily(df)
    rows = load_ledger()

    backfill = next((a for a in args if a.startswith("--backfill")), None)
    added = 0
    if backfill:
        _, lo, hi = sys.argv[sys.argv.index(backfill) - 0: sys.argv.index(backfill) + 3][:3] \
            if False else (None, sys.argv[sys.argv.index(backfill) + 1], sys.argv[sys.argv.index(backfill) + 2])
        lo_i, hi_i = int(lo), int(hi)
        sub = mkt[(mkt["date"] >= lo_i) & (mkt["date"] <= hi_i)].dropna(subset=["gear_prev"])
        for _, r in sub.iterrows():
            rec = make_rec(r, in_sample=int(r["date"]) < SHADOW_START)
            if upsert(rows, rec):
                added += 1
        save_ledger(rows)
        print(f"[BACKFILL] {lo}-{hi} 追加/覆盖 {added} 行 (in_sample口径)")
        return 0

    date_arg = next((a for a in args if a.startswith("--date=")), None)
    if date_arg:
        target = int(date_arg.split("=", 1)[1])
    else:
        target = int(mkt["date"].max())
    sub = mkt[mkt["date"] == target].dropna(subset=["gear_prev"])
    if sub.empty:
        print(f"[SKIP] {target} 无数据(非交易日/未更新)")
        return 0
    rec = make_rec(sub.iloc[0], in_sample=int(target) < SHADOW_START)
    changed = upsert(rows, rec)
    save_ledger(rows)
    gates = evaluate_gates(rows)
    print(f"[SCAN] {target} zt={rec['zt_cnt']} 今日档={rec['gear_today']} "
          f"{'新增' if changed else '幂等覆盖'}; 台账{len(rows)}行")
    print(f"[GATES] {gates['verdict']} 影子{gates['shadow_days']}日 强档n={gates['strong_n']} "
          f"f5med={gates['f5_med']} win={gates['f5_win']} | {gates['gate_detail']}")
    if "--report" in args:
        print(write_brief(rows, gates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
