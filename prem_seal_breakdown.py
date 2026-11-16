#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prem_seal_breakdown.py — 溢价率分层 × 晋级率分层 × 封成比 侦察件
================================================================================
性质：侦察级（RECON，2026-09-30 学习落地A项）——只读、不接 cron、不进信号、
     不拦交易、无消费方；产出仅供每日复盘人工参考。
来源：reports/SENTIMENT_CYCLE_MONEY_EFFECT_DEEP_STUDY_20260930.md §7.3 落地A项。
     系统既有 owner 不动：全口径溢价 = prem_seal_daily.py（22:00链第十一步）；
     整体晋级率 = zt_sentiment_calibrator.py；本件只做二者之下钻分层，数据同源
     （dragon_quant.db stock_daily_hsj + price_limits 唯一涨停口径），绝不清算/覆写
     上述两件的产物。

三个下钻（口径全部对齐流派并显式标注；阈值含流派经验值，本件只算数不判定）：
  1) 溢价率分层：昨日涨停股按 连板(streak>=2)/首板(streak==1) 分组 → 今日均涨幅/
     中位数/涨跌家数。对齐流派"昨日连板指数 / 昨日首板指数"两口径；一字板无买入
     机会，均值含一字仅作全口径参照（与 prem_seal_daily 全口径一致性互验）。
  2) 晋级率分层：1进2 / 2进3 / 3进4+ （昨日N连板今日再涨停比例）。
     流派参考刻度（经验值，zt_sentiment_calibrator 刻度同源）：>=30% 强 / <15% 退潮。
  3) 封成比（金额比口径）：seal_snapshot 最新快照 sealed_amount / 当日成交额。
     🔴口径声明：流派原口径为"封单量÷成交量"（量比），系统 seal_snapshot 有
     sealed_vol_hand 但 hsj 成交量单位不稳，暂以金额比近似并如实标注，两者不可混读。
     流派经验（未验证）：封成比>10 为极强封板，次日涨停开盘概率经验 70%+。

用法：
  python prem_seal_breakdown.py                # 主库最新交易日
  python prem_seal_breakdown.py --date 20260929
  python prem_seal_breakdown.py --json         # 追加写 reports/prem_seal_breakdown_latest.json
输出：控制台 + (--json) reports/prem_seal_breakdown_latest.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import price_limits  # noqa: E402  涨停判定唯一口径

MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
ATTR_DB = Path(r'D:\zcodesj\attribution\attribution.db')
LATEST_JSON = Path(r'D:\zcodesj\reports') / 'prem_seal_breakdown_latest.json'
LOOKBACK_DATES = 13   # 连板回溯窗口（覆盖 11 板上限绰绰有余）
MAX_STREAK_GROUP = 3  # 3进4+ 归并为一组


def load_history(c: sqlite3.Connection, target: str) -> tuple[list[str], dict[str, dict[str, float]], dict[str, str]]:
    """取 target 及之前 LOOKBACK_DATES 个交易日的 close/amount + 名称。
    日期列实际为 INTEGER 存储, 读出一律归一化 str, 消除与 CLI 参数的类型分裂。"""
    dates = sorted(str(r[0]) for r in c.execute(
        'SELECT DISTINCT date FROM stock_daily_hsj WHERE date <= ? ORDER BY date DESC LIMIT ?',
        (target, LOOKBACK_DATES)))
    closes: dict[str, dict[str, float]] = {}
    amounts: dict[str, dict[str, float]] = {}
    names: dict[str, str] = {}
    ph = ','.join('?' * len(dates))
    for code, d, close, amount, _ in c.execute(
            f'SELECT code, date, close, amount, NULL FROM stock_daily_hsj WHERE date IN ({ph})',
            dates):
        if close:
            ds = str(d)
            closes.setdefault(code, {})[ds] = float(close)
            if amount:
                amounts.setdefault(code, {})[ds] = float(amount)
    for code, name in c.execute('SELECT code, name FROM stock_info'):
        if name:
            names[code] = name
    return dates, closes, amounts, names


def is_lu(code: str, prev_close: float, close: float, d: str, name: str) -> bool:
    try:
        return bool(price_limits.is_limit_up(code, prev_close, close, _d8(d), name=name))
    except Exception:
        return False


def _d8(d: str):
    from datetime import date as _date
    return _date(int(d[:4]), int(d[4:6]), int(d[6:]))


