#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sentiment_quality_shadow.py — 情绪质量+fresh 池内二次选择 前向影子引擎 v1
（提案①⑥, 2026-10-01 主人「测试后，按推荐继续」批准；侦察级·零交易权·零闸门接线）

 prereg: PREREG_SNTQUAL_FRESH_20261001（指纹见 --fingerprint 输出）
 证据链: reports/sentiment_z2s_conditional_study_20261001.md（六轮验证）+
         reports/proposal_sentiment_quality_prereg_20261001.md（冻结口径）

 信号（信号日 t 收盘可知, next-bar 入场, 全程无前视）:
   Z2s strict = 泛强池(pos250≤50∧close>MA20∧MA20>MA60∧距250日低≤35%∧60日内放量大阳∧价≤30)
                ∧40日内启动事件(放量大阳∧前60日跌≥25%∧启动前缩量≤0.95)
                ∧回踩MA10企稳(low≤MA10≤close∧MA20上行)∧缩量(v<vma5)
   标签(冻结口径, 涨停集合=精确涨停价 round(前收×(1±10%/20%),2)):
     prem = 昨日涨停集合今日chg中位(%);  promo = 今涨停∩昨涨停/昨涨停;  lu = 今日涨停家数
     strong = (prem≥2 ∨ lu>100 ∨ promo≥30%) 命中≥2;  mid = 无确认∧(prem∈[0,2) ∨ promo∈[15%,30%));  rest=其余
     fresh  = 距最近启动事件 ≤5 交易日;  aged = 6~40
     regime = 上证 close>MA20 = BULL/BEAR
 判据(组合级对照为唯一主判据, 六轮方法论铁律——事件级均值禁作承诺):
   读数窗口 = 前向段(warmup=0)事件 ≥300 或 ≥60 交易日先到
   ①质量层维持条件: strong池 vs ALL池 — 无亏年多≥1 且 MDD改善≥5pp
   ②fresh维持条件: fresh池 vs 全体 — 总收益改善≥5pp 或 MDD改善≥10pp 且无亏年新增
   FAIL → 标签降级为观察词入证伪档案, 不进 pre_trade_check(永不自动接生产)
 组合回放引擎: 10槽等权 / next-bar / -8%止损(收盘判定) / T10 / 双边0.3% / 同日多信号按code排序取样

 台账: data/sentiment_quality_ledger.jsonl  (scan 幂等: 同 trade_date 先删后写)
 用法:
   python sentiment_quality_shadow.py --scan              # 信号日跑: 当日事件+标签
   python sentiment_quality_shadow.py --settle            # 回填到期事件 t20e/t10e/stopped
   python sentiment_quality_shadow.py --report            # 组合级对照读数
   python sentiment_quality_shadow.py --backfill 20260601 # 历史热身(warmup=1 不入判据)
   python sentiment_quality_shadow.py --fingerprint       # prereg 冻结指纹
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date

import numpy as np

ROOT = r"D:\zcodesj"
QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
LEDGER = os.path.join(ROOT, "data", "sentiment_quality_ledger.jsonl")
FETCH_FROM = "20240601"  # 250日预热+40日启动窗 → 事件最早约2025-06; 热身回填更早已足够
EVENT_FROM_MIN = "20250601"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---- prereg 冻结口径(改动=违约, 须新指纹) ----
CFG = {
    "stop": 0.92, "hold": 10, "slots": 10, "fee": 0.003,
    "prem_thr": 0.02, "lu_thr": 100, "promo_thr": 0.30,
    "fresh_days": 5, "dedup": 5,
}


def fingerprint() -> str:
    s = json.dumps(CFG, sort_keys=True) + "|Z2s_strict_v1.3|precise_limit_price"
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def ma(x, win):
    out = np.full(len(x), np.nan)
    if len(x) >= win:
        c = np.cumsum(np.insert(np.asarray(x, float), 0, 0.0))
        out[win - 1:] = (c[win:] - c[:-win]) / win
    return out


def load_panel():
    con = sqlite3.connect(QFQ_DB, uri=True)
    rows = con.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date",
        (FETCH_FROM,)).fetchall()
    con.close()
    panel = defaultdict(lambda: ([], [], [], [], [], []))
    for code, d, o, h, low, c, v in rows:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue
        p = panel[code]
        p[0].append(d); p[1].append(o); p[2].append(h); p[3].append(low); p[4].append(c); p[5].append(v)
    return panel


