#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prem_seal_daily.py — 昨日涨停今日均涨幅（情绪最灵敏温度计）每日自动计算
================================================================================
铁律依据（2026-08-15第三轮自省发现）：
  手册SC-001定义prem_seal为"情绪最灵敏温度计"，熔断规则依赖
  （prem_seal.rolling(20).mean()<=0 → 空仓），但此前无任何自动计算工具——
  实战分析漏掉该信号（8/14实际=-0.13%亏钱效应，此前分析只看涨停数误判温和）。

口径：
  昨日涨停股（按板块分阈值）在今日的平均涨幅
  >+2% 情绪高涨 | 0~+2% 温和 | <0 亏钱效应（熔断预警）

v2（2026-08-22 升级，盘感模块全面落地配套）：
  - 新增 --json：写 reports/prem_seal_latest.json（稳定路径，供 vibe_card / 熔断消费）
  - 新增历史序列 _cache/prem_seal_history.csv（幂等，同 date 覆盖），并计算 rolling20 均值
    → SC-001 熔断：prem_seal<=0 → 亏钱效应预警；prem_seal.rolling20<=0 → 空仓提示
  - 接入 22:00 全管线（第十一步）每日自动执行

用法：
  python prem_seal_daily.py                # 最新交易日，控制台
  python prem_seal_daily.py --date 20260814
  python prem_seal_daily.py --json         # 写 latest.json + 历史序列
