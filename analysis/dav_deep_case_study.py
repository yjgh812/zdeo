# -*- coding: utf-8 -*-
"""二池/归因 全量逐笔深拆 2026-10-11 (主人问句: 针对某一位选手做完整逐笔拆解示例)
训练法示范: 每笔按五问完整叙事(Q1大盘窗口数值/Q2主线强度+行业动量/Q3形态量能均线+启动日识别+回踩序号
           /Q4买点位置(距MA20/距10日高)/Q5止盈止损反推), 亏损单标注重点复盘
样本: dav_merged_pairtrades_v2 中 case∈{二池,归因} 全部配对
输出: reports/dav_deep_case_erychi_guiyin_20261011.md
边界: 研究层零交易权; Q5为止损/止盈原因的结果反推非原始意图
"""
import csv
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

from tdx_reader import read_day

PAIRS = r'D:\zcodesj\data\dav_merged_pairtrades_v2_20261007.csv'
PAIRS_EXTRA = [r'D:\zcodesj\data\dav_pairs_zhihe_export_20261011.csv']  # 只核大学生(episodes快照段口径)
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
IND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
OUT_MD = r'D:\zcodesj\reports\dav_deep_case_all_families_20261011.md'
TARGETS = None  # None=全部家族
NAMES_OVERRIDE = {'300223': '北京君正', '301139': '*ST元道'}  # 映射表/台账陈旧名修正


def load_env():
    sh = read_day('sh', '000001')
    sh_d = [r[0] for r in sh]
    sh_c = [r[4] for r in sh]
    sh_l = [r[3] for r in sh]
    env = {}
    for i in range(20, len(sh_d)):
        ma20 = st.mean(sh_c[i - 19:i + 1])
        bias = 100.0 * (sh_c[i] / ma20 - 1)
        lo10 = min(sh_l[i - 9:i + 1])
        lo_prev = min(sh_l[i - 19:i - 9])
        env[str(sh_d[i])] = (bias, int(sh_c[i] > ma20), int(lo10 > lo_prev))
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute("SELECT date, SUM(change_pct>0), SUM(change_pct<=-9.7), COUNT(*) "
                      "FROM stock_daily_meta_qfq WHERE date>='20230101' AND change_pct IS NOT NULL GROUP BY date")
    breadth = {str(d): (up / tot if tot else 0, dn) for d, up, dn, tot in cur}
    con.close()
    bd = sorted(breadth)
    env2 = {}
    for k, d in enumerate(bd):
        w = bd[max(0, k - 4):k + 1]
        env2[d] = (env.get(d, (0, 0, 0)), st.mean([breadth[x][0] for x in w]), st.mean([breadth[x][1] for x in w]))
    return env2


