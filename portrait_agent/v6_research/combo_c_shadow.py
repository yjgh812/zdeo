# -*- coding: utf-8 -*-
"""组合C影子跟踪(2026-09-06 prereg_combo_c_v1 落地, 直跑制).

用法:
  python combo_c_shadow.py --signal   # 计算最新截面组合C信号名单→登记窗口(台账append)
  python combo_c_shadow.py --eval     # 评估到期窗口(T+20已过)→记录超额→台账更新
  python combo_c_shadow.py --status   # 台账汇总(prereg门槛进度)
产物: data/combo_c_shadow_ledger.jsonl(台账) + data/combo_c_signals_YYYYMMDD.json(每期名单)
依赖: 缠论全量批跑(~7分钟) + 形态/情绪特征(复用retrain_v4_full链路函数)
禁: 不动生产缓存(panel_v1.pkl), 不下单, 观察池直跑制(prereg boundary_LOCKED)
"""
import os, sys, json, time
import bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 2026-09-25迁v6_research后指向portrait_agent根(feature_engine/event_bus)
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-09-25 迁入v6_research后, cache仍在父目录 portrait_agent/cache
CACHE = os.path.join(os.path.dirname(HERE), 'cache')
DATA = r'D:\zcodesj\data'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
LEDGER = os.path.join(DATA, 'combo_c_shadow_ledger.jsonl')
POS4 = ['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold']
NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
        'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']
HOLD = 20

T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

def chan_signals_fresh(df):
    """缠论批跑(全序列, 复用chan_batch映射逻辑), 返回信号DataFrame."""
    from chanlun_engine.analyzer import ChanAnalyzer
    analyzer = ChanAnalyzer()
    rows = []
    for k, (code, g) in enumerate(df.groupby('code', sort=False)):
        if k % 1000 == 0: log(f'  缠论 {k}')
        try:
            r = analyzer.analyze(g.reset_index(drop=True))
            merged = r.merged
            fr = sorted([(f.price, f.kline_idx) for f in r.fractals], key=lambda x: x[1])
            pts = [(b.type, b.price) for b in r.buy_sell_points if b.type.startswith('buy')]
            pts += [('bottom_div', d.price) for d in r.divergences if d.direction == 'bottom']
            fi = 0; used = -1
            for typ, price in pts:
                j = max(fi, used + 1)
                while j < len(fr):
                    if fr[j][0] == price and fr[j][1] > used:
                        # v2确认日口径(P2审计修复, 与chan_batch.py同步): 第3根merged K收盘才确认
                        ci = fr[j][1] + 1
                        used = fr[j][1]; fi = j + 1
                        if ci < len(merged):
                            rows.append({'code': code, 'date': merged[ci].date, 'type': typ})
                        break
                    j += 1
        except Exception:
            continue
    return pd.DataFrame(rows)

