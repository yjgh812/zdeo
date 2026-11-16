#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""放量锚历史回放研究器 v1 (2026-11-11)

执行合同=PREREG_VOL_ANCHOR_LOCATOR_V1_20261111.json (指纹 3608b9a20cc10860)。
回放定位=描述性统计 + 已知漏网样本专项(M3); 正式 M1/M2 判定由 60 交易日影子期产出
(回放对照组存在时段偏差, 预注册已声明为已知局限)。
纯研究零交易权; 价格源=qfq 库只读; fail-closed。
"""
import json
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\data\vol_anchor_replay_20261111.json"

WIN_START, WIN_END = "20251112", "20261111"
TIER_X = 1.5
CHG_MIN = 3.0
COOLDOWN = 20
FWD = (5, 10, 20)
MISSED_CASES = [("002520", "日发精机", "20260801", "20260825"), ("600127", "金健米业", "20260725", "20260820")]


def load_bars(conn, code):
    rows = conn.execute(
        "SELECT date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>=? AND date<=? ORDER BY date", (code, WIN_START, WIN_END)).fetchall()
    return [r for r in rows if r[5] is not None and r[5] > 0 and r[2] and r[2] > 0]


def scan_events(bars, code):
    """窗口内三条件放量锚事件(排除当日涨停, 20日冷却)。返回 [(idx, tier, vx)]。"""
    thr_limit = 19.8 if str(code).startswith(("300", "301", "302")) else 9.8
    n = len(bars)
    events = []
    last_i = -999
    for i in range(20, n):
        d, o, h, l, c, v, chg = bars[i]
        if chg is None or chg >= thr_limit:
            continue
        prior = bars[max(i - 20, 0):i]
        if len(prior) < 10:
            continue
        v20 = sum(b[5] for b in prior) / len(prior)
        if v20 <= 0:
            continue
        vx = v / v20
        hi20 = max(b[2] for b in bars[max(i - 20, 0):i])
        if vx >= TIER_X and chg >= CHG_MIN and h >= hi20:
            if i - last_i < COOLDOWN:
                continue
            tier = "2.0x" if vx >= 2.0 else "1.5x"
            events.append((i, tier, round(vx, 2)))
            last_i = i
    return events


def fwd_returns(bars, idx):
    c0 = bars[idx][4]
    out = {}
    for k in FWD:
        if idx + k < len(bars):
            out["t%d" % k] = round((bars[idx + k][4] / c0 - 1.0) * 100, 2)
    return out


def stats(vals):
    if not vals:
        return {"n": 0}
    vals_s = sorted(vals)
    n = len(vals_s)
    return {"n": n, "mean": round(sum(vals_s) / n, 2),
            "median": round(vals_s[n // 2], 2),
            "win_rate": round(100.0 * sum(1 for v in vals_s if v > 0) / n, 1)}


def main():
    conn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
    universe = [c for c in codes if str(c)[0] in ("0", "3", "6")
                and not str(c).startswith(("4", "8", "9", "688", "689"))]
    print("宇宙=%d只" % len(universe))

    tier15, tier20 = {"t5": [], "t10": [], "t20": []}, {"t5": [], "t10": [], "t20": []}
    baseline = {"t5": [], "t10": [], "t20": []}
    missed = {}
    n_codes_done = 0
    for code in universe:
        bars = load_bars(conn, code)
        if len(bars) < 60:
            continue
        n_codes_done += 1
        # 事件扫描
        for idx, tier, vx in scan_events(bars, code):
            fr = fwd_returns(bars, idx)
            bucket = tier20 if tier == "2.0x" else tier15
            for k in FWD:
                if "t%d" % k in fr:
                    bucket["t%d" % k].append(fr["t%d" % k])
        # 对照基线: 每票窗口首个可用观测日
        fr0 = fwd_returns(bars, 0)
        for k in FWD:
            if "t%d" % k in fr0:
                baseline["t%d" % k].append(fr0["t%d" % k])
        # 漏网样本专项
        for mc, mn, ws, we in MISSED_CASES:
            if code == mc:
                sub = [(i, t, x) for i, t, x in scan_events(bars, code) if ws <= bars[i][0] <= we]
                missed[mc] = {"name": mn,
                              "hit": [{"date": bars[i][0], "tier": t, "vol_x": x,
                                       "fwd": fwd_returns(bars, i)} for i, t, x in sub]}
    conn.close()
    print("处理完成=%d只" % n_codes_done)

    result = {
        "asof": time.strftime("%Y%m%d"), "window": [WIN_START, WIN_END],
        "universe_done": n_codes_done,
        "tier_1_5x": {k: stats(v) for k, v in tier15.items()},
        "tier_2_0x": {k: stats(v) for k, v in tier20.items()},
        "baseline_firstday": {k: stats(v) for k, v in baseline.items()},
        "missed_cases": missed,
        "prereg": "PREREG_VOL_ANCHOR_LOCATOR_V1_20261111 fp=3608b9a20cc10860",
        "note": "回放=描述性+漏网样本专项; 正式M1/M2由60交易日影子产出; 对照组时段偏差已知局限",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    for tag, b in [("1.5x", tier15), ("2.0x", tier20), ("基线", baseline)]:
        for k in FWD:
            print(tag, k, stats(b["t%d" % k]))
    for mc in missed:
        print("漏网专项", mc, json.dumps(missed[mc], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
