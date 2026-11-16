# -*- coding: utf-8 -*-
"""每日画像推理入口(计划书§3.1每日闭环): 加载L1模型对最新截面打分→TopN(500)→C2发布→T-1命中率复盘.
数据幂等: qfq无今日bar则用最新可用日并标注滞后.
20260913迭代(主人批准): ①发布闸(剔科创板/close<4元, 适用TopN名单), 宪法§0边界, 常量=model_v1.PUBLISH_GATE单一真源)
②分数并列次级排序键turnover20(0909截面曾一分7只, nlargest并列序不稳定)
③无新截面skip: qfq MAX==上次infer截面时跳过(不刷新panel不重发, 防白跑掩盖面板真实新鲜度)
④截面lag告警: 截面落后qfq≥1日WARN."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
import sqlite3
import feature_engine as fe
from event_bus import publish
from model_v1 import FEATURES, PUBLISH_GATE, PORTFOLIO_TOPN

HERE = os.path.dirname(os.path.abspath(__file__))
LAST_SECTION_F = os.path.join(HERE, 'reports', 'last_infer_section.txt')
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

def _qfq_max_date():
    con = sqlite3.connect(QFQ_URI, uri=True, timeout=30)
    try:
        return str(con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0])
    finally:
        con.close()

def infer(force=False):
    qfq_max = _qfq_max_date()
    last = open(LAST_SECTION_F, encoding='utf-8').read().strip() if os.path.exists(LAST_SECTION_F) else ''
    if qfq_max == last and not force:
        log(f'SKIP: qfq MAX={qfq_max} 与上次infer截面相同, 无新bar(非交易日/日更未完成), '
            f'不刷新panel不重发名单——若预期有新bar请检查qfq日更链')
        return None
    if force and qfq_max == last:
        log(f'--force: 同截面({qfq_max})强制重发(发布层变更场景, 幂等覆盖)')
    # 最新截面特征: warm与训练口径一致(20230101, 消除maxstreak等窗口累积特征的train/serve偏斜)
    panel_last = fe.build()
    if 'dif_pos60' not in panel_last.columns:   # 兼容旧缓存面板(engine已原生构造)
        panel_last['dif_pos60'] = panel_last.groupby('code')['dif'].transform(
            lambda x: x.gt(0).astype(float).where(x.notna()).rolling(60, min_periods=30).mean())
    # 面板落盘保鲜: shadow_gate(每日23:10)/retrain_v1(周一05:30)共享本缓存, 唯一刷新方=每日推理
    panel_last.to_pickle(os.path.join(HERE, 'cache', 'panel_v1.pkl'))
    last_date = str(panel_last['date'].max())
    if last_date < qfq_max:
        log(f'WARN: 本次截面 {last_date} 落后 qfq MAX {qfq_max} ≥1日, 名单基于滞后截面(qfq日更未覆盖该bar)')
    cs = panel_last[panel_last['date'] == last_date].copy()
    log(f'截面 {last_date} n={len(cs)} 板块={cs.groupby("board")["code"].nunique().to_dict()}')
    out = {'date': last_date, 'n': int(len(cs)), 'models': {}}
    for tag, tgt in [('short', 'y_short'), ('mid', 'y_mid')]:
        mp = os.path.join(HERE, 'models', f'v1.0.0_{tag}.txt')
        if not os.path.exists(mp):
            out['models'][tag] = '模型缺失'; continue
        bst = lgb.Booster(model_file=mp)
        cs['prob'] = bst.predict(cs[FEATURES])
        # 发布闸+次级排序键: 名单层执行产品边界(训练面不动); 分数并列按turnover20降序稳定排序
        gated = cs[~cs['code'].astype(str).str.startswith(PUBLISH_GATE['excluded_prefixes'])
                   & (cs['close'] >= PUBLISH_GATE['min_close'])]
        top = gated.sort_values(['prob', 'turnover20'], ascending=False).head(PORTFOLIO_TOPN)
        out['models'][tag] = {'topn': PORTFOLIO_TOPN,
                              'topn_list': [{'code': r.code, '行业': r.L1, '板块': r.board,
                                             '收盘': float(r.close), '分数': float(r.prob)}
                                            for r in top.itertuples()]}
        # 全截面分数导出(屠龙对接原料: portrait_feed/AB台账按此重排序; 2026-09-05对接接线; 不含发布闸=全池分数)
        cs[['code', 'prob']].to_csv(
            os.path.join(HERE, 'reports', f'portrait_scores_{last_date}_{tag}.csv'), index=False)
    # C2发布(影子模式, 权重0, 计划书§2.2步骤6)
    publish('C2_PORTRAIT_RESULT', {'kind': 'daily_portrait', **out, 'shadow': True, 'weight': 0.0})
    json.dump(out, open(os.path.join(HERE, 'reports', f'portrait_{last_date}.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, default=float, indent=1)
    with open(LAST_SECTION_F, 'w', encoding='utf-8') as f:
        f.write(last_date)
    log(f'已发布C2并落盘 reports/portrait_{last_date}.json; 面板缓存已刷新(cache/panel_v1.pkl)')
    return out

# 效果复盘语义 owner = shadow_gate(history含每截面Top500命中/RankIC/port5, --daily/--replay), 此处不再重复实现

if __name__ == '__main__':
    infer(force='--force' in sys.argv)
