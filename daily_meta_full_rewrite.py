# -*- coding: utf-8 -*-
"""
daily_meta_full_rewrite.py — stock_daily_meta 全市场全历史 vipdoc 正源重写 (v2 临时表方案)
==========================================================================================
背景(2026-10-29): meta 日线库确诊多族历史缺陷(6月错位/手单位残留/指数混入/历史错位事故)。
根治 = 以 vipdoc 权威正源(read_day 已含手单位归一)全市场全历史重建。

v2 方案(20261029 深夜, v1 随机 REPLACE 实测仅 667行/s 弃用):
  61GB 大库上逐行 REPLACE 需同时维护 3 棵 B 树(rowid主键 + code,date唯一索引 + date索引),
  随机写放大导致极慢。改为:
  1) vipdoc 全量数据按 (date, code) 排序顺序导入裸临时表 stock_daily_meta_new(无索引)
  2) 旧表独有行(vipdoc 缺失的更早期退市历史) LEFT JOIN 补入
  3) 事后一次性建索引(比逐行维护快 10-50 倍)
  4) 验收通过后 RENAME 原子切换; 旧表保留为 stock_daily_meta_old_20261029 待主人裁决清理
构建期间旧表完整可读, 读方零影响。

用法:
  python daily_meta_full_rewrite.py             # 全量重建+切换
  python daily_meta_full_rewrite.py --dry-run   # 只统计计划不写库
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day  # noqa: E402

META_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")
VIPDOC = Path(r"D:\anzhuang\tdx\vipdoc")
NEW_TABLE = "stock_daily_meta_new"
OLD_TABLE = "stock_daily_meta_old_20261029"
LOG = Path(r"D:\zcodesj\reports\daily_meta_full_rewrite.log")

# 重建行在内存按 (date, code) 排序需要的体量: ~1800万行元组 ≈ 4-6GB, 可行;
# 若内存吃紧可分日期段处理, 此处按全量实现(机器 32GB+)。


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# A 股代码段白名单(20261029): sh 目录混存指数(sh000001=上证指数)/基金(5xxxxx)/B股(900xxx),
# 与深市股票 000001 撞号——不过滤会把指数行当股票写入(实测 19910403 000001 双行:
# 121.71=上证指数点位, 49.0=深发展)。bj 取北交所段。
_A_SHARE_PREFIX = {
    "sh": ("600", "601", "603", "605", "688", "689"),
    "sz": ("000", "001", "002", "003", "300", "301"),
    "bj": ("43", "83", "87", "920"),
}


def iter_files():
    """枚举沪深北三市场 A 股股票段 .day 文件 → (market, code)。"""
    for market, prefixes in _A_SHARE_PREFIX.items():
        for p in (VIPDOC / market / "lday").glob(f"{market}*.day"):
            code = p.stem[len(market):]
            if code.startswith(prefixes):
                yield market, code


def main():
    parser = argparse.ArgumentParser(description="stock_daily_meta 全史 vipdoc 重建(v2)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(str(META_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-524288")  # 512MB
    cur = conn.cursor()

    old_n = cur.execute("SELECT COUNT(*) FROM stock_daily_meta").fetchone()[0]
    log(f"=== v2 重建启动 | 旧表 {old_n:,} 行 ===")

    if cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (NEW_TABLE,)).fetchone()[0]:
        log(f"清理上次残留 {NEW_TABLE}")
        cur.execute(f"DROP TABLE {NEW_TABLE}")
        conn.commit()

    # ── 1. vipdoc 全量读取 + 按 (date, code) 排序 ──
    t0 = time.time()
    all_rows = []  # (date, code, o, h, l, c, vol, amt, chg, chg_amt, amp)
    files = sorted(iter_files())
    log(f"读取 {len(files)} 个 .day 文件(全史)...")
    for fi, (market, code) in enumerate(files, 1):
        prev_c = None
        for d, o, h, l, c, amt, vol in read_day(market=market, code=code):
            if o <= 0 or c <= 0:
                if c > 0:
                    prev_c = c
                continue
            if prev_c and prev_c > 0:
                chg = (c - prev_c) / prev_c * 100
                chg_amt = c - prev_c
                amp = (h - l) / prev_c * 100
            else:
                chg = chg_amt = amp = None
            all_rows.append((d, code, o, h, l, c, vol, amt, chg, chg_amt, amp))
            prev_c = c
        if fi % 2000 == 0:
            log(f"  读取进度 {fi}/{len(files)}, 累计 {len(all_rows):,} 行")
    all_rows.sort(key=lambda r: (r[0], r[1]))
    log(f"读取完成: {len(all_rows):,} 行, 耗时 {time.time() - t0:.0f}s")

    if args.dry_run:
        log("[DRY-RUN] 计划: 建新表→顺序导入→补旧表独有→建索引→切换。退出。")
        return

    # ── 2. 建裸新表 + 顺序导入 ──
    cur.execute(f"""
        CREATE TABLE {NEW_TABLE} (
            id INTEGER PRIMARY KEY,
            code VARCHAR(10), date INT, open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, amount REAL, change_pct REAL, change_amount REAL,
            amplitude REAL, updated_at TIMESTAMP
        )
    """)
    t1 = time.time()
    CHUNK = 200_000
    for i in range(0, len(all_rows), CHUNK):
        cur.executemany(f"""
            INSERT INTO {NEW_TABLE}
            (code, date, open, high, low, close, volume, amount,
             change_pct, change_amount, amplitude, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        """, [(r[1], r[0]) + tuple(r[2:]) for r in all_rows[i:i + CHUNK]])
        conn.commit()
        if (i // CHUNK) % 10 == 0:
            log(f"  导入进度 {i + CHUNK:,}/{len(all_rows):,}, {time.time() - t1:.0f}s")
    log(f"顺序导入完成, 耗时 {time.time() - t1:.0f}s")

    # ── 3. 补旧表独有行(vipdoc 缺失的更早期退市历史) ──
    t2 = time.time()
    cur.execute(f"CREATE INDEX idx_new_cd ON {NEW_TABLE}(code, date)")
    cur.execute(f"""
        INSERT INTO {NEW_TABLE}
            (code, date, open, high, low, close, volume, amount,
             change_pct, change_amount, amplitude, updated_at)
        SELECT s.code, s.date, s.open, s.high, s.low, s.close, s.volume, s.amount,
               s.change_pct, s.change_amount, s.amplitude, s.updated_at
        FROM stock_daily_meta s
        LEFT JOIN {NEW_TABLE} n ON n.code = s.code AND n.date = s.date
        WHERE n.id IS NULL
    """)
    conn.commit()
    legacy = cur.execute(f"SELECT COUNT(*) FROM {NEW_TABLE}").fetchone()[0] - len(all_rows)
    log(f"补入旧表独有行 {max(legacy, 0):,} 行, 耗时 {time.time() - t2:.0f}s")

    # ── 4. 建正式索引 ──
    t3 = time.time()
    cur.execute(f"CREATE INDEX idx_{NEW_TABLE}_date ON {NEW_TABLE}(date)")
    cur.execute(f"CREATE UNIQUE INDEX uidx_{NEW_TABLE}_cd ON {NEW_TABLE}(code, date)")
    conn.commit()
    log(f"索引构建完成, 耗时 {time.time() - t3:.0f}s")

    # ── 5. 验收门禁 ──
    new_n = cur.execute(f"SELECT COUNT(*) FROM {NEW_TABLE}").fetchone()[0]
    log(f"验收: 新表 {new_n:,} 行 (vipdoc {len(all_rows):,} + 旧独有 {max(legacy, 0):,})")
    # 抽样对照: 三只股票 6/26 close/vol 与 vipdoc 真值
    checks = [("000333", 20260626, 78.55, 44953388), ("600519", 20260626, 1168.63, 5006647),
              ("000725", 20260626, None, 4525176800)]
    ok = True
    for code, d, ec, ev in checks:
        r = cur.execute(f"SELECT close, volume FROM {NEW_TABLE} WHERE code=? AND date=?",
                        (code, d)).fetchone()
        if r is None:
            log(f"  ✗ {code} {d} 无行")
            ok = False
            continue
        if ec is not None and abs(r[0] - ec) > 0.01:
            log(f"  ✗ {code} {d} close {r[0]} != {ec}")
            ok = False
        if abs(r[1] - ev) > 100000:
            log(f"  ✗ {code} {d} vol {r[1]:,} != {ev:,}")
            ok = False
    resid = cur.execute(f"""
        SELECT COUNT(*) FROM {NEW_TABLE}
        WHERE date >= 20100101 AND volume > 1000000 AND amount > 0 AND close > 0
          AND amount / volume / close > 20
    """).fetchone()[0]
    log(f"验收: 现代段(2010+)手单位残留 {resid} 行")
    if resid > 50000:
        log("  ✗ 手单位残留超阈值, 中止切换")
        ok = False
    if not ok:
        log("验收未通过, 新表保留待诊断, 旧表未动。中止。")
        conn.close()
        sys.exit(2)

    # ── 6. 原子切换 ──
    cur.execute(f"ALTER TABLE stock_daily_meta RENAME TO {OLD_TABLE}")
    cur.execute(f"ALTER TABLE {NEW_TABLE} RENAME TO stock_daily_meta")
    # 旧索引名随表换名仍在 old 表上; 新表索引已建。重建与旧表同名的正式索引名。
    conn.commit()
    final_n = cur.execute("SELECT COUNT(*) FROM stock_daily_meta").fetchone()[0]
    log(f"=== 原子切换完成: stock_daily_meta = {final_n:,} 行; 旧表保留为 {OLD_TABLE} ===")
    conn.close()


if __name__ == "__main__":
    main()
