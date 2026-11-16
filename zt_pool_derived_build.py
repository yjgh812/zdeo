# -*- coding: utf-8 -*-
"""zt_pool_derived_build.py — 涨停池历史库生成器: 从qfq库派生涨停/炸板/连板梯队 (全窗幂等重建+build_log)

出处: 原tmp/build_zt_pool_derived_20261007.py(2026-10-07派生工程), 2026-10-08迁出gitignore入版本控制
      并修复幂等bug(裸INSERT→OR REPLACE, 二次运行必撞build_log UNIQUE且整体回滚)。
      data/zt_pool_history.db 本库口径唯一owner=本脚本; akshare东财口径增量(zt_pool_history_build.py)已被
      derived_owned硬闸拒绝, 勿混库。
口径声明(研究级派生,非kpl口径):
  - 真涨停近似: pct∈[阈值,阈值+0.35](10cm=9.85/20cm=19.85) —— 复权收益不变性,含四舍五入容差
  - 炸板近似: (high/prev_close-1)*100>=阈值 且 收盘不满足涨停
  - boards=连续涨停日串长(按交易日位置连续性,炸板/中断清零); ST五厘板不可识别(声明); 首日/无前收不判
  - 封单/首次最后涨停时间/盘中开板次数=kpl专属字段,置NULL
  - 覆盖: 沪深主板00/60+创业板30; 剔688/8xx/4xx
依赖: wb qfq库 dragon_quant_qfq.db(唯一真源; 2026-10-13翻转定谳=wb, 旧zc副本停更10/09已废, SOP一百三十二)
目标: data/zt_pool_history.db zt_pool表(窗口重建2020起) + build_log"""
import sqlite3, sys, time
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ZT = r'D:\zcodesj\data\zt_pool_history.db'
t0 = time.time()

cq = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
cq.execute("CREATE TEMP TABLE cand AS SELECT code,date,high,close,amount,change_pct, LAG(close) OVER (PARTITION BY code ORDER BY date) AS pc FROM stock_daily_meta_qfq WHERE date>='20200101' AND (substr(code,1,2) IN ('00','60','30'))")
rows = pd.read_sql("""SELECT code,date,high,close,amount,change_pct AS pct,pc FROM cand
WHERE (change_pct BETWEEN 9.85 AND 10.15) OR change_pct>=19.85
   OR (pc IS NOT NULL AND (high*1.0/pc-1)*100>=9.85)""", cq)
cal = [r[0] for r in cq.execute("SELECT DISTINCT date FROM cand ORDER BY date")]
cal_pos = {d: i for i, d in enumerate(cal)}
cq.close()
print(f'候选行(涨停+炸板): {len(rows)} 交易日{len(cal)}天 用时{time.time()-t0:.0f}s')

rows = rows.sort_values(['code', 'date']).reset_index(drop=True)
pfx = rows.code.str[:2]
th = pfx.map({'30': 19.85}).fillna(9.85)
pct_hi = (rows.high / rows.pc - 1) * 100
pct_cl = (rows.close / rows.pc - 1) * 100
is_zt = (pct_cl >= th) & (pct_cl <= th + 0.35) & (rows.pct >= th)
is_zha = (~is_zt) & (pct_hi >= th) & rows.pc.notna()
rows['stat'] = None
rows.loc[is_zt, 'stat'] = 'zt'
rows.loc[is_zha, 'stat'] = 'zha'
zt = rows[rows.stat == 'zt'].copy()

# 连板串: 每code内交易日位置连续的涨停行计串长
zt['pos'] = zt.date.map(cal_pos)
zt = zt.sort_values(['code', 'pos'])
new_blk = (zt.code != zt.code.shift()) | (zt.pos.diff() != 1)
zt['blk'] = new_blk.cumsum()
zt['boards'] = zt.groupby('blk').cumcount() + 1
zt_out = zt[['date', 'code', 'pct', 'close', 'amount', 'stat', 'boards']]
zha_out = rows.loc[is_zha, ['date', 'code', 'pct', 'close', 'amount', 'stat']].copy()
zha_out['boards'] = 0
out = pd.concat([zt_out, zha_out]).sort_values(['date', 'code'])
print(f'涨停行={len(zt)} 炸板行={len(zha_out)} 用时{time.time()-t0:.0f}s')
print('boards分布:', zt.boards.value_counts().sort_index().head(8).to_dict())

