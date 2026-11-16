# -*- coding: utf-8 -*-
"""
health_check_daily.py — 盘前系统健康巡检 (2026-08-15 H4 补缺)
============================================================
背景: l0_system_health.py 只冒烟闸门脚本,无表新鲜度/磁盘/数据源检查,
且从未挂 cron。数据"没到/脚本死了/盘满了"只能在开盘后人工发现。

巡检项:
  1. 数据源新鲜度: vipdoc 最新交易日(严格取 <今日 的最近交易日)
  2. 关键表新鲜度: 7张核心表 max(date) vs 预期交易日(落后=ERROR)
  3. 磁盘空间: D: 剩余 <50G=WARN, <20G=ERROR
  4. 闸门脚本冒烟: 复用 l0_system_health.py(8项R1-R18/M5/M6/M1/M2/L5/L6)
  5. TickFlow 面板存活: localhost:3018
  6. vipdoc 数据文件 mtime(超过7天未更新=数据源停滞告警)
  7. 宪法状态(2026-08-23): 宪法文件存在性/mtime(AGENTS.md/PROJECT_BOUNDARY.md/CONSTITUTION_SELF_CHECK.md),缺失=ERROR;边界漂移不自动判定,按A5/B5人工核对
    8. 数据库备份(2026-08-23备份强制化): 复用 ensure_backup 自愈守卫(查最近应完成备份日的12库产物,缺失自动补跑db_backup);正常=OK,补跑仍失败=WARN;schtasks 05:00 独立调度兜底(2026-10-17 自 22:30 迁移,错峰链写窗)

用法:
  python health_check_daily.py            # 巡检并输出 md+json
  python health_check_daily.py --json     # 仅 JSON 摘要(供 cron 解析)

退出码: 0=健康 1=有警告 2=有ERROR(表落后/闸门FAIL/磁盘临界/数据源停滞)
ERROR 时自动调用 notify.py 告警。
"""
import csv
import datetime
import json
import os
import shutil
import struct
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

BASE = "D:/zcodesj"
VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh600519.day"
REPORTS = os.path.join(BASE, "reports")
# 数据库备份巡检（2026-08-23 备份强制化第二/三层）：
#   单一真源 = ensure_backup.py（12 库清单来自 db_backup.SMALL_DBS，此处不重复定义防漂移）

DBS = {
    "dragon_quant": r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
    "dragon_quant_meta": r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
    "dragon_quant_qfq": r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
    "fundamental_data": r"D:\workbuddyspace\databases\fundamental_data.db",
    # 2026-09-10 补查: industry_kline_ths 位于申万库
    "sw_industry": r"D:\workbuddyspace\databases\sw_industry.db",
}
STRICT_TABLES = [
    ("dragon_quant_meta", "stock_daily_meta", "date"),
    ("dragon_quant", "stock_daily_raw", "date"),
    ("dragon_quant_qfq", "stock_daily_meta_qfq", "date"),
    ("dragon_quant", "feature_daily_v2", "date"),
    ("dragon_quant", "stock_signals_v43", "date"),
    ("dragon_quant", "stock_signals_v375", "date"),
    # 2026-09-05 补查: v22 ml 分数(8/14 停更曾致 10/1 调仓陈旧分 P0; 现入链 aux 日更)
    ("dragon_quant", "stock_ml_scores_v22", "date"),
    ("fundamental_data", "daily_basic", "trade_date"),
    # 2026-09-08 补查: 龙虎榜家族(lhb_update.py aux 日更; 此前三表 9/2 起断更 4 日无人报警
    # =巡检无此二表; 龙虎榜同为盘后数据, 语义与 daily_basic 一致)
    ("fundamental_data", "longhubang_daily", "trade_date"),
    ("fundamental_data", "lhb_seat_detail", "trade_date"),
    # 2026-09-10 补查(巡检盲区收口): 以下 6 表均有生产消费方(情绪/行业动量/行业K线/缠论/
    # 聪明钱/龙虎榜旧口径)但产出环节失败不阻断且链内 verify 不设退出码——断更时任何闸门
    # 都不会报警(9/2 chan 断更、9/4 minute 断更同族事故), "表在但陈旧也 OK"漏检家族收口。
    ("dragon_quant", "chanlun_signals_nolookahead", "date"),
    ("dragon_quant_meta", "smart_money_factors", "date"),
    ("fundamental_data", "feature_market_sentiment", "trade_date"),
    ("fundamental_data", "feature_industry_momentum", "trade_date"),
    ("fundamental_data", "dragon_tiger_new", "trade_date"),
    ("sw_industry", "industry_kline_ths", "trade_date"),
]
DISK_WARN_GB, DISK_ERR_GB = 50, 20
# A1 归因回填监控（2026-08-17 接入，S5）
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
BACKFILL_RATE_TARGET = 0.95


def check_attribution_backfill():
    """归因回填监控：engine_attribution_log 回填率 + 窗口到期未填检测。

    返回 (stats_dict, issue_str_or_None, is_error_bool)。
    overdue>0（数据已到窗口但未回填）→ ERROR；仅回填率低但无到期未填 → WARN（正常等待窗口）。
    """
    try:
        a = sqlite3.connect(ATTR_DB)
        total = a.execute("SELECT COUNT(*) FROM engine_attribution_log").fetchone()[0]
        f3 = a.execute("SELECT COUNT(*) FROM engine_attribution_log WHERE fwd3 IS NOT NULL").fetchone()[0]
        f5 = a.execute("SELECT COUNT(*) FROM engine_attribution_log WHERE fwd5 IS NOT NULL").fetchone()[0]
        a.close()
    except Exception as e:
        return None, "归因回填检查失败: %s" % str(e)[:100], True
    if not total:
        return None, None, False
    # 2026-08-24 P1修复: 覆盖率分母改为"到期行"（T+5窗口未到的近5日事件不计入），
    # 否则持续新增事件的系统里 rate5 结构性 <95%，WARN 永久噪音化（实测201缺填
    # =194行窗口未到+7行停牌/当日，无一行真实漏填，overdue=0 才是漏填真判据）。
    try:
        m = sqlite3.connect(DBS["dragon_quant_meta"])
        meta_max = str(m.execute("SELECT MAX(date) FROM stock_daily_meta").fetchone()[0])
        cal5 = [str(r[0]) for r in m.execute(
            "SELECT DISTINCT date FROM stock_daily_meta WHERE date<=? ORDER BY date LIMIT 999999",
            (meta_max,))]
        due_cut = cal5[-6] if len(cal5) >= 6 else None  # 到期线=最新日往前第5个交易日的事件日
        m.close()
        a = sqlite3.connect(ATTR_DB)
        if due_cut:
            total_due = a.execute(
                "SELECT COUNT(*) FROM engine_attribution_log WHERE event_date<=?",
                (due_cut,)).fetchone()[0]
        else:
            total_due = total
        a.close()
    except Exception:
        # 20261011批4: 检查器自身读库失败不再伪装成"无漏填"(与 overdue=-1 同族伪绿)——
        # total_due=None → 回填率标 DEGRADED, detail 如实呈现不可信。
        total_due = None
    if total_due:
        rate5 = f5 / total_due
        rate_note = ""
    else:
        rate5 = 0.0
        rate_note = " [DEGRADED:归因库/到期线读库失败,回填率不可信]"
    # 窗口到期未填：event_date 之后第 5 个交易日已 <= meta 最新交易日，但 fwd5 IS NULL
    overdue = 0
    try:
        m = sqlite3.connect(DBS["dragon_quant_meta"])
        meta_max = str(m.execute("SELECT MAX(date) FROM stock_daily_meta").fetchone()[0])
        cal = [str(r[0]) for r in m.execute(
            "SELECT DISTINCT date FROM stock_daily_meta WHERE date<=? ORDER BY date",
            (meta_max,))]
        cal_set = set(cal)
        a = sqlite3.connect(ATTR_DB)
        # 逐行判定：该股 T+5 当日须自身有K线才算到期（排除蓝盾光电类停牌股误报）
        for ed, code in a.execute(
                "SELECT DISTINCT event_date, code FROM engine_attribution_log WHERE fwd5 IS NULL"):
            ed = str(ed)
            if ed not in cal_set:
                continue
            i = cal.index(ed)
            if not (i + 5 < len(cal) and cal[i + 5] <= meta_max):
                continue
            bare = str(code)[-6:]
            # 2026-09-14 修复: 到期判定与回填脚本 fwd_returns 完全同构——
            # qfq 自事件日起凑齐 horizon+1=6 根K才判到期。meta 库含停牌占位行且
            # 停牌股(龙版传媒 9/9-9/11 停牌案)T+5当日单根K不足以回填, 两旧口径均误报
            try:
                q = sqlite3.connect(
                    "file:" + DBS["dragon_quant_qfq"].replace("\\", "/") + "?mode=ro", uri=True)
                cnt6 = q.execute(
                    "SELECT COUNT(*) FROM stock_daily_meta_qfq "
                    "WHERE code=? AND date>=?", (bare, ed)).fetchone()[0]
                has_data = (1,) if cnt6 >= 6 else None
                q.close()
            except Exception:
                has_data = m.execute(
                    "SELECT 1 FROM stock_daily_meta WHERE code=? AND date=? LIMIT 1",
                    (bare, cal[i + 5])).fetchone()
            if has_data:
                overdue += 1
        a.close()
        m.close()
    except Exception:
        overdue = -1
    stats = {"total": total, "fwd3": f3, "fwd5": f5,
             "fwd5_rate": round(rate5, 4), "overdue": overdue, "degraded_note": rate_note}
    if overdue > 0:
        return stats, "归因回填链路异常: fwd5 回填率 %.1f%% 且 %d 行窗口到期未填" % (rate5 * 100, overdue), True
    if rate5 < BACKFILL_RATE_TARGET:
        return stats, "归因回填进行中: fwd5 %.1f%% (%d/%d) 目标≥%.0f%%（窗口滚动中）" % (
            rate5 * 100, f5, total, BACKFILL_RATE_TARGET * 100), False
    return stats, None, False


def check_attribution_closed_loop():
    """归因闭环新鲜度（2026-09-08 O-N1 接入，第八轮自省 B3 防复发）。

    背景: position_history 此前唯一写入方=一次性导入脚本，8/27 后断链 9 天无人发现——
    归因反馈报告胜率分布停留在旧账。closed_loop_sync.py 落地后本检查盯"再断链"。

    判据: trade_records 最新卖出日 vs position_history 最新闭环日的日历差。
    滞后>=3日 → ERROR（闭环断链复发信号）；滞后1-2日 → WARN；无卖出 → OK。
    异常 → ERROR（同 check_attribution_backfill 口径）。
    返回 (stats_dict, issue_or_None, is_error_bool)。

    2026-10-20 伪警修正: 闭环同步器在夜链执行(22:00/22:30), 当日新卖出必然到今晚才
    闭环——原判据按自然日对墙钟, 任何"今日有卖出"的交易日早晨都会把 gap 抬到 >=3 误报
    ERROR(当日实例: 10/20 晨两笔 S-5 卖出被误判"断链复发", 实际 10/17-19 零卖出、
    同步链健康)。修正=断链判定只锚定**昨日内已存在**的卖出(max_sell_prev),
    当日新卖出单列为 sells_today_pending 信息项, 不参与 ERROR/WARN 判定。
    """
    try:
        a = sqlite3.connect(ATTR_DB)
        max_exit = a.execute(
            "SELECT MAX(REPLACE(exit_date,'-','')) FROM position_history "
            "WHERE status IN ('CLOSED','PARTIAL_CLOSED')").fetchone()[0]
        max_sell = a.execute(
            "SELECT MAX(REPLACE(trade_date,'-','')) FROM trade_records "
            "WHERE action LIKE '%SELL%'").fetchone()[0]
        n_closed = a.execute(
            "SELECT COUNT(*) FROM position_history "
            "WHERE status IN ('CLOSED','PARTIAL_CLOSED')").fetchone()[0]
        today = datetime.datetime.now().strftime("%Y%m%d")
        max_sell_prev = a.execute(
            "SELECT MAX(REPLACE(trade_date,'-','')) FROM trade_records "
            "WHERE action LIKE '%SELL%' AND REPLACE(trade_date,'-','') < ?",
            (today,)).fetchone()[0]
        n_sell_today = a.execute(
            "SELECT COUNT(*) FROM trade_records "
            "WHERE action LIKE '%SELL%' AND REPLACE(trade_date,'-','') = ?",
            (today,)).fetchone()[0]
        a.close()
    except Exception as e:
        return None, "归因闭环检查失败: %s" % str(e)[:100], True
    stats = {"closed_total": n_closed, "max_closed": str(max_exit or ""),
             "max_sell": str(max_sell or ""), "sells_today_pending": n_sell_today}
    if not max_sell:
        return stats, None, False
    if not max_exit:
        return stats, "归因闭环为空但有卖出记录(max_sell=%s)" % max_sell, True
    # 断链判定只看昨日内已存在的卖出(今日新卖出今晚夜链闭环, 见 docstring 伪警修正)
    if not max_sell_prev:
        stats["lag_days"] = 0
    else:
        try:
            stats["lag_days"] = (datetime.datetime.strptime(str(max_sell_prev), "%Y%m%d")
                                 - datetime.datetime.strptime(str(max_exit), "%Y%m%d")).days
        except ValueError:
            return stats, "归因闭环日期格式异常: %s/%s" % (max_exit, max_sell_prev), True
    gap = stats.get("lag_days", 0)
    if gap >= 3:
        return stats, ("归因闭环滞后%d日: 昨日前卖出%s 未闭环(最新闭环%s)"
                       "——closed_loop_sync断链复发" % (gap, max_sell_prev, max_exit)), True
    if gap >= 1:
        return stats, "归因闭环滞后%d日(昨日前卖出%s/闭环%s)" % (gap, max_sell_prev, max_exit), False
    if n_sell_today:
        # 今日新卖出待今晚夜链闭环: 信息项不告警
        return stats, None, False
    return stats, None, False


def check_verification_feedback():
    """归因反馈闭环新鲜度（2026-10-06 三尾巴②接入，B3 防复发同族）。

    背景: verification_trades 的 sync 闭合与 actual_ret/result 回填 owner=
    feed_feedback_tables.py，原 WestockReconcile_1640 schtasks 丢失后断链，
    9/29-30 行 result=None 无人回填，直到全维度审计才发现。本检查盯「再断链」。

    判据: ①trade_records 最新 BUY 日 vs verification_trades 最新 trade_date 的
    日历差 >=3 日 → ERROR（sync 断链）；②result IS NULL 但 actual_ret_t1 已回填
    的积压行 >0 → WARN（update 标注段断链）；无 BUY → OK。
    返回 (stats_dict, issue_or_None, is_error_bool)。
    """
    try:
        a = sqlite3.connect(ATTR_DB)
        max_buy = a.execute(
            "SELECT MAX(REPLACE(trade_date,'-','')) FROM trade_records "
            "WHERE action LIKE '%BUY%'").fetchone()[0]
        max_ver = a.execute(
            "SELECT MAX(trade_date) FROM verification_trades").fetchone()[0]
        backlog = a.execute(
            "SELECT COUNT(*) FROM verification_trades "
            "WHERE result IS NULL AND actual_ret_t1 IS NOT NULL").fetchone()[0]
        a.close()
    except Exception as e:
        return None, "归因反馈检查失败: %s" % str(e)[:100], True
    stats = {"max_buy": str(max_buy or ""), "max_verification": str(max_ver or ""),
             "result_backlog": backlog}
    if not max_buy:
        return stats, None, False
    if not max_ver:
        return stats, "verification_trades 为空但有买入记录(max_buy=%s)——sync断链" % max_buy, True
    try:
        gap = (datetime.datetime.strptime(str(max_buy), "%Y%m%d")
               - datetime.datetime.strptime(str(norm_date(max_ver)), "%Y%m%d")).days
    except ValueError:
        return stats, "归因反馈日期格式异常: %s/%s" % (max_buy, max_ver), True
    stats["lag_days"] = gap
    if gap >= 3:
        return stats, ("归因反馈滞后%d日: 最新买入%s 未入verification_trades(最新%s)"
                       "——feed_feedback sync断链复发" % (gap, max_buy, max_ver)), True
    if backlog > 0:
        return stats, ("归因反馈标注积压%d行(result空但t1已回填)——update断链" % backlog), False
    if gap >= 1:
        return stats, "归因反馈滞后%d日(最新买入%s/verification%s)" % (gap, max_buy, max_ver), False
    return stats, None, False


