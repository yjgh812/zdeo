#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
verify_regression_test.py — m7校验器全量回归测试套件（t22交付）
================================================================================
背景: verify_agent_output.py 曾出漏放BUG（\w含中文），证明校验器自己也会坏。
每次改动 verify_agent_output.py 后必须跑本套件全绿才算改完。

用例矩阵（每条 = 输入报告 + 数据包 + 预期verdict + 预期触发的校验项类型）:
  现有样本（D:/zcodesj/.gate_logs/_bugtest/）:
    1  bad_report.md          → REJECTED  范式缺失/段序错误/结论先行/编造数字/预判未标
    2  fake_report.md         → REJECTED  编造数字 + 预判未标
    3  good_report.md         → PASS
    4  report_E1.md           → REJECTED  编造数字/结论先行/反向缺失/预判未标/编造来源
    5  report_E2.md           → REJECTED  编造代码/编造数字/范式缺失/预判未标/编造来源
    6  report_E3.md           → REJECTED  编造代码/编造数字/预判未标/编造来源
    7  report_compliant_E1.md → PASS
  对抗用例（t22新增，adv_*.md）:
    8  adv_predgap.md         → REJECTED  预判未标注（词库缺口: "大概率向上，有机会走强"
                                   ——2026-08-17 t22暴露，已当场补录 大概率/有机会/走强/走弱）
    9  adv_date_disguise.md   → REJECTED  编造数字（数字伪装日期格式 "2026.99" 测白名单绕过）
   10  adv_concl_pct.md       → REJECTED  编造数字（结论段塞包外百分比 "涨幅66.6%" 测溯源覆盖）

历史修复回归锚点:
  - 2026-08-17 t22: ① PREDICTION_RE 补录 大概率|有机会|走强|走弱（对抗用例A1暴露的漏放）
                    ② check_paradigm 段序检查死代码修复：原实现按范式序构造present导致
                       ranks恒升序、永不触发；改为按文中物理位置排序后比对
                       （bad_report "结论在前论据在后" 从此真正被拦截，见用例1）

