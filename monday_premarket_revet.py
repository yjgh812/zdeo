# -*- coding: utf-8 -*-
"""monday_premarket_revet.py — 周一盘前复检(2026-11-07建, 补周末消息真空缺口)

背景: 12只名单周六上午排雷, 但周五深夜/周日晚间的利空公告(立案/减持/问询等)
到周一9:27直接买入之间无复检=消息真空。本脚本周一09:05跑:
  ①westock实时新闻桥逐票扫硬红旗(立案/调查/减持/处罚/问询/违规/退市/冻结/质押爆仓/强制平仓)
  ②命中者从买入计划中剔除(重写plan json)并留痕——与终审层合同一致(执行=已排雷版)
  ③本地forecast库新负面预告(ann_date>=周六)作次级检查(可能滞后, 仅参考)
fail-safe: 任何异常→计划原样不动(宁可买到已知名单, 不因复检故障丢单); 已.done则跳过。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
PLAN = os.path.join(ROOT, "data", "monday12_buy_plan_20261109.json")
LOG = os.path.join(ROOT, "data", "monday_premarket_revet.log")
NOTIFY = os.path.join(ROOT, "data", "notifications.log")


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(msg):
    try:
        with open(NOTIFY, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def main():
    if not os.path.exists(PLAN):
        log("计划不存在(可能已.done或已执行) → 跳过复检")
        return 0
    try:
        arr = json.load(open(PLAN, encoding="utf-8"))
    except Exception as e:
        log("计划读取失败(%s) → fail-safe原样不动" % e)
        return 0
    log("==== 周一盘前复检: %d只 ====" % len(arr))
    from nextday_buy_list import news_redflag
    removals = []
    try:
        fcon = sqlite3.connect(
            "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro", uri=True)
        neg_new = {r[0][:6] for r in fcon.execute(
            "select ts_code from forecast where ann_date>=20261107 and type in "
            "('预减','首亏','略减','预亏')")}
        fcon.close()
    except Exception:
        neg_new = set()
    for e in arr:
        c6 = str(e.get("code", ""))[-6:]
        nm = e.get("name", "")
        try:
            hard, soft, n_titles, err = news_redflag(c6)
        except Exception as ex:
            log("  %s %s 新闻桥异常(%s) → 保留(fail-open)" % (c6, nm, str(ex)[:40]))
            continue
        if hard:
            removals.append((c6, nm, hard[0][:60]))
            continue
        if c6 in neg_new:
            removals.append((c6, nm, "新负面预告"))
            continue
        log("  ✅ %s %s 扫描%d条无硬红旗%s" % (
            c6, nm, n_titles, ("(桥不可用)" if err else "")))
    if removals:
        keep = [e for e in arr if str(e.get("code", ""))[-6:] not in {r[0] for r in removals}]
        json.dump(keep, open(PLAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for c6, nm, why in removals:
            log("  🚫 %s %s 剔除: %s" % (c6, nm, why))
        notify("周一盘前复检剔除%d只: %s" % (
            len(removals), "; ".join("%s(%s)" % (nm, why[:20]) for _c, nm, why in removals)))
        log("计划已重写为%d只, 9:27按剔除后名单执行" % len(keep))
    else:
        log("全部通过, 12只计划原样保留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
