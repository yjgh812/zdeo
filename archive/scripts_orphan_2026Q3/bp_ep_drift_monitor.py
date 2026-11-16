# -*- coding: utf-8 -*-
"""bp_ep 名单漂移日检(P2-A, 胜率计划 WINRATE_UPGRADE_PLAN_20260829.md)·只读观测件

目的: 把「月频名单在快速轮动市的时滞」从体感变成每日数字(v4 根因链第一层)。
输出(P0-2 行业告警的数据底座):
  1) 当前名单行业分布 + 单一行业占比>50% 告警
  2) 成员价格结构漂移: 距 20 日高/低位置、20 日振幅、名单日以来的盈亏
  3) 「若今日按价格结构重选」的代理信号: 已跌破 MA20/跌出低波动区的成员数(非因子重算)
不做: 因子分数重算(bp/ep 打分归 picks 管线, 此处不复制语义); 任何买卖动作(纯观测)
用法: python bp_ep_drift_monitor.py [--list bp_ep_monthly_20260821_20260824.csv]
产物: reports/bp_ep_drift_YYYYMMDD.json + 控制台摘要(周五复盘消费)
"""
from __future__ import annotations
import argparse
import csv
import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

BASE = r"D:\zcodesj"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LIST_DIR = os.path.join(BASE, "multifactor", "reports", "daily_top20")
OUT = os.path.join(BASE, "reports", "bp_ep_drift_%s.json")
SECTOR_CAP_WARN = 0.50  # v3 P0-2 告警阈值(纯告警不降档)


def latest_list() -> str | None:
    files = sorted(glob.glob(os.path.join(LIST_DIR, "bp_ep_monthly_*.csv")))
    return files[-1] if files else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default=None, help="指定名单CSV(默认目录最新)")
    args = ap.parse_args()
    path = args.list or latest_list()
    if not path or not os.path.exists(path):
        print("[FAIL] 名单文件不存在")
        return 2
    with open(path, encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("rank") and r.get("code")]
    rows.sort(key=lambda r: int(float(r["rank"])))
    top = [r for r in rows if int(float(r["rank"])) <= 15]
    list_date = os.path.basename(path).split("_")[-2]  # bp_ep_monthly_{sig}_{eff}.csv

    qconn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True, timeout=10)
    qconn.row_factory = sqlite3.Row
    qcur = qconn.cursor()
    qcur.execute("SELECT MAX(date) FROM stock_daily_meta_qfq")
    px_date = qcur.fetchone()[0]

    members = []
    for r in top:
        code, name = r["code"], r.get("name") or ""
        qcur.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 21",
            (code, px_date))
        ks = [dict(x) for x in qcur.fetchall()]
        if len(ks) < 21:
            members.append({"code": code, "name": name, "industry": r.get("industry", ""), "status": "数据不足"})
            continue
        closes = [k["close"] for k in ks]
        ma20 = sum(closes[:20]) / 20
        hi20, lo20 = max(closes[:20]), min(closes[:20])
        px = closes[0]
        list_px = next((k["close"] for k in ks if k["date"] == list_date), None)
        m = {
            "code": code, "name": name, "industry": r.get("industry", ""), "rank": r["rank"],
            "close": px, "pos20": round((px - lo20) / (hi20 - lo20) * 100, 0) if hi20 > lo20 else None,
            "rng20_pct": round((hi20 - lo20) / lo20 * 100, 1),
            "below_ma20": px < ma20,
            "pnl_since_list_pct": round((px / list_px - 1) * 100, 1) if list_px else None,
        }
        members.append(m)

    # 行业分布与告警
    ind = Counter((m.get("industry") or "未知") for m in members)
    top_ind, top_cnt = (ind.most_common(1)[0] if ind else ("未知", 0))
    sector_alarm = top_cnt / max(len(members), 1) > SECTOR_CAP_WARN and len(members) > 0
    # 价格结构漂移摘要
    below_ma20_n = sum(1 for m in members if m.get("below_ma20"))
    breakers = [m["name"] for m in members if m.get("below_ma20")]
    losers = sorted([m for m in members if m.get("pnl_since_list_pct") is not None],
                    key=lambda m: m["pnl_since_list_pct"])
    drift_score = round(below_ma20_n / len(members) * 100, 0) if members else 0

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "list_file": os.path.basename(path), "list_signal_date": list_date,
        "price_asof": px_date, "members_n": len(members),
        "sector_dist": dict(ind.most_common(8)),
        "sector_alarm": {"top_industry": top_ind, "share": round(top_cnt / max(len(members), 1) * 100, 1),
                         "threshold_pct": SECTOR_CAP_WARN * 100, "triggered": sector_alarm},
        "drift": {"below_ma20_cnt": below_ma20_n, "drift_score_pct": drift_score,
                  "breakers": breakers,
                  "worst_since_list": [{"name": m["name"], "pnl": m["pnl_since_list_pct"]} for m in losers[:5]]},
        "members": members,
        "note": "P2-A观测件: 行业占比>阈值仅告警不降档(v3回测反证硬干预); drift_score=跌破MA20成员占比,"
                "与名单超额的关系需积累>=4期数据后再评估(P2-B/C证据入口), 前4期只积累不下结论",
    }
    with open(OUT % px_date, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"[bp_ep漂移日检] 名单={os.path.basename(path)} 行情截至{px_date}")
    print(f"  行业分布: {dict(ind.most_common(5))}")
    if sector_alarm:
        print(f"  ⚠️ 行业堆积告警: {top_ind} 占 {top_cnt}/{len(members)}"
              f" ({top_cnt/len(members)*100:.0f}% > {SECTOR_CAP_WARN*100:.0f}%) — 复盘时人工核主线")
    print(f"  漂移: {below_ma20_n}/{len(members)} 成员已跌破MA20 (drift {drift_score:.0f}%) {breakers[:6]}")
    if losers:
        print("  名单日以来最差: " + ", ".join(f"{m['name']}{m['pnl_since_list_pct']:+.1f}%" for m in losers[:5]))
    print(f"  产物: {OUT % px_date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
