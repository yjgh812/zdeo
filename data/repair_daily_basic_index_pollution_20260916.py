# -*- coding: utf-8 -*-
"""
repair_daily_basic_index_pollution_20260916.py — daily_basic 指数值污染外科修复（一次性）
事件: 20260521~20260528 六个交易日, 87只深市000/001段股票的 daily_basic 行被写入
      指数点位值(双义码解析错误, 如 000001.SZ close=4077.28=上证指数当日收盘)。
口径: 与 backfill_daily_basic_hist_2020_2024.py 完全一致(baostock 不复权 close/turn/
      peTTM/pbMRQ/psTTM, 股本取自本表最近干净日, mv=close×share, 其余0.0),
      INSERT OR REPLACE 幂等。
用法: python repair_daily_basic_index_pollution_20260916.py --apply   (缺省 dry-run)
"""
import json, socket, sqlite3, sys, time
from pathlib import Path

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
BACKUP = Path(r"D:\zcodesj\data\_daily_basic_pollution_backup_20260521_28.json")
APPLY = "--apply" in sys.argv

con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
bad = pd = __import__("pandas").read_sql(
    "SELECT ts_code, trade_date, close, turnover_rate, volume_ratio, total_share, float_share, total_mv "
    "FROM daily_basic WHERE (substr(ts_code,1,3)='000' OR substr(ts_code,1,3)='001') AND close>1000 "
    "ORDER BY ts_code, trade_date", con)
codes = sorted(bad.ts_code.unique())
dates = sorted(bad.trade_date.unique())
print(f"[scan] 坏行 {len(bad)} 行 / {len(codes)} 只 / 日期 {dates}")
sample = con.execute("SELECT * FROM daily_basic WHERE ts_code='000001.SZ' AND trade_date='20260521'").fetchone()
print("[sample坏行]", sample)
share_rows = {}
for c in codes:
    r = con.execute("SELECT total_share, float_share FROM daily_basic WHERE ts_code=? AND trade_date<'20260521' "
                    "AND total_share>0 ORDER BY trade_date DESC LIMIT 1", (c,)).fetchone()
    share_rows[c] = r if r else (0.0, 0.0)
con.close()

BACKUP.write_text(json.dumps({"event": "20260521-28 指数值污染", "rows": bad.to_dict("records")},
                             ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[backup] {len(bad)} 行已备份 -> {BACKUP}")
if not APPLY:
    print("[dry-run] 待修复", len(codes), "只; 退出(--apply 生效)")
    sys.exit(0)

socket.setdefaulttimeout(30)
import baostock as bs
lg = bs.login()
if lg.error_code != "0":
    sys.exit(f"baostock 登录失败: {lg.error_msg}")
wcon = sqlite3.connect(FUND_DB, timeout=60)
wcon.execute("PRAGMA journal_mode=WAL")
wcon.execute("PRAGMA busy_timeout=60000")
n = 0
consec = 0
t0 = time.time()
try:
    for i, c in enumerate(codes, 1):
        rs = None
        for attempt in range(3):
            try:
                rs = bs.query_history_k_data_plus(
                    "sz." + c[:6], "date,close,peTTM,pbMRQ,psTTM,turn",
                    start_date="2026-05-21", end_date="2026-05-28", frequency="d", adjustflag="3")
            except Exception:
                rs = None
            if rs is not None and rs.error_code == "0":
                break
            time.sleep(3 * (attempt + 1))
        if rs is None or rs.error_code != "0":
            consec += 1
            print(f"[skip] {c} 拉取失败", flush=True)
            if consec >= 10:
                print(f"[abort] 连续 {consec} 只失败, 熔断退出(网络异常, 稍后重跑幂等)", flush=True)
                break
            if consec >= 3:
                try:
                    bs.logout()
                except Exception:
                    pass
                lg = bs.login()
                consec = 0
            continue
        consec = 0
        tot_sh, flt_sh = share_rows[c]
        rows = []
        while rs.next():
            d, close, pe, pb, ps, turn = rs.get_row_data()
            if not close:
                continue
            close = float(close)
            if close > 1000:
                print(f"[guard] {c} {d} 源数据仍>1000({close}), 跳过该行", flush=True)
                continue
            rows.append((c, d.replace("-", ""), close,
                         float(turn) if turn else 0.0, 0.0, 0.0, 0.0,
                         float(pe) if pe else 0.0, float(pb) if pb else 0.0,
                         0.0, float(ps) if ps else 0.0, 0.0, 0.0,
                         tot_sh, flt_sh, 0.0, close * tot_sh, close * flt_sh))
        if rows:
            wcon.executemany("INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        if i % 30 == 0:
            wcon.commit()
            print(f"[repair] {i}/{len(codes)} 已写 {n} 行 {time.time()-t0:.0f}s", flush=True)
    wcon.commit()
finally:
    try:
        bs.logout()
    except Exception:
        pass
    wcon.close()
    socket.setdefaulttimeout(None)
print(f"[done] 修复写入 {n} 行 耗时 {time.time()-t0:.0f}s")
