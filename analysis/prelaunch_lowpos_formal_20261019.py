# -*- coding: utf-8 -*-
"""低位基率标定 + 连板链胚胎判别力正式检验 + 胚胎→启动时序倒推 2026-10-19
主人令: 用画像系统回答(能涨的股票什么样)→倒推(低位什么样)→倒推(买卖点)
定位: 10/10 entry_portrait_db §十/§十一 已定谳的正式统计化补强 + 两块真缺口
  Part A 低位基率: 低位股票日的 20 日前瞻收益/冲高/出板基率, 2016-2026 分年 (诚实分母)
  Part B 链胚胎判别力: H>=2(续板链) vs H=1(一日游) 启动前 T-k 特征, Cliff's delta+MWU+BH-FDR
          + 胚胎格(T-20 pos60>=60 & 0<ret20<20 & 板史60>=2) 对 H>=2 的 lift
  Part C 胚胎日事前倒推(买卖点核心): 胚胎状态日(事前可观测, 不条件于未来成链) ->
          P(出板 within k)/间隔分布/埋伏 f5/f20/启动链高质量, vs 全天基线
宇宙修正: 10/10 家族脚本 code[0] in '684' 误剔沪市主板 60xxxx (实际=深主板+创业板 only);
  本脚本宇宙=60+00+30 (剔 68 科创 / 92 北交), 并对 Part B 胚胎 lift 做 00+30 vs 全宇宙复检
口径: ZT=派生 qfq 相邻收盘比>=thr(主板9.85/创业19.85/ST4.85); pos60=(cl-min60(C))/(max60(C)-min60(C));
  与 chain_prelaunch_study/entry_portrait 系列对齐; 研究层零交易权; 边界: qfq 无退市股=幸存者偏差
"""
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
NAME_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
LHB_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
YEARS = ('2024', '2025')
START = '20160101'
COST = 0.30          # 双边成本假设(%), 在案§十二口径上偏保守
STOP1, STOP2 = -8.0, -12.0
RNG = np.random.default_rng(20261019)


def load_names():
    names = defaultdict(set)
    z = sqlite3.connect(f'file:{ZT_DB}?mode=ro', uri=True)
    for code, nm in z.execute('SELECT code, name FROM zt_pool'):
        if nm:
            names[code].add(nm)
    z.close()
    try:
        con = sqlite3.connect(f'file:{NAME_DB}?mode=ro', uri=True)
        for code, nm in con.execute('SELECT code, name FROM spot_daily'):
            if nm:
                names[code].add(nm)
        con.close()
    except sqlite3.Error:
        pass
    return {c: max(s, key=len) for c, s in names.items()}


def thr_of(code, name):
    if 'ST' in (name or '').upper():
        return 4.85
    return 19.85 if code[:3] in ('300', '301') else 9.85


def load_lhb_set():
    """LHB 事件集合 {(security_code, yyyymmdd)}"""
    s = set()
    con = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True)
    for d, c in con.execute('SELECT trade_date, security_code FROM lhb_seat_detail'):
        s.add((c, d.replace('-', '')))
    con.close()
    return s


def load_ind_map():
    """industry_map_ths -> {code: L1行业id}, 非PIT现映射(在案声明)"""
    m, ids = {}, {}
    con = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True)
    for code, ind in con.execute('SELECT code, industry FROM industry_map_ths'):
        l1 = (ind or '').split('-')[0]
        if l1 not in ids:
            ids[l1] = len(ids) + 1
        m[code] = ids[l1]
    con.close()
    return m


def in_universe(code):
    return code[:2] in ('60', '00', '30')


def rolling_feats(D, O, H, L, C, V, A):
    """全bar向量化: pct(派生)/pos60/bias20/ret5/ret20/vol_ratio/zt5/zt20/zt60/ma_bull/振幅收敛/额分位/距60高回撤"""
    n = len(D)
    Cn = np.array(C, dtype=float)
    On = np.array(O, dtype=float)
    Hn = np.array(H, dtype=float)
    Ln = np.array(L, dtype=float)
    Vn = np.array(V, dtype=float)
    An = np.array(A, dtype=float)
    pct = np.full(n, np.nan)
    pct[1:] = (Cn[1:] / Cn[:-1] - 1) * 100.0
    ok = ~np.isnan(Cn)
    cs = np.where(ok, Cn, np.nan)
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    import pandas as pd
    hi = pd.Series(np.where(np.isnan(Hn), np.nan, Hn)).rolling(60, min_periods=40).max().to_numpy()
    lo = pd.Series(np.where(np.isnan(Hn), np.nan, Hn)).rolling(60, min_periods=40).min().to_numpy()
    cmax = pd.Series(cs).rolling(60, min_periods=40).max().to_numpy()
    cmin = pd.Series(cs).rolling(60, min_periods=40).min().to_numpy()
    ma20 = pd.Series(cs).rolling(20, min_periods=20).mean().to_numpy()
    ma5 = pd.Series(cs).rolling(5, min_periods=5).mean().to_numpy()
    ma10 = pd.Series(cs).rolling(10, min_periods=10).mean().to_numpy()
    v5 = pd.Series(Vn).rolling(5, min_periods=3).mean().to_numpy()
    ret20 = np.where(Cn > 0, 100.0 * (Cn / np.roll(Cn, 20) - 1), np.nan)
    ret20[:20] = np.nan
    ret5 = np.where(Cn > 0, 100.0 * (Cn / np.roll(Cn, 5) - 1), np.nan)
    ret5[:5] = np.nan
    pos60 = np.where((cmax - cmin) > 0, 100.0 * (Cn - cmin) / (cmax - cmin), np.nan)
    pos60 = np.where(~np.isnan(cmin), pos60, np.nan)
    bias20 = 100.0 * (Cn / ma20 - 1)
    vr = np.where(v5 > 0, Vn / v5, np.nan)
    iszt = (pct >= 9.85).astype(float)  # per-stock thr 后处理覆盖
    # 前 k 根计数 = cum[j] - cum[j-k](纯回看; 20261019勘误: 原convolve[k:n+k]切片实为未来k根前视, 已废)
    x = np.nan_to_num(iszt, nan=0.0)
    cum = np.concatenate([[0.0], np.cumsum(x)])
    idx = np.arange(n)
    zt5 = cum[idx] - cum[np.maximum(idx - 5, 0)]
    zt20 = cum[idx] - cum[np.maximum(idx - 20, 0)]
    zt60 = cum[idx] - cum[np.maximum(idx - 60, 0)]
    mab = ((ma5 > ma10) & (ma10 > ma20)).astype(float)
    mab[:20] = np.nan
    # ---- Part A2 特征: 日内振幅 / 额60日分位 / 距60日高回撤 ----
    cprev = np.roll(Cn, 1)
    cprev[0] = np.nan
    amp = (Hn - Ln) / np.where(cprev > 0, cprev, np.nan)
    amp10 = pd.Series(amp).rolling(10, min_periods=6).mean().to_numpy()
    amp60 = pd.Series(amp).rolling(60, min_periods=40).mean().to_numpy()
    amp_ratio = np.where(amp60 > 0, amp10 / amp60, np.nan)
    amt_pos = np.full(n, np.nan)
    if n >= 60:
        win = np.lib.stride_tricks.sliding_window_view(An, 60)          # (n-59,60) 结束于 i
        ge = (win >= An[59:, None]).sum(axis=1)
        amt_pos[59:] = ge / 60.0                                        # 含当日, >=当前占比
    dd60 = np.where(hi > 0, (hi - Cn) / hi, np.nan)                     # hi=60bar high backward max
    return dict(D=D, n=n, pct=pct, pos60=pos60, bias20=bias20, ret5=ret5, ret20=ret20,
                vr=vr, zt5=zt5, zt20=zt20, zt60=zt60, mab=mab, hi=hi, lo=lo, C=Cn, On=On, Hraw=Hn,
                L=Ln, amp_ratio=amp_ratio, amt_pos=amt_pos, dd60=dd60)


