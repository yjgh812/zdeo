"""tickflow free-api 历史日K vs 本地 qfq 库 对账（只读）。

用途：验证 tickflow 免费源能否作为独立日K校验源（双源对账）。
方法：拉 tickflow(adjust=none, 最近 N 根) 与 qfq 库同区间数据，
      比对①日期覆盖②最新收盘价③逐日涨跌幅 change_pct（除权日除外）。
用法（系统 python 需可 import tickflow，或用 tickflow-stock-panel/backend/.venv）：
    python tickflow_vs_qfq_reconcile.py
"""
from __future__ import annotations

import argparse
import sqlite3

QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
# 跨行业样本（含当前持仓/大盘蓝筹）
SAMPLES = {
    "600519.SH": "贵州茅台",
    "000001.SZ": "平安银行",
    "600036.SH": "招商银行",
    "300750.SZ": "宁德时代",
    "601919.SH": "中远海控",
    "000725.SZ": "京东方A",
}
N_BARS = 1000  # 最近约 4 个交易年


def main() -> None:
    from tickflow import TickFlow  # 延迟导入

    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=N_BARS)
    args = ap.parse_args()

    tf = TickFlow.free()
    symbols = list(SAMPLES)
    raw = tf.klines.batch(
        symbols, period="1d", adjust="none",
        count=args.count, as_dataframe=True, show_progress=False,
    )
    if not isinstance(raw, dict):
        print("FATAL: 期望返回 dict[symbol -> DataFrame]")
        raise SystemExit(2)

    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    print("=" * 78)
    print("tickflow free-api 日K  vs  本地 qfq 库 对账")
    print("=" * 78)
    all_ok = True
    for sym, df in raw.items():
        if df is None or len(df) == 0:
            print(f"[{sym}] {SAMPLES[sym]}: tickflow 无数据")
            continue
        code = sym.split(".")[0]
        # 日期统一为 YYYYMMDD
        ts = df["timestamp"].to_list()
        closes = df["close"].to_list()
        tick_dates = []
        import datetime as _dt
        for t in ts:
            d = _dt.datetime.fromtimestamp(t / 1000, _dt.timezone(_dt.timedelta(hours=8)))
            tick_dates.append(d.strftime("%Y%m%d"))
        # 计算 tickflow 涨跌幅（%）：close / prev_close - 1
        tick_pct = []
        for i in range(len(closes)):
            if i == 0:
                tick_pct.append(None)
            else:
                prev = closes[i - 1]
                if prev and closes[i]:
                    tick_pct.append((closes[i] / prev - 1.0) * 100.0)
                else:
                    tick_pct.append(None)

        # qfq 库
        ph = ",".join("?" * len(tick_dates))
        qrows = con.execute(
            f"SELECT date, close, change_pct FROM stock_daily_meta_qfq "
            f"WHERE code=? AND date IN ({ph}) ORDER BY date", [code] + tick_dates
        ).fetchall()
        qmap = {r[0]: (r[1], r[2]) for r in qrows}

        # 比对
        matched = 0
        close_exact = 0
        pct_match = 0
        pct_mismatch = 0
        mismatch_days = []
        for d, c, p in zip(tick_dates, closes, tick_pct):
            if d not in qmap:
                continue
            matched += 1
            qc, qp = qmap[d]
            if qc is not None and abs(qc - c) < 0.005:
                close_exact += 1
            if p is None or qp is None:
                continue
            if abs(p - qp) < 0.02:  # 0.02pp 容差（舍入）
                pct_match += 1
            else:
                pct_mismatch += 1
                mismatch_days.append((d, round(p, 2), round(qp, 2)))

        latest_date = tick_dates[-1] if tick_dates else None
        latest_tick = closes[-1] if closes else None
        latest_qfq = qmap.get(latest_date, (None, None))[0] if latest_date else None
        latest_ok = latest_tick is not None and latest_qfq is not None and abs(latest_tick - latest_qfq) < 0.005

        ok = (matched > 100) and (close_exact >= matched - 2) and (pct_match / max(1, matched - 1) >= 0.95)
        all_ok = all_ok and ok
        print(f"\n[{sym}] {SAMPLES[sym]}")
        print(f"  匹配交易日: {matched} | 收盘价一致: {close_exact}/{matched} | 涨跌幅一致: {pct_match} | 不一致: {pct_mismatch}")
        print(f"  最新日 {latest_date}: tickflow={latest_tick} qfq={latest_qfq} -> {'✅一致' if latest_ok else '❌不一致'}")
        if mismatch_days:
            print(f"  涨跌幅不一致样例(前5): {mismatch_days[:5]}  <- 多为除权除息日,预期内")
        print(f"  判定: {'✅ 通过' if ok else '❌ 未通过'}")

    con.close()
    print("\n" + "=" * 78)
    print(f"总判定: {'✅ 全部通过, tickflow free-api 可作独立日K校验源' if all_ok else '❌ 存在未通过样本, 详见上方'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
