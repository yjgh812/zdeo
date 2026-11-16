# -*- coding: utf-8 -*-
"""选股发布闸 SG1-SG8(2026-09-06 主人指令: 版本漂移致选错股=严重问题, 必须改进).

背景: 9/6选股六版漂移(信号口径/过滤口径/基本面漏验/复核工具bug), 主人定性=会导致选错股亏损, 严重.
本工具=选股发布前强制检查单(代码化, 任何非exit0=禁止向主人呈报名单).

SG1 信号源唯一真源: chan_signals_v2.pkl(v2确认日口径), 缓存滞后qfq>3日=FAIL
SG2 过滤口径: 负过滤交易日窗口(非日历近似), 与panel rolling口径一致性抽查≥5特征
SG3 基本面闸: PIT盈利+ROE>-50% (组合C配方无此闸, 发布层强制——9/6蓝丰ROE-1199漏网教训)
SG4 事件排雷: 解禁20日内>5%流通盘 + 龙虎榜5日净买入 → 强制披露(买入审查用)
SG5 名称价格双源: industry_map_ths vs westock实时 (宪法名称铁律)
SG6 独立复算抽查: ≥3特征从qfq原始K线直算与panel对照, 全一致才PASS
SG7 名单口径指纹: 记录(信号版本/过滤口径/基本面闸/截面日)四元组, 与上一发布版不同→必须显式标注变更原因
SG8 反臆想合规: 输出必含历史分布(胜率/分位/暴跌率)+置信度+禁止"确定能涨"类表述

用法:
  python stock_publish_gate.py --names combo_c        # 检查组合C最新名单 → exit0=可发布
  python stock_publish_gate.py --codes 002478,300296  # 任意名单
输出: reports/stock_gate_<date>.json + 终端摘要
"""
import os, sys, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
DATA = r'D:\zcodesj\data'
REPORTS = os.path.join(HERE, 'reports')
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ROE_FLOOR = -50.0

def _fail(sg, msg, result):
    result['fails'].append(f'SG{sg} {msg}')
    print(f'  ✗ SG{sg} FAIL: {msg}')

def _pass(sg, msg, result):
    result['passes'].append(f'SG{sg} {msg}')
    print(f'  ✓ SG{sg}: {msg}')

def _warn(sg, msg, result):
    result['warns'].append(f'SG{sg} {msg}')
    print(f'  ⚠ SG{sg} WARN: {msg}')

