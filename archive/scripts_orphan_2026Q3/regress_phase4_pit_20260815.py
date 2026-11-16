#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""regress_phase4_pit_20260815.py — Phase4 PIT修复回归测试（不改生产库）

对比 修复前(D:\zcodesj\archive\tulong_daily_update.py.bak_20260815_prePIT)
   vs 修复后(D:\workbuddyspace\tulong_daily_update.py 生产文件) 的
phase4_update_fundamental_factors，在临时库上跑真实函数（importlib加载两版模块，
monkeypatch DB_MAIN 指向临时库）：

  场景A(20260814增量): 旧12列语义逐行全等；schema迁移+历史回填不改旧列；
        新列 pit_avail_date=法定披露截止；幂等重跑无变化；兜底UPDATE可修复缺失pit。
  场景B(2022-07-01前视铁证复现): 2022-07-01 当日 report_date 仍=20220630
        （旧as-of语义必须不变），但 pit_avail_date=20220831 → PIT消费方按
        pit<=T 过滤即无前视。

通过标准: 全部断言通过, exit 0。
"""
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

WB = r"D:\workbuddyspace"
sys.path.insert(0, WB)  # db_connect
OLD_FILE = r"D:\zcodesj\archive\tulong_daily_update.py.bak_20260815_prePIT"
NEW_FILE = WB + r"\tulong_daily_update.py"
MAIN_DB = Path(WB) / "factor_engine" / "data" / "dragon_quant.db"
TMP = Path(r"D:\zcodesj\tmp")
TMP.mkdir(exist_ok=True)

FAILED = []


def check(name, ok, detail=""):
    print("  [%s] %s %s" % ("PASS" if ok else "FAIL", name, detail), flush=True)
    if not ok:
        FAILED.append(name)


def load_module(path, name):
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 独立参考映射（与 build_factor_pit_20260815.py 同口径, 故意重写以交叉验证生成端）
def ref_pit(rd):
    y, md = rd // 10000, rd % 10000
    if md == 331:
        return y * 10000 + 430
    if md == 630:
        return y * 10000 + 831
    if md == 930:
        return y * 10000 + 1031
    if md == 1231:
        return (y + 1) * 10000 + 430
    raise AssertionError("非标准报告期末 %d" % rd)


# ────────────────────────────────────────────────────────────
# 临时库构建（schema与生产一致; 数据从生产库只读抽样）
# ────────────────────────────────────────────────────────────
DDL = """
CREATE TABLE stock_factors_fundamental (
    code TEXT NOT NULL, date INTEGER NOT NULL, report_date INTEGER,
    close REAL, ep REAL, bp REAL, sp REAL, roe REAL, lev REAL, ato REAL,
    pm REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_financial (
    id INTEGER PRIMARY KEY, code VARCHAR(10) NOT NULL, report_date INT,
    roe REAL, bps REAL, gross_margin REAL, net_margin REAL,
    total_shares REAL, revenue_ttm REAL
);
CREATE TABLE stock_daily (
    code TEXT NOT NULL, date INTEGER NOT NULL, close REAL, vol REAL, amt REAL,
    PRIMARY KEY (code, date)
);
"""


def pick_codes(n=40):
    src = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True)
    rows = src.execute("""
        SELECT DISTINCT f.code FROM stock_financial f
        WHERE f.report_date IN (20211231, 20220331, 20220630)
        GROUP BY f.code HAVING COUNT(DISTINCT f.report_date) = 3
    """).fetchall()
    codes_all = [r[0] for r in rows]
    step = max(1, len(codes_all) // n)
    picked = codes_all[::step][:n]
    src.close()
    return picked


def build_db(dest, codes, daily_lo, daily_hi, ff_seed_date, ff_seed_rd):
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(dest)
    conn.executescript(DDL)
    src = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True)
    ph = ",".join("?" * len(codes))
    # 财务全量(含未来报告期, 用于暴露bisect前视边界)
    rows = src.execute("""
        SELECT id, code, report_date, roe, bps, gross_margin, net_margin,
               total_shares, revenue_ttm
        FROM stock_financial WHERE code IN (%s) AND roe IS NOT NULL
        ORDER BY code, report_date""" % ph, codes).fetchall()
    conn.executemany("INSERT INTO stock_financial VALUES (?,?,?,?,?,?,?,?,?)", rows)
    # 日线窗口
    rows = src.execute("""
        SELECT code, date, close, vol, amt FROM stock_daily
        WHERE code IN (%s) AND date BETWEEN ? AND ? ORDER BY code, date""" % ph,
        codes + [daily_lo, daily_hi]).fetchall()
    conn.executemany("INSERT INTO stock_daily VALUES (?,?,?,?,?)", rows)
    # 预置历史ff行(模拟"昨日已跑"), codes[0]故意用 report_date=NULL 桩
    conn.executemany(
        "INSERT INTO stock_factors_fundamental "
        "(code, date, report_date, close, ep, bp, sp, roe) VALUES (?,?,?,?,?,?,?,?)",
        [(c, ff_seed_date, ff_seed_rd, 10.0, 0.5, 2.0, 1.5, 12.0) for c in codes[1:]]
        + [(codes[0], ff_seed_date, None, 10.0, None, None, None, None)])
    conn.commit()
    n_daily = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    n_fin = conn.execute("SELECT COUNT(*) FROM stock_financial").fetchone()[0]
    src.close()
    conn.close()
    return n_daily, n_fin


def run_phase4(mod, db_path, target):
    mod.DB_MAIN = Path(db_path)
    t0 = time.time()
    ok = mod.phase4_update_fundamental_factors(target, False)
    return ok, time.time() - t0


def fetch_ff(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM stock_factors_fundamental ORDER BY code, date")]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stock_factors_fundamental)")]
    conn.close()
    return rows, cols


def eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    return a == b


OLD_COLS = ["code", "date", "report_date", "close", "ep", "bp", "sp",
            "roe", "lev", "ato", "pm"]


def compare_semantics(rows_old, rows_new, tag):
    """旧列语义必须逐行全等(created_at除外)"""
    check(tag + " 行数一致", len(rows_old) == len(rows_new),
          "old=%d new=%d" % (len(rows_old), len(rows_new)))
    ko = {(r["code"], r["date"]): r for r in rows_old}
    kn = {(r["code"], r["date"]): r for r in rows_new}
    check(tag + " 主键集合一致", set(ko) == set(kn),
          "diff=%d" % len(set(ko) ^ set(kn)))
    bad = []
    for k in ko:
        for col in OLD_COLS:
            if not eq(ko[k][col], kn[k][col]):
                bad.append((k, col, ko[k][col], kn[k][col]))
    check(tag + " 旧11列取值全等(语义零变化)", not bad, "bad=%s" % bad[:3])


def check_pit(rows, tag):
    """新列pit_avail_date = 参考映射(report_date); NULL报告期→NULL"""
    bad = [(r["code"], r["date"], r["report_date"], r.get("pit_avail_date"))
           for r in rows
           if r["report_date"] is not None
           and r.get("pit_avail_date") != ref_pit(r["report_date"])]
    check(tag + " pit_avail_date=法定披露截止", not bad, "bad=%s" % bad[:3])
    n_null_rd = sum(1 for r in rows if r["report_date"] is None)
    n_null_pit = sum(1 for r in rows if r.get("pit_avail_date") is None)
    check(tag + " NULL报告期行pit亦为NULL", n_null_rd == n_null_pit,
          "rd_null=%d pit_null=%d" % (n_null_rd, n_null_pit))


# ────────────────────────────────────────────────────────────
# 场景A: 20260814 增量 + 迁移 + 回填 + 幂等 + 兜底
# ────────────────────────────────────────────────────────────
def scenario_a(mod_old, mod_new):
    print("\n===== 场景A: 20260814 增量(迁移+回填+幂等+兜底) =====", flush=True)
    codes = pick_codes(40)
    db_old = TMP / "regress_A_old.db"
    db_new = TMP / "regress_A_new.db"
    nd, nf = build_db(db_old, codes, 20260804, 20260814, 20260813, 20260630)
    build_db(db_new, codes, 20260804, 20260814, 20260813, 20260630)
    print("  样本: %d只 日线%d条 财务%d条" % (len(codes), nd, nf))

    ok, el = run_phase4(mod_old, db_old, 20260814)
    check("A 旧版phase4跑通", ok, "%.1fs" % el)
    ok, el = run_phase4(mod_new, db_new, 20260814)
    check("A 新版phase4跑通", ok, "%.1fs" % el)

    rows_old, cols_old = fetch_ff(db_old)
    rows_new, cols_new = fetch_ff(db_new)
    check("A 旧版无新列", "pit_avail_date" not in cols_old, str(cols_old[-2:]))
    check("A 新版有新列", "pit_avail_date" in cols_new, str(cols_new[-2:]))
    compare_semantics(rows_old, rows_new, "A")
    check_pit(rows_new, "A")

    # 回填不改旧列: 预置历史行(20260813)在新库应有pit且旧列与旧库一致
    seed_old = [r for r in rows_old if r["date"] == 20260813]
    seed_new = [r for r in rows_new if r["date"] == 20260813]
    check("A 预置历史行数一致(回填未删改行)", len(seed_old) == len(seed_new),
          "%d/%d" % (len(seed_old), len(seed_new)))
    backfilled = sum(1 for r in seed_new
                     if r["report_date"] is not None
                     and r["pit_avail_date"] == ref_pit(r["report_date"]))
    check("A 预置历史行已回填pit", backfilled == len(seed_new) - 1,
          "%d/%d" % (backfilled, len(seed_new) - 1))
    s_old = {(r["code"]): r for r in seed_old}
    s_new = {(r["code"]): r for r in seed_new}
    check("A 预置历史行旧列未动",
          all(eq(s_old[c][col], s_new[c][col]) for c in s_old for col in OLD_COLS))

    # 幂等: 重跑应"已最新"且零变化
    before, _ = fetch_ff(db_new)
    ok, _ = run_phase4(mod_new, db_new, 20260814)
    after, _ = fetch_ff(db_new)
    check("A 幂等重跑跑通", ok)
    check("A 幂等重跑零变化",
          len(before) == len(after)
          and all(eq(b[col], a[col]) for b, a in zip(before, after)
                  for col in OLD_COLS + ["pit_avail_date"]))

    # 兜底: 新增20260815日线, 抹掉部分20260814的pit, 重跑应补齐
    conn = sqlite3.connect(db_new)
    rows = conn.execute(
        "SELECT code, close, vol, amt FROM stock_daily WHERE date=20260814").fetchall()
    conn.executemany("INSERT OR REPLACE INTO stock_daily VALUES (?,?,?,?,?)",
                     [(c, 20260815, cl, v, a) for c, cl, v, a in rows])
    conn.execute("UPDATE stock_factors_fundamental SET pit_avail_date=NULL "
                 "WHERE date=20260814 AND report_date IS NOT NULL AND "
                 "code IN (SELECT code FROM stock_factors_fundamental "
                 "         WHERE date=20260814 LIMIT 7)")
    conn.commit()
    n_nulled = conn.execute(
        "SELECT COUNT(*) FROM stock_factors_fundamental "
        "WHERE date>=20260814 AND report_date IS NOT NULL AND pit_avail_date IS NULL"
    ).fetchone()[0]
    conn.close()
    check("A 兜底预置NULL行", n_nulled > 0, "n=%d" % n_nulled)
    run_phase4(mod_new, db_new, 20260815)
    conn = sqlite3.connect(db_new)
    left = conn.execute(
        "SELECT COUNT(*) FROM stock_factors_fundamental "
        "WHERE date>=20260814 AND report_date IS NOT NULL AND pit_avail_date IS NULL"
    ).fetchone()[0]
    n815 = conn.execute(
        "SELECT COUNT(*) FROM stock_factors_fundamental WHERE date=20260815").fetchone()[0]
    conn.close()
    check("A 兜底后无缺失pit", left == 0, "left=%d" % left)
    check("A 20260815新行已写入", n815 == len(rows), "%d/%d" % (n815, len(rows)))


# ────────────────────────────────────────────────────────────
# 场景B: 2022-07-01 前视铁证复现(语义不变 + pit正确)
# ────────────────────────────────────────────────────────────
def scenario_b(mod_old, mod_new):
    print("\n===== 场景B: 2022-07-01 前视铁证复现 =====", flush=True)
    codes = pick_codes(40)
    db_old = TMP / "regress_B_old.db"
    db_new = TMP / "regress_B_new.db"
    nd, nf = build_db(db_old, codes, 20220428, 20220705, 20220427, 20211231)
    build_db(db_new, codes, 20220428, 20220705, 20220427, 20211231)
    print("  样本: %d只 日线%d条 财务%d条(含20220630未来报告)" % (len(codes), nd, nf))

    ok, _ = run_phase4(mod_old, db_old, 20220705)
    check("B 旧版phase4跑通", ok)
    ok, _ = run_phase4(mod_new, db_new, 20220705)
    check("B 新版phase4跑通", ok)

    rows_old, _ = fetch_ff(db_old)
    rows_new, _ = fetch_ff(db_new)
    compare_semantics(rows_old, rows_new, "B")
    check_pit(rows_new, "B")

    def at(rows, d):
        return {(r["code"]): r for r in rows if r["date"] == d}

    # 铁证复现: 2022-07-01 当日两版 report_date 均=20220630(as-of语义未变)
    o701, n701 = at(rows_old, 20220701), at(rows_new, 20220701)
    check("B 20220701有数据", len(o701) > 30, "n=%d" % len(o701))
    check("B 20220701旧语义=20220630(铁证复现,语义不变)",
          all(r["report_date"] == 20220630 for r in o701.values()))
    check("B 20220701新语义仍=20220630(未破坏消费方)",
          all(r["report_date"] == 20220630 for r in n701.values()))
    # PIT正确性: 20220630报告的合法使用日=20220831 → 0701不可用, PIT消费方按pit过滤
    check("B 20220701 pit=20220831(>T, PIT消费方应回退20220331)",
          all(r["pit_avail_date"] == 20220831 for r in n701.values()))
    # 对照: 05-05当日最新rd=20220331(旧语义恰与PIT一致), pit=20220430<=T可用
    n505 = at(rows_new, 20220505)
    check("B 20220505 rd=20220331且pit=20220430(可用)",
          all(r["report_date"] == 20220331 and r["pit_avail_date"] == 20220430
              for r in n505.values()))
    # PIT消费方模板: T=20220701 应取 pit<=T 的最新一期 = 20220331
    import bisect
    snap = {}
    for r in rows_new:
        if r["pit_avail_date"] is not None and r["pit_avail_date"] <= 20220701:
            key = r["code"]
            if key not in snap or r["report_date"] > snap[key]["report_date"]:
                snap[key] = r
    check("B PIT消费模板: T=20220701每股取20220331(无前视)",
          snap and all(r["report_date"] == 20220331 for r in snap.values()),
          "n=%d" % len(snap))


def main():
    t0 = time.time()
    print("加载两版模块: OLD=%s\n            NEW=%s" % (OLD_FILE, NEW_FILE))
    mod_old = load_module(OLD_FILE, "tulong_old_prePIT")
    mod_new = load_module(NEW_FILE, "tulong_new_pit")
    scenario_a(mod_old, mod_new)
    scenario_b(mod_old, mod_new)
    print("\n%s 耗时%.0fs" % ("=" * 46, time.time() - t0))
    if FAILED:
        print("回归结果: FAIL %d项: %s" % (len(FAILED), FAILED))
        return 1
    print("回归结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