def norm_date(v):
    # 2026-08-24 P1修复: 表被清空时 MAX(date)=None → str(None)="None"，
    # 字符串比较 "None">"2026…" 恒不落后 → 空表判 OK(伪健康)。None→"" 必然触发 BEHIND。
    if v is None:
        return ""
    s = str(v)
    return s.replace("-", "")[:8]


def vipdoc_trading_dates():
    """解析 vipdoc 日线全部日期,返回 (max_date, max_before_today)。"""
    dates = []
    with open(VIPDOC_DAY, "rb") as f:
        data = f.read()
    rec = struct.Struct("<IIIIIfII")
    for i in range(0, len(data) - 31, 32):
        d = rec.unpack_from(data, i)[0]
        if d > 19900101:
            dates.append(d)
    today = int(datetime.date.today().strftime("%Y%m%d"))
    mx = max(dates) if dates else None
    before = [d for d in dates if d < today]
    # 2026-08-24 P1修复: 盘后(>=15:30)且 vipdoc 已落当日数据时，预期交易日=当日，
    # 否则当日数据链滞后永远检不出（18:45 巡检停在昨日全绿伪健康）。
    # 盘前/盘中仍取 <today 的最大交易日（当日尚未收盘，不算滞后）。
    if mx is not None and mx == today:
        now_hm = datetime.datetime.now().hour * 100 + datetime.datetime.now().minute
        if now_hm >= 1530:
            return mx, mx
    return mx, (max(before) if before else None)


def table_max_date(db_key, table, col):
    path = DBS[db_key]
    if not os.path.exists(path):
        return None, "库缺失"
    try:
        con = sqlite3.connect(path)
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            if table not in tabs:
                return None, "缺表"
            if not str(col).isidentifier():
                return None, "非法列名"
            row = con.execute("SELECT MAX(%s) FROM %s" % (col, table)).fetchone()
            return row[0], None
        finally:
            con.close()
    except Exception as e:
        return None, str(e)


def disk_free_gb():
    # 2026-08-27 修复(E60): 原 PowerShell Get-PSDrive 路径曾在凌晨并发期返回瞬时0造成
    # 全局 ERROR 级误报(实测 df 同刻 967G); 改纯Python shutil 为主路径, PS 仅兜底
    try:
        return round(shutil.disk_usage("D:\\").free / (1024 ** 3), 1)
    except Exception:
        pass
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-PSDrive D).Free/1GB"], capture_output=True, text=True, timeout=30)
        toks = [t for t in r.stdout.replace("\n", " ").split() if _is_float(t)]
        return float(toks[-1]) if toks else None
    except Exception:
        return None


def _is_float(s):
    try:
        float(s)
        return True
    except Exception:
        return False


