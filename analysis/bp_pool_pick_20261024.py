# -*- coding: utf-8 -*-
"""低位横盘池×主线命中子集 选股提取(20261024) — "按我的方法选5只"的口径落地。

方法真源: data/lowconsol_signal_latest.json (scan_date=20261023)
  - 集合级效应 +2.75~4.55pp (OOS_CONFIRMED, 档案自述"无个股排序依据")
  - 效应载体 = main_hit_list(主线命中子集, 38只)
本脚本只做: 引擎/宪法硬闸过滤(cap30/R32高位禁买/流动性/ST/次新) + 透明呈现排序,
不发明任何新alpha排序。零交易权, 买入须走 pre_trade_check_v2 + trade_executor 闸门。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STDB = r"D:\zcodesj\data\st_history.db"
SIG = r"D:\zcodesj\data\lowconsol_signal_latest.json"

PRICE_MAX = 30.0        # cap30 (引擎 price_max)
R32_CHG20_MAX = 0.30    # R32: 20日涨幅>=30% 禁买
MIN_AMT20 = 30e6        # 20日均成交额 >= 3000万
MIN_TRADEDAYS = 61      # 次新剔除
TOPN = 5


def main():
    sig = json.load(open(SIG, encoding="utf-8"))
    assert sig["scan_date"] == "20261023", f"信号扫描日非最新交易日: {sig['scan_date']}"
    pool = sig["main_hit_list"]
    print(f"信号扫描日={sig['scan_date']} 主线命中池={len(pool)}只  相位开关={sig['phase_switch']['switch']}(amt_q={sig['phase_switch']['amt_q']:.3f})")

    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query(
        "SELECT code, MAX(date) AS last_date, MAX(is_st) AS is_st FROM st_daily GROUP BY code", st_con)
    st_con.close()
    st_map = {c[-6:]: int(s) for c, s, in zip(st_rows["code"], st_rows["is_st"])}

    codes = [p["code"] for p in pool]
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    q = pd.read_sql_query(
        f"SELECT code,date,close,amount FROM stock_daily_meta_qfq WHERE date>='20260901' "
        f"AND code IN ({','.join(chr(39)+c+chr(39) for c in codes)}) ORDER BY code,date", con)
    age = pd.read_sql_query(
        f"SELECT code, COUNT(*) AS n_days FROM stock_daily_meta_qfq "
        f"WHERE code IN ({','.join(chr(39)+c+chr(39) for c in codes)}) GROUP BY code", con)
    con.close()
    age_map = dict(zip(age["code"], age["n_days"]))
    last = q.groupby("code").tail(22)
    rows = []
    for p in pool:
        g = last[last["code"] == p["code"]].sort_values("date")
        if len(g) < 21:
            continue
        close_now = float(g["close"].iloc[-1])
        close_20 = float(g["close"].iloc[-21])
        chg20 = close_now / close_20 - 1.0
        amt20 = float(g["amount"].tail(20).mean())
        n_days = int(age_map.get(p["code"], 0))
        rows.append(dict(code=p["code"], name=p["name"], l1=p["l1"], close_real=p["close"],
                         pos250=p["pos250"], streak=p["streak"], chg20=chg20, amt20=amt20,
                         n_days=n_days, is_st=st_map.get(p["code"], 0)))
    df = pd.DataFrame(rows)

    f = df[(df["close_real"] <= PRICE_MAX) & (df["chg20"] < R32_CHG20_MAX) &
           (df["amt20"] >= MIN_AMT20) & (df["n_days"] >= MIN_TRADEDAYS) & (df["is_st"] == 0)].copy()
    print(f"硬闸过滤: 池{len(df)} → 通过{len(f)}  (剔除明细: 价格>{len(df[df.close_real>PRICE_MAX])} "
          f"R32高位>{len(df[df.chg20>=R32_CHG20_MAX])} 流动性>{len(df[df.amt20<MIN_AMT20])} "
          f"次新/数据不足>{len(df[df.n_days<MIN_TRADEDAYS])} ST>{len(df[df.is_st>0])})")

    f = f.sort_values("amt20", ascending=False)  # 呈现排序: 流动性降序(可投资性抽样; 档案明示池内无个股alpha排序依据)
    picks = f.head(TOPN)
    out = pd.DataFrame(picks)[["code", "name", "l1", "close_real", "pos250", "streak", "chg20", "amt20"]]
    print("\n== 通过硬闸的全部标的(按20日均额降序, 前20) ==")
    print(f.head(20).to_string(index=False))
    print(f"\n== 5只呈现篮子(流动性降序抽样; 行业分布={dict(out['l1'].value_counts())}) ==")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
