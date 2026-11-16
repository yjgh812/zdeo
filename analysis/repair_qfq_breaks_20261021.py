# -*- coding: utf-8 -*-
"""qfq 断层修复脚本(20261021) — A类因子伪影582点。默认DRY, --execute 才写库。

方法(阶段1分类器定稿):
  每断点错误乘数 m = (1+qfq_chg)/(1+raw_chg)  (比值之比, 与其他断点无关, 可独立计算);
  断点前全段(该股所有 < 断点日的行)累计缩放: row_mult[t] = Π m_b (b∈断点, b > t);
  修复列 = open/high/low/close × mult; change_pct 全序列重算(百分数口径)。
  volume/amount 不动(原始口径记录), 台账记分段乘数+样例, 回滚=恢复备份库。
验证: --verify 对已修复股重跑越限检查, 断点应为0。
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from build_qfq_meta_tdx_20260811 import read_tdx_day  # noqa: E402

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CLS_CSV = r"D:\zcodesj\analysis\qfq_break_classified_20261021.csv"
LEDGER = r"D:\zcodesj\analysis\qfq_repair_ledger_20261021.jsonl"


def main():
    execute = "--execute" in sys.argv
    verify = "--verify" in sys.argv
    hd = pd.read_csv(CLS_CSV, dtype={"code": str, "date": str})
    a = hd[hd["class"] == "A_因子伪影"].copy()
    stocks = sorted(a["code"].unique())
    print(f"[in] A类断点 {len(a)} 个 / {len(stocks)} 只 | 模式={'EXECUTE' if execute else 'DRY'}"
          + ("+VERIFY" if verify else ""))

    con = sqlite3.connect(QFQ)
    con.execute("PRAGMA journal_mode=WAL")
    ledger_f = open(LEDGER, "a", encoding="utf-8")
    total_rows = 0
    repaired_stocks = 0

    for code in stocks:
        pts = a[a["code"] == code].sort_values("date")
        q = pd.read_sql_query(
            "SELECT date,open,high,low,close,change_pct FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            con, params=(code,))
        if len(q) < 10:
            print(f"  [skip] {code}: qfq行数不足({len(q)})")
            continue
        q["mult"] = 1.0
        ms = []
        for p in pts.itertuples():
            i = q.index[q["date"] == p.date]
            if len(i) == 0:
                print(f"  [warn] {code}@{p.date}: qfq无此日, 跳过")
                continue
            ix = i[0]
            if ix == 0:
                continue
            qfq_chg = q.at[ix, "close"] / q.at[ix - 1, "close"] - 1
            m = (1 + qfq_chg) / (1 + float(p.raw_chg))
            q.loc[q.index < ix, "mult"] *= m
            ms.append({"date": p.date, "m": round(m, 5), "raw_chg": float(p.raw_chg),
                       "qfq_chg": round(float(qfq_chg), 4)})
        if not ms:
            continue
        last_break = max(x["date"] for x in ms)
        touched = q[q["date"] <= last_break]
        n_rows = int((q["date"] <= last_break).sum())
        total_rows += n_rows
        repaired_stocks += 1
        if not execute:
            r0 = touched.iloc[0]
            print(f"  [DRY] {code}: 断点{len(ms)}个, 将更新{n_rows}行, 累计乘数段示例 "
                  f"{r0['date']}:×{r0['mult']:.4f}, 末断点{ms[-1]['date']}")
            continue

        cur = con.cursor()
        try:
            cur.execute("BEGIN")
            upd = touched[touched["mult"] != 1.0]
            rows = [
                (float(r.open * r.mult), float(r.high * r.mult), float(r.low * r.mult),
                 float(r.close * r.mult), r.date, code)
                for r in upd.itertuples()
            ]
            cur.executemany(
                "UPDATE stock_daily_meta_qfq SET open=?,high=?,low=?,close=? WHERE date=? AND code=?",
                rows)
            # change_pct 全序列重算(百分数)
            q2 = pd.read_sql_query(
                "SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", con, params=(code,))
            cp = (q2["close"] / q2["close"].shift(1) - 1) * 100
            cp_rows = [(float(v) if pd.notna(v) else None, d, code)
                       for d, v in zip(q2["date"], cp)]
            cur.executemany("UPDATE stock_daily_meta_qfq SET change_pct=? WHERE date=? AND code=?", cp_rows)
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"  [FAIL] {code}: {type(e).__name__}: {e} (已回滚)")
            continue
        ledger_f.write(json.dumps({"code": code, "breaks": ms, "rows_updated": n_rows,
                                   "repair": "20261021"}, ensure_ascii=False) + "\n")
        ledger_f.flush()
        if verify:
            v = pd.read_sql_query(
                "SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", con, params=(code,))
            chg = v["close"] / v["close"].shift(1) - 1
            bad = 0
            for x in ms:
                ix = v.index[v["date"] == int(x["date"])]
                if len(ix) and ix[0] > 0:
                    c = float(chg.iloc[ix[0]])
                    if abs(c) > 0.21:  # 各板限+1pp 的粗保险(GEM/科创20%/主板10%/北交30%中主板最严,修复后应≈raw变动)
                        bad += 1
            flag = "OK" if bad == 0 else f"⚠️残留{bad}"
            print(f"  [FIXED] {code}: {len(ms)}断点 {n_rows}行 | 断点处残留越限={bad} {flag}")

    ledger_f.close()
    con.close()
    print(f"\n[summary] {'已写入' if execute else 'DRY'}: {repaired_stocks} 只 / {total_rows} 行"
          f" | 台账={LEDGER}")


if __name__ == "__main__":
    sys.exit(main())