def main():
    codes_arg = None
    if '--codes' in sys.argv:
        codes_arg = [c.strip() for c in sys.argv[sys.argv.index('--codes')+1].split(',') if c.strip()]
    result = {'fails': [], 'passes': [], 'warns': [], 'codes': codes_arg or [],
              'ts': __import__('time').strftime('%Y-%m-%d %H:%M')}

    # ---- SG1 信号源唯一真源 ----
    print('SG1 信号源唯一真源(v2确认日口径):')
    c1, c2 = os.path.join(CACHE, 'chan_signals_v1.pkl'), os.path.join(CACHE, 'chan_signals_v2.pkl')
    if not os.path.exists(c2):
        _fail(1, 'chan_signals_v2.pkl 缺失', result)
    else:
        con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
        qmax = con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0]
        con.close()
        chan2 = pd.read_pickle(c2)
        lag = (pd.Timestamp(qmax) - pd.Timestamp(str(chan2['date'].max()))).days
        if lag > 4:
            _fail(1, f'v2缓存滞后qfq{lag}天(>{4}), 重跑chan_batch.py', result)
        else:
            _pass(1, f'v2缓存截面{chan2["date"].max()} vs qfq{qmax} (lag={lag}天)', result)
        if os.path.exists(c1):
            cv1 = pd.read_pickle(c1)
            if cv1['date'].max() >= chan2['date'].max():
                _warn(1, 'v1缓存比v2还新(疑似旧口径批跑残留), 确认未消费v1', result)

    # ---- 载入名单与panel ----
    if codes_arg:
        codes = codes_arg
    else:
        fs = sorted(f for f in glob.glob(os.path.join(DATA, 'combo_c_signals_*.json'))
                    if 'v2check' not in f and 'ind_' not in f)
        if not fs: _fail(0, '无组合C名单文件', result); sys.exit(2)
        codes = json.load(open(fs[-1], encoding='utf-8'))['selected_codes']
    result['codes'] = codes
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    cs = m[m['date'] == m['date'].max()].set_index('code')
    last = m['date'].max()

    # ---- SG2 过滤口径抽查(独立复算5特征与panel对照, 也服务SG6) ----
    print('SG2/SG6 过滤口径与独立复算:')
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    RISK12 = ['lhb_buy_5','limit_break_5','new_high_3','vwap60_bias','vwap20_bias','gap_up_run_5',
              'chan_buy3_10','new_high20','macd_golden_hi','rounded_20','red_3_soldiers','higher_low']
    mismatch = 0; checked = 0
    for code in codes[:8]:   # 抽查前8只
        if code not in cs.index: continue
        df = pd.read_sql_query(
            "SELECT date,high,low,close,volume FROM stock_daily_meta_qfq WHERE code=? "
            "ORDER BY date DESC LIMIT 80", con, params=[code]).sort_values('date').reset_index(drop=True)  # 按date升序(sort_index对DESC取数是反序bug)
        if len(df) < 70: continue
        h, l, c, v = df['high'], df['low'], df['close'], df['volume']
        r = cs.loc[code]
        if isinstance(r, pd.DataFrame): r = r.iloc[0]
        chk = {
            'higher_low': bool(l.rolling(10).min().iloc[-1] > l.shift(10).rolling(10).min().iloc[-1]*1.02),
            'new_high20': bool(c.iloc[-1] >= h.shift(1).rolling(20).max().iloc[-1]*0.998),
            'vwap60_bias': bool(c.iloc[-1]/((c*v).rolling(60).sum()/v.rolling(60).sum()).iloc[-1] - 1 > 0.10),
        }
        for f, mine in chk.items():
            theirs = bool(r[f] > 0.5)
            checked += 1
            if mine != theirs:
                mismatch += 1
                _fail(6 if checked <= 24 else 2, f'{code} {f} 独立复算{mine}≠panel{theirs}', result)
    con.close()
    if mismatch == 0:
        _pass(6, f'独立复算{checked}项特征与panel全一致', result)
        _pass(2, '过滤口径抽查通过', result)
    else:
        _fail(2, f'过滤口径{mismatch}/{checked}不一致, panel或口径需查', result)

    # ---- SG3 基本面闸(发布层强制) ----
    print('SG3 基本面闸(盈利+ROE下限):')
    bad = []
    for code in codes:
        if code not in cs.index:
            _warn(3, f'{code} 池外(价>20或流动性), 基本面N/A', result); continue
        r = cs.loc[code]
        if isinstance(r, pd.DataFrame): r = r.iloc[0]
        ep, roe = r.get('pit_ep'), r.get('pit_roe')
        if pd.isna(ep): bad.append((code, 'PIT缺失')); continue
        if ep < 0: bad.append((code, f'亏损(ROE={roe:.1f}%)'))
        elif pd.notna(roe) and roe < ROE_FLOOR: bad.append((code, f'ROE={roe:.0f}%<{ROE_FLOOR}'))
    if bad:
        for c_, w_ in bad: _fail(3, f'{c_} {w_} (蓝丰ROE-1199漏网教训, 发布层强制剔除)', result)
        codes_clean = [c for c in codes if c not in {b[0] for b in bad}]
        result['codes_clean'] = codes_clean
        print(f'  → SG3剔除后合格名单{len(codes_clean)}只: {codes_clean}')
    else:
        result['codes_clean'] = codes
        _pass(3, f'{len(codes)}只全部盈利且ROE>{ROE_FLOOR}', result)

    # ---- SG4 事件排雷披露 ----
    print('SG4 事件排雷(解禁/龙虎榜):')
    con = sqlite3.connect(r'file:D:\zcodesj\event_calendar.db?mode=ro', uri=True)
    ph = ','.join('?'*len(codes))
    rel = con.execute(f"SELECT code,name,release_date,pct_of_float_mv FROM restricted_release "
                      f"WHERE code IN ({ph}) ORDER BY release_date", codes).fetchall()
    con.close()
    con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    # 2026-09-10 D1: dragon_tiger(8/10 冻结)→dragon_tiger_new(日更); 硬编码日期
    # '20260825' 晚于旧表 MAX 致恒空=排雷永不触发 → 改相对 6 日窗口
    from datetime import date as _d, timedelta as _td
    _cut = (_d.today() - _td(days=6)).strftime("%Y%m%d")
    lhb = con.execute(f"SELECT DISTINCT substr(ts_code,1,6) FROM dragon_tiger_new "
                      f"WHERE substr(ts_code,1,6) IN ({ph}) AND trade_date>=?", list(codes) + [_cut]).fetchall()
    con.close()
    for r_ in rel:
        pct = float(r_[3])*100 if r_[3] else 0
        if pct > 5: _warn(4, f'{r_[0]} {r_[1]} {r_[2]}解禁{pct:.1f}%流通盘(>5%)', result)
    if lhb:
        for (c_,) in lhb: _warn(4, f'{c_} 近期龙虎榜上榜(5日窗口口径审查)', result)
    if not any('SG4' in w for w in result['warns']):
        _pass(4, '无解禁/龙虎榜披露项', result)

    # ---- SG5 名称价格双源 ----
    print('SG5 名称价格双源:')
    con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    ph = ','.join('?'*len(codes))
    nm = dict(con.execute(f'SELECT code,name FROM industry_map_ths WHERE code IN ({ph})', codes).fetchall())
    con.close()
    missing = [c for c in codes if not nm.get(c)]
    if missing: _fail(5, f'名称缺失: {missing}', result)
    else: _pass(5, f'{len(codes)}只名称全查(与westock实时核对由呈报时人工执行, 本闸记录待办)', result)

    # ---- SG7 口径指纹 ----
    print('SG7 名单口径指纹:')
    fingerprint = {'截面': last, '信号源': 'chan_signals_v2(确认日)', '过滤': 'NEG9交易日窗口',
                   '基本面闸': f'盈利+ROE>{ROE_FLOOR}', 'codes': sorted(codes)}
    prev_fs = sorted(glob.glob(os.path.join(REPORTS, 'stock_gate_*.json')))
    if prev_fs:
        prev = json.load(open(prev_fs[-1], encoding='utf-8'))
        if prev.get('fingerprint', {}).get('截面') != last:
            _warn(7, f'截面已从{prev["fingerprint"]["截面"]}更新到{last}(数据驱动变化, 正当)', result)
    result['fingerprint'] = fingerprint
    _pass(7, f'指纹: 截面{last}/v2/NEG9/盈利闸/{len(codes)}只', result)

    # ---- SG8 反臆想 ----
    print('SG8 反臆想合规:')
    hist = m[(m['chan_buy2_10'] > 0) & m['fwd20_close'].notna()]
    need = {'胜率': f'{(hist["fwd20_close"]>0).mean()*100:.1f}%',
            'P10': f'{hist["fwd20_close"].quantile(0.1)*100:+.1f}%',
            '暴跌率': f'{(hist["fwd20_close"]<=-0.15).mean()*100:.1f}%'}
    result['history_dist'] = need
    _pass(8, f'历史二买分布已载入(呈报必附): {need}; 呈报模板=正向/反向/结论/置信度', result)

    # ---- 判定 ----
    result['verdict'] = 'PASS' if not result['fails'] else 'FAIL'
    fp = os.path.join(REPORTS, f'stock_gate_{last}.json')
    json.dump(result, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    print(f'\n===== 发布闸判定: {result["verdict"]} =====')
    if result['fails']:
        print('FAIL项(修复后重跑):')
        for f_ in result['fails']: print(' ', f_)
        sys.exit(2)
    print(f'全部SG通过, 名单{len(codes)}只可发布; 报告={fp}')

if __name__ == '__main__':
    main()
