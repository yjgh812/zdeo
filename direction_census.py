# -*- coding: utf-8 -*-
"""全市场波段方向普查（2026-09-19 主人令：判断哪些股票可以上涨/哪些会继续下跌）

方法=5年15.5万段统计的条件规则(非画像匹配分——后者干净校验未过):
  🟢 上涨候选(主升早段): 深跌(前60日>=25%)后放量大阳启动 + 距段起-3%~+17% + 多头 + pos250<50 + 三年分位<55%
  🟡 底部酝酿(上涨上游): 一年内深跌>=35% + 当前缩量(20日/250日<=0.9) + 未启动 —— 等启动信号
  🔴 下跌风险(继续跌): 空头排列 + 20日动量为负 + (年内跌幅<25% 或 未缩量) —— 下跌中段未衰竭
  🟠 顶部风险(涨势末端): 距一年低点已涨>=40% + (5日急涨>=12% 或 乖离MA20>=10% 或 天量滞涨)
  ⚪ 中性
输出: reports/direction_census_YYYYMMDD.md (四桶分布+清单) + data/direction_census_YYYYMMDD.json
"""
from __future__ import annotations
import json, os, sqlite3, sys
from datetime import datetime
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402
from state_history_lookup import _load_table  # noqa: E402

QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"


def f3(x):
    try:
        return float(x)
    except Exception:
        return None


def classify(closes, vols, ms_state):
    """对单票(倒序序列)做波段方向分类, 返回(桶, 明细dict)。"""
    n = len(closes)
    if n < 320:
        return None, None
    c = closes[0]
    ma20 = sum(closes[:20]) / 20
    ma60 = sum(closes[:60]) / 60
    ma250 = sum(closes[:250]) / 250
    yr = closes[:250]
    hi, lo = max(yr), min(yr)
    pos250 = 100 * (c - lo) / (hi - lo) if hi > lo else 50
    r20 = (c / closes[20] - 1) * 100 if closes[20] else 0
    off = (c / ma20 - 1) * 100
    v250 = sum(vols[:250]) / 250
    v20 = sum(vols[:20]) / 20 if v250 else 0
    d = {'pos250': round(pos250), 'r20': round(r20, 1), 'off': round(off, 1),
         'bull': ma20 > ma60, 'shrink': round(v20 / v250, 2) if v250 else 1}
    yr_drawdown = (lo / hi - 1) * 100 if hi else 0
    d['yr_dd'] = round(yr_drawdown)
    # 🟠 顶部风险
    off_lo250 = (c / lo - 1) * 100 if lo else 0
    top_score = (off_lo250 >= 40) + (n > 5 and closes[5] and c / closes[5] - 1 >= 0.12) + (off >= 10) + \
                (v250 and vols[0] >= max(vols[:250]) and (closes[1] and (c / closes[1] - 1) <= 0.01))
    if top_score >= 3 and off_lo250 >= 40:
        return 'TOP', {**d, 'off_lo': round(off_lo250)}
    # 🟢 上涨候选: v4b 条件(全市场版, 不限主线)
    if ma20 > ma60 and pos250 < 50:
        lo_i = yr.index(lo)
        start_i = None
        for i in range(lo_i, -1, -1):
            w = vols[i:i + 20]
            if len(w) < 20:
                break
            if i + 1 < n and closes[i + 1] and vols[i] >= 2 * (sum(w) / 20) and (closes[i] / closes[i + 1] - 1) >= 0.03:
                start_i = i
                break
        if start_i is not None and 2 <= start_i <= 40:
            off_s = (c / closes[start_i] - 1) * 100
            if -3 <= off_s <= 17:
                pre60 = closes[start_i + 1:start_i + 61]
                if len(pre60) >= 40 and (min(pre60) / max(pre60) - 1) * 100 <= -25:
                    shrink_cap = 1.56 if ms_state == 'LOW_VOL' else 0.95
                    p20 = sum(vols[start_i + 1:start_i + 21]) / 20 if start_i + 21 <= n else v250
                    ps = p20 / v250 if v250 else 1
                    if ps <= shrink_cap:
                        yr3 = closes[:750] if n >= 750 else closes
                        hi3, lo3 = max(yr3), min(yr3)
                        p3 = 100 * (c - lo3) / (hi3 - lo3) if hi3 > lo3 else 50
                        if p3 < 55 and (c / lo3 - 1) * 100 <= 80:
                            return 'UP', {**d, 'off_start': round(off_s, 1), 'days': start_i, 'p3': round(p3)}
    # 🟡 底部酝酿: 深跌+缩量+未启动
    if yr_drawdown <= -35 and d['shrink'] <= 0.9 and pos250 < 40:
        return 'BASE', d
    # 🔴 下跌风险: 空头+负动量+未衰竭
    if ma20 < ma60 and r20 < 0:
        if yr_drawdown > -25 or d['shrink'] > 0.9:
            return 'DOWN', d
    return 'MID', d


