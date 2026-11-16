# -*- coding: utf-8 -*-
"""ddprotect_shadow_review.py — 回撤保护门控影子期评审器(只读).

用途: 影子期满(约2026-10-14)一条命令出证据包, 供主人裁决是否修订§10-2启用卖出联动.
输入: data/ddprotect_gate_history.jsonl(滞回2%逐日门控) + data/ddprotect_gate_state.json(当前态).
影子期起点: 2026-09-14(rule_version=hyst2%上线日, 宪法§10-2改约决策锚点).
输出: stdout + data/ddprotect_shadow_review_<asof>.json. 零交易联动.
用法: python ddprotect_shadow_review.py [--asof YYYYMMDD]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r'D:\zcodesj')
HIST = ROOT / 'data' / 'ddprotect_gate_history.jsonl'
STATE = ROOT / 'data' / 'ddprotect_gate_state.json'
SHADOW_START = '2026-09-14'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', default=datetime.now().strftime('%Y%m%d'))
    a = ap.parse_args()
    asof = f'{a.asof[:4]}-{a.asof[4:6]}-{a.asof[6:]}' if '-' not in a.asof else a.asof

    if not HIST.exists() or HIST.stat().st_size == 0:
        print('[FAIL] 门控历史台账缺失, 先跑 ddprotect_gate_backfill.py')
        return 2
    lines = [json.loads(l) for l in HIST.read_text(encoding='utf-8').splitlines() if l.strip()]
    shadow = [x for x in lines if SHADOW_START <= x['date'] <= asof]
    if len(shadow) < 5:
        print(f'[WAIT] 影子期仅{len(shadow)}日(<5), 评审期未满. 当前: '
              f"{shadow[-1]['gate'] if shadow else 'N/A'}")
        return 0

    g = [x['gate'] for x in shadow]
    flips = [(shadow[i]['date'], f'{g[i-1]}→{g[i]}') for i in range(1, len(g)) if g[i] != g[i - 1]]
    days_def = g.count('防御档')
    episodes, cur = [], 0
    for x in g:
        if x == '防御档':
            cur += 1
        elif cur:
            episodes.append(cur)
            cur = 0
    if cur:
        episodes.append(cur)

    full = [x['gate'] for x in lines]
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    res = {'asof': asof, 'shadow_start': SHADOW_START, 'shadow_days': len(shadow),
           'days_defense': days_def, 'days_full': len(g) - days_def,
           'flips': flips, 'n_flips': len(flips),
           'defense_episodes': len(episodes),
           'episode_median_days': sorted(episodes)[len(episodes) // 2] if episodes else None,
           'current': {'gate': state.get('gate'), 'since': None, 'suggest': state.get('suggest_exposure')},
           'context_since_2020': {'defense_ratio_pct': round(full.count('防御档') / len(full) * 100, 1),
                                  'flips_per_year': round(
                                      sum(1 for i in range(1, len(full)) if full[i] != full[i - 1])
                                      / (len(full) / 242), 1)},
           'decision_note': ('§10-2改约裁决证据包: 影子期内门控态与翻转如实呈现; '
                             '启用卖出联动=主人修约决策, 系统不自动执行')}
    out = ROOT / 'data' / f'ddprotect_shadow_review_{a.asof}.json'
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"影子期 {SHADOW_START}~{asof}: {len(shadow)}日 "
          f"(防御{days_def}日/满仓{len(g) - days_def}日, 翻转{len(flips)}次)")
    for d, f_ in flips:
        print(f'  翻转: {d} {f_}')
    print(f"当前: {state.get('gate')} 建议敞口={state.get('suggest_exposure')}")
    print(f'证据包已写: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
