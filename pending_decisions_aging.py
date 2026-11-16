#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""pending_decisions_aging.py — 悬置决策时效台账检测器
（自省第三期长效机制 #2，2026-09-20 「B2 悬置 48h 机制化」；进化周期 2 落地）

真源: data/pending_decisions_ledger.jsonl（唯一结构化台账；
      reports/PENDING_DECISIONS_*.md = 人读研究报告快照，不是本脚本输入）
规则: status=open 且 category != system_backlog 的项，
      自 raised_date 之后经过的**已收盘交易日**数 > sla_trading_days → OVERDUE
交易日源: qfq 主库 distinct date（只读零 MCP 依赖；语义=已收盘，周日跑用最后交易日）
输出: reports/pending_aging_YYYYMMDD.md 全量表 + OVERDUE 项 toast（notify 本机通道）
     + 视图↔真源 diff 段（S4 2026-09-28 全维度自省：PENDING_DECISIONS.md 是手维视图，
       9/28 实测其把已销账的 PD-022 仍列 open=裁决漏执行风险，日检漂移）
退出码: 0=正常（含存在 OVERDUE，报告与 toast 即为产物）
        2=台账坏行/缺字段（fail-loud）
        3=交易日源不可用（fail-loud，禁止拿自然日冒充交易日=伪健康）
        4=视图↔真源 open 集合漂移（fail-loud，视图须按本表订正后复跑归零）
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
LEDGER = os.path.join(ROOT, 'data', 'pending_decisions_ledger.jsonl')
VIEW = os.path.join(ROOT, 'PENDING_DECISIONS.md')
QFQ_DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
REPORT = os.path.join(ROOT, 'reports', 'pending_aging_%s.md')


def load_ledger():
    rows, bad = [], 0
    if not os.path.exists(LEDGER):
        print('[FAIL] 台账不存在: %s' % LEDGER, file=sys.stderr)
        sys.exit(2)
    for line in open(LEDGER, encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            # 20261011 triage: 台账schema漂移兼容——PD体系新条目用 pd_id/date 键且无
            # category(10/7起25条实锤); 归一到解析合同, 旧schema零影响。
            # 否则每周五 15:45 aging 必 fail-loud rc=2(PendingAging_1545 10/9 实锤)。
            r.setdefault('id', r.get('pd_id'))
            r.setdefault('raised_date', r.get('date'))
            r.setdefault('category', 'PD')
            assert r.get('id') and r.get('raised_date') and r.get('status') \
                and r.get('category') and r.get('title')
            rows.append(r)
        except Exception as e:
            bad += 1
            print('[WARN] 坏行跳过: %s (%s)' % (line[:60], e), file=sys.stderr)
    if bad:
        print('[FAIL] 台账坏行 %d 条（fail-loud）' % bad, file=sys.stderr)
        sys.exit(2)
    return rows


def load_trade_dates(min_date):
    """qfq 主库 distinct date（str YYYYMMDD，升序）。不可用=exit 3。"""
    try:
        con = sqlite3.connect(QFQ_DB, uri=True)
        ds = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date",
            (min_date.replace('-', ''),))]
        con.close()
    except Exception as e:
        print('[FAIL] 交易日源不可用: %s' % e, file=sys.stderr)
        sys.exit(3)
    if not ds:
        print('[FAIL] 交易日源为空(库无 %s 后数据)' % min_date, file=sys.stderr)
        sys.exit(3)
    return ds


def load_view_open_ids():
    """解析视图 PENDING_DECISIONS.md 的「待裁决」表 open id 集合。

    返回 (ids:set|None, err:str|None)。文件缺失/无该小节=err（不阻断 aging 主流程，
    但 drift 视为不可判定并在报告与退出码中如实呈现，禁止默认"无漂移"=伪健康）。
    """
    if not os.path.exists(VIEW):
        return None, '视图文件缺失: %s' % VIEW
    ids, in_open = set(), False
    for line in open(VIEW, encoding='utf-8'):
        s = line.strip()
        if s.startswith('## '):
            in_open = ('待裁决' in s)
            continue
        if in_open:
            m = re.match(r'^\|\s*(PD-\d{3})\s*\|', s)
            if m:
                ids.add(m.group(1))
    return ids, None


