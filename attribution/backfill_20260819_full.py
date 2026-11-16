# -*- coding: utf-8 -*-
"""8/19复盘补全工程: 跌停全量入库 + 8/18断档补库 + 横盘突破定量 + 回头看增强
数据: qfq库(stock_daily_meta_qfq) + dragon_quant.db(stock_info) — 无未来函数,信号日=8/18收盘,验证日=8/19
"""
import sqlite3, datetime, sys
sys.path.insert(0, 'D:/zcodesj')
import pandas as pd

QFQ = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
INFO = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro'
ATTR = 'D:/zcodesj/attribution/attribution.db'
NOW = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# ---------- 基础数据 ----------
info = pd.read_sql("select code,name,industry,is_st,list_date from stock_info", sqlite3.connect(INFO, uri=True))
info = info.set_index('code')

c = sqlite3.connect(QFQ, uri=True)
dates = [r[0] for r in c.execute("select distinct date from stock_daily_meta_qfq where date>='20260401' order by date")]
d_idx = {d: i for i, d in enumerate(dates)}
D19, D18 = '20260819', '20260818'
assert D19 in d_idx and D18 in d_idx, 'dates missing'
start60 = dates[d_idx[D19] - 75]
df = pd.read_sql(f"select code,date,open,high,low,close,volume,change_pct from stock_daily_meta_qfq where date>='{start60}'", c)
df = df.merge(info[['name', 'is_st', 'list_date']], left_on='code', right_index=True, how='left')
# 范围: 主板+创业板, 剔ST, 剔上市<60天(相对8/19), 剔688/4/8/92前缀
df = df[~df.code.str.startswith(('68', '4', '8', '92'))]
df = df[(df.is_st != '1') & (df.is_st != 1)]
df = df[df.list_date <= '20260620']
piv = df.pivot_table(index='date', columns='code', values='change_pct')
d_idx = {d: i for i, d in enumerate(piv.index)}  # 位置索引必须相对已加载帧
close = df.pivot_table(index='date', columns='code', values='close')
high = df.pivot_table(index='date', columns='code', values='high')
low = df.pivot_table(index='date', columns='code', values='low')
vol = df.pivot_table(index='date', columns='code', values='volume')

def limit_mask(day, direction):
    pct = piv.loc[day]
    thr = pd.Series(-9.8 if direction == 'dn' else 9.8, index=pct.index)
    is_cy = pct.index.str.startswith('30')
    thr[is_cy] = -19.8 if direction == 'dn' else 19.8
    m = pct <= thr if direction == 'dn' else pct >= thr
    return pct[m & pct.notna()]

def gain60(day):
    i = d_idx[day]
    base = close.iloc[i - 60]
    return (close.loc[day] / base - 1) * 100

