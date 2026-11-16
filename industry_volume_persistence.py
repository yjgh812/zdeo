#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙系统·行业量能持续度影子件（H-NEW6，2026-09-16 立项）

功能：每日计算申万一级 31 行业量能状态，输出快照 + 日追加台账。
口径：volr20 = 当日量/20日均量；hot5 = 近5日中 volr20>=1.0 天数；C1(持续放量) = volr20>=1.3 且 hot5>=4。
证据：reports/volume_rules_lib_20260916.md §4.2（C1 行业 fwd20 +1.96%/胜率56.5%，n=14168，本地实证）。
输出：data/industry_volume_state_latest.json（快照）+ data/industry_volume_state_daily.jsonl（逐日台账，同日幂等覆盖）。
状态：影子观察件（20 交易日评估，2026-10 中旬首评）；只写自身台账文件，零生产链依赖，失败不阻断任何链路。
用法：python industry_volume_persistence.py [--date YYYYMMDD] [--selftest]
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SW_DB = Path(r"D:\workbuddyspace\databases\sw_industry.db")
OUT_DIR = Path(r"D:\zcodesj\data")
LATEST = OUT_DIR / "industry_volume_state_latest.json"
DAILY = OUT_DIR / "industry_volume_state_daily.jsonl"
C1_VOLR, C1_HOT = 1.3, 4


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} [industry_vol] {msg}", flush=True)


def compute():
    if not SW_DB.exists():
        raise FileNotFoundError(f"申万库缺失: {SW_DB}")
    con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True)
    try:
        sw = sqlite_read(con)
    finally:
        con.close()
    return sw


def sqlite_read(con):
    import pandas as pd
    sw = pd.read_sql("SELECT ts_code,name,trade_date,close,volume FROM industry_kline_sw WHERE level='L1'", con)
    sw = sw[sw.volume > 0].copy()
    sw["trade_date"] = sw.trade_date.astype(str).str.replace("-", "", regex=False)
    sw = sw.sort_values(["ts_code", "trade_date"])
    frames = []
    for _code, g in sw.groupby("ts_code", sort=False):
        if len(g) < 300:
            continue
        g = g.reset_index(drop=True).copy()
        g["volr20"] = g["volume"] / g["volume"].rolling(20, min_periods=15).mean()
        g["hot5"] = g["volr20"].rolling(5).apply(lambda x: (x >= 1.0).sum(), raw=True)
        g["c1"] = (g.volr20 >= C1_VOLR) & (g.hot5 >= C1_HOT)
        frames.append(g)
    if not frames:
        raise ValueError("行业序列为空, 数据源异常")
    return pd.concat(frames)


def build_state(swall, date=None):
    d = date or swall.trade_date.max()
    day = swall[swall.trade_date == d]
    if len(day) == 0:
        raise ValueError(f"{d} 无行业数据(非交易日或数据未更新)")
    recs = [{"industry": r.name, "volr20": round(float(r.volr20), 4),
             "hot5": int(r.hot5), "c1": bool(r.c1)} for r in day.itertuples() if r.volr20 == r.volr20]
    recs.sort(key=lambda x: -x["volr20"])
    return {"date": d, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "口径": "volr20=量/20日均量; hot5=近5日volr20>=1.0天数; C1=volr20>=1.3且hot5>=4",
            "n_industries": len(recs), "c1_count": sum(1 for x in recs if x["c1"]),
            "c1_industries": [x["industry"] for x in recs if x["c1"]], "industries": recs}


def write_outputs(state):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = []
    if DAILY.exists():
        for line in DAILY.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("date") != state["date"]:
                    lines.append(line)
            except json.JSONDecodeError:
                continue
    slim = {k: state[k] for k in ("date", "generated_at", "n_industries", "c1_count", "c1_industries")}
    lines.append(json.dumps(slim, ensure_ascii=False))
    DAILY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYYMMDD, 缺省=库内最新日")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    try:
        swall = compute()
        if args.selftest:
            assert swall.ts_code.nunique() == 31, f"L1行业数异常: {swall.ts_code.nunique()}"
            assert {"ts_code", "name", "trade_date", "volr20", "hot5", "c1"} <= set(swall.columns)
            assert swall.trade_date.max() >= "20250101", "数据陈旧"
            print("SELFTEST PASS: 31行业/列完整/数据新鲜")
            return 0
        state = build_state(swall, args.date)
        write_outputs(state)
        log(f"date={state['date']} 行业={state['n_industries']} 持续放量C1={state['c1_count']}个 "
            f"{'/'.join(state['c1_industries']) if state['c1_industries'] else '无'} "
            f"量能Top3={'/'.join(x['industry'] for x in state['industries'][:3])}")
        return 0
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
