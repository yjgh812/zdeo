# -*- coding: utf-8 -*-
"""F2B_RALLY_CONFIRM 主升3日新高确认窗影子日更(等权指数层, 观测层零交易权)
对应 PREREG_F2B_RALLY_CONFIRM_SHADOW_20261009, 主人2026-10-09「升级」令激活影子层。
口径与验证轮 analysis/r46_f2_g2_phase_calendar_study_20261009.py 逐位一致:
等权指数(60/00/30开头)创60日收盘新高且距前60日低点收盘反弹>=10% = 事件(主升候选态),
后 confirm_win 个交易日收盘再创新高 = 确认态; fwd20 从事件日收盘起算。
参数单一真源=data/PREREG_F2B_RALLY_CONFIRM_SHADOW_20261009.json params 字段
(缺字段即fail-closed退出, 禁止回退硬编码)。
幂等: 全史重算+台账原子覆盖, 重跑安全; 台账含 in_sample 参考层与 OOS 影子层。
边界: 纯指数层状态描述影子, 零交易权, 禁止接闸门/仓位/扳机(PD-034 铁律)。

用法: python analysis/r46_f2b_rally_shadow_daily.py [--report]
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_F2B_RALLY_CONFIRM_SHADOW_20261009.json")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 2026-10-13 切wb唯一真源(zc停更致asof恒10/09, SOP一百三十二; prereg JSON仅参数真源, db路径在本常量)
LEDGER = Path(r"D:\zcodesj\data\f2b_rally_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\f2b_rally_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\f2b_rally_shadow_brief_latest.txt")


def fail(msg: str):
    print(f"[F2B-SHADOW][FATAL] {msg}")
    sys.exit(2)


def load_params() -> dict:
    if not PREREG.exists():
        fail(f"prereg 缺失 {PREREG}")
    d = json.loads(PREREG.read_text(encoding="utf-8"))
    if d.get("status") != "ACTIVE":
        fail(f"prereg status={d.get('status')} 非 ACTIVE, fail-closed")
    p = d.get("params") or {}
    for k in ("W", "rebound_th", "confirm_win", "fwd", "activation_since"):
        if k not in p:
            fail(f"prereg params 缺 {k}, fail-closed")
    return p


def main() -> None:
    p = load_params()
    W, rb, cw, fwd = int(p["W"]), float(p["rebound_th"]), int(p["confirm_win"]), int(p["fwd"])
    since = str(p["activation_since"])

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    # 2026-10-19 a1提速(夜链瓶颈审计呈请, 「按推荐继续」批准): 等权聚合逐日期缓存
    # (data/f2b_rally_ew_agg_cache.json, 原子覆盖), 夜间仅对 cache 尾日期之后做
    # idx_qfq_date 区间扫(秒级), 缓存缺失/损坏→全量重算(=原行为, 口径逐位不变)。
    # 产出与全量重算逐位一致(等权聚合按日期确定性), 双跑 md5 对拍为验收。
    CACHE = Path(r"D:\zcodesj\data\f2b_rally_ew_agg_cache.json")
    dates, closes, avg_open_all, avg_chg_all = [], [], [], []

    def _run_agg(after: str | None):
        sql = ("SELECT date, AVG(open), AVG(close), AVG(change_pct) "
               "FROM stock_daily_meta_qfq "
               "WHERE date >= '20160101' AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')")
        args = []
        if after:
            sql += " AND date > ?"
            args.append(after)
        sql += " GROUP BY date ORDER BY date"
        out = []
        for d, o, c, p2 in cur.execute(sql, args):
            out.append((str(d), o, c, p2))
        return out

    cache_ok = False
    if CACHE.exists():
        try:
            ca = json.loads(CACHE.read_text(encoding="utf-8"))
            if (ca.get("v") == 1 and isinstance(ca.get("dates"), list)
                    and len(ca.get("dates", [])) > 0
                    and len(ca["dates"]) == len(ca.get("avg_close", []))):
                dates = list(ca["dates"])
                avg_open_all = list(ca["avg_open"])
                closes = list(ca["avg_close"])
                avg_chg_all = list(ca["avg_chg"])
                cache_ok = True
        except Exception:
            cache_ok = False
    after = dates[-1] if cache_ok else None
    new_rows = _run_agg(after)
    if cache_ok and not new_rows:
        pass  # 无新日期, 直接用缓存
    else:
        if not cache_ok and len(new_rows) < 100:
            fail(f"等权序列过短 {len(new_rows)}")
        for d, o, c, p2 in new_rows:
            dates.append(d)
            avg_open_all.append(o)
            closes.append(c)
            avg_chg_all.append(p2)
        tmpc = CACHE.with_suffix(".json.tmp")
        tmpc.write_text(json.dumps({"v": 1, "dates": dates, "avg_open": avg_open_all,
                                    "avg_close": closes, "avg_chg": avg_chg_all},
                                   ensure_ascii=False), encoding="utf-8")
        os.replace(tmpc, CACHE)
    con.close()
    if len(dates) < W + fwd + 5:
        fail(f"等权序列过短 {len(dates)}")

    n = len(dates)
    events = []  # dict per event
    active = None  # (event_idx, deadline_idx, event_close)
    phase_now = "normal"
    for i in range(W, n):
        win = closes[i - W:i]
        hi, lo = max(win), min(win)
        is_event = closes[i] > max(win) and (closes[i] - lo) / lo >= rb
        # 事件窗口推进
        if active is not None:
            e_idx, dl, e_close = active
            if closes[i] > e_close and events[-1]["confirm_date"] is None:
                events[-1]["confirm_date"] = dates[i]
                events[-1]["hit"] = 1
                phase_now = "confirmed"
            if i >= dl:
                if events[-1]["confirm_date"] is None:
                    events[-1]["hit"] = 0
                active = None
            else:
                phase_now = f"candidate_D{dl - i}"
        if is_event:
            ev = {
                "event_date": dates[i],
                "confirm_date": None,
                "hit": None,
                "fwd20": round(closes[i + fwd] / closes[i] - 1, 6) if i + fwd < n else None,
                "layer": "oos" if dates[i] >= since else "in_sample",
            }
            events.append(ev)
            active = (i, i + cw, closes[i])
            phase_now = "candidate_D1" if active[1] > i else phase_now
        elif active is None:
            phase_now = "normal"

    oos = [e for e in events if e["layer"] == "oos"]
    oos_done = [e for e in oos if e["hit"] is not None]
    oos_conf = [e for e in oos if e["hit"] == 1 and e["fwd20"] is not None]
    ins_hit = [e["hit"] for e in events if e["layer"] == "in_sample" and e["hit"] is not None]

    snap = {
        "asof": dates[-1],
        "phase": phase_now,
        "activation_since": since,
        "events_total": len(events),
        "in_sample": {"n": len(ins_hit), "hit": round(sum(ins_hit) / len(ins_hit), 4) if ins_hit else None},
        "oos": {"n_events": len(oos), "n_resolved": len(oos_done),
                "n_confirmed": len(oos_conf),
                "fwd20_med": round(statistics.median([e["fwd20"] for e in oos_conf]) * 100, 3) if oos_conf else None,
                "hit_ratio": round(len(oos_conf) / len(oos_done), 4) if oos_done else None},
        "acceptance": "60交易日或OOS n>=20先到; pass=OOS fwd20 med>0且hit>=55%; kill=OOS前10个确认态fwd20中位<0",
    }
    LEDGER.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + ("\n" if events else ""),
        encoding="utf-8",
    )
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        f"F2B主升确认窗影子 {snap['asof']} phase={phase_now}",
        f"事件总数={len(events)} (in_sample {len(ins_hit)} hit={snap['in_sample']['hit']} / OOS {len(oos)} 已决{len(oos_done)} 确认{len(oos_conf)})",
        f"OOS fwd20 med={snap['oos']['fwd20_med']}% hit={snap['oos']['hit_ratio']}",
        f"验收门: {snap['acceptance']}",
        "边界: 指数层状态描述, 零交易权禁扳机(PD-034)",
    ]
    BRIEF.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if "--report" in sys.argv:
        print("\n".join(lines))
    print(f"[F2B-SHADOW] OK events={len(events)} oos={len(oos)} phase={phase_now} asof={snap['asof']}")


if __name__ == "__main__":
    main()