def build_market_sentiment(panel):
    """精确涨停价口径(冻结): 涨停=close>=round(prev_close*(1+lim),2)-0.005"""
    per_day_chg = defaultdict(dict)
    per_day_lu = defaultdict(set)
    for code, (dates, _, _, _, closes, _) in panel.items():
        c = np.asarray(closes, float)
        if len(c) < 2:
            continue
        gem = code[:3] in ("300", "301")
        lim = 0.20 if gem else 0.10
        for i in range(1, len(c)):
            pc, cl = c[i - 1], c[i]
            if not pc or pc <= 0:
                continue
            d = dates[i]
            per_day_chg[d][code] = cl / pc - 1.0
            if cl >= round(pc * (1 + lim), 2) - 0.005:
                per_day_lu[d].add(code)
    dlist = sorted(per_day_chg.keys())
    sent, prev = {}, set()
    for d in dlist:
        lu = per_day_lu.get(d, set())
        promo = (len(lu & prev) / len(prev)) if prev else None
        prem = None
        if prev:
            vals = [per_day_chg[d][x] for x in prev if x in per_day_chg[d]]
            if vals:
                prem = float(np.median(vals))
        sent[d] = {"lu": len(lu), "promo": promo, "prem": prem}
        prev = lu
    return sent, dlist


def collect_events(panel, event_from: str):
    """Z2s strict 事件(带 fresh 元数据)。返回 [(code, date, t, fresh)]"""
    events = []
    for code, (dates, opens, highs, lows, closes, vols) in panel.items():
        n = len(dates)
        if n < 260:
            continue
        low = np.asarray(lows, float)
        c = np.asarray(closes, float)
        v = np.asarray(vols, float)
        prev_c = np.concatenate([[np.nan], c[:-1]])
        chg = c / prev_c - 1.0
        ma10, ma20, ma60 = ma(c, 10), ma(c, 20), ma(c, 60)
        ma20_prev5 = np.full(n, np.nan)
        ma20_prev5[5:] = ma20[:-5]
        vma5, vma20, v250 = ma(v, 5), ma(v, 20), ma(v, 250)
        pos250 = np.full(n, np.nan)
        low250 = np.full(n, np.nan)
        if n >= 250:
            sw = np.lib.stride_tricks.sliding_window_view(c, 250)
            pos250[249:] = (sw[:, :-1] < sw[:, -1:]).mean(axis=1)
            low250[249:] = sw.min(axis=1)
        big = (vma20 > 0) & (v >= 2.0 * vma20) & (chg >= 0.03)
        big60 = np.convolve(big.astype(np.int8), np.ones(60, dtype=np.int8), mode="full")[:n] > 0
        pop = ((pos250 <= 0.50) & (c > ma20) & (ma20 > ma60)
               & (c / low250 - 1.0 <= 0.35) & big60 & (c <= 30.0))
        dd60 = np.full(n, np.nan)
        if n >= 60:
            sw60 = np.lib.stride_tricks.sliding_window_view(c, 60)
            dd60[59:] = (sw60.max(axis=1) - sw60.min(axis=1)) / sw60.max(axis=1)
        pre_shrink = np.full(n, np.nan)
        okv = v250 > 0
        pre_shrink[okv] = vma20[okv] / v250[okv]
        startup = big & (dd60 >= 0.25) & (pre_shrink <= 0.95)
        last_su = np.full(n, -1)
        for i in range(n):
            last_su[i] = i if startup[i] else (last_su[i - 1] if i > 0 else -1)
        startup40 = np.convolve(startup.astype(np.int8), np.ones(40, dtype=np.int8), mode="full")[:n] > 0
        pop_strict = pop & startup40
        cand = []
        for t in range(260, n):
            if dates[t] < event_from:
                continue
            ma20_up = ma20[t] > ma20_prev5[t]
            if (pop_strict[t] and not np.isnan(ma10[t]) and low[t] <= ma10[t] <= c[t] and ma20_up
                    and not np.isnan(vma5[t]) and v[t] < vma5[t]):
                cand.append(t)
        last = -99
        for t in cand:
            if t - last < CFG["dedup"]:
                continue
            last = t
            ds = t - last_su[t] if last_su[t] >= 0 else 999
            events.append((code, dates[t], t, bool(0 <= ds <= CFG["fresh_days"])))
    return events


