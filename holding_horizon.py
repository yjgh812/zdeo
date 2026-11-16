#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持有周期标签（holding horizon）唯一真源。

背景（2026-08-24 三周交易专项复盘, 53笔平仓配对）:
    回测 bp_ep 胜率 54% 建立在"月度调仓·平均持有 142 天"之上；
    实盘所有票被同一个"平均 3 天 + -3%~-5% 趋势卖出"模板处理, 胜率 11.3%。
    根因之一 = 买入建议只回答"买什么/买多少", 从未回答"拿多久/什么才是卖点"。

本模块是 source → 长短线 的唯一映射真源, 三个消费点共用、禁止各自复制:
    1. tulong_buy_whitelist.py   白名单 CSV 写 holding_horizon 列(交付给主人)
    2. pre_sell_check.py         持有期观察(2026-09-07 主人裁决"持仓无持有时间
                                 限制"——C7-R28 最低持有期拦截已停用, 天数降为
                                 观察信息不进 holds)
    3. guarded_trade_executor / trade_executor  卖出链自动补 --hold-days/--horizon

策略本性依据(生产配置 v1.2, round8_pitfix 回测; 仅作持仓分类描述, 不构成
卖出时限——2026-09-07 裁决后卖点统一为 -8% 止损或主人裁量):
    long  = bp_ep 价值因子: 月度调仓, 回测平均持有 142 天
    short = 短线信号(ml/master_hist 等): 回测参考 20 天

映射失败/未知来源 → unknown(仅影响观察标签, 不再有任何持有期拦截)。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

DQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
BASE = r"D:\zcodesj"

# source → 持有周期。新增信号源必须在此登记, 禁止在消费方散落 if/else。
SOURCE_HORIZON: dict[str, str] = {
    "bp_ep": "long",
    "bp_ep_core": "long",
    "full_market": "long",  # bp_ep 同源 PIT 因子 rank16-50 顺延段，同月度调仓节奏
    "master_hist": "short",
    "mass_scan": "short",
    # 2026-09-10 补登记(审计实锤 9/9 白名单 34 只 ml 票+1028 只 universe 票全落
    # unknown「未知来源·按短线守卫」): ml_signal=M-3 复活登记 hold20bars 短线族
    # (docstring 原定义即含 ml); universe=白名单资格层买入, 短线语义与历史
    # unknown 守卫行为一致, 仅标签显式化。
    "ml_signal": "short",
    "universe": "short",
    "hotspot_breakout": "short",
    "smc_s2_roe": "short",
    "manual_confirmed": "short",
    "manual": "short",
    "mcp_backfill": "short",
    "existing_hold": "unknown",
}

# 每档的最低持有期(自然日)与人读标签。long=10 天依据: 月度调仓周期≈22 交易日,
# 取一半作为"趋势卖出禁用窗口"; short=5 天与 C7 原阈值一致。
HORIZON_MIN_HOLD_DAYS: dict[str, int] = {"long": 10, "short": 5, "unknown": 5}

HORIZON_LABEL: dict[str, str] = {
    "long": "长线·月度调仓·卖点=调仓换股或-8%",
    "short": "短线≤20天·卖点=时间止损或-8%",
    "unknown": "未知来源·按短线守卫",
}

# ---------------------------------------------------------------------------
# bp_ep 月名单归属判定（2026-09-02 缺口修复）
# 缺口: horizon 原仅按买入时 signal_source 判定——名单内价值仓因历史 source
# (mass_scan/manual_confirmed 等)被标 short → R5a/R5b 短周期规则会提前卖出
# bp_ep 保留组(实测: 601169 北京银行/002936 郑州银行均在 9 月 Top20 名单内,
# P0-E 主人裁决=bp_ep 组保留走月度调仓; 8/13-24 三周 53 笔提前卖长线价值票
# 胜率 11.3% 教训)。修复: 票在最新 bp_ep 月名单核心段 → 强制 long;
# 名单外自动回落 source 口径(月度调仓换股不受阻), -8% 止损路径不经本判定。
_BP_LIST_CACHE: dict = {"key": None, "codes": set()}


