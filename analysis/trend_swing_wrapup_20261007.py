# -*- coding: utf-8 -*-
"""趋势波段收尾三合一研究 — 2026-10-07 研究侧零接线
A: 温斯坦阶段2纯状态层A股抽样检验(个股趋势状态的条件期望,无事件条件)
B: PD-034恐慌事件→FTD确认时间差分布(配对语义深化)
C: FTD 29事件 ERP/情绪相位分层摸底(prereg分层字段预演)
判据跑前冻结。数据: dragon_quant_qfq.db(抽样) + vipdoc sh000001 + erp_history + phase台账。
"""
import json
import random
import sqlite3
import struct
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
ERP_DB = "file:D:/zcodesj/erp_history.db?mode=ro"
PHASE_LEDGER = Path(r"D:\zcodesj\data\emotion_phase_v2_ledger.jsonl")
FTD_JSON = Path(r"D:\zcodesj\tmp\ftd_followthrough_result_20261007.json")
OUT = Path(r"D:\zcodesj\tmp\trend_swing_wrapup_result_20261007.json")

PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
SAMPLE_N = 800
SEED = 20261007
START = "20100101"
MAX_PRICE = 30.0
FWD_HORIZONS = [5, 10, 20]


def med_win(vals):
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(vals), "med": round(s[len(s) // 2], 2),
            "win": round(sum(1 for v in vals if v > 0) * 100.0 / len(vals), 1)}


# ---------- A: 阶段2状态层 ----------
def part_a():
    con = sqlite3.connect(QFQ_DB, uri=True, timeout=60)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE substr(code,1,3) IN (%s)"
        % ",".join("'%s'" % p for p in PREFIX))]
    random.seed(SEED)
    sample = random.sample(codes, min(SAMPLE_N, len(codes)))
    print(f"[A] universe={len(codes)} sample={len(sample)} seed={SEED}")

    ph = {}
    for i, c in enumerate(sample):
        rows = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND close IS NOT NULL ORDER BY date",
            (c, "20080101")).fetchall()
        if len(rows) < 220:
            continue
        # 剔次新: 上市(首行)后60日内不计
        first_d = rows[0][0]
        closes = [r[1] for r in rows]
        dates = [r[0] for r in rows]
        # MA150 与其5日前
        ma = []
        s = 0.0
        for i2, v in enumerate(closes):
            s += v
            if i2 >= 150:
                s -= closes[i2 - 150]
                ma.append(s / 150.0)
            else:
                ma.append(None)
        for i2 in range(155, len(closes) - 20):
            d = dates[i2]
            if d < START or d <= first_d or (int(d) - int(first_d)) < 60 * 100:
                pass  # 60自然日近似: 首行日期差
            m_now, m_5 = ma[i2], ma[i2 - 5]
            if m_now is None or m_5 is None or closes[i2] > MAX_PRICE:
                continue
            fw = {}
            ok = True
            for h in FWD_HORIZONS:
                j = i2 + h
                if j >= len(closes):
                    ok = False
                    break
                fw[f"fwd{h}"] = (closes[j] / closes[i2] - 1) * 100.0
            if not ok:
                continue
            above = closes[i2] > m_now
            rising = m_now > m_5
            if above and rising:
                grp = "S2_rising"
            elif above:
                grp = "above_flat"
            else:
                grp = "below"
            rec = {"code": c, "date": d, **fw, "grp": grp}
            ph.setdefault(grp, []).append(rec)
    con.close()

    # 日聚簇: 每组按日期聚合日均值再做描述(条件日均值序列)
    out_a = {"sample": len(sample), "seed": SEED}
    for grp in ("S2_rising", "above_flat", "below"):
        recs = ph.get(grp, [])
        base = {}
        for h in FWD_HORIZONS:
            vals = [r[f"fwd{h}"] for r in recs]
            base[f"fwd{h}"] = med_win(vals)
        # 日聚簇中位: 按日聚合组内均值
        byday = {}
        for r in recs:
            byday.setdefault(r["date"], []).append(r["fwd20"])
        daymeans = [sum(v) / len(v) for v in byday.values()]
        base["fwd20_daycluster"] = med_win(daymeans)
        out_a[grp] = base
        print(f"[A] {grp}: n={base['fwd20']['n']} fwd20 med={base['fwd20']['med']} "
              f"win={base['fwd20']['win']} daycluster med={base['fwd20_daycluster']['med']}")
    return out_a


# ---------- 共用: 上证与FTD事件 ----------
def read_vipdoc_closes():
    rows = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append((str(d), c / 100.0))
    return rows


