# -*- coding: utf-8 -*-
"""赢家画像五维增强层 v3 (只读输入, 新增生成物)
v3 迭代 (2026-10-07 完善优化迭代轮):
  - 主线等级扩全概念: 632板块 ret20(成分均值)排行, 个股取最强所属板块 rank 分位
    -> 主升主线3(九主线成分∧PD035主升) / 主线2(板块rank top20%) / 轮动1(20-50%) / 其他0
  - pos60 口径升级: qfq 近60交易日价格位置 (与区域引擎对齐; pos250 保留并存)
  - 人气 w_pop: 东财人气榜 top100 (data/popularity_em_latest.csv)
  - 评分权重集中输出: w_mainline_score 直接落字段, ab_track 侧零权重逻辑
输入(全只读): universe_portrait_latest.json + sector_data.db + concept_stage_ledger +
  zt_pool_history.db + popularity_em_latest.csv + dragon_quant_qfq.db(pos60)
输出: data/universe_portrait_winner5_latest.json
边界: 呈现/筛选参考层零交易权; 板块ret20=成分均值近似(未加权); 不改原画像生成器。
"""
import json, sqlite3, sys
from collections import defaultdict
from datetime import datetime
import statistics as st
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'D:\zcodesj')
from portrait_manifest import write_manifest  # 批1' 防手改manifest(2026-10-11)

ACTIVE_KW = ["人形机器人", "算力概念", "CPO概念", "固态电池", "创新药", "军工", "低空经济", "商业航天", "黄金概念"]
SECTOR_DB = r"D:/zcodesj/eastmoney_quant/sector/sector_data.db"

# 1) 原画像快照 (ret20_pct 为板块聚合与位置外的现成收益字段)
P = json.load(open(r'D:\zcodesj\data\universe_portrait_latest.json', encoding='utf-8'))
rows = P['rows']
asof = P['asof'].replace('-', '')
print(f"原快照: {len(rows)}行 asof={asof}")

# 2) 全概念成分 + 板块 ret20 排行
cm = sqlite3.connect(SECTOR_DB)
names = dict(cm.execute("SELECT sector_code,sector_name FROM sector_basic").fetchall())
sect_stocks = defaultdict(set)
code2sectors = defaultdict(set)
TECH_BLOCK = ['昨日', '涨停', '连板', '新高', '百元', '高送转', '次新', 'ST', '破净', '微盘', '融资融券', '转融券']
for sc, stk in cm.execute("SELECT sector_code,stock_code FROM sector_member").fetchall():
    sect_stocks[str(sc)].add(str(stk).zfill(6))
    nm = names.get(sc, '')
    if nm and not any(t in nm for t in TECH_BLOCK):
        code2sectors[str(stk).zfill(6)].add(str(sc))
cm.close()
ret20 = {str(r.get('code', '')).zfill(6): r.get('ret20_pct') for r in rows if r.get('ret20_pct') is not None}
sect_ret = {}
for sc, stks in sect_stocks.items():
    vals = [ret20[s] for s in stks if s in ret20]
    if len(vals) >= 10:
        sect_ret[sc] = st.mean(vals)
rank_sorted = sorted(sect_ret, key=lambda x: -sect_ret[x])
n_sect = len(rank_sorted)
sect_pct = {sc: 100.0 * i / n_sect for i, sc in enumerate(rank_sorted)}
print(f"全概念板块: {n_sect}个(成分>=10) | 样例top5: {[(names.get(s, s)[:8], round(sect_ret[s],1)) for s in rank_sorted[:5]]}")

# 3) 九主线成分与 PD-035 阶段 (主升主线特档; 技术性板块不入排行)
sect_stocks = {sc: v for sc, v in sect_stocks.items() if not any(t in names.get(sc, '') for t in TECH_BLOCK)}
nine = set()
for sc, stks in sect_stocks.items():
    if any(k in names.get(sc, '') for k in ACTIVE_KW):
        nine |= stks
