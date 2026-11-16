# -*- coding: utf-8 -*-
"""影子期灰度递进执行器(计划书§2.4) · 口径v2(20260913主人批准迭代):
影子(0%)→连续5截面 pass→低权重(15%) →低权重10截面 超额≥2%+MDD≥-5%→标准(30%).
评价口径v2=可实现口径: entry=T后第2根bar开盘(名单最早T+1 15:16发布→T+2开盘可执行), exit=T后第6根bar收盘(5日持有),
真实等权(不clip). 判据 pass = port5>base5 且 rankIC≥0.03(hit绝对门槛废除: 20260913标定证明其只衡量市场方向,
24截面 hit中位50% vs base51%; 标定见 data/_shadow_calib_realizable_20260913.json).
v1口径(fwd5_high机会面)已废弃: 训练标签摸高概率≠买入持有收益, 18条旧history全部标 metric=fwd5_high 隔离.
状态落 models/shadow_state.json, 升降级发布C2.
用法: python shadow_gate.py --replay N   # 回放最近N个可评估截面(qfq窗口已齐)
      python shadow_gate.py --daily      # 补齐全部窗口已齐未评截面并递进判定(挂每日调度)"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
import sqlite3
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
STATE_F = os.path.join(MODELS, 'shadow_state.json')
FEATURES_PATH = os.path.join(HERE, 'cache', 'panel_v1.pkl')
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
from model_v1 import FEATURES, PUBLISH_GATE, PORTFOLIO_TOPN

IC_TH = 0.03                        # 排序能力门槛(v1口径同值沿用)
LOW_DAYS, STD_DAYS = 5, 10          # 影子→15% 需连续5截面; 15%→30% 需连续10截面
ENTRY_LAG, HOLD_BARS = 2, 5         # entry=T后第2根bar开盘; exit=T后第(2+5-1)=6根bar收盘
METRIC_V2 = 'realizable'
METRIC_V1 = 'fwd5_high'
# 名单宽度(2026-09-13 主人裁决"放宽到Top500"): 评价/发布共用 model_v1.PORTFOLIO_TOPN 单一真源;
# 20260913 Top20形态诊断实锤窄边界任意(第20/21名分差中位0.00023/并列组最大112只)
TOP_N = PORTFOLIO_TOPN
# 评价线(20260913新增mid): 两模型同截面各评一条 history(date×tag);
# 20260913标定24截面(Top20口径): mid可实现超额中位+1.05pp/IC+0.0662 vs short -0.49pp/-0.055(每状态分层mid均≥short);
# 递进判定仍走模块单线(advance内tag=='short')——模型分工变更(如mid升主力)属产品决策待主人裁决
EVAL_TAGS = ('short', 'mid')


def _qfq_con():
    return sqlite3.connect(QFQ_URI, uri=True, timeout=60)


def _qfq_max_date(con):
    return str(con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0])


def _qfq_dates_after(con, T):
    """T之后已有交易日列表(用于窗口齐备判定)"""
    rows = con.execute('SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>? ORDER BY date', (str(T),)).fetchall()
    return [str(r[0]) for r in rows]


def _qfq_all_dates(con):
    """全部交易日列表(一次查询, 调用方自行二分计数, 避免逐截面全表扫)"""
    rows = con.execute('SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date').fetchall()
    return [str(r[0]) for r in rows]


def _bars_after_count(all_dates, T):
    """T之后的交易日数(基于全量交易日列表)"""
    import bisect
    i = bisect.bisect_right(all_dates, str(T))
    return len(all_dates) - i


def _load_bars(con, codes, d0, d1):
    out = []
    cs = sorted(codes)
    for i in range(0, len(cs), 900):
        chunk = cs[i:i+900]
        ph = ','.join('?' * len(chunk))
        out.append(pd.read_sql_query(
            f'SELECT code,date,open,close FROM stock_daily_meta_qfq '
            f"WHERE code IN ({ph}) AND date>='{d0}' AND date<='{d1}' ORDER BY code,date", con, params=chunk))
    df = pd.concat(out, ignore_index=True)
    df['date'] = df['date'].astype(str)
    return {c: g.reset_index(drop=True) for c, g in df.groupby('code')}


def _realizable(bars, c, T):
    """可实现收益: entry=T后第ENTRY_LAG根bar开盘, exit=T后第ENTRY_LAG+HOLD_BARS-1根bar收盘; 窗口不足/停牌→NaN"""
    g = bars.get(c)
    if g is None:
        return np.nan
    idx = g.index[g['date'] > str(T)]
    if len(idx) < ENTRY_LAG + HOLD_BARS - 1:
        return np.nan
    e, x = g.loc[idx[ENTRY_LAG - 1]], g.loc[idx[ENTRY_LAG + HOLD_BARS - 2]]
    if not e['open'] or not x['close']:
        return np.nan
    return x['close'] / e['open'] - 1


def eval_section(panel, T, tag='short', con=None):
    """截面T(口径v2): 模型打分→发布闸→TopN(PORTFOLIO_TOPN=500)→可实现口径 hit/port5/base5/rankIC.
    面板缺该截面特征→None(等infer刷新); qfq窗口未齐→None."""
    own = con is None
    con = con or _qfq_con()
    try:
        dates_after = _qfq_dates_after(con, T)
        if len(dates_after) < ENTRY_LAG + HOLD_BARS - 1:
            return None
        cs = panel[panel['date'].astype(str) == T].copy()
        if len(cs) < 500:
            return None
        mp = os.path.join(MODELS, f'v1.0.0_{tag}.txt')
        if not os.path.exists(mp):
            return None
        bst = lgb.Booster(model_file=mp)
        cs = cs[cs['close'].notna() & (~cs['lim_today'])]
        cs = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
                & (cs['close'] >= PUBLISH_GATE['min_close'])]
        cs['prob'] = bst.predict(cs[FEATURES])
        top = cs.sort_values(['prob', 'turnover20'], ascending=False).head(TOP_N)
        bars = _load_bars(con, cs['code'].tolist(), str(T), dates_after[-1])
        pool_map = {c: _realizable(bars, c, T) for c in cs['code']}
        pool = np.array([v for v in pool_map.values() if not np.isnan(v)])
        tret = np.array([pool_map.get(c, np.nan) for c in top['code']], dtype=float)
        tret = tret[~np.isnan(tret)]
        if len(pool) < 500 or len(tret) < TOP_N * 0.75:
            return None
        pairs = [(p, pool_map[c]) for c, p in zip(cs['code'], cs['prob'])
                 if not np.isnan(pool_map.get(c, np.nan))]
        ic = float(spearmanr([x[0] for x in pairs], [x[1] for x in pairs]).statistic)
        hit = float((tret > 0).mean() * 100)
        port = float(tret.mean())
        base = float(pool.mean())
        return {'date': T, 'tag': tag, 'metric': METRIC_V2, 'topn': TOP_N,
                'topn_hit': round(hit, 1),
                'rankIC': round(ic, 4),
                'port5': round(port * 100, 2), 'base5': round(base * 100, 2),
                'excess': round((port - base) * 100, 2),
                'pass': bool(port > base and ic >= IC_TH)}
    finally:
        if own:
            con.close()


def load_state():
    if os.path.exists(STATE_F):
        return json.load(open(STATE_F, encoding='utf-8'))
    return {'phase': 'shadow', 'weight': 0.0, 'history': [], 'streak': 0,
            'low_phase_start': None, 'low_nav': [], 'activated': time.strftime('%Y-%m-%d %H:%M')}


def migrate_state(state):
    """迁移(一次性):
    ①口径v1→v2: 旧history标 metric=fwd5_high 隔离;
    ②宽度20→500(20260913主人裁决): v2且无topn的旧记录补 topn=20 标注(留痕不参与新判定).
    streak归零; low_nav清空(按新口径重新积累). phase维持low: 升降级只由新口径未来截面判定."""
    msg = None
    if state.get('metric_version') != METRIC_V2:
        n_old = 0
        for h in state.get('history', []):
            if 'metric' not in h:
                h['metric'] = METRIC_V1
                n_old += 1
        state['metric_version'] = METRIC_V2
        state['streak'] = 0
        state['low_nav'] = []
        state['migrated_at'] = time.strftime('%Y-%m-%d %H:%M')
        msg = f'口径v2迁移: {n_old}条旧口径history已隔离(fwd5_high), streak归零, 低权重期10日窗口重新积累'
    n_w = 0
    for h in state.get('history', []):
        if h.get('metric') == METRIC_V2 and 'topn' not in h:
            h['topn'] = 20
            n_w += 1
    if n_w:
        state['topn_version'] = TOP_N
        state['streak'] = 0
        state['low_nav'] = []
        msg = (msg or '') + f' | 宽度迁移: {n_w}条Top20口径记录标topn=20隔离, 新判定按Top{TOP_N}重新积累'
    return msg


def advance(state):
    """按history尾部连续达标数推进phase(口径v2 + Top500宽度判据)."""
    hist = state['history']
    def _in_scope(h):
        return h.get('metric') == METRIC_V2 and h.get('topn') == TOP_N
    if state['phase'] == 'shadow':
        streak = 0
        for h in reversed(hist):
            if not _in_scope(h):
                break
            if h['pass']:
                streak += 1
            else:
                break
        state['streak'] = streak
        if streak >= LOW_DAYS:
            state['phase'], state['weight'] = 'low', 0.15
            state['low_phase_start'] = hist[-1]['date']
            state['low_nav'] = [1.0]
            return f'🔥影子期达标(连续{streak}截面pass) → 升级低权重期15%'
    elif state['phase'] == 'low':
        seg = [h for h in hist
               if _in_scope(h)
               and h['date'] >= (state.get('low_phase_start') or '')
               and h.get('tag') == 'short']
        if len(seg) >= STD_DAYS:
            nav = np.cumprod([1 + h['port5'] / 100 for h in seg])
            bnav = np.cumprod([1 + h['base5'] / 100 for h in seg])
            cum_ex = nav[-1] / bnav[-1] - 1
            mdd = float((nav / np.maximum.accumulate(nav) - 1).min())
            if cum_ex >= 0.02 and mdd >= -0.05:
                state['phase'], state['weight'] = 'standard', 0.30
                return f'🔥低权重期达标(累计超额{cum_ex*100:.1f}%/MDD{mdd*100:.1f}%) → 升级标准期30%'
            else:
                state['phase'], state['weight'], state['streak'] = 'shadow', 0.0, 0
                state['low_nav'] = []
                return f'⚠️低权重期未达标(超额{cum_ex*100:.1f}%/MDD{mdd*100:.1f}%) → 回退影子期'
    return None


def main():
    replay_n = 0
    if '--replay' in sys.argv:
        replay_n = int(sys.argv[sys.argv.index('--replay') + 1])
    panel = pd.read_pickle(FEATURES_PATH)
    state = load_state()
    mig_msg = migrate_state(state)
    if mig_msg:
        print(mig_msg)
    panel_dates = sorted({str(d) for d in panel['date'].unique()})
    con = _qfq_con()
    # B-M2递进冻结告警(20260910): 面板最新截面落后qfq≥3日历时输出WARN(不自愈不阻断, 仅可见性)
    try:
        _qmax = _qfq_max_date(con)
        _pnew = panel_dates[-1] if panel_dates else ''
        if _pnew.isdigit() and _qmax.isdigit() and int(_qmax) - int(_pnew) >= 3:
            print(f"[shadow_gate][WARN] 面板最新截面 {_pnew} 落后 qfq {_qmax} ≥3日历日, "
                  f"新截面未入面板——检查 portrait_infer(22:50) 是否正常")
    except Exception as e:
        print(f"[shadow_gate][WARN] qfq 新鲜度检查失败: {type(e).__name__}: {e}")
    try:
        # 可评估截面 = qfq窗口已齐 且 面板有截面特征 且 未入history(date×tag×topn)
        # --daily=只评"最近30个可评截面"内的未评项(上界语义=范围上限, 防无限回补历史);
        # --replay N=最近N个(含已评, 重放场景)
        limit = replay_n if replay_n else 30
        done = {(h['date'], h.get('tag', 'short')) for h in state['history']
                if h.get('metric') == METRIC_V2 and h.get('topn') == TOP_N}
        all_dates = _qfq_all_dates(con)
        avail = [T for T in reversed(panel_dates)
                 if _bars_after_count(all_dates, T) >= ENTRY_LAG + HOLD_BARS - 1][:limit]
        if replay_n:
            cands = sorted(avail)
        else:
            cands = sorted(T for T in avail if any((T, tg) not in done for tg in EVAL_TAGS))
        new = []
        for T in cands:
            for tag in EVAL_TAGS:
                if (T, tag) in done:
                    continue
                r = eval_section(panel, T, tag=tag, con=con)
                if r:
                    r['source'] = 'replay' if replay_n else 'daily'
                    new.append(r)
                    print(f"{T}[{r['tag']}] hit={r.get('topn_hit', r.get('top20_hit'))}% IC={r['rankIC']} "
                          f"port5={r['port5']}% vs base {r['base5']}% (超额{r['excess']}pp) => {'PASS' if r['pass'] else 'FAIL'}")
        if not new:
            print('无可评估新截面' + ('(窗口已齐截面均已在history, 幂等)' if '--daily' in sys.argv else ''))
        # history: 新旧口径/宽度/双线混合保存, 排序截尾240(容量足容旧留痕+新500口径)
        state['history'] = sorted(state['history'] + new,
                                  key=lambda h: (h['date'], h.get('metric', ''), h.get('tag', ''), h.get('topn', 0)))[-240:]
        msg = advance(state)
    finally:
        con.close()
    json.dump(state, open(STATE_F, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    from event_bus import publish
    publish('C2_PORTRAIT_RESULT', {'kind': 'shadow_gate', 'metric_version': state.get('metric_version'),
                                   'phase': state['phase'], 'weight': state['weight'],
                                   'streak': state['streak'], 'new_evals': new, 'event': msg or mig_msg})
    print(f"\n状态: phase={state['phase']} weight={state['weight']} 口径={state.get('metric_version')} "
          f"v2连续pass={state['streak']}")
    if msg:
        print(msg)


if __name__ == '__main__':
    main()