def label_of(s: dict | None) -> str:
    if s is None:
        return "rest"
    prem3 = s["prem"] is not None and s["prem"] >= CFG["prem_thr"]
    lu4 = s["lu"] > CFG["lu_thr"]
    promo3 = s["promo"] is not None and s["promo"] >= CFG["promo_thr"]
    promo2 = s["promo"] is not None and 0.15 <= s["promo"] < CFG["promo_thr"]
    prem2 = s["prem"] is not None and 0.0 <= s["prem"] < CFG["prem_thr"]
    score = prem3 + lu4 + promo3
    if score >= 2:
        return "strong"
    if (prem2 or promo2) and score == 0:
        return "mid"
    return "rest"


def load_regime():
    from tdx_reader import read_day
    rows = read_day("sh", "000001")
    c = np.asarray([r[4] for r in rows], float)
    ma20 = ma(c, 20)
    return {int(d): ("BULL" if cc > m else "BEAR") for d, cc, m in zip([r[0] for r in rows], c, ma20)
            if not np.isnan(m)}


def load_ledger():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def save_ledger(rows):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, LEDGER)


def cmd_scan(target_date: str | None, warmup: bool) -> int:
    panel = load_panel()
    sent, dlist = build_market_sentiment(panel)
    regime = load_regime()
    td = target_date or date.today().strftime("%Y%m%d")
    events = [e for e in collect_events(panel, EVENT_FROM_MIN) if e[1] == td]
    rows = load_ledger()
    rows = [r for r in rows if r["trade_date"] != td]
    for code, d, t, fresh in events:
        s = sent.get(d)
        rows.append({
            "trade_date": d, "code": code, "t_index": t, "warmup": 1 if warmup else 0,
            "label": label_of(s), "fresh": fresh,
            "regime": regime.get(int(d)),
            "sent_raw": {"lu": s["lu"], "promo": s["promo"],
                         "prem": None if s["prem"] is None else round(s["prem"], 4)} if s else {},
            "t20e": None, "t10e": None, "stopped": None,
        })
    save_ledger(rows)
    n_s = sum(1 for r in rows if r["trade_date"] == td and r["label"] == "strong")
    n_f = sum(1 for r in rows if r["trade_date"] == td and r["fresh"])
    print(f"[SNTQUAL-SCAN] {td} events={len(events)} (strong={n_s} fresh={n_f} warmup={warmup}) "
          f"-> ledger 总行数={len(rows)}")
    return 0


def cmd_settle() -> int:
    """回填到期事件(t+21 已在 panel 内)。t_index 对 panel 加载序敏感 → 重载后校验 code/date 对齐。"""
    panel = load_panel()
    rows = load_ledger()
    n_fill = 0
    for r in rows:
        if r["t20e"] is not None:
            continue
        code, t = r["code"], r["t_index"]
        dates, opens, _, _, closes, _ = panel.get(code, ([], [], [], [], [], []))
        if not dates or dates[t] != r["trade_date"]:  # 防重载错位
            continue
        n = len(dates)
        if t + 21 >= n:
            continue
        entry = opens[t + 1]
        if not entry or entry <= 0:
            r["t20e"], r["t10e"], r["stopped"] = None, None, None
            continue

        def stop_exit(hold):
            ex = None
            for j in range(t + 1, min(t + 1 + hold, n)):
                if closes[j] <= entry * CFG["stop"]:
                    ex = closes[j]
                    break
            if ex is None:
                ex = closes[min(t + hold, n - 1)]
            return ex / entry - 1.0

        r["t10e"] = round(stop_exit(10), 5)
        r["t20e"] = round(stop_exit(20), 5)
        r["stopped"] = bool(any(closes[j] <= entry * CFG["stop"] for j in range(t + 1, t + 21)))
        n_fill += 1
    save_ledger(rows)
    print(f"[SNTQUAL-SETTLE] 回填 {n_fill} 行 (台账总 {len(rows)})")
    return 0


