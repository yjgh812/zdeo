# -*- coding: utf-8 -*-
"""BAYA-FINGERPRINT-V1 影子轨(prereg_baya_fingerprint_v1 执行面, 只读观察零交易影响)

被测规则 F = R(pos250<=45∧距年高<=-30%∧ret60<=0∧ret20>=-12∧vol20<=4)
          ∧ 地量(amt_ratio<0.35) , 作用于 bp_ep 月名单 rank<=20 成员,
          且当期市场门开(上证 pos250@T0<67)才构成F组。
判定: 3期OOS(名单文件第一段>=20261001)累计F组成员>=10只方可评;
     F组合并fwd20-全市场等权>=+2.0pp 且 组内中位数>0 -> PASS_CANDIDATE, 否则FAIL。
用法:
  python data/baya_fingerprint_shadow.py --snapshot            # 登记OOS名单(幂等)
  python data/baya_fingerprint_shadow.py --snapshot --dry-run [--include-insample]
  python data/baya_fingerprint_shadow.py --eval
台账: data/baya_fingerprint_shadow_ledger.jsonl (幂等键=名单文件名)
"""
import sys, os, csv, glob, json, bisect, sqlite3, statistics
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
from datetime import datetime

BASE = r'D:\zcodesj'
LIST_DIR = os.path.join(BASE, 'multifactor', 'reports', 'daily_top20')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
IDX_CSV = os.path.join(BASE, 'data', '_sh_index_daily.csv')
LEDGER = os.path.join(BASE, 'data', 'baya_fingerprint_shadow_ledger.jsonl')
TH_AMT = 0.35
MKT_GATE = 67.0
OOS_FROM = 20261001
TOPN = 20

_qc = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
_hc = {}
def hist(code):
    if code not in _hc:
        _hc[code] = _qc.execute('SELECT date, close, high, amount FROM stock_daily_meta_qfq WHERE code=? ORDER BY date', (code,)).fetchall()
    return _hc[code]

def refresh_index():
    """baostock刷新上证日线(尽力而为, 失败沿用旧CSV)"""
    try:
        import baostock as bs
        bs.login()
        rs = bs.query_history_k_data_plus('sh.000001', 'date,close', start_date='2015-06-01', end_date='2026-12-31', frequency='d', adjustflag='3')
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        with open(IDX_CSV, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['date', 'close']); w.writerows(rows)
    except Exception as e:
        print(f'  [warn] 指数刷新失败沿用旧CSV: {e}')

_idx_d = _idx_c = None
def mkt_pos250(t0):
    global _idx_d, _idx_c
    if _idx_d is None:
        rows = list(csv.reader(open(IDX_CSV, encoding='utf-8')))[1:]
        rows = [r for r in rows if r and r[0] != 'date']
        _idx_d = [r[0].replace('-', '') for r in rows]
        _idx_c = [float(r[1]) for r in rows]
    i = bisect.bisect_left(_idx_d, str(t0))
    if i >= len(_idx_d) or _idx_d[i] != str(t0) or i < 249:
        return None
    seg = _idx_c[i-249:i+1]
    lo, hi = min(seg), max(seg)
    return (seg[-1]-lo)/(hi-lo)*100 if hi > lo else None

def member_feat(code, t0):
    hh = hist(code); dates = [x[0] for x in hh]
    i0 = bisect.bisect_left(dates, str(t0))
    if i0 >= len(dates) or dates[i0] != str(t0) or i0 < 61:
        return None
    seg = hh[max(0, i0-249):i0+1]
    c = np.array([x[1] for x in seg]); hiv = np.array([x[2] for x in seg]); amt = np.array([float(x[3]) for x in seg])
    last = c[-1]; lo, hi = c.min(), hiv.max()
    if hi <= lo:
        return None
    ret = c[1:]/c[:-1]-1
    amt20 = np.array([amt[max(0, j-19):j+1].mean() for j in range(len(amt))])
    peak = amt20[-250:].max()
    amt_ratio = float(amt20[-1]/peak) if peak > 0 else None
    pos250 = (last-lo)/(hi-lo)*100
    dist_hi = (last/hi-1)*100
    ret20 = (last/c[-21]-1)*100
    ret60 = (last/c[-61]-1)*100
    vol20 = float(np.std(ret[-20:], ddof=1)*100)
    R = (pos250 <= 45 and dist_hi <= -30 and ret60 <= 0 and ret20 >= -12 and vol20 <= 4)
    desert = amt_ratio is not None and amt_ratio < TH_AMT
    return dict(code=code, pos250=round(pos250, 1), amt_ratio=round(amt_ratio, 2) if amt_ratio is not None else None,
                R=bool(R), desert=bool(desert), F=bool(R and desert), close=round(float(last), 2))