# ---- 写库(窗口重建) ----
# 名称/行业回填(修复重建段name=None): 真源=workbuddyspace fundamental_data.db industry_map_ths(20261010)
cn = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
name_map = {r[0]: (r[1], r[2]) for r in cn.execute('select code,name,industry from industry_map_ths')}
cn.close()
cz = sqlite3.connect(ZT)
cz.execute('DELETE FROM zt_pool WHERE date>=?', ('20200102',))
cz.executemany('INSERT INTO zt_pool(date,code,name,pct,close,amount,circ_mv,total_mv,turnover,seal_amount,first_time,last_time,break_cnt,zt_stat,boards,industry) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               [(r.date, r.code, name_map.get(r.code, (None, None))[0], round(float(r.pct), 3), float(r.close), float(r.amount) if pd.notna(r.amount) else None,
                 None, None, None, None, None, None, None, r.stat, int(r.boards) if r.stat == 'zt' else None, name_map.get(r.code, (None, None))[1])
                for r in out.itertuples()])
n = cz.execute('SELECT changes()').fetchone()[0]
cols = [r[1] for r in cz.execute('pragma table_info(build_log)')]
if 'note' not in cols:
    cz.execute('ALTER TABLE build_log ADD COLUMN note TEXT')
cz.execute("INSERT OR REPLACE INTO build_log(date,rows,ts,note) VALUES(?,?,datetime('now','localtime'),?)", (
    '20200102', n,
    f'qfq派生重建2020起: 真涨停=pct∈[阈值,阈值+0.35](10cm=9.85/20cm=19.85,复权收益不变口径,含高=收盘复核); 炸板=high触阈未封住; boards=连续涨停串(交易日位置连续性); ST五厘板不可识别(声明); 封单/首末涨停时间/开板次数=kpl专属字段置NULL; 覆盖00/60/30,剔688/8xx/4xx; 涨停{len(zt)}行+炸板{len(zha_out)}行,共{n}行; 脚本zt_pool_derived_build.py(迁自tmp/build_zt_pool_derived_20261007.py); 20261010名称/行业回填(industry_map_ths真源,修复重建段name=None)',))
cz.commit()
print(f'写库完成: {n}行 用时{time.time()-t0:.0f}s')

# ---- 验收1: 与情绪表涨停家数对照 ----
cf = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
sen = {d.replace('-', ''): v for d, v in cf.execute("select trade_date,limit_up from feature_market_sentiment where trade_date>='2021-01-01' and limit_up is not null")}
mine = out[out.stat == 'zt'].groupby('date').size()
common = [d for d in mine.index if d in sen]
import statistics
diffs = [mine[d] - sen[d] for d in common]
print(f'验收1 对情绪表limit_up: 对照{len(common)}日 MAE={statistics.mean(abs(x) for x in diffs):.1f}只 偏差均值={statistics.mean(diffs):+.1f} (情绪表含ST/科创/北交全口径,本库剔之,系统性偏低=预期)')

# ---- 验收2: 连板梯队(最近交易日) ----
last_d = zt.date.max()
top = zt[zt.date == last_d].sort_values('boards', ascending=False).head(8)
print(f'验收2 {last_d} 连板梯队Top8: ' + ' | '.join(f'{r.code}x{r.boards}板' for r in top.itertuples()))
print('  板数分布:', zt[zt.date == last_d].boards.value_counts().sort_index().to_dict())

# ---- 验收3: 1进2晋级率(锚: 20260928-29复盘口径约7.9%冰点→25%修复) ----
def promote(d):
    if d not in cal_pos: return None, 0, None
    nxt = cal_pos[d] + 1
    if nxt >= len(cal): return None, 0, None
    d2 = cal[nxt]
    fb = set(zt[(zt.date == d) & (zt.boards == 1)].code)
    nz = set(zt[zt.date == d2].code)
    return (100 * len(fb & nz) / len(fb), len(fb), d2) if fb else (None, 0, d2)
for d in ['20260924', '20260928', '20260929', '20260930']:
    r, n1, d2 = promote(d)
    if r is not None:
        print(f'验收3 {d}: 首板{n1}只 -> {d2} 1进2晋级率={r:.0f}%')
