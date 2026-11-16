# -*- coding: utf-8 -*-
"""0910截面名单闸后刷新(一次性, 主人「能现在执行的就现在干」): 0910截面portrait_20260910.json系旧代码生成(含科创板688660+<4元),
用当前PUBLISH_GATE+次级排序键对同截面重新打分发布(分数语义不变=同一模型同截面特征, 仅发布层执行产品边界),
使9/14可执行名单为闸后版本. 原11只被挤出/补入差异打印留痕. 不触碰0911截面(已是闸后生成)."""
import os, sys, json
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE

HERE = r'D:\zcodesj\portrait_agent'
T = '20260910'
panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v1.pkl'))
cs = panel[panel['date'].astype(str) == T].copy()
assert len(cs) > 500, f'面板缺{T}截面'
out_old = json.load(open(os.path.join(HERE, 'reports', f'portrait_{T}.json'), encoding='utf-8'))

for tag in ('short', 'mid'):
    mp = os.path.join(HERE, 'models', f'v1.0.0_{tag}.txt')
    bst = lgb.Booster(model_file=mp)
    cs['prob'] = bst.predict(cs[FEATURES])
    gated = cs[~cs['code'].astype(str).str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])]
    top = gated.sort_values(['prob', 'turnover20'], ascending=False).head(20)
    new_codes = [r.code for r in top.itertuples()]
    old_codes = [it['code'] for it in out_old['models'][tag]['top20']]
    out_old['models'][tag]['top20'] = [{'code': r.code, '行业': r.L1, '板块': r.board,
                                        '收盘': float(r.close), '分数': float(r.prob)}
                                       for r in top.itertuples()]
    out_old['models'][tag]['gate_refresh'] = '20260913 PUBLISH_GATE闸后刷新'
    print(f"[{tag}] 挤出: {sorted(set(old_codes) - set(new_codes))}")
    print(f"[{tag}] 补入: {sorted(set(new_codes) - set(old_codes))}")
    print(f"[{tag}] 保留: {len(set(old_codes) & set(new_codes))}/20")

json.dump(out_old, open(os.path.join(HERE, 'reports', f'portrait_{T}.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, default=float, indent=1)
print(f'已刷新 reports/portrait_{T}.json (n={out_old["n"]})')
