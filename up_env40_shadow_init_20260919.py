# -*- coding: utf-8 -*-
"""UP×env<40 门控版 prereg 影子台账首日登记(2026-09-19)
来源: data/direction_census_20260919.json UP 明细(截断80) + 大盘前置 env_pos250=31(<40 命中)。
铁律: 影子登记, 不买入; fwd 判定由到期脚本按 qfq 收盘价动态计算; 阈值/口径冻结后禁改。
"""
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
cj = json.load(open(f'{ROOT}/data/direction_census_20260919.json', encoding='utf-8'))
ups = cj['buckets']['UP']
env = 31  # census 20260919 大盘前置: 上证3911.87 = 年线窗口31分位 (生产口径=上证, 非回测等权代理)
cap = '1.56'  # ms=LOW_VOL
REG_DATE = '20260919'
rows = []
for x in ups:
    rows.append({'reg_date': REG_DATE, 'code': x['code'], 'name': x['name'],
                 'px': x['px'], 'off_start': x.get('off_start'), 'days': x.get('days'),
                 'p3': x.get('p3'), 'env_bucket': f'{env//20*20}~{env//20*20+19}',
                 'env': env, 'cap': cap, 'src': 'census_20260919',
                 'status': 'PENDING', 'fwd20': None, 'fwd60': None})
out = f'{ROOT}/data/up_env40_shadow_ledger.jsonl'
with open(out, 'a', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print(f'[SHADOW-INIT] +{len(rows)} events (env={env}, bucket {env//20*20}~{env//20*20+19}) -> {out}')
print(f"[NOTE] fwd20 到期≈{REG_DATE}后第20个交易日(约202610下旬), fwd60≈第60个交易日(约202612中旬); 判定须用 qfq 收盘+同日全市场中位超额口径")
