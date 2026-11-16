# -*- coding: utf-8 -*-
"""历史截面json格式统一(一次性): 0904/0907/0909/0910 的 top20 键 → topn_list(500) 新格式.
同模型同截面特征重算(确定性), 仅发布层格式对齐; 保留原json的n/date等元数据.
执行后 feed 的旧格式双读分支可清理(标记退出条件)."""
import os, sys, json
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE, PORTFOLIO_TOPN

HERE = r'D:\zcodesj\portrait_agent'
panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v1.pkl'))
panel['date'] = panel['date'].astype(str)
bst = {t: lgb.Booster(model_file=os.path.join(HERE, 'models', f'v1.0.0_{t}.txt')) for t in ('short', 'mid')}

for T in ('20260904', '20260907', '20260909', '20260910'):
    p = os.path.join(HERE, 'reports', f'portrait_{T}.json')
    if not os.path.exists(p):
        print(f'{T}: 缺文件跳过'); continue
    j = json.load(open(p, encoding='utf-8'))
    cs = panel[panel['date'] == T].copy()
    if len(cs) < 500:
        print(f'{T}: 面板无该截面, 跳过'); continue
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gated = cs[~cs['code'].astype(str).str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    for tag in ('short', 'mid'):
        gated['prob'] = bst[tag].predict(gated[FEATURES])
        top = gated.sort_values(['prob', 'turnover20'], ascending=False).head(PORTFOLIO_TOPN)
        old_keys = list(j['models'][tag].keys())
        j['models'][tag] = {'topn': PORTFOLIO_TOPN,
                            'topn_list': [{'code': r.code, '行业': r.L1, '板块': r.board,
                                           '收盘': float(r.close), '分数': float(r.prob)}
                                          for r in top.itertuples()],
                            'format_migrated': f'20260913 由{old_keys}统一为topn_list(Top500)'}
    json.dump(j, open(p, 'w', encoding='utf-8'), ensure_ascii=False, default=float, indent=1)
    print(f"{T}: 统一完成 (short/mid 各{PORTFOLIO_TOPN}只)")
print('历史json格式统一完毕 — feed 双读分支退出条件达成(feed清理后仍兼容)')