def streak_of(code: str, dates: list[str], closes: dict[str, dict[str, float]],
              prev: str, name: str) -> int:
    """昨日(prev)涨停成立前提下，向前数连续涨停板数。"""
    streak = 1
    for i in range(len(dates) - 2, 0, -1):  # dates[i] 从 prev2 开始向前
        d, d_prev = dates[i], dates[i - 1]
        cl, clp = closes.get(code, {}).get(d), closes.get(code, {}).get(d_prev)
        if not cl or not clp:
            break
        if is_lu(code, clp, cl, d, name):
            streak += 1
        else:
            break
    return streak


def group_stats(vals: list[float]) -> dict:
    if not vals:
        return {'n': 0}
    return {'n': len(vals), 'mean': round(sum(vals) / len(vals), 2),
            'median': round(statistics.median(vals), 2),
            'up': sum(1 for v in vals if v > 0), 'down': sum(1 for v in vals if v < 0)}


def seal_ratio_block(c: sqlite3.Connection, target: str) -> dict | None:
    """封成比（金额比口径）。快照日取 <= target 最新；成交额取同日 hsj.amount。"""
    a = sqlite3.connect(f'file:{ATTR_DB}?mode=ro', uri=True)
    row = a.execute('SELECT MAX(date) FROM seal_snapshot WHERE date <= ?', (target,)).fetchone()
    if not row or not row[0]:
        return None
    snap_date = row[0]
    snap = a.execute(
        'SELECT code, name, sealed_amount, sealed FROM seal_snapshot WHERE date = ?',
        (snap_date,)).fetchall()
    a.close()
    if not snap:
        return None
    ratios = []
    for code, name, sealed_amount, sealed in snap:
        if not sealed or not sealed_amount:
            continue
        amt = None
        # 成交额延迟查主库（同日）
        global _AMT_CACHE
        amt = _AMT_CACHE.get(code)
        if amt:
            ratios.append((code, name or '', sealed_amount / amt))
    if not ratios:
        return {'snap_date': snap_date, 'note': '无可用成交额配对', 'n': 0}
    vals = sorted(r[2] for r in ratios)
    top10 = sorted(ratios, key=lambda r: -r[2])[:10]
    return {
        'snap_date': snap_date, 'snap_rows': len(snap), 'n': len(vals),
        'median': round(statistics.median(vals), 4), 'mean': round(sum(vals) / len(vals), 4),
        'gt10': sum(1 for v in vals if v > 10),
        'top10': [{'code': r[0], 'name': r[1], 'ratio': round(r[2], 4)} for r in top10],
        'caliber': 'sealed_amount/hsj.amount 金额比(流派原口径为量比,不可混读,阈值>10对金额比无意义)',
    }


