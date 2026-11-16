"""晨间修复点自验（一次性任务，2026-09-23 09:45）。

核验 9/22-9/23 修复轮的两个自然验证点：
1. baostock_daily.db：9/22 应已由 07:30 稳定窗任务补完（>=4000 只）
2. erp_history.db：9:10 链应已运行，最新日期 >= 9/21（9/22=全过；9/21=链活但乐咕晨间抓取失败回落，属已知行为）
结果写 logs/morning_verify_20260923.log；任一 FAIL 以退出码 1 暴露。
"""
import sqlite3
import logging
from pathlib import Path
from datetime import date

LOG = Path("D:/zcodesj/logs/morning_verify_20260923.log")
LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG), level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")

fails = []

# 1) baostock 9/22 补完验收
con = sqlite3.connect("file:D:/workbuddyspace/data/baostock/baostock_daily.db?mode=ro", uri=True)
n22 = con.execute("SELECT COUNT(*) FROM daily WHERE date='2026-09-22'").fetchone()[0]
n23 = con.execute("SELECT COUNT(*) FROM daily WHERE date='2026-09-23'").fetchone()[0]
con.close()
logging.info("baostock 9/22 rows=%d, 9/23 rows=%d (9/23为今日盘中,期望0)", n22, n23)
if n22 >= 4000:
    logging.info("CHECK1 baostock 9/22: PASS")
else:
    fails.append(f"baostock 9/22 rows={n22} < 4000 (07:30任务未补完?)")
    logging.error("CHECK1 baostock 9/22: FAIL")

# 2) erp_history 链活性验收
con = sqlite3.connect("file:D:/zcodesj/erp_history.db?mode=ro", uri=True)
tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
latest = None
for t in tabs:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
    dcol = next((c for c in cols if c.lower() in ("date", "trade_date", "dt")), None)
    if dcol:
        v = con.execute(f"SELECT MAX({dcol}) FROM {t}").fetchone()[0]
        if v and (latest is None or str(v) > str(latest)):
            latest = v
con.close()
logging.info("erp_history latest=%s (tables=%s)", latest, tabs)
s = str(latest)
if "2026-09-22" in s or "20260922" in s:
    logging.info("CHECK2 erp_history: PASS (9/22当日值已落)")
elif "2026-09-21" in s or "20260921" in s:
    logging.warning("CHECK2 erp_history: SOFT-PASS (链活但9:10乐咕晨间抓取失败回落本地,已知行为)")
else:
    fails.append(f"erp_history latest={latest} 过旧,9:10链可能未恢复")
    logging.error("CHECK2 erp_history: FAIL")

logging.info("RESULT: %s", "ALL PASS" if not fails else f"FAIL x{len(fails)}: {fails}")
raise SystemExit(1 if fails else 0)