def build_section(target=None):
    """构建最新截面组合C信号(全链路). target=YYYYMMDD 回扫历史截面(2026-09-19补:
    9/16 v6_research 迁移断链致 0915-0917 窗口漏登; df 预过滤后特征链只用截至target数据, 无前视)."""
    from morph_profile import load_pool, compute_patterns
    from retrain_v4_full import compute_new_patterns
    from feature_engine import TRAD_PREFIX
    df = load_pool()   # 已剔ST, 全池
    if target:
        df = df[df['date'].astype(str) <= str(target)]
        log(f'回扫模式: df 截止 {df["date"].max()} (target={target})')
    df = df.sort_values(['code', 'date']).reset_index(drop=True)
    log(f'行情 {len(df)} 只={df["code"].nunique()}')
    # 形态
    parts = []
    for k, (code, g) in enumerate(df.groupby('code', sort=False)):
        if k % 1000 == 0: log(f'  形态 {k}')
        p = compute_patterns(g); p['code'] = code; p['date'] = g['date'].values
        parts.append(p)
    pats = pd.concat(parts, ignore_index=True)
    # 缠论: 消费chan_batch产物chan_signals_v2.pkl(单一真源——复制粘贴双实现曾致754信号漂移, 2026-09-06修)
    # 口径切换点: 20260920 02:16起新引擎fe155a5(数据max=20260918); ≥20260921信号与之前影子台账须分段归因, 详见prereg_combo_c_obs_v2_20260906.json口径切换补记
    chan_fp = os.path.join(CACHE, 'chan_signals_v2.pkl')
    qfq_max = df['date'].max()
    if os.path.exists(chan_fp):
        chan = pd.read_pickle(chan_fp)
        if str(chan['date'].max()) >= str(qfq_max):
            log(f'缠论: 消费缓存 {os.path.basename(chan_fp)} ({len(chan)}信号, 截面{chan["date"].max()})')
        else:
            log(f'缠论缓存滞后({chan["date"].max()}<{qfq_max}), 内联批跑(建议重跑chan_batch.py)')
            chan = chan_signals_fresh(df)
    else:
        log('缠论缓存缺失, 内联批跑')
        chan = chan_signals_fresh(df)
    log(f'缠论信号 {len(chan)}')
    # 池过滤
    df['amt20_'] = df.groupby('code')['amount'].transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    bar = df.groupby('code')['code'].transform('size')
    df = df[(df['close'] <= 20.0) & (df['amt20_'] >= 500e4) & (bar >= 120)]
    last_date = df['date'].max()
    cs = df[df['date'] == last_date].copy()
    cs_codes = set(cs['code'])
    # 截面级特征(只用截至last_date的数据)
    pats_last = pats[(pats['date'] == last_date) & pats['code'].isin(cs_codes)]
    pats_int = pats.copy(); pats_int['dint'] = pats_int['date'].map(int)
    ld_int = int(last_date)
    # 交易日窗口(修复: 原日历差近似致rounded_20等长窗负过滤漏标, 2026-09-06复核轮bug)
    trade_dates = sorted(df['date'].unique())
    def recent_sig(col, ndays):
        win = set(trade_dates[-ndays:])   # 近ndays个交易日(与panel链路rolling口径一致)
        sub = pats_int[(pats_int[col] > 0) & (pats_int['date'].isin(win))]
        return set(sub['code'])
    sig_map = {
        'island_5': recent_sig('island_bottom', 5),
        'limit_break_5': recent_sig('limit_break', 5),
        'gap_up_run_5': recent_sig('gap_up_run', 5),
        'rounded_20': recent_sig('rounded_bottom', 20),
        'new_high_3': recent_sig('new_high250', 3),
    }
    # gap_up_hold 当日截面判定(属新特征族, 3日前向上跳空+近3日未回补): 近20日尾部计算
    r20 = df.groupby('code').tail(20)
    ph = r20.groupby('code')['high'].shift(1)
    lo_s = r20['low']
    gu = (lo_s > ph) & \
         (lo_s.groupby(r20['code']).shift(1) > ph.groupby(r20['code']).shift(1)) & \
         (lo_s.groupby(r20['code']).shift(2) > ph.groupby(r20['code']).shift(2)) & \
         (lo_s.groupby(r20['code']).shift(3) <= ph.groupby(r20['code']).shift(3))
    last_rows = r20[r20['date'] == last_date]
    gu_last = gu.reindex(last_rows.index).fillna(False)
    sig_map['gap_up_hold'] = set(last_rows.loc[gu_last, 'code'])
    sig_map['up4of5'] = set()  # 由K线判定(下方)
    # 缠论15个交易日内买点(修复: 原日历差近似)
    win15 = set(trade_dates[-15:])
    for t, col in [('buy1', 'chan_buy1_10'), ('buy2', 'chan_buy2_10')]:
        sub = chan[(chan['type'] == t) & (chan['date'].isin(win15))]
        sig_map[col] = set(sub['code'])
    # lhb 龙虎榜净买入5日内
    con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    # 2026-09-10 D1: 迁移 dragon_tiger(8/10 冻结) → dragon_tiger_new(日更), net_buy→net_amount
    lhb = pd.read_sql_query(
        "SELECT trade_date,ts_code,net_amount AS net_buy FROM dragon_tiger_new", con)
    con.close()
    lhb['net_buy'] = pd.to_numeric(lhb['net_buy'], errors='coerce')
    lhb['code'] = lhb['ts_code'].str[:6]; lhb['date'] = lhb['trade_date'].astype(str)
    lb = lhb[(lhb['net_buy'] > 0) & (lhb['date'].isin(set(trade_dates[-5:])))]
    sig_map['lhb_buy_5'] = set(lb['code'])
    # 当日截面判定: vwap乖离/涨停/相对强度
    o = df[df['date'] == last_date].copy()
    recent60 = df.groupby('code').tail(60)
    vwc_num = (recent60['close']*recent60['volume']).groupby(recent60['code']).sum()
    vwc_den = recent60['volume'].groupby(recent60['code']).sum()
    vwc60_last = (vwc_num/vwc_den).reindex(o['code']).to_numpy()
    close_arr = o['close'].astype(float).to_numpy()
    vw_hi = set(o.loc[(~np.isnan(vwc60_last)) & (close_arr > vwc60_last*1.10), 'code'])
    chg = df.groupby('code')['close'].pct_change()*100
    chg_last = chg[df['date'] == last_date]
    lim_up = set(o.loc[chg_last.reindex(o.index) >= 9.8, 'code'])
    # rel_ind: 超额(个股chg-行业L1日均chg)当日截面top10%, 对齐panel/data_enrich_v5口径
    # (2026-09-06修复: 原merge重置索引后reindex错位致过滤失效[3105/3106 NaN], 拉卡拉9/4违规入选实证)
    ind = pd.read_sql_query("SELECT code,industry FROM industry_map_ths",
                            sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True))
    ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
    o2 = o.copy()
    o2['chg'] = chg_last.reindex(o.index).values
    o2 = o2.merge(ind[['code', 'L1']].drop_duplicates('code'), on='code', how='left')
    o2['rel_excess'] = o2['chg'] - o2.groupby('L1')['chg'].transform('mean')
    o2['rel_hi'] = o2['rel_excess'].rank(pct=True) >= 0.90
    rel_hi = set(o2.loc[o2['rel_hi'].fillna(False), 'code'])
    # up4of5: 近5日≥4阳
    up5 = df[df['date'].map(int) >= int(last_date) - 10].copy()
    up5['yang'] = (up5['close'] > up5['open']).astype(int)
    ycnt = up5.groupby('code')['yang'].sum()
    up45 = set(ycnt[ycnt >= 4].index)
    sig_map['up4of5'] = up45
    sig_map['vwap60_bias'] = vw_hi
    sig_map['lim_today'] = lim_up
    sig_map['rel_ind_hi'] = rel_hi
    # 组合C
    pos_codes = sig_map['chan_buy2_10'] | sig_map['chan_buy1_10'] | sig_map['island_5'] | sig_map['gap_up_hold']
    neg = (sig_map['vwap60_bias'] | sig_map['limit_break_5'] | sig_map['gap_up_run_5'] |
           sig_map['rounded_20'] | sig_map['up4of5'] | sig_map['new_high_3'] |
           sig_map['lhb_buy_5'] | sig_map['rel_ind_hi'] | sig_map['lim_today'])
    sel = (pos_codes & cs_codes) - neg
    sel = sorted(sel)[:60]
    out = {'date': last_date, 'n_candidates': len(pos_codes & cs_codes),
           'n_selected': len(sel),
           'signals': {k: len(v & cs_codes) for k, v in sig_map.items()},
           'selected_codes': sel, 'ts': time.strftime('%Y-%m-%d %H:%M')}
    return out

