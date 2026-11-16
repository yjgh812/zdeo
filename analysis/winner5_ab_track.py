# -*- coding: utf-8 -*-
"""赢家画像五维 A/B跟踪器 (只读快照+qfq, 幂等记账)
score = 主线(主线2/轮动1/其他0) + 位置(中带2/深位1/灰带1/高区0) + 涨停基因(2-3板+/4板+=2, 1板=1, 无=0)
分桶: 高>=5 中3-4 低<=2
模式:
  track  = 记录当日分桶与score (幂等: 同asof跳过), T+1/T+5待后续回填
  backfill = 用qfq回填已有track行的T+1/T+5
  sep    = 9月截面回溯初验 (快照维度 vs 快照自带ret20_pct; 口径声明: 维度为9/30时点, 解释9月表现存在前视, 仅作区分力粗验)
输出: data/winner5_ab_track.jsonl
"""
import json, sqlite3, sys
from collections import defaultdict
import statistics as st
sys.stdout.reconfigure(encoding='utf-8')

MODE = sys.argv[1] if len(sys.argv) > 1 else 'track'
SNAP = r'D:\zcodesj\data\universe_portrait_winner5_latest.json'
TRACK = r'D:\zcodesj\data\winner5_ab_track.jsonl'
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'

d = json.load(open(SNAP, encoding='utf-8'))
rows = d['rows']
asof = d['asof'].replace('-', '')

def score(r):
    s = 0
    ml = r.get('w_mainline', '其他')
    s += {'主线': 2, '轮动': 1, '其他': 0}.get(ml, 0)
    s += {'中带(趋势派正桶)': 2, '深位(低吸正桶)': 1, '灰带(收紧候选)': 1, '高区(禁)': 0}.get(r.get('w_posband', ''), 0)
    s += {'2-3板': 2, '4板+': 2, '1板': 1, '无': 0}.get(r.get('w_zt10', '无'), 0)
    return s

def bucket(s):
    return '高' if s >= 5 else ('中' if s >= 3 else '低')

def score_v2(r):
    s = 0
    # 主线: dims侧集中输出 w_mainline_score (主升主线3/主线2/轮动1/其他0)
    s += {3: 3, 2: 2, 1: 1, 0: 0}.get(r.get('w_mainline_score'), 0)
    # 位置带
    s += {'中带(趋势派正桶)': 2, '深位(低吸正桶)': 1, '灰带(收紧候选)': 1, '高区(禁)': 0}.get(r.get('w_posband', ''), 0)
    # 涨停基因
    s += {'2-3板': 2, '4板+': 2, '1板': 1, '无': 0}.get(r.get('w_zt10', '无'), 0)
    # 人气
    s += {2: 2, 1: 1, 0: 0}.get(r.get('w_pop_score'), 0)
    return s

def bucket_v2(scores, s):
    """当日截面分位: >=P90 高 / P40-P90 中 / <P40 低"""
    import statistics as st2
    if not scores: return '低'
    ss = sorted(scores)
    import math
    p40 = ss[max(0, int(len(ss)*0.4)-1)]
    p90 = ss[min(len(ss)-1, int(len(ss)*0.9))]
    if s >= p90: return '高'
    if s >= p40: return '中'
    return '低'


