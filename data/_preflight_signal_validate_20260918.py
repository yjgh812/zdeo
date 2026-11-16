# -*- coding: utf-8 -*-
"""preflight 信号判别力实证（2026-09-18 主人授权自主迭代）
问题: 查四新信号(量价四象限/失量/贴线/regime)在本地实盘样本有无判别力?
样本: 241 笔 8-9 月已实现逐笔(selection_deep_analysis_20260916.json, 含 pnl/mkt_pos)
方法: 逐笔回溯买入日的量价状态(qfq 补算), 分组算胜率/均值, 组间差>=10pp 且 n>=30 为有判别力
预声明: 任何维度无判别力则查四该维度降级为描述层(诚实标注), 有判别力则写入阈值背书
"""
import json, sqlite3, statistics, sys, os
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'
recs = json.load(open(os.path.join(ROOT, 'attribution', 'selection_deep_analysis_20260916.json'), encoding='utf-8'))
q = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qc = q.cursor()

def vp_state(code, date):
    """买入日的量价状态: (四象限比值, 失量比, 乖离%) 以买入日为最近一根K线"""
    r = qc.execute('SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 60',
                   (code.zfill(6), date)).fetchall()
    if len(r) < 45:
        return None, None, None
    closes = [x[0] for x in r]; vols = [x[1] for x in r]
    up_v, dn_v, up_n, dn_n = 0.0, 0.0, 0, 0
    for i in range(20):
        if closes[i] > closes[i + 1]:
            up_v += vols[i]; up_n += 1
        elif closes[i] < closes[i + 1]:
            dn_v += vols[i]; dn_n += 1
    vp = round((up_v / up_n) / (dn_v / dn_n), 2) if up_n and dn_n and dn_v else None
    vr30 = sum(vols[:30]) / (sum(vols[30:60]) / 30) if sum(vols[30:60]) else None
    ma20 = sum(closes[:20]) / 20
    off = round((closes[0] / ma20 - 1) * 100, 1)
    return vp, vr30, off

groups = {k: defaultdict(lambda: [0, 0, []]) for k in ('regime', 'vp_quad', 'vol_trend', 'off_ma20')}
miss = 0
for rec in recs:
    pnl = rec['pnl']; win = pnl > 0
    # regime: mkt_pos 字段=买入日市场pos250中位(0-1)
    mp = rec.get('mkt_pos')
    if mp is not None:
        g = 'LOW(<0.25)' if mp < 0.25 else ('MID(0.25-0.40)' if mp < 0.40 else 'HIGH(>=0.40)')
        b = groups['regime'][g]; b[0] += 1; b[1] += win; b[2].append(pnl)
    vp, vr, off = vp_state(rec['code'], rec['date'])
    if vp is None:
        miss += 1; continue
    if vp >= 1.2:
        g = '配合(>=1.2)'
    elif vp <= 0.8:
        g = '背离(<=0.8)'
    else:
        g = '中性'
    b = groups['vp_quad'][g]; b[0] += 1; b[1] += win; b[2].append(pnl)
    if vr is not None:
        g = '失量(<0.85)' if vr < 0.85 else ('带量(>=1.15)' if vr >= 1.15 else '平量')
        b = groups['vol_trend'][g]; b[0] += 1; b[1] += win; b[2].append(pnl)
    if off is not None:
        g = '贴线(|off|<=3%)' if abs(off) <= 3 else ('高位乖离(>3%)' if off > 3 else '跌破MA20(<-3%)')
        b = groups['off_ma20'][g]; b[0] += 1; b[1] += win; b[2].append(pnl)
q.close()

OUT = {'样本': len(recs), '量价缺失': miss}
for dim, bd in groups.items():
    OUT[dim] = {k: {'n': v[0], 'win%': round(100 * v[1] / v[0], 1),
                    'mean_pnl': round(statistics.mean(v[2]))} for k, v in sorted(bd.items())}
    ns = [v[0] for v in bd.values()]; ws = [100 * v[1] / v[0] for v in bd.values()]
    OUT[dim + '_判定'] = f"组间胜率差 {round(max(ws) - min(ws), 1)}pp, 最大组n={max(ns)}, 最小组n={min(ns)}" + \
                        (' → 有判别力' if (max(ws) - min(ws) >= 10 and min(ns) >= 30) else ' → 判别力不足(降级描述层)')

json.dump(OUT, open(os.path.join(ROOT, 'data', 'preflight_signal_validate_20260918.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
