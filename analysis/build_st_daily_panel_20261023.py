# -*- coding: utf-8 -*-
"""
build_st_daily_panel_20261023.py — ST 历史日频面板构建（PD-049 提案 m1，来源替代定案）
=====================================================================
主人 2026-10-23 令「没有 ftshare API Key，找替代」→ 源定案 = baostock
query_history_k_data_plus 的 isST 披露字段（非价格启发式；baostock 为
PROJECT_BOUNDARY L89 在册取数适配层，项目已有日更管线，零 Key）。

验证锚（2026-10-23 实测三源互证）：sh.600079 isST 于 20251216 翻转 0→1，
与 ftshare 区间 start_date=20251216、本地原始日线 ±5% 板签名切换日逐日一致。

产出: data/st_history.db
  st_daily(code TEXT, date INT, is_st INT, PK(code,date))  — 20160101 起全 A（含退市）
  pull_done(code TEXT PK, pulled_at TEXT)                  — 断点续传
  pull_failed(code TEXT PK, err TEXT, ts TEXT)             — 失败重试清单
  meta(key TEXT PK, value TEXT)
幂等: 全表 INSERT OR REPLACE；重跑自动跳过 pull_done 已完成票。
运行: python analysis/build_st_daily_panel_20261023.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime

import baostock as bs

DB_PATH = r"D:\zcodesj\data\st_history.db"
WINDOW_START = "2016-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")
COMMIT_EVERY = 100
RELOGIN_EVERY = 800


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS st_daily(
            code TEXT NOT NULL, date INTEGER NOT NULL, is_st INTEGER NOT NULL,
            PRIMARY KEY(code, date));
        CREATE TABLE IF NOT EXISTS pull_done(code TEXT PRIMARY KEY, pulled_at TEXT);
        CREATE TABLE IF NOT EXISTS pull_failed(code TEXT PRIMARY KEY, err TEXT, ts TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.commit()


def enumerate_stocks() -> list[str]:
    """本地宇宙枚举（2026-10-23 晚补丁）：baostock query_stock_basic 服务端流式响应
    今晚持续停滞（登录成功但枚举挂死，实测 20:06-21:15 多轮复现），改从本地两库取并集：
    baostock_daily.db daily(4,862 票, 裸 6 位码) ∪ delisted_segments_baostock.db
    stock_daily_delisted_raw(退市票, sh./sz. 前缀码)。归一化为 baostock 格式。"""
    codes: set[str] = set()
    con = sqlite3.connect(r"file:D:/workbuddyspace/data/baostock/baostock_daily.db?mode=ro", uri=True)
    for (c,) in con.execute("SELECT DISTINCT code FROM daily"):
        c6 = str(c)[-6:]
        if not c6.isdigit():
            continue
        codes.add(("sh." if c6.startswith("6") else "sz.") + c6)
    con.close()
    con = sqlite3.connect(r"file:D:\zcodesj\data\delisted_segments_baostock.db?mode=ro", uri=True)
    for (c,) in con.execute("SELECT DISTINCT code FROM stock_daily_delisted_raw"):
        c = str(c)
        if c.startswith(("sh.", "sz.")):
            codes.add(c)
    con.close()
    return sorted(codes)


def relogin() -> None:
    try:
        bs.logout()
    except Exception:
        pass
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")


def main() -> None:
    # 看门狗（照搬 baostock_daily_update.py 模式）: baostock socket 无超时，
    # 服务端抖动时 login/查询会永久挂起（2026-10-23 20:08 实测复现），7200s 强杀
    # 以非零退出码暴露，断点续传重跑即续。
    import os
    import threading

    def _watchdog() -> None:
        print("WATCHDOG fired after 14400s, hard exit", flush=True)
        os._exit(3)

    t = threading.Timer(14400, _watchdog)
    t.daemon = True
    t.start()

    con = sqlite3.connect(DB_PATH)
    init_db(con)
    done = {r[0] for r in con.execute("SELECT code FROM pull_done")}
    relogin()
    stocks = enumerate_stocks()
    todo = [c for c in stocks if c not in done]
    con.execute("INSERT OR REPLACE INTO meta VALUES('source','baostock isST')")
    con.execute("INSERT OR REPLACE INTO meta VALUES('window_start',?)", (WINDOW_START,))
    con.execute("INSERT OR REPLACE INTO meta VALUES('built_at',?)",
                (datetime.now().isoformat(timespec="seconds"),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('universe_n',?)", (str(len(stocks)),))
    con.commit()
    print(f"[1/2] 宇宙={len(stocks)} 已完成={len(done)} 本轮待拉={len(todo)}", flush=True)

    n_ok = n_fail = n_rows = 0
    t0 = time.time()
    for i, code in enumerate(todo, 1):
        try:
            rs = bs.query_history_k_data_plus(
                code, "date,isST", start_date=WINDOW_START, end_date=END_DATE,
                frequency="d", adjustflag="3")
            rows = []
            while rs.error_code == "0" and rs.next():
                data = rs.get_row_data()
                d, flag = data[0], data[1]
                if d and flag != "":
                    rows.append((code, int(d.replace("-", "")), int(flag)))
            if rs.error_code != "0":
                raise RuntimeError(f"{rs.error_code} {rs.error_msg}")
            con.executemany("INSERT OR REPLACE INTO st_daily VALUES(?,?,?)", rows)
            con.execute("INSERT OR REPLACE INTO pull_done VALUES(?,?)",
                        (code, datetime.now().isoformat(timespec="seconds")))
            con.execute("DELETE FROM pull_failed WHERE code=?", (code,))
            n_ok += 1
            n_rows += len(rows)
        except Exception as e:  # noqa: BLE001 — 单票失败入重试清单不中断整批
            n_fail += 1
            con.execute("INSERT OR REPLACE INTO pull_failed VALUES(?,?,?)",
                        (code, str(e)[:200], datetime.now().isoformat(timespec="seconds")))
        time.sleep(0.3)  # 限速(照抄日更器0.3s; 2026-10-23黑名单事故根因修补, 无sleep连续查询触发10001011)
        if i % COMMIT_EVERY == 0:
            con.commit()
            print(f"  进度 {i}/{len(todo)} ok={n_ok} fail={n_fail} rows={n_rows} "
                  f"({time.time()-t0:.0f}s)", flush=True)
        if i % RELOGIN_EVERY == 0:
            relogin()
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM st_daily").fetchone()[0]
    st1 = con.execute("SELECT COUNT(*) FROM st_daily WHERE is_st=1").fetchone()[0]
    con.execute("INSERT OR REPLACE INTO meta VALUES('total_rows',?)", (str(total),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('st1_rows',?)", (str(st1),))
    con.commit()
    con.close()
    print(f"[2/2] 完成 ok={n_ok} fail={n_fail} 本轮rows={n_rows} "
          f"面板总行={total} ST行={st1} 用时{time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
