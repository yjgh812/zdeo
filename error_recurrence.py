#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""error_recurrence.py — 错误重演检测器（自省第三期长效机制 #4，进化周期 3 落地）

真源: data/error_pattern_registry.json（E1-E12 人轨 + S1-S10 系统轨，含 recurrence_log）
用法:
  --list                    全表浏览（模式+状态+重演计数）
  --check "<新错误描述>"     关键词辅助匹配（辅助非决定；月度自省仍须人工对照全表）
  --hit E6 --note "..." [--variant-of E3] [--escalation "B3"]
                           登记一次重演（计数+1+log 追加；同日同模式幂等拒绝）
  --month 2026-09           当月重演汇总（月度自省§段直接引用）
退出码: 0 正常 / 2 参数或注册表错误 / 3 注册表不可达（fail-loud）
纪律: 本工具不自动判定错误归类——"是否重演"由月度自省人工确认后用 --hit 落账，
     keywords 匹配仅提示候选，防关键词误报漂移（同 E11 教训：先定位真源再下结论）。
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
REG = os.path.join(ROOT, 'data', 'error_pattern_registry.json')


def load():
    if not os.path.exists(REG):
        print('[FAIL] 注册表不存在: %s' % REG, file=sys.stderr)
        sys.exit(3)
    try:
        return json.load(open(REG, encoding='utf-8'))
    except Exception as e:
        print('[FAIL] 注册表解析失败: %s' % e, file=sys.stderr)
        sys.exit(3)


def save(reg):
    tmp = REG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, REG)


def find(reg, pid):
    for tr in ('human_track', 'system_track'):
        if pid in reg[tr]:
            return tr, reg[tr][pid]
    return None, None


def main():
    ap = argparse.ArgumentParser(description='错误重演检测器（月度自省配套）')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--check')
    ap.add_argument('--hit')
    ap.add_argument('--note')
    ap.add_argument('--variant-of', dest='variant_of')
    ap.add_argument('--escalation')
    ap.add_argument('--month')
    ap.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'))
    args = ap.parse_args()
    reg = load()

    if args.list or (not args.check and not args.hit and not args.month):
        print('=== 错误模式注册表 v%s（建账 %s）===' % (reg['version'], reg['established']))
        for tr, tag in (('human_track', '人轨'), ('system_track', '系统轨')):
            print('\n[%s]' % tag)
            for pid in sorted(reg[tr]):
                p = reg[tr][pid]
                print(' %s %-14s 重演%d次 %-22s %s' % (
                    pid, p['name'], p['recurrence_count'], p['status'], p['measures'][:40]))
        print('\n[重演日志] %d 条' % len(reg['recurrence_log']))
        for r in reg['recurrence_log'][-8:]:
            print(' %s %s <- %s %s' % (r['date'], r['pattern'], r.get('variant_of', '-'), r['note'][:40]))
        return 0

    if args.check:
        text = args.check
        hits = []
        for tr in ('human_track', 'system_track'):
            for pid, p in reg[tr].items():
                kws = [k for k in p.get('keywords', []) if k in text]
                if kws:
                    hits.append((pid, p['name'], kws))
        print('=== 关键词辅助匹配（候选，须人工确认）===')
        if not hits:
            print(' 无命中 → 可能是新错误模式：评估是否新增注册表条目（防 E12 转化率不足）')
        else:
            for pid, name, kws in sorted(hits):
                tr, p = find(reg, pid)
                print(' 候选 %s %s（重演%d次，关键词:%s）' % (pid, name, p['recurrence_count'], '/'.join(kws)))
            print(' 人工确认重演后: python error_recurrence.py --hit <编号> --note "<描述>"')
        return 0

    if args.hit:
        tr, p = find(reg, args.hit)
        if p is None:
            print('[FAIL] 未知模式编号: %s（先 --list）' % args.hit, file=sys.stderr)
            return 2
        if not args.note:
            print('[FAIL] --hit 必须带 --note（质量门：无指针/描述的登记=形式化）', file=sys.stderr)
            return 2
        if any(r['pattern'] == args.hit and r['date'] == args.date for r in reg['recurrence_log']):
            print('[SKIP] 同日同模式已登记（幂等拒绝）')
            return 0
        p['recurrence_count'] += 1
        entry = {'date': args.date, 'pattern': args.hit, 'note': args.note}
        if args.variant_of:
            entry['variant_of'] = args.variant_of
        if args.escalation:
            entry['measure_escalation'] = args.escalation
        reg['recurrence_log'].append(entry)
        save(reg)
        tr2, p2 = find(reg, args.hit)
        print('[HIT] %s %s 重演第%d次已登记（措施须按强度升级，见自省#4）' % (
            args.hit, p2['name'], p2['recurrence_count']))
        return 0

    if args.month:
        rows = [r for r in reg['recurrence_log'] if r['date'].startswith(args.month)]
        print('=== %s 重演汇总（月度自省引用段）===' % args.month)
        if not rows:
            print(' 本月零重演登记。注意：零登记≠零重演——若本月有错误但未对照，先人工对照全表（--list）')
        for r in rows:
            print(' %s %s <-%s %s | 升级:%s' % (
                r['date'], r['pattern'], r.get('variant_of', '-'),
                r['note'][:50], r.get('measure_escalation', '-')))
        return 0


if __name__ == '__main__':
    sys.exit(main())
