# -*- coding: utf-8 -*-
"""11/01盈利模式复现批 六法可实现口径统一审计 (与ML胜率复刻同一把尺子).
口径: qfq收盘→收盘, T日收盘算因子(T收盘数据全可得, 无未来函数), 前向5/10/20日;
      宇宙=主板+创业板, 剔688/689/北证+历史ST(st_daily按日), 0.5-30元(各方法uni()自带);
      各方法条件逐字转录自 analysis/evrank_original_20261021/session_scripts_20261101/
      (rescan_all.py / rescan3.py / add_highwin.py); 名额与排序键同合同.
检验: 每日按合同条件+排序取Top N, 前向收益 vs 同日同宇宙池均值; 全期+分年.
限制(如实): ①主线方向法行业映射用当前快照(无历史映射库) ②挂单价为收盘价口径(实盘高挂0.5%更差)
      ③停牌/一字板等不可成交情形未模拟=对方法是宽标尺.
落盘: data/_replica_audit_daily_20261105.csv"""
import sys, sqlite3, numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
con = sqlite3.connect(QFQ, uri=True, timeout=300)
rows = con.execute(
    "select code,date,close,volume,amount from stock_daily_meta_qfq where date>='20150101' order by date"
).fetchall()
con.close()
codes_all = sorted({r[0] for r in rows})
ci = {c: i for i, c in enumerate(codes_all)}
dates_all = sorted({str(r[1]) for r in rows})
di = {d: j for j, d in enumerate(dates_all)}
nc, nd = len(codes_all), len(dates_all)
C = np.full((nd, nc), np.nan, dtype=np.float64)
V = np.full((nd, nc), np.nan, dtype=np.float64)
A = np.full((nd, nc), np.nan, dtype=np.float64)
for code, d, c, v, a in rows:
    C[di[str(d)], ci[code]] = float(c)
    V[di[str(d)], ci[code]] = float(v or np.nan)
    A[di[str(d)], ci[code]] = float(a or np.nan)
print('matrix:', C.shape, flush=True)

# 宇宙掩码: 主板+创业板
uni_codes = [c for c in codes_all if c[0] in '036' and not c.startswith('688') and not c.startswith('689')]
u_idx = np.array([ci[c] for c in uni_codes])
U_MASK = np.zeros(nc, dtype=bool)
U_MASK[u_idx] = True

# 历史ST: st_daily(code,date,is_st) 按日
scon = sqlite3.connect('file:D:/zcodesj/data/st_history.db?mode=ro', uri=True, timeout=60)
st_rows = scon.execute("select code, date, is_st from st_daily where is_st=1").fetchall()
scon.close()
ST = np.zeros((nd, nc), dtype=bool)
n_st = 0
for code, d, s in st_rows:
    j = di.get(str(d))
    k = ci.get(str(code)[-6:])
    if j is not None and k is not None:
        ST[j, k] = True
        n_st += 1
print('ST rows mapped:', n_st, flush=True)

# 每日宇宙有效掩码(价帽+非ST+主板创业板)
PX_OK = (C > 0.5) & (C <= 30.0) & ~np.isnan(C)
UNI = U_MASK[None, :] & PX_OK & (~ST)

# 历史PE宽表 (fundamental_data.daily_basic, PIT当日PE)
bcon = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True, timeout=300)
brows = bcon.execute(
    "select ts_code, trade_date, pe_ttm from daily_basic where trade_date>='20150101'").fetchall()
bcon.close()
PE = np.full((nd, nc), np.nan, dtype=np.float64)
for ts, d, pe in brows:
    k = ci.get(str(ts)[:6])
    j = di.get(str(d))
    if k is not None and j is not None and pe not in (None, ''):
        try:
            PE[j, k] = float(pe)
        except (TypeError, ValueError):
            continue
print('PE mapped cells:', int(np.sum(~np.isnan(PE))), flush=True)

# 行业映射(当前快照, 近似)
icon = sqlite3.connect('file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro', uri=True, timeout=30)
ind_pairs = icon.execute("select code,l1 from l1_member").fetchall()
icon.close()
IND = {}
for k, v in ind_pairs:
    IND[str(k)[-6:]] = v
ind_names = sorted(set(IND.values()))
ind_id = {v: i for i, v in enumerate(ind_names)}
INDV = np.full(nc, -1, dtype=np.int32)
for c, v in IND.items():
    k = ci.get(c)
    if k is not None:
        INDV[k] = ind_id.get(v, -1)

# 公共派生矩阵
R20 = C / np.roll(C, 20, axis=0) - 1          # 20日收益(止于当前行), 前20行为nan
R20[:20, :] = np.nan
MA20 = pd.DataFrame(C).rolling(20, min_periods=20).mean().values
V20 = pd.DataFrame(V).rolling(20, min_periods=20).mean().shift(1).values  # 合同v20=v[-21:-1]=不含当日
V60 = pd.DataFrame(V).rolling(60, min_periods=60).mean().values
AMT20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().values
HI250 = pd.DataFrame(C).rolling(250, min_periods=250).max().values
LO250 = pd.DataFrame(C).rolling(250, min_periods=250).min().values
HI10 = pd.DataFrame(C).rolling(10, min_periods=10).max().values
LO10 = pd.DataFrame(C).rolling(10, min_periods=10).min().values
V5 = pd.DataFrame(V).rolling(5, min_periods=5).mean().values
UP250 = (np.roll(C, 250, axis=0) != 0)
with np.errstate(divide='ignore', invalid='ignore'):
    RET = C / np.roll(C, 1, axis=0) - 1
