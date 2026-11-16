# -*- coding: utf-8 -*-
"""master_hist 信号行业分布监控(P0-3, WINRATE_UPGRADE_PLAN_20260829)·只读观测件

每日读 reports/daily_signals/signal_*.csv(02:00 链 v4 信号产物)前 20 名行业分布,
输出金融(银行+非银)占比; 连续 3 日金融占比>50% → 告警(P0-3 判据)。

历史回填证据(2026-08-29 首次计算):
  8/17 电子6/通信3(金融1) → 8/24 电子9(金融3, 反转) → 8/25 金融12 → 8/26 13 → 8/27 16/20 顶点
  → 若监控 8/25 上线, 8/27 即可预警当日 4 只银行买入(西安/南京/交通/平安, 次日银行下跌)。
产物: reports/master_hist_sector_YYYYMMDD.json(周五复盘消费)
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
SIGNAL_DIR = os.path.join(BASE, "reports", "daily_signals")
SW1_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
THRESH = 0.50   # 金融占比阈值
STREAK = 3      # 连续 3 日触发告警


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10, help="回看天数(默认10, 全量回填用 --days 99)")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{SW1_DB}?mode=ro", uri=True)
    sw1 = {r[0].lower(): r[1] for r in conn.execute("SELECT code, sector_name FROM stock_sector_sw1")}
    conn.close()

    files = sorted(glob.glob(os.path.join(SIGNAL_DIR, "signal_*.csv")))
    daily = []
    for f in files[-a.days:]:
        d = os.path.basename(f).replace("signal_", "").replace(".csv", "")
        rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))[:20]
        c = Counter()
        for r in rows:
            code6 = str(r.get("code", "")).lower()
            sec = sw1.get(code6, "未知")
            c[sec] += 1
        fin = sum(v for k, v in c.items() if ("银行" in k or "非银金融" in k))
        daily.append({"date": d, "n": len(rows), "financial": fin,
                      "financial_pct": round(fin / len(rows) * 100, 1) if rows else None,
                      "top_sectors": dict(c.most_common(5))})
    daily.sort(key=lambda x: x["date"])

    alarm_days = [x["date"] for x in daily if (x.get("financial_pct") or 0) > THRESH * 100]
    streak_hits = []
    for i in range(len(daily) - STREAK + 1):
        win = daily[i:i + STREAK]
        if all((x.get("financial_pct") or 0) > THRESH * 100 for x in win):
            streak_hits.append({"start": win[0]["date"], "end": win[-1]["date"],
                                "pcts": [x["financial_pct"] for x in win]})

    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "rule": f"金融(银行+非银)占比连续{STREAK}日>{THRESH*100:.0f}% 告警",
           "daily": daily, "streak_alarms": streak_hits,
           "note": "P0-3 纯观测: 触发告警在周五复盘提示 master_hist 源倾向大金融, "
                   "供人工评估是否暂缓该源买入/与主线核对; 不改模型不改权重"}
    path = os.path.join(BASE, "reports", f"master_hist_sector_{datetime.now().strftime('%Y%m%d')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"[master_hist行业监控] 金融占比(最近{len(daily)}期):")
    for x in daily:
        flag = " ⚠️" if (x.get("financial_pct") or 0) > THRESH * 100 else ""
        print(f"  {x['date']}: 金融{x['financial']}/{x['n']} ({x['financial_pct']}%){flag}")
    if streak_hits:
        print(f"\n🔴 连续{STREAK}日>50%告警: " + " | ".join(f"{s['start']}~{s['end']}({s['pcts']})" for s in streak_hits))
    else:
        print("\n无连续3日金融>50%告警(观察窗内)")
    print(f"产物: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
