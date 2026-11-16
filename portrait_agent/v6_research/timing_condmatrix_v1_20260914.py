# -*- coding: utf-8 -*-
"""PREREG-TIMING-CONDMATRIX-V1 (指纹 d3a154496b6aeb8d) 一次跑执行器.

用途: 判死边界②『事件×位置条件矩阵选股层』首次检验——情绪状态(v3梯队三分制)
      对引擎A信号(19d5c135) fwd20市场超额的条件区分度.
纪律: prereg已先落盘冻结(d3a154496b6aeb8d), 本脚本只执行不调参;
      唯一写入 = data/timing_condmatrix_v1_results_20260914.json;
      状态内存计算, 不写生产台账; panel/qfq 只读.
用法: python -u timing_condmatrix_v1_20260914.py
"""
import hashlib
import json
import math
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
HERE = r'D:\zcodesj\portrait_agent'
PREREG_PATH = r'D:\zcodesj\data\prereg_timing_condmatrix_v1_20260914.json'
OUT = r'D:\zcodesj\data\timing_condmatrix_v1_results_20260914.json'
START = '20200101'
MAIN_TOL, CHINEXT_TOL, TOUCH_BUFFER = 0.08, 0.15, 0.0015

sys.path.insert(0, r'D:\zcodesj')
from sentiment_cycle_state import classify  # noqa: E402  v2五态描述用, 原函数口径


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m1, m2 = a.mean(), b.mean()
    v1, v2 = a.var(ddof=1), b.var(ddof=1)
    n1, n2 = len(a), len(b)
    se = math.sqrt(v1 / n1 + v2 / n2)
    t = (m1 - m2) / se if se > 0 else float('nan')
    df = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)) if se > 0 else float('nan')
    return m1, m2, t, df


