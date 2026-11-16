# -*- coding: utf-8 -*-
"""census 桶前向追踪台账（PD-011，2026-09-20 落地）

来源: P2 侦察收官待裁决项"census 台账每日接线"(p2-recon 20260919)；PD-011 auto_research。
解读依据: 每日桶计数已被 schtasks DirectionCensus_1530 覆盖(散装 json)，本件的增量=前向追踪——
  把 P2 侦察一次性回测的"UP 桶无条件 FAIL(月度 t0.58-1.14/fwd60 中位负)"变成持续监控:
  每日登记桶计数, T+5/T+10 到期回填各桶 fwd 中位 → 已证伪口径若随 regime 变化出现持续反转,
  有据可查(触发复查提请, 非复活信号——复活须新判据 prereg, falsified 规则不动)。
定位: 只读研究台账, 零交易权零调度(手动跑, 挂链随 PD-013 一并裁决)。
数据: data/direction_census_YYYYMMDD.json(每日产物) + qfq 主库(到期回填)。
台账: data/census_bucket_ledger.jsonl 按 date UPSERT 幂等。
用法: python census_bucket_track.py [--fwd 5,10]
退出码: 0 成功 / 2 数据源失败
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sqlite3
import statistics as st
from datetime import datetime

ROOT = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CENSUS_GLOB = os.path.join(ROOT, "data", "direction_census_*.json")
LEDGER = os.path.join(ROOT, "data", "census_bucket_ledger.jsonl")
SAMPLE_CAP = 120          # 大桶(BASE/MID)抽样上限, 小桶(UP/DOWN/TOP)全量
try:
    sys = __import__("sys"); sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_ledger() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                d = json.loads(line)
                out[d["date"]] = d
    return out


def trade_dates(con) -> dict[str, list]:
    """全部交易日升序(qfq 600000)"""
    rows = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE code='600000' ORDER BY date")]
    return {"dates": rows}


def fwd_close(con, code: str, base_date: str, dates: list[str], n: int):
    """base_date 后第 n 个交易日收盘; 无则 None"""
    i = None
    for j, d in enumerate(dates):
        if d > base_date:
            i = j
            break
    if i is None or i + n - 1 >= len(dates):
        return None
    tgt = dates[i + n - 1]
    r = con.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?", (code, tgt)).fetchone()
    return (float(r[0]), tgt) if r else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd", default="5,10")
    a = ap.parse_args()
    fwds = [int(x) for x in a.fwd.split(",")]

    files = sorted(glob.glob(CENSUS_GLOB))
    if not files:
        print("[CENSUS-FAIL] 无 direction_census 产物")
        return 2
    ledger = load_ledger()
    con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)
    cal = trade_dates(con)
    dates = cal["dates"]
    today = dates[-1]

    changed = 0
    seen截面: set[str] = set()
    for fp in files:
        fday = os.path.basename(fp)[len("direction_census_"):-len(".json")]  # 生成日 YYYYMMDD
        # 截面日=生成日之前(含)最近交易日(周末生成的文件=周五截面重复快照, 归并同一条)
        dstr = next((d for d in reversed(dates) if d <= fday), None)
        if dstr is None or dstr in seen截面:
            continue
        seen截面.add(dstr)
        census = json.load(open(fp, encoding="utf-8"))
        row = ledger.setdefault(dstr, {"date": dstr, "counts": census.get("counts", {})})
        row["counts"] = census.get("counts", row["counts"])
        # 到期回填
        base_idx = dates.index(dstr) if dstr in dates else None
        if base_idx is None:
            continue
        for n in fwds:
            key = f"fwd{n}"
            if key in row and row[key]:
                continue  # 已回填
            if len(dates) - 1 - base_idx < n:
                continue  # 未到期
            stats = {}
            for bucket, items in (census.get("buckets") or {}).items():
                if not isinstance(items, list) or not items:
                    continue
                sample = items if len(items) <= SAMPLE_CAP else items[::max(1, len(items) // SAMPLE_CAP)][:SAMPLE_CAP]
                rets = []
                for it in sample:
                    c = str(it.get("code", ""))
                    fc = fwd_close(con, c, dstr, dates, n)
                    px = it.get("px")
                    if fc and px:
                        rets.append(fc[0] / float(px) - 1)
                if rets:
                    stats[bucket] = {"med": round(st.median(rets), 4), "n": len(rets)}
            if stats:
                row[key] = stats
        changed += 1

    with open(LEDGER, "w", encoding="utf-8") as fh:
        for d in sorted(ledger):
            fh.write(json.dumps(ledger[d], ensure_ascii=False) + "\n")

    n_filled = sum(1 for r in ledger.values() if any(r.get(f"fwd{n}") for n in fwds))
    print(f"[CENSUS-OK] 台账{len(ledger)}日(扫描{changed}份产物) 已回填{n_filled}日 | 最新={today} "
          f"| 最新计数={ledger.get(dates and sorted(ledger)[-1] if ledger else '', {}).get('counts')}")
    con.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