"""
import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
import price_limits  # noqa: E402  涨停判定唯一口径

MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
REPORT_DIR = Path(r'D:\zcodesj\reports')
CACHE = Path(r'D:\zcodesj\_cache')
LATEST_JSON = REPORT_DIR / 'prem_seal_latest.json'
HISTORY_CSV = CACHE / 'prem_seal_history.csv'


def limit_threshold(code: str) -> float:
    """[2026-08-22 已弃用，保留仅作回退] 粗糙板块阈值。新逻辑一律走 price_limits.is_limit_up。"""
    if code[:2] in ('30', '68'):
        return 19.9
    if code[0] in ('8', '4') and code[:2] != '68':
        return 29.9
    return 9.9


def compute(target: str) -> dict | None:
    """计算T日的prem_seal：T-1涨停股在T日的平均涨幅。
    T-1涨停判定走 price_limits.is_limit_up 唯一口径
    （ST 5%/北交所 30%/2026-07-06 主板ST 5%→10%切换日 全部自动处理）。
    2026-08-22 对齐：原粗糙阈值法(9.9/19.9/29.9)不识别 ST 与切换日。"""
    with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
        prev = c.execute(
            'SELECT MAX(date) FROM stock_daily_hsj WHERE date < ?', (target,)
        ).fetchone()[0]
        if not prev:
            return None
        prev2 = c.execute(
            'SELECT MAX(date) FROM stock_daily_hsj WHERE date < ?', (prev,)
        ).fetchone()[0]
        rows = c.execute('''
            SELECT t.code, i.name,
            (t.close - y.close) / y.close * 100 as today_pct,
            y.close, z.close
            FROM stock_daily_hsj t
            JOIN stock_daily_hsj y ON t.code = y.code AND y.date = ?
            JOIN stock_daily_hsj z ON y.code = z.code AND z.date = ?
            LEFT JOIN stock_info i ON t.code = i.code
            WHERE t.date = ?''', (prev, prev2, target)).fetchall()

    pdate = date(int(str(prev)[:4]), int(str(prev)[4:6]), int(str(prev)[6:]))
    # T-1涨停（price_limits 唯一口径，剔无名称）
    yest_limit = []
    for code, name, tpct, yclose, zclose in rows:
        if name is None or not yclose or not zclose:
            continue
        try:
            if price_limits.is_limit_up(code, float(zclose), float(yclose), pdate, name=name):
                yest_limit.append((code, name, tpct))
        except Exception:
            continue

    if not yest_limit:
        return {'date': target, 'prev': prev, 'count': 0, 'prem_seal': None}

    avg = sum(x[2] for x in yest_limit) / len(yest_limit)
    up = sum(1 for x in yest_limit if x[2] > 0)
    down = sum(1 for x in yest_limit if x[2] < 0)
    best = max(yest_limit, key=lambda x: x[2])
    worst = min(yest_limit, key=lambda x: x[2])
    return {
        'date': target, 'prev': prev, 'count': len(yest_limit),
        'prem_seal': round(avg, 2), 'up': up, 'down': down,
        'best': (best[0], best[1], round(best[2], 1)),
        'worst': (worst[0], worst[1], round(worst[2], 1)),
    }


def append_history(r: dict) -> dict:
    """写 latest.json + 追加历史序列，返回含 rolling20 的熔断判定。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    # 历史序列（幂等：同 date 覆盖）
    row = {'date': r['date'], 'prem_seal': r['prem_seal'], 'count': r['count'],
           'up': r['up'], 'down': r['down']}
    df = pd.DataFrame([row])
    if HISTORY_CSV.exists():
        old = pd.read_csv(HISTORY_CSV, dtype={'date': str})
        old = old[old['date'] != str(r['date'])]
        df = pd.concat([old, df], ignore_index=True)
    df = df.sort_values('date').drop_duplicates('date', keep='last')
    df.to_csv(HISTORY_CSV, index=False, encoding='utf-8-sig')
    # rolling20 均值
    s = df['prem_seal'].astype(float)
    rolling20 = float(s.tail(20).mean()) if len(s) >= 1 else None
    verdict = ('亏钱效应 ❌ 熔断预警' if r['prem_seal'] <= 0
               else '温和/过渡 🟡 正常仓位' if r['prem_seal'] <= 2
               else '发酵/主升 ✅ 可满仓做主线')
    payload = {
        'date': str(r['date']), 'prem_seal': r['prem_seal'], 'count': r['count'],
        'up': r['up'], 'down': r['down'],
        'best': {'code': r['best'][0], 'name': r['best'][1], 'pct': r['best'][2]},
        'worst': {'code': r['worst'][0], 'name': r['worst'][1], 'pct': r['worst'][2]},
        'rolling20': None if rolling20 is None else round(rolling20, 2),
        'verdict': verdict,
        'sc001_circuit_breaker': r['prem_seal'] <= 0,
        'sc001_empty_advice': rolling20 is not None and rolling20 <= 0,
        'source': 'prem_seal_daily.py (stock_daily_hsj 板块阈值, 剔ST)',
        'history_rows': int(len(df)),
    }
    # 数据就绪守卫（方案A，2026-09-30）：9/29 事故定谳——晚链以"主库最新交易日"为 target 执行，
    # 若当时当日日线未入库，本脚本会把前一日幂等覆盖、当日行静默缺失（无告警）。
    # 此处输出结构化事实（纯追加字段，不改既有字段与 verdict 语义，不涉任何交易决策），
    # 供人工接管/晨间兜底自动化判断是否需补跑。
    import datetime as _dt
    calendar_today = _dt.date.today().strftime('%Y%m%d')
    with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as _c:
        hsj_latest = str(_c.execute('SELECT MAX(date) FROM stock_daily_hsj').fetchone()[0])
    payload['calendar_today'] = calendar_today
    payload['hsj_latest'] = hsj_latest
    if hsj_latest < calendar_today:
        payload['data_note'] = (
            f'DATA-NOT-READY: 主库最新 {hsj_latest} < 日历今日 {calendar_today}; '
            f'若今日为交易日, 待日线入库后须补跑 --date {calendar_today}; '
            f'本次写入为对 {r["date"]} 的幂等覆盖')
        print(f"  [WARN] {payload['data_note']}")
    else:
        payload['data_note'] = ''
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='昨日涨停今日均涨幅（SC-001 情绪温度计）')
    ap.add_argument('--date', default=None, help='目标交易日 YYYYMMDD（默认主库最新）')
    ap.add_argument('--json', action='store_true', help='写 prem_seal_latest.json + 历史序列')
    args = ap.parse_args(argv)

    with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
        target = args.date or c.execute('SELECT MAX(date) FROM stock_daily_hsj').fetchone()[0]

    r = compute(str(target))
    if not r or r['prem_seal'] is None:
        print(f'{target}: 无前日涨停数据')
        return 1

    if args.json:
        payload = append_history(r)
        ps = payload['prem_seal']
        print(f'[SC-001] prem_seal {payload["date"]} = {ps:+.2f}% → {payload["verdict"]}')
        print(f"  样本: {r['prev']}涨停{r['count']}只 → 今日 上涨{r['up']}/下跌{r['down']}")
        print(f"  rolling20 = {payload['rolling20']}% → "
              f"{'空仓熔断提示' if payload['sc001_empty_advice'] else '正常'}")
        print(f"  [save] {LATEST_JSON} | 历史 {payload['history_rows']} 行 {HISTORY_CSV}")
        return 0

    ps = r['prem_seal']
    if ps > 2:
        verdict = '发酵/主升 ✅ 可满仓做主线'
    elif ps > 0:
        verdict = '温和/过渡 🟡 正常仓位'
    else:
        verdict = '亏钱效应 ❌ 熔断预警（rolling20均值<=0时空仓）'
    date_str = r['date']
    print(f'[SC-001] prem_seal {date_str} = {ps:+.2f}% → {verdict}')
    print(f"  样本: {r['prev']}涨停{r['count']}只 → 今日 上涨{r['up']}/下跌{r['down']}")
    print(f"  最佳: {r['best'][0]} {r['best'][1]} {r['best'][2]:+.1f}%")
    print(f"  最差: {r['worst'][0]} {r['worst'][1]} {r['worst'][2]:+.1f}%")
    return 0


if __name__ == '__main__':
    sys.exit(main())
