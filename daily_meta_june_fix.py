# -*- coding: utf-8 -*-
"""
daily_meta_june_fix.py — stock_daily_meta 202606 末段错位污染修复
================================================================
病理(2026-10-29 复验确诊):
  stock_daily_meta 的 20260624~20260627 四个日期槽, 全市场约 4600 只被填充为
  6/26 同一份数据(6/24 真实数据丢失, 6/27 为周六虚构行)。6/22/6/23/6/29 正常。
  成因: 6 月末某次补数错位(历史事故残留); 本脚本以 vipdoc 权威正源修复。
修复:
  - 正源: vipdoc .day (主人手动更新的通达信本地原始日线, tdx_reader.read_day
    已实测正确处理 int×100 价格编码与 float 编码)
  - DELETE date=20260627 (周六, 全市场无真实行情)
  - INSERT OR REPLACE 20260624/25/26 三天全市场真实数据
  - 派生列 change_pct/change_amount/amplitude 用 vipdoc 前收(6/23)重算
幂等: REPLACE+DELETE 窗口替换, 重跑安全; 只触碰 6/24~6/27, 其余日期不动。
验收(内置):
  - 重写行数核对; 修复后相邻重复扫描应无 202606 命中(外部复核)

用法:
  python daily_meta_june_fix.py            # 执行修复
  python daily_meta_june_fix.py --dry-run  # 只打印计划
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

# 修复窗口: 6/24(周三)/6/25(周四)/6/26(周五) 重写, 6/27(周六) 删除
FIX_DATES = (20260624, 20260625, 20260626)
DROP_DATE = 20260627
# 派生列基准前收日(6/23, vipdoc 真值)
PREV_DATE = 20260623
LOG = Path(r"D:\zcodesj\reports\daily_meta_june_fix.log")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def iter_vipdoc_codes():
    """枚举沪深两市全部日线文件代码。"""
    for market in ("sh", "sz"):
        for p in (VIPDOC / market / "lday").glob(f"{market}*.day"):
            yield market, p.stem[len(market):]


def load_window(market, code, dates_needed):
    """读单只 .day, 返回 {date: (o,h,l,c,amount,vol)} 只含窗口日期与前收日。"""
    try:
        rows = read_day(market=market, code=code)
    except Exception:
        return {}
    need = set(dates_needed) | {PREV_DATE}
    return {r[0]: r for r in rows if r[0] in need}


def main():
    parser = argparse.ArgumentParser(description="stock_daily_meta 202606 错位污染修复")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log("=== daily_meta_june_fix 启动 ===")
    conn = sqlite3.connect(str(META_DB))
    cur = conn.cursor()
    for d in (*FIX_DATES, DROP_DATE):
        n = cur.execute("SELECT COUNT(*) FROM stock_daily_meta WHERE date=?", (d,)).fetchone()[0]
        log(f"修复前 {d}: {n} 行")

    if args.dry_run:
        log("[DRY-RUN] 计划: 删除 20260627, 重写 20260624/25/26。退出。")
        return

    # 1. 删除周六虚构日
    cur.execute("DELETE FROM stock_daily_meta WHERE date=?", (DROP_DATE,))
    conn.commit()
    log(f"已删除虚构日 {DROP_DATE}")

    # 2. 全市场读 vipdoc 窗口数据
    t0 = time.time()
    by_code_date = {}
    prev_closes = {}
    missing = 0
    codes = list(iter_vipdoc_codes())
    for i, (market, code) in enumerate(codes, 1):
        w = load_window(market, code, FIX_DATES)
        if PREV_DATE in w:
            prev_closes[code] = w[PREV_DATE][4]
        for d in FIX_DATES:
            if d not in w:
                missing += 1
                continue
            r = w[d]
            by_code_date[(code, d)] = (r[1], r[2], r[3], r[4], r[5], r[6])  # o,h,l,c,amt,vol
        if i % 1000 == 0:
            log(f"进度 {i}/{len(codes)} 文件")

    # 3. 组装重写行(派生列: 6/24 前收=6/23, 6/25 前收=6/24, 6/26 前收=6/25, 全部 vipdoc 真值)
    fixed_rows = []
    for (code, d), (o, h, l, c, amount, vol) in sorted(by_code_date.items()):
        if d == FIX_DATES[0]:
            prev_close = prev_closes.get(code)
        else:
            prev_day = FIX_DATES[FIX_DATES.index(d) - 1]
            prev_close = by_code_date.get((code, prev_day), (None,) * 6)[3]
        if prev_close:
            chg = (c - prev_close) / prev_close * 100
            chg_amt = c - prev_close
            amp = (h - l) / prev_close * 100
        else:
            chg = chg_amt = amp = None
        fixed_rows.append((code, d, o, h, l, c, vol, amount, chg, chg_amt, amp))

    cur.executemany("""
        INSERT OR REPLACE INTO stock_daily_meta
        (code, date, open, high, low, close, volume, amount, change_pct, change_amount, amplitude, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    """, fixed_rows)
    conn.commit()
    log(f"重写 {len(fixed_rows)} 行 (缺失 {missing} 只·日), 耗时 {time.time() - t0:.0f}s")

    # 3. 内置验收
    log("=== 内置验收 ===")
    for d in (*FIX_DATES, DROP_DATE):
        n = cur.execute("SELECT COUNT(*) FROM stock_daily_meta WHERE date=?", (d,)).fetchone()[0]
        log(f"修复后 {d}: {n} 行")
    dup = cur.execute("""
        WITH pairs AS (
            SELECT code, date, LAG(date) OVER (PARTITION BY code ORDER BY date) AS pd,
                   LAG(close) OVER (PARTITION BY code ORDER BY date) AS pc,
                   LAG(volume) OVER (PARTITION BY code ORDER BY date) AS pv,
                   close, volume
            FROM stock_daily_meta
            WHERE date BETWEEN 20260620 AND 20260630)
        SELECT COUNT(*) FROM pairs
        WHERE pd IS NOT NULL AND date > pd AND close=pc AND volume=pv AND volume > 0
    """).fetchone()[0]
    log(f"6/20~6/30 相邻重复组: {dup} (应为 0)")
    conn.close()
    log("=== 完成 ===")


if __name__ == "__main__":
    main()
