# -*- coding: utf-8 -*-
"""11/02盈利法扩容批 六源可实现口径统一审计 (与复现批审计同一把尺子).
对象: tandi探底企稳/breakout突破共振/dibo低波/starter低位启动/industry行业动量(hydl前提)/bpep价值系(bp_ep_engine+bpep_weekly同前提).
     满弦/双优跳过: 属蓄势线, 已有wf权威回测+双锚守卫, 本转录口径不足以复判(如实标注).
口径: qfq收盘→收盘, T日收盘算因子, 前向5/10/20日; 宇宙=主板+创业板, 剔688/689/北证+历史ST, 价帽按各法合同;
      条件逐字转录自 sel11.py(build_topup.py 上游) + build_topup.py bp_ep排名.
限制(如实): 满弦/双优未考; hy_tail臂未考(行业RS排名联动); 动态held去重未模拟(对方法是宽标尺)."""
import sys, sqlite3, numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
con = sqlite3.connect(QFQ, uri=True, timeout=300)
rows = con.execute(
    "select code,date,open,close,volume,amount from stock_daily_meta_qfq where date>='20150101' order by date"
).fetchall()
con.close()
codes_all = sorted({r[0] for r in rows})
ci = {c: i for i, c in enumerate(codes_all)}
dates_all = sorted({str(r[1]) for r in rows})
di = {d: j for j, d in enumerate(dates_all)}
nc, nd = len(codes_all), len(dates_all)
C = np.full((nd, nc), np.nan)
O = np.full((nd, nc), np.nan)
V = np.full((nd, nc), np.nan)
A = np.full((nd, nc), np.nan)
for code, d, o, c, v, a in rows:
    j, k = di[str(d)], ci[code]
    O[j, k] = float(o)
    C[j, k] = float(c)
    V[j, k] = float(v) if v else np.nan
    A[j, k] = float(a) if a else np.nan
print('matrix:', C.shape, flush=True)

uni_codes = [c for c in codes_all if c[0] in '036' and not c.startswith('688') and not c.startswith('689')]
u_idx = np.array([ci[c] for c in uni_codes])
U_MASK = np.zeros(nc, dtype=bool)
U_MASK[u_idx] = True

scon = sqlite3.connect('file:D:/zcodesj/data/st_history.db?mode=ro', uri=True, timeout=60)
st_rows = scon.execute("select code, date, is_st from st_daily where is_st=1").fetchall()
scon.close()
ST = np.zeros((nd, nc), dtype=bool)
for code, d, s in st_rows:
    j, k = di.get(str(d)), ci.get(str(code)[-6:])
    if j is not None and k is not None:
        ST[j, k] = True

PX_OK = (C > 0.5) & (C <= 30.0) & ~np.isnan(C)
UNI = U_MASK[None, :] & PX_OK & (~ST)
UNI_250 = UNI & (pd.DataFrame(C).notna().rolling(250, min_periods=250).max().values.astype(bool))

bcon = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True, timeout=300)
brows = bcon.execute(
    "select ts_code, trade_date, pe_ttm, pb from daily_basic where trade_date>='20150101'").fetchall()
bcon.close()
PE = np.full((nd, nc), np.nan)
PB = np.full((nd, nc), np.nan)
for ts, d, pe, pb in brows:
    k, j = ci.get(str(ts)[:6]), di.get(str(d))
    if k is None or j is None:
        continue
    for name, val in (('pe', pe), ('pb', pb)):
        if val in (None, ''):
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if name == 'pe':
            PE[j, k] = val
        else:
            PB[j, k] = val
print('PE/PB mapped', flush=True)

icon = sqlite3.connect('file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro', uri=True, timeout=30)
ind_pairs = icon.execute("select code,l1 from l1_member").fetchall()
icon.close()
IND = {str(k)[-6:]: v for k, v in ind_pairs}
ind_names = sorted(set(IND.values()))
ind_id = {v: i for i, v in enumerate(ind_names)}
INDV = np.full(nc, -1, dtype=np.int32)
for c, v in IND.items():
    k = ci.get(c)
    if k is not None:
        INDV[k] = ind_id.get(v, -1)

R1 = C / np.roll(C, 1, axis=0) - 1
R1[:1] = np.nan
R20 = C / np.roll(C, 20, axis=0) - 1
R20[:20] = np.nan
MA20 = pd.DataFrame(C).rolling(20, min_periods=20).mean().values
V20C = pd.DataFrame(V).rolling(20, min_periods=20).mean().values   # 含当日(tandi合同v20=v[-20:])
V5B = pd.DataFrame(V).rolling(5, min_periods=5).mean().shift(1).values  # 不含当日(breakout合同vol5=v[-6:-1])
AMT20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().values
HI250 = pd.DataFrame(C).rolling(250, min_periods=250).max().values
LO250 = pd.DataFrame(C).rolling(250, min_periods=250).min().values
HI20 = pd.DataFrame(C).rolling(20, min_periods=20).max().shift(1).values  # 前二十日高(不含当日)
VOL60 = pd.DataFrame(R1).rolling(60, min_periods=60).std(ddof=0).values * (250 ** 0.5)
print('derived done', flush=True)

