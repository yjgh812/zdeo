# -*- coding: utf-8 -*-
"""qfq库close列完整性守卫(2026-09-08 接入, 源于一次性诊断脚本审计发现的系统性盲区).

背景: close 列此前无任何 owner——qfq_amount_guard.quick 把 NULL/零 close 静默排除出
vwap 分母(close 全坏 → 分母 0 → rate 0 → 报 OK 伪健康), qfq_sanity_check 对 NULL/零
close 也是静默 continue; 若 close 列重演 amount 式污染(9/6 事故 93%+ 行脏), 两个守卫
都会绿灯放行. 本守卫补位: 窗口内 close IS NULL 或 <=0 的行率真值应为 0.
口径: 只查 stock_daily_meta_qfq(生产库唯一行情表, 与 qfq_amount_guard 同表同窗风格);
窗口默认最近 90 自然日.
阈值: 脏行率>0%(即任意脏行) WARN, >0.1% FAIL(exit 2); 窗口无行=UNAVAILABLE(不伪健康).
用法:
  python qfq_close_guard.py [--db <qfq库路径>] [--days 90] [--warn 0] [--fail 0.1]
接线: health_check_daily.check_qfq_close_guard(run_check import; FAIL→ERROR 数据真相级,
UNAVAILABLE→WARN, 守卫异常 fail-open WARN 不阻断巡检).
"""
import os, sys, argparse, sqlite3, datetime
sys.stdout.reconfigure(encoding='utf-8')


def run_check(db, days=90, warn=None, fail=None):
    """close 完整性守卫核心(可 import 复用, health_check_daily 接线用). 返回:
    {status: OK|WARN|FAIL|UNAVAILABLE, rate: float|None, detail: str}"""
    if warn is None: warn = 0.0
    if fail is None: fail = 0.1
    if not os.path.exists(db) or os.path.getsize(db) == 0:
        return {'status': 'UNAVAILABLE', 'rate': None, 'detail': f'库不可用(不存在或0字节): {db}'}
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=20)
    try:
        cur = con.cursor()
        end = cur.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        if not end:
            return {'status': 'UNAVAILABLE', 'rate': None, 'detail': '表无数据(MAX(date)为空)'}
        start = (datetime.date.today() - datetime.timedelta(days=days)).strftime('%Y%m%d')
        total, n_null, n_nonpos = cur.execute(
            "SELECT COUNT(*),"
            " SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN close IS NOT NULL AND close<=0 THEN 1 ELSE 0 END)"
            " FROM stock_daily_meta_qfq WHERE date>=? AND date<=?", (start, end)).fetchone()
    finally:
        con.close()
    if not total:
        return {'status': 'UNAVAILABLE', 'rate': None, 'detail': f'窗口{start}~{end}内无行, 无法评估'}
    dirty = (n_null or 0) + (n_nonpos or 0)
    rate = dirty / total * 100
    detail = (f'窗口{start}~{end} close NULL={n_null or 0} 非正={n_nonpos or 0} '
              f'脏行率{rate:.4f}% (共{total}行; 真值应为0)')
    status = 'FAIL' if rate > fail else ('WARN' if rate > warn else 'OK')
    return {'status': status, 'rate': round(rate, 4), 'detail': detail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db')
    ap.add_argument('--days', type=int, default=90, help='检查最近N个自然日(相对今天)')
    ap.add_argument('--warn', type=float, default=0.0, help='脏行率>此值WARN(%%), 默认0=任意脏行')
    ap.add_argument('--fail', type=float, default=0.1, help='脏行率>此值FAIL(%%), 默认0.1')
    args = ap.parse_args()
    r = run_check(args.db, args.days, args.warn, args.fail)
    print(f'[close] {r["status"]}: {r["detail"]}')
    if r['status'] == 'UNAVAILABLE':
        sys.exit(2)
    if r['status'] == 'FAIL':
        print('❌ FAIL — close 列疑似污染, 禁止消费该库 close 列, 先审计修复再放行')
        sys.exit(2)
    if r['status'] == 'WARN':
        print('⚠️ WARN — 存在 NULL/非正 close 行, 定位来源(close 真值应为 0)')
    sys.exit(0)


if __name__ == '__main__':
    main()
