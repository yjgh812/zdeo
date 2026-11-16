r"""board_nextday_backtest.py — 打板次日开盘模型 全历史回测（PREREG_DABAN_RESTART_V1）。

预注册冻结: data/PREREG_DABAN_RESTART_V1_20261114.json (指纹2c3da9cbe9bf5cf4)。
板日T: 封死(close>=high*0.999) + 涨幅阈值 + 非一字(open<high) + T-1未涨停(首板) + close>=2元。
入场: T+1开盘。离场: 唯一=T+2开盘(T+1制度最早卖点, 无盘中止损=风险结构)。
四组试验(声明于prereg, 分层一次跑完): T0全部 / T1+板换手<3% / T2+指数>MA60 / T3+T+1开盘溢价≤6%。
成本: 双边0.3%基准 + 0.5%拥挤档并列。零交易权。
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
OUT = r"D:\zcodesj\reports\board_nextday_bt_20210101_20261114.json"
START, END = "20210101", "20261113"


def main():
    t0 = time.time()
    m = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True)
    irows = m.execute(
        "SELECT date, close FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
        ("20200601",)).fetchall()
    m.close()
    ic = [r[1] for r in irows]
    idt = [int(r[0]) for r in irows]
    idx_state = {idt[i]: ic[i] > sum(ic[i - 60:i]) / 60.0 for i in range(60, len(ic))}

    trades = []
    n_codes = 0
    q = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    cur = q.execute(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date", (START, END))
    for code, grp in groupby(cur, key=lambda r: r[0]):
        bars = [r[1:] for r in grp]
        n_codes += 1
        if code.startswith(("4", "8", "9", "68")):
            continue
        gem = code.startswith(("300", "301", "302"))
        thr = 19.8 if gem else 9.8
        n = len(bars)
        for ti in range(1, n - 2):
            t = bars[ti]
            if t[4] is None or t[1] is None or t[2] is None or t[6] is None:
                continue
            if t[4] < 2.0 or t[4] < t[2] * 0.999 or t[1] >= t[2]:
                continue
            if t[6] < thr:
                continue
            p = bars[ti - 1]
            if p[6] is not None and p[6] >= thr:
                continue  # 首板要求: 前日未涨停
            e1 = bars[ti + 1]
            e2 = bars[ti + 2]
            if e1[1] is None or e2[1] is None:
                continue
            gross = e2[1] / e1[1] - 1
            trades.append({
                "code": code, "board_date": t[0], "gem": gem, "entry_date": e1[0],
                "entry_open": e1[1], "board_close": t[4],
                "premp": round((e1[1] / t[4] - 1) * 100, 2),
                "gross_pct": round(gross * 100, 3),
                "net_pct": round((gross - 0.003) * 100, 3),
                "net5_pct": round((gross - 0.005) * 100, 3),
                "idx_above": idx_state.get(int(t[0])), "year": t[0][:4]})
    q.close()
    print("[%s] %d 码, 板次信号 %d 笔, 耗时%.0fs" % (
        time.strftime("%H:%M:%S"), n_codes, len(trades), time.time() - t0))

    codes_needed = sorted({t["code"] for t in trades})
    turn = {}
    f = sqlite3.connect("file:%s?mode=ro" % FUND_DB, uri=True)
    for c in codes_needed:
        ts = c + (".SH" if c.startswith("6") else ".SZ")
        for d, tr in f.execute(
                "SELECT trade_date, turnover_rate FROM daily_basic WHERE ts_code=? AND trade_date>=?",
                (ts, START)):
            if tr is not None:
                turn[(c, d)] = tr
    f.close()
    for t in trades:
        t["board_turnover"] = turn.get((t["code"], t["board_date"]))

    def agg(ts):
        if not ts:
            return {"n": 0}
        nets = [t["net_pct"] for t in ts]
        n5 = [t["net5_pct"] for t in ts]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        return {"n": len(ts),
                "win_rate": round(len(wins) / len(ts), 4),
                "mean_net": round(sum(nets) / len(ts), 3),
                "mean_net5": round(sum(n5) / len(ts), 3),
                "avg_win": round(sum(wins) / len(wins), 3) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 3) if losses else 0,
                "pl_ratio": round(abs(sum(wins) / len(wins) / (sum(losses) / len(losses))), 2)
                if wins and losses and sum(losses) else None,
                "median_net": round(sorted(nets)[len(nets) // 2], 3)}

    A = lambda t: True
    B = lambda t: t["board_turnover"] is not None and t["board_turnover"] < 3.0
    C = lambda t: B(t) and t["idx_above"] is True
    D = lambda t: C(t) and t["premp"] <= 6.0
    trials = {"T0": A, "T1": B, "T2": C, "T3": D}
    out = {"window": [START, END], "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "n_codes": n_codes, "trials": {}}
    for name, cond in trials.items():
        ts = [t for t in trades if cond(t)]
        t_out = {"overall": agg(ts),
                 "by_year": {y: agg([x for x in ts if x["year"] == y])
                             for y in sorted({x["year"] for x in ts})},
                 "by_idx": {"above": agg([x for x in ts if x["idx_above"] is True]),
                            "below": agg([x for x in ts if x["idx_above"] is False])},
                 "by_gem": {"gem": agg([x for x in ts if x["gem"]]),
                            "main": agg([x for x in ts if not x["gem"]])}}
        out["trials"][name] = t_out
        o = t_out["overall"]
        print("%s: n=%d 胜率=%.1f%% 均净=%.2f%%(0.5%%档%.2f%%) 盈亏比=%s" % (
            name, o["n"], o["win_rate"] * 100, o["mean_net"], o["mean_net5"], o.get("pl_ratio")))
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("已写入", OUT)


if __name__ == "__main__":
    main()