F5 = np.full_like(C, np.nan)
F10 = np.full_like(C, np.nan)
F20 = np.full_like(C, np.nan)
F5[:-5] = C[5:] / C[:-5] - 1
F10[:-10] = C[10:] / C[:-10] - 1
F20[:-20] = C[20:] / C[:-20] - 1

# 行业成员索引(用于逐日行业均值)
ind_members = {}
for k in np.unique(INDV[INDV >= 0]):
    ind_members[int(k)] = np.where(INDV == k)[0]

def ind_daily_mean(t, arr):
    out = np.full(len(ind_members) + 1, np.nan)
    means = {}
    for ik, mem in ind_members.items():
        vals = arr[t, mem]
        vals = vals[~np.isnan(vals)]
        if len(vals) >= 3:
            means[ik] = float(vals.mean())
    return means

METHODS = {}

def m_tandi(t):
    if t < 61:
        return None
    ret20 = R20[t]
    ok = (UNI[t] & ~np.isnan(ret20) & (ret20 <= -0.15)
          & (C[t] > O[t])
          & (V[t] < 0.9 * V20C[t])
          & ~np.isnan(F5[t]))
    return ok, ret20 * (-1.0), 10  # 排序=ret20升序(最深优先) → key=-ret20

METHODS['tandi'] = ('探底缩量企稳(扩容批)', m_tandi)

def m_breakout(t):
    if t < 61:
        return None
    prior_high = HI20[t]
    chg1 = R1[t]
    ok0 = (UNI[t] & (C[t] >= 2.0) & ~np.isnan(prior_high) & (C[t] >= prior_high)
           & ~np.isnan(V5B[t]) & (V5B[t] > 0) & (V[t] >= 1.5 * V5B[t])
           & (chg1 > 0) & ~np.isnan(F5[t]))
    if ok0.sum() < 1:
        return ok0, A[t], 10
    mkt1d = np.nanmean(R1[t][U_MASK[t] & ~np.isnan(R1[t])])
    ind1 = ind_daily_mean(t, R1)
    # 向量化: 行业1d均值>大盘
    ind_ok = np.zeros(nc, dtype=bool)
    for ik, mem in ind_members.items():
        v = ind1.get(ik)
        if v is not None and v > mkt1d:
            ind_ok[mem] = True
    ok = ok0 & ind_ok
    if ok.sum() == 0:
        return ok, A[t], 10
    # 同行业仅取1只: 按amt20降序逐行业取首只
    idx = np.where(ok)[0]
    order = idx[np.argsort(-A[t][idx], kind='stable')]
    picked, used = [], set()
    for k_i in order:
        ik = INDV[k_i]
        if ik in used:
            continue
        used.add(ik)
        picked.append(k_i)
        if len(picked) >= 10:
            break
    pm = np.zeros(nc, dtype=bool)
    pm[picked] = True
    return pm, A[t], 10

METHODS['breakout'] = ('突破共振(扩容批)', m_breakout)

def m_dibo(t):
    if t < 62:
        return None
    v = VOL60[t]
    base = UNI[t] & ~np.isnan(v)
    if base.sum() < 100:
        return None
    cut = np.nanquantile(v[base], 1.0 / 3.0)
    ok = (base & (v <= cut) & ~np.isnan(R20[t]) & ~np.isnan(F5[t]))
    return ok, R20[t], 10

METHODS['dibo'] = ('低波修复(扩容批)', m_dibo)

def m_starter(t):
    if t < 251:
        return None
    rng = HI250[t] - LO250[t]
    with np.errstate(invalid='ignore', divide='ignore'):
        pos = (C[t] - LO250[t]) / rng * 100
    chg1 = R1[t] * 100
    m20 = R20[t] * 100
    ok = (UNI_250[t] & ~np.isnan(pos) & (pos <= 30)
          & ~np.isnan(chg1) & (chg1 >= 2) & (chg1 <= 5.5)
          & ~np.isnan(m20) & (m20 >= -10) & (m20 <= 6)
          & ~np.isnan(F5[t]))
    return ok, AMT20[t], 10

METHODS['starter'] = ('低位启动(扩容批)', m_starter)

