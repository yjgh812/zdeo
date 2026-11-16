"""
数据加载层
============
复用 multifactor/factor_loader 的只读 DB 接口，获取前复权日K数据。
支持：日线（stock_daily_hsj）、通达信分钟线（vipdoc .lc1/.lc5）。
"""
from __future__ import annotations
import sys
from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, List

# 把 multifactor 加入 path 以复用 config 的 DB 路径
_MULTIFACTOR_DIR = Path(r"D:\zcodesj\multifactor")
if str(_MULTIFACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(_MULTIFACTOR_DIR))

# 屠龙 DB 路径（与 multifactor/config.py 一致）
DB_DRAGON_QUANT = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """只读连接，防止误写"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    return conn


def get_recent_trade_date(n_back: int = 0) -> Optional[str]:
    """
    获取最近第 n_back 个交易日（0=最新有数据的日期）。
    返回 yyyymmdd 字符串。
    """
    with _connect_ro(DB_DRAGON_QUANT) as c:
        rows = c.execute(
            "SELECT DISTINCT date FROM stock_daily_hsj ORDER BY date DESC LIMIT ?",
            (n_back + 1,)
        ).fetchall()
    if len(rows) > n_back:
        return rows[n_back][0]
    return rows[-1][0] if rows else None


def get_daily_kline(code: str,
                    start: str = "20180101",
                    end: Optional[str] = None) -> pd.DataFrame:
    """
    获取单只股票的日K线（前复权口径）。

    参数
    ----
    code  : 6位纯数字代码，如 '600519'
    start : 起始日期 yyyymmdd
    end   : 结束日期 yyyymmdd（None=取到数据源最新, 修复硬编码20260810断供事故）
    """
    with _connect_ro(DB_DRAGON_QUANT) as c:
        df = pd.read_sql(
            "SELECT date, open, high, low, close, vol, amt "
            "FROM stock_daily_hsj "
            "WHERE code = ? AND date BETWEEN ? AND ? "
            "ORDER BY date",
            c, params=[code, start, end or "20991231"]
        )
    if df.empty:
        return df
    df["date"] = df["date"].astype(str)
    for col in ["open", "high", "low", "close", "vol", "amt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.reset_index(drop=True)
    return df


def get_daily_klines(codes: List[str],
                     start: str = "20180101",
                     end: str = "20260810") -> pd.DataFrame:
    """
    批量获取多只股票日K线。
    返回 DataFrame[code, date, open, high, low, close, vol, amt]。
    """
    if not codes:
        return pd.DataFrame()
    placeholders = ",".join("?" * len(codes))
    with _connect_ro(DB_DRAGON_QUANT) as c:
        df = pd.read_sql(
            f"SELECT code, date, open, high, low, close, vol, amt "
            f"FROM stock_daily_hsj "
            f"WHERE date BETWEEN ? AND ? AND code IN ({placeholders}) "
            f"ORDER BY code, date",
            c, params=[start, end] + codes
        )
    if df.empty:
        return df
    df["date"] = df["date"].astype(str)
    for col in ["open", "high", "low", "close", "vol", "amt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def read_tdx_minute(code: str, period: int = 5) -> pd.DataFrame:
    """
    读取通达信本地分钟线数据。

    参数
    ----
    code   : 6位代码如 '600519'
    period : 1 或 5（分钟）

    返回
    ----
    DataFrame[datetime, open, high, low, close, vol, amt]

    2026-08-31 修复+迁移: 原实现路径拼接错误(vipdoc/shfzline 缺分隔)、把打包日期
    直接当 YYYYMMDD、vol 恒 0, 导致长期静默返回空; 现统一走 tdx_reader.read_min5/read_min1
    (全量验证过、纯 stdlib), 日期解码与 vol 均正确。
    """
    from tdx_reader import read_min1, read_min5

    rows = read_min5(code=code) if period == 5 else read_min1(code=code)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "datetime": pd.to_datetime(r["dt"], format="%Y-%m-%d %H:%M"),
        "open": r["o"], "high": r["h"], "low": r["l"],
        "close": r["c"], "vol": r["vol"], "amt": r["amount"],
    } for r in rows])
    return df.reset_index(drop=True)


def get_index_daily(index_code: str = "sh000001",
                    start: str = "20180101",
                    end: str = "20260810") -> pd.DataFrame:
    """
    获取指数日K线（如上证指数 sh000001）。
    返回 DataFrame[date, open, high, low, close, vol, amt]。
    """
    with _connect_ro(DB_DRAGON_QUANT) as c:
        # 尝试 index_daily 表
        try:
            df = pd.read_sql(
                "SELECT date, open, high, low, close, vol, amount AS amt "
                "FROM index_daily "
                "WHERE index_code = ? AND date BETWEEN ? AND ? "
                "ORDER BY date",
                c, params=[index_code, start, end]
            )
            if not df.empty:
                df["date"] = df["date"].astype(str)
                return df.reset_index(drop=True)
        except Exception:
            pass

    return pd.DataFrame()


if __name__ == "__main__":
    print("=" * 60)
    print("data_loader 自检")
    print("=" * 60)

    # 测试单股日K
    try:
        df = get_daily_kline("600519", "20260101", "20260810")
        print(f"[OK] 600519 贵州茅台 日K: {len(df)}行, "
              f"日期={df['date'].min()}~{df['date'].max()}")
    except Exception as e:
        print(f"[FAIL] 日K加载: {e}")

    # 测试指数
    try:
        idx = get_index_daily("sh000001", "20260101", "20260810")
        print(f"[OK] 上证指数: {len(idx)}行")
    except Exception as e:
        print(f"[FAIL] 指数加载: {e}")
