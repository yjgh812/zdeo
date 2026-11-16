#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略验证仪表盘——实时汇总各策略的胜率/PF/样本量
每日盘后运行，输出各策略验证进度和初步结论。

用法：python dashboard.py
"""
import sqlite3
import math
import json
from collections import defaultdict
from datetime import datetime

DB_PATH = r'D:\zcodesj\attribution\attribution.db'

STRATEGY_NAMES = {
    'V1': '低开承接(2-4%)',
    'V2': '5日高动量',
    'V3': '高波+正动量',
    'V4': '主升期高波',
    'V5_low': 'AB低开(0-2%)',
    'V5_mid': 'AB中开(4-7%)',
    'V6': '超跌反弹',
    'V7': '主线板块',
    'V8': '情绪择时',
    'MANUAL': '手动交易',
}

def calc_stats(returns):
    n = len(returns)
    if n == 0:
        return {'n': 0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None
    avg = sum(returns) / n
    return {
        'n': n,
        'wins': len(wins),
        'win_rate': round(len(wins) / n * 100, 1),
        'avg_return': round(avg * 100, 2),
        'pf': round(pf, 2) if pf else None,
    }

def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print(f"{'='*70}")
    print(f"策略验证仪表盘 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")

    # 总览
    c.execute('SELECT COUNT(*), COUNT(DISTINCT strategy_id) FROM verification_trades')
    total, strategies = c.fetchone()
    c.execute('SELECT COUNT(*) FROM verification_trades WHERE entry_price IS NOT NULL')
    bought = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM verification_trades WHERE actual_ret_t1 IS NOT NULL')
    filled = c.fetchone()[0]

    print(f"\n总览:")
    print(f"  总记录: {total}笔 ({strategies}个策略)")
    print(f"  已买入: {bought}笔")
    print(f"  已回填T+1: {filled}笔")

    if filled == 0:
        print(f"\n⚠️ 尚无回填数据，请先运行 afternoon_fillback.py")
        conn.close()
        return

    # 按策略汇总
    print(f"\n{'='*70}")
    print("各策略验证进度")
    print(f"{'='*70}")

    c.execute('''SELECT strategy_id, 
                        COUNT(*) as total,
                        SUM(CASE WHEN entry_price IS NOT NULL THEN 1 ELSE 0 END) as bought,
                        SUM(CASE WHEN actual_ret_t1 IS NOT NULL THEN 1 ELSE 0 END) as filled
                 FROM verification_trades 
                 GROUP BY strategy_id 
                 ORDER BY filled DESC''')

    print(f"\n{'策略':25s} | {'总记录':>6s} | {'已买入':>6s} | {'已回填':>6s} | {'进度':>5s}")
    print("-" * 60)
    for row in c.fetchall():
        sid, total, bought, filled = row
        name = STRATEGY_NAMES.get(sid, sid)
        progress = f"{filled}/30" if filled < 30 else "✅≥30"
        print(f"{name:25s} | {total:6d} | {bought:6d} | {filled:6d} | {progress}")

    # T+1收益统计（按策略）
    print(f"\n{'='*70}")
    print("T+1收益分析（已有回填数据的策略）")
    print(f"{'='*70}")

    c.execute('''SELECT strategy_id, stock_code, entry_price, actual_ret_t1, result
                 FROM verification_trades 
                 WHERE actual_ret_t1 IS NOT NULL
                 ORDER BY strategy_id''')

    by_strategy = defaultdict(list)
    for row in c.fetchall():
        sid, code, entry, ret, result = row
        by_strategy[sid].append({'code': code, 'ret': ret, 'result': result})

    print(f"\n{'策略':25s} | {'N':>4s} | {'胜率':>6s} | {'均收':>8s} | {'PF':>5s} | {'confirmed':>9s} | {'refuted':>8s}")
    print("-" * 80)

    all_results = {}
    for sid in sorted(by_strategy.keys()):
        trades = by_strategy[sid]
        rets = [t['ret'] for t in trades]
        s = calc_stats(rets)
        confirmed = sum(1 for t in trades if t['result'] == 'confirmed')
        refuted = sum(1 for t in trades if t['result'] == 'refuted')

        name = STRATEGY_NAMES.get(sid, sid)
        pf_str = f"{s['pf']:.2f}" if s.get('pf') else "?"
        print(f"{name:25s} | {s['n']:4d} | {s['win_rate']:5.1f}% | {s['avg_return']:+7.2f}% | {pf_str:>5s} | {confirmed:9d} | {refuted:8d}")
        all_results[sid] = {**s, 'confirmed': confirmed, 'refuted': refuted}

    # 策略间对比
    if len(all_results) >= 2:
        print(f"\n{'='*70}")
        print("策略排名（按T+1均收）")
        print(f"{'='*70}")

        ranked = sorted(all_results.items(), key=lambda x: x[1].get('avg_return', -99), reverse=True)
        for rank, (sid, s) in enumerate(ranked, 1):
            if s['n'] < 5:
                continue
            name = STRATEGY_NAMES.get(sid, sid)
            verdict = '✅' if s.get('avg_return', 0) > 0.3 else ('🟡' if s.get('avg_return', 0) > 0 else '⚠️')
            print(f"  #{rank} {name}: 均收={s['avg_return']:+.2f}%, PF={s.get('pf','?')}, N={s['n']} {verdict}")

    # 下一步建议
    print(f"\n{'='*70}")
    print("下一步建议")
    print(f"{'='*70}")

    min_samples = min((s['n'] for s in all_results.values() if s['n'] > 0), default=0)
    if min_samples < 10:
        print(f"  → 样本量不足（最少{min_samples}/10），继续多交易积累")
    elif min_samples < 30:
        print(f"  → 初步方向已现（最少{min_samples}/30），继续积累到30做t检验")
    else:
        print(f"  → 样本量≥30，可做t检验判断显著性")

    conn.close()

if __name__ == '__main__':
    main()