CLI: python verify_regression_test.py
退出码: 0=全部通过 / 1=存在失败用例（禁止合入，先修verify_agent_output.py再回归）
"""
import re
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE = Path('D:/zcodesj')
BUGTEST = BASE / '.gate_logs' / '_bugtest'
VERIFIER = BASE / 'verify_agent_output.py'
PKG = BASE / '.gate_logs' / 'packages' / 'pkg_20260817_211229.json'

# 校验项类型常量（与verify_agent_output.py中violations['type']严格一致）
T_FAB_NUM = '编造数字(数据包中无此值)'
T_FAB_CODE = '编造数据(代码不在包内)'
T_FAB_SRC = '编造来源(库.表不在数据包sources)'
T_MISSING = '输出范式缺失段落'
T_ORDER = '输出范式段序错误'
T_LEAD = '结论先于论据'
T_COUNTER = '反向信号缺失(只有正向无反向)'
T_PRED = '预判未标注不确定性'

# ---------------- 用例矩阵 ----------------
# (编号, 报告路径, 预期verdict, 预期触发的校验项类型列表——须全部出现在实际违规类型中)
CASES = [
    ('R01', BUGTEST / 'bad_report.md', 'REJECTED',
     [T_MISSING, T_ORDER, T_LEAD, T_FAB_NUM, T_PRED],
     '范式缺失+结论先行+段序错误(2026-08-17 t22修复死代码后的回归锚点)'),
    ('R02', BUGTEST / 'fake_report.md', 'REJECTED',
     [T_FAB_NUM, T_PRED],
     '编造数字9.9 + 预判词"必涨"未标注'),
    ('R03', BUGTEST / 'good_report.md', 'PASS',
     [],
     '全部数字可溯源、六段齐全、无预判词 → 放行'),
    ('R04', BUGTEST / 'report_E1.md', 'REJECTED',
     [T_FAB_NUM, T_LEAD, T_COUNTER, T_PRED, T_FAB_SRC],
     '追高臆想: 前言下结论+编造量比/换手/封单+反向信号"无"+编造来源level2_smart_flow'),
    ('R05', BUGTEST / 'report_E2.md', 'REJECTED',
     [T_FAB_CODE, T_FAB_NUM, T_MISSING, T_PRED, T_FAB_SRC],
     '死水臆断: 包外代码002354+编造资金数据+缺规则核对/反向信号段+编造来源'),
    ('R06', BUGTEST / 'report_E3.md', 'REJECTED',
     [T_FAB_CODE, T_FAB_NUM, T_PRED, T_FAB_SRC],
     '标签误读: 包外代码000620+编造封单数据+预判"有望连板"未标+编造来源zt_pool_api'),
    ('R07', BUGTEST / 'report_compliant_E1.md', 'PASS',
     [],
     '对照组合规报告: 数字全可溯源+预判已标注 → 放行（防误伤金标准）'),
    ('R08', BUGTEST / 'adv_predgap.md', 'REJECTED',
     [T_PRED],
     '对抗A1: 未收录预判词"大概率向上，有机会走强"——t22暴露词库缺口，已补录，本用例锁定该修复'),
    ('R09', BUGTEST / 'adv_date_disguise.md', 'REJECTED',
     [T_FAB_NUM],
     '对抗A2: 包外数字伪装日期格式"2026.99"，白名单(年份/8位日期/ISO)不得放行'),
    ('R10', BUGTEST / 'adv_concl_pct.md', 'REJECTED',
     [T_FAB_NUM],
     '对抗A3: 结论段塞包外百分比"涨幅66.6%"，数字溯源须覆盖结论段'),
]

VIOLATION_LINE_RE = re.compile(r'^\s*\d+\.\s+\[(.+?)\]', re.M)


def run_case(cid, report_path, expected_verdict, expected_types, note):
    """跑单条用例: 调verify_agent_output.py CLI(--no-log)，比对verdict与违规类型。"""
    problems = []
    if not Path(report_path).exists():
        return False, ['报告文件不存在: %s' % report_path], []
    cmd = [sys.executable, str(VERIFIER),
           '--report', str(report_path), '--package', str(PKG),
           '--agent', 'm7_regression', '--task', 't22_regression', '--no-log']
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', cwd=str(BASE))
    out = proc.stdout or ''
    # verdict由退出码+输出双重判定: 0=PASS放行 1=REJECTED驳回 2=ERROR
    rc = proc.returncode
    if rc == 0 and 'PASS' in out:
        actual_verdict = 'PASS'
    elif rc == 1 and 'REJECTED' in out:
        actual_verdict = 'REJECTED'
    else:
        actual_verdict = 'ERROR(rc=%d)' % rc
    actual_types = VIOLATION_LINE_RE.findall(out)

    if actual_verdict != expected_verdict:
        problems.append('verdict不符: 预期=%s 实际=%s' % (expected_verdict, actual_verdict))
    if expected_verdict == 'PASS':
        if actual_types:
            problems.append('PASS用例出现违规: %s' % ' / '.join(actual_types))
    else:
        missing = [t for t in expected_types if t not in actual_types]
        if missing:
            problems.append('预期校验项未触发: %s（实际触发: %s）'
                            % (' / '.join(missing), ' / '.join(actual_types) or '无'))
    return (not problems), problems, actual_types


def main():
    print('=' * 78)
    print('m7校验器全量回归测试套件 — verify_agent_output.py 回归 (%d条用例)' % len(CASES))
    print('校验器: %s' % VERIFIER)
    print('数据包: %s' % PKG)
    print('=' * 78)
    if not VERIFIER.exists():
        print('⛔ 找不到校验器: %s' % VERIFIER)
        return 1
    if not PKG.exists():
        print('⛔ 找不到数据包: %s' % PKG)
        return 1

    failures = []
    for cid, report_path, verdict, types, note in CASES:
        ok, problems, actual_types = run_case(cid, report_path, verdict, types, note)
        mark = '✅' if ok else '❌'
        print('%s %s %-28s 预期=%-8s 触发=%s' % (
            mark, cid, Path(report_path).name, verdict,
            '无(放行)' if verdict == 'PASS' else '✔'.join(sorted(set(actual_types)))))
        print('     %s' % note)
        if not ok:
            failures.append((cid, Path(report_path).name, problems))

    print('-' * 78)
    total, passed = len(CASES), len(CASES) - len(failures)
    print('回归结果: %d/%d 通过' % (passed, total))
    if failures:
        print('失败用例详情:')
        for cid, name, problems in failures:
            print('  ❌ %s %s' % (cid, name))
            for p in problems:
                print('     - %s' % p)
        print('结论: 存在失败——禁止合入。先修 D:/zcodesj/verify_agent_output.py 再重跑本套件到全绿。')
        return 1
    print('结论: 全绿 ✅ —— verify_agent_output.py 本次改动回归通过，m7盖章。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
