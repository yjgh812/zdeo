# -*- coding: utf-8 -*-
"""qfq 最后7残点修复(20261021) — 300116×3 / 300117×3 / 300262×1, raw(vipdoc)本身段损坏。

真值源: 腾讯公开不复权K线端点(web.ifzq.gtimg.cn, 与生产 trade_executor 行情降级同类公开源)。
  每断点: m = (1+qfq_chg)/(1+true_raw_chg), 断点前全段累计缩放(与A/C修复同构)。
默认DRY, --execute 写库(同前: OHLC×mult + change_pct重算 + 台账 + 断点处复检)。
"""
import json
import sqlite3
import sys
import urllib.request

import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = r"D:\zcodesj\analysis\qfq_repair_final7_ledger_20261021.jsonl"

POINTS = [
    ("300116", "20150703"),
    ("300116", "20160322"),
    ("300116", "20170418"),
    ("300117", "20160104"),
    ("300117", "20160506"),
    ("300117", "20170721"),
    ("300262", "20160104"),
]


def tencent_raw(code: str, start: str, end: str):
    sym = ("sz" if not code.startswith("6") else "sh") + code
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,{start},{end},640,")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        j = json.loads(r.read().decode("utf-8"))
    node = (j["data"][0] if isinstance(j["data"], list) else j["data"]).get(sym, {})
    days = node.get("day") or node.get("qfqday")
    return [(row[0].replace("-", ""), float(row[2])) for row in days]  # (date, close不复权)


def main():
    execute = "--execute" in sys.argv
    con = sqlite3.connect(QFQ)
    con.execute("PRAGMA journal_mode=WAL")
    ledger = open(LEDGER, "a", encoding="utf-8")
    total, nst = 0, 0
    for code in sorted({p[0] for p in POINTS}):
        pts = [p[1] for p in POINTS if p[0] == code]
        q = pd.read_sql_query(
            "SELECT date,open,high,low,close,change_pct FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            con, params=(code,))
        q["mult"] = 1.0
        ms = []
        for d in pts:
            ix_arr = q.index[q["date"] == d]
            if len(ix_arr) == 0 or ix_arr[0] == 0:
                print(f"  [warn] {code}@{d}: qfq缺行")
                continue
            ix = ix_arr[0]
            qfq_chg = q.at[ix, "close"] / q.at[ix - 1, "close"] - 1
            import datetime as _dt
            _y, _m, _dd = int(d[:4]), int(d[4:6]), int(d[6:])
            _start = (_dt.date(_y, _m, _dd) - _dt.timedelta(days=90)).isoformat()
            _end = f"{_y}-{_m:02d}-{_dd:02d}"
            raw = dict(tencent_raw(code, _start, _end))
            prev_dates = [x for x in sorted(raw) if x < d]
            if len(prev_dates) < 1 or d not in raw:
                print(f"  [warn] {code}@{d}: raw窗口缺行")
                continue
            d_prev = prev_dates[-1]
            true_chg = raw[d] / raw[d_prev] - 1
            m = (1 + float(qfq_chg)) / (1 + true_chg)
            q.loc[q.index < ix, "mult"] *= m
            ms.append({"date": d, "mult": round(m, 5), "qfq_chg": round(float(qfq_chg), 4),
                       "true_raw_chg": round(true_chg, 4)})
        if not ms:
            continue
        last_break = max(x["date"] for x in ms)
        n_rows = int((q["date"] <= last_break).sum())
        total += n_rows
        nst += 1
        if not execute:
            print(f"  [DRY] {code}: {len(ms)}点 {n_rows}行 | " +
                  " ".join(f"{x['date']}:m={x['mult']} raw真值={x['true_raw_chg']:+.2%}" for x in ms))
            continue
        cur = con.cursor()
        try:
            cur.execute("BEGIN")
            touched = q[q["date"] <= last_break]
            upd = touched[touched["mult"] != 1.0]
            rows = [(float(r.open * r.mult), float(r.high * r.mult), float(r.low * r.mult),
                     float(r.close * r.mult), r.date, code) for r in upd.itertuples()]
            cur.executemany("UPDATE stock_daily_meta_qfq SET open=?,high=?,low=?,close=? WHERE date=? AND code=?", rows)
            q2 = pd.read_sql_query("SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                                   con, params=(code,))
            cp = (q2["close"] / q2["close"].shift(1) - 1) * 100
            cur.executemany("UPDATE stock_daily_meta_qfq SET change_pct=? WHERE date=? AND code=?",
                            [(float(v) if pd.notna(v) else None, d, code) for d, v in zip(q2["date"], cp)])
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"  [FAIL] {code}: {type(e).__name__}: {e}")
            continue
        ledger.write(json.dumps({"code": code, "breaks": ms, "rows_updated": n_rows,
                                 "repair": "20261021_final7"}, ensure_ascii=False) + "\n")
        ledger.flush()
        v = pd.read_sql_query("SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                              con, params=(code,))
        chg = v["close"] / v["close"].shift(1) - 1
        bad = 0
        for x in ms:
            ix = v.index[v["date"] == x["date"]]
            if len(ix) and ix[0] > 0 and abs(float(chg.iloc[ix[0]])) > 0.21:
                bad += 1
        print(f"  [{'FIXED' if bad == 0 else '⚠️残留'}] {code}: {len(ms)}点 {n_rows}行 | 残留={bad}")
    ledger.close()
    con.close()
    print(f"\n[summary] {'已写入' if execute else 'DRY'}: {nst} 只 / {total} 行")


if __name__ == "__main__":
    sys.exit(main())