def in_bp_ep_monthly_list(code6: str) -> bool:
    """票是否在最新 bp_ep 月名单核心段(≤100行文件, 与白名单 _load_bp_ep_file 同口径)。"""
    import csv
    import glob
    import io
    import os
    try:
        beep_dir = os.path.join(BASE, "multifactor", "reports", "daily_top20")
        files = sorted(glob.glob(os.path.join(beep_dir, "bp_ep_monthly_*.csv")))
        core = None
        for path in reversed(files):
            try:
                with open(path, encoding="utf-8-sig") as f:
                    lines = f.readlines()
            except OSError:
                continue
            if 0 < len(lines) - 1 <= 100:
                core = (path, "".join(lines))
                break
        if core is None:
            return False
        path, text = core
        key = (path, os.path.getmtime(path))
        if _BP_LIST_CACHE["key"] != key:
            rows = list(csv.DictReader(io.StringIO(text)))
            _BP_LIST_CACHE["codes"] = {str(r.get("code", ""))[-6:] for r in rows}
            _BP_LIST_CACHE["key"] = key
        return str(code6)[-6:] in _BP_LIST_CACHE["codes"]
    except Exception:
        return False  # fail-open: 名单判定失败回落 source 口径, 与 C7 守卫 fail-open 一致


def horizon_of(source: str | None) -> str:
    if not source:
        return "unknown"
    return SOURCE_HORIZON.get(source, "unknown")


def horizon_label(source: str | None) -> str:
    return HORIZON_LABEL[horizon_of(source)]


def _norm_date(d: str | None) -> str | None:
    """buy_tracker 里混存 20260824 与 2026-08-13 两种格式, 归一为 YYYY-MM-DD。"""
    if not d:
        return None
    d = str(d).strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if len(d) >= 10:
        return d[:10]
    return None


def _with_prefix(code: str) -> str:
    """裸 6 位码补交易所前缀; 已带前缀原样返回。"""
    c = (code or "").strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if len(c) == 6:
        return ("sh" if c.startswith(("6", "9", "5")) else "sz") + c
    return c


def query_position(code: str) -> dict:
    """查 buy_tracker 最近一条 HOLD 记录, 返回持有上下文。

    return: {found, buy_date(YYYY-MM-DD), source, horizon,
             hold_days(自然日, 至今), min_hold_days}
    查询失败/无记录 → found=False（消费方 fail-open, 不阻断卖出流程）。
    """
    out = {"found": False, "buy_date": None, "source": None,
           "horizon": "unknown", "hold_days": None, "min_hold_days": None}
    try:
        conn = sqlite3.connect(f"file:{DQ_DB}?mode=ro", uri=True, timeout=5)
        row = conn.execute(
            "SELECT buy_date, signal_source FROM buy_tracker "
            "WHERE code=? AND status='HOLD' ORDER BY id DESC LIMIT 1",
            (_with_prefix(code),)).fetchone()
        conn.close()
        if not row:
            return out
        buy_date = _norm_date(row[0])
        source = row[1]
        horizon = horizon_of(source)
        # 2026-09-02 缺口修复: 最新 bp_ep 月名单内 → 月度调仓节奏, 强制 long
        # (名单优先于历史 source; 查询失败 fail-open 回落 source 口径)
        if not in_bp_ep_monthly_list(str(code)[-6:]):
            pass  # 名单外 → 维持 source 口径
        else:
            horizon = "long"
        hold_days = None
        if buy_date:
            try:
                hold_days = (datetime.now() - datetime.strptime(buy_date, "%Y-%m-%d")).days
            except ValueError:
                pass
        out.update({"found": True, "buy_date": buy_date, "source": source,
                    "horizon": horizon, "hold_days": hold_days,
                    "min_hold_days": HORIZON_MIN_HOLD_DAYS[horizon]})
        return out
    except Exception:
        return out


if __name__ == "__main__":
    # 冒烟: python holding_horizon.py sz002936 sh600629
    import sys
    for c in sys.argv[1:]:
        print(c, "->", query_position(c))
