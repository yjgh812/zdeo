# -*- coding: utf-8 -*-
"""BPEP-HIGHPOS-FILTER 影子轨(prereg_bpep_highpos_filter_v1 执行面, 只读观察零交易影响)

对照实验: bp_ep月名单 A臂(rank<=20原样) vs B臂(剔除pos250>63高位成员, 同文件rank顺延补足20).
判定: 3期OOS月度名单(20261001起)合并 B-A(fwd20)>=+2.0pp 且 >=2/3期方向为正 -> PASS_CANDIDATE;
     否则FAIL(该实现未通过验证 != 方向否定). 详见 data/prereg_bpep_highpos_filter_v1.json.

用法:
  python data/bpep_highpos_shadow.py --snapshot            # 登记OOS名单(幂等, 文件第一段>=20261001)
  python data/bpep_highpos_shadow.py --snapshot --dry-run  # 演算不落盘
  python data/bpep_highpos_shadow.py --snapshot --include-insample --dry-run  # 测试用历史名单
  python data/bpep_highpos_shadow.py --eval                # 窗口成熟后评估判定
台账: data/bpep_highpos_shadow_ledger.jsonl (幂等键=名单文件名)
边界: 主库只读; 不产生买卖信号; 不复制bp_ep因子语义(名单文件即生产真源).
"""
import sys, os, csv, glob, json, bisect, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
import statistics

BASE = r'D:\zcodesj'
LIST_DIR = os.path.join(BASE, 'multifactor', 'reports', 'daily_top20')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
LEDGER = os.path.join(BASE, 'data', 'bpep_highpos_shadow_ledger.jsonl')
THRESH = 63          # prereg冻结, 禁改
OOS_FROM = 20261001  # prereg冻结: 文件第一段>=此日期方为OOS
TOPN = 20
MIN_BARS = 60

_conn = None
def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    return _conn

_hist_cache = {}
def hist(code):
    if code not in _hist_cache:
        _hist_cache[code] = conn().execute(
            'SELECT date, close, high FROM stock_daily_meta_qfq WHERE code=? ORDER BY date',
            (code,)).fetchall()
    return _hist_cache[code]

def pos250_at(code, t0):
    """prereg口径: T0前最多250bar, (close-min)/(maxhigh-min)*100; 不足MIN_BARS或hi<=lo返回None"""
    h = hist(code)
    dates = [x[0] for x in h]
    i0 = bisect.bisect_left(dates, str(t0))
    if i0 >= len(dates) or dates[i0] != str(t0):
        return None, None
    seg = h[max(0, i0 - 249):i0 + 1]
    if len(seg) < MIN_BARS:
        return None, h[i0][1]
    closes = [x[1] for x in seg]; hi = max(x[2] for x in seg); lo = min(closes)
    if hi <= lo:
        return None, h[i0][1]
    return round((closes[-1] - lo) / (hi - lo) * 100, 2), h[i0][1]

def load_list(path):
    """读名单: A臂取rank<=20; fill池允许21~50(prereg补足条款); >50截断(8/21期全市场清单防护)"""
    out = []
    for r in csv.DictReader(open(path, encoding='utf-8-sig')):
        rk = r.get('rank', '')
        if rk.strip().isdigit() and int(rk) > 50:
            continue
        out.append((int(rk) if rk.strip().isdigit() else len(out) + 1,
                    r['code'], r['name']))
    return sorted(out)

def build_arms(path):
    t0 = int(os.path.basename(path).split('_')[3])
    members = load_list(path)
    a_arm, b_arm, fill_pool = [], [], []
    for rk, code, name in members:
        p, close = pos250_at(code, t0)
        if rk <= TOPN:
            a_arm.append(dict(rank=rk, code=code, name=name, pos250=p, close=close))
            if p is not None and p > THRESH:
                continue
            b_arm.append(dict(rank=rk, code=code, name=name, pos250=p, close=close))
        else:
            fill_pool.append(dict(rank=rk, code=code, name=name, pos250=p, close=close))
    need = TOPN - len(b_arm)
    if need > 0:
        b_arm += fill_pool[:need]
    b_arm.sort(key=lambda x: x['rank'])
    return t0, a_arm, b_arm