stage_by_name = {}
for ln in open(r'D:\zcodesj\data\concept_stage_ledger.jsonl', encoding='utf-8'):
    d = json.loads(ln)
    if d.get('kind') == 'stage':
        prev = stage_by_name.get(d['sector_name'])
        if prev is None or d['date'] >= prev[0]:
            stage_by_name[d['sector_name']] = (d['date'], d['stage'])
stage_by_name = {k: v[1] for k, v in stage_by_name.items()}
nine_stage_up = set()
for sc in rank_sorted:
    if '主升' == stage_by_name.get(names.get(sc, ''), None) and any(k in names.get(sc, '') for k in ACTIVE_KW):
        nine_stage_up |= sect_stocks[sc]
print(f"九主线成分: {len(nine)}只 | 主升主线成分: {len(nine_stage_up)}只")

# 4) pos60 (qfq 近60交易日价格位置)
cq = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)  # 2026-10-13 切wb唯一真源(SOP一百三十二)
codes = [str(r.get('code', '')).zfill(6) for r in rows]
pos60 = {}
qf = {}
for c in set(codes):
    try:
        px = [(d_, cl) for d_, cl in cq.execute(
            "SELECT date,close FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date", (c, '20250601'))]
        if len(px) >= 20:
            w = px[-60:] if len(px) >= 60 else px
            hi = max(x[1] for x in w); lo = min(x[1] for x in w)
            if hi > lo:
                pos60[c] = round(100.0 * (px[-1][1] - lo) / (hi - lo), 1)
            qf[c] = px[-1][1]
    except Exception:
        pass
print(f"pos60覆盖: {len(pos60)}只")

# 5) 涨停基因
zt = sqlite3.connect(r'file:D:\zcodesj\data\zt_pool_history.db?mode=ro', uri=True)
dates = [r[0] for r in zt.execute("SELECT DISTINCT date FROM zt_pool WHERE zt_stat='zt' ORDER BY date DESC LIMIT 10")]
zt_cnt = defaultdict(int)
for d_ in dates:
    for (c,) in zt.execute("SELECT code FROM zt_pool WHERE date=? AND zt_stat='zt'", (d_,)):
        zt_cnt[str(c).zfill(6)] += 1
last_d = dates[0]
zt_today = set(str(c).zfill(6) for (c,) in zt.execute("SELECT code FROM zt_pool WHERE date=? AND zt_stat='zt'", (last_d,)))
zt.close()

# 6) 人气榜
pop_rank = {}
try:
    _lines = open(r'D:\zcodesj\data\popularity_em_latest.csv', encoding='utf-8').read().splitlines()
    # 格式合同 reader 侧防御: 首行非"数字|code|..."开头视为表头跳过(兼容带/不带表头两种写入)
    if _lines and not _lines[0].split('|')[0].strip().isdigit():
        _lines = _lines[1:]
    for ln in _lines:
        f_ = ln.split('|')
        if len(f_) >= 2:
            pop_rank[f_[1].zfill(6)] = int(f_[0])
    print(f"人气榜: {len(pop_rank)}只")
except FileNotFoundError:
    print('人气榜文件缺失, w_pop=N/A')

# 7) 打标
def pos_band60(v):
    if v is None: return 'N/A'
    if v <= 25: return '深位(低吸正桶)'
    if v <= 40: return '灰带(收紧候选)'
    if v <= 80: return '中带(趋势派正桶)'
    return '高区(禁)'

def zt_gene(n):
    if n <= 0: return '无'
    if n == 1: return '1板'
    if n <= 3: return '2-3板'
    return '4板+'

