#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卖出前五检查校验器 v1.1（防卖飞低估值涨停股+复牌顶部预警）。

背景：好想你(002582) 三次卖飞涨停，PE 2.66/股息 5.14%/业绩扭亏全部被忽略。
本脚本在卖出决策前强制执行五项检查，任一命中即触发"建议保留"警告。

五检查（任一命中即 WARN）:
  C1 低估值: PE_TTM < 10 或 PB < 1.5
  C2 业绩拐点: 近期业绩预告扭亏/净利润同比>0/公允价值变动大幅改善
  C3 高股息: 股息率(TTM) > 3%
  C4 主力流入: 近5日主力资金净流入 > 0
  C5 趋势验证: 价格 vs MA5/MA20 排列（YTD禁用铁律）
  C5 复牌顶部: 复牌股连续3板+换手≥5%=顶部区域，建议主动止盈

R6 已验证风险特征（2026-08-18全市场验证,提示性,不改变verdict,--no-auto-risk可关）:
  炸板(次日均-0.45%/63%低开) / 断头铡刀(-0.39%) / 长上影阳线(28.5%跌≥3%)
  / 高位放量下跌(T+5跌占比63.2%中期信号) — 自动从qfq库计算最近交易日

C8 高位退潮提示（2026-08-23复盘线索落地,提示性,不改变verdict）:
  8/21 实证: 13家跌停全为高位/断板股; 当前市场连板高度3板封顶+高位退潮烈度大。
  连板≥3 或 20日涨幅≥30% → 追加卖出支持信号(risk_hints), 供主人权衡兑现。

C7-R28 持有期分层守卫（2026-08-24 升级,holding_horizon.py 唯一真源）:
  long(bp_ep系) 持有<10天 → 禁趋势卖出, 仅-8%止损或月度调仓换股可卖;
  short 持有<5天 → 短持卖出需额外确认。--hold-days/--horizon 未传时自动查 buy_tracker。

用法:
    python pre_sell_check.py --code 002582 --name 好想你 --pe 2.66 --pb 1.06 \
        --dividend-yield 5.14 --earnings-yoy 150 --net-inflow-5d 5000000

