r"""stage_locate_daily.py — 四阶段定位每日夜间批跑 wrapper（零交易权）。
方法来源: 主人 2026-11-10 令四阶段实战化 + 「继续」令调度挂载。
码源 = 最新 reports/pullback_scan_*.json (观察档+候选) + data/position_codes_snapshot.json (westock持仓快照, 非实时真值声明)。
真值声明: 持仓/盈亏/成交唯一真值 = westock MCP 实时; 本 wrapper 的持仓快照仅用于圈定观察名单, 禁止用于交易决策。
输出: reports/stage_locate_<date>.json + 控制台重点摘要。
用法: python attribution/stage_locate_daily.py
"""
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj\attribution")
import sqlite3

import stage_locator as SL

OUT_DIR = r"D:\zcodesj\reports"
SNAPSHOT = r"D:\zcodesj\data\position_codes_snapshot.json"


def load_codes():
    codes = []
    scans = sorted(glob.glob(os.path.join(OUT_DIR, "pullback_scan_*.json")))
    if scans:
        with open(scans[-1], encoding="utf-8") as f:
            d = json.load(f)
        for x in (d.get("candidates") or []) + (d.get("watchlist") or []):
            codes.append(str(x.get("code")))
    if os.path.exists(SNAPSHOT):
        with open(SNAPSHOT, encoding="utf-8") as f:
            codes.extend(str(c) for c in json.load(f))
    seen = set()
    uniq = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def main():
    codes = load_codes()
    if not codes:
        print("[FAIL] 无候选码源")
        return 2
    fconn = None
    try:
        fconn = sqlite3.connect("file:%s?mode=ro" % SL.FUND_DB, uri=True)
    except sqlite3.Error:
        fconn = None
    results = []
    for c in codes:
        try:
            results.append(SL.locate(c, fconn=fconn))
        except Exception as e:
            results.append({"code": c, "stage": "ERROR", "detail": repr(e)})
    if fconn is not None:
        fconn.close()
    date_tag = results[0].get("date", "NA") if results else "NA"
    out_path = os.path.join(OUT_DIR, "stage_locate_%s.json" % date_tag)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    from collections import Counter
    cnt = Counter(r.get("stage", "?") for r in results)
    print("定位 %d 只 -> %s" % (len(results), out_path))
    for k, v in cnt.most_common():
        print("  %5d %s" % (v, k))
    for r in results:
        st = r.get("stage", "")
        tv = (r.get("turnover") or {}).get("band", "")
        if st.startswith(("S3", "S4b", "S5x")) or tv == "T4_异常(>20%派发预警)":
            print("[重点] %s %s %s %s" % (r["code"], st, tv, r.get("action", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
