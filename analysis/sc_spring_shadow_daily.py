# -*- coding: utf-8 -*-
"""SC_SPRING_DEEP_LOUD 深位放量破位收回弹簧影子日更(候选甲, 观测层零交易权)
对应 PREREG_SC_SPRING_DEEP_LOUD_20261007, 主人2026-10-07「按推荐继续」批准激活影子层。
事件定义与收益口径与样内验证 tmp/wyckoff_local_validation_20261007.py /
tmp/wyckoff_spring_deep_20261007.py 逐位一致;
参数单一真源=data/PREREG_SC_SPRING_DEEP_LOUD_20261007.json params 字段
(缺字段即fail-closed退出, 禁止回退硬编码)。
数据源=dragon_quant_qfq.db stock_daily_meta_qfq(唯一回测价格源)。
幂等: (code,arm,event_date)主键全史重算+台账原子覆盖, 重跑安全;
台账含样内参考层(in_sample=true, entry<since)与OOS影子层。
边界: 纯观测影子层零交易权, 禁止接交易闸门/交易cron/executor;
OOS验收门(LOCKED)=日历2026-12-31或OOS到期n>=20先到为准,
pass=OOS med20>=+3.0%且win20>=55%; kill_switch=OOS前10个到期事件med20全<0提请废案。

用法:
  python analysis/sc_spring_shadow_daily.py            # 全史重算+台账原子覆盖+快照
  python analysis/sc_spring_shadow_daily.py --report   # 同上并打印简报
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

PREREG = Path(r"D:\zcodesj\data\PREREG_SC_SPRING_DEEP_LOUD_20261007.json")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
LEDGER = Path(r"D:\zcodesj\data\sc_spring_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\sc_spring_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\sc_spring_shadow_brief_latest.txt")
BLOCKS = ["000_", "001_", "002_", "003_", "300_", "301_", "600_", "601_", "603_", "605_"]


def load_prereg() -> dict:
    """参数单一真源: prereg params 字段; 缺字段fail-closed。"""
    p = json.loads(PREREG.read_text(encoding="utf-8"))
    if p.get("status") != "ACTIVE":
        raise SystemExit(f"[FAIL-CLOSED] prereg status={p.get('status')} 非ACTIVE")
    pa = p.get("params") or {}
    need = ["lookback_low", "vol_mult_break", "dep_min_confirm", "recover_days",
            "dedup_days", "holding_horizons", "entry", "stop_sim_pct",
            "universe_exclude_prefix"]
    for k in need:
        if k not in pa:
            raise SystemExit(f"[FAIL-CLOSED] prereg params 缺字段 {k}")
    if pa["entry"] != "next_open":
        raise SystemExit("[FAIL-CLOSED] prereg entry 非next_open")
    since = int(json.loads(p["approval"].replace("'", '"'))["activation_since"])
    acc = json.loads(p["acceptance_locked"].replace("'", '"'))
    return {"pa": pa, "since": since, "acc": acc,
            "name": p.get("name"), "status": p.get("status")}


def limit_skip(code: str, date: int, o: float, prev_c: float, gem20: bool) -> bool:
    """涨停开盘不可成交剔除(与样内验证同口径)。"""
    if code.startswith(("300", "301")):
        lim = 0.195 if date >= 20200824 else 0.095
    else:
        lim = 0.095
    return prev_c > 0 and o / prev_c - 1 >= lim


def scan_stock(code: str, df: pd.DataFrame, cfg: dict):
    """返回 (main_recs, control_recs)。口径与样内验证逐位一致。"""
    pa = cfg["pa"]
    LB = int(pa["lookback_low"]); VM = float(pa["vol_mult_break"])
    DEP = float(pa["dep_min_confirm"]); RD = int(pa["recover_days"])
    DD = int(pa["dedup_days"])
    excl = tuple(pa["universe_exclude_prefix"])
    if code.startswith(excl) or len(df) < 140:
        return [], []
    o = df["open"].values; h = df["high"].values; l = df["low"].values
    c = df["close"].values; v = df["volume"].values.astype(float)
    d = df["date"].values.astype(int)
    n = len(df)
    ma_v = pd.Series(v).rolling(LB).mean().shift(1).values
    L20 = pd.Series(l).rolling(LB).min().shift(1).values
    H20 = pd.Series(h).rolling(LB).max().shift(1).values
    with np.errstate(invalid="ignore", divide="ignore"):
        vr = v / ma_v
    main, control = [], []
    closed = np.where((~np.isnan(L20)) & (c < L20))[0]
    for i in closed:
        if not np.isfinite(vr[i]) or vr[i] < VM:
            continue
        hit = None
        for j in range(i + 1, min(i + RD + 1, n)):
            if c[j] >= L20[i]:
                hit = j
                break
        # 口径对齐样内验证(tmp/wyckoff_spring_deep): 收集不做e>=n与涨停剔除(占去重槽位),
        # 去重键=事件日数组下标(样内按idx去重, 跨假期日历差≠下标差)
        if hit is not None:
            dep = (H20[hit] - c[hit]) / H20[hit] if np.isfinite(H20[hit]) else 0.0
            if dep <= DEP:
                continue
            main.append({
                "code": code, "arm": "main", "event_date": int(d[hit]),
                "break_date": int(d[i]), "hit_idx": int(hit), "entry_idx": int(hit + 1),
                "dep": round(float(dep), 4),
                "break_vol_ratio": round(float(vr[i]), 3),
                "rev_vol_ratio": round(float(vr[hit]), 3),
                "limitdn_break": bool((c[i] / c[i - 1] - 1) <= -0.095) if c[i - 1] > 0 else False,
            })
        else:
            control.append({
                "code": code, "arm": "real_breakdown", "event_date": int(d[i]),
                "hit_idx": int(i), "entry_idx": int(i + 1),
            })
    # 同票去重(每臂独立, 键=事件日数组下标, 与样内验证逐位一致)
    def dedup(rows, key="hit_idx"):
        rows = sorted(rows, key=lambda r: r[key])
        out, last = [], -99
        for r in rows:
            if r[key] - last >= DD:
                out.append(r); last = r[key]
        return out
    md, cd = dedup(main), dedup(control)
    # 去重之后: e>=n/涨停开盘剔除 + 收益结算(样内mk_rec顺序); 最短窗有值即入样,
    # 未到期窗存null(OFF存OPEN态, 全史重算自然推进), stats按非None子集计算
    HZ = [int(x) for x in pa["holding_horizons"]]
    STOP = float(pa["stop_sim_pct"])
    main_out = []
    for r in md:
        e = r.pop("entry_idx"); r.pop("hit_idx", None)
        if e >= n or limit_skip(code, int(d[e]), o[e], c[e - 1], code.startswith(("300", "301"))):
            continue
        entry = o[e]
        fwd = {k: (float(c[e + k] / entry - 1) if e + k < n else None) for k in HZ}
        if all(v is None for v in fwd.values()):
            continue
        stop_hit = False
        for jj in range(e, min(e + 61, n)):  # 60日窗对齐样内验证stop口径
            if l[jj] <= entry * (1 + STOP):
                stop_hit = True
                break
        r.update({"entry_date": int(d[e]), "entry_px": round(float(entry), 4),
                  "fwd": {str(k): fwd[k] for k in HZ}, "stop_hit": stop_hit,
                  "settled": fwd[20] is not None})  # 主验收窗f20到期即settled; f5仅为入样锚定
        main_out.append(r)
    ctrl_out = []
    for r in cd:
        e = r.pop("entry_idx")
        if e >= n or limit_skip(code, int(d[e]), o[e], c[e - 1], code.startswith(("300", "301"))):
            continue
        f20 = float(c[e + 20] / o[e] - 1) if e + 20 < n else None
        r.update({"entry_date": int(d[e]), "f20": f20, "settled": f20 is not None})
        ctrl_out.append(r)
    return main_out, ctrl_out


def stats(rows: list, horizon=20):
    f = [r["fwd"][str(horizon)] for r in rows if r.get("fwd", {}).get(str(horizon)) is not None]
    if not f:
        return {"n_settled": 0}
    a = np.array(f) * 100.0
    return {"n_settled": len(a), f"med{horizon}": round(float(np.median(a)), 2),
            f"win{horizon}": round(float((a > 0).mean()) * 100, 1)}


def main(report: bool):
    t0 = time.time()
    cfg = load_prereg()
    since = cfg["since"]
    main_rows, control_rows = [], []
    con = sqlite3.connect(DB)
    for pref in BLOCKS:
        q = ("SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
             "WHERE code LIKE ? ORDER BY code,date")
        df = pd.read_sql(q, con, params=(pref + "%",))
        if df.empty:
            continue
        df["date"] = df["date"].astype(int)
        for code, g in df.groupby("code"):
            m, c = scan_stock(code, g.reset_index(drop=True), cfg)
            main_rows.extend(m); control_rows.extend(c)
    con.close()
    for r in main_rows:
        r["in_sample"] = r["entry_date"] < since
    for r in control_rows:
        r["in_sample"] = r["entry_date"] < since
    # 台账原子覆盖(main全字段 + control精简)
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        for r in main_rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in control_rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(LEDGER)
    # 统计快照
    oos_main = [r for r in main_rows if not r["in_sample"]]
    oos_settled = [r for r in oos_main if r["settled"]]
    s_all = stats(main_rows); s_oos = stats(oos_main)
    oos_loud = [r for r in oos_main if r.get("rev_vol_ratio", 0) > 1.0]
    oos_dn = [r for r in oos_main if r.get("limitdn_break")]
    ctrl_oos = [r for r in control_rows if not r["in_sample"] and r["settled"]]
    ctrl_f = [r["f20"] * 100 for r in ctrl_oos]
    acc = cfg["acc"]
    gate = {"window": acc["window"], "pass_rule": acc["pass"],
            "kill_switch": acc["kill_switch"], "oos_n_settled": s_oos.get("n_settled", 0)}
    if s_oos.get("n_settled", 0) >= 10 and s_oos.get("n_settled", 0) < 20:
        early = [r["fwd"]["20"] * 100 for r in oos_settled[:10]]
        gate["kill_check"] = {"first10_med20": round(float(np.median(early)), 2),
                              "kill_triggered": bool(np.median(early) < 0)}
    if s_oos.get("n_settled", 0) >= 20 or len(oos_settled) >= 20:
        gate["verdict_now"] = ("PASS" if s_oos.get("med20", -99) >= 3.0 and s_oos.get("win20", 0) >= 55
                               else "BELOW_GATE(未达验收线,继续观察)")
    snap = {"name": cfg["name"], "status": cfg["status"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "since": since, "total_events": len(main_rows),
            "in_sample": {"n": sum(1 for r in main_rows if r["in_sample"]), **s_all},
            "oos": {"n_events": len(oos_main), **s_oos,
                    "tier_revloud": stats(oos_loud), "tier_limitdn": stats(oos_dn)},
            "control_real_breakdown_oos": ({"n": len(ctrl_f), "med20": round(float(np.median(ctrl_f)), 2)} if ctrl_f else {"n": 0}),
            "gate": gate, "elapsed_s": round(time.time() - t0, 1)}
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"[sc_spring] events={len(main_rows)}(oos={len(oos_main)}) "
             f"oos_settled={s_oos.get('n_settled', 0)} "
             f"oos_med20={s_oos.get('med20', 'NA')} oos_win20={s_oos.get('win20', 'NA')} "
             f"gate={gate.get('verdict_now', 'WAITING')} {round(time.time()-t0, 0)}s"]
    BRIEF.write_text("\n".join(lines), encoding="utf-8")
    if report:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    print(lines[0])


if __name__ == "__main__":
    main("--report" in sys.argv)