def check_scout_track():
    """低位首板蓄势感知层新鲜度（2026-09-09 接入，红棉断链防复发）。

    背景: breakout_scout 台账/日志停在 9/4 无人发现（9/7 起 GBK 打印崩溃 + 调度任务
    从未存活），9/5-9/8 期间 19 只二波涨停漏登记。daily_chain aux [scout] 接线后
    本检查盯"再断链"（同 B3 归因闭环防复发模式）。

    判据: 台账 max(last_update) 落后 qfq 库最新交易日的日历差。
    滞后>=3日 → ERROR（断链复发信号）；1-2日 → WARN；台账缺失/空 → ERROR。
    返回 (stats_dict, issue_or_None, is_error_bool)。
    """
    track_p = r"D:\zcodesj\data\breakout_scout_track.jsonl"
    try:
        con = sqlite3.connect(
            "file:" + DBS["dragon_quant_qfq"].replace("\\", "/") + "?mode=ro", uri=True)
        qfq_max = con.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        con.close()
        max_upd, n = 0, 0
        with open(track_p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                max_upd = max(max_upd, int(json.loads(line).get("last_update") or 0))
    except Exception as e:
        return None, "scout台账检查失败: %s" % str(e)[:100], True
    stats = {"entries": n, "track_max": max_upd, "qfq_max": str(qfq_max or "")}
    if not qfq_max:
        return stats, None, False
    if n == 0 or not max_upd:
        return stats, "scout台账缺失/空（感知层断链）qfq_max=%s" % qfq_max, True
    try:
        gap = (datetime.datetime.strptime(str(qfq_max), "%Y%m%d")
               - datetime.datetime.strptime(str(max_upd), "%Y%m%d")).days
    except ValueError:
        return stats, "scout台账日期格式异常: %s/%s" % (max_upd, qfq_max), True
    stats["lag_days"] = gap
    if gap >= 3:
        return stats, ("scout台账滞后%d日: 更新至%s qfq已至%s（感知层断链复发）"
                       % (gap, max_upd, qfq_max)), True
    if gap >= 1:
        return stats, ("scout台账滞后%d日: 更新至%s qfq已至%s" % (gap, max_upd, qfq_max)), False
    return stats, None, False


def check_limitup_patterns():
    """涨停范式库新鲜度（2026-09-09 接入，白名单主线标注供给防复发）。

    背景: limit_up_patterns 双口径=engine_v2(attribution_patterns_backfill, 曾靠
    手动跑, 9/8 断供 1 日无人发现) + 复盘口径(每晚复盘人工主题标签, 9/4-9/7 曾断)。
    表滞后 → 首板共振探测器/白名单主线标注/复盘链全部降级。

    判据: MAX(trade_date) 落后 qfq 库最新交易日的日历差。
    滞后>=3日 → ERROR；1-2日 → WARN。返回 (stats, issue, is_error)。"""
    try:
        con = sqlite3.connect(
            "file:" + ATTR_DB.replace("\\", "/") + "?mode=ro", uri=True)
        mx = con.execute("SELECT MAX(trade_date) FROM limit_up_patterns").fetchone()[0]
        con.close()
        con = sqlite3.connect(
            "file:" + DBS["dragon_quant_qfq"].replace("\\", "/") + "?mode=ro", uri=True)
        qfq_max = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        con.close()
    except Exception as e:
        return None, "涨停范式库检查失败: %s" % str(e)[:100], True
    stats = {"patterns_max": str(mx or ""), "qfq_max": str(qfq_max or "")}
    if not mx or not qfq_max:
        return stats, None, False
    try:
        gap = (datetime.datetime.strptime(str(qfq_max), "%Y%m%d")
               - datetime.datetime.strptime(str(mx), "%Y%m%d")).days
    except ValueError:
        return stats, "涨停范式库日期格式异常: %s/%s" % (mx, qfq_max), True
    stats["lag_days"] = gap
    if gap >= 3:
        return stats, ("涨停范式库滞后%d日: MAX=%s qfq=%s（复盘/backfill 双断供）"
                       % (gap, mx, qfq_max)), True
    if gap >= 1:
        return stats, ("涨停范式库滞后%d日: MAX=%s qfq=%s" % (gap, mx, qfq_max)), False
    return stats, None, False


def check_leader_watchlist():
    """主线龙头观察池新鲜度（2026-09-09 接入，供给闭环 D 防复发）。

    背景: leader_watchlist.py update（行情+setup 检查）此前从未接线——80 只活跃
    条目行情缺口靠 scout 升级顺带刷新，断链病与 breakout_scout 同源。
    判据(2026-10-10 修正为多数派语义): update 正常运行的充要特征=绝大多数活跃
    条目 last_update 齐平 qfq MAX。原 MIN(last_update) 判据会被停牌股击穿——
    实证 600293(三峡新材) 9/29 起停牌 qfq 无新数据, last_update 正确停留在停牌日,
    停牌时长累积 gap≥3 日即误报 ERROR(任务日志证实 update 每晚正常运行)。
    新判据: 齐平占比≥90% → OK(少数落后=停牌股, last_update 语义正确);
    占比<90% 才按 MIN gap 分级 ERROR/WARN(真断供时多数派必落后)。
    返回 (stats, issue, is_error)。"""
    try:
        with open(r"D:\zcodesj\data\leader_watchlist.json", encoding="utf-8") as f:
            es = json.load(f).get("entries") or []
        active = [e for e in es if e.get("status") != "dropped" and e.get("last_update")]
        con = sqlite3.connect(
            "file:" + DBS["dragon_quant_qfq"].replace("\\", "/") + "?mode=ro", uri=True)
        qfq_max = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        con.close()
    except Exception as e:
        return None, "观察池检查失败: %s" % str(e)[:100], True
    stats = {"active": len(active), "qfq_max": str(qfq_max or "")}
    if not qfq_max:
        return stats, None, False
    if not active:
        return stats, None, False
    try:
        lus = sorted(int(e["last_update"]) for e in active)
        sync_ratio = sum(1 for v in lus if v == int(qfq_max)) / len(lus)
        oldest = lus[0]
        gap = (datetime.datetime.strptime(str(qfq_max), "%Y%m%d")
               - datetime.datetime.strptime(str(oldest), "%Y%m%d")).days
    except (ValueError, TypeError):
        return stats, "观察池日期格式异常", True
    stats["oldest_update"] = oldest
    stats["lag_days"] = gap
    stats["sync_ratio"] = round(sync_ratio, 3)
    if sync_ratio >= 0.9:
        # 多数派齐平=update 健康; 少数落后=停牌股(last_update 停留在停牌日语义正确)
        return stats, None, False
    if gap >= 3:
        return stats, ("观察池update滞后%d日: 最旧条目%s qfq=%s 齐平占比%.0f%%（接线断供）"
                       % (gap, oldest, qfq_max, sync_ratio * 100)), True
    if gap >= 1:
        return stats, ("观察池update滞后%d日: 最旧条目%s qfq=%s 齐平占比%.0f%%"
                       % (gap, oldest, qfq_max, sync_ratio * 100)), False
    return stats, None, False


def check_tickflow():
    try:
        with urllib.request.urlopen("http://localhost:3018", timeout=5) as resp:
            return resp.status, None
    except Exception as e:
        return None, str(e)


def check_backup_daily_source():
    """备源三通道活性探测（2026-10-10 接线，验收=analysis/backup_daily_probe.py）。

    防"备源悄悄全死、断供时才发现"：调 build_qfq_meta_tdx.fetch_backup_raw_map
    （链内同源函数）拉 3 样本票近 10 日原始价，与 vipdoc 逐位比对。
    分层判定，不伪造健康（宪法§9）：
      主源 vipdoc 代表票不可读 → WARN（主源自身异常，备源探测无基准）；
      全通道失败 → ERROR（断供防线失效）；价偏离>0.011 → ERROR（口径污染禁补位）；
      ≥1 通道逐位一致 → OK。网络开销 3~8s/日，可接受。
    """
    try:
        sys.path.insert(0, r"D:\zcodesj")
        import build_qfq_meta_tdx as _m
        vd = _m.vipdoc_latest_date()
        if not vd:
            return "WARN", "vipdoc 代表票不可读——备源探测跳过（主源本地缺失，另查通达信）"
        sd = (datetime.datetime.strptime(vd, "%Y%m%d") - datetime.timedelta(days=10)).strftime("%Y%m%d")
        # --json 模式 stdout 是纯 JSON 契约（宪法测试 T5）；备源链路内 log() 会污染, 静默重定向
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            bm = _m.fetch_backup_raw_map(["600519", "000858", "300750"], sd, vd)
        if not bm:
            return "ERROR", "备源三通道(tencent/baostock/tushare)全部失败——断供防线失效，查网络/接口/token"
        bad = []
        for code, df in bm.items():
            vdf = _m.read_tdx_day(code)
            vmap = ({d.strftime("%Y%m%d"): r["close"] for d, r in vdf.iterrows()}
                    if vdf is not None else {})
            mism = sum(1 for d, r in df.iterrows()
                       if d.strftime("%Y%m%d") in vmap
                       and abs(float(r["close"]) - vmap[d.strftime("%Y%m%d")]) > 0.011)
            if mism:
                bad.append("%s:%d日偏离" % (code, mism))
        if bad:
            return "ERROR", "备源价与vipdoc偏离(%s)——口径污染禁止补位，先查数据源" % ",".join(bad)
        return "OK", "%d/3样本×%s 近端逐位一致" % (len(bm), ",".join(sorted(bm)))
    except Exception as e:
        return "WARN", "备源探测异常: %s: %s" % (type(e).__name__, str(e)[:100])


def check_backup_source_hitrate():
    """备源链命中率观测呈现（2026-10-20 接线，呈请2：DeepSeek审计B-4可见性补盲）。

    定位=纯呈现层：只读 analysis/backup_source_hitrate_20261018_out.json（观测件
    季度复跑追加累积，本函数不改观测件、不做网络试呼）。能力探测归
    check_backup_daily_source，两者互补不重叠。文件缺失/为空/超季未复跑
    → WARN 并说明结构化原因，不伪造健康（宪法§9）。
    """
    path = r"D:\zcodesj\analysis\backup_source_hitrate_20261018_out.json"
    try:
        if not os.path.exists(path):
            return "WARN", "命中率观测件无产出(%s)——季度复跑未执行，跑 analysis/backup_source_hitrate_20261018.py" % os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            recs = json.load(f)
        if not recs:
            return "WARN", "命中率观测件为空——季度复跑未执行，跑 analysis/backup_source_hitrate_20261018.py"
        last = recs[-1]
        ts = datetime.datetime.strptime(str(last.get("timestamp", ""))[:19], "%Y-%m-%dT%H:%M:%S")
        age = (datetime.datetime.now() - ts).days
        if age > 100:
            return "WARN", "命中率观测超季未复跑(最近%s, %d天前)——复跑 analysis/backup_source_hitrate_20261018.py" % (
                str(last.get("timestamp"))[:10], age)
        return "OK", "观测%s: 新浪%s baostock%s tushare%s" % (
            str(last.get("timestamp"))[:10], last.get("sina_hitrate"),
            last.get("baostock_hitrate"), last.get("tushare_hitrate"))
    except Exception as e:
        return "WARN", "命中率观测读取异常: %s: %s" % (type(e).__name__, str(e)[:100])


def check_census_freshness():
    """DirectionCensus 产物新鲜度探针（2026-10-14 接入）。

    背景：schtasks 失败探针只看 Last Result，抓不到"任务压根没触发"的静默漏跑——
    10/12-10/13 两日漏跑实锤（无失败码无日志），census md 停 20261009 →
    regime_gate 影子台账 20261008 后空窗。规则：最新 reports/direction_census_*.md
    生成日 < qfq 最新交易日 → ERROR（交易日 15:30 应产出当日 md）；周末/节假日
    qfq 不前移自然豁免。md 零产物 → ERROR。探测异常 → WARN 不阻断。
    """
    try:
        import glob as _glob
        import re as _re
        mds = _glob.glob(r"D:\zcodesj\reports\direction_census_*.md")
        if not mds:
            return "ERROR", "direction_census_*.md 零产物——census 链断"
        newest = max(int(_re.search(r"(\d{8})", p).group(1)) for p in mds)
        con = sqlite3.connect(
            "file:" + DBS["dragon_quant_qfq"].replace("\\", "/") + "?mode=ro", uri=True)
        qfq_max = int(con.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
        con.close()
        if newest < qfq_max:
            return "ERROR", ("最新md生成日%d < qfq最新交易日%d——census静默漏跑"
                             "(任务未触发无失败码), 主线/regime影子链降级, 查15:30调度"
                             % (newest, qfq_max))
        return "OK", "census md 生成日%d ≥ qfq %d" % (newest, qfq_max)
    except Exception as e:
        return "WARN", "census 探针异常: %s: %s" % (type(e).__name__, str(e)[:100])


def _westock_expiry_state(saved_at: float, expires_in: float, now: float = 0.0):
    """token 剩余寿命预警（纯函数便于测试）。

    背景（2026-09-05 实测）：stockbuddy token 端点签发 refresh_token 但拒绝
    refresh grant（400），token 不可续期，寿命=expires_in（当前 360000s≈100h），
    到期前必须按 SOP§四 重签。返回 None=寿命充足；str=预警文案。
    """
    import time as _time
    now = _time.time() if not now else now
    exp_h = expires_in / 3600.0
    if exp_h <= 0:
        return None
    left_h = exp_h - (now - saved_at) / 3600.0
    if left_h <= 0:
        return f"westock token 已过理论有效期 {-left_h:.0f}h——MCP直连可能401，立即按SOP§四重签"
    if left_h <= 6:
        return f"westock token 将于 {left_h:.1f}h 后过期——端点不支持续期，请按SOP§四重签"
    return None


def check_westock():
    """westock 唯一真值源探针（2026-08-27 O22b, E47 收口）。

    分层判定, 不伪造健康状态（宪法§9）:
      token 缺失 → ("ERROR", "token缺失...") —— 直接可判, 不再发无效请求;
      token 在   → 剩余寿命≤6h → WARN 预警（9/5 新增, refresh 端点不支持续期）;
                   直连 MCP 拉持仓: 失败(None) → ERROR; 返回列表(含空) → OK。
    """
    import json as _json
    tok = ""
    try:
        # 20261011 triage: 硬编码双用户名路径(yujie)改 USERPROFILE 运行时解析(同 stop_loss_executor)。
        _cfgp = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Administrator"),
                             ".zcode", "cli", "config.json")
        cfg = _json.load(open(_cfgp, encoding="utf-8"))
        ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
        tok = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    except OSError:
        pass
    if not tok:
        tok = os.environ.get("WESTOCK_TOKEN", "")
    if not tok:
        return "ERROR", ("westock token 缺失(config.json为空)——重签两步(详见SOP§四三步法)："
                         "①python D:\\workbuddyspace\\westock_oauth.py 浏览器授权"
                         "（已有登录态自动跳转，确认页点「同意授权」）；"
                         "②本地直同步 token→config（SOP§四脚本；🔴禁用 WeStockClient.refresh——"
                         "9/5 实证其端点返空会覆盖好 token）")
    # 9/5 新增: 剩余寿命预警（refresh 端点实测 400 拒绝续期，到期前必须重签）
    # _saved_at 缺失（oauth 只写原始响应）时回退文件 mtime，与 WeStockClient 语义一致
    try:
        _tp = r"D:\workbuddyspace\westock_token.json"
        tk = _json.load(open(_tp, encoding="utf-8"))
        warn = _westock_expiry_state(
            float(tk.get("_saved_at") or os.path.getmtime(_tp)),
            float(tk.get("expires_in", 0) or 0))
        if warn:
            return "WARN", warn
    except (OSError, ValueError):
        pass
    # stop_loss_executor._fetch_positions_via_mcp 自包含直连(读取同一 token 来源)
    sys.path.insert(0, r"D:\zcodesj")
    try:
        from stop_loss_executor import _fetch_positions_via_mcp
    except Exception as e:
        return "WARN", f"探针加载失败 {e}"
    pos = _fetch_positions_via_mcp()
    if pos is None:
        return "ERROR", "MCP 直连失败(401/网络/超时)——唯一真值源不可用"
    return "OK", f"持仓{len(pos)}只"


def run_gate_smoke():
    """复用 l0_system_health.py 冒烟 8 个闸门脚本。返回 (passed, failed, total)。"""
    try:
        # A5 铁律：Windows subprocess 显式 UTF-8（否则 l0_system_health 中文输出 GBK 解码失败 → 冒烟假 FAIL）
        # 20261011批4: 外层 180s < l0 内层 11项×30s=330s 最坏值——闸门慢的当天必以 (0,1,1) 报
        # ERROR 且无法区分"闸门坏"与"巡检超时"。放宽到 420s(>330s 最坏值)。
        r = subprocess.run([sys.executable, os.path.join(BASE, "l0_system_health.py")],
                           capture_output=True, text=True, timeout=420,
                           encoding="utf-8", errors="replace")
        out = r.stdout
        import re
        # 20261011批5尾: l0输出已带SKIP字段(批4c), 正则同步兼容——旧式曾致解析None→
        # 伪造"PASS 0/FAIL 1"假FAIL(全量健康首跑实抓, 消费方契约同步教训)
        m = re.search(r"PASS (\d+) / FAIL (\d+)(?: / SKIP (\d+))? / TOTAL (\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(4))
        return 0, 1, 1
    except Exception as e:
        return 0, 1, 1


# 宪法状态检查（2026-08-23 接入：宪法执行机制化第2件）
# 只输出客观可查项：宪法文件存在性/mtime。
# ⚠️ 2026-08-23 实跑修正：曾尝试对「已否定路线锚点词」做自动扫描，但边界文档/历史脚本/SOP
#    自身必然含这些词（清单/对比/判死性质），天天误报 → 伪信号 → 狼来无人看。
#    故删除自动判定；边界漂移属判断力问题，按 CONSTITUTION_SELF_CHECK A5/B5 由会话/m7 人工核对。
CONSTITUTION_FILES = ["AGENTS.md", "PROJECT_BOUNDARY.md", "CONSTITUTION_SELF_CHECK.md"]
CONSTITUTION_NOTE = "边界漂移不自动判定，按 CONSTITUTION_SELF_CHECK A5/B5 人工核对"


def check_constitution():
    """返回 (status, files_desc)。status: OK/ERROR。"""
    missing, desc = [], []
    for fn in CONSTITUTION_FILES:
        p = os.path.join(BASE, fn)
        if not os.path.exists(p):
            missing.append(fn)
            desc.append("%s❌缺失" % fn)
        else:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(p))
            desc.append("%s✅(%s)" % (fn, mt.strftime("%m-%d")))
    status = "ERROR" if missing else "OK"
    return status, desc


def check_db_backup():
    """当日 12 库备份产物齐全性（2026-08-23 备份强制化第二/三层）。

    复用 ensure_backup 守卫（单一真源）：
      - expected = 最近应完成备份的自然日（边界随主档迁移: 2026-10-17 起 22:30→05:00，
        当日 05:00 前=昨日、05:00 后=今日；推断真源=ensure_backup.expected_backup_date）；
      - 缺失 → ensure_backup 自动补跑 db_backup.py；
      - 补跑成功 = OK（WARN 仅当补跑仍失败，避免周末/停机误报狼来了）。
    """
    try:
        import ensure_backup as _eb
        status, detail = _eb.ensure()
    except Exception as e:
        return "WARN", "备份守卫异常: %s" % str(e)[:120]
    if status == "PAUSED":
        # 2026-10-18 主人令「暂时不需要备份」: 暂停=显式运维态, 独立呈现不计 WARN/ERROR
        return "PAUSED", detail + "；恢复=删除 backups/.backup_paused 标记"
    if status in ("OK", "REPAIRED"):
        return "OK", detail
    return "WARN", detail


def check_horse_race_settle():
    """赛马月度 T+20 结算接线探针(2026-10-31 缺口5闭合: 首跑20261126起月度滚动, 无探针=人工义务静默)。

    规则: 今日<20261126 → OK(探针待命); 首跑后, 当前月尚无 settle_t20 报告(asof按文件内容max取,
    规避文件名测试残留 20270601) → WARN 逾期。只读探针, 不代跑结算。
    """
    import glob, json, time as _time
    today = int(_time.strftime("%Y%m%d"))
    if today < 20261126:
        return "OK", "赛马结算首跑未到(20261126), 探针待命"
    asof_max = 0
    for f in sorted(glob.glob(r"D:\zcodesj\data\settle_t20_report_*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                asof_max = max(asof_max, int(json.load(fh).get("asof") or 0))
        except Exception:
            continue
    mx, err = table_max_date("dragon_quant_qfq", "stock_daily_meta_qfq", "date")
    if err or not mx:
        return "WARN", "赛马结算探针取交易日失败: %s" % (err or "空")
    if asof_max and int(str(asof_max)[:6]) >= int(str(today)[:6]):
        return "OK", "赛马结算当月报告在位(asof=%d)" % asof_max
    return "WARN", "赛马月度结算逾期: 当月无settle_t20报告(最新asof=%d), 履行11/26首跑+月末滚动义务" % asof_max


def check_cron_health():
    """cron 运行健康（2026-08-24 接入：cron 失败不再静默）。
    查 tasks-index.sqlite automation_runs 最近24h failed / 漏派发(skipped)。

    2026-09-10 晚修复（当日实锤伪健康）：原实现 `created_at > <ISO字符串>` 拿 INTEGER
    毫秒列与 TEXT 比较——SQLite 类型序 INTEGER<TEXT 使条件**恒假**→ 恒报 "OK 无 cron 失败"
    （9/10 实际 5 failed + 5 skipped 仍 OK，见 21:58 巡检行）；且漏派发行 outcome=NULL、
    状态落在 dispatch_status='skipped'，原 LIKE '%fail%' 亦匹配不到。
    现按 scheduled_at 毫秒整数窗口查询；failed→ERROR、仅漏派发→WARN（多为应用未运行窗口）。

    2026-09-22 晚修复（知微终检实锤伪健康第二例）：C盘重装后 workspace 键迁移，宿主活注册表
    变为用户级 C:\\Users\\Administrator\\.zcode\\v2\\tasks-index.sqlite（今日派发正常写入），
    而本检查仍读 D 盘旧工作区副本（runs 停在 9/21 22:45）→ 24h 空窗被当"无失败"= 假绿，
    真实漏派发（9/22 08:00 三合一缺派发即实例）从此查不出。修复：①在候选注册表中取
    automation_runs 最新者（活源优先，workspace 再迁移也免疫）；②空窗守卫——若选中库 24h
    零 run 且存在 enabled 且 next_run_at 已到期的自动化，报 WARN 而非 OK（空窗≠健康）。
    """
    candidates = [
        r"C:\Users\Administrator\.zcode\v2\tasks-index.sqlite",
        r"D:\zcodesj\.zcode\v2\tasks-index.sqlite",
    ]
    try:
        since_ms = int((datetime.datetime.now() - datetime.timedelta(hours=24)).timestamp() * 1000)
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        best_path, best_max = None, -1
        for cand in candidates:
            if not os.path.exists(cand):
                continue
            try:
                c = sqlite3.connect("file:%s?mode=ro" % cand, uri=True)
                mx = c.execute("SELECT COALESCE(MAX(created_at),0) FROM automation_runs").fetchone()[0]
                c.close()
                if mx > best_max:
                    best_path, best_max = cand, mx
            except sqlite3.Error:
                continue
        if best_path is None:
            return "WARN", "tasks-index.sqlite 均不可读（cron 运行历史不可查）"
        p = best_path
        c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        rows = c.execute(
            "SELECT COALESCE(a.title, r.automation_id), r.dispatch_status, r.outcome, "
            "(a.title IS NULL) AS orphan "
            "FROM automation_runs r LEFT JOIN automations a ON r.automation_id = a.automation_id "
            "WHERE r.scheduled_at > ? AND (r.outcome = 'failed' OR r.dispatch_status LIKE '%fail%' "
            "OR r.dispatch_status = 'skipped') "
            "ORDER BY r.scheduled_at DESC LIMIT 20", (since_ms,)).fetchall()
        total_24h = c.execute(
            "SELECT COUNT(*) FROM automation_runs WHERE scheduled_at > ?", (since_ms,)).fetchone()[0]
        due_pending = 0
        if total_24h == 0:
            due_pending = c.execute(
                "SELECT COUNT(*) FROM automations WHERE enabled = 1 "
                "AND next_run_at IS NOT NULL AND next_run_at <= ?", (now_ms,)).fetchone()[0]
        # 2026-10-09 加固(10/9全量测试两例实证: 14:30 failed=功能全成仅汇报层中断,
        # 9:10 running卡死11h但产物五件俱全): running>6h未收口=簿记滞留, 单列提示
        # 不混入功能失败; 链健康以产物时间戳与库行为准。
        stuck_rows = c.execute(
            "SELECT COALESCE(a.title, r.automation_id) FROM automation_runs r "
            "LEFT JOIN automations a ON r.automation_id = a.automation_id "
            "WHERE r.outcome = 'running' AND r.updated_at < ? LIMIT 5",
            (now_ms - 6 * 3600 * 1000,)).fetchall()
        c.close()
        # 2026-09-23 补(假阳第三例, 9/23 凌晨实锤): 9/22 全量换绑删除旧世代 automations 行后,
        # 其 24h 窗口内历史 run 变孤儿(a.title IS NULL)被误报当日失败。孤儿 run = 已删除任务
        # 的历史记录, 不计当前健康异常, 仅在 detail 尾注计数(信息性, 防"假绿"反向误判)。
        orphan_cnt = sum(1 for r in rows if r[3])
        rows = [r for r in rows if not r[3]]
        fails = [r for r in rows if (r[2] or "") == "failed" or "fail" in (r[1] or "")]
        skips = [r for r in rows if (r[1] or "") == "skipped" and (r[2] or "") != "failed"]
        stuck_note = ("; 状态未收口%d条(>6h running,簿记滞留先核产物): %s"
                      % (len(stuck_rows), "; ".join(r[0][:28] for r in stuck_rows))) if stuck_rows else ""
        orphan_note = ("; 另有%d条孤儿run(已删除旧世代任务历史,不计异常)" % orphan_cnt) if orphan_cnt else ""
        if fails or skips:
            detail = "; ".join("%s[%s]" % (r[0][:36], r[2] or r[1]) for r in rows[:5]) + orphan_note + stuck_note
            if fails:
                return ("ERROR", "最近24h cron 失败%d/漏派发%d: %s"
                        " || 注: failed可为簿记失真(10/9两例实证), 定性前先核该链产物时间戳"
                        % (len(fails), len(skips), detail))
            return "WARN", "最近24h cron 漏派发%d(应用未运行窗口): %s" % (len(skips), detail)
        if stuck_rows:
            return "WARN", "最近24h 无失败/漏派发%s" % stuck_note
        if total_24h == 0:
            if due_pending:
                return ("WARN", "cron runs 24h 空窗且%d条自动化已到期未派发(注册表源=%s,疑似读旧副本/宿主未运行)"
                        % (due_pending, os.path.basename(os.path.dirname(os.path.dirname(p)))))
            return ("WARN", "cron runs 24h 空窗(注册表源=%s, 若宿主长期未开机属正常)"
                    % os.path.basename(os.path.dirname(os.path.dirname(p))))
        return "OK", "最近24h 无 cron 失败/漏派发(源=%s, 24h runs=%d)" % (
            os.path.basename(os.path.dirname(os.path.dirname(p))), total_24h)
    except Exception as e:
        return "WARN", "cron 健康查询失败: %s" % str(e)[:80]


def check_schtasks():
    """L1 schtasks 备份任务存在性（2026-08-24 接入）。
    schtasks 05:00 独立调度兜底(2026-10-17 自 22:30 迁移)，任务被误删时巡检发现。
    """
    try:
        # 2026-09-10 加固: shell=True 改 False(参数全为常量, 无注入面; 消除 Mimosa
        # 命令注入类告警的真实签名——shell=True+列表是历史写法)
        r = subprocess.run(
            ["schtasks.exe", "/query", "/tn", "tulong_db_backup_daily"],
            capture_output=True, text=True, timeout=10, shell=False,
            encoding="gbk", errors="replace")
        if r.returncode == 0:
            return "OK", "tulong_db_backup_daily 任务存在"
        return "WARN", "tulong_db_backup_daily 任务不存在（L1 备份兜底缺失）"
    except Exception as e:
        return "WARN", "schtasks 查询失败: %s" % str(e)[:80]


def check_schtasks_failures():
    """schtasks 失败探针（2026-09-13 接入：自省轮发现调度静默失效面）。
    查近7天内 Last Result 非0的屠龙域任务（滤除第三方/系统任务噪音）；只报可见性不自愈。
    背景：9/13 自省发现 107 任务带电源条件强杀（DisallowBatt/StopOnBatt）+ GateWeeklyWinrate 幽灵条目
    （任务文件丢失仅剩缓存）——此类失效此前无人知（schtasks 无失败监控面，cron 侧已有 cron_health）。
    退出码约定：0=成功 / 267009=运行中 / 267011=从未运行 / 267014=用户终止（均不算失败）。
    """
    import csv as _csv
    import io as _io
    KNOWN_FAILS = []
    try:
        _kp = r"D:\zcodesj\data\schtasks_known_failures.json"
        if os.path.exists(_kp):
            import json as _json
            KNOWN_FAILS = _json.load(open(_kp, encoding="utf-8")).get("known", [])
    except Exception:
        pass
    KEYS = ("tulong", "portrait", "westock", "bpep", "s5t1", "sentinel", "m1daily", "newspit",
            "factordecay", "pullbackalert", "riskalert", "s2b_", "vipdocprobe", "eastmoney",
            "sell_", "winnerprofile", "leaderwatch", "breakoutscout", "garchshadow", "missedcost",
            "preregwatch", "slfallback", "kronos", "tickflow", "db_backup", "gateweekly")
    BENIGN = {"0", "267009", "267011", "267014"}
    try:
        r = subprocess.run(["schtasks.exe", "/query", "/fo", "csv", "/v"],
                           capture_output=True, text=True, timeout=60, shell=False,
                           encoding="gbk", errors="replace")
        if r.returncode != 0:
            return "WARN", "schtasks 全量查询失败(rc=%s)" % r.returncode
        rows = list(_csv.DictReader(_io.StringIO(r.stdout)))
        now = datetime.datetime.now()
        bad = []
        _n_domain = 0
        for row in rows:
            name = (row.get("任务名") or row.get("TaskName") or "").strip().lstrip("\\")
            if not name:
                continue
            # 20261011批3结构化识别(替代9/13建表的纯任务名白名单): 白名单只认任务名,
            # 其后新增任务(MondayBuy/StopLoss链/Sentinel_1430/SealSnapshot等~30个)从未
            # 同步进表=失败监控盲区, 白名单必然再腐。屡龙域判定改为"任务行全文含
            # zcodesj"为主(列名无关, 对 GBK CSV 表头变化免疫), 任务名 KEYS 保留为兑底。
            _rowtxt = " ".join(str(v) for v in row.values() if v).lower()
            if "zcodesj" not in _rowtxt and not any(k in name.lower() for k in KEYS):
                continue
            _n_domain += 1
            rc = (row.get("上次结果") or row.get("Last Result") or "").strip()
            lrt = (row.get("上次运行时间") or row.get("Last Run Time") or "").strip()
            if rc in BENIGN or not rc:
                continue
            try:
                t = datetime.datetime.strptime(lrt, "%Y/%m/%d %H:%M:%S")
                if (now - t).days > 7:
                    continue
            except Exception:
                pass  # 时间不可解析仍报(留痕)
            # 20260923 结构性豁免: TulongWake_* 为防睡眠 timeout dummy(命令=timeout /t 2700
            # 空转占会话), 会话被终止/注销即退出 rc=C000013A, 属正常退出语义无信息量
            # (9/22-9/23 连续两日伪告警实证); 非 wake 型同 rc 不豁免仍报。
            # 20260924 扩展: rc=800710E0(操作员/管理员拒绝请求)=睡眠/锁屏窗口补跑被拒,
            # 对 timeout dummy 同样无信息量(9/23 22:25 TulongWake_2155 实证, 与 22:00
            # cron skip 同根因睡眠窗口); 数据型任务同 rc 不豁免仍报。
            if name.startswith("TulongWake") and rc in ("-1073741510", "-2147020576"):
                continue
            # 已知项登记过滤(20260913: triage完毕的历史失败不重复告警, 防告警疲劳; 新失败仍报)
            if any(k["task"] == name and k["rc"] == rc and k["date"] == lrt for k in KNOWN_FAILS):
                continue
            bad.append("%s[rc=%s@%s]" % (name, rc, lrt))
        if bad:
            return "ERROR", "近7天屠龙域 schtasks 失败%d: %s" % (len(bad), "; ".join(bad[:5]))
        return "OK", "近7天屠龙域 schtasks 无失败(屡龙域扫%d/全表%d任务)" % (_n_domain, len(rows))
    except Exception as e:
        return "WARN", "schtasks 失败探针异常: %s" % str(e)[:80]


def check_schtasks_bare_python():
    """L1 schtasks 裸python守卫（2026-09-07 接入：9/7 清仓卖单+买入批定时任务全哑火事故）。

    根因: 裸 `python xxx.py` 在 schtasks 环境(无 shell profile)解析不到解释器
    → 0x80070002 文件未找到 → 任务触发但全部静默失败。正确形态 = cmd /c xxx_run.cmd
    启动器(内部全路径 python.exe)。

    单次 `schtasks /query /fo csv /v` 全量扫描(400 任务 ~5s):
      - 屠龙域(参数含 zcodesj)裸python = ERROR（会静默杀死交易/研究链）;
      - 外域(workbuddyspace 等,owner=D:\\workbuddyspace) = WARN（报备不越权修改）。

    2026-09-08 盲区补丁: 原逻辑只查命令首词, 漏检 `cmd /c ... && python xxx.py`
    形态(cmd 内裸python 同样依赖 PATH, 与 9/7 事故同型)。现追加整条命令正则
    扫描(排除全路径 python.exe / py_run.cmd 启动器)。
    """
    try:
        # 2026-09-10 加固: shell=True 改 False(参数全为常量)
        r = subprocess.run(["schtasks.exe", "/query", "/fo", "csv", "/v"],
                           capture_output=True, timeout=120, shell=False)
        rows = list(csv.reader(r.stdout.decode("gbk", errors="replace").splitlines()))
        if len(rows) < 2:
            return "WARN", "schtasks /v 无输出, 守卫降级"
        hdr = rows[0]
        name_i = next((i for i, h in enumerate(hdr) if h == "任务名"), 1)
        cmd_i = next((i for i, h in enumerate(hdr) if "要运行的任务" in h), None)
        if cmd_i is None:
            return "WARN", "未找到[要运行的任务]列, 守卫降级"
        bare = []
        # 2026-09-08 盲区补丁: 匹配命令中任意位置的裸 python(排除全路径与启动器名)
        bare_py_re = re.compile(
            r'(?<![\\/A-Za-z0-9_.])python(?:3)?(?:\.exe)?(?=\s|"|$)', re.IGNORECASE)
        for row in rows[1:]:
            if len(row) <= max(cmd_i, name_i):
                continue
            to_run = row[cmd_i].strip()
            parts = to_run.split()
            if not parts:
                continue
            base = os.path.basename(parts[0]).lower()
            hit = base in ("python", "python.exe", "python3", "python3.exe", "py", "py.exe") \
                or bare_py_re.search(to_run)
            if hit:
                domain = "zcodesj" if "zcodesj" in to_run.lower() else "external"
                bare.append((row[name_i].lstrip("\\"), domain))
        zl = [b[0] for b in bare if b[1] == "zcodesj"]
        ext = [b[0] for b in bare if b[1] != "zcodesj"]
        if zl:
            return "ERROR", "屠龙域裸python %d 个(触发即静默哑火,须换cmd启动器): %s" % (
                len(zl), "; ".join(zl[:5]))
        if ext:
            return "WARN", "外域裸python %d 个(报备不越权): %s" % (
                len(ext), "; ".join(ext[:5]))
        return "OK", "全部 %d 任务无裸python" % (len(rows) - 1)
    except Exception as e:
        return "WARN", "裸python扫描失败: %s" % str(e)[:80]


def check_cmd_bare_call_chain():
    """L1 cmd 裸调用守卫（2026-10-31 接入：batch_plan_run.cmd 假绿灯事故，SOP一百九十补记二）。

    根因: 多腿 .cmd 内不带 `call` 调用另一 .cmd（含 py_run.cmd）= cmd 控制权单程转移，
    后续腿永不执行且 rc=首腿 rc（假绿灯）。10/26 起每周六计划生成第二腿从未自动执行过，
    历次周计划均为会话人工直出。

    规则: 扫描 D:\\zcodesj 全部 *.cmd（递归, 排除 archive/.git/.zcode），凡出现
    「不以 call 开头的 .cmd 调用行」且其后仍存在可执行行（非注释/空行/exit /b）= ERROR；
    单腿裸调用（后面无可执行行）= 合法，不报。扫描失败降级 WARN 不阻断体检。
    """
    import glob as _glob
    try:
        root = r"D:\zcodesj"
        files = [f for f in _glob.glob(os.path.join(root, "**", "*.cmd"), recursive=True)
                 if not any(s in f for s in ("\\archive\\", "\\_archive\\", "\\_git\\", "\\.zcode\\"))]
        bad = []
        for f in files:
            try:
                lines = [l.rstrip() for l in open(f, encoding="utf-8", errors="replace").read().splitlines()]
            except OSError:
                continue
            for i, raw in enumerate(lines):
                s = raw.strip()
                if not s or s.startswith(("rem ", "rem:", "@echo", "::")):
                    continue
                low = s.lower()
                # .cmd 调用行形态: 首token以.cmd结尾, 或 call 之外引用 py_run.cmd 等
                first = low.split()[0] if low.split() else ""
                is_call = low.startswith("call ")
                is_cmd_invoke = first.endswith(".cmd") and not is_call
                if not is_cmd_invoke:
                    continue
                # 其后是否存在可执行行
                rest = [l.strip() for l in lines[i + 1:]
                        if l.strip() and not l.strip().startswith(("rem ", "@echo", "::"))]
                if rest and not all(l.lower().startswith("exit /b") for l in rest):
                    bad.append((os.path.relpath(f, root), s[:70]))
                    break
        if bad:
            return "ERROR", "cmd裸调用陷阱 %d 个(多腿后续行不执行/假绿灯): %s" % (
                len(bad), "; ".join("%s[%s]" % (f, s) for f, s in bad[:4]))
        return "OK", "全部 %d 个cmd无裸调用陷阱" % len(files)
    except Exception as e:
        return "WARN", "cmd裸调用扫描失败: %s" % str(e)[:80]


def check_schtasks_quoted_paths():
    """L1 schtasks 引号守卫（2026-09-24 接入：EastmoneyDataRefresh 空格路径未加引号案）。

    根因: cmd 命令串里 `C:\\Program Files\\...python.exe`（空格路径）未加引号 → cmd 按空格
    截断成 `C:\\Program` → 任务触发即秒败 rc=1（"不是内部或外部命令"）。9/22 重装后
    python 迁入带空格路径, 3:30 东财增量任务自 9/22 起天天哑火, 靠 22:00 链环节掩盖无感知。

    经 Get-ScheduledTask Actions 精确读取命令串（schtasks /v CSV 会吞命令串内首引号,
    引号审计不可走 CSV）。判定: 命令串每个 `C:\\Program Files` 出现点, 向前跳过空格/& 后
    若非 `"` 即为未引号。
    屠龙域(zcodesj) = ERROR（会静默杀死交易/研究链）; 其它 = WARN（报备不越权）。
    PowerShell 不可用（CIM 瞬时取消等）→ WARN 降级 fail-open, 重试 1 次。
    """
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$out=@();"
        "foreach($t in Get-ScheduledTask){"
        "foreach($a in $t.Actions){"
        "if($null -eq $a){continue};"
        "$c='' + $a.Execute + ' ' + $a.Arguments;"
        "if($c -notmatch 'zcodesj|workbuddyspace|anzhuang|Python313'){continue};"
        "$pos=$c.IndexOf('C:\\Program Files');"
        "while($pos -ge 0){"
        "$j=$pos-1;"
        "while(($j -ge 0) -and (($c[$j] -eq ' ') -or ($c[$j] -eq '&'))){$j--};"
        "if(($j -lt 0) -or ($c[$j] -ne '\"')){$out+=($t.TaskName)}"
        "$pos=$c.IndexOf('C:\\Program Files',($pos+16))"
        "}"
        "}"
        "};"
        "$out | Select-Object -Unique"
    )
    for attempt in (1, 2):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True, timeout=120, shell=False)
            if r.returncode != 0 and attempt == 1:
                time.sleep(5)
                continue
            lines = [ln.strip() for ln in r.stdout.decode("gbk", errors="replace").splitlines()
                     if ln.strip()]
            zl = [x for x in lines if "zcodesj" in x.lower()]
            ext = [x for x in lines if x not in zl]
            if zl:
                return "ERROR", ("屠龙域空格路径未引号 %d 个(cmd截断秒败, 须加引号): %s"
                                 % (len(zl), "; ".join(zl[:5])))
            if ext:
                return "WARN", "外域空格路径未引号 %d 个(报备不越权): %s" % (
                    len(ext), "; ".join(ext[:5]))
            return "OK", "全部任务空格路径均已引号"
        except Exception as e:
            if attempt == 2:
                return "WARN", "引号扫描失败(fail-open): %s" % str(e)[:80]
            time.sleep(5)
    return "WARN", "引号扫描降级"


def check_knowledge_base():
    """知识库状态（2026-08-24 接入：kb/ 索引存在性 + 行数 + 基线）。

    轻量检查（不加载 bge 模型避免巡检过重）：
      - LanceDB 向量表存在 + 行数；
      - ingest_log 基线条数；
      - kb/ 文件数。
    缺失/为空 = WARN（不阻断交易，提示需 index_builder 重建）。
    """
    try:
        import lancedb
        kb_root = os.path.join(BASE, "kb")
        # 1. LanceDB 表
        db = lancedb.connect(os.path.join(kb_root, "lancedb"))
        rows = None
        try:
            tbl = db.open_table("tulong_kb")
            rows = tbl.count_rows()
        except Exception:
            rows = None
        # 2. ingest_log 基线
        baseline = 0
        log_path = os.path.join(kb_root, "ingest_log.jsonl")
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                baseline = sum(1 for l in f if l.strip())
        # 3. kb/ md 文件数
        md_count = 0
        if os.path.isdir(kb_root):
            for _root, _dirs, _files in os.walk(kb_root):
                md_count += sum(1 for fn in _files if fn.endswith(".md"))
        if rows is None:
            return "WARN", "向量表缺失(需 index_builder.py 重建)"
        if rows <= 0:
            return "WARN", "向量表为空(%d 块)" % rows
        return "OK", "向量 %d 块 / 基线 %d / md %d" % (rows, baseline, md_count)
    except Exception as e:
        return "WARN", "知识库检查异常: %s" % str(e)[:120]


NIGHT_WATCH_DBS = {
    "dragon_quant": r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
    "dragon_quant_meta": r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
    "dragon_quant_qfq": r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
}
NIGHT_WATCH_STATE = r"D:\zcodesj\data\night_watch_state.json"
# v2 数据级探针（2026-09-04）：库 -> (路径, 主表, 日期列)；均须有日期索引（实测 MAX 0.00s）
NIGHT_WATCH_DATA_PROBES = {
    "dragon_quant_qfq": (r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db", "stock_daily_meta_qfq", "date"),
    "dragon_quant_meta": (r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db", "stock_daily_meta", "date"),
    "fundamental_data": (r"D:\workbuddyspace\databases\fundamental_data.db", "daily_basic", "trade_date"),
}


def _night_probe_data_dates():
    """只读探针：各监视库主表最新交易日。失败记 None（N/A），不阻断巡检。"""
    dates = {}
    for name, (path, table, col) in NIGHT_WATCH_DATA_PROBES.items():
        try:
            if not (str(table).isidentifier() and str(col).isidentifier()):
                dates[name] = None
                continue
            con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
            try:
                v = con.execute("SELECT MAX(%s) FROM %s" % (col, table)).fetchone()[0]
                dates[name] = str(v) if v is not None else None
            finally:
                con.close()
        except Exception:
            dates[name] = None
    return dates


def check_night_write_watch(now):
    """准则33 夜间数据写入监督 v2（2026-09-04 升级自 v1 mtime 级）。
    v1 缺陷：多运行面共库现状（tickflow 面板/维护操作/并行会话 touch 37.9GB 主库
    mtime，2026-09-04 00:32 实证 mtime 变而各表数据最新仍为 9/3）导致禁改时段
    mtime 告警噪音化。v2 改数据级：仅当禁改时段内「库内最新交易日前进」（真实新
    数据落库）才 WARN；mtime 变而数据未变降级为 note「运行面触碰」。
    合法写段 = 22:00 定时链(至23:29) 与 02:00 凌晨链(02:00-03:04)；禁改时段 =
    23:30-01:59 与 03:05-06:29。WARN 时不刷新基线（告警持续可见至合法段确认）。"""
    detail = {"status": "OK", "note": "准则33 v2 数据级监督", "changed": [], "touched": []}
    hhmm = now.hour * 100 + now.minute
    in_gap = (2330 <= hhmm) or (hhmm <= 159) or (305 <= hhmm <= 629)
    try:
        cur_mtime = {k: int(os.path.getmtime(p)) for k, p in NIGHT_WATCH_DBS.items()
                     if os.path.exists(p)}
        cur_data = _night_probe_data_dates()
        prev_data, prev_mtime = {}, {}
        if os.path.exists(NIGHT_WATCH_STATE):
            try:
                with open(NIGHT_WATCH_STATE, encoding="utf-8") as f:
                    _s = json.load(f)
                prev_data = _s.get("data_dates") or {}
                prev_mtime = _s.get("mtimes") or {}
            except Exception:
                prev_data, prev_mtime = {}, {}
        chg = sorted(k for k, v in cur_data.items()
                     if v and prev_data.get(k) and str(v) > str(prev_data[k])) if (in_gap and prev_data) else []
        touched = sorted(k for k, v in cur_mtime.items()
                         if prev_mtime.get(k) and v != prev_mtime[k] and k not in chg) if (in_gap and prev_mtime) else []
        if chg:
            detail["status"] = "WARN"
            detail["changed"] = chg
            if touched:
                detail["note"] += " | 运行面触碰(数据未变): %s" % ",".join(touched)
            return detail, ["准则33夜间禁改时段检测到新数据写入: %s (数据级v2)" % ",".join(chg)]
        if touched:
            detail["note"] += " | 运行面触碰(数据未变): %s" % ",".join(touched)
        d = os.path.dirname(NIGHT_WATCH_STATE)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        with open(NIGHT_WATCH_STATE, "w", encoding="utf-8") as f:
            json.dump({"updated": now.isoformat(), "mtimes": cur_mtime,
                       "data_dates": cur_data}, f, ensure_ascii=False)
    except Exception as e:
        detail["status"] = "ERROR"
        return detail, ["夜写监督自检失败: %s" % str(e)[:80]]
    return detail, []


def check_qfq_write_window(now):
    """E70 qfq 写库窗口消费防护提示（2026-08-28 接入）。
    22:00 链在 ~22:00-23:30 分批写 qfq 库当日行；窗口内读取会拿到半截数据
    （8/27 22:15 首拉 8/27 行与收盘实价不一致、22:49 复核一致的暂态实证）。
    窗口内 → 提示 WARN（提示性，不改变巡检结论级别——巡检只读不算消费方）；
    窗口外 → OK。研究/复盘消费方须过滤"完成日"或窗口外重读。"""
    hhmm = now.hour * 100 + now.minute
    in_window = 2200 <= hhmm <= 2330
    note = ("22:00链写库窗口内(至约23:30)，qfq当日行可能为写入中途暂态，"
            "消费方(研究/复盘/选股)禁直接引用当日行，须窗口后重读" if in_window
            else "窗口外，qfq 当日行（如有）可正常引用")
    return {"status": "WARN" if in_window else "OK", "in_window": in_window, "note": note}, \
        (["qfq写库窗口提示(E70): 窗口内读取=半截数据风险，详见note"] if in_window else [])


def check_qfq_amount_guard():
    """amount 质量守卫（2026-09-06 接入；20260906 全史 amount 事故产物）。
    qfq 库 amount 列曾自建库起 93%+ 行脏（假天量/近零），已 vipdoc 对账修复 1074 万行；
    本检查为复防：quick 模式 vwap 自洽率（除权误报基线约 12%，阈值 WARN15/FAIL30）。
    FAIL → ERROR（数据真相级）；UNAVAILABLE → WARN（主库重建期 0 字节属正常暂态）。
    精确复检/修复用 qfq_amount_guard.py --mode audit 与 fix_qfq_amount_20260906.py。"""
    try:
        from qfq_amount_guard import run_check
        r = run_check(DBS["dragon_quant_qfq"], mode='quick', months=3)
    except Exception as e:
        return {"status": "WARN", "rate": None, "note": "守卫执行异常: %s" % str(e)[:80]}, \
            ["qfq amount 守卫执行异常(视为 WARN，不阻断): %s" % str(e)[:80]]
    note = r["detail"]
    if r["status"] == "FAIL":
        return {"status": "ERROR", "rate": r["rate"], "note": note}, \
            ["qfq amount 守卫 FAIL: %s — 禁消费 amount 列，执行 fix_qfq_amount_20260906.py" % note]
    if r["status"] == "UNAVAILABLE":
        return {"status": "WARN", "rate": None, "note": note}, \
            ["qfq amount 守卫: 库不可用(WARN 不 ERROR，主库重建期属正常暂态)"]
    if r["status"] == "WARN":
        return {"status": "WARN", "rate": r["rate"], "note": note}, \
            ["qfq amount 守卫 WARN: %s" % note]
    return {"status": "OK", "rate": r["rate"], "note": note}, []


def check_qfq_close_guard():
    """qfq 库 close 列完整性守卫（2026-09-08 接入）。
    close 列此前无 owner：amount_guard.quick 将 NULL/零 close 静默排除出 vwap 分母、
    qfq_sanity_check 对 NULL/零 close 静默 continue——close 若重演 amount 式污染
    两守卫均绿灯。本检查：窗口内 close IS NULL/<=0 行率（真值=0，阈值 WARN>0/FAIL>0.1%）
    → FAIL 即 ERROR（数据真相级）；UNAVAILABLE→WARN；异常 fail-open WARN 不阻断。"""
    try:
        from qfq_close_guard import run_check
        r = run_check(DBS["dragon_quant_qfq"], days=90)
    except Exception as e:
        return {"status": "WARN", "rate": None, "note": "守卫执行异常: %s" % str(e)[:80]}, \
            ["qfq close 守卫执行异常(视为 WARN，不阻断): %s" % str(e)[:80]]
    note = r["detail"]
    if r["status"] == "FAIL":
        return {"status": "ERROR", "rate": r["rate"], "note": note}, \
            ["qfq close 守卫 FAIL: %s — 禁消费 close 列，先审计修复再放行" % note]
    if r["status"] == "UNAVAILABLE":
        return {"status": "WARN", "rate": None, "note": note}, \
            ["qfq close 守卫: 库不可用/窗口无行(WARN 不 ERROR，不伪健康)"]
    if r["status"] == "WARN":
        return {"status": "WARN", "rate": r["rate"], "note": note}, \
            ["qfq close 守卫 WARN: %s" % note]
    return {"status": "OK", "rate": r["rate"], "note": note}, []


def check_qfq_cell_revision_guard():
    """qfq 库历史单元格修订审计守卫（2026-09-20 接入，自省 S8/M5，漂移 v3 移交项）。
    9/13-14 型历史单元格修订（修订后值内部自洽）此前无 owner，close/amount 守卫均绿灯。
    本检查：月级特征校验和（行数+六列 SUM）vs 基线 data/qfq_cell_baseline.json；
    检测区=完整月且距 qfq_max>7 自然日（近端=合法回填窗口）；FAIL 即 ERROR（数据真相级，
    禁消费该区间先备份 diff 审计）；BASELINE_CREATED/UNAVAILABLE→OK/WARN；异常 fail-open WARN。"""
    try:
        from qfq_cell_revision_guard import run_check
        r = run_check(DBS["dragon_quant_qfq"])
    except Exception as e:
        return {"status": "WARN", "note": "守卫执行异常: %s" % str(e)[:80]}, \
            ["qfq 单元格修订守卫执行异常(视为 WARN，不阻断): %s" % str(e)[:80]]
    note = r["detail"]
    if r["status"] == "FAIL":
        return {"status": "ERROR", "note": note}, \
            ["qfq 单元格修订守卫 FAIL: %s — 禁消费变更月，先 db_backup diff 审计" % note]
    if r["status"] == "UNAVAILABLE":
        return {"status": "WARN", "note": note}, \
            ["qfq 单元格修订守卫: 库不可用(WARN 不 ERROR，不伪健康)"]
    return {"status": "OK", "note": note}, []


def check_py_compile_guard():
    """git 跟踪 Python 面编译完整性守卫（2026-09-08 接入）。
    语法损坏文件此前无 owner：daily_signal_production.py/v2 弃用后无法编译存续
    17 天无人发现。本检查：跟踪面(排除 archive/qlib-learning/_ts_test)逐文件
    py_compile，错误>0 → FAIL 即 ERROR（代码真相级）；git 不可用/跟踪面空 →
    UNAVAILABLE→WARN；异常 fail-open WARN 不阻断。实测 841 文件约 2.1s。"""
    try:
        from py_compile_guard import run_check
        r = run_check()
    except Exception as e:
        return {"status": "WARN", "errors": None, "note": "守卫执行异常: %s" % str(e)[:80]}, \
            ["py_compile 守卫执行异常(视为 WARN，不阻断): %s" % str(e)[:80]]
    note = r["detail"]
    if r["status"] == "FAIL":
        return {"status": "ERROR", "errors": r["errors"], "note": note}, \
            ["py_compile 守卫 FAIL: %s — 存在无法编译的跟踪文件，先修复或归档" % note]
    if r["status"] == "UNAVAILABLE":
        return {"status": "WARN", "errors": None, "note": note}, \
            ["py_compile 守卫: git 跟踪面不可评估(WARN 不 ERROR，不伪健康)"]
    return {"status": "OK", "errors": 0, "note": note}, []


def check_step4_products(now):
    """22:00 链第四步(盘前信号预生成)产物探针（2026-08-28 接入，E62 夜链缺口检测）。

    目标交易日 = 上一完整交易日(vipdoc max)；任一产物缺失 → WARN 提示（巡检只读不改写）。
    自愈入口：`python chain_step4_probe.py --fix`（幂等生成器重跑，供 09:10 会话按需执行）。
    """
    try:
        sys.path.insert(0, r"D:\zcodesj")
        import chain_step4_probe as sp
        d = sp.expected_trading_date()
        if not d:
            return {"status": "OK", "note": "vipdoc 无交易日可推导"}, []
        p = sp.probe(d)
        if p["all_ok"]:
            return {"status": "OK", "date": d, "note": "步4三产物齐备"}, []
        note = ("步4产物缺失(%s): %s — 09:10 前运行 `python chain_step4_probe.py --fix` 自愈"
                % (d, ",".join(p["missing"])))
        return {"status": "WARN", "date": d, "note": note}, ["步4产物缺失(E62防复发): " + note]
    except Exception as e:
        return {"status": "ERROR", "note": f"步4探针自检异常: {type(e).__name__}: {e}"}, []


def check_portrait_feed():
    """画像快照探针（2026-09-05 对接接入，只读）：data/portrait_feed.json 存在性/新鲜度/影子状态。

    画像当前=提示性标注层（零拦截权），断供不影响交易链 → 缺失/超龄只 WARN 不 ERROR。
    自愈入口：`python portrait_feed.py build`（幂等，读 portrait_agent 最新截面）。"""
    import json as _json
    feed_f = os.path.join(r"D:\zcodesj", "data", "portrait_feed.json")
    if not os.path.exists(feed_f):
        return {"status": "WARN", "note": "快照不存在: python portrait_feed.py build"}, \
               ["画像快照缺失(白名单标注将无画像列): python portrait_feed.py build"]
    try:
        with open(feed_f, encoding="utf-8") as f:
            s = _json.load(f)
        if s.get("unavailable"):
            return {"status": "WARN", "note": "快照不可用: %s" % s["unavailable"]}, \
                   ["画像快照不可用: %s" % s["unavailable"]]
        age_h = (datetime.datetime.now() - datetime.datetime.fromtimestamp(
            os.path.getmtime(feed_f))).total_seconds() / 3600.0
        note = ("截面%s lag=%sd 影子=%s(%s) 快照%.0fh前"
                % (s.get("snapshot_date"), (s.get("freshness") or {}).get("lag_days"),
                   (s.get("shadow") or {}).get("phase"), (s.get("shadow") or {}).get("weight"),
                   age_h))
        status = "OK"
        if age_h > 30:
            status = "WARN"
            note += " (快照>30h未刷新, 检查15:15/22:50画像任务)"
        return {"status": status, "note": note}, ([] if status == "OK" else ["画像快照: " + note])
    except Exception as e:
        return {"status": "WARN", "note": "快照解析失败: %s: %s" % (type(e).__name__, e)}, \
               ["画像快照解析失败: %s: %s" % (type(e).__name__, e)]


def check_portrait_permissions_gate():
    """画像零交易权 CI 门探针（2026-10-11 批1' 接入）：交易链零画像引用/permissions 决策位/
    manifest 防手改哈希/三源 asof 互差。owner=check_portrait_permissions.py（--root 沙箱可测）。
    违规=画像防线破口 → ERROR 级；rc=2 配置错误 → WARN（不冒充违规）。自愈=生成器重跑(幂等)。"""
    import subprocess as _sp
    r = _sp.run([sys.executable, os.path.join(r"D:\zcodesj", "check_portrait_permissions.py")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    tail = [l.strip() for l in (r.stdout or "").splitlines() if l.strip().startswith("[FAIL]")]
    _lines = (r.stdout or "").strip().splitlines()
    note = (tail[-1] if tail else (_lines[-1] if _lines else ""))[:200]
    if r.returncode == 0:
        return {"status": "OK", "note": "CI门通过 " + note}, []
    if r.returncode == 1:
        return {"status": "ERROR", "note": note}, ["画像零交易权CI门违规: " + note]
    return {"status": "WARN", "note": "CI门配置异常rc=2: " + note}, \
        ["画像CI门配置异常(rc=2, 非违规): " + note]


def check_pit_isolation_gate():
    """PIT 弃用隔离 CI 门探针（2026-10-18 DeepSeek审计V6 接入）：活跃树禁用表引用扫描
    (v1_deprecated / factor_daily 稀疏表) + 墓碑自检。owner=check_pit_deprecated_isolation.py。
    违规=财务因子前视/稀疏静默失效风险破口 → ERROR 级（数据真相类）；rc=2 配置异常 → WARN。"""
    import subprocess as _sp
    r = _sp.run([sys.executable, os.path.join(r"D:\zcodesj", "check_pit_deprecated_isolation.py")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    tail = [l.strip() for l in (r.stdout or "").splitlines() if l.strip().startswith("[FAIL]")]
    _lines = (r.stdout or "").strip().splitlines()
    note = (tail[-1] if tail else (_lines[-1] if _lines else ""))[:200]
    if r.returncode == 0:
        return {"status": "OK", "note": "PIT隔离门通过 " + note}, []
    if r.returncode == 1:
        return {"status": "ERROR", "note": note}, ["PIT弃用隔离CI门违规: " + note]
    return {"status": "WARN", "note": "PIT隔离门配置异常rc=2: " + note}, \
        ["PIT隔离门配置异常(rc=2, 非违规): " + note]


def check_panel_v6():
    """panel_v6 截面探针（2026-09-12 接入，只读 WARN 级）：画像日链(chan_batch→panel_v6_update
    →引擎A/组合C影子)的唯一新鲜度窗口。9/10-9/11 panel 断跑两天而巡检无感知的盲区堵补——
    原画像快照行看的是 portrait_feed(22:00链产物)，与本链不同源。
    判定: panel_max < qfq_max → WARN(该链产物整体滞后, 引擎A影子/组合C影子无新截面)。
    附带校验 parquet 派生缓存(研究侧投影源)与 pkl 的同步(mtime)。
    自愈入口: `python portrait_agent/v6_research/daily_portrait_update.py`(手工链, 接线待主人裁决; 幂等; 2026-09-25线B件迁v6_research)。"""
    pkl_f = os.path.join(r"D:\zcodesj\portrait_agent\cache", "panel_v6_full.pkl")
    pq_f = os.path.join(r"D:\zcodesj\portrait_agent\cache", "panel_v6_full.parquet")
    if not os.path.exists(pkl_f):
        return {"status": "WARN", "note": "panel_v6_full.pkl 不存在"}, \
               ["panel_v6: pkl 不存在(画像日链从未跑或被移)"]
    try:
        con = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
        qmax = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        con.close()
        import pandas as _pd
        if os.path.exists(pq_f) and os.path.getmtime(pq_f) >= os.path.getmtime(pkl_f):
            pmax = str(_pd.read_parquet(pq_f, columns=["date"])["date"].max())
            src = "parquet"
        else:
            pmax = str(_pd.read_pickle(pkl_f)["date"].astype(str).max())
            src = "pkl(整载~15s)"
        note = "截面%s qfq=%s 源=%s" % (pmax, qmax, src)
        sync = ""
        if os.path.exists(pq_f) and os.path.getmtime(pq_f) < os.path.getmtime(pkl_f):
            sync = " parquet缓存落后(研究侧投影旧): python portrait_agent/v6_research/panel_v6_to_parquet.py"
        if pmax < str(qmax):
            note += " 落后→WARN" + sync
            return {"status": "WARN", "note": note}, \
                   ["panel_v6落后qfq(引擎A/组合C影子无新截面): python portrait_agent/v6_research/daily_portrait_update.py" + sync]
        return {"status": "OK", "note": note + sync}, []
    except Exception as e:
        return {"status": "WARN", "note": "探针失败: %s: %s" % (type(e).__name__, e)}, \
               ["panel_v6探针失败: %s: %s" % (type(e).__name__, e)]


CHAIN_TIMING_BUDGETS = {
    # 2026-10-14 根治令: 环节耗时预算(秒)。基线=10/12-10/13链日志+注册表化并行后预期。
    # daily_basic>300 = tushare快路径未生效(配额被烧/源故障)落兜底——单列预算因主路径仅数秒;
    # qfq>900 = tickflow预拉取超时重演; aux 预算=新件膨胀或并行池失效——防慢性膨胀复发。
    # 2026-10-21 主人「继续」批准: aux 900→1800 与链内 patterns 子件超时上调(900→1800,
    # 晚间窗口 akshare 限频致连续两晚撞 900)联动——patterns 正常满额 ~1000-1400s,
    # 1800 仍保留对真膨胀/并行池失效的甄别力; 今日实测 aux=1426s 已超旧预算即此因。
    "daily_basic": 300, "qfq": 900, "aux": 1800,
}
CHAIN_TOTAL_WARN_S = 2700   # 整链>45min WARN。2026-10-21 与 patterns 超时上调联动校准(原2400/40min):
                            # patterns 满额回灌为每交易日常态, 正常包络 ~2450-2600s, 2400 每晚必响=噪音失效;
                            # ERROR 3600 不动(qfq/patterns 双超时等真异常夜仍可触达)。
CHAIN_TOTAL_ERR_S = 3600    # 整链>60min ERROR


def check_chain_timing():
    """链耗时预算告警（2026-10-14 根治令接入）: 读 data/chain_timing_latest.json
    (daily_chain_update 链尾写), 堵"aux 86s→1111s 涨 13 倍无人看见"类慢性膨胀盲区。
    分级: 台账缺失/停滞>4天 → WARN(链尾环节可能未跑完); 环节超预算 → WARN;
    整链超 60min → ERROR。只读告警不动链。"""
    path = os.path.join(r"D:\zcodesj\data", "chain_timing_latest.json")
    if not os.path.exists(path):
        return "WARN", "链耗时台账缺失(链未跑到链尾或首次部署)——" + path
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return "WARN", "链耗时台账不可读: %s" % str(e)[:80]
    written = str(data.get("target", ""))
    try:
        age_days = (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        age_days = 0
    if age_days > 4:
        return "WARN", "链耗时台账停滞 %.1f 天(target=%s)——链可能持续未跑完" % (age_days, written)
    phases = data.get("phases", {}) or {}
    aux = data.get("aux_steps", {}) or {}
    total = sum(float(v) for v in phases.values())
    issues, notes = [], []
    for ph, budget in CHAIN_TIMING_BUDGETS.items():
        v = float(phases.get(ph, 0) or 0)
        if v > budget:
            issues.append("%s=%.0fs>预算%.0fs" % (ph, v, budget))
    if total > CHAIN_TOTAL_ERR_S:
        return "ERROR", "整链 %.0fmin 超 60min(预算台账 target=%s): %s" % (
            total / 60, written, "; ".join(issues) or "各环节无单项超标")
    if issues:
        notes.append("环节超标: " + "; ".join(issues))
    top = sorted(aux.items(), key=lambda kv: -float(kv[1].get("seconds", 0) or 0))[:3]
    detail = "整链%.0fmin/%d环节 target=%s | aux top: %s" % (
        total / 60, len(phases), written,
        ", ".join("%s=%.0fs(rc%s)" % (k, float(v.get("seconds", 0) or 0), v.get("rc", 0))
                  for k, v in top))
    if notes:
        return "WARN", notes[0] + " | " + detail
    return "OK", detail


def check_chain_lock():
    """数据链单实例锁心跳（2026-09-19 接入，只读告警不动进程）: 堵 9/18 夜链挂死十五小时
    无人发现的盲区。锁=data/_daily_chain_update.lock(v2含pid|创建时刻, 环节循环touch保活)。
    判定: 锁在&pid活&锁龄>=90min → ERROR(挂死, 新起链将自动强杀接管或人工 taskkill);
          锁在&pid活&<90min → OK(在跑); 锁在&pid死 → WARN(陈旧锁, 下次链自动覆盖无害);
          无锁 → OK(链不在跑)。注: v1裸pid与v2"pid|时刻"双格式兼容取pid段(2026-10-08)。"""
    lock_f = os.path.join(r"D:\zcodesj\data", "_daily_chain_update.lock")
    if not os.path.exists(lock_f):
        return {"status": "OK", "note": "无锁(链不在跑)"}, []
    try:
        pid = int(open(lock_f, encoding="utf-8").read().strip().split("|", 1)[0] or 0)
        age_min = (time.time() - os.path.getmtime(lock_f)) / 60
    except (OSError, ValueError) as e:
        return {"status": "WARN", "note": "锁文件不可读: %s" % e}, []
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, 0, pid)
        alive = bool(h)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        alive = True  # 检测失败按活着处理(保守, 避免误报挂死)
    if not alive:
        return {"status": "WARN", "note": "陈旧锁(pid=%s 已死, 锁龄%.0fmin), 下次链自动覆盖" % (pid, age_min)}, \
               ["链陈旧锁残留(无害): pid=" + str(pid)]
    if age_min >= 90:
        return {"status": "ERROR", "note": "疑似挂死: pid=%s 活着但锁龄%.0fmin>=90min(环节心跳停摆)" % (pid, age_min)}, \
               ["daily_chain_update 疑似挂死(pid=" + str(pid) + "): 新起链自动强杀接管, 或人工 taskkill /F /PID " + str(pid)]
    return {"status": "OK", "note": "链在跑(pid=%s, 锁龄%.0fmin<90min)" % (pid, age_min)}, []


def check_kronos_fwalk():
    """Kronos 前 walk 探针（2026-09-05 对接接入，只读，WARN 级）：attribution/kronos_fwalk_predictions.jsonl
    月末零样本预测留痕。协议起始 2026-09-30（PREREG_KRONOS_FWALK_20260905）；
    研究队列零拦截权，断供不影响交易链 → 只 WARN 不 ERROR。
    自愈入口：月末交易日当晚 `python attribution/kronos_fwalk_20260905.py --predict --score`（幂等）。"""
    import json as _json
    from datetime import datetime as _dt
    f = os.path.join(r"D:\zcodesj", "attribution", "kronos_fwalk_predictions.jsonl")
    if not os.path.exists(f):
        return {"status": "OK", "note": "前walk未开始(协议起始2026-09-30)"}, []
    try:
        last = None
        n = 0
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = _json.loads(line)
                    n += 1
        if last is None:
            return {"status": "OK", "note": "前walk未开始(空文件)"}, []
        last_d = _dt.strptime(str(last["date"]), "%Y%m%d")
        months_late = (_dt.now().year - last_d.year) * 12 + (_dt.now().month - last_d.month)
        note = "已预测%d月, 最近%s" % (n, last["date"])
        if months_late >= 2:
            note += " (连续两月末未跑, 协议作废风险: python attribution/kronos_fwalk_20260905.py --predict --score)"
            return {"status": "WARN", "note": note}, ["Kronos前walk: " + note]
        return {"status": "OK", "note": note}, []
    except Exception as e:
        return {"status": "WARN", "note": "前walk解析失败: %s: %s" % (type(e).__name__, e)}, \
               ["Kronos前walk解析失败: %s: %s" % (type(e).__name__, e)]


def check_review_coverage():
    """复盘覆盖探针（2026-09-06 接入，只读 WARN 级；起因=金健米业 8/6 低位首板日无复盘
    产物、信号无人看——复盘是感知层第一镜头，缺当日产物=该日市场盲区）。
    检查对象=最近一个已完整过去的交易日（<today 的最大 vipdoc 交易日；今日复盘 22:45
    才跑，查 T-1 避免 22:00 巡检误报）。产物判定=reports/ 文件名含日期串且含 review
    （覆盖 DAILY_REVIEW_*/A_SHARE_DAILY_REVIEW_*/daily_review_*/DEEP_REVIEW_* 变体）。
    自愈入口：补跑当日复盘（a-share-daily-review skill）。"""
    import glob as _glob
    try:
        _, prev_td = vipdoc_trading_dates()
    except Exception as e:
        return {"status": "WARN", "note": "交易日历解析失败: %s" % type(e).__name__}, \
               ["复盘覆盖探针: 交易日历解析失败: %s" % type(e).__name__]
    if not prev_td:
        return {"status": "WARN", "note": "无有效前一交易日"}, ["复盘覆盖探针: vipdoc 无历史交易日"]
    hits = [os.path.basename(f) for f in _glob.glob(
        os.path.join(r"D:\zcodesj", "reports", "*%s*" % prev_td))
        if "review" in os.path.basename(f).lower() and f.lower().endswith(".md")]
    if hits:
        return {"status": "OK", "note": "T-1(%s)复盘产物 %d 份" % (prev_td, len(hits))}, []
    note = "T-1(%s)复盘产物缺失——该日市场无镜头(感知断层风险), 补跑当日复盘" % prev_td
    return {"status": "WARN", "note": note}, ["复盘覆盖: " + note]


def check_ledger_recon():
    """执行链双台账状态对账（2026-09-28 全维度自省 S2 接入）。

    背景：monitor 轮询确认 FILLED 历史上从不传播回 executor 提交台账
    （9/28 弘信电子 sz300657 实证；传播修复已入 fill_confirm_monitor S2 段），
    存量 57 条 diff 回填处置权=PD-017（主人冻结），故存量只呈报不告警；
    新增 diff（trade_date>20260928）=传播失效 → ERROR。
    同时子进程调用 ledger_status_recon.py 生成日报 reports/ledger_recon_*.md。
    返回 (stats, issue, is_error)。
    """
    import subprocess
    try:
        r = subprocess.run([sys.executable, r"D:\zcodesj\ledger_status_recon.py"],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip().splitlines()
        line = next((l for l in out if l.startswith("[RECON]")), "")
        kv = {}
        for tok in line.replace("[RECON]", "").strip().split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k] = v
        diff = int(kv.get("diff", -1))
        new_diff = int(kv.get("new_diff", -1))
        sell_open = int(kv.get("SELL_OPEN", -1))
        if diff < 0:
            return None, "双台账对账脚本输出无法解析: %s" % (line[:120] or (r.stderr or "")[:120]), True
        stats = {"diff": diff, "new_diff": new_diff, "sell_open": sell_open}
        if new_diff > 0:
            return stats, ("双台账新增失配 %d 条(trade_date>20260928)——monitor FILLED 传播回写失效，"
                           "查 fill_confirm_monitor S2 段" % new_diff), True
        # 存量(=57 基线)属 PD-017 已知冻结项，不升级告警，仅 freshness 行呈报
        return stats, None, False
    except Exception as e:
        return None, "双台账对账检查失败: %s" % str(e)[:100], True


def check_risk_prealert():
    """持仓前置风险预警（2026-09-29 自省 L3 接入）：解禁/业绩预告 T-5 预扫。

    信息型检查：命中即 WARN（高危解禁≥5%/预减类预告），零命中=OK；
    持仓快照陈旧(>2天)单独 WARN（预警分母失真）。数据源=event_calendar.db
    (ISO日期口径已在脚本内勘误)；质押/评级未接线由脚本如实标注。
    """
    import subprocess
    try:
        r = subprocess.run([sys.executable, r"D:\zcodesj\risk_prealert.py",
                            "--date", datetime.datetime.now().strftime("%Y%m%d")],
                           capture_output=True, text=True, timeout=180, cwd=r"D:\zcodesj")
        line = next((l for l in (r.stdout or "").splitlines()
                     if l.startswith("[RISK-PREALERT]")), "")
        if not line:
            return None, "风险预扫输出无法解析: %s" % (r.stderr or "")[:120], True
        kv = dict(t.split("=", 1) for t in
                  line.replace("[RISK-PREALERT]", "").split() if "=" in t)
        stats = {"持仓": kv.get("持仓", "?"), "解禁": kv.get("解禁", "?"),
                 "预告": kv.get("预告", "?"), "高危": kv.get("高危", "?")}
        if int(kv.get("高危", "0") or 0) > 0:
            return stats, "持仓前置风险高危 %s 笔(解禁≥5%%/预减类预告), 详见当日 risk_prealert 报告" % kv.get("高危"), True
        return stats, None, False
    except Exception as e:
        return None, "风险预扫检查失败: %s" % str(e)[:100], True


def main():
    only_json = "--json" in sys.argv
    now = datetime.datetime.now()
    issues_warn, issues_err = [], []

    max_date, expected = vipdoc_trading_dates()
    if expected is None:
        issues_err.append("vipdoc 无有效交易日数据(文件损坏?)")
    else:
        # 数据源 mtime 停滞检查
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(VIPDOC_DAY))
        days_stale = (now - mt).days
        if days_stale > 7:
            issues_err.append("vipdoc 数据文件 %d 天未更新(数据源停滞)" % days_stale)
        elif days_stale > 2:
            issues_warn.append("vipdoc 数据文件 %d 天未更新" % days_stale)

    # 表新鲜度
    freshness = []
    for db_key, table, col in STRICT_TABLES:
        val, err = table_max_date(db_key, table, col)
        if err:
            issues_err.append("%s.%s %s" % (db_key, table, err))
            freshness.append({"table": table, "status": "ERROR", "detail": err})
            continue
        nv = norm_date(val)
        if expected and nv < str(expected):
            issues_err.append("%s.%s 落后: max=%s 预期=%s" % (db_key, table, nv, expected))
            freshness.append({"table": table, "status": "BEHIND", "max": nv, "expected": str(expected)})
        else:
            freshness.append({"table": table, "status": "OK", "max": nv, "expected": str(expected)})

    # 分钟K 免费源新鲜度 (vipdoc/baostock 独立流水维护, 非数据链; 落后>2 交易日 WARN 防静默滞后)
    try:
        _mc = sqlite3.connect(DBS["dragon_quant_meta"], timeout=30)
        _mm = str(_mc.execute("SELECT MAX(date) FROM stock_minute_1").fetchone()[0] or "")
        if expected:
            _cal = [str(r[0]) for r in _mc.execute(
                "SELECT DISTINCT date FROM stock_daily_meta WHERE date<=? ORDER BY date", (str(expected),))]
        else:
            _cal = []
        _mc.close()
        _gap = 0
        if _mm and _mm in _cal:
            _gap = len(_cal) - 1 - _cal.index(_mm)
        if _mm and expected and _gap > 2:
            issues_warn.append("分钟K stock_minute_1 落后 %d 交易日 (max=%s 预期=%s), 免费分钟源未更新" % (_gap, _mm, expected))
            freshness.append({"table": "stock_minute_1", "status": "WARN", "max": _mm, "expected": str(expected)})
        else:
            freshness.append({"table": "stock_minute_1", "status": "OK", "max": _mm, "expected": str(expected) if expected else ""})
    except Exception as e:
        issues_warn.append("分钟K 新鲜度检查失败: %s" % str(e)[:100])

    # 链耗时预算告警（2026-10-14 根治令接入: 读链尾台账, 防 aux 类慢性膨胀无人察觉）
    ct_status, ct_msg = check_chain_timing()
    if ct_status == "ERROR":
        issues_err.append("链耗时预算: " + ct_msg)
    elif ct_status == "WARN":
        issues_warn.append("链耗时预算: " + ct_msg)
    freshness.append({"table": "chain_timing", "status": ct_status, "detail": ct_msg})

    # 磁盘
    free = disk_free_gb()
    if free is None:
        issues_warn.append("磁盘空间检测失败")
    elif free < DISK_ERR_GB:
        issues_err.append("D: 盘剩余 %.0fGB < 20GB 临界" % free)
    elif free < DISK_WARN_GB:
        issues_warn.append("D: 盘剩余 %.0fGB < 50GB" % free)

    # 归因回填监控（A1 S5，2026-08-17 接入）
    attr_stats, attr_issue, attr_err = check_attribution_backfill()
    if attr_issue:
        if attr_err:
            issues_err.append(attr_issue)
        # 窗口滚动中(overdue=0)是每日常态, 不升级黄灯——否则巡检永远WARN(狼来了);
        # 仅在freshness表内标注WARN供人工查看, 结论保持全绿, 真异常(overdue>0)才变色
    if attr_stats:
        freshness.append({"table": "attribution_backfill", "status": (
            "ERROR" if attr_err else ("OK" if not attr_issue else "WARN")),
            "detail": "fwd5 %d/%d (%.1f%%) overdue=%d%s" % (
                attr_stats["fwd5"], attr_stats["total"],
                attr_stats["fwd5_rate"] * 100, attr_stats["overdue"],
                attr_stats.get("degraded_note", "")),
            "fwd5_rate": attr_stats["fwd5_rate"], "total": attr_stats["total"],
            "fwd5": attr_stats["fwd5"], "overdue": attr_stats["overdue"]})

    # 归因闭环新鲜度（2026-09-08 O-N1/B3 接入: 8/27断链9天无人发现的复发防线）
    cl_stats, cl_issue, cl_err = check_attribution_closed_loop()
    if cl_issue:
        if cl_err:
            issues_err.append(cl_issue)
        else:
            issues_warn.append(cl_issue)
    if cl_stats:
        cl_detail = "closed=%(closed_total)s max_closed=%(max_closed)s max_sell=%(max_sell)s" % cl_stats
        if "lag_days" in cl_stats:
            cl_detail += " lag=%(lag_days)s" % cl_stats
        freshness.append({"table": "attribution_closed_loop", "status": (
            "ERROR" if cl_err else ("OK" if not cl_issue else "WARN")),
            "detail": cl_detail, "closed_total": cl_stats["closed_total"],
            "max_closed": cl_stats["max_closed"], "max_sell": cl_stats["max_sell"]})

    # 归因反馈闭环新鲜度（2026-10-06 三尾巴②接入: verification sync/update 断链防线）
    vf_stats, vf_issue, vf_err = check_verification_feedback()
    if vf_issue:
        if vf_err:
            issues_err.append(vf_issue)
        else:
            issues_warn.append(vf_issue)
    if vf_stats:
        vf_detail = "max_buy=%(max_buy)s max_verification=%(max_verification)s backlog=%(result_backlog)s" % vf_stats
        if "lag_days" in vf_stats:
            vf_detail += " lag=%(lag_days)s" % vf_stats
        freshness.append({"table": "verification_feedback", "status": (
            "ERROR" if vf_err else ("OK" if not vf_issue else "WARN")),
            "detail": vf_detail, "max_buy": vf_stats["max_buy"],
            "max_verification": vf_stats["max_verification"],
            "result_backlog": vf_stats["result_backlog"]})

    # 执行链双台账对账（2026-09-28 全维度自省 S2 接入: FILLED 传播回写失效防复发）
    lr_stats, lr_issue, lr_err = check_ledger_recon()
    if lr_issue:
        if lr_err:
            issues_err.append(lr_issue)
        else:
            issues_warn.append(lr_issue)
    if lr_stats:
        freshness.append({"table": "ledger_status_recon", "status": (
            "ERROR" if lr_err else ("OK" if not lr_issue else "WARN")),
            "detail": "存量diff=%(diff)s(PD-017冻结) 新增diff=%(new_diff)s SELL_OPEN=%(sell_open)s" % lr_stats,
            "diff": lr_stats["diff"], "new_diff": lr_stats["new_diff"],
            "sell_open": lr_stats["sell_open"]})

    # 持仓前置风险预警（2026-09-29 自省 L3 接入: 解禁/预告 T-5 预扫, 信息型）
    rp_stats, rp_issue, rp_err = check_risk_prealert()
    if rp_issue:
        if rp_err:
            issues_err.append(rp_issue)
        else:
            issues_warn.append(rp_issue)
    if rp_stats:
        freshness.append({"table": "risk_prealert", "status": (
            "ERROR" if rp_err else ("OK" if not rp_issue else "WARN")),
            "detail": "持仓%(持仓)s 解禁%(解禁)s 预告%(预告)s 高危%(高危)s" % rp_stats})

    # 低位首板蓄势感知层新鲜度（2026-09-09 接入, 红棉断链防复发: 台账停 9/4 无人发现
    # 3 日, 19 只二波漏登记; daily_chain aux [scout] 接线后盯"再断链"）
    sc_stats, sc_issue, sc_err = check_scout_track()
    if sc_issue:
        if sc_err:
            issues_err.append(sc_issue)
        else:
            issues_warn.append(sc_issue)
    if sc_stats:
        freshness.append({"table": "breakout_scout_track", "status": (
            "ERROR" if sc_err else ("OK" if not sc_issue else "WARN")),
            "detail": "entries=%(entries)s track_max=%(track_max)s qfq_max=%(qfq_max)s" % sc_stats,
            "track_max": sc_stats["track_max"], "qfq_max": sc_stats["qfq_max"]})

    # 涨停范式库新鲜度（2026-09-09 接入, 白名单主线标注供给防复发: 引擎口径曾靠手动跑
    # 9/8 断供 1 日, 复盘口径 9/4-9/7 曾断; aux [patterns] 接线后盯"再断供"）
    lp_stats, lp_issue, lp_err = check_limitup_patterns()
    if lp_issue:
        if lp_err:
            issues_err.append(lp_issue)
        else:
            issues_warn.append(lp_issue)
    if lp_stats:
        lp_detail = "patterns_max=%(patterns_max)s qfq_max=%(qfq_max)s" % lp_stats
        if "lag_days" in lp_stats:
            lp_detail += " lag=%(lag_days)s" % lp_stats
        freshness.append({"table": "limit_up_patterns", "status": (
            "ERROR" if lp_err else ("OK" if not lp_issue else "WARN")),
            "detail": lp_detail, "patterns_max": lp_stats["patterns_max"]})

    # 主线龙头观察池新鲜度（2026-09-09 接入, 供给闭环 D 防复发: update 从未接线,
    # 断链病与 breakout_scout 同源; aux [leader] 接线后盯"再断供"）
    lw_stats, lw_issue, lw_err = check_leader_watchlist()
    if lw_issue:
        if lw_err:
            issues_err.append(lw_issue)
        else:
            issues_warn.append(lw_issue)
    if lw_stats:
        freshness.append({"table": "leader_watchlist", "status": (
            "ERROR" if lw_err else ("OK" if not lw_issue else "WARN")),
            "detail": "active=%(active)s oldest=%(oldest_update)s qfq_max=%(qfq_max)s" % lw_stats,
            "lag_days": lw_stats.get("lag_days")})

    # 数据库备份监控（2026-08-23 备份强制化三层: schtasks 22:30 独立调度 + ensure_backup 自愈守卫 + 巡检显性告警）
    bk_status, bk_detail = check_db_backup()
    if bk_status == "WARN":
        issues_warn.append("数据库备份异常: %s" % bk_detail)
    freshness.append({"table": "db_backup", "status": bk_status, "detail": bk_detail})

    # 赛马月度结算接线探针(2026-10-31 缺口5): 首跑20261126前OK待命; 首跑后当月无报告→WARN
    hr_status, hr_detail = check_horse_race_settle()
    if hr_status == "WARN":
        issues_warn.append("赛马结算: %s" % hr_detail)
    freshness.append({"table": "horse_race_settle", "status": hr_status, "detail": hr_detail})

    # cron 健康监控（2026-08-24 接入：cron 失败不再静默）
    cron_status, cron_detail = check_cron_health()
    if cron_status == "ERROR":
        issues_err.append("cron 失败: %s" % cron_detail)
    elif cron_status == "WARN":
        issues_warn.append("cron: %s" % cron_detail)
    freshness.append({"table": "cron_health", "status": cron_status, "detail": cron_detail})

    # schtasks L1 备份任务存在性（2026-08-24 接入）
    sch_status, sch_detail = check_schtasks()
    if sch_status == "WARN":
        issues_warn.append("schtasks: %s" % sch_detail)
    freshness.append({"table": "schtasks_backup", "status": sch_status, "detail": sch_detail})

    # schtasks 裸python守卫（2026-09-07 接入：9/7 定时任务全哑火事故, 屠龙域ERROR/外域WARN）
    sbp_status, sbp_detail = check_schtasks_bare_python()
    if sbp_status == "ERROR":
        issues_err.append("schtasks 裸python: %s" % sbp_detail)
    elif sbp_status == "WARN":
        issues_warn.append("schtasks 裸python: %s" % sbp_detail)
    freshness.append({"table": "schtasks_bare_python", "status": sbp_status, "detail": sbp_detail})

    # cmd 裸调用守卫（2026-10-31 接入: batch_plan_run 假绿灯案, 多腿后续行不执行）
    cbcc_status, cbcc_detail = check_cmd_bare_call_chain()
    if cbcc_status == "ERROR":
        issues_err.append("cmd裸调用: %s" % cbcc_detail)
    elif cbcc_status == "WARN":
        issues_warn.append("cmd裸调用: %s" % cbcc_detail)
    freshness.append({"table": "cmd_bare_call_chain", "status": cbcc_status, "detail": cbcc_detail})

    # schtasks 引号守卫（2026-09-24 接入：Eastmoney 空格路径未引号案, 屠龙域ERROR/外域WARN）
    sqp_status, sqp_detail = check_schtasks_quoted_paths()
    if sqp_status == "ERROR":
        issues_err.append("schtasks 引号: %s" % sqp_detail)
    elif sqp_status == "WARN":
        issues_warn.append("schtasks 引号: %s" % sqp_detail)
    freshness.append({"table": "schtasks_quoted_paths", "status": sqp_status, "detail": sqp_detail})

    # schtasks 失败探针（2026-09-13 接入：自省轮调度静默失效面——电源条件强杀/幽灵任务类失败此前无人知）
    sfl_status, sfl_detail = check_schtasks_failures()
    if sfl_status == "ERROR":
        issues_err.append("schtasks 失败: %s" % sfl_detail)
    elif sfl_status == "WARN":
        issues_warn.append("schtasks 失败探针: %s" % sfl_detail)
    freshness.append({"table": "schtasks_failures", "status": sfl_status, "detail": sfl_detail})

    # 闸门冒烟
    g_pass, g_fail, g_total = run_gate_smoke()
    if g_fail > 0:
        issues_err.append("闸门冒烟 %d/%d FAIL" % (g_fail, g_total))
    elif g_total == 0:
        issues_warn.append("闸门冒烟无结果")

    # 闸门异常落库计数（O08 2026-09-03: 崩溃路径必须留痕, >0=当日有检查缺陷已fail-closed待修）
    # 2026-09-03 豁免: code=000000+manual_confirmed 的演练注入行(O08崩溃演练)不计;
    # 真实闸门异常(含真实特批REJECT尝试)仍逐笔计数, 豁免条件不可放宽
    try:
        _gcon = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db")
        _gn = _gcon.execute(
            "SELECT COUNT(*) FROM guard_precheck_log "
            "WHERE trade_date=? AND rejects LIKE '%闸门异常%' "
            "AND NOT (code='000000' AND source='manual_confirmed')",
            (datetime.datetime.now().strftime("%Y%m%d"),)).fetchone()[0]
        _gcon.close()
        _gstat, _gdet = (("OK", "当日 0 笔") if _gn == 0
                         else ("WARN", f"当日 {_gn} 笔(检查缺陷须修)"))
        if _gn > 0:
            issues_warn.append(f"闸门异常落库 {_gn} 笔: 检查缺陷已fail-closed, 须修复")
    except Exception as _ge:
        _gstat, _gdet = "WARN", f"计数查询失败: {type(_ge).__name__}"
        issues_warn.append("闸门异常计数查询失败")
    freshness.append({"table": "gate_exception", "status": _gstat, "detail": _gdet})

    # TickFlow
    tf_status, tf_err = check_tickflow()
    if tf_err:
        issues_warn.append("TickFlow 面板不可达: %s" % tf_err[:80])

    # 宪法状态（2026-08-23 接入：宪法执行机制化第2件）
    con_status, con_files = check_constitution()
    if con_status == "ERROR":
        issues_err.append("宪法文件缺失: %s" % ", ".join(con_files))

    # 违规台账每日心跳位（2026-10-06 三尾巴③: 零违规≠断写, 心跳让断写可检测）
    try:
        import violation_log as _vlog
        _hb = _vlog.write_heartbeat()
        _hb_lag = _vlog.heartbeat_lag_days()
        if _hb_lag is None:
            issues_warn.append("violation 心跳: 首日建立(今日%s)" % _hb.get("date", ""))
        elif _hb_lag >= 2:
            issues_err.append("violation 心跳断链: 最新心跳滞后%d日——violation_log写链断" % _hb_lag)
    except Exception as _hb_e:
        issues_warn.append("violation 心跳失败: %s" % str(_hb_e)[:80])

    # westock 唯一真值源探针（2026-08-27 O22b/E47 接入：断供即 ERROR, 触发准则28只读模式）
    ws_status, ws_detail = check_westock()
    if ws_status == "ERROR":
        issues_err.append("westock 真值源: %s" % ws_detail)
    elif ws_status == "WARN":
        issues_warn.append("westock: %s" % ws_detail)

    # 备源三通道活性探测（2026-10-10 接线：全死/口径偏离=ERROR 断供防线失效）
    bd_status, bd_detail = check_backup_daily_source()
    if bd_status == "ERROR":
        issues_err.append("备源日线: %s" % bd_detail)
    elif bd_status == "WARN":
        issues_warn.append("备源日线: %s" % bd_detail)
    freshness.append({"table": "backup_daily_source", "status": bd_status, "detail": bd_detail})

    # 备源命中率观测呈现（2026-10-20 接线：只读呈现层，能力探测在上面 bd 探针）
    bh_status, bh_detail = check_backup_source_hitrate()
    if bh_status == "WARN":
        issues_warn.append("备源命中率观测: %s" % bh_detail)
    freshness.append({"table": "backup_source_hitrate", "status": bh_status, "detail": bh_detail})

    # DirectionCensus 产物新鲜度探针（2026-10-14 接入：静默漏跑补盲，见函数 docstring）
    cs_status, cs_detail = check_census_freshness()
    if cs_status == "ERROR":
        issues_err.append("DirectionCensus 静默漏跑: %s" % cs_detail)
    elif cs_status == "WARN":
        issues_warn.append("DirectionCensus: %s" % cs_detail)
    freshness.append({"table": "direction_census", "status": cs_status, "detail": cs_detail})

    # 知识库状态（2026-08-24 接入）
    kb_status, kb_detail = check_knowledge_base()
    if kb_status == "WARN":
        issues_warn.append("知识库: %s" % kb_detail)

    # 准则33 夜写红线监督（2026-08-27 接入）
    nw_detail, nw_warns = check_night_write_watch(now)
    issues_warn.extend(nw_warns)

    # E70 qfq 写库窗口提示（2026-08-28 接入；提示性不改变结论级别）
    qw_detail, _qw_warns = check_qfq_write_window(now)

    # qfq amount 质量守卫（2026-09-06 接入；FAIL→ERROR 数据真相级，重建期 UNAVAILABLE→WARN）
    qa_detail, qa_warns = check_qfq_amount_guard()
    issues_warn.extend(qa_warns)

    # qfq close 完整性守卫（2026-09-08 接入；close 列盲区补位，FAIL→ERROR 数据真相级）
    qc_detail, qc_warns = check_qfq_close_guard()
    issues_warn.extend(qc_warns)

    # qfq 历史单元格修订审计守卫（2026-09-20 接入；S8/M5 漂移v3移交项，FAIL→ERROR 数据真相级）
    cr_detail, cr_warns = check_qfq_cell_revision_guard()
    if cr_detail.get("status") == "ERROR":
        issues_err.extend(cr_warns)
    else:
        issues_warn.extend(cr_warns)

    # py 编译完整性守卫（2026-09-08 接入；跟踪面语法损坏盲区补位，FAIL→ERROR 代码真相级）
    pc_detail, pc_warns = check_py_compile_guard()
    issues_warn.extend(pc_warns)

    # 步4产物探针（2026-08-28 接入；缺失→WARN，E62 夜链缺口早发现）
    s4_detail, s4_warns = check_step4_products(now)
    issues_warn.extend(s4_warns)

    # 画像快照探针（2026-09-05 对接接入；只读。缺失/超龄→WARN 不 ERROR：画像当前零拦截权，
    # 断供不影响交易链，但对接方（白名单标注/AB台账）需感知）
    pf_detail, pf_warns = check_portrait_feed()
    issues_warn.extend(pf_warns)

    # 画像零交易权 CI 门（2026-10-11 批1' 接入；违规 ERROR 级——画像防线破口=权限模型被破坏）
    pp_detail, pp_warns = check_portrait_permissions_gate()
    if pp_detail.get("status") == "ERROR":
        issues_err.extend(pp_warns)
    else:
        issues_warn.extend(pp_warns)

    # PIT 弃用隔离 CI 门（2026-10-18 DeepSeek审计V6 接入；违规 ERROR 级——前视/稀疏表=数据真相破口）
    pit_detail, pit_warns = check_pit_isolation_gate()
    if pit_detail.get("status") == "ERROR":
        issues_err.extend(pit_warns)
    else:
        issues_warn.extend(pit_warns)

    # Kronos 前 walk 探针（2026-09-05 对接接入；只读 WARN 级。研究队列零拦截权，
    # 连续两月末未跑→WARN 提示协议作废风险）
    kf_detail, kf_warns = check_kronos_fwalk()
    issues_warn.extend(kf_warns)

    # panel_v6 截面探针（2026-09-12 接入；只读 WARN 级。画像日链唯一新鲜度窗口——
    # 9/10-9/11 断跑两天巡检无感知的盲区堵补，引擎A/组合C影子消费方需感知）
    pv6_detail, pv6_warns = check_panel_v6()
    issues_warn.extend(pv6_warns)

    # 链单实例锁心跳（2026-09-19 接入；只读。9/18 夜链挂死十五小时无人发现的盲区堵补；
    # 挂死 ERROR 级=修复入口"新起链自动强杀接管"）
    cl_detail, cl_warns = check_chain_lock()
    if cl_detail.get("status") == "ERROR":
        issues_err.extend(cl_warns)
    else:
        issues_warn.extend(cl_warns)

    # 复盘覆盖探针（2026-09-06 接入；只读 WARN 级。复盘=感知层第一镜头，
    # T-1 产物缺失=该日市场盲区——金健米业 8/6 首板无人看的运营缺口兜底）
    rc_detail, rc_warns = check_review_coverage()
    issues_warn.extend(rc_warns)

    level = "ERROR" if issues_err else ("WARN" if issues_warn else "OK")
    result = {
        "timestamp": now.isoformat(),
        "level": level,
        "expected_trading_date": str(expected) if expected else None,
        "vipdoc_max_date": str(max_date) if max_date else None,
        "freshness": freshness,
        "gate_smoke": {"passed": g_pass, "failed": g_fail, "total": g_total},
        "tickflow": {"status": tf_status, "error": tf_err},
        "westock": {"status": ws_status, "detail": ws_detail},
        "disk_free_gb": free,
        "constitution": {"status": con_status, "files": con_files, "note": CONSTITUTION_NOTE},
        "knowledge_base": {"status": kb_status, "detail": kb_detail},
        "night_write_watch": nw_detail,
        "qfq_write_window": qw_detail,
        "qfq_amount_guard": qa_detail,
        "qfq_close_guard": qc_detail,
        "qfq_cell_revision_guard": cr_detail,
        "py_compile_guard": pc_detail,
        "step4_products": s4_detail,
        "portrait_feed": pf_detail,
        "portrait_permissions_gate": pp_detail,
        "pit_isolation_gate": pit_detail,
        "kronos_fwalk": kf_detail,
        "panel_v6": pv6_detail,
        "warnings": issues_warn,
        "errors": issues_err,
    }

    if only_json:
        print(json.dumps(result, ensure_ascii=False))
        return 2 if issues_err else (1 if issues_warn else 0)

    md = ["# 系统健康巡检 %s" % now.strftime("%Y-%m-%d %H:%M:%S"), "",
          "| 项目 | 状态 | 详情 |", "|---|---|---|"]
    md.append("| 预期交易日 | - | %s (vipdoc max=%s) |" % (expected, max_date))
    for f in freshness:
        md.append("| %s | %s | %s |" % (f["table"], f["status"], f.get("max", f.get("detail", ""))))
    md.append("| 闸门冒烟 | %s | PASS %d / FAIL %d / TOTAL %d |" % (
        "OK" if g_fail == 0 else "FAIL", g_pass, g_fail, g_total))
    md.append("| TickFlow | %s | %s |" % ("OK" if tf_status else "DOWN", tf_err or "3018"))
    md.append("| westock真值源 | %s | %s |" % (ws_status, ws_detail))
    md.append("| 宪法 | %s | %s |" % (con_status, "; ".join(con_files)))
    md.append("| 宪法边界 | 人工核对 | %s |" % CONSTITUTION_NOTE)
    md.append("| 知识库 | %s | %s |" % (kb_status, kb_detail))
    # 20261011批4: 检测失败(free=None)此前显示"剩余 0 GB"=固定值冒充实测, 改 N/A 如实呈现
    md.append("| 磁盘D: | %s | %s |" % (
        "OK" if free and free >= DISK_WARN_GB else "WARN",
        ("剩余 %.0f GB" % free) if free is not None else "N/A(检测失败)"))
    md.append("| qfq写库窗口 | %s | %s |" % (qw_detail["status"], qw_detail["note"]))
    md.append("| qfq amount守卫 | %s | %s |" % (qa_detail["status"], qa_detail.get("note", "")))
    md.append("| qfq close守卫 | %s | %s |" % (qc_detail["status"], qc_detail.get("note", "")))
    md.append("| qfq 单元格修订守卫 | %s | %s |" % (cr_detail["status"], cr_detail.get("note", "")))
    md.append("| 步4产物探针 | %s | %s |" % (s4_detail["status"], s4_detail.get("note", "")))
    md.append("| 画像快照 | %s | %s |" % (pf_detail["status"], pf_detail.get("note", "")))
    md.append("| panel_v6截面 | %s | %s |" % (pv6_detail["status"], pv6_detail.get("note", "")))
    md.append("| PIT弃用隔离门 | %s | %s |" % (pit_detail["status"], pit_detail.get("note", "")))
    md.append("| 链锁心跳 | %s | %s |" % (cl_detail["status"], cl_detail.get("note", "")))
    for w in issues_warn:
        md.append("| WARN | ⚠️ | %s |" % w)
    for e in issues_err:
        md.append("| ERROR | ❌ | %s |" % e)
    md.append("")
    md.append("**结论: %s**" % level)
    body = "\n".join(md)
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "health_check_%s.md" % now.strftime("%Y%m%d")), "w", encoding="utf-8") as f:
        f.write(body)
    with open(os.path.join(REPORTS, "health_check_%s.json" % now.strftime("%Y%m%d")), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(body)

    if issues_err:
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                            "--title", "屠龙系统健康巡检 ERROR",
                            "--content", "; ".join(issues_err)[:400],
                            "--level", "error"], timeout=60)
        except Exception as e:
            # 20261011批4: 告警通道自身断链此前完全静默——notifications.log 补一条留痕,
            # 防"ERROR 已通知"假象; 退出码仍为 2 不变。
            try:
                with open(os.path.join(BASE, "data", "notifications.log"), "a",
                          encoding="utf-8") as _nf:
                    _nf.write("%s [health_check] notify send 失败: %s\n" % (
                        now.strftime("%Y-%m-%d %H:%M:%S"), str(e)[:150]))
            except Exception:
                pass
            print("[health_check] ⚠️ notify 通道异常: %s" % str(e)[:120])
        return 2
    return 1 if issues_warn else 0


if __name__ == "__main__":
    sys.exit(main())
