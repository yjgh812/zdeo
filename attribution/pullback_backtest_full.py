r"""pullback_backtest_full.py — 回踩锚点法全量历史回测 V2（A/B 双试验）。

背景: V1(按第7/8步字面: 确认K线次日开盘入场)全量结果=434笔/37.1%胜率/均净-0.75%/年年负
→ 与号称72.8%/2.3:1严重背离。疑点=217样本统计含"再次放量启动"而第7/8步未含 → 本版A/B双试验:
  Trial A = 确认K线次日开盘入场(第7/8步字面)
  Trial B = 确认后3日内出现"再次放量启动"(量>=确认日1.5倍且收高)→次日开盘入场(217样本口径)
信号/出场/成本与V1完全一致; 指数状态键修正(int/str)。零交易权。
用法: python attribution/pullback_backtest_full.py
"""
import json
import os
import sqlite3
import sys
import time
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_DIR = r"D:\zcodesj\reports"
START, END = "20210101", "20261109"


def exit_of(bars, e_idx, n, anchor, thr):
    """出场判定: 止损优先/二板例外/晨离场。返回(px, reason)。"""
    e = bars[e_idx]
    if e[1] is None:
        return None, None
    if e[4] is not None and e[4] < anchor:
        return e[4], "stop_E"
    if e[6] is not None and e[6] >= thr and e[4] is not None and e[4] >= e[3] * 0.999:
        if e_idx + 1 < n and bars[e_idx + 1][1] is not None:
            return bars[e_idx + 1][1], "board2_hold"
        return e[4], "board2_hold_eod"
    if e_idx + 1 < n and bars[e_idx + 1][1] is not None:
        e1 = bars[e_idx + 1]
        if e1[4] is not None and e1[4] < anchor:
            return e1[4], "stop_E1"
        return e1[1], "morning_exit"
    return e[4], "eod_fallback"