def main():
    env = load_env()
    ind_map, names = {}, {}
    con = sqlite3.connect(f'file:{IND_DB}?mode=ro', uri=True)
    for code, nm, ind in con.execute('SELECT code, name, industry FROM industry_map_ths'):
        ind_map[code] = ind
        names[code] = nm
    names.update(NAMES_OVERRIDE)
    con.close()
    # 行业20日动量分位
    try:
        c3 = sqlite3.connect(r'file:D:\workbuddyspace\databases\sw_industry.db?mode=ro', uri=True)
        srows = c3.execute("SELECT industry_name, trade_date, close FROM industry_kline_ths ORDER BY industry_name, trade_date").fetchall()
        c3.close()
        by = defaultdict(list)
        for nm, d, c in srows:
            by[nm].append(c)
        moms = {nm: (100.0 * (s[-1] / s[-21] - 1) if len(s) >= 21 and s[-21] else None) for nm, s in by.items()}
        vals = sorted(v for v in moms.values() if v is not None)
        ind_pctl = {nm: (100.0 * sum(1 for x in vals if x <= v) / len(vals)) if v is not None else None for nm, v in moms.items()}
    except sqlite3.Error:
        ind_pctl = {}

    pairs = [r for r in csv.DictReader(open(PAIRS, encoding='utf-8-sig')) if TARGETS is None or r['case'] in TARGETS]
    for extra in PAIRS_EXTRA:
        pairs += [r for r in csv.DictReader(open(extra, encoding='utf-8-sig')) if TARGETS is None or r['case'] in TARGETS]
    all_cases = sorted({r['case'] for r in pairs})
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    out = []
    for case in all_cases:
        out.append(f'\n# {case} 全量逐笔深拆（训练法示范）\n')
        g = [r for r in pairs if r['case'] == case]
        g.sort(key=lambda r: r['buy_d'])
        rets = [float(r['ret']) for r in g]
        out.append(f'家族速览: {len(g)}笔, ret中位 {st.median(rets):+.2f}%, 胜率 {100*sum(1 for x in rets if x>0)/len(rets):.0f}%, '
                   f'持有中位 {st.median([float(r["hold"]) for r in g]):.0f}日\n')
        for n_, r in enumerate(g, 1):
            code, buy_d, sell_d = r['code'], r['buy_d'], r['sell_d']
            ret, hold = float(r['ret']), int(float(r['hold']))
            nm = NAMES_OVERRIDE.get(code) or (r.get('name') or '').strip() or names.get(code, code)
            ds, cs, ps = [], [], []
            for d, c, p in con.execute('SELECT date,close,change_pct FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date',
                                       (code, '20220101')):
                ds.append(str(d))
                cs.append(c)
                ps.append(p)
            i = next((k for k, d in enumerate(ds) if d == buy_d), None)
            if i is None or i < 30:
                out.append(f'## {case} #{n_} {nm}({code}) {buy_d}→{sell_d} ret {ret:+.1f}% — qfq数据不足，跳过\n')
                continue
            L = []
            L.append(f'## {case} #{n_} {nm}({code})  {buy_d} → {sell_d}  持有{hold}日  **ret {ret:+.1f}%**' + (' ⚠️亏损单重点复盘' if ret <= 0 else ''))
            # Q1
            e = env.get(buy_d)
            if e:
                (sbias, sma20, snolow), up5, dn5 = e
                cond = sum([sma20, snolow, up5 >= 0.5])
                L.append(f'- **Q1 大盘**: 上证距MA20 {sbias:+.1f}%({"上" if sma20 else "下"})，近10日{"未创" if snolow else "曾创"}20日新低，'
                         f'近5日均上涨占比 {up5*100:.0f}%/跌停均 {dn5:.1f}家 → 可操作窗口 **{cond}/3**' + ('✓' if cond >= 2 else '✗'))
            # Q2
            ind = ind_map.get(code, 'N/A')
            ip = ind_pctl.get(ind)
            L.append(f"- **Q2 主线**: gear={r['gear'] or 'N/A'}；行业={ind or 'N/A'}" +
                     (f'，行业20日动量 {moms.get(ind, 0):+.1f}%（全行业分位 {ip:.0f}%）' if ip is not None else ''))
            # Q3
            c = cs[i]
            ma20 = st.mean(cs[i - 19:i + 1])
            ma20p = st.mean(cs[i - 24:i - 4])
            ma5, ma10 = st.mean(cs[i - 4:i + 1]), st.mean(cs[i - 9:i + 1])
            bias20 = 100.0 * (c / ma20 - 1)
            ma20up = ma20 > ma20p
            bull = ma5 > ma10 > ma20
            zt_days = [k for k in range(max(0, i - 10), i + 1) if ps[k] is not None and ps[k] >= 9.7]
            if zt_days:
                launch = zt_days[0]
                since = i - launch
                launch_ret = 100.0 * (cs[i] / cs[launch] - 1)
                q3 = (f'近10日{len(zt_days)}板，启动日={ds[launch]}（买点距启动 {since} 交易日，启动以来 {launch_ret:+.1f}%）'
                      f'→ **右侧**； bias20={bias20:+.1f}%({"站上" if bias20 > 0 else "跌破"}MA20) MA20{"拐头向上" if ma20up else "仍向下"} '
                      f'多头排列{"✓" if bull else "✗"}')
            else:
                q3 = (f'近10日无板 → **左侧/温和**；bias20={bias20:+.1f}%({"站上" if bias20 > 0 else "跌破"}MA20) '
                      f'MA20{"拐头向上" if ma20up else "仍向下"} 多头排列{"✓" if bull else "✗"}')
            L.append(f'- **Q3 形态量能均线**: {q3}')
            # Q4
            hi10 = max(cs[i - 9:i + 1])
            off_high = 100.0 * (c / hi10 - 1)
            pct = ps[i]
            pdesc = '绿盘' if pct is not None and pct < 0 else '温和' if pct is not None and pct < 5 else '强阳' if pct is not None and pct < 9.5 else '涨停'
            L.append(f'- **Q4 买点位置**: 买入日 {pdesc}（{pct:+.1f}%），距10日高 {off_high:+.1f}%，距MA20 {bias20:+.1f}%' +
                     ('；分批信息台账不可见(近似N/A)'))
            # Q5
            if ret <= 0:
                L.append(f'- **Q5 止损反推**: 亏损 {ret:+.1f}%（{"浅止损-5%内" if ret >= -5 else "深亏损-5%外"}），持有 {hold} 日退出' +
                         '（⚠️亏损单：对照亏损纪律——是破位砍还是深套割）')
            else:
                L.append(f'- **Q5 止盈反推**: 盈利 {ret:+.1f}%，持有 {hold} 日' +
                         ('（隔日兑现）' if hold <= 2 else f'（波段/趋势持有，日均 {ret/max(1,hold):+.2f}%/日）'))
            out.append('\n'.join(L) + '\n')
    con.close()
    # 亏损单汇总(主人原则: 重点看亏损单)
    out.append('\n# 亏损单汇总（重点复盘清单）\n')
    losses = [r for r in pairs if float(r['ret']) <= 0]
    losses.sort(key=lambda r: float(r['ret']))
    out.append('| case | code | buy_d | hold | ret |')
    out.append('|---|---|---|---|---|')
    for r in losses:
        out.append(f"| {r['case']} | {r['code']} | {r['buy_d']} | {int(float(r['hold']))} | {float(r['ret']):+.1f}% |")
    out.append(f'\n共 {len(losses)} 笔亏损单 / {len(pairs)} 笔；深亏(-10%外)建议逐笔对照上文拆解看止损执行。\n')
    Path(OUT_MD).write_text('\n'.join(out), encoding='utf-8')
    print(f'落盘: {OUT_MD}')
    print(f'拆解: {len(pairs)}笔 (全家族: {", ".join(all_cases)})')


if __name__ == '__main__':
    main()
