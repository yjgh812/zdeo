# -*- coding: utf-8 -*-
"""v3: westock OAuth 官方通道重建干净成交台账 fills_clean.db
过滤 status='已成交'（trade_records 撤单污染根治）；翻页+幂等+限频退避。
用法: python fetch_fills_official.py [起始月YYYYMM=202601]
"""
import sys, os, time, sqlite3, json

sys.path.insert(0, r"D:/workbuddyspace")
sys.path.insert(0, r"D:/zcodesj")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from westock_client import WeStockClient

DB = r"D:\zcodesj\data\fills_clean.db"
START_MONTH = sys.argv[1] if len(sys.argv) > 1 else "202601"

def month_iter(start):
    y, m = int(start[:4]), int(start[4:6])
    cur = (y, m)
    while cur <= (2026, 9):
        yield f"{cur[0]}{cur[1]:02d}"
        m += 1
        if m > 12:
            y += 1; m = 1
        cur = (y, m)

con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS fills(
    order_id TEXT PRIMARY KEY, code TEXT, name TEXT, action TEXT,
    price REAL, quantity INTEGER, amount REAL, filled_at TEXT, month TEXT)""")
done_months = {r[0] for r in con.execute("SELECT DISTINCT month FROM fills")}
cli = WeStockClient()
total = 0
for mm in month_iter(START_MONTH):
    if mm in done_months:
        print(f"[SKIP] {mm} 已入库")
        continue
    offset, n_all = 0, 0
    while True:
        for attempt in range(3):
            try:
                resp = cli.call("portfolio_paper_history",
                                {"range": "history", "month": mm, "limit": 200, "offset": offset})
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f"  重试 {mm} off={offset}: {str(e)[:60]}")
                time.sleep(5)
    # 解析
        data = resp.get("data") or {}
        recs = data.get("records") or []
        for r in recs:
            if r.get("status") != "已成交":
                continue
            fa = r.get("filledAt", "")
            con.execute("INSERT OR REPLACE INTO fills VALUES (?,?,?,?,?,?,?,?,?)",
                        (r["orderId"], r["code"], r.get("name"), r["direction"],
                         float(r["price"]), int(r["quantity"]), float(r["amount"]), fa, mm))
            n_all += 1
        con.commit()
        if not data.get("hasMore"):
            break
        offset += 200
        time.sleep(0.5)
    total += n_all
    print(f"[{mm}] 已成交 {n_all} 条")
    time.sleep(1.0)
con.commit()
n = con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
con.close()
print(f"[DONE] fills_clean.db 共 {n} 条已成交 (本轮新增 {total})")