if MODE == 'track':
    import os
    seen = set()
    if os.path.exists(TRACK):
        for ln in open(TRACK, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                seen.add(json.loads(ln).get('asof'))
            except Exception:
                continue
    if asof in seen:
        print(f'asof={asof} 已在账, 幂等跳过'); sys.exit(0)
    # v2分位分桶(当日截面P90/P40) —— 双轨: v1绝对分桶保留历史兼容, v2解决高桶退化(n=3)
    scores_all = [score_v2(r) for r in rows]
    with open(TRACK, 'a', encoding='utf-8') as f:
        for r, sv in zip(rows, scores_all):
            sc = score(r)
            f.write(json.dumps({'asof': asof, 'code': str(r['code']).zfill(6), 'score': sc, 'bucket': bucket(sc),
                                'score_v2': sv, 'bucket_v2': bucket_v2(scores_all, sv),
                                't1': None, 't5': None}, ensure_ascii=False) + '\n')
    n = sum(1 for _ in open(TRACK, encoding='utf-8'))
    print(f'track记账: asof={asof} {len(rows)}行追加, 台账共{n}行')
    b = defaultdict(int)
    bv = defaultdict(int)
    for r, sv in zip(rows, scores_all):
        b[bucket(score(r))] += 1
        bv[bucket_v2(scores_all, sv)] += 1
    print('分桶v1(绝对):', dict(b), '| v2(分位):', dict(bv))

elif MODE == 'backfill':
    cq = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    cal = [r[0] for r in cq.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='20250101' ORDER BY date")]
    cp = {x: i for i, x in enumerate(cal)}
    lines = [json.loads(x) for x in open(TRACK, encoding='utf-8')]
    need = 0; done = 0
    for L in lines:
        if L.get('t1') is None and L.get('asof') in cp:
            i = cp[L['asof']]; need += 1
            code = L['code']
            px = {dd: cl for dd, cl in cq.execute("select date,close from stock_daily_meta_qfq where code=? and date>=?", (code, L['asof']))}
            if not px: continue
            base = px.get(L['asof'])
            if not base: continue
            later = sorted(x for x in px if x > L['asof'])
            if len(later) >= 1:
                L['t1'] = round((px[later[0]] / base - 1) * 100, 2); done += 1
            if len(later) >= 5:
                L['t5'] = round((px[later[4]] / base - 1) * 100, 2)
    with open(TRACK, 'w', encoding='utf-8') as f:
        for L in lines:
            f.write(json.dumps(L, ensure_ascii=False) + '\n')
    print(f'backfill: 可回填{need}行(asof后有K线), 完成t1 {done}行')

elif MODE == 'sep':
    b1 = defaultdict(list); b5 = defaultdict(list); ball = defaultdict(list)
    for r in rows:
        s = score(r); b = bucket(s)
        v20 = r.get('ret20_pct')
        if v20 is not None: ball[b].append(v20)
        b1[b].append(r.get('bias20_pct'))
    print('== 9月截面回溯初验 (ret20_pct=8/25~9/30收益; 口径: 维度为9/30时点, 前视局限声明) ==')
    for b in ('高', '中', '低'):
        a = ball[b]
        print(f'{b}桶(n={len(a)}): ret20 med={st.median(a):+.2f}% mean={st.mean(a):+.2f}% win={100*sum(1 for x in a if x>0)/len(a):.0f}%')
    hi = ball['高']; rest = ball['中'] + ball['低']
    if hi and rest:
        diff = st.median(hi) - st.median(rest)
        print(f'高桶-其余 ret20中位差 = {diff:+.2f}pp {"(高桶占优)" if diff>0 else "(高桶落后)"}')
    json.dump({b: {'n': len(ball[b]), 'med': st.median(ball[b]), 'mean': st.mean(ball[b])} for b in ball},
              open(r'D:\zcodesj\tmp\winner5_sep_20261007.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('已存 tmp/winner5_sep_20261007.json')


# ============ v2 评分 (阶段进分+人气进分; 分位分桶) ============
if MODE == 'sep':
    bv2 = defaultdict(list)
    scores_all = [score_v2(r) for r in rows]
    for r, sv in zip(rows, scores_all):
        bv2[bucket_v2(scores_all, sv)].append(r.get('ret20_pct'))
    print('== v2评分 9月截面 ==')
    for b in ('高', '中', '低'):
        a = bv2[b]
        if a: print(f'v2-{b}桶(n={len(a)}): ret20 med={st.median(a):+.2f}% mean={st.mean(a):+.2f}% win={100*sum(1 for x in a if x>0)/len(a):.0f}%')
    hv2 = bv2['高']; rv2 = bv2['中'] + bv2['低']
    if hv2 and rv2:
        print(f'v2高桶-其余 ret20中位差 = {st.median(hv2) - st.median(rv2):+.2f}pp')
