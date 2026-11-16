# -*- coding: utf-8 -*-
"""百亚深挖StepA: bp_ep历届名单入选时点指纹 × 前向收益 跨届一致性检验(只读研究件)

目的: 9/15单届(n=20)发现"入选时深跌低位者后续跑赢、入选时强动量者跑输",
      本脚本把全部bp_ep_monthly历届名单(8/13~9/1共6期)成员扩样,
      计算每位成员入选时点(pos250/ret20/ret60/vol20/距年高/PIT质量)与前向收益,
      输出秩相关+五分位表, 供"低位质量成长"假设的观察级证据评估。
口径: qfq库(stock_daily_meta_qfq)前复权收盘; PIT走stock_factors_fundamental_pit_v2(pit_avail_date<=T0);
      T0=文件名第一段(生成日交易日, close列即当日收盘); 前向窗+5/+10/+20交易日。
边界: 纯观察研究, 不产生任何买卖信号; 不复制bp_ep因子语义。
"""
import sys, os, csv, glob, sqlite3, bisect
sys.stdout.reconfigure(encoding='utf-8')
import statistics

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PIT = r'D:\zcodesj\factor_pit.db'  # 真身114MB; data/factor_pit.db与workbuddyspace侧均为0字节占位
LIST_DIR = r'D:\zcodesj\multifactor\reports\daily_top20'
OUT = r'D:\zcodesj\data\_baya_deep_cohort_20260915_result.json'

if not os.path.exists(PIT):
    for cand in [r'D:\workbuddyspace\factor_engine\data\factor_pit.db',
                 r'D:\zcodesj\attribution\factor_pit.db']:
        if os.path.exists(cand):
            PIT = cand; break

conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
_hist_cache = {}
def hist(code):
    if code not in _hist_cache:
        _hist_cache[code] = conn.execute(
            'SELECT date, close, high FROM stock_daily_meta_qfq WHERE code=? ORDER BY date',
            (code,)).fetchall()
    return _hist_cache[code]

_pit_conn = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True) if os.path.exists(PIT) else None
def pit_quality(code, t0):
    """PIT口径: pit_avail_date<=t0 的最新一期 roe/ep; 无则(None)"""
    if _pit_conn is None:
        return None, None
    row = _pit_conn.execute(
        'SELECT roe, ep FROM stock_factors_fundamental_pit_v2 '
        'WHERE code=? AND pit_avail_date<=? ORDER BY pit_avail_date DESC, report_date DESC LIMIT 1',
        (code, str(t0))).fetchone()
    return (row[0], row[1]) if row else (None, None)

def fingerprint(h, i0):
    """i0=index of T0 in ascending history; 返回指纹dict或None(数据不足)"""
    if i0 < 61:
        return None
    seg = h[max(0, i0-249):i0+1]
    closes = [x[1] for x in seg]; highs = [x[2] for x in seg]
    last = closes[-1]
    lo, hi = min(closes), max(highs)
    if hi <= lo:
        return None
    pos250 = (last - lo) / (hi - lo) * 100
    ret20 = (last / closes[-21] - 1) * 100
    ret60 = (last / closes[-61] - 1) * 100
    v20 = [abs(closes[j] / closes[j-1] - 1) for j in range(len(closes)-20, len(closes))]
    vol20 = statistics.mean(v20) * 100
    return dict(pos250=round(pos250, 1), ret20=round(ret20, 1), ret60=round(ret60, 1),
                vol20=round(vol20, 2), dist_hi=round((last / hi - 1) * 100, 1))

