# -*- coding: utf-8 -*-
"""strong_pool_shadow_settle.py — 影子快照前向表现结算器 (观测层, 每日与 scan 配对跑)
扫描 data/strong_pool_shadow/pool_*.json 快照, 对已到期(T+5/T+20 交易日数据齐)且未结算的:
  pool3 成员 → V0口径(-8%收盘止损+T5, T+1开盘入场, 扣0.2%)
  pool_a8 成员 → V6口径(-12%收盘止损+T20)
追加 data/strong_pool_shadow/settled.jsonl (按 date+pool 幂等去重)。
用法: python strong_pool_shadow_settle.py   (建议收盘后 scan 之后运行)
"""
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
DIR = "data/strong_pool_shadow"
SETTLED = f"{DIR}/settled.jsonl"
COST = 0.002


def main():
    import sqlite3
    db = sqlite3.connect(DB, uri=True)
    snaps = sorted(glob.glob(f"{DIR}/pool_*.json"))
    if not snaps:
        print("无快照文件")
        return 0
    need_dates = set()
    meta = []
    for p in snaps:
        d = json.load(open(p, encoding="utf-8"))
        meta.append(d)
        need_dates.add(d["date"])
    # 取快照日期并集 ± 前后240日的全市场收盘矩阵(足够覆盖T+20)
    ds = sorted(need_dates)
    lo, hi = ds[0], ds[-1]
    df = pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE date>='{lo}' ORDER BY code,date", db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    # 每票收盘/开盘序列
    close_map, open_map, date_index = {}, {}, {}
    for code, s in df.groupby("code"):
        close_map[code] = s["close"].tolist()
        open_map[code] = s["open"].tolist()
        date_index[code] = s["date"].tolist()
    all_dates = sorted(df["date"].unique())

    def idx_of(code, d):
        lst = date_index.get(code)
        if lst is None:
            return None
        try:
            return lst.index(d)
        except ValueError:
            return None

    def ret_for(code, d, stop, hold):
        i = idx_of(code, d)
        if i is None or i + 1 >= len(close_map.get(code, [])):
            return None, None
        entry = open_map[code][i + 1]  # T+1 开盘
        if not entry or entry <= 0:
            return None, None
        closes = close_map[code][i + 1: i + 1 + hold]
        if len(closes) < hold:
            return None, None  # 未到期
        if stop is None:
            return closes[-1] / entry - 1 - COST, False
        line = entry * (1 - stop)
        for c in closes:
            if c <= line:
                return c / entry - 1 - COST, True
        return closes[-1] / entry - 1 - COST, False

    have = set()
    if os.path.exists(SETTLED):
        for line in open(SETTLED, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                have.add((r["date"], r["pool"]))
    out_lines = []
    for d in meta:
        date = d["date"]
        for pool, key, stop, hold in [("pool3", "pool3_members", 0.08, 5),
                                      ("pool_a8", "pool_a8_members", 0.12, 20)]:
            if (date, pool) in have:
                continue
            members = d.get(key) or []
            codes = [m["code"] if isinstance(m, dict) else m for m in members]
            rets, stops = [], 0
            matured = 0
            for c in codes:
                r, st = ret_for(c, date, stop, hold)
                if r is None:
                    continue
                matured += 1
                rets.append(r)
                stops += 1 if st else 0
            need_n = hold + 2
            # 到期判定: 存在至少 hold+1 个交易日且快照日+hold 的市场交易日已存在
            i_date = all_dates.index(date) if date in all_dates else None
            if i_date is None or i_date + hold >= len(all_dates):
                continue  # 未到期, 跳过等下次
            if matured == 0:
                continue
            row = {"date": date, "pool": pool, "n_member": len(codes), "n_settled": matured,
                   "mean": round(float(np.mean(rets)) * 100, 2),
                   "med": round(float(np.median(rets)) * 100, 2),
                   "win": round(float(np.mean([r > 0 for r in rets])) * 100, 1),
                   "stop_rate": round(stops / matured * 100, 1),
                   "settled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            out_lines.append(json.dumps(row, ensure_ascii=False))
    if out_lines:
        with open(SETTLED, "a", encoding="utf-8") as f:
            for L in out_lines:
                f.write(L + "\n")
    print(f"结算完成: 新增{len(out_lines)}行 (快照{len(snaps)}份), 未到期/无新样本的自动跳过")
    for L in out_lines:
        print(" ", L)
    return 0


if __name__ == "__main__":
    sys.exit(main())