def replay(rows):
    """组合回放: rows=[{trade_date, code, t_index,...}] → nav_path。同引擎分池对照。"""
    panel = load_panel()
    ev_by_day = defaultdict(list)
    for r in rows:
        ev_by_day[r["trade_date"]].append((r["code"], r["t_index"]))
    cash, holdings, nav_path = 1.0, [], []
    for d in sorted(ev_by_day.keys()):
        still = []
        for h in holdings:
            dates, _, _, _, closes, _ = panel[h["code"]]
            n = len(dates)
            exited, j = False, h["next_j"]
            while j < n and dates[j] <= d:
                if closes[j] <= h["entry"] * CFG["stop"] or j - h["entry_j"] >= CFG["hold"]:
                    cash += h["shares"] * (closes[j] / h["entry"]) * (1 - CFG["fee"])
                    exited, j = True, j + 1
                    break
                j += 1
            if not exited:
                h["next_j"] = j
                still.append(h)
        holdings = still
        for code, t in sorted(ev_by_day[d])[: CFG["slots"] - len(holdings)]:
            dates, opens, _, _, _, _ = panel[code]
            n = len(dates)
            if t + 1 >= n or not opens[t + 1] or opens[t + 1] <= 0:
                continue
            cash -= 0.1
            if cash < 0:
                cash += 0.1
                break
            holdings.append({"code": code, "entry": opens[t + 1] * (1 + CFG["fee"]),
                             "shares": 0.1, "entry_j": t + 1, "next_j": t + 1})
        nav = cash
        for h in holdings:
            dates, _, _, _, closes, _ = panel[h["code"]]
            n = len(dates)
            j = min(h["next_j"], n - 1)
            while j + 1 < n and dates[j + 1] <= d:
                j += 1
            px = closes[j] if dates[j] <= d else h["entry"]
            nav += h["shares"] * (px / h["entry"])
        nav_path.append((d, nav))
    return nav_path


def yearly(path):
    years = defaultdict(list)
    for d, nav in path:
        years[d[:4]].append(nav)
    yr, ys = {}, sorted(years.keys())
    for i, y in enumerate(ys):
        base = years[ys[i - 1]][-1] if i > 0 else years[y][0]
        yr[y] = (years[y][-1] / base - 1) * 100
    navs = np.asarray([x[1] for x in path])
    mdd = ((navs / np.maximum.accumulate(navs) - 1).min()) * 100 if len(navs) else 0.0
    return yr, (path[-1][1] - 1) * 100 if path else 0.0, mdd


def cmd_report() -> int:
    rows = load_ledger()
    fwd = [r for r in rows if not r.get("warmup")]
    print(f"[SNTQUAL-REPORT] 台账总={len(rows)} 前向判据段={len(fwd)} "
          f"(≥300事件/≥60交易日为读数窗; 指纹={fingerprint()})")
    if not fwd:
        print("  前向段为空 — 影子刚启动, 无读数(正常)。热身段读数仅管线健康检查:")
        fwd = rows
    pools = {"ALL": fwd, "strong": [r for r in fwd if r["label"] == "strong"],
             "mid": [r for r in fwd if r["label"] == "mid"],
             "rest": [r for r in fwd if r["label"] == "rest"],
             "fresh": [r for r in fwd if r["fresh"]],
             "fresh∧strong": [r for r in fwd if r["fresh"] and r["label"] == "strong"]}
    for k, pool in pools.items():
        if not pool:
            print(f"  {k:14s} n=0")
            continue
        path = replay(pool)
        yr, tot, mdd = yearly(path)
        ys = " ".join(f"{yr.get(y, float('nan')):+6.2f}" for y in sorted(yr))
        print(f"  {k:14s} n={len(pool):4d} 总={tot:+7.2f}% MDD={mdd:+6.1f}% [{ys}]")
    return 0


def cmd_backfill(from_date: str) -> int:
    """历史热身: 逐日写台账(warmup=1)。供管线健康检查与 settle 验证, 不入判据。"""
    panel = load_panel()
    sent, dlist = build_market_sentiment(panel)
    regime = load_regime()
    events = collect_events(panel, from_date)
    rows = [r for r in load_ledger() if r.get("warmup")]  # 保留既有 warmup, 前向行不动
    kept = [r for r in load_ledger() if not r.get("warmup")]
    for code, d, t, fresh in events:
        s = sent.get(d)
        rows.append({"trade_date": d, "code": code, "t_index": t, "warmup": 1,
                     "label": label_of(s), "fresh": fresh, "regime": regime.get(int(d)),
                     "sent_raw": {"lu": s["lu"], "promo": s["promo"],
                                  "prem": None if s["prem"] is None else round(s["prem"], 4)} if s else {},
                     "t20e": None, "t10e": None, "stopped": None})
    save_ledger(rows + kept)
    print(f"[SNTQUAL-BACKFILL] {from_date} 起事件 {len(events)} 行(warmup=1) 入台账")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--scan-date", default="")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--backfill", default="")
    ap.add_argument("--fingerprint", action="store_true")
    a = ap.parse_args()
    if a.fingerprint:
        print(fingerprint())
        return 0
    if a.backfill:
        return cmd_backfill(a.backfill)
    if a.scan:
        return cmd_scan(a.scan_date or None, warmup=False)
    if a.settle:
        return cmd_settle()
    if a.report:
        return cmd_report()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
