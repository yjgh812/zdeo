# -*- coding: utf-8 -*-
"""UP×env<40 影子台账每日登记(幂等) — PREREG_UP_ENV40_20260919 配套
用法: python up_env40_shadow_daily.py [YYYYMMDD]  (默认今天)
数据: data/direction_census_<date>.json (census 15:30 产出, UP 明细截断80)
     env 从 data/direction_census_<date>.md 大盘前置行提取(生产口径=上证 env_pos250)
幂等: ledger 已存在同 reg_date 记录则整体跳过
门控: env<40 才登记; env 缺失/≥40 不登记并打印原因
铁律: 影子登记不买入; 是否挂 cron 由主人裁决(未裁决期间可手动日跑)
"""
import glob
import json
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
LEDGER = os.path.join(ROOT, 'data', 'up_env40_shadow_ledger.jsonl')


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
    jpath = os.path.join(ROOT, 'data', f'direction_census_{date}.json')
    mpath = os.path.join(ROOT, 'reports', f'direction_census_{date}.md')
    if not os.path.exists(jpath):
        print(f'[SKIP] no census json: {jpath}')
        return
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding='utf-8') as f:
            if any(json.loads(l).get('reg_date') == date for l in f if l.strip()):
                print(f'[IDEMPOTENT] {date} already registered, skip')
                return
    # env 提取
    env = None
    if os.path.exists(mpath):
        m = re.search(r'年[线][窗]?口?(\d+)分位', open(mpath, encoding='utf-8').read())
        if m:
            env = int(m.group(1))
    if env is None:
        print(f'[SKIP] env not found in {mpath} (门控口径缺失不入台账)')
        return
    if env >= 40:
        print(f'[SKIP] env={env} >=40, 门控外不入台账')
        return
    cj = json.load(open(jpath, encoding='utf-8'))
    ups = cj.get('buckets', {}).get('UP', [])
    if not ups:
        print(f'[WARN] UP bucket empty in {jpath}, nothing to register')
        return
    bucket = f'{env // 20 * 20}~{env // 20 * 20 + 19}'
    with open(LEDGER, 'a', encoding='utf-8') as f:
        for x in ups:
            f.write(json.dumps({'reg_date': date, 'code': x['code'], 'name': x['name'],
                                'px': x['px'], 'off_start': x.get('off_start'),
                                'days': x.get('days'), 'p3': x.get('p3'),
                                'env': env, 'env_bucket': bucket, 'src': f'census_{date}',
                                'status': 'PENDING', 'fwd20': None, 'fwd60': None},
                               ensure_ascii=False) + '\n')
    print(f'[SHADOW] +{len(ups)} events (env={env}, bucket {bucket}) reg_date={date}')


if __name__ == '__main__':
    main()
