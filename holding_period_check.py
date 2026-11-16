# -*- coding: utf-8 -*-
"""
holding_period_check.py — 屠龙系统·R5 持仓周期分层检查执行器（2026-08-11 知微落地）

【铁证基础】
  W14: T+1 持仓周期最优（名义 +0.968%，盈亏比 0.51），之后单调递减
  W15: 高波(>=50%) 毛利 -0.016% 根本性失效（R5c 禁入已入 execution_guard.py）
       甜区(15-25%) 毛利 +2.124%；临界滑点 1.01% >> 实际 0.63% 单边 → 市价单安全

【R5 分层规则（本执行器覆盖卖出侧 R5a/R5b）】
  R5a 甜区: T+1 到点 -> eod 卖出建议 —— 已停用（主人 2026-09-07 裁决
            "对持有个股没有持有时间上的限制"，advisor 单点生效）
  R5b 中区: 跌破 5 日线强制卖出 —— 已停用（主人 2026-09-03 裁决"破5日线
            不卖出"）；持有 >T+3 eod 卖出建议 —— 已停用（同 9/7 裁决）
  R5c 高波: 禁入（execution_guard 拦截），持仓若现高波交 R2 止损管理
  档位 invalid（数据不足）: fail-closed，不触发，仅告警
  → 本执行器现存卖出触发器为零，--execute 仅扫描汇报（卖出来源=R2 止损
    stop_loss_executor 与主人裁量指令）

【C7-R28 长线持有期守卫（2026-08-26 修复）】
  R5 卖出执行前按 holding_horizon.query_position（与 pre_sell_check 同源）判定：
  long（bp_ep/bp_ep_core/full_market）且持有<10天 → R5a_t1/R5b 卖出禁用并显示 hold，
  仅 -8% 止损或月度调仓换股可卖（止损走 stop_loss_executor 不受影响）。
  背景: 本执行器 2026-08-11 编写早于 C7-R28，R5 卖出直连 ws.sell 绕过 pre_sell_check，
  8/26 大豪科技 8/25 买入次日被 R5 卖出实成交=C7-R28 违规；守卫查询失败 fail-open。

【数据源（铁律：实时 MCP，不读本地缓存）】
  持仓: westock MCP portfolio_paper_positions
  行情: westock MCP data_kline（45 根日线 -> F_AMP40 + MA5）
  买入日期: ①westock 持仓原始字段 ②westock MCP portfolio_paper_history
            （direction=buy & 已成交，取每只最近一次，2026-08-11 实测可查全量）
            ③tulong_system.db fills 表 ④均无 -> 天数规则(R5a_t1/R5b_overhold)禁用，
              hold —— 2026-09-03 主人裁决"破5日线不卖出"，R5b_ma5 分支已停用，
              破5日线降为持有观察（holding_period_advisor.advise_exit 单点生效）

【使用方式】
  python holding_period_check.py             # dry-run 扫描输出建议
  python holding_period_check.py --execute   # 执行卖出（immediate + eod）
  python holding_period_check.py --selftest  # 自测

【挂载点】
  每日环路 14:30 持仓检查（R2 止损之后、主观破位之前）
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 屠龙系统共用
_WS = Path(r"D:\workbuddyspace")
sys.path.insert(0, str(_WS))
sys.path.insert(0, r"D:\zcodesj")
from holding_period_advisor import HoldingPeriodAdvisor  # noqa: E402

logger = logging.getLogger('holding_period_check')

# 审计日志路径
AUDIT_DIR = _WS / "logs"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG = AUDIT_DIR / "holding_period_check.log"

# 本地 fills 表（仅 holding_days 回退用，非持仓真相源）
FILLS_DB = _WS / "tulong_system" / "tulong_system.db"


def load_positions_from_westock():
    """从腾讯自选股模拟盘实时读取持仓（铁律：不读本地缓存）。

    Returns:
        list[dict]: 每项含 code/name/entry_price/shares/current_price/raw
        失败返回 None。
    """
    try:
        from westock_client import WeStockClient
        ws = WeStockClient()
        raw = ws.positions()
        items = []
        # 兼容三种结构：{'data': {'positions': [...]}}, {'data': [...]}, {'positions': [...]}, [...]
        if isinstance(raw, list):
            data = raw
        elif isinstance(raw, dict):
            inner = raw.get('data', raw)
            if isinstance(inner, dict):
                data = inner.get('positions', inner.get('holdings', []))
            elif isinstance(inner, list):
                data = inner
            else:
                data = []
        else:
            data = []
        if not isinstance(data, list):
            logger.warning(f"westock positions 返回结构异常: {str(raw)[:200]}")
            return None
        for r in data:
            code = str(r.get('code', r.get('sCode', '')))
            name = str(r.get('name', r.get('sName', code)))
            entry = float(r.get('costPrice', r.get('cost_price', r.get('avgCost', 0))) or 0)
            shares = int(float(r.get('quantity', r.get('volume', r.get('nZqsl', 0))) or 0))
            cur_price = float(r.get('currentPrice', r.get('current_price', 0)) or 0)
            if code and shares > 0:
                items.append({
                    'code': code,
                    'name': name,
                    'entry_price': entry,
                    'shares': shares,
                    'current_price': cur_price,
                    'raw': r,  # 保留原始字段（含可能的买入日期字段）
                })
        return items
    except Exception as e:
        logger.error(f"读取 westock 持仓失败: {e}")
        return None


def _pick_buy_date(raw: dict):
    """从 westock 持仓原始字段探测买入日期（字段名不确定，多候选探测）。

    Returns:
        str or None: 'YYYY-MM-DD' 或 None
    """
    for k in ('posDate', 'buyDate', 'costDate', 'positionDate', 'initDate',
              'beginDate', 'openDate', 'openingDate'):
        v = raw.get(k)
        if v:
            s = str(v).strip()
            # 兼容 '2026-08-10' / '20260810' / '2026/08/10'
            if len(s) >= 8:
                s2 = s.replace('/', '-')
                if len(s2) == 8 and s2.isdigit():
                    return f"{s2[:4]}-{s2[4:6]}-{s2[6:]}"
                if '-' in s2 and s2[:4].isdigit():
                    return s2[:10]
    return None


def load_buy_dates_from_history(ws):
    """从模拟盘成交历史读每只股票最近一次买入日期（MCP 实时，2026-08-11 实测）。

    持仓买入日期的真相源：portfolio_paper_history（range=recent）
    过滤 direction=='buy' 且 status=='已成交'，每只 code 取 filledAt 最近的日期。

    Args:
        ws: WeStockClient 实例

    Returns:
        dict: {code: 'YYYY-MM-DD'}
    """
    result = {}
    try:
        r = ws.call('portfolio_paper_history', {'range': 'recent', 'limit': 500})
        recs = (r.get('data') or {}).get('records') or []
        for x in recs:
            if x.get('direction') != 'buy' or x.get('status') != '已成交':
                continue
            code = str(x.get('code', ''))
            filled = str(x.get('filledAt', ''))
            if not code or len(filled) < 10:
                continue
            d = filled[:10]  # '2026-08-10 09:35:36' -> '2026-08-10'
            # 取最近一次（filledAt 字符串排序即时间序）
            if code not in result or d > result[code]:
                result[code] = d
    except Exception as e:
        logger.warning(f"读取成交历史买入日期失败: {e}")
    return result


def load_buy_dates_from_fills():
    """从 tulong_system.db fills 表读每只股票最近买入日期（holding_days 回退源）。

    Returns:
        dict: {code: 'YYYY-MM-DD'}
    """
    result = {}
    if not FILLS_DB.exists():
        return result
    try:
        con = sqlite3.connect(str(FILLS_DB))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT code, MAX(date) AS d FROM fills WHERE action='buy' GROUP BY code"
        ).fetchall()
        con.close()
        for r in rows:
            d = str(r['d'])
            # 兼容 '20260810' 与 '2026-08-10'
            if len(d) == 8 and d.isdigit():
                d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            result[str(r['code'])] = d
    except Exception as e:
        logger.warning(f"读取 fills 买入日期失败: {e}")
    return result


def get_kline(ws, code: str, limit: int = 45):
    """取日线 K 线（实时 MCP data_kline）。

    Args:
        ws: WeStockClient 实例
        code: 代码（如 sz300534）
        limit: 根数，需 >=41（40 日窗口 + 窗口前收盘）

    Returns:
        dict or None: {'highs': [...], 'lows': [...], 'closes': [...], 'nodes': [...]}
        nodes 每项 {date, high, low, last, ...}（注意收盘字段名是 last）
    """
    try:
        r = ws.call('data_kline', {'code': code, 'limit': limit, 'period': 'day'})
        if not (isinstance(r, dict) and r.get('ok')):
            logger.warning(f"{code} data_kline 调用未成功: {str(r)[:120]}")
            return None
        data = r.get('data')
        nodes = data.get('nodes') if isinstance(data, dict) else None
        if not isinstance(nodes, list) or len(nodes) < 41:
            logger.warning(f"{code} data_kline 数据不足: {len(nodes) if isinstance(nodes, list) else '无'} 根")
            return None
        # 实证（2026-08-11）：data_kline 返回倒序（最新在前），统一转升序
        nodes = sorted(nodes, key=lambda n: str(n.get('date', '')))
        highs = [float(n['high']) for n in nodes]
        lows = [float(n['low']) for n in nodes]
        closes = [float(n['last']) for n in nodes]  # 实证：收盘字段名为 last
        if any(v <= 0 for v in highs + lows + closes):
            logger.warning(f"{code} K 线含非正值，数据异常")
            return None
        return {'highs': highs, 'lows': lows, 'closes': closes, 'nodes': nodes}
    except Exception as e:
        logger.warning(f"{code} K 线获取异常: {e}")
        return None


def holding_days_from_kline(nodes, buy_date: str):
    """持仓交易日数 = 买入日之后的日线根数（T+1 = 买入次日起 1 个交易日）。

    买入当日（T+0）不算持有：当天买入当天检查 -> 0 天（合法值，非未知）。
    返回 None 仅当买入日完全不在 K 线覆盖范围内（无法判定）。

    Args:
        nodes: 日线列表（每项含 date 'YYYY-MM-DD'）
        buy_date: 'YYYY-MM-DD'

    Returns:
        int（可为 0）or None
    """
    if not nodes or not buy_date:
        return None
    dates = [n['date'] for n in nodes]
    if buy_date > dates[-1]:
        return None  # 买入日晚于 K 线最新日期（未来买入，异常）
    if buy_date < dates[0]:
        # 买入日早于窗口起点：持仓至少覆盖整个窗口，返回窗口根数（下限值）
        return len(dates)
    cnt = sum(1 for d in dates if d > buy_date)
    return cnt


def ma5_from_closes(closes: list):
    """最近 5 根收盘均值。"""
    if len(closes) < 5:
        return None
    return sum(closes[-5:]) / 5.0


def _r5_c7_r28_blocked(code: str, name: str) -> tuple:
    """C7-R28 长线持有期守卫（2026-08-26 修复，与 pre_sell_check 同源 holding_horizon）。

    背景: 本执行器（2026-08-11 编写，早于 C7-R28）R5 卖出直连 ws.sell、不经
    pre_sell_check，导致长线票(bp_ep/full_market)持有<10天也被 R5a_t1/R5b 提前卖
    （8/26 大豪科技 8/25 买入次日被 R5 卖出实成交 = C7-R28 违规案例）。
    此处补与 pre_sell_check C7-R28 完全一致的守卫: long 且持有<10天 → 跳过 R5
    趋势/时间卖出（仅 -8% 止损或月度调仓换股可卖；止损路径在 stop_loss_executor，
    不受影响）。查询失败 fail-open（不阻断），与 pre_sell_check 行为一致。
    """
    try:
        from holding_horizon import HORIZON_MIN_HOLD_DAYS, query_position
        pos = query_position(code)
        if not pos.get("found"):
            return False, None
        horizon = pos.get("horizon")
        hold_days = pos.get("hold_days")
        min_days = HORIZON_MIN_HOLD_DAYS.get("long", 10)
        if horizon == "long" and hold_days is not None and hold_days < min_days:
            return True, (f"C7-R28: {name} 长线票(月度调仓·回测均持142天)持有仅{hold_days}天"
                          f"<{min_days}天最低持有期 → R5 趋势卖出禁用，仅-8%止损或月度调仓换股可卖")
    except Exception:
        pass  # fail-open
    return False, None


def scan_and_check(execute: bool = False,
                   dry_run_positions: list = None,
                   dry_run_kline: dict = None,
                   dry_run_fills: dict = None):
    """扫描持仓并按 R5a/R5b 分层规则给出退出建议。

    Args:
        execute: True=实际执行卖出（immediate + eod）；False=只输出建议
        dry_run_positions: 测试用持仓列表（跳过 westock）
        dry_run_kline: 测试用 K 线 {code: get_kline() 返回结构}（跳过 MCP）
        dry_run_fills: 测试用买入日期 {code: 'YYYY-MM-DD'}（跳过 DB）

    Returns:
        dict: {'scanned', 'immediate', 'eod', 'monitor', 'executed',
               'advices': [dict...], 'errors': [...]}
    """
    result = {'scanned': 0, 'immediate': 0, 'eod': 0, 'monitor': 0,
              'executed': 0, 'advices': [], 'errors': []}
    ws = None
    advisor = HoldingPeriodAdvisor()

    # 1. 加载持仓（铁律：实时 MCP）
    if dry_run_positions is not None:
        positions = dry_run_positions
    else:
        positions = load_positions_from_westock()
        if positions is None:
            result['errors'].append('读取 westock 持仓失败，无法扫描')
            return result

    result['scanned'] = len(positions)
    if not positions:
        logger.info("当前无持仓，R5 持仓周期检查跳过")
        return result

    logger.info(f"R5 持仓周期检查: {len(positions)} 只持仓")

    # 2. 买入日期（①history MCP ②fills 表回退）
    if dry_run_fills is not None:
        # 测试模式：dry_run_fills 直接作为最终买入日期表
        buy_dates = dry_run_fills
        hist_src = 'fills_db'
    else:
        buy_dates = {}
        hist_src = 'unknown'
        try:
            from westock_client import WeStockClient
            if ws is None:
                ws = WeStockClient()
            buy_dates = load_buy_dates_from_history(ws)
            if buy_dates:
                hist_src = 'paper_history'
        except Exception as e:
            logger.warning(f"读取成交历史失败: {e}")
        if not buy_dates:
            buy_dates = load_buy_dates_from_fills()
            hist_src = 'fills_db' if buy_dates else 'unknown'

    # 3. 逐持仓扫描
    for p in positions:
        code = p['code']
        name = p['name']
        entry_price = p.get('entry_price') or 0
        current_price = p.get('current_price') or 0

        # 3.1 K 线（实时 MCP）
        kline = None
        if dry_run_kline is not None:
            kline = dry_run_kline.get(code)
        else:
            try:
                from westock_client import WeStockClient
                if ws is None:
                    ws = WeStockClient()
                kline = get_kline(ws, code)
            except Exception as e:
                result['errors'].append(f"{code} {name} K 线获取失败: {e}")
                continue

        if kline is None:
            result['errors'].append(f"{code} {name} K 线数据不足，跳过（R5 不适用）")
            continue

        highs, lows, closes, nodes = (kline['highs'], kline['lows'],
                                      kline['closes'], kline['nodes'])

        # 3.2 F_AMP40（40 日窗口 + 窗口前收盘）
        pre_close = closes[-41]
        amp = advisor.calc_amp40(highs[-40:], lows[-40:], pre_close)
        band = advisor.classify(amp if amp is not None else float('nan'))

        # 3.3 持仓天数（①westock 字段 ②history ③fills）
        buy_date = _pick_buy_date(p.get('raw') or {})
        hd_src = 'westock_field'
        if not buy_date:
            norm_code = code.split('.')[0].lower()
            buy_date = buy_dates.get(code) or buy_dates.get(norm_code)
            hd_src = hist_src if buy_date else 'unknown'
        holding_days = holding_days_from_kline(nodes, buy_date) if buy_date else None

        # 3.4 MA5
        ma5 = ma5_from_closes(closes)

        # 3.5 退出建议
        advice = advisor.advise_exit(band, holding_days or 0, current_price or closes[-1],
                                     ma5=ma5, entry_price=entry_price or closes[-1],
                                     code=code, name=name)

        # fail-safe: 天数未知时禁用天数相关规则（R5a_t1/R5b_overhold）
        if holding_days is None and advice.rule in ('R5a_t1', 'R5b_overhold'):
            advice = advisor.advise_exit(band, 0, current_price or closes[-1],
                                         ma5=ma5, entry_price=entry_price or closes[-1],
                                         code=code, name=name)
            # 若仍触发天数规则（不应发生），降级为 hold
            if advice.rule in ('R5a_t1', 'R5b_overhold'):
                advice = ExitAdvicePlaceholder(
                    should_exit=False, rule='hold',
                    reason=f"R5 持有: {code} {name} 持仓天数未知（{hd_src}），"
                           f"天数规则禁用，仅破 5 日线规则生效",
                    urgency='monitor')

        # C7-R28 长线持有期守卫（2026-08-26 修复）：长线票(bp_ep/full_market)持有<10天
        # 禁 R5 趋势/时间卖出（仅-8%止损或月度调仓可卖）。R5 卖出原直连 ws.sell 绕过
        # pre_sell_check，8/26 大豪科技 8/25买入次日被 R5 卖出实成交=C7-R28 违规。
        c7_blocked, c7_reason = _r5_c7_r28_blocked(code, name)
        if c7_blocked:
            advice = ExitAdvicePlaceholder(
                should_exit=False, rule='hold',
                reason=c7_reason,
                urgency='monitor')

        adv = {
            'code': code, 'name': name, 'band': band.band,
            'amplitude_pct': round(amp * 100, 2) if amp is not None else None,
            'holding_days': holding_days, 'hd_src': hd_src,
            'ma5': round(ma5, 3) if ma5 else None,
            'current_price': round(current_price, 3) if current_price else round(closes[-1], 3),
            'rule': advice.rule, 'urgency': advice.urgency, 'reason': advice.reason,
            'c7_r28_blocked': c7_blocked,
        }
        result['advices'].append(adv)

        if advice.urgency == 'immediate':
            result['immediate'] += 1
        elif advice.urgency == 'eod':
            result['eod'] += 1
        else:
            result['monitor'] += 1

        # 4. 输出/执行
        icon = {'immediate': '🔴', 'eod': '🟠', 'monitor': '🟢'}.get(advice.urgency, '⚪')
        print(f"  {icon} {code} {name} | 档位={band.band} 振幅={adv['amplitude_pct']}% "
              f"持仓{holding_days}天({hd_src}) | {advice.rule}")
        print(f"      {advice.reason}")

    # 5. 执行卖出（仅 immediate + eod，monitor 不卖）
    if execute:
        targets = [a for a in result['advices'] if a['urgency'] in ('immediate', 'eod')]
        if not targets:
            print("\n[执行] 无 immediate/eod 目标，无需卖出")
            return result

        print(f"\n[执行] {len(targets)} 只进入卖出流程（trade_executor 唯一入口）...")
        for a in targets:
            shares = sum(p['shares'] for p in positions if p['code'] == a['code'])
            if shares <= 0:
                result['errors'].append(f"{a['code']} 持仓数为 0，跳过卖出")
                continue
            # C7-R28 长线持有期守卫（2026-08-26 修复）：R5 卖出直连 ws.sell 曾绕过
            # pre_sell_check，长线票(bp_ep/full_market)持有<10天被 R5a_t1/R5b 提前卖
            # （8/26 大豪科技 8/25买入次日被 R5 卖出实成交=C7-R28 违规）。命中则跳过
            # 卖出，仅 -8% 止损/月度调仓换股可卖（止损走 stop_loss_executor 不受影响）。
            blocked, _reason = _r5_c7_r28_blocked(a['code'], a['name'])
            if blocked:
                a['c7_r28_blocked'] = True
                logger.info(f"[C7-R28 保护] {a['code']} {a['name']} 跳过 R5 卖出: {_reason}")
                print(f"  🛡️ {a['code']} {a['name']} R5卖出被C7-R28拦截（{_reason}）")
                continue
            price = a['current_price']
            # 2026-08-28 P0 物理收口（8/28 14:41 绕台账卖出根因根治，E64 同型）：原分支
            # ws.sell 直连绕过 trade_executor（无 pre_sell/无 O19 台账/无 trade_records/
            # 无 C6 减半）。改prompt无效——漏洞在代码层。现改为子进程调 trade_executor
            # sell，防线全内置：pre_sell fail-closed + HOLD拦截 + C7-R28自动补参 +
            # C6板块减半 + O19台账 + trade_records 落库（notes 含规则名，K2验收标准）。
            import subprocess as _sp
            cmd = [sys.executable,
                   os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "trade_executor.py"),
                   "sell", "--code", a['code'], "--name", a['name'] or "",
                   "--reason", f"{a['rule']} 持有周期退出(holding_period_check)",
                   "--price", f"{float(price):.3f}",
                   "--execute", "--confirmed"]
            try:
                proc = _sp.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", timeout=120, shell=False)
                try:
                    r = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    result['errors'].append(
                        f"{a['code']} trade_executor 输出不可解析: "
                        f"{proc.stdout[:120]} | stderr={proc.stderr[:120]}")
                    logger.error(f"[SELL 异常] {a['code']} executor输出不可解析")
                    continue
            except Exception as e:
                result['errors'].append(f"{a['code']} trade_executor 调用失败: {e}")
                logger.error(f"[SELL 异常] {a['code']} 调用失败: {e}")
                continue
            if r.get('executed'):
                logger.info(f"[SELL 成交] {a['code']} {a['name']} @ {price} | "
                            f"规则={a['rule']} | via trade_executor")
                print(f"  ✅ {a['code']} {a['name']} 卖出成交 @ {price}（{a['rule']}，via trade_executor）")
                result['executed'] += 1
            elif r.get('fill_status') == 'PENDING_CONFIRM':
                # 挂单未即时成交：O19 未决台账已登记，回报由 fill_confirm_monitor 回填
                logger.info(f"[SELL 挂单待回报] {a['code']} {a['name']} @ {price} "
                            f"order={r.get('order_id')}")
                print(f"  ⏳ {a['code']} {a['name']} 挂单待回报（已登记台账 "
                      f"order={r.get('order_id')}，{a['rule']}）")
                result['executed'] += 1
            else:
                # 防卖飞HOLD拦截/C7-R28/无持仓等 executor 侧终止——如实透出
                _why = r.get('reason') or r.get('reason_note') or '未知原因'
                print(f"  🛡️ {a['code']} {a['name']} 被 executor 防线终止: {_why}")
                logger.info(f"[SELL 拦截] {a['code']} {a['name']}: {_why}")
                a['executor_blocked_reason'] = str(_why)[:200]

    # 6. 审计日志
    audit_entry = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'action': 'holding_period_check',
        'execute': execute,
        'scanned': result['scanned'],
        'immediate': result['immediate'],
        'eod': result['eod'],
        'executed': result['executed'],
        'advices': [
            {'code': a['code'], 'name': a['name'], 'band': a['band'],
             'amp_pct': a['amplitude_pct'], 'hd': a['holding_days'],
             'rule': a['rule'], 'urgency': a['urgency']}
            for a in result['advices']
        ],
    }
    try:
        with open(AUDIT_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(audit_entry, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.error(f"审计日志写入失败: {e}")

    return result


class ExitAdvicePlaceholder:
    """天数未知时的 hold 占位（保持与 ExitAdvice 同接口）。"""
    def __init__(self, should_exit, rule, reason, urgency):
        self.should_exit = should_exit
        self.rule = rule
        self.reason = reason
        self.urgency = urgency
        self.detail = {}


# ===== 自测 =====
def _make_kline(pre_close=10.0, amp=0.20, ma5_target=10.0, last_price=None,
                dates=None, n=45):
    """构造测试 K 线：40 日窗口振幅精确 = amp，最近 5 根收盘均值 = ma5_target。

    highs/lows 制造 (max_high - min_low) / close[-41] == amp
    默认日期：45 个连续自然日，最后一天 2026-08-11（与测试买入日同区间）
    """
    import random
    from datetime import datetime, timedelta
    random.seed(42)
    nodes = []
    # 窗口内 high/low：差值 = amp * pre_close，加微小噪声
    spread = amp * pre_close
    base_h = pre_close + spread * 0.55
    base_l = pre_close - spread * 0.45
    # 构造 closes：前 n-5 根围绕 pre_close，后 5 根均值 = ma5_target
    closes = [pre_close + random.uniform(-0.02, 0.02) for _ in range(n - 5)]
    tail = [ma5_target * 5.0 / 5.0 for _ in range(5)]
    # 微调让尾部均值为 ma5_target 且末根 = last_price
    if last_price is not None:
        tail = [ma5_target + (last_price - ma5_target) * 0.25 for _ in range(4)] + [last_price]
    closes = closes + tail
    if dates is None:
        start = datetime(2026, 8, 11) - timedelta(days=n - 1)
        dates = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n)]
    for i in range(n):
        c = closes[i]
        nodes.append({
            'date': dates[i],
            'open': round(c, 2), 'high': round(base_h, 2),
            'low': round(base_l, 2), 'last': round(c, 2),
            'volume': 10000, 'amount': 1000000,
        })
    return {'highs': [n['high'] for n in nodes],
            'lows': [n['low'] for n in nodes],
            'closes': [n['last'] for n in nodes],
            'nodes': nodes}


def _selftest():
    """R5 持仓周期执行器自测（dry_run 隔离，不连 westock/DB）。"""
    print("=" * 68)
    print(" holding_period_check 自测（R5a/R5b 分层退出规则执行器）")
    print("=" * 68)

    # ---- case 1: 甜区 T+1 -> hold（2026-09-07 主人裁决：持仓无持有时间限制）----
    print("\n--- case 1: 甜区(20%) 持仓1天 -> hold（裁决后到期不卖出） ---")
    kline = _make_kline(pre_close=10.0, amp=0.20, ma5_target=10.2, last_price=10.1)
    pos = [{'code': 'sz999999', 'name': '测试股份', 'entry_price': 10.0,
            'shares': 1000, 'current_price': 10.1, 'raw': {'posDate': '2026-08-10'}}]
    r = scan_and_check(execute=False, dry_run_positions=pos,
                       dry_run_kline={'sz999999': kline}, dry_run_fills={})
    assert r['scanned'] == 1, f"scanned={r['scanned']}"
    a = r['advices'][0]
    assert a['band'] == 'sweet', f"应甜区: {a['band']}"
    assert abs(a['amplitude_pct'] - 20.0) < 1.0, f"振幅应≈20%: {a['amplitude_pct']}"
    assert a['holding_days'] == 1, f"持仓1天: {a['holding_days']}"
    assert a['rule'] == 'hold' and a['urgency'] == 'monitor', f"裁决后到期应 hold: {a['rule']}/{a['urgency']}"
    assert r['eod'] == 0
    print(f"  ✅ 甜区 T+1 -> hold（主人9/7裁决：到期不卖出），振幅 {a['amplitude_pct']}%")

    # ---- case 2: 甜区 T+0 -> hold monitor ----
    print("\n--- case 2: 甜区 持仓0天(当天买入) -> hold monitor ---")
    pos2 = [{'code': 'sz999999', 'name': '测试股份', 'entry_price': 10.0,
             'shares': 1000, 'current_price': 10.1, 'raw': {}}]
    fills2 = {'sz999999': '2026-08-11'}  # 今天买入 -> 0 天
    r2 = scan_and_check(execute=False, dry_run_positions=pos2,
                        dry_run_kline={'sz999999': kline}, dry_run_fills=fills2)
    a2 = r2['advices'][0]
    assert a2['rule'] == 'hold' and a2['urgency'] == 'monitor', f"应 hold: {a2['rule']}"
    print(f"  ✅ 甜区 T+0 -> hold（{a2['reason'][:60]}...）")

    # ---- case 3: 中区 破5日线 -> hold（2026-09-03 主人裁决：破5日线不卖出）----
    print("\n--- case 3: 中区(35%) 跌破5日线 -> hold（裁决后不构成卖出） ---")
    kline3 = _make_kline(pre_close=10.0, amp=0.35, ma5_target=10.0, last_price=9.2)
    pos3 = [{'code': 'sz000002', 'name': '万科A', 'entry_price': 10.0,
             'shares': 2000, 'current_price': 9.2, 'raw': {}}]
    r3 = scan_and_check(execute=False, dry_run_positions=pos3,
                        dry_run_kline={'sz000002': kline3}, dry_run_fills={})
    a3 = r3['advices'][0]
    assert a3['band'] == 'mid', f"应中区: {a3['band']}"
    assert a3['rule'] == 'hold' and a3['urgency'] == 'monitor', f"裁决后破5日线应 hold: {a3['rule']}"
    assert a3['holding_days'] is None, "天数未知应 None（fills 无记录）"
    print(f"  ✅ 中区破5日线 -> hold（主人 9/3 裁决：破5日线不卖出，天数未知无额外触发）")

    # ---- case 4: 中区 T+3 未破线 -> hold（2026-09-07 主人裁决：超持不卖出）----
    print("\n--- case 4: 中区 持仓4天 未破5日线 -> hold（裁决后超持不构成卖出） ---")
    kline4 = _make_kline(pre_close=10.0, amp=0.35, ma5_target=10.0, last_price=10.05)
    pos4 = [{'code': 'sz000002', 'name': '万科A', 'entry_price': 10.0,
             'shares': 2000, 'current_price': 10.05, 'raw': {}}]
    fills4 = {'sz000002': '2026-08-05'}  # 4 个交易日以上
    r4 = scan_and_check(execute=False, dry_run_positions=pos4,
                        dry_run_kline={'sz000002': kline4}, dry_run_fills=fills4)
    a4 = r4['advices'][0]
    assert a4['rule'] == 'hold' and a4['urgency'] == 'monitor', f"裁决后超持应 hold: {a4['rule']}"
    print(f"  ✅ 中区 T+4 -> hold（主人9/7裁决：超持不卖出）")

    # ---- case 5: 中区 T+1 未破线 -> hold monitor ----
    print("\n--- case 5: 中区 持仓1天 未破5日线 -> hold monitor ---")
    fills5 = {'sz000002': '2026-08-10'}
    r5 = scan_and_check(execute=False, dry_run_positions=pos4,
                        dry_run_kline={'sz000002': kline4}, dry_run_fills=fills5)
    a5 = r5['advices'][0]
    assert a5['rule'] == 'hold', f"应 hold: {a5['rule']}"
    print(f"  ✅ 中区 T+1 未破线 -> hold")

    # ---- case 6: 高波 -> R5 不适用（交 R2） ----
    print("\n--- case 6: 高波(60%) -> hold（交 R2 止损管理）---")
    kline6 = _make_kline(pre_close=10.0, amp=0.60, ma5_target=10.0, last_price=10.0)
    pos6 = [{'code': 'sz000003', 'name': '金种子酒', 'entry_price': 10.0,
             'shares': 1000, 'current_price': 10.0, 'raw': {}}]
    r6 = scan_and_check(execute=False, dry_run_positions=pos6,
                        dry_run_kline={'sz000003': kline6}, dry_run_fills={})
    a6 = r6['advices'][0]
    assert a6['band'] == 'high', f"应高波: {a6['band']}"
    assert a6['rule'] == 'hold', f"高波应交 R2: {a6['rule']}"
    print(f"  ✅ 高波 -> hold（R5 不越权，交 R2 止损）")

    # ---- case 7: 无持仓 ----
    print("\n--- case 7: 无持仓 ---")
    r7 = scan_and_check(execute=False, dry_run_positions=[], dry_run_kline={}, dry_run_fills={})
    assert r7['scanned'] == 0, f"scanned={r7['scanned']}"
    print(f"  ✅ 无持仓正常退出")

    # ---- case 8: 审计日志 ----
    print("\n--- case 8: 审计日志 ---")
    assert AUDIT_LOG.exists(), f"审计日志应存在: {AUDIT_LOG}"
    content = AUDIT_LOG.read_text(encoding='utf-8')
    assert 'holding_period_check' in content, "审计日志应含动作标记"
    print(f"  ✅ 审计日志已写入: {AUDIT_LOG}")

    print("\n" + "=" * 68)
    print(" holding_period_check 自测全绿 ✓")
    print(" 时间型退出（R5a_t1/R5b_ma5/R5b_overhold）已按主人裁决全部停用")
    print(" （9/3 破5日线 + 9/7 持有时间）；本执行器降为只读扫描汇报")
    print("=" * 68)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    ap = argparse.ArgumentParser(description='R5 持仓周期分层检查执行器')
    ap.add_argument('--execute', action='store_true',
                    help='实际执行卖出（immediate + eod，默认 dry-run）')
    ap.add_argument('--selftest', action='store_true', help='运行自测')
    args = ap.parse_args()

    if args.selftest:
        _selftest()
    else:
        r = scan_and_check(execute=args.execute)
        print(f"\n[结果] 扫描 {r['scanned']} 只 | 立即卖 {r['immediate']} | "
              f"尾盘卖 {r['eod']} | 持有 {r['monitor']} | "
              f"已执行 {r['executed']} | 错误 {len(r['errors'])} 条")
        if r['errors']:
            for e in r['errors']:
                print(f"  ⚠️ {e}")
