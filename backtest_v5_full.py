# -*- coding: utf-8 -*-
"""屠龙选股系统v5.0 全量回测（2026-09-19 主人令：所有股票全量回测）
策略规则（今天定稿的六层标准）:
  入场:
    1. 前60日跌>=25% (深跌前提)
    2. 放量大阳启动(量>=2xMA20量 且 涨>=3%) 后 2-40日内
    3. 距段起 -3%~+17% (最优+5~10%优先)
    4. MA20 > MA60 (多头)
    5. pos250 < 50% (低位)
    6. 三年分位 < 55%, 距三年低 < 80%
    7. 排除: 乖离>3%(bp_ep臂雷), 量分位>=80(9/11年雷)
  出场:
    - 止损: -8% (S-5铁律)
    - 止盈: 持有20日到期卖出
  仓位: 等权(每次买入金额固定)
回测区间: 2021-09-01 ~ 2026-09-18
对比: 上证指数同期
"""
import sqlite3, sys, os, json, statistics
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"

def f3(x):
    try: return float(x)
    except Exception: return None

def run_backtest():
    db = sqlite3.connect(QFQ, uri=True)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20210901'").fetchall()]
    codes = [c for c in codes if not c.startswith('688')]

    all_trades = []
    for ci, code in enumerate(codes):
        if ci % 500 == 0:
            print(f'  ...{ci}/{len(codes)} trades={len(all_trades)}', flush=True)
        nm_code = code
        h = db.execute(
            "SELECT date, close, volume FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>='20210901' AND date<='20260918' ORDER BY date ASC",
            (code,)).fetchall()
        rows = [(d, f3(c), f3(v)) for d, c, v in h if f3(c) is not None]
        if len(rows) < 400:
            continue
        dates = [r[0] for r in rows]
        closes = [r[1] for r in rows]
        vols = [r[2] or 0 for r in rows]
        n = len(closes)
        avg_v = sum(vols) / n
        if not avg_v:
            continue

        # 预计算均线
        ma20 = [None]*n; ma60 = [None]*n; v20 = [None]*n
        for i in range(59, n):
            ma20[i] = sum(closes[i-19:i+1])/20
            ma60[i] = sum(closes[i-59:i+1])/60
            v20[i] = sum(vols[i-19:i+1])/20

        # 逐日扫描入场信号
        in_position = False
        entry_px = 0; entry_i = 0; entry_date = ''
        for i in range(120, n-2):
            c = closes[i]
            if not c or c > 30 or c < 2:
                continue
            if ma20[i] is None or ma60[i] is None or v20[i] is None:
                continue

            # === 持仓中: 检查出场 ===
            if in_position:
                days_held = i - entry_i
                ret = (c / entry_px - 1) * 100
                if ret <= -8:  # 止损
                    all_trades.append({'code': code, 'entry': entry_date, 'exit': dates[i],
                                       'entry_px': entry_px, 'exit_px': c, 'ret': ret,
                                       'days': days_held, 'exit_type': '止损'})
                    in_position = False
                elif days_held >= 20:  # 20日到期
                    all_trades.append({'code': code, 'entry': entry_date, 'exit': dates[i],
                                       'entry_px': entry_px, 'exit_px': c, 'ret': ret,
                                       'days': days_held, 'exit_type': '到期'})
                    in_position = False
                continue

            # === 空仓中: 检查入场信号 ===
            # 条件1: 前60日跌>=25%
            pre60 = closes[max(0, i-60):i]
            if len(pre60) < 40:
                continue
            pre_dd = (min(pre60) / max(pre60) - 1) * 100
            if pre_dd > -25:
                continue

            # 条件2: 放量大阳启动(近10日内)
            start_i = None
            yr_idx = list(range(max(0, i-249), i+1))
            yr_closes = [closes[j] for j in yr_idx]
            if len(yr_closes) < 250:
                continue
            lo = min(yr_closes)
            lo_pos = yr_closes.index(lo)
            # 找低点后的首个放量大阳(倒序扫=从今天往回找最近的)
            for j in range(i, max(i-40, lo_i_global := yr_idx[lo_pos]), -1):
                if j >= n or j < 1 or not closes[j-1]:
                    continue
                vw = vols[j:j+20]
                if len(vw) < 20:
                    break
                v20j = sum(vw) / 20
                if v20j and vols[j] >= 2 * v20j and (closes[j] / closes[j-1] - 1) >= 0.03:
                    start_i = j
                    break
            if start_i is None:
                continue

            off_start = (c / closes[start_i] - 1) * 100
            if off_start > 17 or off_start < -3:
                continue
            days_since = i - start_i  # 正序中的天数差
            if days_since > 40 or days_since < 0:
                continue

            # 条件4: 多头
            if ma20[i] <= ma60[i]:
                continue

            # 条件5: pos250<50%
            yr = closes[i-249:i+1]
            hi_yr, lo_yr2 = max(yr), min(yr)
            pos250 = 100 * (c - lo_yr2) / (hi_yr - lo_yr2) if hi_yr > lo_yr2 else 50
            if pos250 >= 50:
                continue

            # 条件6: 三年分位
            yr3 = closes[:i+1]
            if len(yr3) > 750:
                yr3 = yr3[-750:]
            hi3, lo3 = max(yr3), min(yr3)
            pos3 = 100 * (c - lo3) / (hi3 - lo3) if hi3 > lo3 else 50
            if pos3 >= 55 or (c / lo3 - 1) * 100 > 80:
                continue

            # 条件7: 排除乖离>3%, 量分位>=80
            off = (c / ma20[i] - 1) * 100
            if off > 3:
                continue
            vs = sorted(vols[i:min(i+250, n)])
            vr = 100 * sum(1 for v in vs if v <= vols[i]) / len(vs)
            if vr >= 80:
                continue

            # === 全部条件满足: 入场! ===
            in_position = True
            entry_px = c
            entry_i = i
            entry_date = dates[i]

    db.close()
    return all_trades


