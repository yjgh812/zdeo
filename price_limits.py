# -*- coding: utf-8 -*-
"""
price_limits.py — A股涨停/跌停价唯一判定接口 + 向量化连板递推
================================================================
来源: 移植自 tickflow-stock-panel (github.com/shy3130/tickflow-stock-panel)
      backend/app/price_limits.py + app/indicators/pipeline.py 连板递推段
适配: 屠龙系统 (pandas 技术栈, 股票代码兼容 6位码 / xxxxxx.SZ 两种格式)

背景铁律:
  8/15 错误闭环 — 涨停计数虚高(创业板按9.9%误判, 应为19.9%+)。
  本模块自此为屠龙系统涨停/跌停判定的唯一官方口径, 任何新代码禁止
  再手写 `pct >= 9.9` 这类散落判定。改判定逻辑只改这里。

规则要点 (全部内置):
  - 主板 10% / 创业板(300,301) 20% / 科创板(688,689) 20% / 北交所(BJ) 30%
  - 主板 ST 5% 仅适用于 2026-07-06 之前 (2026-07-06 起主板ST恢复10%)
  - 涨停/跌停价 = 前收 × (1±pct) 后按交易所 half-up 舍入到分,
    用整数分算术实现, 规避 Python round 的银行家舍入差一分钱问题
  - 判定容差 0.005 元 (对齐 TSP signal_limit_up 口径)
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

MAIN_BOARD_ST_LIMIT_CHANGE_DATE = date(2026, 7, 6)  # 主板ST涨跌幅切换日
_CHANGE_DATE_I8 = np.int64(MAIN_BOARD_ST_LIMIT_CHANGE_DATE.strftime("%Y%m%d"))
MAIN_BOARD_LIMIT = 0.10
LEGACY_MAIN_BOARD_ST_LIMIT = 0.05
GROWTH_BOARD_LIMIT = 0.20   # 创业板 + 科创板
BEIJING_BOARD_LIMIT = 0.30

TOL = 0.005  # 成交价 vs 涨停价的判定容差(元)


def _clean_symbol(symbol: str) -> str:
    """兼容 '300319' / '300319.SZ' / 'SZ300319' → 统一成 6位码(北交保留后缀判断)。"""
    s = str(symbol or "").strip().upper()
    if s.endswith((".SZ", ".SH", ".BJ")):
        suffix = s[-3:]
        code = s[:6]
        return code + "." + suffix
    if s.startswith(("SH", "SZ", "BJ")) and len(s) == 8:
        return s[2:] + "." + s[:2]
    return s  # 6位码裸格式


def _is_beijing(symbol: str) -> bool:
    s = _clean_symbol(symbol)
    # 北交号段: 8开头 / 43x 等4开头 / 2026换码后 920 新号段(如920832齐鲁华信)
    return s.endswith(".BJ") or (s and (s[0] in "48" or s.startswith("920")) and "." not in s)


def board_limit_pct(symbol: str) -> float:
    """按板块返回涨跌幅上限: 主板10% / 创业科创20% / 北交30%。"""
    s = _clean_symbol(symbol)
    if _is_beijing(s):
        return BEIJING_BOARD_LIMIT
    code = s[:6] if "." in s else s
    if code.startswith(("300", "301", "688", "689")):
        return GROWTH_BOARD_LIMIT
    return MAIN_BOARD_LIMIT


def is_risk_warning_name(name: str | None) -> bool:
    """股票名称含 ST 即风险警示板。"""
    return "ST" in str(name or "").upper()


def price_limit_pct(symbol: str, trade_date: date, *, is_risk_warning: bool = False) -> float:
    """生效涨跌幅: 2026-07-06 前主板ST为5%, 其余按板块。"""
    base = board_limit_pct(symbol)
    if base == MAIN_BOARD_LIMIT and is_risk_warning and trade_date < MAIN_BOARD_ST_LIMIT_CHANGE_DATE:
        return LEGACY_MAIN_BOARD_ST_LIMIT
    return base


def limit_price(previous_close: float, limit_pct: float, *, up: bool = True) -> float:
    """涨停/跌停价 — 整数分算术模拟交易所 half-up 舍入。

    例: 前收 3.33, 主板涨停 → 3.33*1.1=3.663 → 3.66 元即涨停价。
    银行家舍入的 round(3.663, 2) 恰好也是 3.66, 但 round(2.675, 2)=2.67
    而交易所是 2.68 — 这是差一分钱导致漏判涨停的经典坑, 本函数规避之。
    """
    sign = 1 if up else -1
    numerator = round((1 + sign * limit_pct) * 100)  # 110 / 90 / 120 ...
    cents = int(previous_close * 100 + 0.5)          # 前收 → 分 (half-up)
    return ((cents * numerator + 50) // 100) / 100   # 分 → 元 (half-up)


def is_limit_up(
    symbol: str,
    prev_close: float,
    close: float,
    trade_date: date,
    name: str | None = None,
) -> bool:
    """判定 close 是否涨停 (用未复权价; prev_close 无效时返回 False)。"""
    if not prev_close or prev_close <= 0 or close <= 0:
        return False
    pct = price_limit_pct(symbol, trade_date, is_risk_warning=is_risk_warning_name(name))
    lp = limit_price(prev_close, pct, up=True)
    return close >= lp - TOL


def is_limit_down(
    symbol: str,
    prev_close: float,
    close: float,
    trade_date: date,
    name: str | None = None,
) -> bool:
    """判定 close 是否跌停。"""
    if not prev_close or prev_close <= 0 or close <= 0:
        return False
    pct = price_limit_pct(symbol, trade_date, is_risk_warning=is_risk_warning_name(name))
    lp = limit_price(prev_close, pct, up=False)
    return close <= lp + TOL


# ────────────────────────────────────────────────────────────
# 向量化: DataFrame 批量判定 + 连板递推
# 输入约定: df 需含列 [symbol, prev_close, close, trade_date(可date/str)], 可选 name
# ────────────────────────────────────────────────────────────
def add_limit_flags(df: pd.DataFrame, *, inplace: bool = False) -> pd.DataFrame:
    """给日K DataFrame 追加 signal_limit_up / signal_limit_down 布尔列。

    行式调用 is_limit_up/is_limit_down, 与单股判定完全同口径 (唯一真理源)。
    列: symbol, prev_close, close, trade_date; 可选 name。
    """
    out = df if inplace else df.copy()
    dates = pd.to_datetime(out["trade_date"]).dt.date
    names = out["name"] if "name" in out.columns else [None] * len(out)
    out["signal_limit_up"] = [
        is_limit_up(s, p, c, d, name=n)
        for s, p, c, d, n in zip(out["symbol"], out["prev_close"], out["close"], dates, names)
    ]
    out["signal_limit_down"] = [
        is_limit_down(s, p, c, d, name=n)
        for s, p, c, d, n in zip(out["symbol"], out["prev_close"], out["close"], dates, names)
    ]
    return out


def consecutive_limit_ups(series_limit_up: pd.Series) -> pd.Series:
    """向量化连板数递推 (run-length 计数)。

    原理 (TSP pipeline.py: 非涨停 cumsum 造分组号, 组内涨停 cumsum 即连板数):
      输入按"单只股票时间升序"的涨停布尔列 → 输出逐日连板数。
      [True, True, False, True] → [1, 2, 0, 1]
    """
    s = series_limit_up.fillna(False).astype(bool)
    grp = (~s).cumsum()          # 非涨停次数累计 = 分组号
    return s.groupby(grp).cumsum().astype("int64") * s.astype("int64")


def consecutive_limit_downs(series_limit_down: pd.Series) -> pd.Series:
    """连跌停(连续跌停天数), 同上对称。"""
    s = series_limit_down.fillna(False).astype(bool)
    grp = (~s).cumsum()
    return s.groupby(grp).cumsum().astype("int64") * s.astype("int64")


def numpy_limit_cents(
    prev_cents: "np.ndarray",
    codes: "np.ndarray",
    dates_i8: "np.ndarray",
    is_st: "np.ndarray | None" = None,
    *,
    up: bool = True,
) -> "np.ndarray":
    """向量化涨停/跌停价(单位: 分) — 大批量历史扫描用(唯一真理源)。

    参数:
      prev_cents: 昨收价×100 取整(int64 数组)
      codes:      股票代码数组(6位码或带后缀)
      dates_i8:   交易日 YYYYMMDD int64 数组(用于 ST 切换日判断)
      is_st:      布尔数组; None = 全部按非 ST
      up:         True 涨停价 / False 跌停价
    返回: 涨停/跌停价(分) int64 数组, 与 limit_price 同口径(整数分 half-up)。
    """
    cleaned = [_clean_symbol(c) for c in codes]
    pure = np.array([str(c).split(".")[0] for c in cleaned], dtype=object)
    is_growth = np.array([p.startswith(("300", "301", "688", "689")) for p in pure])
    is_bj = np.array([
        str(c).endswith(".BJ") or p.startswith(("43", "83", "87", "92"))
        for c, p in zip(cleaned, pure)
    ])
    st = np.zeros(len(cleaned), dtype=bool) if is_st is None else np.asarray(is_st, dtype=bool)
    legacy_st = st & ~is_growth & ~is_bj & (np.asarray(dates_i8, dtype=np.int64) < _CHANGE_DATE_I8)

    # 分子 = (1±pct)*100 四舍五入: 主板110/90, 创业科创120/80, 北交130/70, 旧ST 105/95
    pct_num = np.full(len(cleaned), 110 if up else 90, dtype=np.int64)
    pct_num[is_growth] = 120 if up else 80
    pct_num[is_bj] = 130 if up else 70
    pct_num[legacy_st] = 105 if up else 95
    return (prev_cents.astype(np.int64) * pct_num + 50) // 100


# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 自测: python price_limits.py
    d = date(2026, 8, 15)
    ok = 0
    total = 0

    def check(desc: str, got, want):
        global ok, total
        total += 1
        mark = "PASS" if got == want else "FAIL"
        if got == want:
            ok += 1
        print(f"[{mark}] {desc}: got={got} want={want}")

    # 1. 板块比例
    check("主板比例", board_limit_pct("600288"), 0.10)
    check("创业板比例", board_limit_pct("300319"), 0.20)
    check("创业板带后缀", board_limit_pct("300319.SZ"), 0.20)
    check("科创板比例", board_limit_pct("688981"), 0.20)
    check("北交所比例", board_limit_pct("430047.BJ"), 0.30)

    # 2. ST 切换日: 2026-07-05 前 5%, 之后 10%
    check("ST切换前", price_limit_pct("600001", date(2026, 7, 5), is_risk_warning=True), 0.05)
    check("ST切换后", price_limit_pct("600001", date(2026, 7, 6), is_risk_warning=True), 0.10)
    check("非ST不受切换影响", price_limit_pct("600001", date(2026, 7, 5)), 0.10)

    # 3. 舍入: 交易所 half-up
    check("涨停价 3.33*1.1", limit_price(3.33, 0.10), 3.66)
    check("涨停价 4.05*1.1 half-up", limit_price(4.05, 0.10), 4.46)  # 4.455→4.46
    check("涨停价 10.00*1.2", limit_price(10.00, 0.20), 12.00)
    check("跌停价 10.00*0.9", limit_price(10.00, 0.10, up=False), 9.00)

    # 4. 涨停判定 — 精确口径: 收盘价 >= 涨停价, "涨幅9.9%/19.9%"只是近似说法
    #    (前收9.92主板涨停价10.91, 涨幅仅9.98%<10% 但就是涨停; 反之 10.99/10.00 差1分钱未封板)
    check("创业板 涨幅20%封板", is_limit_up("300319", 10.00, 12.00, d), True)
    check("创业板 差1分钱不算", is_limit_up("300319", 10.00, 11.99, d), False)
    check("创业板 涨幅19.96%但到价=涨停", is_limit_up("300319", 10.07, 12.08, d), True)  # 10.07*1.2=12.084→12.08
    check("创业板 涨幅11%非涨停", is_limit_up("300319", 10.00, 11.10, d), False)
    check("主板 涨幅9.98%到价=涨停", is_limit_up("600288", 9.92, 10.91, d), True)
    check("主板 差1分钱不算", is_limit_up("600288", 10.00, 10.99, d), False)
    check("主板 封板", is_limit_up("600288", 10.00, 11.00, d), True)
    check("北交所 涨幅29.99%到价=涨停", is_limit_up("430047.BJ", 10.07, 13.09, d), True)  # 10.07*1.3=13.091→13.09
    check("ST主板4.9%不判(旧规5%下)", is_limit_up("600001", 10.00, 10.49, date(2026, 7, 1), name="ST测试"), False)
    check("ST主板4.95%判涨停(旧规)", is_limit_up("600001", 10.00, 10.50, date(2026, 7, 1), name="ST测试"), True)

    # 5. 连板递推
    check("连板递推", consecutive_limit_ups(pd.Series([True, True, False, True, True, True])).tolist(),
          [1, 2, 0, 1, 2, 3])
    check("连跌递推", consecutive_limit_downs(pd.Series([False, True, True, False, True])).tolist(),
          [0, 1, 2, 0, 1])

    # 6. DataFrame 集成: add_limit_flags + 连板递推串联
    kdf = pd.DataFrame({
        "symbol": ["600288"] * 5,
        "trade_date": ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15"],
        "prev_close": [9.90, 10.89, 11.98, 13.18, 14.50],
        "close": [10.89, 11.98, 13.18, 13.00, 14.50],  # 3连板后断板, 再平
    })
    kdf = add_limit_flags(kdf)
    boards = consecutive_limit_ups(kdf["signal_limit_up"]).tolist()
    check("DataFrame三日连板后断板", boards, [1, 2, 3, 0, 0])
    check("跌停未发生", kdf["signal_limit_down"].tolist(), [False] * 5)

    # 7. numpy 向量化 vs 标量 limit_price 一致性
    codes = ["600288", "300319", "688981", "430047", "600001", "600001"]
    dt = [date(2026, 8, 15)] * 5 + [date(2026, 7, 1)]
    prevs = [9.92, 10.07, 33.33, 10.07, 10.00, 10.00]
    sts = [False, False, False, False, False, True]
    cents = (np.array(prevs) * 100 + 0.5).astype("int64")
    dates_i8 = np.array([int(d.strftime("%Y%m%d")) for d in dt], dtype=np.int64)
    vec_up = numpy_limit_cents(cents, codes, dates_i8, np.array(sts))
    vec_dn = numpy_limit_cents(cents, codes, dates_i8, np.array(sts), up=False)
    for i, (c, d, p, s) in enumerate(zip(codes, dt, prevs, sts)):
        pct = price_limit_pct(c, d, is_risk_warning=s)
        want_up = round(limit_price(p, pct, up=True) * 100)
        want_dn = round(limit_price(p, pct, up=False) * 100)
        check(f"numpy一致UP {c}@{d}", int(vec_up[i]), want_up)
        check(f"numpy一致DN {c}@{d}", int(vec_dn[i]), want_dn)

    print(f"\n{ok}/{total} PASS" + ("" if ok == total else "  ← 有失败!"))
    raise SystemExit(0 if ok == total else 1)
