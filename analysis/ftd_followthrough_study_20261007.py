# -*- coding: utf-8 -*-
"""FTD(Follow-Through Day 跟进日)A股版全史事件研究 — 2026-10-07 研究侧零接线
判据跑前冻结(LOCKED), 数据=tdx vipdoc sh000001.day 全史(1990-2026)。

判据(欧奈尔口径A股适配):
  前提: 指数距最近60日滚动收盘高点回撤 >= TH_DRAWDOWN(基准8%,敏感性6/12)
  Day1 : 回撤条件满足后的首个收涨日
  窗口 : Day1 后第 4~10 个交易日(欧奈尔"反弹第4天起")
  FTD  : 窗口内某日 close/close_prev >= 1+TH_GAIN(基准2%,敏感性1.5/2.5) 且 vol > vol_prev
  去重 : 每次反弹尝试仅取首个FTD; 上一FTD后20个交易日内不记新FTD
对照臂: 同窗涨幅达标但无量增(剥量对照) — 验证量能判据增量
前瞻  : 指数 fwd5/10/20/60 (close-to-close); 事件后20日最大回撤
基线  : 全期无条件 fwd5/10/20/60 分布
G门(冻结): n>=20; fwd20中位-基线中位>=+1pp; win(fwd20>0)>=55%; 分年正>=6/10
差异论证(prereg_preflight CONFLICT应答):
  BREAKOUT-SCOUT-TRADEABLE(二波确认日)为个股级追入族; 本研究=指数级市场择时确认层, 不涉个股追入;
  TIMING-PANIC-STAGE1裸条件双FAIL vs PD-034合取版PASS=先例: FTD判据为多条件合取(回撤前提x日4起x涨幅x量增);
  PD-031/PORTRAIT_GATES"右侧确认"仅描述性用词非同族信号。
"""
import json
import struct
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
OUT = Path(r"D:\zcodesj\tmp\ftd_followthrough_result_20261007.json")

TH_DRAWDOWN = 8.0
TH_GAIN = 2.0
SENS_DRAWDOWN = [6.0, 8.0, 12.0]
SENS_GAIN = [1.5, 2.0, 2.5]
WINDOW_START, WINDOW_END = 4, 10
COOLDOWN = 20
FWD_HORIZONS = [5, 10, 20, 60]


def read_vipdoc_full():
    rows = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append({"date": str(d), "open": o / 100.0, "high": h / 100.0,
                         "low": l / 100.0, "close": c / 100.0,
                         "amount": amt, "vol": vol})
    return rows


def find_ftds(rows, th_dd, th_gain):
    """扫描状态机: 返回FTD事件列表(date, fwd各期, mdd20)与剥量对照事件。"""
    closes = [r["close"] for r in rows]
    vols = [r["vol"] for r in rows]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    n = len(rows)
    rolling_high = []
    hi = -1.0
    for i in range(n):
        lo_i = max(0, i - 59)
        rolling_high.append(max(closes[lo_i:i + 1]))
    dd = [(closes[i] / rolling_high[i] - 1) * 100.0 for i in range(n)]

    events, events_novol = [], []
    in_drawdown = False
    day1_idx = None
    last_ftd_i = -999
    i = 0
    while i < n:
        if dd[i] <= -th_dd:
            in_drawdown = True
            day1_idx = None
        elif in_drawdown and day1_idx is None and rets[i] > 0:
            day1_idx = i
        if in_drawdown and day1_idx is not None:
            offset = i - day1_idx
            if WINDOW_START <= offset <= WINDOW_END:
                if rets[i] >= th_gain / 100.0:
                    if vols[i] > vols[i - 1] and i - last_ftd_i > COOLDOWN:
                        events.append(i)
                        last_ftd_i = i
                        in_drawdown = False
                        day1_idx = None
                    elif vols[i] <= vols[i - 1] and (i - last_ftd_i) > COOLDOWN:
                        events_novol.append(i)
        i += 1

    def enrich(idxs):
        out = []
        for i in idxs:
            r = {"date": rows[i]["date"]}
            for h in FWD_HORIZONS:
                j = i + h
                r[f"fwd{h}"] = round((closes[j] / closes[i] - 1) * 100.0, 2) if j < n else None
            j20 = min(i + 20, n - 1)
            r["mdd20"] = round((min(closes[i:j20 + 1]) / closes[i] - 1) * 100.0, 2)
            out.append(r)
        return out

    return enrich(events), enrich(events_novol), dd