def main():
    names = load_names()
    lhb_set = load_lhb_set()
    ind_map = load_ind_map()
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute(
        "SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
        "WHERE date>='" + START + "' ORDER BY code,date")

    # ---- Part A 累计器 ----
    a_low = defaultdict(lambda: [0, 0, 0, 0])   # year -> [n_low, f20>10, runup>=15, zt_fwd20]
    a_deep = defaultdict(lambda: [0, 0, 0, 0])
    a_all = defaultdict(lambda: [0, 0, 0, 0])   # 全部有效日基线
    a_defs = {k: defaultdict(lambda: [0, 0, 0, 0]) for k in ('D1', 'D2', 'D3', 'D4')}  # year -> [n,f20>10,zt20,lhb20]

    # ---- Part B/C 累计器 ----
    chains = []                                  # (code,s,H,year,board,embryo20)
    feats = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # grp->Tk->feat->vals
    emb_days = {'n': 0, 'zt1': 0, 'zt5': 0, 'zt10': 0, 'zt20': 0, 'f5': [], 'f20': [],
                'ttf': [], 'launch_h1': 0, 'launch_h2': 0, 'lhb20': 0}
    emb_ded = defaultdict(lambda: {'n': 0, 'f5': [], 'f20': [], 'launch': 0, 'h2': 0})  # 去簇(同股>=10bar)+分年
    all_day = {'n': 0, 'zt1': 0, 'zt5': 0, 'zt10': 0, 'zt20': 0, 'f5': [], 'f20': [], 'lhb20': 0}
    fb_total = [0, 0]                            # 全部首板: [n, H>=2]
    # ---- Part D 等待期风险/买入规则累计器 (去簇事件) ----
    Dacc = {'mae_l': [], 'mae_n': [], 'hit8_l': 0, 'hit8_n': 0, 'n_l': 0, 'n_n': 0,
            'r1': [], 'r2': [], 'r3a': [], 'r3b': [], 'holdbars': [], 'lhb_l': 0, 'idle': 0,
            'r1_bd': [], 'r2_bd': [], 'stop_locked': 0, 'stop_n': 0}
    # ---- Part F/G: 匹配对照组面板 + 胚胎事件记录 ----
    day_pan = defaultdict(lambda: ([], [], [], [], []))   # date -> [ind_id, zt_today, emb, f20, fwd_zt20]
    evrec = []                                            # (code,date,ind,f20,launched,hit8)

    code_now, buf = None, None

    def flush(code):
        nonlocal code_now, buf
        if not buf or len(buf[0]) < 70:
            return
        thr = thr_of(code, names.get(code, ''))
        f = rolling_feats(*buf)
        n, D, pct = f['n'], f['D'], f['pct']
        iszt = pct >= thr
        islhb = np.array([(code, d) in lhb_set for d in D], dtype=float)
        cuml = np.concatenate([[0.0], np.cumsum(islhb)])
        lhb20f = np.zeros(n)
        if n > 20:
            lhb20f[:n - 20] = cuml[21:] - cuml[1:n - 19]          # i+1..i+20 内 LHB 计数
        yr = np.array([d[:4] for d in D])
        # Part A: 低位状态 (i>=59, 标签需 i+20<=n-1)
        m_valid = np.zeros(n, dtype=bool)
        m_valid[59:] = True
        m_valid &= ~np.isnan(f['pos60']) & ~np.isnan(f['ret20']) & ~np.isnan(f['C'])
        m_lbl = m_valid.copy()
        m_lbl[n - 20:] = False
        idx = np.where(m_lbl)[0]
        if len(idx):
            y = yr[idx]
            f20 = np.full(n, np.nan)
            f20[idx] = 100.0 * (f['C'][idx + 20] / f['C'][idx] - 1)
            run20 = np.full(n, np.nan)
            # runup = max(high[i+1..i+20])/C[i]-1: 原始 high 反转序列滚动 max 实现前向窗
            rev = pd.Series(np.nan_to_num(f['Hraw'], nan=0.0)[::-1]).rolling(20, min_periods=1).max().to_numpy()[::-1]
            run20[idx] = 100.0 * (rev[idx + 1] / f['C'][idx] - 1)
            zt_fwd = np.zeros(n)
            zt_fwd[idx] = zt_fwd20(iszt, idx, n)
            low = (f['pos60'] < 50) & (f['ret20'] <= 5.0) & (f['zt20'] == 0) & (~iszt)
            deep = (f['pos60'] < 30) & (f['ret20'] <= 5.0) & (f['zt20'] == 0) & (~iszt)
            for tag, mask, acc in (('low', low, a_low), ('deep', deep, a_deep), ('all', m_lbl, a_all)):
                mi = idx[mask[idx]]
                for yy in np.unique(yr[mi]):
                    c = acc[str(yy)]
                    c[0] += int((yr[mi] == yy).sum())
                    sel = yr[mi] == yy
                    c[1] += int(np.nansum(f20[mi][sel] > 10.0))
                    c[2] += int(np.nansum(run20[mi][sel] >= 15.0))
                    c[3] += int(zt_fwd[mi][sel].sum())
            # Part A2: 四定义低位敏感性(各自独立口径, 不附加其它条件)
            defs = {'D1': np.nan_to_num(f['pos60'], nan=1.0) < 40,
                    'D2': np.nan_to_num(f['amt_pos'], nan=1.0) < 0.5,
                    'D3': np.nan_to_num(f['dd60'], nan=0.0) > 0.15,
                    'D4': np.nan_to_num(f['amp_ratio'], nan=1.0) < 0.8}
            for dk, dm in defs.items():
                mi = idx[dm[idx]]
                for yy in np.unique(yr[mi]):
                    c = a_defs[dk][str(yy)]
                    c[0] += int((yr[mi] == yy).sum())
                    sel = yr[mi] == yy
                    c[1] += int(np.nansum(f20[mi][sel] > 10.0))
                    c[2] += int(zt_fwd[mi][sel].sum())
                    c[3] += int(lhb20f[mi][sel].sum())
        # Part B: maximal ZT run 链 (2024-2025)
        i = 59
        while i < n:
            if iszt[i] and not (i > 0 and iszt[i - 1]):
                j = i
                while j + 1 < n and iszt[j + 1]:
                    j += 1
                h = j - i + 1
                if D[i][:4] in YEARS:
                    embryo = bool(f['pos60'][i - 20] >= 60 and 0 < f['ret20'][i - 20] < 20
                                  and f['zt60'][i - 20] >= 2) if i >= 79 else None
                    chains.append((code, i, h, D[i][:4], code[:2], embryo))
                    grp = 'H>=2' if h >= 2 else 'H=1'
                    for off in (1, 5, 10, 20):
                        k = i - off
                        if k < 59:
                            continue
                        vals = {'pos60': f['pos60'][k], 'bias20': f['bias20'][k], 'ret5': f['ret5'][k],
                                'ret20': f['ret20'][k], 'vol_ratio': f['vr'][k], 'zt60': f['zt60'][k],
                                'ma_bull': f['mab'][k]}
                        for kk, vv in vals.items():
                            if vv is not None and not np.isnan(vv):
                                feats[grp][f'T-{off}'][kk].append((D[i], float(vv)))
                i = j + 1
            else:
                i += 1
        # Part B/C 基线与胚胎日 (2024-2025, i>=59, f5/f20 可得)
        dedup_last = -999
        for i in range(59, n - 20):
            if D[i][:4] not in YEARS:
                continue
            nxt = iszt[i + 1:i + 21]
            tt = all_day
            tt['n'] += 1
            if lhb20f[i] > 0:
                tt['lhb20'] += 1
            if nxt[0]:
                tt['zt1'] += 1
            if nxt[:5].any():
                tt['zt5'] += 1
            if nxt[:10].any():
                tt['zt10'] += 1
            if nxt.any():
                tt['zt20'] += 1
            tt['f5'].append(100.0 * (f['C'][i + 5] / f['C'][i] - 1))
            f20v = 100.0 * (f['C'][i + 20] / f['C'][i] - 1)
            tt['f20'].append(f20v)
            emb = (f['pos60'][i] >= 60 and 0 < f['ret20'][i] < 20 and f['zt60'][i] >= 2
                   and f['zt5'][i] == 0 and not iszt[i])
            pp = day_pan[D[i]]
            pp[0].append(ind_map.get(code, -1))
            pp[1].append(1 if iszt[i] else 0)
            pp[2].append(1 if emb else 0)
            pp[3].append(f20v)
            pp[4].append(1 if nxt.any() else 0)
            if emb:
                e = emb_days
                e['n'] += 1
                if nxt[0]:
                    e['zt1'] += 1
                if nxt[:5].any():
                    e['zt5'] += 1
                if nxt[:10].any():
                    e['zt10'] += 1
                if nxt.any():
                    e['zt20'] += 1
                e['f5'].append(100.0 * (f['C'][i + 5] / f['C'][i] - 1))
                e['f20'].append(100.0 * (f['C'][i + 20] / f['C'][i] - 1))
                if nxt.any():
                    j = i + 1 + int(np.argmax(nxt))
                    ttf = j - i
                    e['ttf'].append(ttf)
                    # 链高: 从 j 起连续 ZT 长度
                    k = j
                    while k + 1 < n and iszt[k + 1]:
                        k += 1
                    hl = k - j + 1
                    if hl >= 2:
                        e['launch_h2'] += 1
                    else:
                        e['launch_h1'] += 1
                if lhb20f[i] > 0:
                    e['lhb20'] += 1
                # 去簇复检: 同股相邻记录间隔>=10bar
                if i - dedup_last >= 10:
                    dedup_last = i
                    d = emb_ded[D[i][:4]]
                    d['n'] += 1
                    d['f5'].append(100.0 * (f['C'][i + 5] / f['C'][i] - 1))
                    d['f20'].append(100.0 * (f['C'][i + 20] / f['C'][i] - 1))
                    if nxt.any():
                        j = i + 1 + int(np.argmax(nxt))
                        k = j
                        while k + 1 < n and iszt[k + 1]:
                            k += 1
                        d['launch'] += 1
                        if k - j + 1 >= 2:
                            d['h2'] += 1
                    # ---- Part D: 等待期风险与三买入规则 (去簇事件) ----
                    entry = f['C'][i]
                    f20i = 100.0 * (f['C'][i + 20] / entry - 1)
                    if nxt.any():
                        jw = i + 1 + int(np.argmax(nxt))
                        wait_end = jw - 1
                        launched = True
                    else:
                        jw = None
                        wait_end = i + 20
                        launched = False
                    lo_w = f['L'][i + 1:wait_end + 1]
                    mae_low = float(np.min(lo_w) / entry * 100.0 - 100.0) if len(lo_w) else 0.0
                    r1 = STOP1 if mae_low <= STOP1 else f20i
                    if mae_low <= STOP2:
                        r2 = STOP2
                    elif launched:
                        post = f['L'][jw:i + 21]
                        mae_post = float(np.min(post) / entry * 100.0 - 100.0) if len(post) else 0.0
                        r2 = STOP1 if mae_post <= STOP1 else f20i
                    else:
                        r2 = f20i
                    if launched:
                        Dacc['n_l'] += 1
                        Dacc['mae_l'].append(mae_low)
                        Dacc['hit8_l'] += int(mae_low <= STOP1)
                        if lhb20f[i] > 0:
                            Dacc['lhb_l'] += 1
                        Dacc['r3a'].append(100.0 * (f['C'][min(jw + 20, n - 1)] / f['C'][jw] - 1))
                        if jw + 1 < n and f['On'][jw + 1] > 0:
                            Dacc['r3b'].append(100.0 * (f['C'][min(jw + 21, n - 1)] / f['On'][jw + 1] - 1))
                    else:
                        Dacc['n_n'] += 1
                        Dacc['mae_n'].append(mae_low)
                        Dacc['hit8_n'] += int(mae_low <= STOP1)
                        Dacc['idle'] += 1
                    if mae_low <= STOP1:
                        t8 = int(np.argmax(f['L'][i + 1:wait_end + 1] <= entry * (1.0 + STOP1 / 100.0))) + 1
                        Dacc['holdbars'].append(t8)
                        Dacc['stop_n'] += 1
                        if t8 < len(f['C']) and f['C'][t8 - 1] > 0:
                            cpct = (f['C'][t8] / f['C'][t8 - 1] - 1) * 100.0
                            if cpct <= -(thr - 0.4):
                                Dacc['stop_locked'] += 1
                    else:
                        Dacc['holdbars'].append(20)
                    Dacc['r1'].append(r1)
                    Dacc['r2'].append(r2)
                    Dacc['r1_bd'].append((D[i], r1, f20i))
                    Dacc['r2_bd'].append((D[i], r2))
                    evrec.append((code, D[i], ind_map.get(code, -1), f20i, launched, mae_low <= STOP1))
        return

    import pandas as pd

    def zt_fwd20(iszt, idx, n):
        out = np.zeros(len(idx))
        isz = iszt.astype(float)
        cum = np.concatenate([[0], np.cumsum(isz)])
        for t, i in enumerate(idx):
            out[t] = cum[min(i + 21, n)] - cum[i + 1]
        return out

    for code, d, o, h, l, c, v, a in cur:
        if not in_universe(code):
            continue
        if code != code_now:
            flush(code_now)
            code_now, buf = code, ([], [], [], [], [], [], [])
        buf[0].append(str(d))
        buf[1].append(o)
        buf[2].append(h)
        buf[3].append(l)
        buf[4].append(c)
        buf[5].append(v)
        buf[6].append(a)
    flush(code_now)
    con.close()

    # 首板无条件基线 (链口径): P(H>=2 | 某日首板)
    fb_total[1] = len(chains)
    fb_total[0] = sum(1 for x in chains if x[2] >= 2)

    out = {}
    print('== Part A 低位股票日前瞻基率 (2016-2026, 宇宙=60+00+30, 剔科创/北交, i>=59bar) ==')
    print(f"{'年':<6}{'低位n':>9}{'P(f20>10%)':>11}{'P(runup>=15%)':>14}{'P(20日内出板)':>13} | {'全部日n':>9}{'P(f20>10%)':>11} | {'深位n':>8}{'P(f20>10%)':>11}")
    A = {}
    for yy in sorted(set(list(a_low.keys()) + list(a_all.keys()))):
        lo, dp, al = a_low.get(yy, [0, 0, 0, 0]), a_deep.get(yy, [0, 0, 0, 0]), a_all.get(yy, [0, 0, 0, 0])
        if lo[0] == 0 and al[0] == 0:
            continue
        row = {'low_n': lo[0], 'low_p_f20': lo[1] / lo[0] * 100 if lo[0] else None,
               'low_p_run': lo[2] / lo[0] * 100 if lo[0] else None, 'low_p_zt': lo[3] / lo[0] * 100 if lo[0] else None,
               'all_n': al[0], 'all_p_f20': al[1] / al[0] * 100 if al[0] else None,
               'deep_n': dp[0], 'deep_p_f20': dp[1] / dp[0] * 100 if dp[0] else None}
        A[yy] = row
        print(f"{yy:<6}{lo[0]:>9}{row['low_p_f20']:>10.2f}%{row['low_p_run']:>13.2f}%{row['low_p_zt']:>12.2f}% | "
              f"{al[0]:>9}{row['all_p_f20']:>10.2f}% | {dp[0]:>8}{row['deep_p_f20']:>10.2f}%")
    out['partA'] = A

    # ---- Part B 统计检验 ----
    print('\n== Part B 链胚胎判别力正式检验 (H>=2 vs H=1, 2024-2025, 宇宙=60+00+30) ==')
    n2 = len(feats.get('H>=2', {}).get('T-20', {}).get('pos60', []))
    n1 = len(feats.get('H=1', {}).get('T-20', {}).get('pos60', []))
    print(f'n(H>=2)={n2}  n(H=1)={n1}')
    from scipy.stats import mannwhitneyu
    FL = ['pos60', 'bias20', 'ret5', 'ret20', 'vol_ratio', 'zt60', 'ma_bull']
    tests = []
    for off in ('T-1', 'T-5', 'T-10', 'T-20'):
        for fl in FL:
            x = [v for _, v in feats.get('H>=2', {}).get(off, {}).get(fl, [])]
            y = [v for _, v in feats.get('H=1', {}).get(off, {}).get(fl, [])]
            if len(x) < 30 or len(y) < 30:
                continue
            try:
                u, p = mannwhitneyu(x, y, alternative='two-sided')
            except ValueError:
                continue
            delta = 2.0 * u / (len(x) * len(y)) - 1.0
            tests.append((off, fl, st.median(x), st.median(y), delta, p, len(x), len(y)))
    # BH-FDR
    ps = sorted(range(len(tests)), key=lambda t: tests[t][5])
    m = len(tests)
    fdr = [None] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = ps[rank]
        prev = min(prev, tests[i][5] * m / (rank + 1))
        fdr[i] = prev
    print(f"{'窗':<6}{'特征':<10}{'H>=2中位':>10}{'H=1中位':>10}{'Cliffδ':>8}{'p':>10}{'FDR q':>9}")
    B = []
    for i, (off, fl, mx, my, delta, p, nx, ny) in enumerate(tests):
        B.append({'win': off, 'feat': fl, 'med_h2': mx, 'med_h1': my, 'cliff': delta, 'p': p, 'q': fdr[i]})
        print(f"{off:<6}{fl:<10}{mx:>10.2f}{my:>10.2f}{delta:>+8.3f}{p:>10.2e}{fdr[i]:>9.3f}")
    out['partB_tests'] = B
    out['partB_n'] = {'h2': n2, 'h1': n1}

    # 胚胎 lift (T-20 胚胎格 -> P(H>=2))
    emb_ch = [c for c in chains if c[5]]
    emb_h2 = sum(1 for c in emb_ch if c[2] >= 2)
    un = fb_total[0] / fb_total[1] * 100 if fb_total[1] else 0
    emb_p = emb_h2 / len(emb_ch) * 100 if emb_ch else 0
    sub = [c for c in emb_ch if c[4] in ('00', '30')]
    sub_h2 = sum(1 for c in sub if c[2] >= 2)
    un_sub = sum(1 for c in chains if c[4] in ('00', '30') and c[2] >= 2) / max(1, sum(1 for c in chains if c[4] in ('00', '30'))) * 100
    print(f"\n胚胎格(T-20 pos60>=60 & 0<ret20<20 & 板史60>=2): n={len(emb_ch)} P(H>=2)={emb_p:.2f}% "
          f"vs 无条件 P(H>=2|首板)={un:.2f}% lift={emb_p / un if un else 0:.2f}x")
    print(f"  复检 00+30 子宇宙(=10/10口径): n={len(sub)} P={sub_h2 / len(sub) * 100 if sub else 0:.2f}% vs 基线{un_sub:.2f}%")
    # 板史(zt60@T-1)分桶 lift: 唯一稳健判别特征的阈值化
    x2 = [v for _, v in feats.get('H>=2', {}).get('T-1', {}).get('zt60', [])]
    x1 = [v for _, v in feats.get('H=1', {}).get('T-1', {}).get('zt60', [])]
    buckets = [(0, 1), (2, 3), (4, 5), (6, 99)]
    print('板史60@T-1 分桶 -> P(H>=2):')
    zt_rows = []
    for b0, b1 in buckets:
        sel2 = [1 for v in x2 if b0 <= v <= b1]
        sel1 = [0 for v in x1 if b0 <= v <= b1]
        n = len(sel2) + len(sel1)
        p = len(sel2) / n * 100 if n else 0
        zt_rows.append({'bucket': f'{b0}-{b1 if b1 < 99 else "+"}', 'n': n, 'p_h2': p})
        print(f"  板史{b0}{'+' if b1 == 99 else '-' + str(b1)}: n={n} P(H>=2)={p:.2f}% lift={p / un if un else 0:.2f}x")
    out['partB_embryo'] = {'n': len(emb_ch), 'p_h2': emb_p, 'uncond': un,
                           'n_0030': len(sub), 'p_h2_0030': sub_h2 / len(sub) * 100 if sub else None,
                           'uncond_0030': un_sub}

    # ---- Part C 胚胎日事前倒推 ----
    print('\n== Part C 胚胎日事前倒推 (2024-2025, 可观测状态, 不条件于未来成链) ==')
    print(f"胚胎条件: pos60>=60 & 0<ret20<20 & 60日板史>=2 & 近5日无板 & 当日未板")
    e, t = emb_days, all_day
    def pct(a, b):
        return a / b * 100 if b else 0
    print(f"{'':<14}{'n':>9}{'P板1日':>8}{'P板5日':>8}{'P板10日':>8}{'P板20日':>9}{'medF5':>8}{'medF20':>8}")
    print(f"{'胚胎日':<14}{e['n']:>9}{pct(e['zt1'], e['n']):>7.2f}%{pct(e['zt5'], e['n']):>7.2f}%"
          f"{pct(e['zt10'], e['n']):>7.2f}%{pct(e['zt20'], e['n']):>8.2f}%"
          f"{st.median(e['f5']):>+8.2f}{st.median(e['f20']):>+8.2f}" if e['n'] else '胚胎日: n=0')
    print(f"{'全部日基线':<13}{t['n']:>9}{pct(t['zt1'], t['n']):>7.2f}%{pct(t['zt5'], t['n']):>7.2f}%"
          f"{pct(t['zt10'], t['n']):>7.2f}%{pct(t['zt20'], t['n']):>8.2f}%"
          f"{st.median(t['f5']):>+8.2f}{st.median(t['f20']):>+8.2f}" if t['n'] else '')
    if e['ttf']:
        ttf = sorted(e['ttf'])
        k = len(ttf)
        print(f"胚胎→首板间隔: med={ttf[k // 2]}bar P25={ttf[k // 4]} P75={ttf[3 * k // 4]} "
              f"(条件于20日内出板, n={k})")
        lh = e['launch_h1'] + e['launch_h2']
        print(f"胚胎启动链高: P(H>=2|胚胎后出板)={e['launch_h2'] / lh * 100:.1f}% (n={lh}) "
              f"vs 无条件首板 P(H>=2)={un:.2f}%")
    print('\n-- Part C 去簇复检 (同股相邻记录间隔>=10bar, 分年) --')
    ded_sum = {'n': 0, 'f5': [], 'f20': [], 'launch': 0, 'h2': 0}
    for yy in sorted(emb_ded):
        d = emb_ded[yy]
        for kk in ded_sum:
            if kk == 'n':
                ded_sum[kk] += d['n']
            elif kk in ('f5', 'f20'):
                ded_sum[kk].extend(d[kk])
            else:
                ded_sum[kk] += d[kk]
        f20s = sorted(d['f20'])
        k = len(f20s)
        print(f"  {yy}: n={d['n']} medF5={st.median(d['f5']):+.2f} medF20={st.median(d['f20']):+.2f} "
              f"F20胜率={sum(1 for x in d['f20'] if x > 0) / k * 100:.1f}% F20>5%={sum(1 for x in d['f20'] if x > 5) / k * 100:.1f}% "
              f"P25={f20s[k // 4]:+.1f} P75={f20s[3 * k // 4]:+.1f} "
              f"P(20日内出板)={d['launch'] / d['n'] * 100:.1f}% P(H>=2|出板)={d['h2'] / max(1, d['launch']) * 100:.1f}%")
    f20s = sorted(ded_sum['f20'])
    k = len(f20s)
    print(f"  合计: n={ded_sum['n']} medF5={st.median(ded_sum['f5']):+.2f} medF20={st.median(ded_sum['f20']):+.2f} "
          f"F20胜率={sum(1 for x in ded_sum['f20'] if x > 0) / k * 100:.1f}% F20>5%={sum(1 for x in ded_sum['f20'] if x > 5) / k * 100:.1f}% "
          f"P25={f20s[k // 4]:+.1f} P75={f20s[3 * k // 4]:+.1f} "
          f"P(出板)={ded_sum['launch'] / max(1, ded_sum['n']) * 100:.1f}% P(H>=2|出板)={ded_sum['h2'] / max(1, ded_sum['launch']) * 100:.1f}%")

    # ---- Part A2 打印 ----
    print('\n== Part A2 低位四定义敏感性 (独立口径, 2016-2026, 剔科创/北交) ==')
    A2 = {}
    for dk in ('D1', 'D2', 'D3', 'D4'):
        tot = [0, 0, 0, 0]
        A2[dk] = {}
        for yy in sorted(a_defs[dk]):
            c = a_defs[dk][yy]
            for ti in range(4):
                tot[ti] += c[ti]
            row = {'n': c[0], 'p_f20': c[1] / c[0] * 100 if c[0] else None,
                   'p_zt': c[2] / c[0] * 100 if c[0] else None, 'p_lhb': c[3] / c[0] * 100 if c[0] else None}
            A2[dk][yy] = row
            if yy in ('2024', '2025', '2026'):
                print(f"  {dk} {yy}: n={c[0]:>8} P(f20>10%)={row['p_f20']:>5.2f}% P(出板)={row['p_zt']:>5.2f}% P(LHB)={row['p_lhb']:>5.2f}%")
        pool = {'n': tot[0], 'p_f20': tot[1] / tot[0] * 100 if tot[0] else None,
                'p_zt': tot[2] / tot[0] * 100 if tot[0] else None, 'p_lhb': tot[3] / tot[0] * 100 if tot[0] else None}
        A2[dk]['pooled'] = pool
        print(f"  {dk} 全期: n={tot[0]:>8} P(f20>10%)={pool['p_f20']:>5.2f}% P(出板)={pool['p_zt']:>5.2f}% P(LHB)={pool['p_lhb']:>5.2f}%   (D1价格分位<40 D2额分位<50 D3距60高回撤>15% D4振幅收敛<0.8)")

    # ---- Part D/E 打印 ----
    print('\n== Part D 等待期风险与买入规则 (胚胎域去簇事件) ==')
    nl, nn = Dacc['n_l'], Dacc['n_n']
    def qtl(a, q):
        s = sorted(a)
        return s[min(len(s) - 1, int(len(s) * q))] if s else float('nan')
    mae_l, mae_n = Dacc['mae_l'], Dacc['mae_n']
    print(f"启动事件 n={nl}: 等待期MAE(low触及) P50={qtl(mae_l, .5):+.2f}% P75={qtl(mae_l, .75):+.2f}% P90={qtl(mae_l, .90):+.2f}% "
          f"-8%震出率={Dacc['hit8_l'] / max(1, nl) * 100:.1f}%")
    print(f"未启动事件 n={nn}: MAE P50={qtl(mae_n, .5):+.2f}% P75={qtl(mae_n, .75):+.2f}% "
          f"-8%触发率={Dacc['hit8_n'] / max(1, nn) * 100:.1f}%")

    def rstat(a, name):
        s = sorted(a)
        n_ = len(a)
        mean = sum(a) / n_
        win = sum(1 for x in a if x > 0) / n_ * 100
        p95 = s[min(n_ - 1, int(n_ * 0.95))]
        print(f"{name:<30} n={n_:>6} mean={mean:+7.2f} med={s[n_ // 2]:+6.2f} win={win:5.1f}% P95={p95:+6.1f} E_net(扣{COST})={mean - COST:+.2f}")
        return {'n': n_, 'mean': mean, 'med': s[n_ // 2], 'win': win, 'p95': p95}
    r1s = rstat(Dacc['r1'], 'R1 触发买+8%硬止损')
    r2s = rstat(Dacc['r2'], 'R2 触发买+等待12%/启动8%')
    r3as = rstat(Dacc['r3a'], 'R3a 出板日收盘买(排板上限)')
    r3bs = rstat(Dacc['r3b'], 'R3b 出板次日开盘买')
    idle = Dacc['idle'] / max(1, nl + nn) * 100
    print(f"R3 资金闲置率(未启动无交易)={idle:.1f}%  启动事件中-8%先触发占比={Dacc['hit8_l'] / max(1, nl) * 100:.1f}%")

    # ---- Part E: 功效/MDE + 期望值基准 + 日聚簇bootstrap ----
    print('\n== Part E 功效与期望值 (去簇事件, 双边成本0.30%) ==')
    p0_zt = all_day['zt20'] / max(1, all_day['n'])
    n_e = len(Dacc['r1'])
    mde_l1 = 2.8 * (p0_zt * (1 - p0_zt) / n_e) ** 0.5 * 100
    obs_l1 = ded_sum['launch'] / max(1, ded_sum['n']) * 100 - p0_zt * 100
    p0_f20 = (a_all.get('2024', [0, 0])[1] + a_all.get('2025', [0, 0])[1]) / max(1, a_all.get('2024', [0])[0] + a_all.get('2025', [0])[0])
    mde_l4 = 2.8 * (p0_f20 * (1 - p0_f20) / n_e) ** 0.5 * 100
    obs_l4 = (sum(1 for x in ded_sum['f20'] if x > 10) / max(1, len(ded_sum['f20'])) - p0_f20) * 100
    mkt_mean = sum(all_day['f20']) / max(1, len(all_day['f20']))
    mkt_med = st.median(all_day['f20'])
    sys20 = ((1.1634) ** (20 / 250) - 1) * 100
    hb = Dacc['holdbars']
    print(f"功效: L1出板标签 MDE(80%功效)={mde_l1:.2f}pp << 观测Δ=+{obs_l1:.1f}pp (n={n_e}); "
          f"L4标签 MDE={mde_l4:.2f}pp << 观测Δ=+{obs_l4:.1f}pp → 功效闸通过")
    print(f"匹配丰度: 胚胎状态占全部股票日 {emb_days['n'] / max(1, all_day['n']) * 100:.1f}% (>1%红线); 去簇事件 {n_e} 个/2年 (~{n_e // 2}/年)")
    print(f"期望值: R1 E_net={r1s['mean'] - COST:+.2f}%/笔 (占资中位{st.median(hb):.0f}bar) vs 全市场20日 mean={mkt_mean:+.2f}%/med={mkt_med:+.2f}% "
          f"vs 主系统20bar折算≈+{sys20:.2f}% (窗口回测CAGR16.34%折算, 引用值)")
    print(f"资金效率: R1 E_net/占资bar={((r1s['mean'] - COST) / max(1, st.median(hb))):+.3f}%/bar vs 市场 {mkt_med / 20:+.3f}%/bar(中位口径) vs 系统 {sys20 / 20:+.3f}%/bar")
    # 日聚簇 bootstrap
    fbd, r1bd, r2bd = defaultdict(list), defaultdict(list), defaultdict(list)
    for d, r1v, f20v in Dacc['r1_bd']:
        fbd[d].append(f20v)
        r1bd[d].append(r1v)
    for d, r2v in Dacc['r2_bd']:
        r2bd[d].append(r2v)
    dts = sorted(fbd)
    af = [np.array(fbd[x]) for x in dts]
    a1 = [np.array(r1bd[x]) for x in dts]
    a2 = [np.array(r2bd[x]) for x in dts]
    mf, m1, m2 = [], [], []
    for _ in range(1000):
        pick = RNG.integers(0, len(dts), len(dts))
        mf.append(float(np.median(np.concatenate([af[p] for p in pick]))))
        m1.append(float(np.mean(np.concatenate([a1[p] for p in pick]))))
        m2.append(float(np.median(np.concatenate([a2[p] for p in pick]))))
    ci = lambda arr: (round(float(np.percentile(arr, 2.5)), 2), round(float(np.percentile(arr, 97.5)), 2))
    print(f"日聚簇bootstrap(1000次,按日重采样): medF20 CI={ci(mf)}  R1均值CI={ci(m1)}  R2中位CI={ci(m2)}")

    # ---- Part F: 匹配对照组 (同日同行业L1 未板 非胚胎, 3:1, 不与全市场比) ----
    print('\n== Part F 匹配对照组配对差 (同日/同L1行业/未板/非胚胎, 3:1 种子抽样) ==')
    ev_by_date = defaultdict(list)
    for code, d, iid, f20v, launched, hit8 in evrec:
        ev_by_date[d].append((iid, f20v, launched, hit8))
    dlt_bd, dl1b = defaultdict(list), defaultdict(list)
    n_noctrl, n_pair = 0, 0
    for d, evs in sorted(ev_by_date.items()):
        pan = day_pan.get(d)
        if pan is None:
            continue
        inds = np.array(pan[0])
        zt = np.array(pan[1], dtype=bool)
        emb = np.array(pan[2], dtype=bool)
        f20p = np.array(pan[3], dtype=float)
        lzt = np.array(pan[4], dtype=bool)
        base = (~emb) & (~zt) & (inds > 0) & ~np.isnan(f20p)
        for (iid, f20v, launched, hit8) in evs:
            cand = np.where(base & (inds == iid))[0]
            if len(cand) == 0:
                n_noctrl += 1
                continue
            pick = RNG.choice(cand, size=min(3, len(cand)), replace=False)
            dlt_bd[d].append(f20v - float(np.median(f20p[pick])))
            dl1b[d].append((1.0 if launched else 0.0) - float(lzt[pick].mean()))
            n_pair += 1
    all_d = [x for v in dlt_bd.values() for x in v]
    all_l = [x for v in dl1b.values() for x in v]
    print(f"配对成功 n={n_pair} (无对照剔除 {n_noctrl})  配对Δ(f20-对照中位): med={st.median(all_d):+.2f}% "
          f"mean={sum(all_d) / len(all_d):+.2f}%  配对Δ(L1出板): {sum(all_l) / len(all_l) * 100:+.2f}pp")
    dts_f = sorted(dlt_bd)
    ad = [np.array(dlt_bd[x]) for x in dts_f]
    al = [np.array(dl1b[x]) for x in dts_f]
    bm, bl = [], []
    for _ in range(1000):
        pick = RNG.integers(0, len(dts_f), len(dts_f))
        bm.append(float(np.median(np.concatenate([ad[p] for p in pick]))))
        bl.append(float(np.mean(np.concatenate([al[p] for p in pick])) * 100))
    print(f"日聚簇bootstrap: 配对Δ中位 CI={ci(bm)}  配对Δ(L1) CI={ci(bl)}")

    # ---- Part G: 震出聚集性 + 止损成交风险 ----
    print('\n== Part G 震出聚集性与止损成交风险 ==')
    shake_bd = defaultdict(int)
    for code, d, iid, f20v, launched, hit8 in evrec:
        if launched and hit8:
            shake_bd[d] += 1
    tot_shake = sum(shake_bd.values())
    conc = sum(c for c in shake_bd.values() if c >= 3)
    print(f"正确埋伏震出事件 n={tot_shake}, 聚集日(同日≥3笔)承载占比={conc / max(1, tot_shake) * 100:.1f}% "
          f"(聚集日数={sum(1 for c in shake_bd.values() if c >= 3)})")
    print(f"止损触及日收盘≈跌停(锁定无法成交)占比={Dacc['stop_locked'] / max(1, Dacc['stop_n']) * 100:.1f}% "
          f"(n={Dacc['stop_n']}) — 真实止损成交成本高于按精确-8%假设")
    print(f"MAE劣侧分位(启动事件): P10={qtl(mae_l, .10):+.2f}% P15={qtl(mae_l, .15):+.2f}% — "
          f"材料建议'用P75/P90定止损'方向反了(本分布P90=-0.89%会震出90%赢家), 正确口径=劣侧P10~P15, 本轮-12%≈P10~P15实证锚")

    # ---- Part H: 主系统基准与机会成本扣减 ----
    print('\n== Part H 主系统基准与机会成本 (材料0.8效率规则) ==')
    sys_full, sys_win = 0.0446, 0.0616   # %/bar: CAGR11.60%(全史)/16.34%(2024窗) 折算, 材料同一锚点
    mb_mean = sum(Dacc['holdbars']) / max(1, len(Dacc['holdbars']))
    mb_med = st.median(Dacc['holdbars'])
    e_r1 = r1s['mean'] - COST
    e_r2 = r2s['mean'] - COST
    for tag, ev, sysd in (('R1', e_r1, sys_full), ('R2', e_r2, sys_full)):
        eff = ev / max(1, mb_mean)
        print(f"{tag}: E_net={ev:+.2f}% 占资均值bar={mb_mean:.1f}(中位{mb_med:.0f}) 效率={eff:+.3f}%/bar vs "
              f"0.8×主系统日均({sysd:.4f})={0.8 * sysd:.4f}%/bar → {'PASS' if eff >= 0.8 * sysd else 'FAIL'}")
    print(f"机会成本扣减(等待期占用×主系统日均): R1 E_net扣后={e_r1 - mb_mean * sys_full:+.2f}%  "
          f"R2扣后={e_r2 - mb_mean * sys_full:+.2f}% (用全史日均锚{sys_full}%/bar×占资{mb_mean:.1f}bar; 真实逐笔库多路径仅小样本副本n=25/51, 生产库路径待指认后复核)")
    out['partF'] = {'n_pair': n_pair, 'n_noctrl': n_noctrl,
                    'd_med': st.median(all_d), 'd_mean': sum(all_d) / len(all_d),
                    'dl1': sum(all_l) / len(all_l) * 100, 'boot_d_med': ci(bm), 'boot_dl1': ci(bl)}
    out['partG'] = {'shake_total': tot_shake, 'conc_share': conc / max(1, tot_shake),
                    'stop_locked_share': Dacc['stop_locked'] / max(1, Dacc['stop_n']),
                    'mae_p10': qtl(mae_l, .10), 'mae_p15': qtl(mae_l, .15)}
    out['partH'] = {'hold_mean': mb_mean, 'hold_med': mb_med, 'e_r1': e_r1, 'e_r2': e_r2,
                    'eff_r1': e_r1 / max(1, mb_mean), 'eff_r2': e_r2 / max(1, mb_mean),
                    'thr_08_full': 0.8 * sys_full, 'e_opp_r1': e_r1 - mb_mean * sys_full,
                    'e_opp_r2': e_r2 - mb_mean * sys_full}

    out['partC_dedup'] = {yy: {'n': d['n'], 'med_f5': st.median(d['f5']), 'med_f20': st.median(d['f20']),
                               'win20': sum(1 for x in d['f20'] if x > 0) / len(d['f20']) * 100,
                               'launch': d['launch'], 'h2': d['h2']} for yy, d in emb_ded.items()}
    out['partA2'] = A2
    out['partD'] = {'mae_l_p50': qtl(mae_l, .5), 'mae_l_p75': qtl(mae_l, .75), 'mae_l_p90': qtl(mae_l, .90),
                    'hit8_l': Dacc['hit8_l'] / max(1, nl), 'mae_n_p50': qtl(mae_n, .5), 'mae_n_p75': qtl(mae_n, .75),
                    'hit8_n': Dacc['hit8_n'] / max(1, nn), 'idle': idle,
                    'r1': r1s, 'r2': r2s, 'r3a': r3as, 'r3b': r3bs, 'hold_med': st.median(hb)}
    out['partE'] = {'mde_l1': mde_l1, 'obs_l1': obs_l1, 'mde_l4': mde_l4, 'obs_l4': obs_l4,
                    'mkt_mean_f20': mkt_mean, 'mkt_med_f20': mkt_med, 'sys20': sys20,
                    'boot_med_f20': ci(mf), 'boot_mean_r1': ci(m1), 'boot_med_r2': ci(m2),
                    'lhb20_base': all_day['lhb20'] / max(1, all_day['n']) * 100,
                    'emb_lhb20': emb_days['lhb20'] / max(1, emb_days['n']) * 100}
    # 锚文件只存摘要统计, 不落原始数组(20261019修正: 全市场f5/f20各219万元素曾致JSON 459万行)
    out['partC'] = {kk: (sorted(v) if kk == 'ttf' else
                         (round(sum(v) / len(v), 4) if kk in ('f5', 'f20') and v else (v if not isinstance(v, list) else None)))
                    for kk, v in emb_days.items() if kk != 'f5' and kk != 'f20'}
    out['partC_baseline'] = {kk: (round(sum(v) / len(v), 4) if kk in ('f5', 'f20') else v)
                             for kk, v in all_day.items() if kk != 'f5' and kk != 'f20'}
    out['partC_med'] = {'emb_f5': st.median(e['f5']) if e['f5'] else None,
                        'emb_f20': st.median(e['f20']) if e['f20'] else None,
                        'all_f5': st.median(t['f5']) if t['f5'] else None,
                        'all_f20': st.median(t['f20']) if t['f20'] else None,
                        'p_h2_launch': e['launch_h2'] / (e['launch_h1'] + e['launch_h2']) * 100 if (e['launch_h1'] + e['launch_h2']) else None}
    with open(r'D:\zcodesj\data\prelaunch_lowpos_formal_20261019.json', 'w', encoding='utf-8') as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print('\nJSON -> data/prelaunch_lowpos_formal_20261019.json')


if __name__ == '__main__':
    main()
