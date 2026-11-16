# -*- coding: utf-8 -*-
"""新迭代画像系统5只选样 本周验证(eval件, 只读观察, 20260915)
选样规则(选前冻结, 详见 data/week5_fingerprint_track_20260915.jsonl note):
  第1席=当前生产bp_ep名单(9/1期)完整指纹F组(唯一命中: 中国中铁 rank20 pos250=9.9 地量0.11);
  第2-5席=13票观察池6/6满指纹成员按工具地量分位升序(同分ROE真项决标):
  正丹股份(0.10,ROE真项+266%) 宁波色母(0.10,+113%) 博士眼镜(0.16,+107%) 万丰奥威(0.16,+82%)。
  古麒绒材6/6已持仓不入新选样; 润本4/6不及格。
验证: T0=20260915收盘; 本周读数=20260918收盘(fwd3) + 下周二(fwd5); 基准=全市场等权同窗。
证据边界: 指纹规律=生产域内观察级(+2~5pp/20d), 域外未证; 3日读数噪声主导, 真裁决在prereg OOS。
用法: python data/_week5_track_eval.py
"""
import sys, os, json, sqlite3, statistics
sys.stdout.reconfigure(encoding='utf-8')

TRACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'week5_fingerprint_track_20260915.jsonl')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'

rec = json.loads(open(TRACK, encoding='utf-8').readline())
t0 = rec['t0']
conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
dates = [r[0] for r in conn.execute('SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>? ORDER BY date', (t0,)).fetchall()]
print(f'T0={t0} 收盘入场, 已成熟 {len(dates)} 个交易日')
for nd in (3, 5, 20):
    if len(dates) < nd:
        print(f'fwd{nd}: 未成熟'); continue
    td = dates[nd-1]
    rets = []
    for it in rec['items']:
        r = conn.execute('SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?', (it['code'], td)).fetchone()
        if r:
            rets.append((r[0]/it['p0']-1)*100)
            print(f"  {it['code']} {it['name']}: {it['p0']} -> {r[0]} ({(r[0]/it['p0']-1)*100:+.2f}%)")
    mkt = conn.execute('SELECT AVG(c/p0-1)*100 FROM (SELECT code, '
                       '(SELECT close FROM stock_daily_meta_qfq q2 WHERE q2.code=q1.code AND q2.date=?) p0, '
                       'close c FROM stock_daily_meta_qfq q1 WHERE q1.date=?)', (t0, td)).fetchone()[0]
    print(f"fwd{nd}({td}): 5只等权 {statistics.mean(rets):+.2f}% vs 全市场等权 {mkt:+.2f}% -> 超额 {statistics.mean(rets)-mkt:+.2f}pp")
conn.close()
