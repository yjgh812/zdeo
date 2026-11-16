"""同花顺行业板块指数库（ths_sector.db）— 东财 push2his 被封后的板块日K替代源。

设计红线：THS 数值只进独立表，绝不混写 eastmoney_quant 的 BK 键表（两家指数口径不同）。

表：
  ths_boards(ths_code PK, ths_name)            同花顺行业名单
  bk_ths_map(bk_code PK, bk_name, ths_code, ths_name, match_type)  东财BK↔THS映射(exact/alias/unmatched)
  ths_kline(ths_code, trade_date, open, high, low, close, volume, amount, PK(ths_code,trade_date))

用法：
  python ths_sector_update.py                 # 日更：刷名单+映射+各板块增量K线
  python ths_sector_update.py --backfill 20200101  # 深补数：从指定日期拉全量
数据源：akshare stock_board_industry_*_ths / _em（免Key直连，已实测）。
"""
import argparse
import logging
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

import akshare as ak

os.environ.setdefault("TQDM_DISABLE", "1")  # 压掉 akshare 内部进度条刷屏

DB_PATH = "D:/zcodesj/data/ths_sector.db"
DEFAULT_BACKFILL_START = "20200101"
LOG_DIR = Path("D:/zcodesj/logs")

# 人工别名表（自动规范化后仍对不上的，左=东财BK名，右=同花顺名；随运行证据持续补充）
ALIAS = {
    "种植业": "种植业与林业",
}

_SUFFIX = re.compile(r"[ⅠⅡⅢ]+$")


def norm(name: str) -> str:
    return _SUFFIX.sub("", str(name).replace(" ", "").replace("　", ""))


def get_conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS ths_boards (
                        ths_code TEXT PRIMARY KEY, ths_name TEXT, module TEXT)""")
    try:
        conn.execute("ALTER TABLE ths_boards ADD COLUMN module TEXT DEFAULT 'industry'")
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.execute("""CREATE TABLE IF NOT EXISTS bk_ths_map (
                        bk_code TEXT PRIMARY KEY, bk_name TEXT,
                        ths_code TEXT, ths_name TEXT, match_type TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ths_kline (
                        ths_code TEXT, trade_date TEXT,
                        open REAL, high REAL, low REAL, close REAL,
                        volume REAL, amount REAL,
                        PRIMARY KEY (ths_code, trade_date))""")
    conn.commit()
    return conn


# 模块注册表：(module, 名单函数, K线函数)
MODULES = [
    ("industry", ak.stock_board_industry_name_ths, ak.stock_board_industry_index_ths),
    ("concept", ak.stock_board_concept_name_ths, ak.stock_board_concept_index_ths),
]


def refresh_lists_and_mapping(conn) -> dict:
    ths_map = {}
    for module, list_fn, _ in MODULES:
        df = list_fn()
        for _, r in df.iterrows():
            code, name = str(r["code"]), str(r["name"])
            ths_map[norm(name)] = (code, name)
            conn.execute(
                "INSERT OR REPLACE INTO ths_boards VALUES (?,?,?)", (code, name, module))
    conn.commit()
    stats = {"ths": len(ths_map), "em": 0, "exact": 0, "alias": 0, "unmatched": []}
    # 东财BK名单走 push2 家族（本机被间歇封锁）；失败时保留已有映射，下次再补
    try:
        em = ak.stock_board_industry_name_em()        # 板块名称, 板块代码, ...
    except Exception as e:
        logging.warning("EM industry list unavailable (%s), mapping deferred", str(e)[:80])
        return stats
    name_col = "板块名称" if "板块名称" in em.columns else em.columns[1]
    code_col = "板块代码" if "板块代码" in em.columns else em.columns[2]
    for _, r in em.iterrows():
        bk_name, bk_code = str(r[name_col]), str(r[code_col])
        stats["em"] += 1
        hit = ths_map.get(norm(bk_name))
        mtype = None
        ths_hit = None
        if hit:
            ths_hit, mtype = hit, "exact"
            stats["exact"] += 1
        elif norm(bk_name) in ALIAS and ths_map.get(norm(ALIAS[norm(bk_name)])):
            ths_hit = ths_map[norm(ALIAS[norm(bk_name)])]
            mtype = "alias"
            stats["alias"] += 1
        else:
            stats["unmatched"].append(bk_name)
        conn.execute(
            "INSERT OR REPLACE INTO bk_ths_map VALUES (?,?,?,?,?)",
            (bk_code, bk_name, ths_hit[0] if ths_hit else None,
             ths_hit[1] if ths_hit else None, mtype))
    conn.commit()
    return stats


def update_klines(conn, start: str, incremental: bool) -> dict:
    kline_fns = {m: fn for m, _, fn in MODULES}
    rows = conn.execute(
        "SELECT ths_code, ths_name, COALESCE(module,'industry') FROM ths_boards").fetchall()
    end = _kline_end()
    saved = failed = 0
    for i, (code, name, module) in enumerate(rows, 1):
        if not name:
            continue
        fn = kline_fns.get(module)
        if fn is None:
            continue
        eff_start = start
        if incremental:
            md = conn.execute(
                "SELECT MAX(trade_date) FROM ths_kline WHERE ths_code=?", (code,)).fetchone()[0]
            if md:
                y, m, d = md.split("-")
                eff_start = f"{y}{m}{d}"
        try:
            df = fn(symbol=name, start_date=eff_start, end_date=end)
            if df is None or df.empty:
                time.sleep(0.3)
                continue
            recs = [(code, str(r["日期"]),
                     float(r["开盘价"]), float(r["最高价"]), float(r["最低价"]),
                     float(r["收盘价"]), float(r["成交量"]), float(r["成交额"]))
                    for _, r in df.iterrows()]
            conn.executemany(
                "INSERT OR REPLACE INTO ths_kline VALUES (?,?,?,?,?,?,?,?)", recs)
            conn.commit()
            saved += len(recs)
        except Exception as e:
            failed += 1
            logging.warning("kline %s(%s) fail: %s", code, name, str(e)[:120])
        if i % 20 == 0:
            logging.info("kline progress %d/%d saved=%d failed=%d", i, len(rows), saved, failed)
        time.sleep(0.35)
    return {"boards": len(rows), "rows_saved": saved, "failed": failed}


def _kline_end() -> str:
    """收盘守卫：15:05 前（盘中/午间/盘前）调用时 end 收到昨日，杜绝写入未收盘 bar。"""
    now = date.today()
    if now.weekday() < 5:
        import datetime as _dt
        moment = _dt.datetime.now()
        if moment.hour * 60 + moment.minute < 15 * 60 + 5:
            return (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y%m%d")
    return now.strftime("%Y%m%d")


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(LOG_DIR / "ths_sector_update.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(_fh)
    logging.getLogger().setLevel(logging.INFO)

    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", metavar="YYYYMMDD", default=None,
                    help="深补数起始日期（缺省=日增模式）")
    args = ap.parse_args()

    conn = get_conn()
    stats = refresh_lists_and_mapping(conn)
    logging.info("lists: ths=%(ths)s em=%(em)s exact=%(exact)s alias=%(alias)s "
                 "unmatched=%(unmatched)s", stats)
    k = update_klines(conn, args.backfill or DEFAULT_BACKFILL_START,
                      incremental=args.backfill is None)
    logging.info("kline: %s", k)
    conn.close()
    return 0 if k["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
