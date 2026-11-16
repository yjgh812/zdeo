# -*- coding: utf-8 -*-
"""DIPFLUSH_BREADTH_BASKET 深踩广度日篮子影子日更(观测层零交易权)
对应 PREREG_DIPFLUSH_BREADTH_BASKET_20261011, 主人2026-10-11「继续」批准激活影子层。
口径与 reports/tgb_pullflush_proto_20261011.md §三/§四 及 tmp/bt_pullflush_proto_20261011.py 逐位一致;
参数单一真源=data/PREREG_DIPFLUSH_BREADTH_BASKET_20261011.json params 字段(缺字段fail-closed退出)。
数据源=dragon_quant_qfq.db stock_daily_meta_qfq(唯一回测价格源)。
幂等: 全史重算+台账原子覆盖, 重跑安全; 台账含样内参考层(in_sample=true)与OOS影子层。
边界: 纯观测零交易权, 禁止接交易闸门/交易cron/executor;
OOS验收门(LOCKED)=到期篮子n>=10或日历2027-06-30先到, pass=R5中位>=+100bp且win>=60%,
kill_switch=OOS前5个到期篮子R5中位<0提请废案。

用法:
  python analysis/dipflush_breadth_shadow_daily.py            # 全史重算+台账原子覆盖+快照
  python analysis/dipflush_breadth_shadow_daily.py --report   # 同上并打印快照
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_DIPFLUSH_BREADTH_BASKET_20261011.json")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
LEDGER = Path(r"D:\zcodesj\data\dipflush_breadth_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\dipflush_breadth_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\dipflush_breadth_brief_latest.txt")
BLOCKS = ["000_", "001_", "002_", "003_", "300_", "301_", "600_", "601_", "603_", "605_"]


def load_prereg() -> dict:
    """参数单一真源: prereg params 字段; 缺字段fail-closed。"""
    p = json.loads(PREREG.read_text(encoding="utf-8"))
    if p.get("status") != "ACTIVE":
        raise SystemExit(f"[FAIL-CLOSED] prereg status={p.get('status')} 非ACTIVE")
    pa = p.get("params") or {}
    for k in ("deep_ratio", "ma_short", "ma_mid", "ma_long", "gap_max",
              "breadth_min", "holding_days", "entry", "gate", "universe_blocks"):
        if k not in pa:
            raise SystemExit(f"[FAIL-CLOSED] prereg params 缺字段 {k}")
    if pa["entry"] != "next_open":
        raise SystemExit("[FAIL-CLOSED] prereg entry 非next_open")
    since = int(json.loads(p["approval"].replace("'", '"'))["activation_since"])
    acc = json.loads(p["acceptance_locked"].replace("'", '"'))
    return {"pa": pa, "since": since, "acc": acc, "name": p.get("name"), "status": p.get("status")}


def scan_stock(code: str, df: pd.DataFrame, cfg: dict):
    """单股: 产出演算事件记录(code, sig_date, entry_date, r1, r2, r5)+日收益序列。"""
    pa = cfg["pa"]
    DS, DM, DL = float(pa["deep_ratio"]), int(pa["ma_mid"]), int(pa["ma_long"])
    GMAX = float(pa["gap_max"]); HOLD = int(pa["holding_days"])
    if len(df) < DL + 40:
        return [], {}
    o = df["open"].values; l = df["low"].values; c = df["close"].values
    d = df["date"].values.astype(int)
    n = len(df)
    ma = lambda arr, w: pd.Series(arr).rolling(w).mean().values
    m5, m10, m20 = ma(c, int(pa["ma_short"])), ma(c, DM), ma(c, DL)
    events = []
    for i in range(DL, n - 1):
        if np.isnan(m20[i - 1]):
            continue
        if not (m5[i - 1] > m10[i - 1] > m20[i - 1] and c[i - 1] > m5[i - 1]):
            continue
        if not (l[i] <= m10[i] * DS and c[i] > m20[i]):
            continue
        if o[i + 1] / c[i] - 1 >= GMAX:
            continue
        r1 = r2 = r5 = None
        if i + 1 < n and o[i + 1] > 0:
            r1 = float(c[i + 1] / o[i + 1] - 1)
            if i + 2 < n:
                r2 = float(c[i + 2] / o[i + 1] - 1)
            if i + HOLD < n:
                r5 = float(c[i + HOLD] / o[i + 1] - 1)
        events.append({"code": code, "sig_date": int(d[i]), "entry_date": int(d[i + 1]),
                       "r1": r1, "r2": r2, "r5": r5})
    rets = {int(dd): float(rr) for dd, rr in zip(d[1:], c[1:] / c[:-1] - 1)}
    return events, rets


def main(report: bool):
    t0 = time.time()
    cfg = load_prereg()
    since = cfg["since"]
    con = sqlite3.connect(DB)
    all_events, mkt, mkt_cnt = [], {}, {}
    for pref in cfg["pa"]["universe_blocks"]:
        q = ("SELECT code,date,open,low,close FROM stock_daily_meta_qfq "
             "WHERE code LIKE ? ORDER BY code,date")
        df = pd.read_sql(q, con, params=(pref + "%",))
        if df.empty:
            continue
        df["date"] = df["date"].astype(int)
        for code, g in df.groupby("code"):
            ev, rets = scan_stock(code, g.reset_index(drop=True), cfg)
            all_events.extend(ev)
            for k, v in rets.items():
                mkt[k] = mkt.get(k, 0.0) + v
                mkt_cnt[k] = mkt_cnt.get(k, 0) + 1
    con.close()

    # 市场等权指数(与信号同宇宙同过滤口径, 和/计数同源) + MA20闸
    dates = sorted(mkt.keys())
    hist, mid_series, gate = [], {}, {}
    mi = 1.0
    for dt in dates:
        mi *= (1 + mkt[dt] / max(mkt_cnt.get(dt, 0), 1))
        hist.append(mi)
        mid_series[dt] = mi
        gate[dt] = bool(len(hist) >= 20 and mi >= np.mean(hist[-20:]))

    # 篮子聚合(信号日级): 仅 breadth>=breadth_min 发射(=prereg trigger_locked)
    BMIN = int(cfg["pa"]["breadth_min"])
    by_sig = {}
    for e in all_events:
        by_sig.setdefault(e["sig_date"], []).append(e)
    gated_event_days = 0
    rows = []
    for sig in sorted(by_sig):
        if not gate.get(sig, False):
            continue
        mem = by_sig[sig]
        gated_event_days += 1
        if len(mem) < BMIN:
            continue
        r5s = [m["r5"] for m in mem if m["r5"] is not None]
        r1s = [m["r1"] for m in mem if m["r1"] is not None]
        settled = len(r5s) == len(mem) and len(r5s) > 0
        rows.append({
            "sig_date": sig, "entry_date": mem[0]["entry_date"],
            "n_events": len(mem), "breadth": len(mem),
            "basket": sorted(m["code"] for m in mem),
            "r1_mean": round(float(np.mean(r1s)), 5) if r1s else None,
            "r5_mean": round(float(np.mean(r5s)), 5) if r5s else None,
            "settled": settled,
            "in_sample": bool(mem[0]["entry_date"] < since),
        })
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(LEDGER)

    # OOS 统计与验收门
    oos = [r for r in rows if not r["in_sample"]]
    oos_set = [r for r in oos if r["settled"]]
    r5s = [r["r5"] * 100 for r in oos_set]
    acc = cfg["acc"]
    gate_state = {"window": acc["window"], "pass_rule": acc["pass"],
                  "kill_switch": acc["kill_switch"], "oos_n_settled": len(oos_set)}
    if len(oos_set) >= 5:
        first5 = [r["r5"] * 100 for r in oos_set[:5]]
        gate_state["kill_check"] = {"first5_med_r5": round(float(np.median(first5)), 2),
                                    "kill_triggered": bool(np.median(first5) < 0)}
    if len(oos_set) >= 10:
        med = float(np.median(r5s)); win = float(np.mean([x > 0 for x in r5s])) * 100
        gate_state["verdict_now"] = ("PASS" if med >= 100 and win >= 60
                                     else "BELOW_GATE(未达验收线,继续观察)")
    snap = {"name": cfg["name"], "status": cfg["status"],
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "since": since,
            "gated_event_days": gated_event_days,
            "fired_days_breadth_ge_min": len(rows),
            "in_sample": {"n": sum(1 for r in rows if r["in_sample"]),
                          "settled": sum(1 for r in rows if r["in_sample"] and r["settled"]),
                          "med_r5_bp": round(float(np.median([r["r5_mean"] for r in rows if r["in_sample"] and r["settled"]])) * 1e4, 1) if any(r["in_sample"] and r["settled"] for r in rows) else None},
            "oos": {"n_days": len(oos), "n_settled": len(oos_set),
                    "med_r5_bp": round(float(np.median(r5s)), 1) if r5s else None,
                    "win_pct": round(float(np.mean([x > 0 for x in r5s])) * 100, 1) if r5s else None},
            "gate": gate_state, "elapsed_s": round(time.time() - t0, 1)}
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"[dipflush] signal_days={len(rows)}(oos={len(oos)}) oos_settled={len(oos_set)} "
             f"oos_med_r5={snap['oos']['med_r5_bp']} gate={gate_state.get('verdict_now', 'WAITING')} "
             f"{round(time.time() - t0, 0)}s"]
    BRIEF.write_text("\n".join(lines), encoding="utf-8")
    if report:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    print(lines[0])


if __name__ == "__main__":
    main("--report" in sys.argv)
