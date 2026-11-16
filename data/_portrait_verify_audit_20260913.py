# -*- coding: utf-8 -*-
"""独立对拍(只用SQL+手算, 不复用任何验证脚本函数): 抽20260602截面, 手工复算 short Top20 h5 与池基准, 与construct_verify JSON对账."""
import sys, json, sqlite3
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE

T = '20260602'
panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
cs = panel[panel['date'] == T].copy()
cs = cs[cs['close'].notna() & (~cs['lim_today'])]
gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes']) & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
print(f'截面 {T}: 闸前{len(cs)} 闸后{len(gate)}')

bst = lgb.Booster(model_file=r'D:\zcodesj\portrait_agent\models\v1.0.0_short.txt')
gate['prob'] = bst.predict(gate[FEATURES])
top = gate.sort_values(['prob', 'turnover20'], ascending=False).head(20)
top_codes = list(top['code'])

# --- 纯SQL手算 h5: entry=T后第2根bar开盘, exit=T后第6根bar收盘 ---
con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True, timeout=60)
def manual_ret(code):
    rows = con.execute("SELECT date,open,close FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date LIMIT 6",
                       (code, T)).fetchall()
    if len(rows) < 6: return None
    e_open = rows[1][1]; x_close = rows[5][2]
    if not e_open or not x_close: return None
    return x_close / e_open - 1

top_rets = [manual_ret(c) for c in top_codes]
top_rets = [r for r in top_rets if r is not None]
my_top20_h5 = float(np.mean(top_rets) * 100)
manual_all = [manual_ret(c) for c in gate['code']]
manual_all = [r for r in manual_all if r is not None]
my_base_h5 = float(np.mean(manual_all) * 100)

# --- 对账 ---
recs = json.load(open(r'D:\zcodesj\data\_portrait_construct_verify_20260913.json', encoding='utf-8'))
rec = next(r for r in recs if r['date'] == T)
print(f"\n对拍截面 {T}:")
print(f"  short Top20 h5:  手算={my_top20_h5:+.4f}%  脚本={rec['short_top20_h5']:+.4f}%  差={abs(my_top20_h5-rec['short_top20_h5']):.4f}pp")
print(f"  池基准 h5:       手算={my_base_h5:+.4f}%  脚本={rec['base']['5']:+.4f}%  差={abs(my_base_h5-rec['base']['5']):.4f}pp")
print(f"  超额:            手算={my_top20_h5-my_base_h5:+.4f}pp  脚本={rec['short_top20_h5']-rec['base']['5']:+.4f}pp")

# Top20 名单一致性(前5只)
print(f"\n  Top20前5: {top_codes[:5]}")
# 十分位边界抽验: 最高分1只所在分位(D1)的 h5 手算 vs 脚本
q = np.argsort(np.argsort(-gate['prob'].values))
n = len(gate)
d1_members = gate['code'].values[(q >= 0) & (q < n / 10)]
d1_rets = [manual_ret(c) for c in d1_members]
d1_rets = [r for r in d1_rets if r is not None]
my_d1 = float(np.mean(d1_rets) * 100)
print(f"  D1(最高分位) h5: 手算={my_d1:+.4f}%  脚本={rec['short_dec_h5']['1']:+.4f}%  差={abs(my_d1-rec['short_dec_h5']['1']):.4f}pp  (n={len(d1_rets)})")
con.close()

ok = (abs(my_top20_h5 - rec['short_top20_h5']) < 0.01 and abs(my_base_h5 - rec['base']['5']) < 0.01
      and abs(my_d1 - rec['short_dec_h5']['1']) < 0.01)
print(f"\n对拍结论: {'✅ 分毫一致(容差0.01pp)' if ok else '❌ 存在差异需排查'}")
