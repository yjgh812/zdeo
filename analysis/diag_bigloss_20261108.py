# -*- coding: utf-8 -*-
"""P0诊断(只读): 两腿大亏归因。回答「止损执行不及时?」——结论: 证伪。
大亏构成91-95%=止损笔本身(止损体系固有形态); 止损执行紧(bp_ep 71.7%止损笔
深度>-10%); ml腿47.9%止损笔穿-10%以下=跳空穿仓+高波动选股域属性。
数据源 = 零污染终跑 backtest_full_20261106_eod_final。2026-11-08。
"""
import csv
from collections import defaultdict

RUN = r'D:\zcodesj\reports\backtest_full_20261106_eod_final'
REASON_CN = {
    'stop_loss': '止损(S-5)',
    'rebalance': '月度调仓换出',
    'max_holding': '持有到期',
    'end_of_sample': '样本末估值',
}


def load(leg):
    path = RUN + '\\' + leg + '_trades_full.csv'
    out = []
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            out.append({
                'pct': float(r['盈亏比例%']),
                'pnl': float(r['盈亏金额']),
                'hold': int(r['持仓天数']),
                'yr': r['买入日期'][:4],
                'reason': r['卖出触发理由'].strip() or 'unknown',
                'code': r['股票代码'].zfill(6),
                'name': r['股票名称'],
            })
    return out


def bucket(pct):
    if pct > -8.5:
        return 'a_贴线(-8~-8.5)'
    if pct > -10:
        return 'b_浅穿(-8.5~-10)'
    if pct > -15:
        return 'c_中穿(-10~-15)'
    if pct > -25:
        return 'd_深穿(-15~-25)'
    return 'e_极端(<=-25)'


def diag_leg(leg):
    rows = load(leg)
    big = [t for t in rows if t['pct'] <= -5]
    total = len(rows)
    pnl_big = sum(t['pnl'] for t in big)
    print('=====', leg, '=====')
    msg = '总笔数={0} 大亏={1}笔({2:.1f}%) 大亏合计={3:.0f}元'
    print(msg.format(total, len(big), len(big) * 100.0 / total, pnl_big))
    byr = defaultdict(lambda: [0, 0.0, 0])
    for t in rows:
        cell = byr[t['reason']]
        cell[2] += 1
        if t['pct'] <= -5:
            cell[0] += 1
            cell[1] += t['pnl']
    print('退出理由 总笔数 大亏笔 大亏占比 大亏合计')
    for reason, cell in sorted(byr.items(), key=lambda kv: -kv[1][0]):
        cn = REASON_CN.get(reason, reason)
        line = '{0:<12} {1:>5} {2:>5} {3:>7.1f}% {4:>12.0f}'
        print(line.format(cn, cell[2], cell[0], cell[0] * 100.0 / cell[2], cell[1]))
    stops = [t for t in rows if t['reason'] == 'stop_loss']
    if stops:
        db = defaultdict(lambda: [0, 0.0])
        for t in stops:
            b = bucket(t['pct'])
            db[b][0] += 1
            db[b][1] += t['pct']
        print('止损出场深度分布(止损线-8%):')
        for k in sorted(db):
            n, s = db[k]
            line = '  {0:<18} {1:>4}笔 均深{2:>7.2f}%'
            print(line.format(k, n, s / n))
    bh = sum(t['hold'] for t in big) / max(len(big), 1)
    oth = [t for t in rows if t['pct'] > -5]
    oh = sum(t['hold'] for t in oth) / max(len(oth), 1)
    print('持仓天数: 大亏均值={0:.1f}天 vs 其余={1:.1f}天'.format(bh, oh))
    ydb = defaultdict(lambda: [0, 0.0])
    for t in big:
        ydb[t['yr']][0] += 1
        ydb[t['yr']][1] += t['pnl']
    items = []
    for y in ('2024', '2025', '2026'):
        if y in ydb:
            items.append(y + ':' + str(ydb[y][0]) + '笔/' + format(ydb[y][1], ',.0f') + '元')
    print('分年大亏(近3年):', ' '.join(items))


def year2026():
    print('===== 2026段结构(两腿同期) =====')
    for leg in ('bp_ep_core', 'ml_signal'):
        rows = [t for t in load(leg) if t['yr'] == '2026']
        n = len(rows)
        pnl = sum(t['pnl'] for t in rows)
        wins = sum(1 for t in rows if t['pct'] > 0)
        wr = wins * 100.0 / max(n, 1)
        top = sorted(rows, key=lambda t: t['pnl'])[:3]
        parts = []
        for t in top:
            cn = REASON_CN.get(t['reason'], t['reason'])
            parts.append(t['code'] + t['name'] + ' ' + format(t['pct'], '+.1f') + '%(' + cn + ')')
        msg = '{0}: {1}笔 胜率{2:.1f}% 合计{3:+,.0f}元 | 最差3笔: {4}'
        print(msg.format(leg, n, wr, pnl, '; '.join(parts)))


def main():
    diag_leg('bp_ep_core')
    diag_leg('ml_signal')
    year2026()


if __name__ == '__main__':
    main()