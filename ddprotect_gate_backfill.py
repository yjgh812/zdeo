# -*- coding: utf-8 -*-
"""ddprotect_gate_backfill.py — 门控历史回填(只读, 一次性).
与 ddprotect_gate_daily.py 同口径(池指数cumprod vs MA20, 起点2020-01-02):
逐日门控态+翻转统计, 供 floor 档位决策与影子期背景.
输出: data/ddprotect_gate_history.jsonl + data/ddprotect_gate_history_summary.json
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'D:\zcodesj')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
HIST = ROOT / 'data' / 'ddprotect_gate_history.jsonl'
SUM = ROOT / 'data' / 'ddprotect_gate_history_summary.json'


def main() -> int:
    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    df = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>='20200101' GROUP BY date ORDER BY date", con)
    con.close()
    r = df.set_index(pd.to_datetime(df['date'].astype(str), format='%Y%m%d'))['ret'].astype(float)
    idx = (1 + r).cumprod()
    ma = idx.rolling(20).mean()
    # 滞回2%状态机(rule_version=hyst2%, 与 ddprotect_gate_daily/V6 同口径, 2026-09-14起)
    HYST = 0.02
    cur, states = False, []
    for t in idx.index:
        if (not cur) and (idx.loc[t] < ma.loc[t] * (1 - HYST)):
            cur = True
        elif cur and (idx.loc[t] > ma.loc[t] * (1 + HYST)):
            cur = False
        states.append(cur)
    gate = pd.Series(states, index=idx.index)
    valid = ma.notna()

    lines = []
    for d in idx.index[valid]:
        # 注意: gate序列True=防御态(滞回状态机), 打标须反转
        lines.append({'date': str(d.date()), 'gate': '防御档' if gate.loc[d] else '满仓',
                      'gap_pct': round((idx.loc[d] / ma.loc[d] - 1) * 100, 2)})
    HIST.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in lines) + '\n', encoding='utf-8')

    # 翻转统计
    g = [x['gate'] for x in lines]
    flips = sum(1 for i in range(1, len(g)) if g[i] != g[i - 1])
    # 防御档片段时长
    episodes, cur = [], 0
    for x in lines:
        if x['gate'] == '防御档':
            cur += 1
        elif cur:
            episodes.append(cur)
            cur = 0
    if cur:
        episodes.append(cur)
    by_year = {}
    for x in lines:
        y = x['date'][:4]
        by_year.setdefault(y, []).append(x['gate'] == '防御档')
    year_ratio = {y: round(float(np.mean(v)) * 100, 1) for y, v in by_year.items()}
    cur_len = len(episodes) and episodes[-1] if g[-1] == '防御档' else 0
    summary = {'window': [lines[0]['date'], lines[-1]['date']], 'days': len(lines),
               'defense_ratio_pct': round(float(np.mean([x['gate'] == '防御档' for x in lines])) * 100, 1),
               'flips_total': flips, 'flips_per_year': round(flips / (len(lines) / 242), 1),
               'defense_episodes': len(episodes),
               'defense_episode_days_median': float(np.median(episodes)) if episodes else None,
               'defense_episode_days_max': int(max(episodes)) if episodes else None,
               'defense_ratio_by_year_pct': year_ratio,
               'current_episode': {'state': g[-1], 'days': cur_len}}
    SUM.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f'回填完成: {HIST} ({len(lines)}日)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
