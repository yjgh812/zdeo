# -*- coding: utf-8 -*-
"""Regime 预算闸门 — 影子期每日评估(2026-09-19 设计文档验收路径第1步)
铁律: 影子期不拦截任何交易, 只输出"若生效今日为何档/当日买入若生效会拦几笔";
     判定顺序 红>绿>黄(高位普涨重叠日记 overlap 标记供影子期观察);
     LOW_VOL 连续3日规则首版用 probe 的 prev_state 近似(≥2日), streak 待 jsonl 累积后回算。
输入: census md env(生产同源, 缺失则本日不入档) / market_state_probe.json / qfq 全市场上涨占比
     (剔688/北交所, 与 data_changedist 口径差异已声明) / executor_orders.jsonl 当日买入。
输出: data/regime_gate_shadow.jsonl (幂等)
"""
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
LEDGER = os.path.join(ROOT, 'data', 'regime_gate_shadow.jsonl')


def tier_of(env, low_vol2, up_pct):
    """红>绿>黄; 返回(tier, overlap)"""
    red = (env is not None and env >= 80) or low_vol2
    green = (env is not None and env < 40) or (up_pct is not None and up_pct > 50)
    if red:
        return ('RED', green)
    if green:
        return ('GREEN', False)
    return ('YELLOW', False)


def main():
    db = sqlite3.connect(DB, uri=True)
    db_max = db.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    asof = db_max  # 默认库最新; 若census md可解析数据日则覆盖(见下)
    env = None
    cands = sorted(glob.glob(os.path.join(ROOT, 'reports', 'direction_census_*.md')))
    if cands:
        txt = open(cands[-1], encoding='utf-8').read()
        # 目标日=census md 大盘前置行标注的数据日(上证 x.xx(YYYYMMDD)), 不是md标题生成日:
        # md每日15:30生成, baostock最新=上一交易日收盘 -> env分位对应括号内日期。
        # 20260925 根因修复: 旧逻辑asof=库MAX(date), 21:10调度时库已到当日而md数据日为昨日且
        # census生成器end_date硬编码致数据日冻结9/18 -> asof not in txt 永远SKIP。双根同修。
        md_data_day = re.search(r'上证 [\d.]+\((\d{8})\)', txt)
        if md_data_day:
            asof = md_data_day.group(1)
        else:
            print('[SKIP] census md 大盘前置行无 (YYYYMMDD) 数据日标注')
            db.close()
            return
        m = re.search(r'年[线][窗]?口?(\d+)分位', txt)
        if m:
            env = int(m.group(1))
    row = db.execute(
        "SELECT SUM(CASE WHEN change_pct>0 THEN 1 ELSE 0 END), COUNT(*) FROM stock_daily_meta_qfq "
        "WHERE date=? AND change_pct IS NOT NULL AND code NOT LIKE '688%' AND length(code)=6 "
        "AND code NOT LIKE '4%' AND code NOT LIKE '8%'", (asof,)).fetchone()
    db.close()
    up_n, tot_n = row
    up_pct = round(100 * up_n / tot_n, 1) if tot_n else None

    state = prev = None
    try:
        p = json.load(open(os.path.join(ROOT, 'data', 'market_state_probe.json'), encoding='utf-8'))
        if p.get('asof') == asof or p.get('date') == asof:
            state, prev = p.get('state'), p.get('prev_state')
    except Exception:
        pass
    low_vol2 = state == 'LOW_VOL' and prev == 'LOW_VOL'

    buys = []
    opath = os.path.join(ROOT, 'data', 'executor_orders.jsonl')
    if os.path.exists(opath):
        for line in open(opath, encoding='utf-8'):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get('trade_date') == asof and o.get('action') == 'BUY':
                buys.append(o)
    buys_amt = round(sum((o.get('price') or 0) * (o.get('qty') or 0) for o in buys))

    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding='utf-8'):
            if line.strip() and json.loads(line).get('date') == asof:
                print(f'[IDEMPOTENT] {asof} already recorded, skip')
                return

    if env is None:
        print(f'[SKIP] {asof}: env not found in latest census md (生产同源缺失不入档)')
        return
    tier, overlap = tier_of(env, low_vol2, up_pct)
    will = [o for o in buys] if tier == 'RED' else []
    rec = {'date': asof, 'env': env, 'state': state, 'prev_state': prev,
           'up_pct': up_pct, 'tier': tier, 'overlap_highgreen': overlap,
           'buys_n': len(buys), 'buys_amt': buys_amt,
           'would_block_n': len(will), 'would_block_amt': round(sum(
               (o.get('price') or 0) * (o.get('qty') or 0) for o in will)),
           'note': 'SHADOW ONLY 不拦截; RED 条件含 LOW_VOL>=2日近似(设计为3日, streak待回算)' if tier == 'RED' else 'SHADOW ONLY 不拦截',
           'ts': datetime.now().strftime('%Y-%m-%d %H:%M')}
    with open(LEDGER, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print(f"[GATE-SHADOW] {asof} tier={tier} env={env} state={state} up%={up_pct} "
          f"buys={rec['buys_n']}/{rec['buys_amt']} would_block={rec['would_block_n']}")


if __name__ == '__main__':
    main()
