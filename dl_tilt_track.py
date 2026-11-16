#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""dl_tilt_track.py · DL 红利低波 tilt 接线前向 A/B 台账 CLI（2026-09-06，PREREG_DL_TILT_WIRING_20260906）

  A 臂 = bp_ep 月度调仓生产 Top20（合同序）
  D 臂 = 同一 Top50 池按 DL(=0.5*rank(dv_ratio)+0.5*rank(-vol60)) 重排取 Top20
  判据/暂停条款见 reports/PREREG_DL_TILT_WIRING_20260906.md；首评 2026-12-31。

用法（每调仓月一次）：
  python dl_tilt_track.py append --pool <top50逗号分隔> --top20 <生产Top20逗号分隔> [--date YYYY-MM-DD] [--pick-date YYYYMMDD]
  python dl_tilt_track.py show
  python dl_tilt_track.py eval --fwd 20 10     # 12/31 首评
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
LEDGER = Path(BASE) / "data" / "dl_tilt_track.jsonl"


def _norm6(c: str) -> str:
    c = str(c).strip()
    for pre in ("sh", "sz", "SH", "SZ"):
        if c.startswith(pre):
            c = c[len(pre):]
    return c.zfill(6)


def _valid_date(d: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""):
        return False
    try:
        _date.fromisoformat(d)
        return True
    except ValueError:
        return False


def compute_dl(codes: list[str], pick_date: str) -> dict[str, float]:
    """DL = 0.5*rank(dv_ratio)+0.5*rank(-vol60)，as-of pick_date（缺数据者不返回=排末位）。"""
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    dv = pd.read_sql(
        "SELECT ts_code,trade_date,dv_ratio FROM daily_basic WHERE trade_date<=?",
        con, params=[pick_date])
    con.close()
    dv["code"] = dv["ts_code"].str[:6]
    dv = dv[dv["code"].isin(codes)].copy()
    dv["td"] = pd.to_numeric(dv["trade_date"], errors="coerce")
    dv["dv_ratio"] = pd.to_numeric(dv["dv_ratio"], errors="coerce")
    dv = dv.dropna(subset=["dv_ratio"]).sort_values(["code", "td"]).groupby("code").tail(1)
    qcon = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    ph = ",".join("?" * len(codes))
    q = pd.read_sql(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date<=?",
        qcon, params=[*codes, int(pick_date)])
    qcon.close()
    q = q.sort_values(["code", "date"])
    q["ret"] = q.groupby("code")["close"].pct_change()
    v = q.groupby("code")["ret"].apply(lambda s: s.iloc[-60:].std(ddof=1) if len(s.dropna()) >= 40 else np.nan)
    vol = {k: (float(x) if x == x else None) for k, x in v.items()}
    dv_map = dict(zip(dv["code"], dv["dv_ratio"]))
    rows = [{"code": c, "dv": dv_map.get(c), "vol": vol.get(c)} for c in codes]
    df = pd.DataFrame(rows).dropna(subset=["dv", "vol"])
    if df.empty:
        return {}
    df["r_dv"] = df["dv"].rank(pct=True)
    df["r_lv"] = (-df["vol"]).rank(pct=True)
    df["DL"] = 0.5 * df["r_dv"] + 0.5 * df["r_lv"]
    return dict(zip(df["code"], df["DL"].round(4)))