RET[:1, :] = np.nan
UP = (RET > 0).astype(float)
UP[np.isnan(RET)] = np.nan
UP250R = pd.DataFrame(UP).rolling(250, min_periods=200).mean().values
print('derived done', flush=True)

F5 = np.full_like(C, np.nan)
F10 = np.full_like(C, np.nan)
F20 = np.full_like(C, np.nan)
F5[:-5] = C[5:] / C[:-5] - 1
F10[:-10] = C[10:] / C[:-10] - 1
F20[:-20] = C[20:] / C[:-20] - 1

def rolling_sample(arr, t, lo, hi_ex, step):
    return arr[t - lo:t - hi_ex + 1:step]

# ===== 每方法当日(条件, 排序键, 名额) =====
def cond_quantrank(t):
    # 270日滚动20日窗(每10日采样, 窗止于t-10..t-250): 胜率>=60% & 期望>=1% & 20日均额>=1亿
    if t < 270:
        return None, None, 9
    samp = R20[t - 250:t - 9:10]                     # 行=t-250, t-240, ..., t-10 (25窗)
    valid = ~np.isnan(samp)
    nv = valid.sum(axis=0)
    wins = ((samp > 0) & valid).sum(axis=0)
    exps = np.nanmean(samp, axis=0)
    with np.errstate(invalid='ignore', divide='ignore'):
        wr = np.where(nv >= 8, wins / np.maximum(nv, 1), np.nan)
    ok = UNI[t] & (nv >= 8) & (wr >= 0.60) & (exps >= 0.01) & (AMT20[t] >= 1e8) & ~np.isnan(F5[t])
    return ok, exps, 9

def cond_mainline(t):
    # 行业强度=该行业全部成员(有r20数据)等权r20均值(合同口径), 选票层再套UNI
    if t < 21:
        return None, None, 10
    r = R20[t]
    m_all = ~np.isnan(r) & (INDV >= 0)
    if m_all.sum() < 20:
        return None, None, 10
    ind_mean = {}
    for k in np.unique(INDV[m_all]):
        m = m_all & (INDV == k)
        ind_mean[k] = float(np.nanmean(r[m]))
    top3 = sorted(ind_mean.items(), key=lambda x: -x[1])[:3]
    top3k = set(k for k, _ in top3)
    ok = (UNI[t] & ~np.isnan(r) & (r > 0) & np.isin(INDV, list(top3k)) & ~np.isnan(F5[t]))
    return ok, A[t], 10

def cond_farm(t):
    # 0<PE<=30 & 0<=r20<=15% & 量>=1.2x20日均量 & 20日均额>=1亿; 按r20降序
    if t < 21:
        return None, None, 6
    r = R20[t]
    vr = V[t] / V20[t]
    ok = (UNI[t] & ~np.isnan(PE[t]) & (PE[t] > 0) & (PE[t] <= 30)
          & ~np.isnan(r) & (r >= 0) & (r <= 0.15)
          & ~np.isnan(vr) & (vr >= 1.2)
          & (AMT20[t] >= 1e8) & ~np.isnan(F5[t]))
    return ok, r, 6

def cond_comboc(t):
    # 距250日高<=-40% & 20日跌<=-10% & 5日均量>=1.2x60日均量 & 当日涨; 按dd升序(最深优先)
    if t < 250:
        return None, None, 6
    dd = C[t] / HI250[t] - 1
    v5v = V5[t]
    with np.errstate(invalid='ignore', divide='ignore'):
        vr = v5v / (V60[t] * 1.2)
    ok = (UNI[t] & ~np.isnan(dd) & (dd <= -0.40)
          & ~np.isnan(R20[t]) & (R20[t] <= -0.10)
          & ~np.isnan(vr) & (vr >= 1.0)
          & (C[t] > C[t - 1]) & ~np.isnan(F5[t]))
    return ok, -dd, 6  # 合同按dd升序(跌最深优先)

def cond_highwin(t):
    # pos250∈[30,70] & 10日振幅<=12% & 缩量(v5<v60) & 250日上涨占比>=50%; 按wr降序
    if t < 250:
        return None, None, 7
    rng = HI250[t] - LO250[t]
    with np.errstate(invalid='ignore', divide='ignore'):
        pos = (C[t] - LO250[t]) / rng * 100
        band = (HI10[t] - LO10[t]) / LO10[t] * 100
    ok = (UNI[t] & ~np.isnan(pos) & (pos >= 30) & (pos <= 70)
          & ~np.isnan(band) & (band <= 12)
          & (V5[t] < V60[t])
          & ~np.isnan(UP250R[t]) & (UP250R[t] >= 0.50)
          & ~np.isnan(F5[t]))
    return ok, UP250R[t], 7