def market_env_section():
    """大盘环境前置(2026-09-19 主人指令书: 大盘点位作为前置条件纳入)——拉最新上证+5年分位+历史桶位。"""
    import subprocess
    try:
        r = subprocess.run([sys.executable, '-c', '''
import baostock as bs, io
bs.login()
df = bs.query_history_k_data_plus("sh.000001", "date,close", start_date="2021-09-01", end_date=__ENDDATE__, frequency="d").get_data()
bs.logout()
out = io.StringIO(); df.to_csv(out, index=False); print(out.getvalue())
'''.replace('__ENDDATE__', repr(datetime.now().strftime('%Y-%m-%d')))], capture_output=True, text=True, timeout=90, encoding='utf-8', errors='replace')
        rows = []
        for l in (r.stdout or '').splitlines():
            parts = l.strip().split(',')
            if len(parts) == 2 and parts[0][:2] == '20':
                try:
                    rows.append((parts[0].replace('-', ''), float(parts[1])))
                except ValueError:
                    pass
        if len(rows) < 300:
            # 2026-10-20 fallback: baostock 日间/傍晚退化窗口(15:30/21:01 实弹两连缺失,
            # 大盘前置行消失致 regime 门控连续三日 SKIP) → 改读本地 dragon_quant_meta.
            # index_daily_meta(code='000001'=上证, 随链日更, 零网络依赖), 同 5 年窗同口径;
            # 本地亦不足才 return None(fail-open 语义不变)。
            try:
                con2 = sqlite3.connect(
                    "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro",
                    uri=True, timeout=10)
                h2 = con2.execute(
                    "SELECT date, close FROM index_daily_meta "
                    "WHERE code='000001' AND date>='20210901' AND close IS NOT NULL "
                    "ORDER BY date").fetchall()
                con2.close()
                rows = [(d.replace('-', ''), float(c)) for d, c in h2 if c]
            except Exception:
                pass
        if len(rows) < 300:
            return None
        closes = [x[1] for x in rows]; dates = [x[0] for x in rows]
        c = closes[-1]; n = len(closes)
        hi5, lo5 = max(closes), min(closes)
        w = closes[-250:]
        env = round(100 * (c - min(w)) / (max(w) - min(w))) if max(w) > min(w) else 50
        pos5y = round(100 * (c - lo5) / (hi5 - lo5))
        return {'date': dates[-1], 'close': c, 'pos5y': pos5y, 'env_pos250': env,
                'hi5': hi5, 'lo5': lo5}
    except Exception:
        return None