# ================= Part A: 8/19 跌停全量入库 =================
dn19 = limit_mask(D19, 'dn')
zt18_set = set(limit_mask(D18, 'up').index)
g60 = gain60(D19)
ca = sqlite3.connect(ATTR)
ca.execute("delete from limit_down_risks where trade_date='20260819' and risk_tags like '%全量补录%'")
ca.execute("delete from limit_down_risks where trade_date='20260818' and risk_tags like '%回灌补录%'")
ca.execute("delete from limit_up_patterns where trade_date='20260818' and feature_tags like '%回灌补录%'")
ca.commit()
exist = {r[0] for r in ca.execute("select code from limit_down_risks where trade_date='20260819'")}
nA = 0
for code, pct in dn19.sort_values().items():
    if code in exist:
        continue
    nm = info.loc[code] if code in info.index else None
    name, ind = (nm['name'], nm['industry']) if nm is not None else ('未知', '')
    if code in zt18_set:
        dtype, prim, sec, avoid = '断板A杀', '情绪退潮', '前日涨停次日跌停,A杀', 9
        tags = '前日涨停;跌停;A杀;全量补录'
    elif pd.notna(g60.get(code)) and g60[code] >= 30:
        dtype, prim, sec, avoid = '高位兑现', '资金出逃', f'60日涨幅{g60[code]:.0f}%高位股兑现', 8
        tags = f'高位;60日+{g60[code]:.0f}%;跌停;全量补录'
    else:
        dtype, prim, sec, avoid = '普跌跟随', '板块退潮', '系统性风险日泥沙俱下,无个股独立利空确认', 6
        tags = '普跌;系统性风险日;全量补录'
    ca.execute("""insert into limit_down_risks(trade_date,code,name,industry,board,drop_type,primary_attribution,secondary_attribution,avoidability_score,risk_tags,info_available_pre_open,strategy_value,created_at)
                  values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               ('20260819', code, name, ind or '', '主板' if not code.startswith('30') else '创业板',
                dtype, prim, sec, avoid, tags, 1, '8/19全量跌停补录(模板归因,基于收盘前量价)', NOW))
    nA += 1
ca.commit()

# ================= Part B: 8/18 断档补库 =================
nB1 = nB2 = 0
up18 = limit_mask(D18, 'up')
dn18 = limit_mask(D18, 'dn')
zt17_set = set(limit_mask(dates[d_idx[D18] - 1], 'up').index)
g60_18 = gain60(D18)
for code, pct in up18.items():
    if ca.execute("select 1 from limit_up_patterns where trade_date='20260818' and code=?", (code,)).fetchone():
        continue
    nm = info.loc[code] if code in info.index else None
    name, ind = (nm['name'], nm['industry']) if nm is not None else ('未知', '')
    drv = '农业全链主线日(8/18种植业+11.83%)' if ind and ('农' in ind or '种植' in ind or '饲料' in ind) else '8/18主线/支线涨停(回灌补录)'
    ca.execute("""insert into limit_up_patterns(trade_date,code,name,industry,board,limit_count,seal_status,first_limit_time,turnover,primary_attribution,secondary_attribution,core_driver,predictability_score,feature_tags,pattern_type,info_available_pre_open,strategy_value,created_at)
                  values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               ('20260818', code, name, ind or '', '主板' if not code.startswith('30') else '创业板',
                2 if code in zt17_set else 1, '收盘涨停(回灌补录)', '', None,
                '回灌补录', drv, drv, 5, '回灌补录;8/18断档补齐', '回灌补录-待细化', 1, '8/18断档补录,封板细节未采集', NOW))
    nB1 += 1
