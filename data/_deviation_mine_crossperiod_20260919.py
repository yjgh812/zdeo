# -*- coding: utf-8 -*-
"""乖离雷+量分位雷 跨期验证（2026-09-19）
来源: 昨夜241笔实证——乖离>+3%追入 n=148 胜率14.9%均-1408(铁证级单窗口); 量分位>=80=8-9月86%亏损来源。
按 SELECTION-CHECKLIST 教训(8-9月规律不可外推), 升级为规则前必须跨期验证。
样本: bp_ep_core(2044)+ml_signal(1324) 2016-2026 回测逐笔, qfq 算买入日乖离(买入价/MA20-1)与量分位(买入量在250日分位)。
口径声明: 回测为月度调仓 next-bar 开盘买入, 乖离=开盘价/MA20——与实盘盘中追入乖离不同构(开盘乖离天然小于盘中追高),
  若低乖离样本不足导致分组失衡, 如实报告并判定不可证。
预声明: H1 乖离>3%组胜率<其余组且分年方向一致>=8/11 为PASS; H2 量分位>=80组胜率<其余组且分年>=8/11 为PASS。
"""
import csv, json, sqlite3, statistics, sys, os
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'
BASE = os.path.join(ROOT, 'reports', 'drift_check_20260918a_0917end')
q = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qc = q.cursor()

def state_at(code, date, buy_px):
    """买入日: (乖离%=买入价/MA20-1, 量分位=当日量在近250日分位)"""
    r = qc.execute('SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 250',
                   (code.zfill(6), date)).fetchall()
    if len(r) < 60 or not r[1][1]:
        return None, None
    closes = [x[0] for x in r]; vols = [x[1] for x in r]
    ma20 = sum(closes[:20]) / 20
    off = round((buy_px / ma20 - 1) * 100, 1) if ma20 else None
    vs = sorted(vols)
    vrank = round(100 * sum(1 for v in vs if v <= vols[0]) / len(vs))
    return off, vrank

OUT = {}
for arm in ('bp_ep_core', 'ml_signal'):
    with open(os.path.join(BASE, f'{arm}_trades_full.csv'), encoding='utf-8-sig') as f:
        rd = list(csv.DictReader(f))
    recs = []
    for t in rd:
        d = t['买入日期'].replace('-', '')
        off, vrank = state_at(t['股票代码'], d, float(t['买入价格']))
        if off is None:
            continue
        recs.append({'y': d[:4], 'off': off, 'vr': vrank if vrank is not None else -1,
                     'win': float(t['盈亏金额']) > 0})
    S = {'n': len(recs)}

    def agg(rs, keyfn):
        b = defaultdict(lambda: [0, 0])
        for r in rs:
            k = keyfn(r); b[k][0] += 1; b[k][1] += r['win']
        return {k: {'n': v[0], 'win%': round(100 * v[1] / v[0], 1)} for k, v in sorted(b.items())}

    S['乖离分组'] = agg(recs, lambda r: '超乖离>3%' if r['off'] > 3 else ('贴线|off|<=3%' if abs(r['off']) <= 3 else '破线<-3%'))
    S['量分位分组'] = agg(recs, lambda r: '>=80分位' if r['vr'] >= 80 else ('50-80' if r['vr'] >= 50 else '<50'))
    # 分布声明
    S['乖离分布'] = {'>3%笔数': sum(1 for r in recs if r['off'] > 3), '中位乖离': round(statistics.median(r['off'] for r in recs), 1)}
    # 分年方向
    for hyp, keyfn_hi in (('H1_乖离', lambda r: r['off'] > 3), ('H2_量分位', lambda r: r['vr'] >= 80)):
        yr = {}
        for y in sorted({r['y'] for r in recs}):
            rs = [r for r in recs if r['y'] == y]
            hi = [r for r in rs if keyfn_hi(r)]; lo = [r for r in rs if not keyfn_hi(r)]
            if len(hi) >= 10 and len(lo) >= 10:
                wh = 100 * sum(r['win'] for r in hi) / len(hi)
                wl = 100 * sum(r['win'] for r in lo) / len(lo)
                yr[y] = round(wh - wl, 1)
        S[hyp + '_分年差(hi-lo)'] = yr
        neg = sum(1 for v in yr.values() if v < 0)
        S[hyp + '_判定'] = f"负向 {neg}/{len(yr)} 年" + (" → PASS(跨期稳定)" if yr and neg / len(yr) >= 8 / 11 and neg >= 5 else " → FAIL/不可证")
    OUT[arm] = S

q.close()
json.dump(OUT, open(os.path.join(ROOT, 'data', 'deviation_mine_crossperiod_20260919.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
