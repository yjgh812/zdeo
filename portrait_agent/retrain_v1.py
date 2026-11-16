# -*- coding: utf-8 -*-
"""§3.3 每20交易日滚动重训: 滑动3年窗口重训新版本→版本交叉验证→效果不低于上一版才切换(平滑上线), 多败保留旧版.
自检门槛: 距上次成功重训<20个交易日则跳过(幂等, 适配每日调度触发)."""
import os, sys, json, time, shutil
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
STATE_F = os.path.join(MODELS, 'retrain_state.json')
RETRAIN_EVERY_BARS = 20

def trade_dates_since(last_date):
    import sqlite3
    con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
    rows = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>? ORDER BY date", (last_date,))]
    con.close()
    return rows

def qfq_max_date():
    import sqlite3
    con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
    mx = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    con.close()
    return mx

def ensure_fresh_panel(log):
    """面板保鲜: cache落后于qfq最新日则重建(否则周一重训永远用冻结面板)."""
    import pandas as pd
    import feature_engine
    pkl = os.path.join(HERE, 'cache', 'panel_v1.pkl')
    panel = pd.read_pickle(pkl)
    mx = qfq_max_date()
    if str(panel['date'].max()) >= str(mx):
        log(f'面板缓存已新鲜({panel["date"].max()}), 直接复用')
        return panel
    log(f'面板缓存陈旧({panel["date"].max()} < {mx}), 重建...')
    panel = feature_engine.build()
    panel.to_pickle(pkl)
    return panel

def main():
    force = '--force' in sys.argv   # 手动强制重训(跳过20日门槛, 双门槛判定保留)
    state = {'last_retrain': None, 'versions': []}
    if os.path.exists(STATE_F):
        state = json.load(open(STATE_F, encoding='utf-8'))
    last_date = state.get('last_retrain') or qfq_max_date()   # 动态锚: 首跑以当前数据末端为基准
    n_new = len(trade_dates_since(last_date))
    if n_new < RETRAIN_EVERY_BARS and not force:
        print(f'[retrain] 距上次重训({last_date})仅{n_new}个新交易日 <{RETRAIN_EVERY_BARS}, 跳过(幂等)')
        return 0
    print(f'[retrain] {"--force 强制重训" if force else f"新交易日{n_new}>={RETRAIN_EVERY_BARS}"}, 开始滚动重训')
    import pandas as pd
    import model_v1
    panel = model_v1.make_labels(ensure_fresh_panel(log=print))
    ver = time.strftime('v1.%m%d')
    results = {}
    for tgt in ['y_short', 'y_mid']:
        tag = 'short' if tgt == 'y_short' else 'mid'
        res, m, te = model_v1.fit_and_eval(panel, tgt, prod_tag=None)  # 生产切换由本函数双门槛控制
        new_f = os.path.join(MODELS, f'{ver}_{tag}.txt')
        m.booster_.save_model(new_f)
        # 版本对比: 加载真实旧生产文件在同一测试段评估RankIC(不依赖state缓存数字, 防state空缺时default=新值自比恒真缺陷)
        old_f = os.path.join(MODELS, f'v1.0.0_{tag}.txt')
        old_ic = None
        if os.path.exists(old_f):
            import lightgbm as lgb
            from scipy.stats import spearmanr
            ob = lgb.Booster(model_file=old_f)
            te = te.copy()
            te['old_prob'] = ob.predict(te[model_v1.FEATURES].to_numpy(dtype='float32'))
            fwd_col = 'fwd5_high' if tgt == 'y_short' else 'fwd20_close'
            _ics = []
            for _, g in te.groupby('date'):
                g2 = g[g[fwd_col].notna()]
                if len(g2) > 200:
                    _ic = spearmanr(g2['old_prob'], g2[fwd_col]).statistic
                    if not np.isnan(_ic): _ics.append(_ic)
            old_ic = float(np.mean(_ics)) if _ics else None
        if old_ic is None:
            old_ic = res['rankIC_mean']   # 无旧模型时(首装)以新值为基准
        # 双门槛切换: 新版不劣于旧版95% AND 新版自身过RankIC阈(0.03)——防KPI不过的弱模型
        # 经copyfile绕过fit_and_eval的'KPI全过才写生产'保护(mid 0.0092被切换的洞, 2026-09-05实测)
        ok = (res['rankIC_mean'] >= old_ic * 0.95) and (res['rankIC_mean'] >= 0.03)
        results[tag] = {'rankIC': res['rankIC_mean'], 'old_rankIC': old_ic, 'top20': res['top20_hit5_mean'],
                        'auc_gap': res['auc_gap'], 'switch': ok}
        if ok:
            shutil.copyfile(new_f, old_f)      # 平滑切换: 生产名指向新版, 旧版内容被新版覆盖(版本文件保留)
            print(f'  {tag}: RankIC={res["rankIC_mean"]:.4f} vs 旧文件实测{old_ic:.4f} => 切换上线')
        else:
            os.remove(new_f)
            print(f'  {tag}: RankIC={res["rankIC_mean"]:.4f} vs 旧文件实测{old_ic:.4f} => 废弃新版保留旧版')
    state = {'last_retrain': time.strftime('%Y%m%d'), 'versions': state['versions'] + [
        {'ver': ver, 'ts': time.strftime('%Y-%m-%d %H:%M'), 'metrics': results}],
        'prev_metrics': {t: {'rankIC_mean': r['rankIC']} for t, r in results.items()}}
    json.dump(state, open(STATE_F, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[retrain] done, state saved')
    return 0

if __name__ == '__main__':
    sys.exit(main())
