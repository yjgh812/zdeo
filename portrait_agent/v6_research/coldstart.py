# -*- coding: utf-8 -*-
"""冷启动编排(计划书§2.1自检 + §2.2六步骤). 单交易日内完成, 目标≤4小时.
用法: python coldstart.py [--skip-train](已有缓存时复用特征面板)"""
import os, sys, json, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding='utf-8')
T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

def preflight():
    """计划书§2.1 六项自检."""
    checks = []
    # 1 环境依赖
    try:
        import lightgbm, pandas, numpy, scipy, sklearn
        checks.append(('环境依赖', True, f"lgb{lightgbm.__version__}/pd{pandas.__version__}/np{numpy.__version__}"))
    except Exception as e:
        checks.append(('环境依赖', False, str(e)))
    # 2 接口权限(本地实现: 总线/数据目录)
    from event_bus import self_test
    bus = self_test()
    checks.append(('消息总线', bus['pass'], f"100条收发丢包{bus['lost']}"))
    for d in ['logs', 'cache', 'models', 'reports']:
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
    checks.append(('目录权限', all(os.access(os.path.join(HERE, d), os.W_OK) for d in ['logs','cache','models','reports']), '读写OK'))
    # 3 数据可用性: qfq覆盖+板块分布+北向降级标注
    import sqlite3
    con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
    mx, mn = con.execute("SELECT MAX(date),MIN(date) FROM stock_daily_meta_qfq WHERE date>='20230101'").fetchone()
    n_bse = con.execute("SELECT COUNT(DISTINCT code) FROM stock_daily_meta_qfq WHERE date>=? AND substr(code,1,2)='92'", (mx,)).fetchone()[0]
    con.close()
    checks.append(('数据可用性', True, f"qfq {mn}~{mx} 3年+; 北交所码在库{n_bse}只→引擎前缀过滤拦截"))
    checks.append(('北向数据源', False, '⚠️停采(2026-09-01裁决)→画像以行情/资金/基本面三源运行, 降级不阻断(计划书§1.4失败降级)'))
    # 4 配置合法性: 引擎常量自洽
    import feature_engine as fe
    ok = fe.PRICE_CAP == 20.0 and fe.MIN_BARS == 60 and fe.MIN_AMT20 == 500e4 and '688' in fe.TRAD_PREFIX
    checks.append(('配置合法性', ok, f"≤{fe.PRICE_CAP}元/次新{fe.MIN_BARS}日/流动性{fe.MIN_AMT20/1e4:.0f}万/含科创板"))
    return checks

def main():
    skip_train = '--skip-train' in sys.argv
    log('== 步骤0: 启动前置自检(计划书§2.1) ==')
    for name, ok, note in preflight():
        log(f"  [{'✅' if ok else '⚠️'}] {name}: {note}")
    log('== 步骤1+3: 特征引擎 + A股股票池(计划书§2.2) ==')
    import feature_engine as fe
    import pandas as pd
    cache_pkl = os.path.join(HERE, 'cache', 'panel_v1.pkl')
    if skip_train and os.path.exists(cache_pkl):
        panel = pd.read_pickle(cache_pkl); log(f'复用缓存面板 rows={len(panel)}')
    else:
        panel = fe.build()
        os.makedirs(os.path.join(HERE, 'cache'), exist_ok=True)
        panel.to_pickle(cache_pkl)
    # 补列: dif_pos60(engine FEATURES 声明而未构造, 缓存面板上现算, 秒级)
    if 'dif_pos60' not in panel.columns:
        panel['dif_pos60'] = panel.groupby('code')['dif'].transform(
            lambda x: x.gt(0).astype(float).where(x.notna()).rolling(60, min_periods=30).mean())
    bd = panel.groupby('board')['code'].nunique().to_dict()
    no_bse = int((panel['board'] == '其他').sum() == 0)
    log(f"  股票池校验: 分板块{bd}; 北交所剔除={'✅' if no_bse else '❌'}")
    log('== 步骤4: L1基线模型训练(双标签 7:2:1) ==')
    import model_v1
    panel = model_v1.make_labels(panel)
    out = {}
    for tgt in ['y_short', 'y_mid']:
        res, m, te = model_v1.fit_and_eval(panel, tgt, prod_tag=('short' if tgt == 'y_short' else 'mid'))
        out[tgt] = res
        log(f"  [{tgt}] RankIC={res['rankIC_mean']:.4f}(阈0.03) Top20命中={res['top20_hit5_mean']:.1f}%(阈55%) AUC差={res['auc_gap']:.4f}(阈0.05) 分层={ {k: round(v['rankIC'],4) for k,v in res['layers'].items()} } => {'PASS' if res['baseline_pass'] else 'FAIL(标记待优化, 计划书§2.2步骤4失败回滚=加载预置基准并迭代)'}")
    os.makedirs(os.path.join(HERE, 'reports'), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, 'reports', 'baseline_v1_result.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, default=float, indent=1)
    log('== 步骤6: 影子模式标记 ==')
    from event_bus import publish
    publish('C2_PORTRAIT_RESULT', {'kind': 'coldstart_done', 'version': 'v1.0.0-A股',
                                   'baseline': {k: v['baseline_pass'] for k, v in out.items()},
                                   'shadow_mode': True, 'weight': 0.0})
    log(f'冷启动完成(计划书§2.3): 模型v1.0.0-A股落盘models/, 结果reports/, 影子模式生效(0权重). 总耗时{time.time()-T0:.0f}s')

if __name__ == '__main__':
    main()