def stats(events, key):
    vals = [e[key] for e in events if e.get(key) is not None]
    if not vals:
        return {"n": 0}
    vals_sorted = sorted(vals)
    med = vals_sorted[len(vals_sorted) // 2]
    win = sum(1 for v in vals if v > 0) * 100.0 / len(vals)
    mean = sum(vals) / len(vals)
    return {"n": len(vals), "med": round(med, 2), "mean": round(mean, 2),
            "win": round(win, 1), "min": round(min(vals), 2), "max": round(max(vals), 2)}


def yearly(events, key="fwd20"):
    by = {}
    for e in events:
        v = e.get(key)
        if v is not None:
            by.setdefault(e["date"][:4], []).append(v)
    out = {}
    for y in sorted(by):
        vs = by[y]
        out[y] = {"n": len(vs), "med": round(sorted(vs)[len(vs) // 2], 2),
                  "win": round(sum(1 for v in vs if v > 0) * 100.0 / len(vs), 1)}
    return out


def baseline(rows):
    closes = [r["close"] for r in rows]
    n = len(closes)
    out = {}
    for h in FWD_HORIZONS:
        vals = [(closes[i + h] / closes[i] - 1) * 100.0 for i in range(0, n - h)]
        vals.sort()
        out[f"fwd{h}"] = {"n": len(vals), "med": round(vals[len(vals) // 2], 2),
                          "mean": round(sum(vals) / len(vals), 2),
                          "win": round(sum(1 for v in vals if v > 0) * 100.0 / len(vals), 1)}
    return out


def overlap_pd034(events):
    """与PD-034恐慌事件(超跌出击)日期重叠检查。"""
    led = Path(r"D:\zcodesj\data\panic_overshoot_shadow.jsonl")
    if not led.exists():
        return {"available": False}
    pd034 = set()
    for line in open(led, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                pd034.add(str(json.loads(line)["event_date"]))
            except Exception:
                pass
    ftd_dates = [e["date"] for e in events]
    overlap = [d for d in ftd_dates if d in pd034]
    return {"available": True, "pd034_events": len(pd034), "ftd_events": len(ftd_dates),
            "overlap": overlap, "overlap_ratio": round(len(overlap) * 100.0 / max(1, len(ftd_dates)), 1)}


def regime_split(events):
    """制度切分: 19961216涨跌停制前/后, 及2015后现代段(沿跌停潮研究先例)。"""
    out = {}
    for name, lo, hi in [("pre_19961216", "19900101", "19961215"),
                         ("post_19961216", "19961216", "20991231"),
                         ("post_2015", "20150101", "20991231")]:
        sub = [e for e in events if lo <= e["date"] <= hi]
        s20 = stats(sub, "fwd20")
        s10 = stats(sub, "fwd10")
        out[name] = {"fwd10": s10, "fwd20": s20}
    return out


def main():
    t0 = time.time()
    rows = read_vipdoc_full()
    print(f"sh000001 rows: {len(rows)}  {rows[0]['date']} ~ {rows[-1]['date']}")
    base = baseline(rows)

    # 敏感性网格(中位/胜率/n)
    grid = []
    for th_dd in SENS_DRAWDOWN:
        for th_g in SENS_GAIN:
            ev, _, _ = find_ftds(rows, th_dd, th_g)
            s = stats(ev, "fwd20")
            grid.append({"drawdown": th_dd, "gain": th_g, **s})
            print(f"  dd={th_dd}% gain={th_g}% -> n={s.get('n')} med={s.get('med')} win={s.get('win')}")

    ev_main, ev_novol, _ = find_ftds(rows, TH_DRAWDOWN, TH_GAIN)
    s_main = {f"fwd{h}": stats(ev_main, f"fwd{h}") for h in FWD_HORIZONS}
    s_main["mdd20"] = stats(ev_main, "mdd20")
    s_novol = {f"fwd{h}": stats(ev_novol, f"fwd{h}") for h in FWD_HORIZONS}

    yr = yearly(ev_main, "fwd20")
    pos_years = sum(1 for v in yr.values() if v["med"] > 0)
    total_years = len(yr)

    gates = {
        "G1_n>=20": s_main["fwd20"]["n"] >= 20,
        "G2_med_excess>=+1pp": (s_main["fwd20"].get("med", -99) - base["fwd20"]["med"]) >= 1.0,
        "G3_win>=55": s_main["fwd20"].get("win", 0) >= 55.0,
        "G4_pos_years>=6/10": pos_years >= 6,
        "_detail": f"pos {pos_years}/{total_years}",
    }
    ov = overlap_pd034(ev_main)
    split = regime_split(ev_main)

    worst = sorted([e for e in ev_main if e.get("fwd20") is not None], key=lambda x: x["fwd20"])[:5]
    best = sorted([e for e in ev_main if e.get("fwd20") is not None], key=lambda x: -x["fwd20"])[:5]

    result = {
        "meta": {"script": "analysis/ftd_followthrough_study_20261007.py",
                 "data": "vipdoc sh000001.day", "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "rows": len(rows), "span": [rows[0]["date"], rows[-1]["date"]],
                 "locked": {"drawdown": TH_DRAWDOWN, "gain": TH_GAIN,
                            "window": [WINDOW_START, WINDOW_END], "cooldown": COOLDOWN}},
        "baseline": base,
        "main_ftd": s_main,
        "no_vol_control": s_novol,
        "sensitivity_grid": grid,
        "yearly_fwd20": yr,
        "gates": gates,
        "overlap_pd034": ov,
        "regime_split": split,
        "worst5": worst, "best5": best,
        "events_main": ev_main,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[BASELINE] fwd20 med={base['fwd20']['med']} win={base['fwd20']['win']}")
    print(f"[FTD main] n={s_main['fwd20']['n']} fwd20 med={s_main['fwd20']['med']} "
          f"win={s_main['fwd20']['win']} mdd20 med={s_main['mdd20']['med']}")
    print(f"[NOVOL ctrl] n={s_novol['fwd20']['n']} fwd20 med={s_novol['fwd20']['med']} win={s_novol['fwd20']['win']}")
    print(f"[GATES] {gates}")
    print(f"[SPLIT] " + " | ".join(f"{k}: n={v['fwd20'].get('n')} med={v['fwd20'].get('med')} win={v['fwd20'].get('win')}"
                                  for k, v in split.items()))
    print(f"[PD034 overlap] {ov}")
    print(f"saved -> {OUT}  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
