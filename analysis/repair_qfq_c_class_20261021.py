# -*- coding: utf-8 -*-
"""qfq C类(除权漏调/同病段错)修复(20261021) — 22点。默认DRY, --execute 写库。

统一框架: 每断点乘数 → 断点前全段累计缩放(与A类修复同构)。
  EVENT类(8点): 除权事件漏调, k = ((p−d)/(1+b))/p, p=断点前raw收盘(事件条款来自
                westock data_dividend 20261021拉取, 已逐股核对断点日±1交易日);
  RATIO类(14点): 断点日无任何除权事件(tushare/westock/hithink三源核对), 属A类同病
                (raw段尺度错或qfq因子错), m = (1+qfq_chg)/(1+raw_chg)。
事件不解释跳变幅度的(如300117@20170721仅0.1%分红对+120%跳变)归RATIO。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from build_qfq_meta_tdx_20260811 import read_tdx_day  # noqa: E402

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CLS_CSV = r"D:\zcodesj\analysis\qfq_break_classified_20261021.csv"
LEDGER = r"D:\zcodesj\analysis\qfq_repair_c_ledger_20261021.jsonl"

# (code, date, kind, b, d)  kind: EVENT=事件k / RATIO=比值m; b=每股送转, d=每股派息
POINTS = [
    ("000584", "20250620", "RATIO", 0, 0),
    ("000622", "20250625", "RATIO", 0, 0),
    ("600421", "20260601", "RATIO", 0, 0),
    ("605081", "20260608", "RATIO", 0, 0),
    ("300023", "20160104", "RATIO", 0, 0),
    ("300038", "20160901", "RATIO", 0, 0),
    ("300038", "20180514", "RATIO", 0, 0),
    ("300038", "20190709", "RATIO", 0, 0),
    ("300116", "20160322", "RATIO", 0, 0),
    ("300117", "20160104", "RATIO", 0, 0),
    ("300117", "20160506", "RATIO", 0, 0),
    ("300117", "20170721", "RATIO", 0, 0),
    ("300262", "20160104", "RATIO", 0, 0),
    ("300495", "20160104", "RATIO", 0, 0),
    ("300108", "20150505", "RATIO", 0, 0),
    ("300108", "20160526", "RATIO", 0, 0),
    ("300116", "20150703", "RATIO", 0, 0),
    ("300116", "20170418", "RATIO", 0, 0),
    ("300208", "20150924", "RATIO", 0, 0),
    ("300495", "20160613", "RATIO", 0, 0),
    ("300495", "20170427", "RATIO", 0, 0),
]


def main():
    execute = "--execute" in sys.argv
    cls = pd.read_csv(CLS_CSV, dtype={"code": str, "date": str})
    con = sqlite3.connect(QFQ)
    con.execute("PRAGMA journal_mode=WAL")
    ledger = open(LEDGER, "a", encoding="utf-8")
    total_rows, n_stocks = 0, 0
    for code in sorted({p[0] for p in POINTS}):
        pts = [p for p in POINTS if p[0] == code]
        q = pd.read_sql_query(
            "SELECT date,open,high,low,close,change_pct FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            con, params=(code,))
        raw = read_tdx_day(code)
        raw = raw.sort_values("date").reset_index(drop=True) if raw is not None and len(raw) else None
        q["mult"] = 1.0
        ms = []
        for (c, d, kind, b, dd) in pts:
            ix_arr = q.index[q["date"] == d]
            if len(ix_arr) == 0 or ix_arr[0] == 0:
                print(f"  [warn] {code}@{d}: qfq缺行, 跳过")
                continue
            ix = ix_arr[0]
            qfq_chg = q.at[ix, "close"] / q.at[ix - 1, "close"] - 1
            if kind == "EVENT":
                if raw is None:
                    print(f"  [warn] {code}@{d}: 无raw, 跳过")
                    continue
                arr = raw[raw.index < int(d)]
                if len(arr) < 1:
                    print(f"  [warn] {code}@{d}: raw缺行, 跳过")
                    continue
                p_prev = float(arr.iloc[-1]["close"])
                k = ((p_prev - dd) / (1 + b)) / p_prev
                mult = k
                src = f"EVENT b={b} d={dd} k={k:.4f}"
            else:
                if cls is None:
                    continue
                row = cls[(cls["code"] == c) & (cls["date"] == d)]
                raw_chg = float(row["raw_chg"].iloc[0]) if len(row) else np.nan
                mult = (1 + float(qfq_chg)) / (1 + raw_chg)
                src = f"RATIO m={mult:.4f}"
            q.loc[q.index < ix, "mult"] *= mult
            ms.append({"date": d, "kind": kind, "mult": round(float(mult), 5),
                       "qfq_chg": round(float(qfq_chg), 4)})
        if not ms:
            continue
        last_break = max(x["date"] for x in ms)
        n_rows = int((q["date"] <= last_break).sum())
        total_rows += n_rows
        n_stocks += 1
        if not execute:
            print(f"  [DRY] {code}: {len(ms)}断点({'+'.join(x['kind'] for x in ms)}), {n_rows}行, "
                  f"示例{ms[0]['date']}:{ms[0]['mult']}")
            continue
        cur = con.cursor()
        try:
            cur.execute("BEGIN")
            touched = q[q["date"] <= last_break]
            upd = touched[touched["mult"] != 1.0]
            rows = [(float(r.open * r.mult), float(r.high * r.mult), float(r.low * r.mult),
                     float(r.close * r.mult), r.date, code) for r in upd.itertuples()]
            cur.executemany(
                "UPDATE stock_daily_meta_qfq SET open=?,high=?,low=?,close=? WHERE date=? AND code=?", rows)
            q2 = pd.read_sql_query(
                "SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", con, params=(code,))
            cp = (q2["close"] / q2["close"].shift(1) - 1) * 100
            cur.executemany("UPDATE stock_daily_meta_qfq SET change_pct=? WHERE date=? AND code=?",
                            [(float(v) if pd.notna(v) else None, d, code) for d, v in zip(q2["date"], cp)])
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"  [FAIL] {code}: {type(e).__name__}: {e}")
            continue
        ledger.write(json.dumps({"code": code, "breaks": ms, "rows_updated": n_rows,
                                 "repair": "20261021_C"}, ensure_ascii=False) + "\n")
        ledger.flush()
        v = pd.read_sql_query(
            "SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", con, params=(code,))
        chg = v["close"] / v["close"].shift(1) - 1
        bad = 0
        for x in ms:
            ix = v.index[v["date"] == x["date"]]
            if len(ix) and ix[0] > 0 and abs(float(chg.iloc[ix[0]])) > 0.21:
                bad += 1
        print(f"  [{'FIXED' if bad == 0 else '⚠️残留'}] {code}: {len(ms)}断点 {n_rows}行 | 残留={bad}")
    ledger.close()
    con.close()
    print(f"\n[summary] {'已写入' if execute else 'DRY'}: {n_stocks} 只 / {total_rows} 行")


if __name__ == "__main__":
    sys.exit(main())
