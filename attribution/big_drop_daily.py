#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""big_drop_daily.py — 大跌风险库日更 wrapper（2026-09-14 结构缺口补齐；10/22 区间语义）。

背景：limit_up_patterns（涨停范式库）有 22:00 链引擎日更，limit_down_risks
（大跌风险库）自本 wrapper 起有日更 owner，对 attribution_patterns_backfill
--event big_drop（官方入口幂等：同日同码 engine_big_drop 已存在则跳过）。

【2026-10-22 主人「按推荐继续」批准：单日语义 → 区间补齐语义】
原「仅 qfq MAX 单日」在夜链慢于 22:35 触发点时（9/18 链僵死夜、10/19 patterns
超时夜实锤），触发时 qfq MAX 尚停在 T-1 → 幂等跳过、当日漏填且永不补位。
新语义 = 表内 MAX+1 → qfq MAX 区间内全部交易日逐日补齐，链慢自愈整类漏日：
  - 下限夹 45 自然日：跌停池源仅提供近 30 个交易日窗口（9/8、9/18 补齐实测
    ValueError 硬限制），避免每晚对必败远期日期无谓重试；
  - 单次最多补 10 日：big_drop 实测 ~50s/只 ÷ 4 workers，封顶防 22:35 槽位
    超时膨胀；余量留次晚幂等续补；
  - 交易日序列取自 qfq stock_daily_meta_qfq distinct date（只读）。
"""
import os
import runpy
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = r"D:\zcodesj"
QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
LOWER_NATURAL_DAYS = 45   # 跌停池源 30 交易日窗口 ≈ 45 自然日, 再远必败不重试
MAX_DATES_PER_RUN = 10    # 单次补齐上限, 防触发槽超时膨胀; 余量次晚续补

sys.path.insert(0, ROOT)
try:  # schtasks 子进程 stdout 为 GBK（9/14 emoji 崩溃同族预防）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def pending_dates(tbl_max_override=None):
    """表内 MAX+1 → qfq MAX 区间内的交易日（升序, 最多 MAX_DATES_PER_RUN 日）。

    tbl_max_override 仅供函数级测试注入表内 MAX, 生产路径不传（真读库）。
    所有 SQL 均参数绑定（Mimosa 约束）。
    """
    if tbl_max_override is not None:
        tbl_max = _as_int(tbl_max_override)
    else:
        con_attr = sqlite3.connect(ATTR_DB)
        try:
            tbl_max = _as_int(con_attr.execute(
                "SELECT MAX(trade_date) FROM limit_down_risks "
                "WHERE drop_type='engine_big_drop'").fetchone()[0])
        finally:
            con_attr.close()

    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True)
    try:
        qfq_max = _as_int(con.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
        lower = int((datetime.strptime(str(qfq_max), "%Y%m%d")
                     - timedelta(days=LOWER_NATURAL_DAYS)).strftime("%Y%m%d"))
        days = con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq "
            "WHERE date>? AND date<=? AND date>=? ORDER BY date",
            (tbl_max, qfq_max, lower)).fetchall()
    finally:
        con.close()
    return [str(r[0]) for r in days][:MAX_DATES_PER_RUN]


if __name__ == "__main__":
    dates = pending_dates()
    print(f"[big_drop_daily] pending dates = {dates if dates else '无(表内已最新)'}")
    if not dates:
        raise SystemExit(0)
    os.chdir(ROOT)
    sys.argv = ["attribution_patterns_backfill.py",
                "--dates", ",".join(dates), "--event", "big_drop"]
    runpy.run_path(os.path.join(ROOT, "attribution_patterns_backfill.py"),
                   run_name="__main__")
