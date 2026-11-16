#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_pollution_guard.py — stock_daily_raw / stock_daily_meta 数据库层防污染护栏（V1）
================================================================
背景：2026-08-24~26 stock_daily_raw/meta/qfq 被 ≥65 只退市/停牌老股几千元假价污染，
     活跃写入进程未锁定（静态分析穷尽，见 reports/META_QFQ_POLLUTION_ESCALATION_20260826.md）。

机制：在 dragon_quant.db(stock_daily_raw) 与 dragon_quant_meta.db(stock_daily_meta)
     上安装 BEFORE INSERT / UPDATE 触发器：
       新 close 若【无法】被该股任意历史价落在 [close*0.02, close*6] 区间内容纳
       （即与全部历史价脱钩，如 0.18 元股出现 9000 元），则 RAISE(IGNORE) 丢弃该行
       并写入 pollution_guard_log 审计表——污染入库被数据库层硬拦，无需锁定进程。
     新股首日（无历史价）天然放行；极端除权（如 10 转 25 → 价 ÷26）因历史价区间宽容亦放行。

用法:
  python db_pollution_guard.py install     # 幂等安装两库触发器
  python db_pollution_guard.py selftest    # 回滚事务内自测（拦截/放行/审计）
  python db_pollution_guard.py status      # 查看审计表（被拦截记录）
"""
import sqlite3
import sys

MAIN_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
META_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db"

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS pollution_guard_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now','localtime')),
    tbl TEXT, op TEXT, code TEXT, date TEXT,
    attempted_close REAL, detail TEXT
);
"""

def _triggers(tbl):
    return [
        f"""CREATE TRIGGER IF NOT EXISTS trg_pguard_{tbl}_ins
BEFORE INSERT ON {tbl} FOR EACH ROW
WHEN NEW.close IS NOT NULL AND NEW.close > 0
  AND EXISTS (SELECT 1 FROM {tbl} p WHERE p.code = NEW.code AND p.close > 0)
  AND NOT EXISTS (
      SELECT 1 FROM {tbl} p
      WHERE p.code = NEW.code AND p.close > 0
        AND NEW.close BETWEEN p.close * 0.02 AND p.close * 6
  )
BEGIN
    INSERT INTO pollution_guard_log(tbl,op,code,date,attempted_close,detail)
    VALUES ('{tbl}','INSERT',NEW.code,CAST(NEW.date AS TEXT),NEW.close,'假价拦截:与全部历史价脱钩');
    SELECT RAISE(IGNORE);
END;""",
        f"""CREATE TRIGGER IF NOT EXISTS trg_pguard_{tbl}_upd
BEFORE UPDATE OF close ON {tbl} FOR EACH ROW
WHEN NEW.close IS NOT NULL AND NEW.close > 0
  AND EXISTS (SELECT 1 FROM {tbl} p WHERE p.code = NEW.code AND p.date <> NEW.date AND p.close > 0)
  AND NOT EXISTS (
      SELECT 1 FROM {tbl} p
      WHERE p.code = NEW.code AND p.date <> NEW.date AND p.close > 0
        AND NEW.close BETWEEN p.close * 0.02 AND p.close * 6
  )
BEGIN
    INSERT INTO pollution_guard_log(tbl,op,code,date,attempted_close,detail)
    VALUES ('{tbl}','UPDATE',NEW.code,CAST(NEW.date AS TEXT),NEW.close,'假价拦截:与全部历史价脱钩(update)');
    SELECT RAISE(IGNORE);
END;""",
    ]


def install():
    for db in (MAIN_DB, META_DB):
        con = sqlite3.connect(db, timeout=60)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(AUDIT_DDL)
        tbl = "stock_daily_raw" if db == MAIN_DB else "stock_daily_meta"
        for ddl in _triggers(tbl):
            con.execute(ddl)
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_pguard%'").fetchone()[0]
        print(f"{db.split('/')[-1]} [{tbl}]: 触发器 x{n}, 审计表 OK")
        con.close()


def selftest():
    """回滚事务内自测：污染行应被 IGNORE（不入表+留审计），正常行应通过。"""
    con = sqlite3.connect(MAIN_DB, timeout=60)
    con.execute(AUDIT_DDL)
    ok_ok = ok_rej = False
    con.execute("BEGIN")
    # 1) 模拟污染插入：600519 贵州茅台塞 99999 元（历史最高 ~1900，超出任何区间）
    try:
        before = con.execute("SELECT COUNT(*) FROM stock_daily_raw WHERE code='600519' AND date=20260101").fetchone()[0]
        con.execute(
            "INSERT OR REPLACE INTO stock_daily_raw (code,date,open,high,low,close,vol,amt) "
            "VALUES ('600519',20260101,99999,99999,99999,99999,1,1)")
        after = con.execute("SELECT COUNT(*) FROM stock_daily_raw WHERE code='600519' AND date=20260101").fetchone()[0]
        ok_rej = (after == before)   # 行未进入 → 已被 IGNORE
    except Exception as e:
        print("污染插入异常(应为 IGNORE 静默):", e)
    # 2) 模拟正常插入：600519 明日在历史带内的合理价（回滚不落库）
    try:
        con.execute(
            "INSERT OR REPLACE INTO stock_daily_raw (code,date,open,high,low,close,vol,amt) "
            "VALUES ('600519',20260101,1300,1310,1290,1300,100,10000)")
        cnt = con.execute("SELECT COUNT(*) FROM stock_daily_raw WHERE code='600519' AND date=20260101").fetchone()[0]
        ok_ok = cnt >= 1              # 行进入 → 正常放行
    except Exception as e:
        print("正常插入异常:", e)
    con.execute("ROLLBACK")
    print(f"自测: 假价拦截={'PASS' if ok_rej else 'FAIL'} | 正常放行={'PASS' if ok_ok else 'FAIL'}")
    con.close()
    return 0 if (ok_rej and ok_ok) else 1


def status():
    for db, tbl in ((MAIN_DB, "stock_daily_raw"), (META_DB, "stock_daily_meta")):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT ts,tbl,op,code,date,attempted_close FROM pollution_guard_log "
            "WHERE tbl=? ORDER BY id DESC LIMIT 10", (tbl,)).fetchall()
        total = con.execute("SELECT COUNT(*) FROM pollution_guard_log WHERE tbl=?", (tbl,)).fetchone()[0]
        print(f"[{tbl}] 近期拦截 {len(rows)} / 累计 {total}:")
        for r in rows:
            print("   ", r)
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        sys.exit(install())
    elif cmd == "selftest":
        sys.exit(selftest())
    elif cmd == "status":
        sys.exit(status())
    else:
        print(__doc__)
        sys.exit(2)
