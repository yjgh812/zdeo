# -*- coding: utf-8 -*-
"""收阴簇/日内高位接盘 跨期独立段验证 2026-09-18
假设来源: 8-9月实盘241笔发现——收阴簇115笔胜率13.0%占总亏损62%;收阴×浅追4.1%/收阳×追≥3% 9.1%最毒。
验证样本: bp_ep_core(2044笔)+ml_signal(1324笔) 2016-2026 回测产物,qfq独立取价。
预先声明判定标准(防数据窥探):
  H1 收阴簇(买入当日收阴)胜率<收阳簇,分年方向一致率>=8/11 为PASS
  H2 高位组(收阴×日内0-3浅追 ∪ 收阳×日内>=3)胜率<其余组,分年方向一致率>=8/11 为PASS
口径声明: 回测为月度调仓next-bar买入,买入价=次日开盘,故"日内追涨度"实为隔夜跳空幅度,
  与实盘盘中追涨不同构;但"当日收阴"分组检验依然有效(买入当天转弱的负向)。
"""
import csv, json, sqlite3, statistics, sys, os
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'
BASE = os.path.join(ROOT, 'reports', 'drift_check_20260918a_0917end')

qconn = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qcur = qconn.cursor()

def qfetch(code, date):
    r = qcur.execute("SELECT close, change_pct FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 2",
                     (code.zfill(6), date)).fetchall()
    if len(r) < 2 or not r[1][0]:
        return None, None, None
    return float(r[0][0]), (float(r[0][1]) if r[0][1] is not None else None), float(r[1][0])

OUT = {'samples': {}}
for arm in ('bp_ep_core', 'ml_signal'):
    with open(os.path.join(BASE, f'{arm}_trades_full.csv'), encoding='utf-8-sig') as f:
        rd = list(csv.DictReader(f))
    recs = []
    miss = 0
    for t in rd:
        d = t['买入日期'].replace('-', ''); code = t['股票代码']; price = float(t['买入价格'])
        close, day_chg, prev = qfetch(code, d)
        if close is None or day_chg is None or prev is None:
            miss += 1; continue
        intra = (price/prev - 1) * 100
        grp = ('收阴' if day_chg < 0 else '收阳')
        pos = ('接飞刀<0' if intra < 0 else ('浅追0-3' if intra < 3 else ('追3-6' if intra < 6 else '追>=6')))
        hi = (grp == '收阴' and 0 <= intra < 3) or (grp == '收阳' and intra >= 3)
        recs.append({'year': d[:4], 'grp': grp, 'pos': pos, 'hi': hi,
                     'win': float(t['盈亏金额']) > 0, 'pnl_pct': float(t['盈亏比例%'])})
    S = {'n': len(recs), '缺行情': miss}

    def agg(rs, keyfn):
        b = defaultdict(lambda: [0, 0, []])
        for r in rs:
            k = keyfn(r); b[k][0] += 1; b[k][1] += r['win']; b[k][2].append(r['pnl_pct'])
        return {k: {'n': v[0], 'win%': round(100*v[1]/v[0], 1), 'mean_pnl%': round(statistics.mean(v[2]), 2)}
                for k, v in sorted(b.items())}

    S['按收盘方向'] = agg(recs, lambda r: r['grp'])
    S['按日内位置'] = agg(recs, lambda r: r['pos'])
    S['高位组vs其余'] = agg(recs, lambda r: '高位组(收阴浅追∪收阳追>=3)' if r['hi'] else '其余')
    S['四分组'] = agg(recs, lambda r: f"{r['grp']}×{r['pos']}")

    # 分年方向一致性
    yrs = sorted({r['year'] for r in recs})
    dir_h1, dir_h2 = {}, {}
    for y in yrs:
        rs = [r for r in recs if r['year'] == y]
        w1 = agg(rs, lambda r: r['grp']); w2 = agg(rs, lambda r: 'H' if r['hi'] else 'L')
        if '收阴' in w1 and '收阳' in w1 and w1['收阴']['n'] >= 10 and w1['收阳']['n'] >= 10:
            dir_h1[y] = round(w1['收阴']['win%'] - w1['收阳']['win%'], 1)
        if 'H' in w2 and 'L' in w2 and w2['H']['n'] >= 10 and w2['L']['n'] >= 10:
            dir_h2[y] = round(w2['H']['win%'] - w2['L']['win%'], 1)
    S['H1分年_收阴-收阳胜率差'] = dir_h1
    S['H2分年_高位组-其余胜率差'] = dir_h2
    S['H1判定'] = f"负向年 {sum(1 for v in dir_h1.values() if v < 0)}/{len(dir_h1)}"
    S['H2判定'] = f"负向年 {sum(1 for v in dir_h2.values() if v < 0)}/{len(dir_h2)}"
    OUT['samples'][arm] = S

qconn.close()
json.dump(OUT, open(os.path.join(ROOT, 'data', 'chase_cluster_crossperiod_20260918.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
