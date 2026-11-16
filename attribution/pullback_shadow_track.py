r"""pullback_shadow_track.py — 回踩锚点法影子台账回填器。

消费 pullback_scan_<date>.json 的观察档/候选, 用 qfq 数据回填其后每交易日结果:
  d1_confirmed  = 次日收阳且不创新低且收在[anchor,anchor*1.03] (方法确认日)
  entry_date/px = 确认次日开盘价(方法买入点); 之后最新收盘相对开盘收益
  stop_hit      = 任意日收盘跌破 anchor (1R口径前哨)
幂等: 按(scan_date,code)覆盖重写。零交易权。
用法: python attribution/pullback_shadow_track.py [scan_date=latest]
"""
import glob
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = r"D:\zcodesj\data\pullback_shadow_ledger.jsonl"
SCAN_DIR = r"D:\zcodesj\reports"


def main():
    scan_date = None
    if len(sys.argv) > 1:
        scan_date = sys.argv[1]
        path = os.path.join(SCAN_DIR, "pullback_scan_%s.json" % scan_date)
    else:
        files = sorted(glob.glob(os.path.join(SCAN_DIR, "pullback_scan_*.json")))
        if not files:
            print("[FATAL] 无扫描文件")
            sys.exit(2)
        path = files[-1]
        scan_date = os.path.basename(path)[14:22]
    scan = json.load(open(path, encoding="utf-8"))
    cands = (scan.get("candidates") or []) + (scan.get("watchlist") or [])
    print("[%s] scan_date=%s 候选/观察=%d" % (time.strftime("%H:%M:%S"), scan_date, len(cands)))

    conn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    out = []
    for c in cands:
        code = c["code"]
        rows = conn.execute(
            "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>? ORDER BY date ASC LIMIT 15",
            (code, scan_date)).fetchall()
        anchor = c["anchor"]
        rec = dict(code=code, board_date=c["board_date"], anchor=anchor,
                   scan_date=scan_date, scan_close=c["close"],
                   vol_ratio_scan=c["vol_ratio_vs_board"], updated=time.strftime("%Y-%m-%d %H:%M:%S"))
        entry = None
        for d, o, h, l, cl, v in rows:
            yang = cl > o
            in_zone = anchor <= cl <= anchor * 1.03
            if entry is None and yang and in_zone:
                rec["d1_confirmed_date"] = d
                entry = {"date": d, "open": o}
            if entry and "entry_next_close" not in entry and d > entry["date"]:
                entry["entry_next_close"] = cl
            if cl < anchor:
                rec["stop_hit_date"] = rec.get("stop_hit_date") or d
        if entry:
            rec["entry_date"] = entry["date"]
            rec["entry_open"] = entry["open"]
            if "entry_next_close" in entry:
                rec["ret_since_entry_pct"] = round((entry["entry_next_close"] / entry["open"] - 1) * 100, 2)
        rec["days_fwd_available"] = len(rows)
        rec["status"] = ("stopped" if rec.get("stop_hit_date")
                         else ("entry_done" if entry else "watching"))
        out.append(rec)
    conn.close()

    old = []
    if os.path.exists(LEDGER):
        for ln in open(LEDGER, encoding="utf-8"):
            try:
                r = json.loads(ln)
                if r.get("scan_date") != scan_date:
                    old.append(r)
            except Exception:
                pass
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in old:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("[%s] 台账回填 %d 行 -> %s (幂等覆盖 scan_date=%s)" % (
        time.strftime("%H:%M:%S"), len(out), LEDGER, scan_date))
    for r in out:
        print(" %s %s status=%s 确认日=%s 止损=%s" % (
            r["code"], "锚%.2f" % r["anchor"], r["status"],
            r.get("d1_confirmed_date", "-"), r.get("stop_hit_date", "-")))


if __name__ == "__main__":
    main()