def fwd(code, t0, nd):
    h = hist(code)
    dates = [x[0] for x in h]
    i0 = bisect.bisect_left(dates, str(t0))
    if i0 >= len(dates) or dates[i0] != str(t0) or i0 + nd >= len(h):
        return None
    return round((h[i0 + nd][1] / h[i0][1] - 1) * 100, 2)

def snapshot(dry=False, include_insample=False):
    files = sorted(glob.glob(os.path.join(LIST_DIR, 'bp_ep_monthly_*.csv')))
    done = set()
    if os.path.exists(LEDGER) and not dry:
        done = {json.loads(l)['file'] for l in open(LEDGER, encoding='utf-8')}
    for f in files:
        fname = os.path.basename(f)
        t0 = int(fname.split('_')[3])
        if t0 < OOS_FROM and not include_insample:
            continue
        if fname in done:
            continue
        t0c, a_arm, b_arm = build_arms(f)
        rec = dict(file=fname, t0=t0c, thresh=THRESH,
                   n_a=len(a_arm), n_b=len(b_arm),
                   a=a_arm, b=b_arm, sample='oos')
        n_cut = sum(1 for x in a_arm if x['pos250'] is not None and x['pos250'] > THRESH)
        print(f"[{'DRY' if dry else 'LEDGER'}] {fname}: A={len(a_arm)} B={len(b_arm)} "
              f"(剔高位{n_cut}, pos250不可算{sum(1 for x in a_arm if x['pos250'] is None)})")
        if not dry:
            with open(LEDGER, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

def eval_():
    if not os.path.exists(LEDGER):
        print('台账为空: 先 --snapshot'); return
    recs = [json.loads(l) for l in open(LEDGER, encoding='utf-8')]
    rows = []
    for rec in recs:
        t0 = rec['t0']
        out = dict(file=rec['file'], t0=t0, n_a=rec['n_a'], n_b=rec['n_b'])
        for w in (5, 10, 20):
            fa = [fwd(x['code'], t0, w) for x in rec['a']]
            fb = [fwd(x['code'], t0, w) for x in rec['b']]
            fa = [x for x in fa if x is not None]; fb = [x for x in fb if x is not None]
            out[f'fwd{w}_a'] = round(statistics.mean(fa), 2) if fa else None
            out[f'fwd{w}_b'] = round(statistics.mean(fb), 2) if fb else None
            out[f'fwd{w}_mature'] = bool(fa) and len(fa) == rec['n_a']
        rows.append(out)
        print(f"{out['file']}: A(f10={out['fwd10_a']},f20={out['fwd20_a']}) "
              f"B(f10={out['fwd10_b']},f20={out['fwd20_b']}) fwd20成熟={out['fwd20_mature']}")
    mature = [r for r in rows if r['fwd20_mature']]
    print(f'\n判定(需>=3期fwd20成熟): 当前成熟 {len(mature)} 期')
    if len(mature) >= 3:
        diffs = [r['fwd20_b'] - r['fwd20_a'] for r in mature]
        pos_n = sum(1 for d in diffs if d > 0)
        merged = statistics.mean(diffs)
        verdict = 'PASS_CANDIDATE' if (merged >= 2.0 and pos_n >= 2) else 'FAIL'
        print(f'合并B-A(fwd20)={merged:+.2f}pp, 方向为正{pos_n}/{len(diffs)}期 -> {verdict}')
        print('(FAIL=该实现未通过验证, 不否定方向本身 — 主人2026-09-14措辞纪律)')
    else:
        print('窗口未成熟, 继续观察; 每期名单生效满20个交易日后复跑本命令')

if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--snapshot' in argv:
        snapshot(dry='--dry-run' in argv,
                 include_insample='--include-insample' in argv)
    elif '--eval' in argv:
        eval_()
    else:
        print(__doc__)