for code, pct in dn18.items():
    if ca.execute("select 1 from limit_down_risks where trade_date='20260818' and code=?", (code,)).fetchone():
        continue
    nm = info.loc[code] if code in info.index else None
    name, ind = (nm['name'], nm['industry']) if nm is not None else ('未知', '')
    g = g60_18.get(code)
    dtype = '高位兑现' if pd.notna(g) and g >= 30 else '普跌跟随'
    ca.execute("""insert into limit_down_risks(trade_date,code,name,industry,board,drop_type,primary_attribution,secondary_attribution,avoidability_score,risk_tags,info_available_pre_open,strategy_value,created_at)
                  values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               ('20260818', code, name, ind or '', '主板' if not code.startswith('30') else '创业板',
                dtype, '回灌补录', '8/18分化日跌停', 6, '回灌补录;8/18断档补齐', 1, '8/18断档补录', NOW))
    nB2 += 1
ca.commit()

# ================= Part C: 横盘突破定量(信号日8/18收盘,验证日8/19) =================
i18 = d_idx[D18]
w_close = close.iloc[i18 - 19:i18 + 1]          # 20日含8/18
prior15_high = high.iloc[i18 - 15:i18]           # 前15日(不含8/18)
prior15_low = low.iloc[i18 - 15:i18]
prior20_high_close = close.iloc[i18 - 20:i18]    # 8/18之前的20日收盘高点
vol20 = vol.iloc[i18 - 20:i18].mean()
amp15 = (prior15_high.max() - prior15_low.min()) / prior15_low.min() * 100
sig = (close.loc[D18] >= prior20_high_close.max()) & (amp15 <= 18) & (vol.loc[D18] >= 1.5 * vol20)
sig_codes = sig[sig].index.tolist()
r19 = piv.loc[D19]
sig_ret = r19[sig_codes].dropna()
mkt = r19.dropna()
resC = {
    '信号数': len(sig_ret),
    '8/19平均涨幅': round(sig_ret.mean(), 2), '中位数': round(sig_ret.median(), 2),
    '上涨占比%': round((sig_ret > 0).mean() * 100, 1),
    '跑赢上证(-2.4%)占比%': round((sig_ret > -2.4).mean() * 100, 1),
    '涨停数(>=9.8)': int((sig_ret >= 9.8).sum()), '跌停数': int((sig_ret <= -9.8).sum()),
    '全市场中位数': round(mkt.median(), 2), '全市场平均': round(mkt.mean(), 2),
    '全市场上涨占比%': round((mkt > 0).mean() * 100, 1),
}
top = sig_ret.sort_values(ascending=False).head(8)
resC['top8'] = [(cd, info.loc[cd,'name'] if cd in info.index else '', round(v, 2)) for cd, v in top.items()]

# ================= Part D: 回头看增强 =================
sells = [
    ('20260817', '002414', 14.01), ('20260817', '600719', 7.38), ('20260817', '002104', 10.16),
    ('20260817', '002194', 10.52), ('20260817', '601991', 6.39), ('20260817', '600726', 6.22),
    ('20260818', '600776', 12.78), ('20260818', '600667', 23.20), ('20260818', '600721', 13.70),
    ('20260818', '002724', 6.46),
    ('20260819', '300021', 3.96), ('20260819', '002418', 3.60), ('20260819', '002167', 17.45),
    ('20260819', '300017', 16.15), ('20260819', '002491', 18.20), ('20260819', '603318', 9.50),
    ('20260819', '601011', 2.73), ('20260819', '300333', 10.95), ('20260819', '002185', 17.95),
    ('20260819', '002322', 12.10),
]
rowsD = []
for sd, code, sp in sells:
    i0 = d_idx[sd] + 1  # 卖出次日算起
    if i0 <= d_idx[D19]:
        h = high.iloc[i0:d_idx[D19] + 1][code].max()
        l = low.iloc[i0:d_idx[D19] + 1][code].min()
        mx = (h / sp - 1) * 100 if pd.notna(h) else None
        dd = (l / sp - 1) * 100 if pd.notna(l) else None
    else:
        mx = dd = None
    cl = close.loc[D19, code]
    rowsD.append((sd, code, info.loc[code,'name'] if code in info.index else '', sp,
                  round(mx, 1) if mx is not None else None, round(dd, 1) if dd is not None else None,
                  round(cl, 2), round((cl / sp - 1) * 100, 1)))

tot_up = ca.execute("select count(*) from limit_up_patterns").fetchone()[0]
tot_dn = ca.execute("select count(*) from limit_down_risks").fetchone()[0]
ca.close()

print('=== PartA 8/19跌停全量 ===')
print(f'口径内跌停总数={len(dn19)} (主板<=-9.8 创业<=-19.8, 剔ST/新股/科创北交); 新入库={nA}; 累计={tot_dn}')
print('分类统计: 断板A杀=', len(set(dn19.index)&zt18_set), ' 高位兑现(60d>=30%)=', int(sum(1 for cd in dn19.index if pd.notna(g60.get(cd)) and g60[cd] >= 30 and cd not in zt18_set)), ' 普跌跟随=', int(len(dn19) - len(set(dn19.index)&zt18_set) - sum(1 for cd in dn19.index if pd.notna(g60.get(cd)) and g60[cd] >= 30 and cd not in zt18_set)))
print('=== PartB 8/18补库 ===')
print(f'8/18涨停口径={len(up18)} 入库={nB1}; 跌停口径={len(dn18)} 入库={nB2}; 范式库累计={tot_up}')
print('=== PartC 横盘突破定量 ===')
for k, v in resC.items():
    print(' ', k, v)
print('=== PartD 回头看增强(卖后至8/19) ===')
print('卖日 代码 名称 卖价 卖后最大涨% 最大回撤% 8/19收 卖后涨跌%')
for r in rowsD:
    print(' ', r)
