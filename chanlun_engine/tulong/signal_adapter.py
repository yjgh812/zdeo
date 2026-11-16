"""
信号接入适配器
================
将缠论引擎的信号写入 sidecar SQLite（chan_signals_strict.db），
供屠龙系统只读消费。

铁律：只写 sidecar，绝不写屠龙原库。

使用：
  python -m chanlun_engine.tulong.signal_adapter              # 扫描屠龙选股宇宙
  python -m chanlun_engine.tulong.signal_adapter --codes 600519,000858  # 指定股票
"""
from __future__ import annotations
import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.signal import scan_signal, SignalType
from chanlun_engine.data_loader import get_daily_kline, get_recent_trade_date, DB_DRAGON_QUANT, _connect_ro
from chanlun_engine.analyzer import ChanAnalyzer

SIDECAR_DB = Path(r"D:\zcodesj\chanlun_engine\chan_signals_strict.db")


def init_db():
    """初始化 sidecar 数据库"""
    SIDECAR_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SIDECAR_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chan_signals_strict (
            date        TEXT,
            code        TEXT,
            name        TEXT,
            signal_type TEXT,
            level       TEXT,
            price       REAL,
            confidence  REAL,
            pivot_zg    REAL,
            pivot_zd    REAL,
            pivot_dir   TEXT,
            details     TEXT,
            created_at  TEXT,
            PRIMARY KEY (date, code, level)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chan_date
        ON chan_signals_strict(date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chan_code_date
        ON chan_signals_strict(code, date)
    """)
    conn.commit()
    conn.close()


def write_signal(signal, name: str = ""):
    """写入单个信号到 sidecar"""
    conn = sqlite3.connect(str(SIDECAR_DB))
    conn.execute("""
        INSERT OR REPLACE INTO chan_signals_strict
        (date, code, name, signal_type, level, price, confidence,
         pivot_zg, pivot_zd, pivot_dir, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal.date,
        signal.code,
        name,
        signal.signal_type.value,
        signal.level,
        signal.price,
        signal.confidence,
        signal.pivot_info.get("zg") if signal.pivot_info else None,
        signal.pivot_info.get("zd") if signal.pivot_info else None,
        signal.pivot_info.get("direction") if signal.pivot_info else None,
        signal.details,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()
    conn.close()


def get_stock_name(code: str) -> str:
    """从屠龙 stock_info 获取股票名称"""
    try:
        with _connect_ro(DB_DRAGON_QUANT) as c:
            row = c.execute(
                "SELECT name FROM stock_info WHERE code = ?", (code,)
            ).fetchone()
            return row[0] if row else ""
    except Exception:
        return ""


def scan_and_write(codes: List[str], date: Optional[str] = None):
    """
    扫描指定股票列表的缠论信号，写入 sidecar。

    参数
    ----
    codes : 股票代码列表
    date  : 信号日期（默认最新交易日）
    """
    if date is None:
        date = get_recent_trade_date(0) or datetime.now().strftime("%Y%m%d")

    init_db()

    n_signals = 0
    n_errors = 0

    for i, code in enumerate(codes):
        try:
            name = get_stock_name(code)
            signal = scan_signal(code)
            if signal and signal.signal_type != SignalType.NONE:
                write_signal(signal, name)
                n_signals += 1
                print(f"  [{i+1}/{len(codes)}] {code} {name}: "
                      f"{signal.signal_type.value} conf={signal.confidence:.2f}")
        except Exception as e:
            n_errors += 1
            if n_errors <= 3:
                print(f"  [{i+1}/{len(codes)}] {code}: ERROR {e}")

    print(f"\n完成: {n_signals} 个信号写入, {n_errors} 个错误")
    print(f"Sidecar: {SIDECAR_DB}")


def get_tulong_universe() -> List[str]:
    """获取屠龙选股宇宙（从 stock_factors_fundamental 取最新ep/bp>0的）"""
    try:
        latest_date = get_recent_trade_date(0)
        with _connect_ro(DB_DRAGON_QUANT) as c:
            rows = c.execute(
                "SELECT DISTINCT code FROM stock_factors_fundamental "
                "WHERE date = ? AND ep > 0 AND bp > 0 "
                "AND (code LIKE '60%%' OR code LIKE '00%%') "
                "LIMIT 500",
                (latest_date,)
            ).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"[WARN] 获取选股宇宙失败: {e}")
        return []


def query_signals(date: str, code: Optional[str] = None) -> list:
    """查询 sidecar 中的信号（供 dragon_v4 消费）"""
    conn = sqlite3.connect(f"file:{SIDECAR_DB}?mode=ro", uri=True)
    if code:
        rows = conn.execute(
            "SELECT * FROM chan_signals_strict WHERE date = ? AND code = ?",
            (date, code)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM chan_signals_strict WHERE date = ?", (date,)
        ).fetchall()
    conn.close()
    return [dict(zip([d[0] for d in rows_description], r)) for r in rows] \
        if rows else []


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=str, default=None)
    parser.add_argument("--universe-size", type=int, default=50,
                        help="从屠龙选股宇宙取N只（加速）")
    args = parser.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        print("获取屠龙选股宇宙...")
        all_codes = get_tulong_universe()
        codes = all_codes[:args.universe_size]
        print(f"取前 {len(codes)} 只")

    print(f"\n扫描 {len(codes)} 只股票的缠论信号...")
    scan_and_write(codes)