def main() -> int:
    prereg = json.load(open(PREREG_PATH, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    fp_check = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
    assert fp == fp_check, f'prereg指纹不符: {fp} vs {fp_check}'
    print(f'[prereg] TIMING-CONDMATRIX-V1 指纹校验通过 {fp}')

    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)

    # ---------- Step1 状态内存回填(qfq推算口径, 同 sentiment_cycle_backfill) ----------
    base = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<?", (START,)).fetchone()[0]
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date", (base,))]
    print(f'[states] 载入 {base}~{dates[-1]} 共{len(dates)}交易日 ...')
    df = pd.read_sql_query(
        "SELECT code,date,high,close,change_pct FROM stock_daily_meta_qfq WHERE date>=?",
        con, params=(base,))
    print(f'[states] {len(df)}行')
    df['date'] = df['date'].astype(str)
    streak: dict = {}
    state_of: dict = {}      # date -> 三分制 高/低/中 (v3冻结口径)
    board_of: dict = {}      # date -> 当日最高连板(高'/低'敏感性变体用)
    v2_of: dict = {}         # date -> classify 五态
    prev_limit: set = set()
    prev_max: int | None = None
    for d, g in df.groupby('date', sort=True):
        chg = pd.to_numeric(g['change_pct'], errors='coerce').to_numpy()
        code_arr = g['code'].astype(str).to_numpy()
        high_arr = pd.to_numeric(g['high'], errors='coerce').to_numpy()
        close_arr = pd.to_numeric(g['close'], errors='coerce').to_numpy()
        ratio = np.where(np.char.startswith(code_arr.astype('U6'), '30'), 0.20, 0.10)
        tol = np.where(ratio == 0.20, CHINEXT_TOL, MAIN_TOL)
        is_lim = (~np.isnan(chg)) & (np.abs(chg - ratio * 100) <= tol)
        limit_today = set(code_arr[is_lim].tolist())
        new_streak = {c: streak.get(c, 0) + 1 if c in prev_limit else 1 for c in limit_today}
        promoted = len(limit_today & prev_limit)
        # 炸板: 触及涨停价(昨收口径)未封
        br_cnt = 0
        prev_close_map = dict(zip(code_arr.tolist(), close_arr.tolist()))
        for c, h in zip(code_arr.tolist(), high_arr.tolist()):
            if c in limit_today or math.isnan(h):
                continue
            pc = prev_close_map.get(c)
            if pc is None or math.isnan(pc) or pc == 0:
                continue
            r = 0.20 if c.startswith(('300', '301')) else 0.10
            if h >= pc * (1 + r - TOUCH_BUFFER):
                br_cnt += 1
        max_board = max(new_streak.values()) if new_streak else 0
        p_rate = (promoted / len(prev_limit)) if prev_limit else None
        # v3 三分制(冻结口径)
        hi = (max_board >= 5) or (p_rate is not None and p_rate >= 0.40)
        lo = (max_board <= 2) or (prev_max is not None and (prev_max - max_board) >= 2)
        state_of[d] = '低' if (hi and lo) else ('高' if hi else ('低' if lo else '中'))
        board_of[d] = max_board
        # v2 五态(描述用, classify 原函数)
        lu_n, lb_n = len(limit_today), br_cnt
        brate = (lb_n / (lu_n + lb_n)) if (lu_n + lb_n) > 0 else None
        stage, _ev = classify({'limit_up_count': lu_n, 'limit_break_count': lb_n,
                               'max_board': max_board, 'prev_max_board': prev_max,
                               'break_rate': brate})
        v2_of[d] = stage
        streak, prev_limit, prev_max = new_streak, limit_today, max_board
    del df
    print(f'[states] 状态日数 高={sum(1 for v in state_of.values() if v == "高")} '
          f'中={sum(1 for v in state_of.values() if v == "中")} '
          f'低={sum(1 for v in state_of.values() if v == "低")} (含基准日{base})')

    # ---------- Step2 引擎A信号(panel 历史截面, 冻结配方 19d5c135) ----------
    print('[signals] 载入 panel_v6_full.pkl ...')
    panel = pd.read_pickle(HERE + r'\cache\panel_v6_full.pkl')
    panel['date'] = panel['date'].astype(str)
    panel = panel[panel['date'] >= START]
    pos = pd.to_numeric(panel['pos250'], errors='coerce')
    ev = (panel[['chan_buy1_10', 'chan_buy2_10', 'island_5', 'gap_up_hold']].fillna(0) > 0.5).any(axis=1)
    neg = (panel[['rounded_20', 'lhb_buy_5', 'vwap60_bias', 'vwap20_bias']].fillna(1) <= 0.5).all(axis=1)
    sig = panel[(pos <= 0.40) & ev & neg][['date', 'code']].copy()
    del panel, pos, ev, neg
    sig = sig[~sig['code'].astype(str).str.startswith('688')]
    sig['state'] = sig['date'].map(state_of)
    n_unknown = int(sig['state'].isna().sum())
    n_mid = int((sig['state'] == '中').sum())
    sig_pri = sig[sig['state'].isin(['高', '低'])].copy()
    print(f'[signals] 信号行={len(sig)} (高{int((sig_pri["state"] == "高").sum())}/'
          f'低{int((sig_pri["state"] == "低").sum())}/中{n_mid}/unknown缺失{n_unknown})')

    # ---------- Step3 fwd20 市场超额(engineA_shadow_eval 同款查询) ----------
    tds = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    idx = {d: i for i, d in enumerate(tds)}
    rows_out, dropped_fwd, dropped_px = [], 0, 0
    for d, g in sig_pri.groupby('date'):
        i0 = idx.get(d)
        if i0 is None or i0 + 21 >= len(tds):
            dropped_fwd += len(g)
            continue
        d1, d21 = tds[i0 + 1], tds[i0 + 21]
        codes = g['code'].astype(str).tolist()
        ph = ','.join('?' * len(codes))
        bench = con.execute(
            "SELECT AVG(close/(SELECT close FROM stock_daily_meta_qfq AS t2 "
            "WHERE t2.code=t1.code AND t2.date=?)-1)*100 FROM stock_daily_meta_qfq AS t1 WHERE date=?",
            (d1, d21)).fetchone()[0]
        px1 = dict(con.execute(f"SELECT code,close FROM stock_daily_meta_qfq WHERE date=? AND code IN ({ph})",
                               (d1, *codes)).fetchall())
        px2 = dict(con.execute(f"SELECT code,close FROM stock_daily_meta_qfq WHERE date=? AND code IN ({ph})",
                               (d21, *codes)).fetchall())
        st = g['state'].iloc[0]
        for c in codes:
            if c not in px1 or c not in px2 or not px1[c] or not px2[c]:
                dropped_px += 1
                continue
            rows_out.append({'date': d, 'code': c, 'state': st,
                             'excess': (px2[c] / px1[c] - 1) * 100 - (bench or 0.0)})
    rows_df = pd.DataFrame(rows_out)
    print(f'[fwd20] 有效行={len(rows_df)} 剔除(超窗)={dropped_fwd} 剔除(停牌缺价)={dropped_px}')

    # ---------- Step4 判定(冻结判据) ----------
    res = {'prereg_id': prereg['id'], 'fingerprint': fp, 'window': [dates[1], dates[-1]],
           'n_signals_raw': int(len(sig)), 'n_unknown_state': n_unknown, 'n_mid_state': n_mid,
           'n_rows_used': int(len(rows_df)), 'dropped_fwd': dropped_fwd, 'dropped_px': dropped_px}
    hi_rows = rows_df[rows_df['state'] == '高']
    lo_rows = rows_df[rows_df['state'] == '低']
    hi_day = hi_rows.groupby('date')['excess'].mean()
    lo_day = lo_rows.groupby('date')['excess'].mean()
    p0 = bool(len(hi_day) >= 30 and len(lo_day) >= 30 and len(hi_rows) >= 500 and len(lo_rows) >= 500)
    res['P0_power'] = {'high_days': int(len(hi_day)), 'low_days': int(len(lo_day)),
                       'high_rows': int(len(hi_rows)), 'low_rows': int(len(lo_rows)),
                       'pass': p0}
    if not p0:
        res['primary'] = {'verdict': 'INSUFFICIENT_POWER'}
        res['verdict'] = 'INSUFFICIENT_POWER'
    else:
        m_hi, m_lo, t, dfree = welch(hi_day.values, lo_day.values)
        diff = m_hi - m_lo
        p1 = bool(diff >= 1.0 and t >= 2.0)
        days_all = sorted(hi_day.index.union(lo_day.index))
        med = days_all[len(days_all) // 2]
        halves = {}
        for tag, sel in (('前半', days_all[:len(days_all) // 2]), ('后半', days_all[len(days_all) // 2:])):
            hs = [hi_day[x] for x in sel if x in hi_day.index]
            ls = [lo_day[x] for x in sel if x in lo_day.index]
            halves[tag] = {'high_days': len(hs), 'low_days': len(ls),
                           'high_mean': round(float(np.mean(hs)), 3) if hs else None,
                           'low_mean': round(float(np.mean(ls)), 3) if ls else None}
        h1, h2 = halves['前半'], halves['后半']
        if min(h1['high_days'], h1['low_days'], h2['high_days'], h2['low_days']) < 5:
            p2, p2_note = False, 'UNTESTABLE(半窗某态<5日,按不过处理)'
        else:
            p2 = bool(h1['high_mean'] > h1['low_mean'] and h2['high_mean'] > h2['low_mean'])
            p2_note = '两半方向一致' if p2 else '方向反转'
        res['primary'] = {
            'verdict': 'PASS' if (p1 and p2) else 'FAIL',
            'P1': {'high_day_mean': round(m_hi, 3), 'low_day_mean': round(m_lo, 3),
                   'diff_pp': round(diff, 3), 'welch_t': round(t, 2), 'welch_df': round(dfree, 1),
                   'pass': p1},
            'P2': {'halves': halves, 'pass': p2, 'note': p2_note},
            'row_level_descriptive': {
                'high_row_mean': round(float(hi_rows['excess'].mean()), 3),
                'low_row_mean': round(float(lo_rows['excess'].mean()), 3),
                'mid_row_mean': round(float(rows_df.loc[rows_df['state'] == '中', 'excess'].mean()), 3) if n_mid else None}}
        res['verdict'] = res['primary']['verdict']

    # ---- 敏感性(描述性, 非判据): 高'=仅board>=5 / 低'=仅board<=2 ----
    var_state = {}
    for d in state_of:
        b = board_of[d]
        var_state[d] = '高' if b >= 5 else ('低' if b <= 2 else '中')
    v_rows = rows_df.copy()
    v_rows['vs'] = v_rows['date'].map(var_state)
    vh = v_rows[v_rows['vs'] == '高'].groupby('date')['excess'].mean()
    vl = v_rows[v_rows['vs'] == '低'].groupby('date')['excess'].mean()
    res['sensitivity_board_only'] = {
        'note': '描述性方向观察, 非判据(prereg sensitivity_descriptive_not_gates)',
        'high_days': int(len(vh)), 'low_days': int(len(vl)),
        'high_day_mean': round(float(vh.mean()), 3) if len(vh) else None,
        'low_day_mean': round(float(vl.mean()), 3) if len(vl) else None,
        'direction_high_gt_low': bool(vh.mean() > vl.mean()) if (len(vh) and len(vl)) else None}

    # ---- v2 五态描述(classify 原函数, 非判据) ----
    vd = rows_df.copy()
    vd['v2'] = vd['date'].map(v2_of)
    res['v2_five_state_descriptive'] = {}
    for st, g2 in vd.groupby('v2'):
        res['v2_five_state_descriptive'][st] = {
            'rows': int(len(g2)), 'days': int(g2['date'].nunique()),
            'row_mean': round(float(g2['excess'].mean()), 3)}

    con.close()
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n===== 判定结果 =====')
    print(json.dumps(res.get('primary'), ensure_ascii=False, indent=1))
    print(f"敏感性(仅高度): {json.dumps(res['sensitivity_board_only'], ensure_ascii=False)}")
    print(f"v2五态描述: {json.dumps(res['v2_five_state_descriptive'], ensure_ascii=False)}")
    print(f"VERDICT = {res['verdict']}")
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
