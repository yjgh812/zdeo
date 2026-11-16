#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E10 量能×市值影子日更（PD-037-E10，主人 2026-10-07「按推荐继续」批准激活）。

prereg: reports/PREREG_E10_TURNOVER_SIZE_20261007.md（判据冻结，不得改参数）。
信号: 全市场日成交额(qfq SUM(amount)) 60交易日滚动分位 ≥80% → 信号日;
记录: 大1档-小10档(circ_mv NTILE(10)) 当日收盘, fwd5/10/20 价差由 --settle 回填。
窗口: 2026-10-08 起 60 个交易日（prereg 批准口径）。
零交易权零接线: 仅描述层台账+简报, 判据评审批准后另做。

用法:
  python analysis/e10_turnover_size_shadow.py --all      # scan+settle+report(调度用)
  python analysis/e10_turnover_size_shadow.py --scan     # 仅登记最新可得交易日
  python analysis/e10_turnover_size_shadow.py --report   # 仅刷新简报

调度: 每交易日 07:00（前一交易日数据由 02:00 对账保证完整; 与 prereg
「22:00 末位」的偏差=调度机制适应, 数据语义不变, 已在台账注记）。
数据未更新守卫: 最新 qfq 交易日 ≥ 窗口起点才工作, 否则一句带过。
"""
import hashlib
import json
import sqlite3
from collections import defaultdict

FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PREREG = r"D:\zcodesj\data\PREREG_E10_TURNOVER_SIZE_20261007.json"
LEDGER = r"D:\zcodesj\data\e10_turnover_size_ledger.jsonl"
BRIEF = r"D:\zcodesj\data\e10_shadow_brief_latest.txt"
OUT = r"D:\zcodesj\tmp\e10_shadow_scan_out.json"
# 🔴2026-10-13 数据源注记: qfq 读 wb 主库(生产夜链在更, 10/12 全日 5561 行)——zcodesj/data
# 旧副本已停更(10/09), 与 circuit_gate 10/11批4 同款修法; 副本滞后=窗口漏日 fail-open 方向。

PCTL = 0.80  # 由 prereg JSON 校验后使用（下方 verify_prereg fail-closed）


def verify_prereg():
    """指纹校验（FTD 同款 fail-closed）：JSON 缺失/指纹不符 → 退出禁止运行。"""
    try:
        doc = json.load(open(PREREG, encoding="utf-8"))
    except Exception as e:
        print(f"PREREG-FAIL: prereg JSON 不可读({e}) → fail-closed 退出")
        raise SystemExit(2)
    p = doc.get("params") or {}
    canonical = json.dumps(p, ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    if fp != doc.get("fingerprint"):
        print(f"PREREG-FAIL: 指纹不符(计算{fp} ≠ 登记{doc.get('fingerprint')}) → "
              "prereg 疑被篡改, fail-closed 退出并需写 notifications.log")
        raise SystemExit(2)
    return p


def load_rows():
    q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    turn = {r[0]: float(r[1] or 0) for r in q.execute(
        "SELECT date, SUM(amount) FROM stock_daily_meta_qfq "
        "WHERE date>=? GROUP BY date", (HIST0,))}
    q.close()
    f = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)
    f.row_factory = sqlite3.Row
    rows = f.execute(
        "SELECT trade_date, bkt, AVG(close) AS ac FROM ("
        " SELECT trade_date, close, NTILE(10) OVER ("
        "   PARTITION BY trade_date ORDER BY circ_mv DESC) AS bkt"
        " FROM daily_basic WHERE trade_date>=? AND close>0 AND circ_mv>0)"
        " GROUP BY trade_date, bkt", (WIN_START,)).fetchall()
    f.close()
    bucket = defaultdict(dict)
    for r in rows:
        bucket[r["trade_date"]][int(r["bkt"])] = float(r["ac"])
    dates = sorted(set(turn) & set(bucket))
    dates = [d for d in dates if d >= WIN_START]
    return dates, turn, bucket


def scan(dates, turn, bucket):
    """登记窗口内最新尚未入账的交易日（幂等 upsert by date）。"""
    have = set()
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    have.add(json.loads(line)["date"])
    except FileNotFoundError:
        pass
    tdates = sorted(turn)
    added = []
    for d in dates:
        if d in have:
            continue
        # 60交易日滚动分位(含当日, 取turn序列中<=d的最后60个)
        hist = [turn[x] for x in tdates if x <= d][-60:]
        if len(hist) < 30:
            continue
        srt = sorted(hist)
        rank = sum(1 for x in hist if x <= turn[d])
        pctl = rank / len(hist)
        row = {"date": d, "turnover_yi": round(turn[d] / 1e8, 0),
               "pctl60": round(pctl, 3), "is_signal": pctl >= PCTL,
               "big1_close": bucket[d].get(1), "small10_close": bucket[d].get(10),
               "fwd5": None, "fwd10": None, "fwd20": None,
               "settled_max_h": 0, "window_start": WIN_START}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        added.append(d)
    return added


def settle(dates, bucket):
    """回填 fwd5/10/20 价差（大1档收益-小10档收益），幂等全量重算。"""
    rows = []
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    idx = {d: i for i, d in enumerate(dates)}
    for r in rows:
        i = idx.get(r["date"])
        if i is None:
            continue
        b0, s0 = bucket[r["date"]].get(1), bucket[r["date"]].get(10)
        if not b0 or not s0:
            continue
        for h in (5, 10, 20):
            if i + h >= len(dates):
                continue
            d2 = dates[i + h]
            b1, s1 = bucket[d2].get(1), bucket[d2].get(10)
            if not b1 or not s1:
                continue
            r[f"fwd{h}"] = round((b1 / b0 - 1) - (s1 / s0 - 1), 5)
            r["settled_max_h"] = max(r.get("settled_max_h") or 0, h)
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def report(rows, dates):
    sig = [r for r in rows if r["is_signal"]]
    settled = [r for r in rows if (r.get("settled_max_h") or 0) >= 20]
    sig_settled = [r for r in sig if (r.get("settled_max_h") or 0) >= 20]
    lines = [f"E10量能×市值影子 | 窗口起点 {WIN_START} | 台账 {len(rows)} 日 "
             f"(信号 {len(sig)}, fwd20已结算 {len(settled)})"]
    if sig_settled:
        m = sum(r["fwd10"] for r in sig_settled) / len(sig_settled)
        lines.append(f"信号日fwd10价差均值(部分窗,非判读): {m:+.4f}")
    lines.append(f"影子进度: {len(rows)}/60 交易日 | 评审判读于满60日后一次性进行(prereg G1/G2/G3)")
    with open(BRIEF, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return lines


if __name__ == "__main__":
    import sys
    p = verify_prereg()
    WIN_START = p["window_start"]          # 注入模块级(函数引用)
    PCTL = p["signal_threshold_pctl"]
    HIST0 = "20260501"                     # 量能分位预热缓冲(实现细节非判据)
    mode = sys.argv[1] if len(sys.argv) > 1 else "--all"
    dates, turn, bucket = load_rows()
    latest = dates[-1] if dates else "无数据"
    res = {}
    if mode in ("--all", "--scan"):
        res["added"] = scan(dates, turn, bucket)
    if mode in ("--all", "--settle"):
        rows = settle(dates, bucket)
        res["ledger_rows"] = len(rows)
    if mode in ("--all", "--report"):
        rows = []
        try:
            with open(LEDGER, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            pass
        res["brief"] = report(rows, dates)
    res["latest_data_date"] = latest
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