def ledger_append(rec):
    with open(LEDGER, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=float) + '\n')

def ledger_supersede(date, ts):
    """(9/30守卫) 指定窗口+ts的window_register标superseded=True(整文件重写, 审计留痕).
    供陈旧登记修正用: 评估侧跳过superseded记录, 修正版继续被评估."""
    lines = [l for l in open(LEDGER, encoding='utf-8').read().splitlines() if l.strip()]
    out, hit = [], False
    for l in lines:
        r = json.loads(l)
        if (r.get('kind') == 'window_register' and r.get('date') == date
                and r.get('ts') == ts and not r.get('superseded')):
            r['superseded'] = True
            hit = True
        out.append(json.dumps(r, ensure_ascii=False, default=float))
    with open(LEDGER, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    return hit

def load_ledger():
    if not os.path.exists(LEDGER): return []
    return [json.loads(l) for l in open(LEDGER, encoding='utf-8') if l.strip()]

def main():
    if '--signal' in sys.argv:
        # 幂等: 同截面日已登记且codes一致→只更新名单不重复append;
        # codes不一致=陈旧登记修正(9/30实锤: 缠论缓存滞后致登记不完整)——未评估的老记录
        # 标superseded+追加修正版(审计留痕, 评估侧自动用新版); 已评估的老记录仅告警不自动改
        led = load_ledger()
        _t = None
        if '--date' in sys.argv:
            _t = sys.argv[sys.argv.index('--date') + 1]
        out = build_section(target=_t)
        fp = os.path.join(DATA, f'combo_c_signals_{out["date"]}.json')
        json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        active_prior = [r for r in led
                        if r.get('kind') == 'window_register' and r.get('date') == out['date']
                        and not r.get('superseded')]
        if active_prior:
            old = active_prior[-1]
            if set(old.get('codes') or []) == set(out['selected_codes']):
                log(f'窗口 {out["date"]} 已在台账(幂等跳过append); 名单已刷新→{os.path.basename(fp)}')
            elif old.get('evaluated'):
                log(f'⚠️ 窗口 {out["date"]} 重算codes与台账不一致且旧登记已评估 → 仅告警不自动改, 请人工核对')
            else:
                ledger_supersede(out['date'], old.get('ts'))
                ledger_append({'kind': 'window_register', 'date': out['date'],
                               'n_selected': out['n_selected'], 'codes': out['selected_codes'],
                               'ts': out['ts'], 'supersedes_ts': old.get('ts'),
                               'note': 'stale-cache correction'})
                log(f'窗口 {out["date"]} 登记修正: 旧{len(old.get("codes") or [])}只(ts={old.get("ts")})'
                    f'标superseded → 新{out["n_selected"]}只(修正版入账)')
        else:
            ledger_append({'kind': 'window_register', 'date': out['date'],
                           'n_selected': out['n_selected'], 'codes': out['selected_codes'],
                           'ts': out['ts']})
            log(f'登记窗口 {out["date"]}: 候选{out["n_candidates"]} 入选{out["n_selected"]} '
                f'名单→{os.path.basename(fp)}')
    elif '--explain' in sys.argv:
        # §6可解释性: 最新名单的每股驱动信号+排雷确认+关键状态(不重跑全链路)
        import glob as _glob
        sig_files = sorted(f for f in _glob.glob(os.path.join(DATA, 'combo_c_signals_*.json'))
                           if 'v2check' not in f and 'ind_' not in f)  # 只读正牌名单(排除临时/变体文件)
        if not sig_files:
            print('FAIL 无信号名单(先跑 --signal)'); sys.exit(2)
        sig = json.load(open(sig_files[-1], encoding='utf-8'))
        m = pd.read_pickle(os.path.join(CACHE, 'panel_v6_full.pkl'))  # v6=v5超集(+P0列+9/8新增行, 2026-09-09)
        last = m['date'].max()
        cs = m[m['date'] == last].set_index('code')
        # FRESHNESS: panel滞后qfq>5天WARN(重建=v4+enrich约15分钟)
        try:
            import sqlite3 as _sq, datetime as _dt
            _c = _sq.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
            _qmax = _c.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]; _c.close()
            _gap = (_dt.datetime.strptime(_qmax, '%Y%m%d') - _dt.datetime.strptime(str(last), '%Y%m%d')).days
            if _gap > 5:
                print(f'WARN panel截面{last}落后qfq{_qmax}约{_gap}天; 重建: python portrait_agent/v6_research/retrain_v4_full.py && python portrait_agent/data_enrich_v5.py')
        except Exception:
            pass
        # 名称
        import sqlite3 as _sq
        con = _sq.connect(f'file:{FUND_DB}?mode=ro', uri=True)
        ph = ','.join('?'*len(sig['selected_codes']))
        nm = dict(con.execute(f'SELECT code,name FROM industry_map_ths WHERE code IN ({ph})',
                              sig['selected_codes']).fetchall())
        con.close()
        POS_DESC = {'chan_buy2_10': '缠论二买(10日内)', 'chan_buy1_10': '缠论一买(10日内)',
                    'island_5': '岛型反转(5日内)', 'gap_up_hold': '向上跳空未回补'}
        print(f"组合C名单解释(截面{sig['date']}/panel截面{last}, {len(sig['selected_codes'])}只):")
        for code in sig['selected_codes']:
            r = cs.loc[code] if code in cs.index else None
            if r is None:
                print(f'  {code} {nm.get(code,"")}: 池外(N/A)'); continue
            if isinstance(r, pd.DataFrame): r = r.iloc[0]
            drv = [POS_DESC[k] for k in ('chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold')
                   if pd.notna(r.get(k)) and r[k] > 0]
            drv_s = '+'.join(drv) if drv else '事件标记已衰减(名单残留)'
            risk_trg = sum(1 for f_ in ['lhb_buy_5', 'limit_break_5', 'new_high_3', 'vwap60_bias',
                                        'vwap20_bias', 'gap_up_run_5', 'chan_buy3_10',
                                        'new_high20', 'macd_golden_hi', 'rounded_20',
                                        'red_3_soldiers', 'higher_low']
                           if pd.notna(r.get(f_)) and r[f_] > 0.5)
            pos250 = r.get('pos250')
            pos_s = f'250日位置{pos250*100:.0f}%' if pd.notna(pos250) else ''
            print(f'  {code} {nm.get(code,"")}: 驱动={drv_s}; 排雷检查={risk_trg}触发; '
                  f'收盘{r["close"]:.2f} {pos_s}')
    elif '--eval' in sys.argv:
        led = load_ledger()
        qfq = sqlite3.connect(
            r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
        dates_all = [r[0] for r in qfq.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
        for rec in led:
            # 评估对象=未作废的窗口登记(v1/v2/v2_fix, superseded者跳过; 2026-09-06修:
            # 原仅匹配window_register会评估已作废v1且跳过权威v2修正版)
            if (not str(rec.get('kind', '')).startswith('window_register')) \
                    or rec.get('evaluated') or rec.get('superseded'):
                continue
            i = bisect.bisect_left(dates_all, rec['date'])
            j = i + HOLD
            if j >= len(dates_all): continue   # 未到期
            d_end = dates_all[j]
            # 组合收益: 等权 fwd20_close
            ph = ','.join('?'*len(rec['codes']))
            closes = pd.read_sql_query(
                f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) "
                f"AND date IN (?,?)", qfq, params=rec['codes'] + [rec['date'], d_end])
            piv = closes.pivot(index='code', columns='date', values='close')
            if rec['date'] not in piv.columns or d_end not in piv.columns: continue
            r = (piv[d_end] / piv[rec['date']] - 1).dropna()
            # 全池基准
            base = pd.read_sql_query(
                "SELECT code,close FROM stock_daily_meta_qfq WHERE date=? AND close<=20",
                qfq, params=[d_end])
            base0 = pd.read_sql_query(
                "SELECT code,close FROM stock_daily_meta_qfq WHERE date=? AND close<=20",
                qfq, params=[rec['date']])
            b0 = base0['code'].isin(base['code'])
            # 基准=截面等权(近似: 当日<=20元池)
            fwd_pool = pd.read_sql_query(
                f"SELECT a.code, a.close/b.close-1 AS fwd FROM stock_daily_meta_qfq a "
                f"JOIN stock_daily_meta_qfq b ON a.code=b.code AND b.date=? "
                f"WHERE a.date=? AND b.close<=20", qfq, params=[rec['date'], d_end])
            pr = float(r.mean()); br = float(fwd_pool['fwd'].mean())
            rec['evaluated'] = True
            rec.update({'eval_date': d_end, 'port_ret': round(pr*100, 3),
                        'base_ret': round(br*100, 3), 'excess': round((pr-br)*100, 3),
                        'cost_adj_excess': round((pr-br)*100 - 0.3, 3), 'n_hit': len(r)})
            log(f"窗口{rec['date']}→{d_end}: port={pr*100:.2f}% base={br*100:.2f}% "
                f"ex={(pr-br)*100:.2f}% n={len(r)}")
        qfq.close()
        with open(LEDGER, 'w', encoding='utf-8') as f:
            for rec in led:
                f.write(json.dumps(rec, ensure_ascii=False, default=float) + '\n')
        log(f'台账更新 {LEDGER}')
    elif '--status' in sys.argv:
        led = load_ledger()
        done = [r for r in led if r.get('evaluated')]
        pend = [r for r in led if str(r.get('kind', '')).startswith('window_register')
                and not r.get('evaluated') and not r.get('superseded')]
        exs = [r['excess'] for r in done]
        t_val = float(np.mean(exs)/(np.std(exs)/np.sqrt(len(exs)))) if len(exs) > 2 and np.std(exs) > 0 else None
        cum = float(np.prod([1 + e/100 for e in exs]) - 1) if exs else None
        print(json.dumps({'登记窗口': len(pend) + len(done), '已评估': len(done),
                          '待到期': len(pend),
                          '累计超额%': round(cum*100, 2) if cum is not None else None,
                          '平均超额%': round(float(np.mean(exs)), 3) if exs else None,
                          't': round(t_val, 2) if t_val else None,
                          '门槛(10窗口/超额3%/t2)': '见 data/prereg_combo_c_v1_20260906.json'},
                         ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