退出码: 0=可卖 / 1=建议保留(需主人确认)
"""
from __future__ import annotations

import argparse
import json
import sys

# ===== 四检查阈值 =====
LOW_PE_THRESHOLD = 10.0       # C1: PE<10 视为低估值
LOW_PB_THRESHOLD = 1.5        # C1: PB<1.5 视为低估值
HIGH_DIVIDEND_THRESHOLD = 3.0  # C3: 股息率>3%
EARNINGS_TURNAROUND_MIN = 0    # C2: 净利润同比>0 视为业绩正向


def check(args) -> dict:
    code = args.code
    name = args.name or ""
    holds = []  # 命中的保留理由
    # 2026-09-10 M1: S-2 时间窗提示专用列表——8/14 主人裁决废除尾盘锁(全天可交易)后,
    # 时间窗提示仍是"提示性非约束"语义, 但原实现 insert 进 holds → verdict=HOLD
    # 硬拦截(9/4 科森 -8.1% 止损被延迟到 14:45 后成交实证); 改为并入 risk_hints
    # (权衡提示侧, 不阻断), 与宪法"全天可交易/-8%触发即执行"对齐
    time_hints = []
    info = {}

    # ---- loss_pct 单位防御（8/16全量巡检：-3被解释为-300%误导紧急通道）----
    # 契约为小数(-0.095=-9.5%)；|值|>1 视为误传百分数，自动归一并显式警告
    # 2026-08-24 P2修复: 仅负值方向归一——正的小数盈利率(如1.5=+150%)是合法值,
    # 旧 abs()>1 会把真实+150%错归一为+1.5%; 误传百分数的危险方向只在负值(触发止损通道)
    if args.loss_pct is not None and args.loss_pct < -1:
        print(f"[warn] --loss-pct={args.loss_pct} 超出小数域，按百分数自动归一为 "
              f"{args.loss_pct / 100}（契约: -0.095 表示亏9.5%）", file=sys.stderr)
        args.loss_pct = args.loss_pct / 100

    # ---- S-2 卖出时间窗口（提示性，非约束）----
    from trade_time_guard import check_sell_window as _check_sell_window
    _tw = _check_sell_window(
        loss_pct=args.loss_pct,
        owner_confirmed_emergency=args.owner_emergency,
    )
    if _tw["verdict"] == "HARD_REJECT":
        # 15:00后收盘，不应出现
        pass
    elif _tw["verdict"] == "SOFT_REJECT":
        # 2026-09-10 M1: 提示性入 time_hints(并入 risk_hints), 不再 HOLD 硬拦
        if _tw.get("is_stop_loss_emergency"):
            time_hints.append(f"止损提醒(非拦截): {_tw['reason']}")
        else:
            time_hints.append(f"时段提示(非拦截): {_tw['reason']}")
    # PASS → 无操作

    # C1 低估值检查
    pe = args.pe
    pb = args.pb
    if pe is not None and pe > 0 and pe < LOW_PE_THRESHOLD:
        holds.append(f"C1低估值: PE={pe:.1f} < {LOW_PE_THRESHOLD} → 极度低估，建议保留")
        info["pe"] = pe
    if pb is not None and pb > 0 and pb < LOW_PB_THRESHOLD:
        holds.append(f"C1低估值: PB={pb:.2f} < {LOW_PB_THRESHOLD} → 破净/低PB，建议保留")
        info["pb"] = pb

    # C2 业绩拐点检查
    earnings_yoy = args.earnings_yoy
    if earnings_yoy is not None and earnings_yoy > EARNINGS_TURNAROUND_MIN:
        holds.append(
            f"C2业绩拐点: 净利润同比{earnings_yoy:+.0f}% > 0 → 业绩正向，建议保留"
        )
        info["earnings_yoy"] = earnings_yoy
    if args.earnings_turnaround:
        holds.append("C2业绩拐点: 近期业绩预告扭亏 → 基本面拐点，建议保留")

    # C3 高股息检查
    div_yield = args.dividend_yield
    if div_yield is not None and div_yield > HIGH_DIVIDEND_THRESHOLD:
        holds.append(
            f"C3高股息: 股息率{div_yield:.1f}% > {HIGH_DIVIDEND_THRESHOLD}% → 高股息防御，建议保留"
        )
        info["dividend_yield"] = div_yield

    # C4 主力流入检查
    net_inflow = args.net_inflow_5d
    if net_inflow is not None and net_inflow > 0:
        holds.append(
            f"C4主力流入: 近5日主力净流入{net_inflow/10000:.0f}万元 > 0 → 资金看好，建议保留"
        )
        info["net_inflow_5d"] = net_inflow

    # C5 复牌一字板顶部预警（蓝盾光电范式：复牌连板→天量开板=顶部区域）
    if args.halt_resume:
        resume_boards = args.post_halt_boards or 1
        resume_turnover = args.post_halt_turnover or 0
        if resume_boards >= 3 and resume_turnover >= 0.05:
            holds.append(
                f"C5复牌顶部预警⚠️: 复牌后{resume_boards}连板 + 今日换手{resume_turnover*100:.1f}% ≥ 5%"
                f" → 复牌一字板天量换手=顶部区域信号，建议主动止盈锁定利润"
            )
            info["halt_resume"] = {"boards": resume_boards, "turnover": resume_turnover}
        elif resume_boards >= 3:
            warns = holds  # Reuse holds list for warning-level signals
            holds.append(
                f"C5复牌连板⚠️: 复牌后{resume_boards}连板(换手{resume_turnover*100:.1f}%)"
                f" → 仍在缩量连板阶段，开板日即顶部，建议开板前主动止盈"
            )
            info["halt_resume"] = {"boards": resume_boards, "turnover": resume_turnover}

    # C5 趋势验证（铁律: YTD不能作为趋势判定依据，2026-08-14广哈通信卖飞闭环）
    trend_verdict = None
    if args.trend_ma5 and args.trend_ma20 and args.price:
        ma5, ma20 = args.trend_ma5, args.trend_ma20
        if args.price > ma5 > ma20:
            holds.append(f"C5趋势验证: 价格{args.price} > MA5({ma5}) > MA20({ma20}) → 趋势完好，建议保留")
            trend_verdict = "HOLD"
        elif args.price < ma20:
            # 偏弱只记录提示，不产生HOLD（避免"可卖"误成拦截）
            info["trend_note"] = f"C5趋势验证: 价格{args.price} < MA20({ma20}) → 20日线下方，趋势偏弱可卖"
        else:
            info["trend_note"] = f"C5趋势验证: 价格{args.price} vs MA5({ma5})/MA20({ma20}) → 中性，谨慎"
    elif args.ytd is not None and args.price:
        # 若只提供YTD → 提示不能作为依据
        info["trend_note"] = f"⚠️ YTD={args.ytd}% 不能作为趋势判定依据（铁律），请提供MA5/MA20后再判定"

    # 特殊规则: 好想你条款——PE<5 且 股息>4% 同时命中=强制保留
    pe_extreme = pe is not None and 0 < pe < 5
    div_extreme = div_yield is not None and div_yield > 4.0
    if pe_extreme and div_extreme:
        holds.insert(0,
            f"⛔好想你条款: PE={pe:.1f}(<5) + 股息率={div_yield:.1f}%(>4%) 双命中"
            f" → 极端低估+高股息，强制建议保留（三次卖飞涨停教训）"
        )

    # ---- C6 板块强势白名单（2026-08-23 S-10: 恐慌篮子防误伤）----
    # 8/19宝泰隆案: 开盘恐慌篮子误伤涨停股(卖在2.73,当日涨停3.04,+11.4%)
    # 板块当日涨幅>3%时提示"板块强势，谨慎卖出"
    if args.sector_pct is not None and args.sector_pct > 3.0:
        holds.append(
            f"C6板块强势⚠️: 所属板块当日涨幅{args.sector_pct:+.1f}% > 3% → "
            f"板块强势，恐慌篮子不应误伤，建议保留（8/19宝泰隆卖飞涨停教训）"
        )
        info["sector_pct"] = args.sector_pct

    # ---- C7 持有期观察（2026-09-07 主人裁决"对持有个股没有持有时间上的限制"）----
    # 原为 C7-R28 持有期分层守卫（2026-08-24：长线票最低持有期禁趋势卖出），时间型
    # 拦截已按裁决停用。实证链（8/13-24 三周 53 笔平仓胜率 11.3%）保留在档，持有
    # 天数/档位降为观察信息（不进 holds，不影响 verdict），卖出交 R2 止损(-8%)与
    # 主人裁量。hold_days/horizon 未传时自动查 buy_tracker(失败 fail-open 跳过)。
    is_stop_loss = args.loss_pct is not None and args.loss_pct <= -0.08
    hold_days = getattr(args, "hold_days", None)
    horizon = getattr(args, "horizon", None)
    if (hold_days is None or horizon is None) and not is_stop_loss:
        try:
            from holding_horizon import query_position
            _pos = query_position(code)
            if _pos["found"]:
                if hold_days is None:
                    hold_days = _pos["hold_days"]
                if horizon is None:
                    horizon = _pos["horizon"]
                info["position_source"] = _pos["source"]
        except Exception as e:
            info["c7_autoload_error"] = f"{type(e).__name__}: {e}"
    if not is_stop_loss and hold_days is not None and horizon is not None:
        info["hold_days"] = hold_days
        info["horizon"] = horizon
        if horizon == "long":
            info["hold_period_note"] = (
                f"长线票持有 {hold_days} 天（原 C7-R28 守卫参考期 10 天）——"
                f"持有天数不构成卖出限制（主人 2026-09-07 裁决：持仓无持有时间限制）"
            )
        elif hold_days < 5:
            info["hold_period_note"] = (
                f"持有 {hold_days} 天（短持观察）——持有天数不构成卖出限制"
                f"（主人 2026-09-07 裁决：持仓无持有时间限制）"
            )

    # ---- R6 已验证风险特征提示（2026-08-18全市场验证，提示性参考，不改变verdict）----
    # 方向与C1-C5相反：C系=保留理由（防卖飞），R6=支持卖出的风险信号（上行失败类）
    # holds与risk_hints同时命中时展示两侧信息供主人权衡；--no-auto-risk 可关闭
    risk_hints = []
    # 2026-09-10 M1: S-2 时间窗提示并入 risk_hints(权衡侧, 非 HOLD 拦截)
    risk_hints.extend(time_hints)
    risk_flags = None
    if not getattr(args, "no_auto_risk", False):
        try:
            from verified_risk_flags import compute_risk_flags, flags_to_hints
            risk_flags = compute_risk_flags(code)
            risk_hints = flags_to_hints(risk_flags)
        except Exception as e:
            risk_flags = {"error": f"R6计算失败(不影响主流程): {type(e).__name__} {e}"}

    # ---- C8 高位退潮提示（2026-08-23复盘线索：8/21 13家跌停全为高位/断板股）----
    # 方向=R6类（卖出支持信号），提示性不改变verdict；连板/20日涨幅由调用方传入
    boards = getattr(args, "boards", None)
    chg_20d = getattr(args, "chg_20d", None)
    if boards is not None and boards >= 3:
        risk_hints.append(
            f"C8高位退潮: 已连板{boards}板 → 当前市场连板高度3板封顶、高位退潮烈度大，"
            f"建议兑现锁盈（8/21 13家跌停全为高位/断板股）"
        )
        info["boards"] = boards
    if chg_20d is not None and chg_20d >= 30:
        risk_hints.append(
            f"C8高位退潮: 20日涨幅{chg_20d:+.0f}% ≥ 30% → 短期涨幅过大，高位兑现风险高，建议兑现"
        )
        info["chg_20d"] = chg_20d

    # ---- C9 系统风险日止损冷静期（2026-08-23 R29物化）----
    # 8/19系统大跌日恐慌止损通鼎互联(+16.5%)/宝泰隆(最大+22.3%)踏空教训：
    # 大盘单日<-3%系统风险日 + 持有<3天 + 未触-8%硬止损 → 提示T+1冷静期，勿恐慌止损。
    # 与C7(持有<5天)互补：C7覆盖普遍短持，C9聚焦系统风险日的特殊恐慌场景。
    market_pct = getattr(args, "market_pct", None)
    if market_pct is not None and market_pct <= -3.0 and not is_stop_loss:
        # 2026-08-24 P1修复: 此处重新 getattr(args) 会丢弃 C7 段 buy_tracker 自动补参
        # 写入的局部 hold_days（变量遮蔽）→ 未显式传参时 C9 恒不触发。直接复用 C7 段
        # 已补全的局部值（C7 在 178-191 行已完成 args 初值+自动补参）。
        if hold_days is not None and hold_days < 3:
            holds.append(
                f"C9系统风险日冷静期⚠️: 大盘{market_pct:+.1f}%系统风险日 + 持有仅{hold_days}天 + "
                f"未触-8%硬止损 → 恐慌止损易踏空反弹（8/19通鼎卖后+16.5%/宝泰隆最大+22.3%教训），"
                f"建议T+1冷静期观察"
            )
            info["system_risk_day"] = {"market_pct": market_pct, "hold_days": hold_days}

    # 2026-08-29 P0-E(胜率计划): 主人显式授权卖出 override 通道——additive, 默认不传=行为零变化。
    # 仅当调用方显式 --owner-override-hold 时豁免"建议性保留"(C1-C9 的 holds)为 SELL_OK;
    # 触发权在主人(如信号源漂移退出), 豁免依据与原 holds 全量回传由调用方落台账, 防卖飞对常规卖出不变。
    # 不豁免: 尾盘窗口 HARD_REJECT/数据异常 fail-closed(不在 holds 语义内, 不受本通道影响)。
    # ---- S-5 硬止损放行（2026-09-05 主人裁决A案: 守卫向宪法对齐）----
    # 宪法§10铁律2「-8%止损(S-5)触发即执行不等待」优先于一切建议性HOLD。
    # 仅当调用方显式声明 --stop-loss 且亏损实际达线(loss_pct≤-8%)时生效——
    # 普通趋势卖/调仓卖不受影响, 尾盘窗口对非止损卖单保留现状;
    # holds 全量转 overridden_holds 供审计(与 --owner-override-hold 同模式)。
    s5_pass = getattr(args, "stop_loss", False) and is_stop_loss
    if s5_pass:
        verdict = "SELL_OK"
        info["s5_stoploss_pass"] = True
        info["overridden_holds"] = holds
        holds_overridden = holds
        holds = []
    elif holds and getattr(args, "owner_override_hold", False):
        verdict = "SELL_OK"
        info["owner_override_hold"] = True
        info["overridden_holds"] = holds
        holds_overridden = holds
        holds = []
    else:
        verdict = "HOLD" if holds else "SELL_OK"
        holds_overridden = []
    return {
        "code": code,
        "name": name,
        "verdict": verdict,
        "hold_reasons": holds,
        "overridden_holds": holds_overridden,
        "risk_hints": risk_hints,
        "risk_flags": risk_flags,
        "info": info,
        "time_window": _tw,
        "checks_run": {
            "pe": pe, "pb": pb, "earnings_yoy": earnings_yoy,
            "dividend_yield": div_yield, "net_inflow_5d": net_inflow,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="卖出前四检查（防卖飞低估值涨停股，好想你三次卖飞教训）"
    )
    p.add_argument("--code", required=True, help="股票代码")
    p.add_argument("--name", default="", help="股票名称")
    p.add_argument("--owner-override-hold", action="store_true",
                   help="P0-E(2026-08-29): 主人显式授权卖出, 豁免建议性保留(C1-C9 holds)→SELL_OK; "
                        "默认关=行为零变化; 豁免依据经 overridden_holds 回传由调用方落台账")
    p.add_argument("--pe", type=float, help="PE_TTM（C1低估值）")
    p.add_argument("--pb", type=float, help="PB（C1低估值）")
    p.add_argument("--dividend-yield", type=float, help="股息率TTM %%（C3高股息）")
    p.add_argument("--earnings-yoy", type=float, help="净利润同比 %%（C2业绩拐点）")
    p.add_argument("--earnings-turnaround", action="store_true", help="业绩预告扭亏标记（C2）")
    p.add_argument("--net-inflow-5d", type=float, help="近5日主力资金净流入额（C4）")
    p.add_argument("--loss-pct", type=float, help="当前亏损百分比(负值)如-0.095")
    p.add_argument("--trend-ma5", type=float, help="MA5均线值（C5趋势验证）")
    p.add_argument("--trend-ma20", type=float, help="MA20均线值（C5趋势验证）")
    p.add_argument("--price", type=float, help="当前价格（C5趋势验证用）")
    p.add_argument("--ytd", type=float, help="YTD涨跌幅%% — 仅参考，不作为趋势判定依据")
    p.add_argument("--owner-emergency", action="store_true", help="主人已确认紧急止损卖出（按卖出三铁律，卖多少必须问主人）")
    p.add_argument("--stop-loss", action="store_true",
                   help="S-5硬止损执行声明(A案2026-09-05): 调用方(执行端is_stop_loss)声明本单为-8%硬止损; "
                        "配合loss_pct≤-8%时按宪法铁律2'触发即执行不等待'放行SELL_OK(holds转overridden_holds审计); "
                        "未达线或未声明=行为零变化")
    p.add_argument("--halt-resume", action="store_true", help="C5: 是否复牌股（曾停牌后复牌）")
    p.add_argument("--post-halt-boards", type=int, default=None, help="C5: 复牌后连续涨停板数")
    p.add_argument("--post-halt-turnover", type=float, default=None, help="C5: 复牌后今日换手率 0~1")
    p.add_argument("--no-auto-risk", action="store_true", help="关闭R6自动风险特征计算(qfq炸板/断头铡刀/长上影/高位放量下跌)")
    p.add_argument("--sector-pct", type=float, help="所属板块当日涨跌幅%%（C6板块强势白名单）")
    p.add_argument("--hold-days", type=int, help="已持有天数（C7持有期守卫；未传时自动查buy_tracker）")
    p.add_argument("--horizon", choices=["long", "short"], help="持有周期标签（C7-R28分层；未传时自动查buy_tracker）")
    p.add_argument("--boards", type=int, help="当前连板数（C8高位退潮提示，≥3触发）")
    p.add_argument("--chg-20d", type=float, help="20日涨跌幅%%（C8高位退潮提示，≥30触发）")
    p.add_argument("--market-pct", type=float, help="大盘当日涨跌幅%%（C9系统风险日冷静期，≤-3触发）")
    p.add_argument("--no-log", action="store_true",
                   help="跳过 guard_precheck_log 落库（仅测试用，防污染对账数据）")
    a = p.parse_args()

    r = check(a)
    _log_presell(a, r)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 1 if r["verdict"] == "HOLD" else 0


def _log_presell(args, result: dict) -> None:
    """卖出前判定无条件落库 guard_precheck_log（--no-log 跳过）。

    2026-08-28 P1a（8/28 14:41 绕台账卖出根因配套）：此前 pre_sell_check 不落库，
    卖出前是否跑过防卖飞检查无法事后审计（K2/R5 收口验收依赖"判定可观测"）。
    与买入侧 pre_trade_check_v2.log_precheck 同表同构；source 列固定 'pre_sell'
    供 naked_trade_audit 区分买卖两侧判定。
    """
    if getattr(args, "no_log", False):
        return
    try:
        from datetime import datetime as _dt
        import sqlite3 as _sq
        _c = str(result.get("code") or getattr(args, "code", "") or "")
        for _p in ("sh", "sz", "bj"):
            if _c.lower().startswith(_p) and len(_c) > 2 and _c[2:3].isdigit():
                _c = _c[2:]
                break
        _c = _c.zfill(6)
        # 2026-09-05 审计链补全: 豁免类SELL_OK（S-5放行/--owner-override-hold）的
        # overridden_holds 此前不落库（rejects=空holds）——事后对账看不到守卫当时
        # 说了什么, 违背 A 案"放行留痕"本意。落库时以标记前缀并入 rejects。
        _rej = list(result.get("hold_reasons", []))
        _ov = result.get("overridden_holds") or []
        if _ov:
            _tag = ("[S-5放行豁免]" if (result.get("info") or {}).get("s5_stoploss_pass")
                    else "[owner豁免]")
            _rej = [f"{_tag}{h}" for h in _ov] + _rej
        con = _sq.connect(r"D:\zcodesj\attribution\attribution.db")
        con.execute(
            "INSERT INTO guard_precheck_log "
            "(ts, trade_date, code, name, price, source, verdict, rejects, warnings) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_dt.now().isoformat(timespec="seconds"),
             _dt.now().strftime("%Y%m%d"),
             _c, result.get("name") or None,
             getattr(args, "price", None), "pre_sell",
             result.get("verdict", ""),
             json.dumps(_rej, ensure_ascii=False),
             json.dumps(result.get("risk_hints", []), ensure_ascii=False)),
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[WARN] guard_precheck_log 落库失败(不阻塞判定): {type(e).__name__} {e}",
              file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