files = sorted(glob.glob(os.path.join(LIST_DIR, 'bp_ep_monthly_*.csv')))
records, seen = [], set()
for f in files:
    base = os.path.basename(f)
    t0 = int(base.split('_')[3])
    for r in csv.DictReader(open(f, encoding='utf-8-sig')):
        if r.get('rank') and r['rank'].strip().isdigit() and int(r['rank']) > 20:
            continue  # 8/21期文件为全市场2762行清单, 统一截断Top20口径
        code = r['code']
        key = (code, t0)
        if key in seen:
            continue
        seen.add(key)
        h = hist(code)
        dates = [x[0] for x in h]
        i0 = bisect.bisect_left(dates, str(t0))
        if i0 >= len(dates) or dates[i0] != str(t0):
            continue
        fp = fingerprint(h, i0)
        if fp is None:
            continue
        p0 = h[i0][1]
        def fwd(nd):
            j = i0 + nd
            return round((h[j][1] / p0 - 1) * 100, 2) if j < len(h) else None
        roe, ep = pit_quality(code, t0)
        records.append(dict(code=code, name=r['name'], t0=t0, p0=p0, **fp,
                            roe=roe, ep=ep, fwd5=fwd(5), fwd10=fwd(10), fwd20=fwd(20)))

print(f'总样本(去重后): {len(records)} 条入选事件')
# ---------- 五分位分析(按pos250, fwd10与fwd20双窗) ----------
import json
valid10 = [r for r in records if r['fwd10'] is not None]
valid20 = [r for r in records if r['fwd20'] is not None]
for tag, valid in [('fwd10', valid10), ('fwd20', valid20)]:
    valid = sorted(valid, key=lambda r: r['pos250'])
    n = len(valid); q = max(1, n // 5)
    print(f'\n按入选时pos250五分位({tag}, 样本n={n}):')
    print('分位 | pos250范围 | n | 均值 | 胜率')
    for k in range(5):
        grp = valid[k*q:(k+1)*q] if k < 4 else valid[4*q:]
        fv = [r[tag] for r in grp]
        win = sum(1 for x in fv if x > 0)
        print(f'Q{k+1} | {grp[0]["pos250"]:.0f}~{grp[-1]["pos250"]:.0f} | {len(grp)} | '
              f'{statistics.mean(fv):+.2f}% | {win}/{len(fv)}')

# ---------- 秩相关(Spearman, 手算) ----------
def spearman(xs, ys):
    def rank(a):
        s = sorted(range(len(a)), key=lambda i: a[i])
        r = [0]*len(a)
        for i, j in enumerate(s): r[j] = i
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a-mx)*(b-my) for a, b in zip(rx, ry))
    den = (sum((a-mx)**2 for a in rx) * sum((b-my)**2 for b in ry)) ** 0.5
    return num/den if den else 0

for col in ['pos250', 'ret20', 'dist_hi', 'vol20']:
    xs = [r[col] for r in valid10]; ys = [r['fwd10'] for r in valid10]
    print(f'Spearman({col}@T0, fwd10) = {spearman(xs, ys):+.3f}  (n={len(xs)})')

# ---------- 分届一致性: pos250<中位 vs >=中位 ----------
print('\n分届: 低位半区(pos250<届内中位) vs 高位半区 fwd10均值:')
byc = {}
for r in valid:
    byc.setdefault(r['t0'], []).append(r)
for t0 in sorted(byc):
    grp = byc[t0]
    med = statistics.median([r['pos250'] for r in grp])
    lo = [r['fwd10'] for r in grp if r['pos250'] < med and r['fwd10'] is not None]
    hi_ = [r['fwd10'] for r in grp if r['pos250'] >= med and r['fwd10'] is not None]
    if lo and hi_:
        print(f'  T0={t0}: n={len(grp)} 低位{statistics.mean(lo):+.2f}% vs 高位{statistics.mean(hi_):+.2f}% '
              f'差{statistics.mean(lo)-statistics.mean(hi_):+.2f}pp')

# ---------- 质量维度: PIT盈利 vs 亏损 的fwd10 ----------
prof = [r for r in valid if r['ep'] is not None]
pos_ep = [r['fwd10'] for r in prof if r['ep'] > 0]
neg_ep = [r['fwd10'] for r in prof if r['ep'] <= 0]
if pos_ep and neg_ep:
    print(f'\nPIT盈利组(n={len(pos_ep)}) fwd10 {statistics.mean(pos_ep):+.2f}% vs 亏损组(n={len(neg_ep)}) {statistics.mean(neg_ep):+.2f}%')

json.dump(dict(generated='20260915', n=len(records), records=records),
          open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'\n落盘 {OUT}')
conn.close()
if _pit_conn: _pit_conn.close()
