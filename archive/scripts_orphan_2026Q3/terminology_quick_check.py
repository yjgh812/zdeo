#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语量化快速核查工具 v1.0
2026-08-15 | 基于《屠龙系统量化术语标准手册》

在分析中快速调用：python terminology_quick_check.py <场景> <参数>

用法示例：
  python terminology_quick_check.py regime --adx 28.5 --prem-seal 2.3 --limits 46
  python terminology_quick_check.py trend --ma5 18.5 --ma10 17.2 --ma20 16.1 --ma60 15.3
  python terminology_quick_check.py volume --vol-ratio 2.1 --vol-ma20-ratio 1.8 --position 85
  python terminology_quick_check.py chase --pct-60d 0.82 --is-limit-price no --regime peak_end
  python terminology_quick_check.py breakout --close 12.8 --resistance 12.5 --vol-ratio 2.2
"""

import argparse, sys, json

# ===== 1. 情绪周期判定 =====
def check_regime(adx, prem_seal, limit_count, top_board, zha_ban_rate):
    """SC-001: 情绪周期阶段判定"""
    results = []
    
    if prem_seal is not None and prem_seal < 0:
        phase = "退潮/亏钱效应"
        verdict = "❌ 空仓/极小仓位"
        evidence = f"prem_seal={prem_seal:.1f}% < 0 → 亏钱效应"
    elif prem_seal is not None and prem_seal > 2:
        phase = "发酵/主升"
        verdict = "✅ 可满仓做主线"
        evidence = f"prem_seal={prem_seal:.1f}% > +2% → 赚钱效应强"
    elif limit_count is not None and limit_count < 20:
        phase = "冰点/启动"
        verdict = "⚠️ 低吸试仓"
        evidence = f"涨停={limit_count} < 20 → 冰点"
    elif limit_count is not None and limit_count > 80:
        phase = "高潮/分歧"
        verdict = "⚠️ 逐步减仓"
        evidence = f"涨停={limit_count} > 80 → 高潮"
    else:
        phase = "温和/过渡"
        verdict = "🟡 正常操作"
        evidence = "各指标处于中间区间"
    
    # ADX regime
    if adx is not None:
        if adx > 25:
            regime_str = "趋势市"
        elif adx < 20:
            regime_str = "震荡市"
        else:
            regime_str = "过渡区"
        results.append(f"[T-002] ADX={adx:.1f} → {regime_str}")
    
    results.append(f"[SC-001] {evidence} → {phase}（{verdict}）")
    return results


# ===== 2. 趋势判定 =====
def check_trend(ma5, ma10, ma20, ma60, adx):
    """T-001/T-002: 均线排列+regime综合判定"""
    results = []
    
    # 均线排列
    if all(x is not None for x in [ma5, ma10, ma20, ma60]):
        if ma5 > ma10 > ma20 > ma60:
            results.append("[T-001] 严格多头排列 MA5>MA10>MA20>MA60 ✅")
        elif ma5 < ma10 < ma20 < ma60:
            results.append("[T-001] 严格空头排列 MA5<MA10<MA20<MA60 ❌")
        elif ma5 > ma20:
            results.append("[T-001] 弱多头排列（短>长，中间有交叉）🟡")
        elif ma5 < ma20:
            results.append("[T-001] 弱空头排列（短<长，中间有交叉）🟡")
    
    # ADX趋势强度
    if adx is not None:
        if adx > 50:
            results.append(f"[T-002] ADX={adx:.1f} > 50 → 极强趋势（少见）⚠️")
        elif adx > 25:
            results.append(f"[T-002] ADX={adx:.1f} > 25 → 趋势市 ✅")
        elif adx > 20:
            results.append(f"[T-002] ADX={adx:.1f} (20-25) → 过渡区 🟡")
        else:
            results.append(f"[T-002] ADX={adx:.1f} < 20 → 震荡市（禁用趋势跟随策略）⚠️")
    
    return results


# ===== 3. 量价判定 =====
def check_volume(vol_ratio, vol_ma20_ratio, position_pct):
    """VP-001→VP-005: 量价状态判定"""
    results = []
    
    # 放量/缩量
    if vol_ratio is not None:
        if vol_ratio > 5:
            results.append(f"[VP-001] 量比={vol_ratio:.1f} > 5 → 暴力放量 ⚠️异动")
        elif vol_ratio > 2.5:
            results.append(f"[VP-001] 量比={vol_ratio:.1f} (2.5-5) → 显著放量 🟡")
        elif vol_ratio > 1.5:
            results.append(f"[VP-001] 量比={vol_ratio:.1f} (1.5-2.5) → 明显放量 ✅")
        elif vol_ratio < 0.5:
            results.append(f"[VP-001] 量比={vol_ratio:.1f} < 0.5 → 极度缩量 ❌冷清")
        elif vol_ratio < 0.8:
            results.append(f"[VP-001] 量比={vol_ratio:.1f} (0.5-0.8) → 缩量 🟡")
        else:
            results.append(f"[VP-001] 量比={vol_ratio:.1f} (0.8-1.2) → 正常量能")

    # 量价位置结合
    if position_pct is not None and vol_ma20_ratio is not None:
        if position_pct > 80 and vol_ma20_ratio > 1.5:
            results.append(f"[C-03] 高位({position_pct:.0f}%)+放量({vol_ma20_ratio:.1f}倍) → ⚠️放量出货风险")
        elif position_pct < 30 and vol_ma20_ratio > 1.5:
            results.append(f"[C-03] 低位({position_pct:.0f}%)+放量({vol_ma20_ratio:.1f}倍) → ✅放量启动信号")
    
    return results


# ===== 4. 追高判定（三条件）=====
def check_chase(pct_60d, is_limit_price, regime_end):
    """TS-005: 追高三条件判定"""
    results = []
    conditions = []
    
    cond1 = pct_60d > 0.70 if pct_60d is not None else None
    cond2 = not is_limit_price if is_limit_price is not None else None
    cond3 = regime_end if regime_end is not None else None
    
    if pct_60d is not None:
        results.append(f"  条件1：60日分位={pct_60d*100:.0f}% {'>70% ✅' if cond1 else '<70% ❌'}")    
    if is_limit_price is not None:
        results.append(f"  条件2：{'非涨停价' if is_limit_price else '涨停价'}买 {'✅' if cond2 else '❌（打板豁免）'}")
    if regime_end is not None:
        results.append(f"  条件3：主升末端={'是' if regime_end else '否'} {'✅' if cond3 else '❌'}")
    
    if cond1 and cond2 and cond3:
        verdict = "❌ **追高成立**——三条件全满足，禁止买入"
    else:
        unmet = []
        if not cond1: unmet.append("条件1(60日分位≤70%)")
        if not cond2: unmet.append("条件2(涨停价=打板豁免)")
        if not cond3: unmet.append("条件3(非主升末端)")
        verdict = f"✅ **非追高**——{', '.join(unmet)}不满足"
    
    results.append(f"[TS-005] → {verdict}")
    return results


# ===== 5. 突破判定 =====
def check_breakout(close, resistance, vol_ma20_ratio):
    """T-005: 有效突破/假突破判定"""
    results = []
    
    if close > resistance:
        if vol_ma20_ratio is not None and vol_ma20_ratio > 1.5:
            results.append(f"[T-005] 收盘{close} > 阻力{resistance} + 放量{vol_ma20_ratio:.1f}倍 → ✅**有效突破**（还需3日站稳确认）")
        else:
            results.append(f"[T-005] 收盘{close} > 阻力{resistance} BUT 量不足{vol_ma20_ratio:.1f}倍 → ⚠️**疑似假突破**（无量突破不可信）")
    else:
        results.append(f"[T-005] 收盘{close} < 阻力{resistance} → 未突破")
    
    return results


# ===== 6. 分歧vs一致判定 =====
def check_divergence(turnover, amplitude):
    """SC-002: 分歧/一致判定"""
    results = []
    
    if turnover is not None and turnover > 10 and amplitude is not None and amplitude > 8:
        results.append(f"[SC-002] 换手={turnover:.1f}%>10% + 振幅={amplitude:.1f}%>8% → **分歧** ✅（买点/接力点）")
    elif turnover is not None and turnover < 5 and amplitude is not None and amplitude < 4:
        results.append(f"[SC-002] 换手={turnover:.1f}%<5% + 振幅={amplitude:.1f}%<4% → **一致** ⚠️（持有/卖点）")
    else:
        results.append(f"[SC-002] 换手={turnover:.1f}% + 振幅={amplitude:.1f}% → 中间态 🟡")
    
    return results


# ===== 7. 趋势股 vs 连板股判定 =====
def check_stock_type(consecutive_limits, turnover, ma_arranged, close_pct_mean):
    """DC-001/DC-002: 趋势股/连板股判定"""
    results = []
    
    is_limit_up_stock = consecutive_limits >= 2 and turnover > 10
    is_trend_stock = ma_arranged and turnover < 5 and close_pct_mean < 4
    
    if is_limit_up_stock and not is_trend_stock:
        results.append(f"[DC-002] 连板{consecutive_limits}天 + 换手{turnover:.1f}%>10% → **连板股（情绪股）** ✅")
        results.append("  操作：打板/接力/分歧低吸，止损严格(-8%)")
        results.append("  ⚠️ 断板后可能A杀，不可用趋势股方法操作")
    elif is_trend_stock and not is_limit_up_stock:
        results.append(f"[DC-001] 均线多头 + 换手{turnover:.1f}%<5% + 日均涨幅{close_pct_mean:.1f}%<4% → **趋势股** ✅")
        results.append("  操作：回踩MA10/MA20买，偏离MA20远卖，不追涨停")
    elif is_limit_up_stock and is_trend_stock:
        results.append(f"[DC-001/002] **混合形态**——同时满足趋势股和连板股特征 🟡")
    else:
        results.append(f"[DC-001/002] 非典型形态——需结合其他维度判断 🟡")
    
    return results


# ===== 8. 弱转强判定 =====
def check_weak_to_strong(yesterday_weak_type, today_open_pct, today_min30_pct):
    """STR-001: 弱转强判定"""
    results = []
    
    weak_types = {"炸板": "昨日炸板(涨停打开未回封)", 
                  "烂板": "昨日烂板(开板≥2次)",
                  "冲高回落": "昨日冲高回落(长上影)",
                  "阴线": "昨日阴线"}
    
    yesterday_str = weak_types.get(yesterday_weak_type, f"昨日状态:{yesterday_weak_type}")
    
    if today_open_pct > 2:
        results.append(f"{yesterday_str} + 今日高开{today_open_pct:+.1f}%>2%")
        if today_min30_pct > 5:
            results.append(f"   + 30分钟涨幅{today_min30_pct:+.1f}%>5%")
            results.append(f"[STR-001] → **弱转强成立** ✅（可关注接力）")
        else:
            results.append(f"   + 30分钟涨幅{today_min30_pct:+.1f}%（未超5%需观察）")
            results.append(f"[STR-001] → **弱转强初步成立** 🟡（高开但拉涨力度不够，观察量能）")
    elif today_open_pct < -2:
        results.append(f"{yesterday_str} + 今日低开{today_open_pct:+.1f}% → 弱转强❌（低开则继续弱）")
    else:
        results.append(f"{yesterday_str} + 今日平开{today_open_pct:+.1f}% → 中性开盘 🟡")
    
    return results


# ===== 主函数 =====
def main():
    parser = argparse.ArgumentParser(description='术语量化快速核查工具 v1.0')
    sub = parser.add_subparsers(dest='command', required=True)
    
    # 情绪周期判定
    p_regime = sub.add_parser('regime', help='SC-001 情绪周期判定')
    p_regime.add_argument('--adx', type=float, help='ADX(14)值')
    p_regime.add_argument('--prem-seal', type=float, help='昨日涨停今日均涨幅%')
    p_regime.add_argument('--limits', type=int, help='涨停家数')
    p_regime.add_argument('--top-board', type=int, help='最高连板')
    p_regime.add_argument('--zha-ban-rate', type=float, help='炸板率%')
    
    # 趋势判定
    p_trend = sub.add_parser('trend', help='T-001/T-002 均线排列判定')
    p_trend.add_argument('--ma5', type=float)
    p_trend.add_argument('--ma10', type=float)
    p_trend.add_argument('--ma20', type=float)
    p_trend.add_argument('--ma60', type=float)
    p_trend.add_argument('--adx', type=float)
    
    # 量价判定
    p_vol = sub.add_parser('volume', help='VP-001~VP-005 量价状态')
    p_vol.add_argument('--vol-ratio', type=float, help='量比')
    p_vol.add_argument('--vol-ma20-ratio', type=float, help='成交量/20日均量倍数')
    p_vol.add_argument('--position', type=float, help='60日分位%')
    
    # 追高判定
    p_chase = sub.add_parser('chase', help='TS-005 追高三条件')
    p_chase.add_argument('--pct-60d', type=float, required=True, help='60日分位（0-1）')
    p_chase.add_argument('--is-limit-price', choices=['yes','no'], required=True, help='是否涨停价买')
    p_chase.add_argument('--regime-end', choices=['yes','no'], required=True, help='是否主升末端')
    
    # 突破判定
    p_break = sub.add_parser('breakout', help='T-005 有效/假突破')
    p_break.add_argument('--close', type=float, required=True)
    p_break.add_argument('--resistance', type=float, required=True)
    p_break.add_argument('--vol-ratio', type=float, help='量比')
    
    # 分歧一致
    p_div = sub.add_parser('divergence', help='SC-002 分歧/一致')
    p_div.add_argument('--turnover', type=float, required=True, help='换手率%')
    p_div.add_argument('--amplitude', type=float, required=True, help='振幅%')
    
    # 趋势股vs连板股
    p_type = sub.add_parser('stocktype', help='DC-001/002 趋势股vs连板股')
    p_type.add_argument('--limits', type=int, required=True, help='连续涨停天数')
    p_type.add_argument('--turnover', type=float, required=True, help='换手率%')
    p_type.add_argument('--ma-arranged', choices=['yes','no'], required=True, help='是否多头排列')
    p_type.add_argument('--avg-pct', type=float, default=3, help='日均涨跌幅%')
    
    # 弱转强
    p_w2s = sub.add_parser('weak2strong', help='STR-001 弱转强判定')
    p_w2s.add_argument('--yesterday', choices=['炸板','烂板','冲高回落','阴线'], required=True)
    p_w2s.add_argument('--open-pct', type=float, required=True, help='开盘涨幅%')
    p_w2s.add_argument('--min30-pct', type=float, default=0, help='30分钟涨幅%')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("《屠龙系统量化术语标准手册》快速核查工具 v1.0")
    print("=" * 60)
    
    if args.command == 'regime':
        results = check_regime(args.adx, args.prem_seal, args.limits, args.top_board, args.zha_ban_rate)
    elif args.command == 'trend':
        results = check_trend(args.ma5, args.ma10, args.ma20, args.ma60, args.adx)
    elif args.command == 'volume':
        results = check_volume(args.vol_ratio, args.vol_ma20_ratio, args.position)
    elif args.command == 'chase':
        is_limit = args.is_limit_price == 'yes'
        is_end = args.regime_end == 'yes'
        results = check_chase(args.pct_60d, is_limit, is_end)
    elif args.command == 'breakout':
        results = check_breakout(args.close, args.resistance, args.vol_ratio)
    elif args.command == 'divergence':
        results = check_divergence(args.turnover, args.amplitude)
    elif args.command == 'stocktype':
        ma_ok = args.ma_arranged == 'yes'
        results = check_stock_type(args.limits, args.turnover, ma_ok, args.avg_pct)
    elif args.command == 'weak2strong':
        results = check_weak_to_strong(args.yesterday, args.open_pct, args.__dict__['min30_pct'])
    
    for r in results:
        print(f"  {r}")
    print("=" * 60)
    
    return 0


if __name__ == '__main__':
    raise SystemExit(main())