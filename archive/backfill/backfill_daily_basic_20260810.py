# -*- coding: utf-8 -*-
"""
backfill_daily_basic_20260810.py — 回补 daily_basic 2026-08-10（周一，唯一缺口交易日）

数据源：baostock（真实历史数据，含 peTTM/pbMRQ/psTTM/turn）
字段映射与库内惯例一致（参照 backfill_daily_basic_20260808.py）：
  close/pe_ttm/pb/ps_ttm/turnover_rate  <- baostock 实测
  total_share/float_share               <- 各股票最后已知值顺延（股本期内基本不变）
  total_mv/circ_mv                      <- close × 股本 重算（万元）
  pe/volume_ratio/turnover_rate_f/free_share/dv_*/ps <- 0.0 占位
断点续跑：已完成的 (ts_code) 记录到独立 checkpoint 文件（与 8/8 脚本分开，避免误跳过）。
"""
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(r"D:\workbuddyspace\databases\fundamental_data.db")
META = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")
CKPT = Path(r"D:\workbuddyspace\backfill_ckpt_daily_basic_20260810.txt")
SKIP = Path(r"D:\workbuddyspace\backfill_skip_daily_basic_20260810.txt")
LOG = Path(r"D:\workbuddyspace\backfill_daily_basic_20260810.log")
START, END = "2026-08-08", "2026-08-10"   # baostock 日期格式（区间内仅 8/10 一个交易日）
END_D = "20260810"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_universe():
    """股票池：meta 库 20260810 交易 A 股（剔除 688 科创 / 8 / 9 / 4 开头），带股本顺延值。"""
    m = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    codes = [r[0] for r in m.execute(
        "SELECT DISTINCT code FROM stock_daily_meta WHERE date=?", (END_D,)).fetchall()]
    m.close()
    codes = [c for c in codes if not c.startswith(("688", "8", "9", "4"))]
    # 股本顺延：取各股票最后一个已知 daily_basic 值
    f = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    shares = {}
    for ts, total, flt in f.execute(
            "SELECT ts_code, total_share, float_share FROM daily_basic "
            "WHERE (ts_code, trade_date) IN (SELECT ts_code, MAX(trade_date) FROM daily_basic GROUP BY ts_code)"):
        shares[ts] = (total or 0.0, flt or 0.0)
    f.close()
    out = []
    for c in sorted(codes):
        ts = f"{c}.SH" if c.startswith("6") else f"{c}.SZ"
        out.append((c, ts, *shares.get(ts, (0.0, 0.0))))
    return out


def main():
    import baostock as bs
    done = set(CKPT.read_text(encoding="utf-8").split()) if CKPT.exists() else set()
    uni = get_universe()
    todo = [x for x in uni if x[1] not in done]
    log(f"股票池 {len(uni)} 只，待回补 {len(todo)} 只（已完成 {len(done)}）")

    lg = bs.login()
    if lg.error_code != "0":
        log(f"baostock 登录失败: {lg.error_msg}")
        sys.exit(1)

    con = sqlite3.connect(str(DB), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")

    n_rows = 0
    t0 = time.time()
    try:
        for i, (code, ts, total_sh, float_sh) in enumerate(todo, 1):
            bscode = ("sh." if code.startswith("6") else "sz.") + code
            ok = False
            for attempt in range(5):   # 指数退避重试，抗限流
                try:
                    rs = bs.query_history_k_data_plus(
                        bscode, "date,close,peTTM,pbMRQ,psTTM,turn",
                        start_date=START, end_date=END, frequency="d", adjustflag="3")
                except Exception as e:
                    rs = None
                    log(f"  异常 {ts}: {e}")
                if rs is not None and rs.error_code == "0":
                    ok = True
                    break
                time.sleep(5 * (attempt + 1))
            if not ok:
                log(f"  [跳过] {ts}: {rs.error_msg if rs else '未知错误'}")
                with SKIP.open("a", encoding="utf-8") as f:
                    f.write(ts + "\n")
                continue
            rows = []
            while rs.next():
                d, close, pe, pb, ps, turn = rs.get_row_data()
                if not close:
                    continue
                dd = d.replace("-", "")
                close = float(close)
                rows.append((
                    ts, dd, close,
                    float(turn) if turn else 0.0,   # turnover_rate
                    0.0,                            # turnover_rate_f
                    0.0,                            # volume_ratio
                    0.0,                            # pe（静态，源无）
                    float(pe) if pe else 0.0,       # pe_ttm
                    float(pb) if pb else 0.0,       # pb
                    0.0,                            # ps
                    float(ps) if ps else 0.0,       # ps_ttm
                    0.0, 0.0,                       # dv_ratio, dv_ttm
                    total_sh, float_sh, 0.0,        # total/float/free_share（万股）
                    close * total_sh,               # total_mv（万元）
                    close * float_sh,               # circ_mv（万元）
                ))
            if rows:
                con.executemany(
                    "INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
                n_rows += len(rows)
            with CKPT.open("a", encoding="utf-8") as f:
                f.write(ts + "\n")
            if i % 100 == 0:
                con.commit()
                rate = i / (time.time() - t0)
                eta = (len(todo) - i) / rate / 60
                log(f"  进度 {i}/{len(todo)}  已写入 {n_rows} 行  速度 {rate:.1f} 只/s  ETA {eta:.1f} 分钟")
        con.commit()
    finally:
        bs.logout()
        con.close()
    log(f"完成：共写入 {n_rows} 行。")


if __name__ == "__main__":
    main()
