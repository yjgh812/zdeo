# -*- coding: utf-8 -*-
"""qlib_bin 历史段重写器(20261021) — 配合 qfq 修复, 零 DB 写入(只写 qlib_bin 特征文件)。

对两个修复台账(A 387只 + C 12只)涉及的全部股票:
  重写 features/{inst}/{open,high,low,close}.day.bin 的全部 DB 覆盖位, 使 bin ≡ 修复后 qfq 库;
  volume/amount/vwap 不动(原始口径未受染); hdr/尾字节/停牌 NaN 惯例保留; 原子写; 先备份。
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
QLIB = Path(r"D:\workbuddyspace\qlib_data\qlib_bin")
LEDGERS = [r"D:\zcodesj\analysis\qfq_repair_ledger_20261021.jsonl",
           r"D:\zcodesj\analysis\qfq_repair_c_ledger_20261021.jsonl"]
BACKUP = Path(r"D:\zcodesj\backups\qlib_bin_pre_repair_20261021")
FIELDS = ["open", "high", "low", "close"]


def main():
    codes = set()
    for lp in LEDGERS:
        for line in open(lp, encoding="utf-8"):
            if line.strip():
                codes.add(json.loads(line)["code"])
    codes = sorted(codes)
    print(f"[in] 修复台账涉及 {len(codes)} 只")

    cal = [d.strip() for d in open(QLIB / "calendars" / "day.txt", encoding="utf-8") if d.strip()]
    cal_norm = [d.replace("-", "") for d in cal]
    print(f"[cal] 日历 {cal[0]}→{cal[-1]} 共{len(cal)}日")

    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    BACKUP.mkdir(parents=True, exist_ok=True)
    n_ok, n_skip, n_err = 0, 0, 0
    for code in codes:
        inst = ("sh" if code.startswith("6") else "sz") + code
        idir = QLIB / "features" / inst
        if not (idir / "close.day.bin").exists():
            print(f"  [skip] {inst}: 无bin")
            n_skip += 1
            continue
        df = pd.read_sql_query(
            "SELECT date,open,high,low,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            con, params=(code,))
        dbmap = {str(d): (float(o), float(h), float(l), float(c))
                 for d, o, h, l, c in df.itertuples(index=False)}
        bdir = BACKUP / inst
        bdir.mkdir(parents=True, exist_ok=True)
        try:
            news = {}
            for fld in FIELDS:
                arr = np.fromfile(idir / f"{fld}.day.bin", dtype="<f4")
                hdr = int(arr[0])
                vals = arr[1:-1].copy()
                for p in range(len(vals)):
                    ci = hdr + p
                    if ci >= len(cal_norm):
                        break
                    d = cal_norm[ci]
                    if d in dbmap:
                        vals[p] = dbmap[d][FIELDS.index(fld)]
                news[fld] = (arr[0], vals, arr[-1])
            for fld in FIELDS:
                (hdr, vals, tail) = news[fld]
                out = np.concatenate([np.array([hdr], dtype="<f4"), vals.astype("<f4"),
                                      np.array([tail], dtype="<f4")])
                (bdir / f"{fld}.day.bin.bak").write_bytes((idir / f"{fld}.day.bin").read_bytes())
                tmp = idir / f"{fld}.day.bin.tmp"
                out.tofile(tmp)
                tmp.replace(idir / f"{fld}.day.bin")
            n_ok += 1
        except Exception as e:
            n_err += 1
            print(f"  [FAIL] {inst}: {type(e).__name__}: {e}")
    con.close()
    print(f"\n[summary] 重写 {n_ok} 只 / 跳过 {n_skip} / 失败 {n_err} | 备份={BACKUP}")


if __name__ == "__main__":
    sys.exit(main())
