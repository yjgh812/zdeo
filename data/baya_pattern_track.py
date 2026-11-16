# -*- coding: utf-8 -*-
"""百亚型指纹观察池 T+20 追踪(BAYA-PATTERN-TRACK, 只读观察零交易影响, 20260915)

内容: v2全维扫描妖气校验后13只候选(9/15收盘入场口径), 追踪fwd5/fwd10/fwd20 vs 全市场等权。
被测假设: bp_ep质量域内"深跌安静质量"状态指纹组fwd20跑赢市场。
历史底座(938笔, 诚实记录): fwd20通过组+3.92%/胜率57% vs 不通过+1.37%/49%, 但分年仅6/10正,
  且2026当年-2.47pp为负; 全持仓期通过组反而跑输(+6.23% vs +9.64%) -> 本track测的是
  "20日观察窗优势", 不是长持优势。首评约20261017-20(T0=20260914+20交易日)。
用法: python data/baya_pattern_track.py --eval
"""
import sys, os, json, bisect, sqlite3, statistics
sys.stdout.reconfigure(encoding='utf-8')

TRACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baya_pattern_track_20260915.jsonl')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
CANDS = ['300926', '603568', '002058', '603612', '300815', '300641', '300622',
         '300824', '301019', '601717', '603697', '002085', '001390']
NAMES = {'300926': '博俊科技', '603568': '伟明环保', '002058': '紫竹高科', '603612': '索通发展',
         '300815': '玉禾田', '300641': '正丹股份', '300622': '博士眼镜', '300824': '北鼎股份',
         '301019': '宁波色母', '601717': '中创智领', '603697': '有友食品', '002085': '万丰奥威',
         '001390': '古麒绒材'}
T0 = 20260914  # qfq最新已同步交易日(与扫描数据同源,无前视)

def ensure_track():
    if os.path.exists(TRACK):
        return
    conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    rec = dict(t0=T0, note='v2全维Top20经妖气校验13只; 假设=指纹组fwd20>市场等权; 历史底座见docstring')
    items = []
    for c in CANDS:
        row = conn.execute('SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?', (c, str(T0))).fetchone()
        items.append(dict(code=c, name=NAMES[c], p0=row[0] if row else None))
    conn.close()
    rec['items'] = items
    with open(TRACK, 'w', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print(f'登记 {len(items)} 只 @ {T0} -> {TRACK}')

def eval_(nd_list=(5, 10, 20)):
    rec = json.loads(open(TRACK, encoding='utf-8').readline())
    t0 = rec['t0']
    conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    dates = [r[0] for r in conn.execute('SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date', (str(t0),))]
    idx = {d: i for i, d in enumerate(dates)}
    if str(t0) not in idx:
        print('T0非交易日?'); return
    import numpy as np
    for nd in nd_list:
        if len(dates) <= nd:
            print(f'fwd{nd} 未成熟(现仅{len(dates)-1}个交易日)'); continue
        td = dates[nd]
        rets = []
        for it in rec['items']:
            r = conn.execute('SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?', (it['code'], td)).fetchone()
            if r and it['p0']:
                rets.append((r[0]/it['p0']-1)*100)
        mkt = conn.execute(
            'SELECT AVG(c/p0-1)*100 FROM (SELECT code, '
            '(SELECT close FROM stock_daily_meta_qfq q2 WHERE q2.code=q1.code AND q2.date=?) p0, close c '
            'FROM stock_daily_meta_qfq q1 WHERE date=?)', (str(t0), td)).fetchone()[0]
        print(f"fwd{nd}({td}): 池等权{statistics.mean(rets):+.2f}% (n={len(rets)}) vs 全市场等权{mkt:+.2f}% "
              f"-> 超额{statistics.mean(rets)-mkt:+.2f}pp")
    conn.close()

if __name__ == '__main__':
    if '--init' in sys.argv:
        ensure_track()
    elif '--eval' in sys.argv:
        eval_()
    else:
        print(__doc__)