def report(trades):
    if not trades:
        print('[BT-FAIL] 零交易')
        return
    wins = [t for t in trades if t['ret'] > 0]
    losses = [t for t in trades if t['ret'] <= 0]
    n = len(trades)
    wr = 100 * len(wins) / n
    avg_ret = statistics.mean(t['ret'] for t in trades)
    med_ret = statistics.median(t['ret'] for t in trades)
    avg_win = statistics.mean(t['ret'] for t in wins) if wins else 0
    avg_loss = statistics.mean(t['ret'] for t in losses) if losses else 0
    pf = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss else float('inf')

    by_type = defaultdict(list)
    for t in trades:
        by_type[t['exit_type']].append(t)

    by_year = defaultdict(list)
    for t in trades:
        by_year[t['exit'][:4]].append(t['ret'])

    print(f'\n====== 全量回测结果 ======')
    print(f'总交易: {n} 笔')
    print(f'胜率: {wr:.1f}% ({len(wins)}W/{len(losses)}L)')
    print(f'平均收益: {avg_ret:+.2f}%  中位: {med_ret:+.2f}%')
    print(f'平均盈利: {avg_win:+.2f}%  平均亏损: {avg_loss:+.2f}%')
    print(f'盈亏比(PF): {pf:.2f}')
    print()
    print('按出场类型:')
    for typ, ts in sorted(by_type.items()):
        tn = len(ts)
        twr = 100 * sum(1 for t in ts if t['ret'] > 0) / tn
        tavg = statistics.mean(t['ret'] for t in ts)
        print(f'  {typ:6}: {tn:>5}笔 胜率{twr:>5.1f}% 均值{tavg:>+6.2f}%')
    print()
    print('按年份:')
    for yr in sorted(by_year):
        ts = by_year[yr]
        twr = 100 * sum(1 for t in ts if t > 0) / len(ts)
        tavg = statistics.mean(ts)
        print(f'  {yr}: {len(ts):>5}笔 胜率{twr:>5.1f}% 均值{tavg:>+6.2f}%')

    # 最差连亏
    rets = [t['ret'] for t in trades]
    max_consec_loss = 0; cur = 0
    for r in rets:
        if r <= 0: cur += 1; max_consec_loss = max(max_consec_loss, cur)
        else: cur = 0
    print(f'\n最大连续止损: {max_consec_loss} 次')

    out = {'total': n, 'win_rate': round(wr, 1), 'avg_ret': round(avg_ret, 2),
           'med_ret': round(med_ret, 2), 'avg_win': round(avg_win, 2),
           'avg_loss': round(avg_loss, 2), 'pf': round(pf, 2),
           'max_consec_loss': max_consec_loss,
           'by_year': {y: {'n': len(v), 'wr': round(100*sum(1 for x in v if x>0)/len(v),1),
                          'avg': round(statistics.mean(v),2)} for y,v in sorted(by_year.items())},
           'by_type': {t: {'n': len(ts), 'wr': round(100*sum(1 for x in ts if x['ret']>0)/len(ts),1),
                          'avg': round(statistics.mean(x['ret'] for x in ts),2)} for t,ts in by_type.items()}}
    json.dump(out, open(os.path.join(ROOT, 'data', 'backtest_v5_result.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n[BT-OK] data/backtest_v5_result.json')


if __name__ == '__main__':
    print('屠龙v5.0 全量回测 2021-09-01 ~ 2026-09-18')
    print('规则: 深跌>=25% + 放量大阳启动 + 距段起-3%~+17% + 多头 + 低位 + 三年分位<55% + 排雷')
    print('出场: -8%止损 / 20日到期')
    print()
    trades = run_backtest()
    report(trades)