def cmd_append(args) -> int:
    global LEDGER
    LEDGER = Path(args.file) if getattr(args, "file", None) else LEDGER
    d = args.date or _date.today().isoformat()
    if not _valid_date(d):
        print(f"[FAIL] 非法日期 {d!r}"); return 2
    pool = [_norm6(x) for x in args.pool.split(",") if x.strip()]
    top20 = [_norm6(x) for x in args.top20.split(",") if x.strip()]
    bad = [c for c in pool + top20 if not re.fullmatch(r"\d{6}", c)]
    if bad:
        print(f"[FAIL] 非法代码 {bad}"); return 2
    pool = list(dict.fromkeys(pool))
    if not set(top20) <= set(pool):
        print("[FAIL] top20 须为 pool 子集（同池对照合同）"); return 2
    pick = (args.pick_date or d.replace("-", ""))[:8]
    dl = compute_dl(pool, pick)
    ranked = sorted(pool, key=lambda c: dl.get(c, float("-inf")), reverse=True)
    enhanced = [c for c in ranked if c in set(top20)][:20]
    row = {"date": d, "pick_date": pick, "pool": pool, "original": top20, "enhanced": enhanced,
           "dl_cover": f"{len(dl)}/{len(pool)}", "notes": args.note or "",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    dup = None
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("date") == d and "init" not in o:
                dup = o
    if dup and not args.force:
        print(f"[FAIL] {d} 已登记（--force 覆盖）"); return 2
    if LEDGER.exists() and LEDGER.stat().st_size > 0:
        with LEDGER.open("rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                with LEDGER.open("a", encoding="utf-8") as f:
                    f.write("\n")
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[OK] 登记 {d}: 池{len(pool)} A={len(top20)} D={len(enhanced)} DL覆盖={row['dl_cover']}")
    return 0


def cmd_show(args) -> int:
    ledger = Path(getattr(args, "file", str(LEDGER)))
    if not ledger.exists():
        print("[N/A] 台账不存在"); return 1
    n = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        if "init" in o:
            print(f"[init] {o.get('note', '')[:70]}")
        else:
            print(f"{o.get('date')} 池{len(o.get('pool', []))} A{len(o.get('original', []))} "
                  f"D{len(o.get('enhanced', []))} DL={o.get('dl_cover')}")
    if n == 0:
        print("[N/A] 台账空（每调仓月 append；首评 2026-12-31）")
    return 0


def cmd_eval(args) -> int:
    rows = []
    ledger = Path(getattr(args, "file", str(LEDGER)))
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "init" not in o:
                rows.append(o)
    if not rows:
        print(json.dumps({"status": "insufficient_data",
                          "reason": "无登记行（每调仓月 append；首评 2026-12-31）"}, ensure_ascii=False))
        return 1
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    px = pd.read_sql_query("SELECT code,date,open FROM stock_daily_meta_qfq WHERE date>='20260101'", con)
    con.close()
    px["date"] = px["date"].astype(int)
    out = {"status": "ok", "n_days": len(rows), "fwd": {},
           "note": "无费用观察口径; A=生产Top20, D=池内DL重排Top20; fwd_N=open[D+1+N]/open[D+1]-1 次开等权"}
    for N in args.fwd:
        a_rets, d_rets, monthly = [], [], []
        skipped = 0
        for row in rows:
            if not _valid_date(row["date"]):
                skipped += 1
                continue
            d_int = int(row["date"].replace("-", ""))
            after = px[px["date"] > d_int]
            days = sorted(after["date"].unique())
            if len(days) < 1 + N:
                skipped += 1
                continue
            entry, exit_ = days[0], days[N]
            o_in = px[px["date"] == entry].set_index("code")["open"]
            o_out = px[px["date"] == exit_].set_index("code")["open"]
            ma = [o_out[c] / o_in[c] - 1.0 for c in row["original"][:20] if c in o_in.index and c in o_out.index]
            md_ = [o_out[c] / o_in[c] - 1.0 for c in row["enhanced"][:20] if c in o_in.index and c in o_out.index]
            a_rets += ma
            d_rets += md_
            if ma and md_:
                monthly.append({"date": row["date"], "A": float(np.mean(ma)), "D": float(np.mean(md_))})
        def pk(r):
            return {"n": len(r), "mean": round(float(np.mean(r)), 6) if r else None,
                    "hit_pct": round(float(np.mean([x > 0 for x in r])) * 100, 1) if r else None}
        out["fwd"][f"N{N}"] = {"skipped_days": skipped, "A_生产序": pk(a_rets), "D_DL重排": pk(d_rets)}
        if a_rets and d_rets:
            out["fwd"][f"N{N}"]["diff_D-A"] = round(float(np.mean(d_rets) - np.mean(a_rets)), 6)
        out["monthly"] = monthly
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DL tilt 接线前向 A/B 台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("append")
    p1.add_argument("--pool", required=True)
    p1.add_argument("--top20", required=True)
    p1.add_argument("--date", default="")
    p1.add_argument("--pick-date", default="", help="DL 截面日 YYYYMMDD（默认=登记日）")
    p1.add_argument("--note", default="")
    p1.add_argument("--force", action="store_true")
    p1.add_argument("--file", default=str(LEDGER), help="测试隔离: 指向副本")
    p2 = sub.add_parser("show")
    p2.add_argument("--file", default=str(LEDGER))
    p3 = sub.add_parser("eval")
    p3.add_argument("--fwd", type=int, nargs="+", default=[20, 10])
    p3.add_argument("--file", default=str(LEDGER))
    a = ap.parse_args()
    return {"append": cmd_append, "show": cmd_show, "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