out_rows = []
cnt = defaultdict(int)
for r in rows:
    code = str(r.get('code', '')).zfill(6)
    # 主线等级 v3: 全概念最强所属板块 rank 分位
    best_pct = 100.0
    for sc in code2sectors.get(code, set()):
        p = sect_pct.get(sc)
        if p is not None and p < best_pct:
            best_pct = p
    if code in nine_stage_up:
        mainline, ml_score = '主升主线', 3
    elif best_pct <= 20:
        mainline, ml_score = '主线', 2
    elif best_pct <= 50:
        mainline, ml_score = '轮动', 1
    else:
        mainline, ml_score = '其他', 0
    stg = '/'.join(sorted(set(stage_by_name.get(names.get(sc), 'N/A') for sc in sect_stocks.get(code, set())))) or 'N/A'
    p60 = pos60.get(code)
    band = pos_band60(p60)
    zn = zt_cnt.get(code, 0)
    pr = pop_rank.get(code)
    if pr is None:
        pop, pop_s = '未上榜', 0
    elif pr <= 10:
        pop, pop_s = f'热榜第{pr}名', 2
    else:
        pop, pop_s = f'热榜第{pr}名', 1
    r2 = dict(r)
    r2['w_mainline'] = mainline
    r2['w_mainline_score'] = ml_score
    r2['w_sect_best_pct'] = round(best_pct, 1) if best_pct < 100 else None
    r2['w_stage'] = stg
    r2['w_pos60'] = p60
    r2['w_posband'] = band
    r2['w_pos250'] = r.get('pos250')
    r2['w_zt10'] = zt_gene(zn)
    r2['w_zt_today'] = code in zt_today
    r2['w_pop'] = pop
    r2['w_pop_score'] = pop_s
    out_rows.append(r2)
    cnt[f"mainline:{mainline}"] += 1
    cnt[f"band60:{band}"] += 1
    cnt[f"zt:{zt_gene(zn)}"] += 1
    cnt[f"pop:{'top10' if (pr or 999) <= 10 else ('top100' if pr else '无')}"] += 1
    if code in zt_today: cnt['当日涨停'] += 1

# asof一致性守护(fail-closed): 快照asof落后于qfq最新日=混合口径(旧基础数据×新K线算pos60),
# 拦截场景: 22:50画像任务失败而次日0830 dims继续跑。正常时序(22:50画像→0830 dims)asof==qfq最新日, 不触发。
qfq_latest = cq.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
if int(asof) < int(qfq_latest):
    print(f"⛔ asof守护: 画像快照asof={asof} < qfq最新日={qfq_latest} (混合口径风险: pos60将混入新K线), 拒绝写快照, 请先修复画像刷新")
    sys.exit(2)

out = {'schema_version': 'portrait.winner5.v1.1', 'owner': 'winner_profile_dims',
       'built_at': datetime.now().isoformat(timespec='seconds'),
       'base_universe_asof': asof, 'tag_asof': asof, 'qfq_max_date': str(qfq_latest),
       'permissions': {'display': True, 'research_shadow': True, 'ranking': False,
                       'filtering': False, 'pre_trade_gate': False, 'trade_execution': False},
       'version': 'winner5-3', 'asof': asof, 'basis': '交割单五人深度+十人截面赢家画像要素',
       'dims': ['w_mainline/w_mainline_score(全概念632板块ret20排行: 主升主线3/主线2/轮动1/其他0)',
                'w_stage(PD-035四阶段)', 'w_pos60/w_posband(60日位置口径,与区域引擎对齐; w_pos250并存)',
                'w_zt10/w_zt_today(涨停基因,zt_pool近10日)', 'w_pop/w_pop_score(东财人气榜top100)'],
       'boundary': '呈现/筛选参考层零交易权; 板块ret20=成分均值近似; 不改原画像生成器',
       'zt_window': f"{dates[-1]}~{dates[0]}", 'n_sectors': n_sect, 'n_rows': len(out_rows), 'rows': out_rows}
_w5_f = r'D:\zcodesj\data\universe_portrait_winner5_latest.json'
json.dump(out, open(_w5_f, 'w', encoding='utf-8'), ensure_ascii=False)
write_manifest('winner5', {'data/universe_portrait_winner5_latest.json': _w5_f})
print(f"\n五维快照v3: data/universe_portrait_winner5_latest.json ({len(out_rows)}行)")
for k in sorted(cnt):
    print(f"  {k}: {cnt[k]}")