_AMT_CACHE: dict[str, float] = {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='', help='归属交易日 YYYYMMDD，默认主库最新')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
        target = str(args.date or c.execute('SELECT MAX(date) FROM stock_daily_hsj').fetchone()[0])
        dates, closes, amounts, names = load_history(c, target)
        global _AMT_CACHE
        # amounts 结构为 {code: {date: amount}}; 按 code 提取 target 当日成交额
        _AMT_CACHE = {code: amts[target] for code, amts in amounts.items() if amts.get(target)}
        if target not in dates or len(dates) < 3:
            print(f'[ERR] {target} 数据不足(可用日 {dates[-3:]})')
            return 2
        i = dates.index(target)
        prev, prev2 = dates[i - 1], dates[i - 2]

        # 昨日涨停候选（与 prem_seal_daily 同一判定链）
        cands = []
        for code in closes:
            cl, clp = closes[code].get(prev), closes[code].get(prev2)
            name = names.get(code)
            if not cl or not clp or not name:
                continue
            if is_lu(code, clp, cl, prev, name):
                streak = streak_of(code, dates[:i], closes, prev, name)
                tcl = closes[code].get(target)
                today_pct = (tcl - cl) / cl * 100 if tcl else None
                lu_today = is_lu(code, cl, tcl, target, name) if tcl else False
                cands.append({'code': code, 'name': name, 'streak': streak,
                              'today_pct': today_pct, 'lu_today': lu_today})

    if not cands:
        print(f'[ERR] {target}: 昨日({prev})涨停候选为空')
        return 2

    fb = [x for x in cands if x['streak'] == 1]           # 首板
    lb = [x for x in cands if x['streak'] >= 2]           # 连板
    lb2 = [x for x in cands if x['streak'] == 2]          # 恰好2板
    lb3 = [x for x in cands if x['streak'] >= 3]          # 3板及以上
    fb_vals = [x['today_pct'] for x in fb if x['today_pct'] is not None]
    lb_vals = [x['today_pct'] for x in lb if x['today_pct'] is not None]

    def promo(g: list[dict]) -> dict | None:
        n = len(g)
        if not n:
            return None
        k = sum(1 for x in g if x['lu_today'])
        return {'n': n, 'promoted': k, 'rate': round(k / n * 100, 1)}

    # 修复质量背离标记(提案②, 2026-10-01 主人「测试后按推荐继续」批准)
    # 定义: 1进2晋级率>=25% ∧ 全口径溢价中位>0 ∧ 当日涨停家数<40 → 「当日缓买/少买」提示
    # 依据: 10/1 六轮验证——9/29背离→9/30涨停溢价-0.67%分化兑现; 历史52个背离日次日prem中位+0.68%/负率36.5% vs 非背离+1.02%/30.5%(方向一致幅度温和); Z2s背离日win36.8%vs55.7%
    lu_today_count = 0
    for code in closes:
        cl_t, cl_p = closes[code].get(target), closes[code].get(prev)
        name = names.get(code)
        if cl_t and cl_p and name and is_lu(code, cl_p, cl_t, target, name):
            lu_today_count += 1
    _p1j2 = promo(fb)
    all_median = group_stats([x['today_pct'] for x in cands if x['today_pct'] is not None])['median']
    diverge = bool(_p1j2 and _p1j2['rate'] >= 25 and all_median is not None and all_median > 0
                   and lu_today_count < 40)

    result = {
        'date': target, 'prev': prev, 'schema': 'prem_seal_breakdown_v2_recon',
        'note': '侦察级:不进信号不拦交易,仅供复盘;阈值口径见脚本头',
        'all': group_stats([x['today_pct'] for x in cands if x['today_pct'] is not None]),
        'first_board': group_stats(fb_vals),
        'lian_board': group_stats(lb_vals),
        'promotion': {'1j2': promo(fb), '2j3': promo(lb2), '3j4p': promo(lb3)},
        'seal_ratio': seal_ratio_block(c, target),
        'divergence': {'lu_today': lu_today_count, 'flag': diverge,
                       'rule': 'promo1j2>=25% ∧ prem_median>0 ∧ lu_today<40',
                       'hint': '修复质量背离日: 当日缓买/少买(呈现级提示)'},
    }

    print(f"[RECON] prem_seal_breakdown {target} (昨日={prev} 涨停候选 {len(cands)} 只)")
    a = result['all']; f = result['first_board']; l = result['lian_board']
    print(f"  全口径: n={a['n']} 均值{a['mean']:+.2f}% 中位{a['median']:+.2f}% 涨{a['up']}/跌{a['down']}")
    if f.get('n'):
        print(f"  首板组: n={f['n']} 均值{f['mean']:+.2f}% 中位{f['median']:+.2f}% 涨{f['up']}/跌{f['down']}")
    else:
        print('  首板组: n=0')
    if l.get('n'):
        print(f"  连板组: n={l['n']} 均值{l['mean']:+.2f}% 中位{l['median']:+.2f}% 涨{l['up']}/跌{l['down']}")
    else:
        print('  连板组: n=0')
    p = result['promotion']
    for k, label in (('1j2', '1进2'), ('2j3', '2进3'), ('3j4p', '3进4+')):
        if p.get(k):
            print(f"  {label}: {p[k]['promoted']}/{p[k]['n']} = {p[k]['rate']}%")
        else:
            print(f"  {label}: 样本0")
    s = result.get('seal_ratio')
    if s and s.get('n'):
        print(f"  封成比(金额比,{s['snap_date']}): 快照{s['snap_rows']}行/配对{s['n']} 中位{s['median']} >10家数={s['gt10']}(量比阈值,金额比仅参照)")
        for t in s['top10'][:5]:
            print(f"    {t['code']} {t['name']} {t['ratio']}")
    elif s:
        print(f"  封成比: {s}")
    dv = result['divergence']
    flag_txt = "🔴是 → 当日缓买/少买(呈现级)" if dv['flag'] else "否"
    print(f"  修复质量背离(提案②): 当日涨停{dv['lu_today']}家 → {flag_txt}")

    if args.json:
        LATEST_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"  [save] {LATEST_JSON}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