def main():
    t0 = time.time()
    m = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True)
    irows = m.execute(
        "SELECT date, close FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
        ("20200601",)).fetchall()
    m.close()
    icloses = [r[1] for r in irows]
    idates = [int(r[0]) for r in irows]
    state = {}
    for i in range(60, len(icloses)):
        state[idates[i]] = icloses[i] > sum(icloses[i - 60:i]) / 60.0

    trades = []
    stats = {"codes": 0}
    q = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    cur = q.execute(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date", (START, END))
    for code, grp in groupby(cur, key=lambda r: r[0]):
        bars = [r[1:] for r in grp]  # (date,o,h,l,cl,v,cp)
        stats["codes"] += 1
        if code.startswith(("4", "8", "9")):
            continue
        gem = code.startswith(("300", "301", "302"))
        thr = 19.8 if gem else 9.8
        n = len(bars)
        for ti in range(25, n - 2):
            t = bars[ti]
            if t[1] is None or t[4] is None:
                continue
            fired = False
            for bi in range(ti - 5, ti - 2):
                if bi < 1:
                    continue
                d, o, h, l, cl, v, cp = bars[bi]
                if cp is None or cp < thr or cl is None or h is None \
                        or cl < h * 0.999 or not v or v <= 0:
                    continue
                bb = bars[bi + 1:ti + 1]
                if len(bb) < 2:
                    break
                if t[5] is None or t[5] > v * 0.5:
                    break
                if min(b[4] for b in bb) < cl:
                    break
                if not (cl <= t[4] <= cl * 1.03):
                    break
                if not (t[4] > t[1] and t[3] >= min(b[3] for b in bb[:-1])):
                    break
                fired = True
                anchor = cl
                board_date = d
                break
            if not fired:
                continue
            # Trial B: 再次放量启动日 U ∈ [T+1, T+3]
            u_idx = None
            for ui in range(ti + 1, min(ti + 4, n - 1)):
                u = bars[ui]
                if u[5] is not None and t[5] is not None and u[5] >= t[5] * 1.5 \
                        and u[4] is not None and u[4] > t[4]:
                    u_idx = ui + 1  # 入场=U次日开盘
                    break
            trials = [("A", ti + 1)]
            if u_idx is not None and u_idx < n:
                trials.append(("B", u_idx))
            for trial, e_idx in trials:
                exit_px, exit_reason = exit_of(bars, e_idx, n, anchor, thr)
                if exit_px is None:
                    continue
                entry_open = bars[e_idx][1]
                gross = exit_px / entry_open - 1
                trades.append({
                    "code": code, "trial": trial, "sig_date": t[0],
                    "board_date": board_date, "entry_date": bars[e_idx][0],
                    "entry_open": entry_open, "anchor": anchor,
                    "exit_reason": exit_reason,
                    "gross_pct": round(gross * 100, 3),
                    "net_pct": round((gross - 0.003) * 100, 3),
                    "entry_premp": round((entry_open / anchor - 1) * 100, 2),
                    "idx_above_ma60": state.get(int(t[0])),
                    "year": t[0][:4]})
    q.close()
    print("[%s] 扫描完成: %d 码, %d 笔(A+B), 耗时%.0fs" % (
        time.strftime("%H:%M:%S"), stats["codes"], len(trades), time.time() - t0))

    pairs = sorted({(t["code"], t["board_date"]) for t in trades})
    turn = {}
    f = sqlite3.connect("file:%s?mode=ro" % FUND_DB, uri=True)
    for c, d in pairs:
        row = f.execute(
            "SELECT turnover_rate FROM daily_basic WHERE ts_code=? AND trade_date=?",
            (c + (".SH" if c.startswith("6") else ".SZ"), d)).fetchone()
        if row and row[0] is not None:
            turn[(c, d)] = row[0]
    f.close()
    for t in trades:
        t["board_turnover"] = turn.get((t["code"], t["board_date"]))

    def agg(ts):
        if not ts:
            return {"n": 0}
        nets = [t["net_pct"] for t in ts]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        return {"n": len(ts),
                "win_rate": round(len(wins) / len(ts), 4),
                "mean_net": round(sum(nets) / len(ts), 3),
                "avg_win": round(sum(wins) / len(wins), 3) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 3) if losses else 0,
                "pl_ratio": round(abs(sum(wins) / len(wins) / (sum(losses) / len(losses))), 2)
                if wins and losses and sum(losses) else None,
                "stop_rate": round(sum(1 for t in ts if t["exit_reason"].startswith("stop")) / len(ts), 3),
                "board2_rate": round(sum(1 for t in ts if t["exit_reason"].startswith("board2")) / len(ts), 3)}

    out = {"window": [START, END], "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "cost_model": "双边0.3%", "n_codes": stats["codes"], "trials": {}}
    for trial in ("A", "B"):
        ts = [t for t in trades if t["trial"] == trial]
        out["trials"][trial] = {
            "overall": agg(ts),
            "by_year": {y: agg([t for t in ts if t["year"] == y])
                        for y in sorted({t["year"] for t in ts})},
            "by_idx_state": {
                "above_ma60": agg([t for t in ts if t["idx_above_ma60"] is True]),
                "below_ma60": agg([t for t in ts if t["idx_above_ma60"] is False])},
            "by_board_turnover": {
                "lt3": agg([t for t in ts if t["board_turnover"] is not None and t["board_turnover"] < 3]),
                "ge3": agg([t for t in ts if t["board_turnover"] is not None and t["board_turnover"] >= 3])},
            "by_combo": {
                "lt3_and_above": agg([t for t in ts if (t["board_turnover"] is not None
                                                        and t["board_turnover"] < 3
                                                        and t["idx_above_ma60"] is True)])}}
    path = os.path.join(OUT_DIR, "pullback_backtest_ab_20210101_20261109.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    for trial in ("A", "B"):
        o = out["trials"][trial]["overall"]
        print("Trial %s: n=%d 胜率=%.1f%% 均净=%.2f%% 盈亏比=%s 止损率=%.1f%%" % (
            trial, o["n"], o["win_rate"] * 100, o["mean_net"], o.get("pl_ratio"), o["stop_rate"] * 100))
    print("已写入", path)


if __name__ == "__main__":
    main()