def cond_prescreen(t):
    # 突破20日高(>=0.98x前20日高) & 20日区间振幅<=25% & 20日涨幅<=20% & 价>MA20 & MA20不退化
    # & 当日额0.8-30亿; 按量比降序
    if t < 26:
        return None, None, 10
    pre_h20 = C[t - 20:t].max(axis=0)
    lo20 = C[t - 20:t].min(axis=0)
    ma20n = MA20[t]
    ma20a = MA20[t - 6]  # 合同ma20a=mean(cl[-25:-5])=c[t-25..t-6]均值=止于t-6的MA20
    vr = V[t] / V20[t]
    rngpct = (pre_h20 - lo20) / lo20 * 100
    with np.errstate(invalid='ignore', divide='ignore'):
        ok = (UNI[t] & ~np.isnan(pre_h20) & (C[t] >= 0.98 * pre_h20)
              & ~np.isnan(rngpct) & (rngpct <= 25)
              & ~np.isnan(R20[t]) & (R20[t] <= 0.20)
              & (C[t] > ma20n) & (ma20n >= 0.995 * ma20a)
              & ~np.isnan(A[t]) & (A[t] >= 0.8e8) & (A[t] <= 30e8)
              & ~np.isnan(F5[t]))
    return ok, vr, 10

METHODS = {
    'quant_rank_rebuild': ('量化综合排名第一(重建版)', cond_quantrank),
    'mainline_direction': ('主线方向法(近似:当前行业映射)', cond_mainline),
    'f_arm_replica': ('对垒法复刻(估值修复侧)', cond_farm),
    'combo_c_rerun': ('组合C抄底复刻', cond_comboc),
    'highwin_rescan': ('HIGHWIN复现(横盘带x高胜率)', cond_highwin),
    'prescreen_breakout10': ('盘前底部放量突破十条件', cond_prescreen),
}

NAMES = np.array(codes_all)
recs = []
for t in range(nd):
    valid_fwd = ~np.isnan(F5[t])
    pool_mask = UNI[t] & valid_fwd
    npool = int(pool_mask.sum())
    if npool < 500:
        continue
    p5, p10, p20 = float(np.nanmean(F5[t][pool_mask])), float(np.nanmean(F10[t][pool_mask])), float(np.nanmean(F20[t][pool_mask]))
    for mk, (cn, fn) in METHODS.items():
        ok, key, quota = fn(t)
        if ok is None:
            continue
        idx = np.where(ok)[0]
        if len(idx) == 0:
            recs.append({'date': dates_all[t], 'method': mk, 'n_picks': 0,
                         'pick_f5': np.nan, 'pick_f10': np.nan, 'pick_f20': np.nan,
                         'pool_f5': p5, 'pool_f10': p10, 'pool_f20': p20})
            continue
        kk = key[idx]
        order = np.argsort(-kk)[:quota]
        pick_idx = idx[order]
        recs.append({'date': dates_all[t], 'method': mk, 'n_picks': int(len(pick_idx)),
                     'pick_f5': float(np.nanmean(F5[t][pick_idx])),
                     'pick_f10': float(np.nanmean(F10[t][pick_idx])),
                     'pick_f20': float(np.nanmean(F20[t][pick_idx])),
                     'pool_f5': p5, 'pool_f10': p10, 'pool_f20': p20})

df = pd.DataFrame(recs)
df['year'] = df['date'].str[:4]
df.to_csv(r'D:\zcodesj\data\_replica_audit_daily_20261105.csv', index=False, encoding='utf-8-sig')
print('days:', len(df), flush=True)

print('\n================ 全期汇总 (前向收益%, 方法 - 池 = 差) ================')
for mk, (cn, _) in METHODS.items():
    g = df[df['method'] == mk]
    g = g[g['n_picks'] > 0]
    if len(g) == 0:
        print(f'{cn}: 零命中日')
        continue
    print(f'\n{cn}  有票日={len(g)}/{len(df)}  平均名额={g["n_picks"].mean():.1f}')
    for h in (5, 10, 20):
        pk, pl = g[f'pick_f{h}'].mean() * 100, g[f'pool_f{h}'].mean() * 100
        print(f'  f{h}: 选票{pk:+.2f}% vs 池{pl:+.2f}%  差{pk-pl:+.2f}pp')

print('\n================ 分年差(pp, 选票-池) ================')
for mk, (cn, _) in METHODS.items():
    g = df[(df['method'] == mk) & (df['n_picks'] > 0)].copy()
    if len(g) == 0:
        continue
    print(f'\n-- {cn} --')
    rows_out = []
    for h in (5, 10, 20):
        d = (g[f'pick_f{h}'] - g[f'pool_f{h}']) * 100
        yy = d.groupby(g['year']).mean().round(2)
        rows_out.append(pd.DataFrame({f'f{h}': yy}).T)
    tab = pd.concat(rows_out)
    print(tab.to_string())
