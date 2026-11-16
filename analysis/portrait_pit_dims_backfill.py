# -*- coding: utf-8 -*-
"""画像 PIT 维度全史回填件 (迭代轮3候选, 2026-10-09)
生成物: data/portrait_pit_dims_daily.db (幂等全量重建)
口径 = analysis/winner_profile_dims.py 逐位同源(唯一真源), 仅扩展为逐日时间轴:
  pos60   = 100*(close-min60)/(max60-min60), 窗=近60根qfq收盘(不足60取全部, <20根=None)
  posband = <=25深位(低吸正桶) / <=40灰带(收紧候选) / <=80中带(趋势派正桶) / >80高区(禁) / None=N/A
  zt_gene = 近10个涨停池日内 zt 次数: 0无/1=1板/2-3=2-3板/4+=4板+ (zt库2020起, 之前=N/A)
PIT 声明: 全部窗口止于当日t(仅用<=t数据), 无前视; 不含主线维(2026名单时代错置)与人气维(无历史)。
边界: 研究侧生成物, 零交易权; A/B 用途见 PREREG_PORTRAIT_PIT_AB 草案。
"""
import sqlite3
import sys
import time

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ZT = r'D:\zcodesj\data\zt_pool_history.db'
OUT = r'D:\zcodesj\data\portrait_pit_dims_daily.db'

t0 = time.time()

def pos_band60(v):
    if v is None:
        return 'N/A'
    if v <= 25:
        return '深位(低吸正桶)'
    if v <= 40:
        return '灰带(收紧候选)'
    if v <= 80:
        return '中带(趋势派正桶)'
    return '高区(禁)'

def zt_gene(n):
    if n is None:
        return 'N/A'
    if n <= 0:
        return '无'
    if n == 1:
        return '1板'
    if n <= 3:
        return '2-3板'
    return '4板+'

# --- 1) qfq 全史 pos60 ---
qf = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
df = pd.read_sql('SELECT code, date, close FROM stock_daily_meta_qfq ORDER BY code, date', qf)
qf.close()
print(f'qfq加载: {len(df):,}行 {df.code.nunique():,}只 {time.time()-t0:.0f}s')

g = df.groupby('code', sort=False)['close']
lo = g.transform(lambda s: s.rolling(60, min_periods=1).min())
hi = g.transform(lambda s: s.rolling(60, min_periods=1).max())
cnt = g.cumcount() + 1
pos60 = (100.0 * (df['close'] - lo) / (hi - lo)).round(1)
pos60[(hi <= lo) | (cnt < 20)] = None
df['pos60'] = pos60
df['posband'] = df['pos60'].map(pos_band60)
print(f'pos60完成: 非空{df.pos60.notna().sum():,}行 {time.time()-t0:.0f}s')

# --- 2) zt 基因 (近10池日计数; 池日=交易日全覆盖2020起) ---
zt = sqlite3.connect(f'file:{ZT}?mode=ro', uri=True)
zdf = pd.read_sql("SELECT date, code FROM zt_pool WHERE zt_stat='zt'", zt)
zt.close()
zdf['code'] = zdf['code'].astype(str).str.zfill(6)
zdf['date'] = zdf['date'].astype(int)
pool_dates = sorted(zdf['date'].unique())
print(f'zt池: {len(zdf):,}行 {len(pool_dates)}池日 {pool_dates[0]}~{pool_dates[-1]}')

zcnt = zdf.groupby(['code', 'date']).size().reset_index(name='n')
# 每码按池日累计计数, 取"<=t 的最近10个池日"窗口和: 用 rank 滑窗
zcnt = zcnt.sort_values(['code', 'date'])
zcnt['rank_desc'] = zcnt.groupby('code')['date'].rank(ascending=False, method='first').astype(int)
# 简洁正确法: 对每个池日索引i, 窗口=池日[i-9..i]; 构建每池日每码计数再按池日滑窗求和
pivot = zcnt.pivot_table(index='date', columns='code', values='n', aggfunc='sum').fillna(0)
roll = pivot.rolling(10, min_periods=1).sum()
stacked = roll.stack()
stacked.name = 'zn'
zt_map = stacked
print(f'zt滑窗完成 {time.time()-t0:.0f}s')

# --- 3) 合并落库 ---
df['date'] = df['date'].astype(int)
df['code'] = df['code'].astype(str).str.zfill(6)
df = df[df['date'] >= pool_dates[0]]  # zt 基因可用域; pos60 单独区间如需全史另行扩展(本轮A/B用2020+窗)
mg = df.merge(zt_map.reset_index().rename(columns={0: 'zn'}), on=['code', 'date'], how='left')
mg['zt_gene'] = mg['zn'].map(lambda x: zt_gene(int(x) if pd.notna(x) else None))

db = sqlite3.connect(OUT)
db.execute('DROP TABLE IF EXISTS pit_dims')
db.execute('DROP TABLE IF EXISTS build_log')
db.execute('CREATE TABLE pit_dims (code TEXT, date INTEGER, pos60 REAL, posband TEXT, zt_gene TEXT, PRIMARY KEY(code,date))')
mg[['code', 'date', 'pos60', 'posband', 'zt_gene']].to_sql('pit_dims', db, if_exists='append', index=False)
db.execute("CREATE TABLE build_log (ts TEXT, rows INTEGER, note TEXT)")
db.execute("INSERT INTO build_log VALUES (datetime('now','localtime'), ?, ?)",
           (len(mg), '口径=winner_profile_dims v3逐位同源; pos60近60根; zt近10池日(2020+); PIT仅用<=t'))
db.commit()
db.close()
n_ban = int((mg['posband'] == '高区(禁)').sum())
print(f'落库: {len(mg):,}行 -> {OUT} ({time.time()-t0:.0f}s) | 高区(禁)行数={n_ban:,}')
