# -*- coding: utf-8 -*-
"""delisted_backfill_20261029.py — 含退市股全量行情库 Phase1 回填（立项执行）

主人令 2026-10-29「含退市股全量行情库立项」。
事实基础（勿重复论证，均已在案）:
  - 10/11 评估 reports/delist_source_eval_20261011: baostock 唯一保留退市股全区间K到
    退市日（6样本 retained_full=true）; tushare daily 有缺口; 腾讯 ifzq 全无。
  - qfq 主库已含 ~347 只退市股的历史段（qfq_delisted_by_year 1999-2026 分布在案）,
    尾部可能带数据链占位行（close 平推 amount=0, 如 000024 招商地产 20260624-26 三行）。
  - tushare stock_basic 本账户限频 1次/小时（评估 v2 已定谳）, 不可作清单源。
  - qfq 主库=daily_chain_update 生成物, 禁手改（宪法 §5）; 幸存者偏差主体=qfq 完全
    缺席的退市股（评估标注"不可计缺失"族）。

本件设计:
  1. 清单源=baostock query_stock_basic 全枚举（STK_PREFIX 码集, outDate 非空=退市）
  2. 回填对象=qfq 主表完全缺席的码（已在库的跳过并登记——其历史段已可用）
  3. 数据源=baostock query_history_k_data_plus adjustflag=3 不复权原始价 + 复权因子,
     写入独立库 dragon_quant_delisted.db（不触碰生成物主库; 本库自带 baostock 因子
     口径自洽, 与主 qfq 通达信口径差异在表注如实标注, 跨库合并回测时只用本库内部
     涨跌幅复权自洽价）
  4. 幂等: INSERT OR IGNORE + 断点续传 delisted_backfill_done.json
边界: baostock 数据起始约 2005 年前不全; 只补日线不补分钟线; 本库只读供回放, 禁接线生产。
"""
from __future__ import annotations
import json, os, sys, time, sqlite3

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEL_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_delisted.db"
QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
DONE_FILE = r"D:\zcodesj\data\delisted_backfill_done.json"
STK_PREFIX = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605")
SLEEP = 0.30  # baostock 限速保护


def bs_login():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login fail {lg.error_code} {lg.error_msg}")
    return bs


def roster_delisted(bs) -> list:
    """baostock 全枚举退市股清单: [(code6, name, ipo, outDate), ...]"""
    out = []
    rs = bs.query_stock_basic()
    while rs.error_code == "0" and rs.next():
        r = dict(zip(rs.fields, rs.get_row_data()))
        code = r.get("code", "")
        if code[3:6] not in STK_PREFIX:
            continue
        if r.get("outDate") and r.get("status") == "0":
            out.append((code[3:], r.get("code_name", "?"), r.get("ipoDate", ""),
                        r.get("outDate", "")))
    return out


def ensure_db(con: sqlite3.Connection):
    con.execute("""create table if not exists stock_daily_delisted (
        code text not null, date integer not null,
        open real, high real, low real, close real,
        volume real, amount real, pct_chg real,
        adj_factor real, out_date text,
        primary key(code, date))""")
    con.execute("""create table if not exists roster (
        code text primary key, name text, ipo_date text, out_date text,
        in_qfq int, backfilled int, bars int, ts text)""")
    con.commit()


def fetch_k(bs, full: str) -> list:
    """全史日K(不复权) + 复权因子, 返回 [(code6,date,o,h,l,c,vol,amt,pct,af,outdate)]"""
    meta = {}
    b = bs.query_stock_basic(code=full)
    while b.error_code == "0" and b.next():
        meta = dict(zip(b.fields, b.get_row_data()))
    outd = meta.get("outDate", "")
    rs = bs.query_history_k_data_plus(
        full, "date,open,high,low,close,volume,amount,pctChg",
        start_date="1990-01-01", end_date=outd or "", frequency="d", adjustflag="3")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    af = {}
    ra = bs.query_adjust_factor(code=full, start_date="1990-01-01", end_date=outd or "")
    while ra.error_code == "0" and ra.next():
        d = dict(zip(ra.fields, ra.get_row_data()))
        af[d["dividOperateDate"].replace("-", "")] = d["adjustFactor"]
    code6 = full[3:]
    recs = []
    for r in rows:
        try:
            recs.append((code6, int(r[0].replace("-", "")),
                         float(r[1]) if r[1] else None, float(r[2]) if r[2] else None,
                         float(r[3]) if r[3] else None, float(r[4]) if r[4] else None,
                         float(r[5]) if r[5] else None, float(r[6]) if r[6] else None,
                         float(r[7]) if r[7] else None,
                         float(af[r[0].replace("-", "")]) if r[0].replace("-", "") in af else None,
                         outd))
        except (ValueError, IndexError):
            continue
    return recs


def main(limit: int = 0, sample: bool = False):
    bs = bs_login()
    con = sqlite3.connect(DEL_DB)
    con.execute("pragma journal_mode=wal")
    ensure_db(con)

    qfq_codes = {r[0] for r in sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
                 .execute("select distinct code from stock_daily_meta_qfq").fetchall()}
    roster = roster_delisted(bs)
    print(f"baostock 退市股清单 {len(roster)} 只; qfq主表 {len(qfq_codes)} 码")
    todo = [(c, n, i, o) for c, n, i, o in roster if c not in qfq_codes]
    absent = len(todo)
    print(f"完全缺席qfq的退市股(回填对象) {absent} 只; 已在库 {len(roster)-absent} 只(跳过)")
    if sample:
        todo = todo[:5]
    if limit:
        todo = todo[:limit]

    done = {}
    if os.path.exists(DONE_FILE):
        done = json.load(open(DONE_FILE, encoding="utf-8"))

    ok, skip, fail = 0, 0, []
    for i, (code, name, ipo, outd) in enumerate(todo):
        if code in done:
            skip += 1
            continue
        full = ("sh." if code[0] == "6" else "sz.") + code
        try:
            recs = fetch_k(bs, full)
            con.executemany("insert or ignore into stock_daily_delisted values (?,?,?,?,?,?,?,?,?,?,?)", recs)
            con.execute("insert or replace into roster values (?,?,?,?,?,?,?,?)",
                        (code, name, ipo, outd, 0, 1, len(recs), time.strftime("%Y%m%d %H:%M")))
            con.commit()
            done[code] = {"name": name, "out": outd, "bars": len(recs),
                          "ts": time.strftime("%Y%m%d %H:%M")}
            ok += 1
            if ok % 10 == 0:
                json.dump(done, open(DONE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"  进度 {i+1}/{len(todo)} 完成{ok} 跳过{skip} 失败{len(fail)}")
        except Exception as e:
            fail.append((code, name, f"{type(e).__name__}: {str(e)[:120]}"))
        time.sleep(SLEEP)

    json.dump(done, open(DONE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = con.execute("select count(*), count(distinct code) from stock_daily_delisted").fetchone()
    nro = con.execute("select count(*) from roster where backfilled=1").fetchone()[0]
    print(f"\n回填完成: 成功{ok} 跳过{skip} 失败{len(fail)}; 库内 {n[1]} 只 / {n[0]} 行; roster {nro} 只")
    if fail:
        print("失败清单(前10):")
        for f in fail[:10]:
            print(f"  {f[0]} {f[1]}: {f[2]}")
        with open(DONE_FILE.replace(".json", "_fail.json"), "w", encoding="utf-8") as fp:
            json.dump(fail, fp, ensure_ascii=False, indent=1)
    bs.logout()
    con.close()


if __name__ == "__main__":
    limit = 0
    sample = "--sample" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = int(a.split("=")[1])
    main(limit, sample)