def main():
    t0 = datetime.now()
    rows = load_ledger()
    min_raised = min(r['raised_date'] for r in rows).replace('-', '')
    ds = load_trade_dates(min_raised)
    qfq_max = ds[-1]

    # aging = 严格晚于 raised_date 且 <= qfq_max 的交易日数（提请当日不计）
    def aging(r):
        rd = r['raised_date'].replace('-', '')
        return sum(1 for d in ds if rd < d <= qfq_max)

    table, overdue = [], []
    for r in sorted(rows, key=lambda x: (x['status'] != 'open', x['raised_date'])):
        a = aging(r) if r['status'] == 'open' else None
        sla = int(r.get('sla_trading_days', 2))
        od = r['status'] == 'open' and r['category'] != 'system_backlog' and a > sla
        if od:
            overdue.append((r, a, sla))
        table.append({
            'id': r['id'], 'title': r['title'][:46], 'category': r['category'],
            'raised': r['raised_date'], 'aging': a, 'sla': sla,
            'status': r['status'] + (' **OVERDUE**' if od else ''),
        })

    lines = ['# 悬置决策时效台账 (%s)' % t0.strftime('%Y-%m-%d %H:%M'),
             '',
             '- 交易日锚: qfq_max=%s（已收盘口径）| 台账 %d 条(open=%d, disposed=%d) | OVERDUE=%d'
             % (qfq_max, len(rows),
                sum(1 for r in rows if r['status'] == 'open'),
                sum(1 for r in rows if r['status'] == 'disposed'), len(overdue)),
             '', '| id | 事项 | 类别 | 提请日 | 交易日aging | SLA | 状态 |',
             '|---|---|---|---|---|---|---|']
    for t in table:
        lines.append('| %s | %s | %s | %s | %s | %s | %s |' % (
            t['id'], t['title'], t['category'], t['raised'],
            t['aging'] if t['aging'] is not None else '-',
            t['sla'] if t['category'] != 'system_backlog' else 'backlog',
            t['status']))
    if overdue:
        lines += ['', '## 🔴 OVERDUE（>SLA 交易日未决，已 toast）', '']
        for r, a, sla in overdue:
            lines.append('- **%s %s**：aging %d/%d（提请 %s）—— %s'
                         % (r['id'], r['title'][:40], a, sla, r['raised_date'],
                            r.get('evidence', '')[:80]))

    # S4(2026-09-28 全维度自省): 视图↔真源 open 集合 diff 守卫。视图=手维产物必漂，
    # 漂移=裁决漏执行风险（9/28 实锤 PD-022 销账后视图仍列 open）。不可解析≠无漂移，
    # 如实按漂移处理，禁止伪健康。
    ledger_open = {r['id'] for r in rows if r['status'] == 'open'}
    view_open, view_err = load_view_open_ids()
    drift_view = drift_ledger = set()
    if view_open is not None:
        drift_view = view_open - ledger_open    # 视图 open/真源已处置 = 视图漏销
        drift_ledger = ledger_open - view_open  # 真源 open/视图漏列 = 视图漏登
    if view_open is not None and not (drift_view or drift_ledger):
        lines += ['', '- 视图↔真源：✅ open 集合一致（%d 条，PENDING_DECISIONS.md）' % len(ledger_open)]
    else:
        head = ('## 🔴 视图不可解析（S4 守卫）' if view_open is None
                else '## 🔴 视图↔真源漂移（S4 守卫，rc=4）')
        lines += ['', head, '']
        if view_open is None:
            lines.append('- %s' % view_err)
        for pid in sorted(drift_view):
            lines.append('- %s：视图列 open 而真源已处置 → 视图漏销，须按 ledger 订正视图' % pid)
        for pid in sorted(drift_ledger):
            lines.append('- %s：真源 open 而视图缺列 → 视图漏登（呈报漏见风险）' % pid)

    lines += ['', '> 真源=data/pending_decisions_ledger.jsonl；处置后由执行方改 status=disposed+disposition（幂等 UPSERT 语义）。']
    rep = REPORT % t0.strftime('%Y%m%d')
    open(rep, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

    drift_n = len(drift_view) + len(drift_ledger) + (1 if view_open is None else 0)
    open_n = sum(1 for r in rows if r['status'] == 'open')
    print('[AGING] open=%d overdue=%d drift=%d -> %s' % (open_n, len(overdue), drift_n, rep))
    if overdue:
        import notify
        txt = '; '.join('%s %s(%d/%d交易日)' % (r['id'], r['title'][:24], a, s)
                        for r, a, s in overdue[:3])
        notify.deliver('悬置决策超时提醒', txt, 'ERROR')
    if drift_n:
        import notify
        notify.deliver('PD视图↔真源漂移(S4)',
                       '漂移=%d（视图漏销%s/漏登%s/不可解析%s）详见 %s'
                       % (drift_n, len(drift_view), len(drift_ledger),
                          1 if view_open is None else 0, rep), 'WARN')
    print('[DONE] qfq_max=%s overdue=%d drift=%d' % (qfq_max, len(overdue), drift_n))
    return 4 if drift_n else 0


if __name__ == '__main__':
    sys.exit(main())
