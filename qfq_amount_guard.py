# -*- coding: utf-8 -*-
"""qfq库amount质量守卫(2026-09-06 数据事故产物, 主人指令"优化").

背景: amount列2026年1-6月60%行脏(假天量/近零), 已由 fix_qfq_amount_20260906.py 对账修复.
本守卫防止再污染, 两种模式:
  quick   vwap自洽率快检(秒级, 只读; 注意: 年内大比例除权股会产生固有误报, 报告中单列)
  audit   与vipdoc对账(分钟级, 只读; 准确口径, 除权无影响——amount/volume不复权)
阈值: 偏差行率>1% WARN, >5% FAIL(exit 2); 连续两期恶化升级.
用法:
  python qfq_amount_guard.py --db <qfq库路径> [--mode quick|audit] [--months 3]
建议挂点: health_check_daily 巡检段只读调用 quick 模式(接线需同步SOP, 待主人裁决).
"""
import os, sys, argparse, sqlite3, time
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
from tdx_reader import read_day

def month_cutoffs(n):
    """最近n个自然月起点(YYYYMMDD)"""
    import datetime
    today = datetime.date.today()
    out = []
    for k in range(n):
        m = today.month - k
        y = today.year
        while m <= 0: m += 12; y -= 1
        out.append(f'{y}{m:02d}01')
    return sorted(out)

def run_check(db, mode='quick', months=3, warn=None, fail=None):
    """守卫核心(可import复用, health_check_daily接线用). 返回:
    {status: OK|WARN|FAIL|UNAVAILABLE, rate: float, detail: str}"""
    if warn is None: warn = 15.0 if mode == 'quick' else 1.0
    if fail is None: fail = 30.0 if mode == 'quick' else 5.0
    if not os.path.exists(db) or os.path.getsize(db) == 0:
        return {'status': 'UNAVAILABLE', 'rate': None, 'detail': f'库不可用(不存在或0字节): {db}'}
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    cur = con.cursor()
    end = cur.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    start = month_cutoffs(months)[0]
    rows = cur.execute(
        "SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq WHERE date>=? AND date<=?",
        (start, end)).fetchall()
    con.close()
    if mode == 'quick':
        total = bad = 0
        bad_by_mon = {}
        for code, date, close, volume, amount in rows:
            if volume and volume > 0 and close and close > 0:
                total += 1
                if abs(amount / volume / close - 1) > 0.5:
                    bad += 1
                    bad_by_mon[date[:6]] = bad_by_mon.get(date[:6], 0) + 1
        rate = bad / total * 100 if total else 0
        detail = (f'窗口{start}~{end} vwap自洁偏离行率{rate:.2f}% (基线约12%=除权误报, 真残留0); '
                  f'月分布={dict(sorted(bad_by_mon.items()))}')
    else:
        fix_like = 0; total = 0; bad_by_mon = {}
        cache = {}
        for code, date, close, volume, amount in rows:
            mkt = 'sh' if code.startswith('6') else ('sz' if code[0] in '03' else None)
            if mkt is None: continue
            if mkt not in cache: cache[mkt] = {}
            if code not in cache[mkt]:
                cache[mkt][code] = {r[0]: r[5] for r in (read_day(mkt, code) or [])}
            va = cache[mkt][code].get(int(date))
            if va and va > 0:
                total += 1
                if abs(va - (amount or 0)) / va > 0.01:
                    fix_like += 1
                    bad_by_mon[date[:6]] = bad_by_mon.get(date[:6], 0) + 1
        rate = fix_like / total * 100 if total else 0
        detail = f'窗口{start}~{end} vipdoc对账偏差>1%行率{rate:.2f}%; 月分布={dict(sorted(bad_by_mon.items()))}'
    status = 'FAIL' if rate > fail else ('WARN' if rate > warn else 'OK')
    return {'status': status, 'rate': round(rate, 2), 'detail': detail}

def main():
    ap = argparse.ArgumentParser()
    # 2026-09-10: 默认改主库(生产真源), 与 qfq_close_guard 对齐——此前默认副本,
    # 手动跑不带 --db 会审计副本而非生产主库; health_check 显式传主库不受影响。
    ap.add_argument('--db', default=r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db')
    ap.add_argument('--mode', choices=['quick', 'audit'], default='quick')
    ap.add_argument('--months', type=int, default=3, help='检查最近N个自然月')
    ap.add_argument('--warn', type=float, default=None, help='默认: quick=15(audit=1)')
    ap.add_argument('--fail', type=float, default=None, help='默认: quick=30(audit=5)')
    args = ap.parse_args()
    r = run_check(args.db, args.mode, args.months, args.warn, args.fail)
    print(f'[{args.mode}] {r["status"]}: {r["detail"]}')
    if r['status'] == 'UNAVAILABLE':
        sys.exit(2)
    if r['status'] == 'FAIL':
        print('❌ FAIL — amount疑似污染, 禁止消费该库amount列, 执行修复工具 fix_qfq_amount_20260906.py')
        sys.exit(2)
    if r['status'] == 'WARN':
        print('⚠️ WARN — 关注趋势')
    sys.exit(0)

if __name__ == '__main__':
    main()
