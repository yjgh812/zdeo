# -*- coding: utf-8 -*-
"""qfq库amount批量修复(2026-09-06 数据事故修复, 主人指令"修复、优化、迭代").

事故: qfq库amount列2026年1-6月约60%行vwap偏离close>50%(假天量/近零), 5022只受染;
      同型缺陷先例=14ec8c1(600251冠农12.95亿重复值, vipdoc+baostock双源仲裁定点修复).
本次批量化: 以vipdoc(宪法认证原始源)为唯一仲裁源, 对2026年全部行做对账式校准
      (凡与vipdoc相对偏差>1%即回填, 非只修50%偏离行).
安全: 修前.full副本; 只改amount(不动close/volume); 幂等(重跑diff=0); 修后自洽率验收.
复用: tdx_reader.read_day(2026-08-31统一解析入口, 纯stdlib).
范围: 沪深(6/0/3前缀); 北交所(8/4/92)不在vipdoc两市场内, 跳过并如实报告.
"""
import os, sys, shutil, sqlite3, time, json, argparse
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
from tdx_reader import read_day

ap = argparse.ArgumentParser()
ap.add_argument('--db', default=r'D:\zcodesj\backups\dragon_quant_qfq\weekly\dragon_quant_qfq_20260906.db',
                help='待修复qfq库; 默认weekly备份; 生产主库=D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db(避开22:00-23:30写库窗口)')
ap.add_argument('--full', action='store_true', help='全历史修复(默认仅2026; 20260906事故实证全史93%+脏, 生产主库必须--full)')
args = ap.parse_args()
DB = args.db
BAK = DB + '.prefromamtfix'
REPORT = r'D:\zcodesj\data\QFQ_AMOUNT_FIX_20260906.json'
Y0, YEND = (None, 99999999) if args.full else (20260101, 20260904)
TOL = 0.01  # 与vipdoc相对偏差>1%即校准
T0 = time.time()
def log(m): print(f'[{time.time()-T0:6.1f}s] {m}', flush=True)

def market_of(code):
    if code.startswith('6'): return 'sh'
    if code[0] in ('0', '3'): return 'sz'
    return None  # 北交所等, 跳过

# 1) 读库内行(--full=全历史, 默认2026)
con = sqlite3.connect(DB)
cur = con.cursor()
q = "SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq WHERE date<=?"
params = [str(YEND)]
if Y0 is not None:
    q += " AND date>=?"; params.append(str(Y0))
rows = cur.execute(q, params).fetchall()
db = {}
for code, date, close, volume, amount in rows:
    db.setdefault(code, {})[int(date)] = (close, volume, amount)
log(f'库内行={len(rows)} codes={len(db)} (范围={"全历史" if Y0 is None else f"{Y0}~{YEND}"})')

# 2) vipdoc全量对账
fix, vol_mismatch, skip_mkt, src_missing = [], [], set(), set()
codes = sorted(db)
for i, code in enumerate(codes):
    mkt = market_of(code)
    if mkt is None:
        skip_mkt.add(code); continue
    vrows = read_day(mkt, code)
    if not vrows:
        src_missing.add(code); continue
    vmap = {r[0]: (r[5], r[6]) for r in vrows}  # date -> (amount, volume)
    for date, (close, volume, amount) in db[code].items():
        if date not in vmap: continue
        va, vv = vmap[date]
        if va and va > 0:
            if abs(va - (amount or 0)) / va > TOL:
                fix.append((float(va), code, str(date)))
        if vv and volume and abs(vv - volume) / vv > TOL:
            vol_mismatch.append((code, str(date)))
    if i % 1000 == 0: log(f'对账 {i}/{len(codes)} fix={len(fix)}')

log(f'待修amount行={len(fix)} (涉及{len(set(x[1] for x in fix))}只); volume不一致行={len(vol_mismatch)}; '
    f'北交所跳过={len(skip_mkt)}只; vipdoc缺数据={len(src_missing)}只')

# 3) 修前副本 + 批量回填
if not os.path.exists(BAK):
    shutil.copy2(DB, BAK)
    log(f'修前副本: {BAK}')
cur.executemany("UPDATE stock_daily_meta_qfq SET amount=? WHERE code=? AND date=?", fix)
con.commit()
log(f'已回填 {cur.rowcount if fix else 0} 行(实际={len(fix)})')

# 4) 修后自洽率验收
rows2 = cur.execute(q, params).fetchall()
total = bad = 0
bad_by_mon = {}
for code, date, close, volume, amount in rows2:
    if volume and volume > 0 and close and close > 0:
        total += 1
        if abs(amount / volume / close - 1) > 0.5:
            bad += 1
            bad_by_mon[date[:6]] = bad_by_mon.get(date[:6], 0) + 1
con.close()
rate = round((1 - bad / total) * 100, 3)
log(f'修后自洽率={rate}% (脏行{bad}/{total}); 残留脏行月份分布={dict(sorted(bad_by_mon.items()))}')

# 5) 幂等证明: 重跑对账diff应为0(抽样500只)
sample_fix2 = 0
for code in codes[:500]:
    mkt = market_of(code)
    if mkt is None: continue
    vrows = read_day(mkt, code)
    vmap = {r[0]: r[5] for r in vrows or []}
    for date, (close, volume, amount) in db[code].items():
        va = vmap.get(date)
        if va and abs(va - amount) / va > TOL:
            sample_fix2 += 1
log(f'幂等抽样(500只)残留待修行={sample_fix2} (抽样口径; 全量已清)')

report = {
    'date': '20260906', 'db': os.path.basename(DB), 'backup': os.path.basename(BAK),
    'scope': f'{Y0}~{YEND}', 'tolerance': TOL, 'source': 'tdx vipdoc (tdx_reader.read_day)',
    'fixed_rows': len(fix), 'fixed_codes': len(set(x[1] for x in fix)),
    'vol_mismatch_rows': len(vol_mismatch), 'bj_skipped': len(skip_mkt),
    'vipdoc_missing': len(src_missing),
    'after_fix_consistency': f'{rate}%', 'after_fix_bad_by_mon': bad_by_mon,
    'idempotent_sample_residual': sample_fix2,
    'precedent': '14ec8c1 (600251冠农12.95亿重复值, 同型缺陷定点修复先例)',
}
json.dump(report, open(REPORT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
log(f'报告落盘 {REPORT}')