def main():
    ms_state = '?'
    try:
        ms_state = json.load(open(os.path.join(ROOT, 'data', 'market_state_probe.json'), encoding='utf-8')).get('state', '?')
    except Exception:
        pass
    db = sqlite3.connect(QFQ, uri=True)
    codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20260901'").fetchall()]
    today = datetime.now().strftime('%Y%m%d')
    # 同态概率表(2026-09-19 从对方系统学习)
    state_table = {}
    try:
        state_table = _load_table()
    except Exception:
        pass
    buckets = {'UP': [], 'BASE': [], 'DOWN': [], 'TOP': [], 'MID': []}
    skipped = 0
    for ci, code in enumerate(codes):
        if code.startswith('688'):
            skipped += 1; continue
        nm = get_name(code) or ''
        if 'ST' in nm.upper() or '退' in nm:
            skipped += 1; continue
        h = db.execute("SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780", (code,)).fetchall()
        rows = [(f3(a), f3(b)) for a, b in h]
        if len(rows) < 320 or rows[0][0] is None or not (2 <= rows[0][0] <= 500):
            skipped += 1; continue
        closes = [r[0] for r in rows]; vols = [r[1] or 0 for r in rows]
        bucket, d = classify(closes, vols, ms_state)
        if bucket is None:
            skipped += 1; continue
        # 同态概率(4维状态向量→查351万日历史胜率)
        trend = 1 if d.get('bull', False) else 0
        s_sh = 0 if d.get('shrink', 1) <= 0.7 else (1 if d.get('shrink', 1) <= 1.3 else 2)
        s_pos = 0 if d.get('pos250', 50) < 20 else (1 if d.get('pos250', 50) < 50 else (2 if d.get('pos250', 50) < 80 else 3))
        s_mom = 0 if d.get('r20', 0) < -5 else (1 if d.get('r20', 0) <= 5 else 2)
        state_key = f"{trend}|{s_sh}|{s_pos}|{s_mom}"
        si = state_table.get(state_key)
        wr = si['win_rate'] if si else None
        buckets[bucket].append({'code': code, 'name': nm, 'px': closes[0],
                                'state_wr': wr, 'state_n': si['n'] if si else 0, **d})
        if ci % 800 == 0:
            print(f'  ...{ci}/{len(codes)}', flush=True)
    db.close()
    cnt = {k: len(v) for k, v in buckets.items()}
    me = market_env_section()
    env_lines = []
    if me:
        # 历史桶位规则(5年统计: 0-19桶上涨段+53.3%最强/80+桶+34.5%最弱, 下跌段反向)
        b = me['env_pos250']
        if b < 20:
            amp, tone = '+53%级(历史最强放大)', '大盘底部环境=上涨波段强放大'
        elif b < 40:
            amp, tone = '+27~35%级(中等放大)', '中位偏弱环境=上涨幅度收敛'
        elif b < 60:
            amp, tone = '+36%级', '中性环境'
        elif b < 80:
            amp, tone = '+32%级', '中高位环境'
        else:
            amp, tone = '+26%级(最弱放大)', '高位环境=上涨幅度最收敛/下跌段最深-29%'
        env_lines = [f"> 🌐 **大盘前置**：上证 {me['close']:.2f}({me['date']}) = 5年{me['pos5y']}%价格分位 / 年线窗口{b}分位 → {tone}（该桶历史上涨波段{amp}）",
                     f"> 5年区间 {me['lo5']:.0f}~{me['hi5']:.0f}"]
    # 排序: UP按段起距升序, DOWN按r20最差, TOP按off_lo
    buckets['UP'].sort(key=lambda x: abs(x.get('off_start', 99)))
    buckets['DOWN'].sort(key=lambda x: x['r20'])
    buckets['TOP'].sort(key=lambda x: -x.get('off_lo', 0))
    L = [f"# 全市场波段方向普查（{datetime.now().strftime('%Y-%m-%d %H:%M')}·5年条件规则版）",
         "",
         f"> 覆盖 {sum(cnt.values())} 只(剔科创/ST/数据不足 {skipped}) ｜ 市场状态={ms_state}（缩量阈值动态 {1.56 if ms_state=='LOW_VOL' else 0.95}）",
         f"> 分布: 🟢上涨候选 {cnt['UP']} ｜ 🟡底部酝酿 {cnt['BASE']} ｜ 🔴下跌风险 {cnt['DOWN']} ｜ 🟠顶部风险 {cnt['TOP']} ｜ ⚪中性 {cnt['MID']}",
         *env_lines,
         f"> 方法=5年15.5万段条件规则(深跌/段起锚/衰竭缩量/顶部四特征), 非画像匹配分(干净校验未过已降级)",
         "",
         "## 🟢 上涨候选（主升早段·v4b 全市场版·Top30）", "",
         "| 代码 | 名称 | 价(qfq) | 段起距 | 第N日 | pos250 | 三年分位 | 20日 |", "|---|---|---|---|---|---|---|---|"]
    for x in buckets['UP'][:30]:
        L.append(f"| {x['code']} | {x['name']} | {x['px']:.2f} | +{x.get('off_start', 0)}% | 第{x.get('days','?')}日 | {x['pos250']}% | {x.get('p3','?')}% | {x['r20']:+.1f}% |")
    L += ["", "## 🟡 底部酝酿（深跌+缩量未启动·上涨上游·Top20）", ""]
    for x in buckets['BASE'][:20]:
        L.append(f"- {x['code']} {x['name']} {x['px']:.2f} | 年跌{x['yr_dd']}% 缩量{x['shrink']}× pos250={x['pos250']}%")
    L += ["", "## 🔴 下跌风险（空头+负动量未衰竭·Top20 最弱）", ""]
    for x in buckets['DOWN'][:20]:
        L.append(f"- {x['code']} {x['name']} {x['px']:.2f} | 20日{x['r20']:+.1f}% pos250={x['pos250']}% 年跌{x['yr_dd']}% 缩量{x['shrink']}×")
    L += ["", "## 🟠 顶部风险（涨势末端·Top20）", ""]
    for x in buckets['TOP'][:20]:
        L.append(f"- {x['code']} {x['name']} {x['px']:.2f} | 距年低+{x.get('off_lo',0):.0f}% 乖离{x['off']:+.1f}%")
    out_md = os.path.join(ROOT, 'reports', f'direction_census_{today}.md')
    open(out_md, 'w', encoding='utf-8').write('\n'.join(L))
    json.dump({'counts': cnt, 'buckets': {k: v[:80] for k, v in buckets.items()}},
              open(os.path.join(ROOT, 'data', f'direction_census_{today}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"[CENSUS-OK] {out_md} | 🟢{cnt['UP']} 🟡{cnt['BASE']} 🔴{cnt['DOWN']} 🟠{cnt['TOP']} ⚪{cnt['MID']}")


if __name__ == '__main__':
    main()