def m_industry(t):
    if t < 21:
        return None
    r = R20[t]
    means = ind_daily_mean(t, R20)
    if len(means) < 3:
        return None
    top3 = sorted(means.items(), key=lambda x: -x[1])[:3]
    ok = np.zeros(nc, dtype=bool)
    key = np.full(nc, np.nan)
    for ik, mv in top3:
        mem = ind_members[ik]
        r20v = r[mem]
        valid = ~np.isnan(r20v)
        mvv = np.where(valid, r20v, -np.inf)
        take = np.argsort(-mvv)[:3]
        for ti in take:
            if valid[ti]:
                ok[mem[ti]] = True
                key[mem[ti]] = r20v[ti]
    ok &= ~np.isnan(F5[t])
    return ok, key, 10

METHODS['industry'] = ('行业动量(扩容批,hydl前提)', m_industry)

def m_bpep(t):
    if t < 61:
        return None
    with np.errstate(divide='ignore', invalid='ignore'):
        ep = 1.0 / PE[t]
        bp = 1.0 / PB[t]
    base = UNI[t] & (PE[t] > 0) & (PB[t] > 0) & ~np.isnan(F5[t])
    if base.sum() < 100:
        return None
    ep_r = pd.Series(ep[base]).rank(pct=True).values
    bp_r = pd.Series(bp[base]).rank(pct=True).values
    ok = np.zeros(nc, dtype=bool)
    idx = np.where(base)[0]
    score = ep_r + bp_r
    order = idx[np.argsort(-score, kind='stable')][:10]
    ok[order] = True
    return ok, np.zeros(nc), 10  # bpep已在ok内截断Top10, key不参与排序

METHODS['bpep'] = ('bp_ep价值系(扩容批:引擎+周滚动同前提)', m_bpep)

recs = []
for t in range(nd):
    pool_mask = UNI[t] & ~np.isnan(F5[t])
    if pool_mask.sum() < 500:
        continue
    p5 = float(np.nanmean(F5[t][pool_mask]))
    p10 = float(np.nanmean(F10[t][pool_mask]))
    p20 = float(np.nanmean(F20[t][pool_mask]))
    for mk, (cn, fn) in METHODS.items():
        res = fn(t)
        if res is None:
            continue
        ok, key, quota = res
        idx = np.where(ok)[0]
        if len(idx) == 0:
            recs.append({'date': dates_all[t], 'method': mk, 'n_picks': 0,
                         'pick_f5': np.nan, 'pick_f10': np.nan, 'pick_f20': np.nan,
                         'pool_f5': p5, 'pool_f10': p10, 'pool_f20': p20})
            continue
        if mk == 'bpep':
            pick_idx = idx  # bpep已在ok内截断为Top10
        else:
            kk = key[idx]
            pick_idx = idx[np.argsort(-kk)[:quota]]
        recs.append({'date': dates_all[t], 'method': mk, 'n_picks': int(len(pick_idx)),
                     'pick_f5': float(np.nanmean(F5[t][pick_idx])),
                     'pick_f10': float(np.nanmean(F10[t][pick_idx])),
                     'pick_f20': float(np.nanmean(F20[t][pick_idx])),
                     'pool_f5': p5, 'pool_f10': p10, 'pool_f20': p20})

df = pd.DataFrame(recs)
df['year'] = df['date'].str[:4]
df.to_csv(r'D:\zcodesj\data\_expansion_audit_daily_20261105.csv', index=False, encoding='utf-8-sig')
print('rows:', len(df), flush=True)

print('\n================ 全期汇总 (前向收益%, 选票-池=差) ================')
for mk, (cn, _) in METHODS.items():
    g = df[(df['method'] == mk) & (df['n_picks'] > 0)]
    if len(g) == 0:
        print(f'{cn}: 零命中日')
        continue
    print(f'\n{cn}  有票日={len(g)}  平均名额={g["n_picks"].mean():.1f}')
    for h in (5, 10, 20):
        pk, pl = g[f'pick_f{h}'].mean() * 100, g[f'pool_f{h}'].mean() * 100
        print(f'  f{h}: 选票{pk:+.2f}% vs 池{pl:+.2f}%  差{pk-pl:+.2f}pp')

print('\n================ 分年差(pp) ================')
for mk, (cn, _) in METHODS.items():
    g = df[(df['method'] == mk) & (df['n_picks'] > 0)].copy()
    if len(g) == 0:
        continue
    print(f'\n-- {cn} --')
    for h in (5, 10, 20):
        d = (g[f'pick_f{h}'] - g[f'pool_f{h}']) * 100
        yy = d.groupby(g['year']).mean().round(2)
        print(f'  f{h}:', dict(yy))
