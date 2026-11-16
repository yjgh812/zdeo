# -*- coding: utf-8 -*-
"""行业轮动过滤器(可独立运行的实时工具) 20261026 — 只读零交易权。
三层架构(主人框架):
  第一层: 数据 — 申万L1行业等权收益+成交额占比(qfq本地计算)
  第二层: 因子 — 动量(20日收益)+拥挤度(成交额占比偏离)+当日强弱
  第三层: 过滤 — 白名单(当日跑赢大盘) / 黑名单(20日最弱后20%且未反弹) / 中性
用法: python analysis/sector_rotation_filter.py [--date YYYYMMDD]
输出: 控制台(行业排序+白名单黑名单) + data/sector_rotation_latest.json
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
IMAP = "data/industry_group_map_snapshot.db"
OUT = "data/sector_rotation_latest.json"


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


def main():
    date_arg = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else None

    # 行业映射
    icon = sqlite3.connect(IMAP)
    imap = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", icon)
    icon.close()
    imap["code6"] = imap["code"].astype(str).str[-6:]
    l1_map = dict(zip(imap["code6"], imap["l1"]))

    # 价格数据(近120交易日)
    con = sqlite3.connect(QFQ, uri=True)
    latest = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    asof = int(date_arg) if date_arg else latest
    df = pd.read_sql_query(
        f"SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq "
        f"WHERE date>='20240101' AND date<='{asof}' ORDER BY code,date", con)
    con.close()
    df["code6"] = df["code"].map(norm)
    df = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")]
    df["l1"] = df["code6"].map(l1_map)
    df = df.dropna(subset=["l1"])
    df["d"] = df["date"].astype("int64")

    # ===== 第一层: 行业日频等权指数 =====
    ind_close = df.groupby(["l1", "d"])["close"].mean().reset_index()
    ind_amt = df.groupby(["l1", "d"])["amount"].sum().reset_index()
    piv_c = ind_close.pivot(index="d", columns="l1", values="close").sort_index()
    piv_a = ind_amt.pivot(index="d", columns="l1", values="amount").sort_index()

    if len(piv_c) < 25:
        print("[FATAL] 数据不足25日")
        return 2

    # ===== 第二层: 因子计算 =====
    # 动量: 20日收益
    mom20 = piv_c.iloc[-1] / piv_c.iloc[-21] - 1
    # 当日涨跌
    today = piv_c.iloc[-1] / piv_c.iloc[-2] - 1
    # 拥挤度: 当日成交额占比 / 过去20日平均占比 (偏离>2倍=拥挤)
    amt_today = piv_a.iloc[-1]
    amt_share_today = amt_today / amt_today.sum()
    amt_share_avg = piv_a.iloc[-20:].mean() / piv_a.iloc[-20:].sum().mean()
    crowding = amt_share_today / amt_share_avg  # >1=资金集中, >2=极度拥挤

    # 大盘等权(当日)
    mkt_today = float(today.mean())
    mkt_mom20 = float(mom20.mean())

    # ===== 第三层: 过滤 =====
    rows = []
    for l1 in piv_c.columns:
        rows.append({
            "l1": l1,
            "today_ret": round(float(today[l1]), 4),
            "mom20": round(float(mom20[l1]), 4),
            "crowding": round(float(crowding[l1]), 2),
            "vs_mkt_today": round(float(today[l1]) - mkt_today, 4),
        })
    ev = pd.DataFrame(rows).sort_values("today_ret", ascending=False)

    # 白名单: 当日跑赢大盘
    whitelist = ev[ev["vs_mkt_today"] > 0]["l1"].tolist()
    # 黑名单: 20日最弱后20% 且 当日未跑赢大盘
    worst_th = ev["mom20"].quantile(0.2)
    blacklist = ev[(ev["mom20"] <= worst_th) & (ev["vs_mkt_today"] <= 0)]["l1"].tolist()
    # 拥挤警告: crowding > 2
    crowded = ev[ev["crowding"] > 2.0]["l1"].tolist()
    # 中性 = 不在白/黑名单
    neutral = [l1 for l1 in ev["l1"] if l1 not in whitelist and l1 not in blacklist]

    # ===== 输出 =====
    result = {
        "asof": asof, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "market_today": round(mkt_today, 4), "market_mom20": round(mkt_mom20, 4),
        "whitelist": whitelist, "blacklist": blacklist, "neutral": neutral,
        "crowded_warning": crowded,
        "filter_verdict": "PAUSE(市场当日下跌,不建议买入)" if mkt_today <= 0 else "ACTIVE(可买白名单行业)",
        "sectors": ev.to_dict("records"),
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"行业轮动过滤器 | 基准日={asof} | 大盘当日={mkt_today:+.2%} | 大盘20日={mkt_mom20:+.2%}")
    print(f"判定: {result['filter_verdict']}")
    print()
    print(f"{'行业':<10} {'当日':>7} {'20日':>7} {'拥挤':>5} {'vs大盘':>7} {'判定':<6}")
    print("-" * 55)
    for _, r in ev.iterrows():
        tag = "白名单" if r["l1"] in whitelist else ("黑名单" if r["l1"] in blacklist else "中性")
        if r["l1"] in crowded:
            tag += "⚠️拥挤"
        print(f"{r['l1']:<10} {r['today_ret']:>+7.2%} {r['mom20']:>+7.2%} "
              f"{r['crowding']:>5.1f}x {r['vs_mkt_today']:>+7.2%} {tag}")
    print()
    print(f"白名单({len(whitelist)}): {', '.join(whitelist[:8])}")
    print(f"黑名单({len(blacklist)}): {', '.join(blacklist[:5])}")
    print(f"拥挤警告({len(crowded)}): {', '.join(crowded)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