def find_ftd_indices(rows):
    closes = [r[1] for r in rows]
    vols = None
    n = len(rows)
    rets = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    rolling_high = []
    for i in range(n):
        lo_i = max(0, i - 59)
        rolling_high.append(max(closes[lo_i:i + 1]))
    dd = [(closes[i] / rolling_high[i] - 1) * 100.0 for i in range(n)]
    # 重读vol
    vols = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            _d, _o, _h, _l, _c, _amt, vol, _r2 = struct.unpack("<IIIIIfII", b)
            vols.append(vol)
    events = []
    in_dd, day1_i, last = False, None, -999
    i = 0
    while i < n:
        if dd[i] <= -8.0:
            in_dd, day1_i = True, None
        elif in_dd and day1_i is None and rets[i] > 0:
            day1_i = i
        if in_dd and day1_i is not None and 4 <= i - day1_i <= 10:
            if rets[i] >= 0.02 and vols[i] > vols[i - 1] and i - last > 20:
                events.append(i)
                last = i
                in_dd, day1_i = False, None
        i += 1
    return events, dates_of(rows), dd


def dates_of(rows):
    return [r[0] for r in rows]


def part_b(rows_dates, ftd_idx_set):
    """恐慌事件→FTD时间差。"""
    led = Path(r"D:\zcodesj\data\panic_overshoot_shadow.jsonl")
    panic = []
    for line in open(led, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                panic.append(str(json.loads(line)["event_date"]))
            except Exception:
                pass
    d2i = {d: i for i, d in enumerate(rows_dates)}
    gaps, no_ftd = [], []
    for p in panic:
        i0 = d2i.get(p)
        if i0 is None:
            continue
        nxt = [i for i in ftd_idx_set if i0 < i <= i0 + 60]
        if nxt:
            gaps.append(min(nxt) - i0)
        else:
            no_ftd.append(p)
    gaps_sorted = sorted(gaps)
    out_b = {"panic_events": len(panic), "with_ftd_within60": len(gaps),
             "no_ftd_within60": no_ftd,
             "gap_med": gaps_sorted[len(gaps_sorted) // 2] if gaps else None,
             "gaps": gaps}
    print(f"[B] panic={len(panic)} withFTD60={len(gaps)} med_gap={out_b['gap_med']} noFTD={len(no_ftd)}")
    return out_b


def part_c(rows_dates, ftd_idx_set):
    """FTD事件 ERP/相位分层摸底(描述性)。"""
    d2i = {d: i for i, d in enumerate(rows_dates)}
    ev_dates = [rows_dates[i] for i in ftd_idx_set]
    erp = {}
    con = sqlite3.connect(ERP_DB, uri=True, timeout=30)
    for d, p in con.execute("SELECT date, erp_pct_5y FROM erp_daily").fetchall():
        if p is not None:
            erp[str(d)] = p
    con.close()
    phase = {}
    if PHASE_LEDGER.exists():
        for line in open(PHASE_LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    phase[r["phase_date"]] = r["phase"]
                except Exception:
                    pass
    matched = []
    for d in ev_dates:
        matched.append({"date": d, "erp": erp.get(d), "phase": phase.get(d, "n/a")})
    erp_vals = [m["erp"] for m in matched if m["erp"] is not None]
    from collections import Counter
    ph_cnt = Counter(m["phase"] for m in matched)
    out_c = {"n": len(matched), "erp_matched": len(erp_vals),
             "erp_med": round(sorted(erp_vals)[len(erp_vals) // 2], 1) if erp_vals else None,
             "erp_ge60_ratio": round(sum(1 for v in erp_vals if v >= 60) * 100.0 / len(erp_vals), 1) if erp_vals else None,
             "phase_counter": dict(ph_cnt), "events": matched}
    print(f"[C] n={len(matched)} erp_med={out_c['erp_med']} erp>=60={out_c['erp_ge60_ratio']}% phases={dict(ph_cnt)}")
    return out_c


def main():
    t0 = time.time()
    a = part_a()
    rows = read_vipdoc_closes()
    ftd_idx, rows_dates, _dd = find_ftd_indices(rows)
    ftd_set = set(ftd_idx)
    b = part_b(rows_dates, ftd_set)
    c = part_c(rows_dates, ftd_set)
    result = {"meta": {"run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "script": "analysis/trend_swing_wrapup_20261007.py"},
              "A_stage2_state": a, "B_panic_to_ftd_gap": b, "C_ftd_layers": c}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved -> {OUT} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
