#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高位 X 天 Y 板核查预警（2026-08-18 落地，复盘线索④）

========== 背景 ==========
爱丽家居 11 连板 +185% 被交易所核查停牌（8/18 复牌）；一鸣食品 15 天 10 板、
风范股份 11 天 7 板同型风险。阈值：zttj.days（X 天）≥ 8 即触发核查预警，
写入 monitor_events 表 + 输出 JSON，纳入禁追高闸门参考。

========== 用法 ==========
    python high_board_verify_alert.py                # 今日实时
    python high_board_verify_alert.py --date 20260817 # 指定日
输出: D:/zcodesj/reports/high_board_alert_YYYYMMDD.json
"""
import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime

HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
OUT_DIR = r"D:/zcodesj/reports"
DB = r"D:/zcodesj/attribution/attribution.db"
WARN_DAYS = 8  # X天Y板 天数阈值


def fetch(url):
    req = urllib.request.Request(url, headers=HDRS)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(2)
    return None


def main(date_str: str | None = None):
    date = date_str or datetime.now().strftime("%Y%m%d")
    url = (f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
           f"&dpt=wz.ztzt&Pageindex=0&pagesize=400&sort=fbt%3Aasc&date={date}")
    d = fetch(url)
    if not d or not d.get("data") or not d["data"].get("pool"):
        print(json.dumps({"date": date, "error": "no pool"}))
        return
    pool = d["data"]["pool"]

    alerts = []
    for p in pool:
        zttj = p.get("zttj") or {}
        days = zttj.get("days") or 0
        ct = zttj.get("ct") or 0
        lbc = p.get("lbc") or 0
        name = p.get("n", "")
        code = p.get("c", "")
        if days >= WARN_DAYS or (lbc >= 5 and days >= 4):
            alerts.append({
                "code": code, "name": name, "days": days, "count": ct,
                "lbc": lbc, "hybk": p.get("hybk", ""),
                "desc": f"{days}天{ct}板, 连板{lbc}, 距爱丽家居核查阈值(days>=8)风险",
            })

    out = {"date": date, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "warn_threshold_days": WARN_DAYS,
           "alert_count": len(alerts), "alerts": alerts}
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"high_board_alert_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 写入 monitor_events（根库为监控事件库）
    try:
        conn = sqlite3.connect(r"D:/zcodesj/attribution.db")
        cur = conn.cursor()
        for a in alerts:
            cur.execute("INSERT INTO monitor_events (ts, rule_id, rule_name, code, name, severity, msg) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "HIGH_BOARD_VERIFY",
                         "X天Y板核查预警", a["code"], a["name"], "HIGH", a["desc"]))
        conn.commit()
        conn.close()
        print(f"[db] monitor_events 写入 {len(alerts)} 条")
    except Exception as e:
        print(f"[db] 写入失败: {e}")

    print(json.dumps({"date": date, "alert_count": len(alerts), "alerts": alerts},
                     ensure_ascii=False, indent=1))
    print("saved:", path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    main(a.date)