def load_list(path):
    out = []
    for r in csv.DictReader(open(path, encoding='utf-8-sig')):
        rk = r.get('rank', '')
        if rk.strip().isdigit() and int(rk) > TOPN:
            continue
        out.append((int(rk) if rk.strip().isdigit() else len(out)+1, r['code'], r['name']))
    return sorted(out)

def snapshot(dry=False, include_insample=False):
    refresh_index()
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
        mkt = mkt_pos250(t0)
        gate_open = mkt is not None and mkt < MKT_GATE
        f_members = []
        all_members = []
        for rk, code, name in load_list(f):
            fm = member_feat(code, t0)
            if fm is None:
                continue
            fm.update(rank=rk, name=name)
            all_members.append(fm)
            if gate_open and fm['F']:
                f_members.append(fm)
        rec = dict(file=fname, t0=t0, mkt_pos250=round(mkt, 1) if mkt is not None else None,
                   gate_open=bool(gate_open), n_list=len(all_members), n_f=len(f_members),
                   f_members=f_members)
        print(f"[{'DRY' if dry else 'LEDGER'}] {fname}: 市场pos250={rec['mkt_pos250']} 门{'开' if gate_open else '关'} "
              f"F组={len(f_members)}/{len(all_members)}")
        if not dry:
            with open(LEDGER, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

def eval_():
    if not os.path.exists(LEDGER):
        print('台账为空: 先 --snapshot'); return
    recs = [json.loads(l) for l in open(LEDGER, encoding='utf-8')]
    all_f, base = [], []
    for rec in recs:
        if not rec['gate_open'] or rec['n_f'] == 0:
            print(f"{rec['file']}: 门{'关' if not rec['gate_open'] else '开'}但F组空, 跳过"); continue
        t0 = rec['t0']
        mature = 0
        for m in rec['f_members']:
            hh = hist(m['code']); dates = [x[0] for x in hh]
            i0 = bisect.bisect_left(dates, str(t0))
            if i0 >= len(dates) or dates[i0] != str(t0) or i0+20 >= len(hh):
                continue
            all_f.append((hh[i0+20][1]/hh[i0][1]-1)*100)
            mature += 1
        print(f"{rec['file']}: F组{rec['n_f']}只, fwd20成熟{mature}只")
        # 全市场等权基准(同窗)
        dates_m = [r[0] for r in _qc.execute('SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>? ORDER BY date', (str(t0),)).fetchall()]
        if len(dates_m) >= 20:
            td = dates_m[19]
            b = _qc.execute('SELECT AVG(c/p0-1)*100 FROM (SELECT code, '
                            '(SELECT close FROM stock_daily_meta_qfq q2 WHERE q2.code=q1.code AND q2.date=?) p0, '
                            'close c FROM stock_daily_meta_qfq q1 WHERE q1.date=?)', (str(t0), td)).fetchone()[0]
            if b is not None:
                base.append(b)
    if not all_f:
        print('尚无成熟F组样本'); return
    n_total = len(all_f)
    med = statistics.median(all_f)
    print(f'\nF组成员 n={n_total}: fwd20均值{statistics.mean(all_f):+.2f}% 中位{med:+.2f}% '
          f'胜率{sum(1 for v in all_f if v > 0)/n_total*100:.0f}%')
    if base:
        bm = statistics.mean(base)
        diff = statistics.mean(all_f) - bm
        if n_total < 10:
            print(f'基准(全市场等权){bm:+.2f}%, 超额{diff:+.2f}pp — 样本{n_total}<10只: 记"样本不足,判据不可评"')
        else:
            verdict = 'PASS_CANDIDATE' if (diff >= 2.0 and med > 0) else 'FAIL'
            print(f'基准(全市场等权){bm:+.2f}%, 超额{diff:+.2f}pp, 中位{med:+.2f}% -> {verdict}')
            print('(FAIL=该实现未通过验证, 不否定方向本身 — 主人20260914措辞纪律)')

if __name__ == '__main__':
    a = sys.argv[1:]
    if '--snapshot' in a:
        snapshot(dry='--dry-run' in a, include_insample='--include-insample' in a)
    elif '--eval' in a:
        eval_()
    else:
        print(__doc__)
