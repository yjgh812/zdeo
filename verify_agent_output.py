#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_agent_output.py — 分析报告校验器（m7校验审核官核心武器）
================================================================================
屠龙反臆想体系 L2/L3/L5 落地。三段式流水线 步骤3【校验Agent】= m7：
分析Agent(m2/m4/m6)的报告，只能基于 m1(pkg_builder.py) 生成的标准数据包；
本工具逐条核对「结论↔论据」，不放行任何臆想。

五大校验项：
  ① 数据溯源   报告中每个数字必须存在于数据包 raw_data（±0.01 容差）；
               6位股票代码必须命中包内 code；否则=编造数字
  ② 输出范式   【原始数据】【规则核对】【正向信号】【反向信号】【结论】【置信度】
               六段齐全且顺序正确；结论不得先于论据（前言出现结论性表述即驳回）
  ③ 反向信号   【反向信号】段为空或"无/暂无"占位 → 只有正向无反向，驳回
  ④ 预判标注   含 会涨/会跌/将/预期/有望/即将 等预判词，
               但无【预判，存在不确定性】标记 → 驳回
  ⑤ 编造检测   报告引用的 库名.表名 不在数据包 sources 中 → 驳回

输出: {verdict: "PASS"/"REJECTED", violations: [{type, detail, 位置}], ...}
REJECTED 时自动按类型追加写入 D:/zcodesj/.agent-teams/violation_log.jsonl（L5闭环）

CLI:
  python verify_agent_output.py --report report.md --package pkg_xxx.json \
      [--agent m4] [--task t18] [--no-log]
退出码: 0=PASS放行 / 1=REJECTED驳回 / 2=参数或数据包错误

数字抽取白名单（避免误伤，非数据类数字不校验）:
  - pkg_日期_时间 / ISO日期 / 时钟 / 8位日期 / 1900-2099四位年份
  - R1-R18风控规则编号、"≥N条"置信度模板、行首序号
  - 标识符内部数字（如 Web3.0、fingerprint十六进制串）
  - 绝对值<=31的无小数整数（序号/计数/天数，编造高危数字通常带小数或>31）
  - 百分比双态匹配: 包值 x 同时登记 x/100 与 x*100（兼容 0.052 vs 5.2% 两种存储）
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

VIOLATION_LOG = 'D:/zcodesj/.agent-teams/violation_log.jsonl'

# L3 输出范式：六段及强制顺序
SECTION_ORDER = ['【原始数据】', '【规则核对】', '【正向信号】', '【反向信号】', '【结论】', '【置信度】']

# ④ 预判词（"将"采用"将+方向字"组合，避免"将军"等误伤；2026-08-17队长验收补全必涨/必跌/看好/看空等；
#    2026-08-17 t22回归套件对抗用例A1暴露缺口：大概率/有机会未收录致臆断放行，当场补录）
PREDICTION_RE = re.compile(
    r'(会涨|会跌|必涨|必跌|将涨|将跌|预期|预计|预测|看好|看空|看涨|看跌|有望|或将|将来|即将|马上|'
    r'大概率|有机会|走强|走弱|'
    r'涨停在望|跌停在望|可能[大暴][涨跌]|明天[涨跌]|后天[涨跌]|次日[将必]?[涨跌]|'
    r'将[会要]?[涨跌回升落突破新高低开收封熔])')
PREDICTION_TAG_RE = re.compile(r'预判[，,]?\s*存在不确定性|【预判')

# ② 结论先行：第一个段标记之前的前言里出现结论性表述
CONCLUSION_LEAD_RE = re.compile(r'(结论[是为：:]|综上|建议(立即)?(买入|卖出|加仓|清仓|追入)|(应|须|必)须?(立即)?(买入|卖出)|立即(买入|卖出)|目标(价|涨幅))')

# ⑤ 库名.表名 引用抽取（库名须含下划线或以db结尾，排除普通英文句点/文件名）
DB_TABLE_RE = re.compile(r'\b([A-Za-z][A-Za-z0-9_]*(?:\.db)?)\.([A-Za-z][A-Za-z0-9_]*)\b')
FILE_EXT_SKIP = {'json', 'py', 'md', 'txt', 'csv', 'log', 'html', 'exe', 'sql', 'db', 'jsonl', 'db3'}

# ① 数字抽取（断言用ASCII类: 中文"量比9.9"要抽取, 但"Web3.0/t18/R3"标识符内部数字不抽;
#    注意Python的\w含中文, 不能用\w做边界）
NUM_RE = re.compile(r'(?<![A-Za-z0-9_.])[+-]?\d+(?:\.\d+)?(?![A-Za-z0-9_.])')
# 白名单模式（mask为等长空格，保留后续offset）
MASK_PATTERNS = [
    re.compile(r'pkg_\d{8}_\d{6}'),                                  # package_id
    re.compile(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?'),    # ISO日期时间
    re.compile(r'\d{2}:\d{2}(?::\d{2})?'),                           # 时钟
    re.compile(r'(?<![\d.])\d{8}(?![\d.])'),                         # 8位日期 20260817
    re.compile(r'R\d+'),                                             # 风控规则编号 R1-R18
    re.compile(r'[≥≤>>=<]\s*\d+\s*条'),                              # 置信度模板
    re.compile(r'^\s*\d{1,3}[.、)）]\s', re.M),                     # 行首序号
]


def _mask(text: str) -> str:
    """把白名单模式替换为等长空白，保持数字offset不变。"""
    for pat in MASK_PATTERNS:
        text = pat.sub(lambda m: ' ' * (m.end() - m.start()), text)
    return text


def _section_at(markers, offset):
    """返回offset所处段名（用于violation位置描述）。"""
    cur = '前言'
    for name, idx in markers:
        if idx <= offset:
            cur = name
        else:
            break
    return cur


def _context(text, offset, width=12):
    return text[max(0, offset - width):min(len(text), offset + width)].replace('\n', ' ').strip()


def collect_pkg_values(raw_data):
    """递归收集数据包内全部数值与6位股票代码集合。"""
    nums, codes = set(), set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            f = float(v)
            nums.add(round(f, 6))
            nums.add(round(f / 100.0, 6))   # 0.052 存储形式 vs 报告写 5.2%
            nums.add(round(f * 100.0, 6))   # 8.13 存储形式 vs 报告写 0.0813
        elif isinstance(v, dict):
            for k, x in v.items():
                if re.fullmatch(r'\d{6}', str(k)):
                    codes.add(str(k))
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(raw_data)
    return nums, codes


# ---------------- ① 数据溯源 ----------------
def check_number_trace(report, pkg, nums, codes, markers):
    """报告中每个数字须存在于raw_data（±0.01）；6位代码须命中包内code。"""
    violations = []
    masked = _mask(report)
    seen_fab = set()
    for m in NUM_RE.finditer(masked):
        raw = m.group(0)
        try:
            val = float(raw)
        except ValueError:
            continue
        sec = _section_at(markers, m.start())
        ctx = _context(report, m.start())
        # 6位股票代码：命中包内code合法，未命中=编造代码
        if re.fullmatch(r'\d{6}', raw):
            if raw in codes:
                continue
            key = ('code', raw)
            if key not in seen_fab:
                seen_fab.add(key)
                violations.append({'type': '编造数据(代码不在包内)',
                                   'detail': '股票代码 %s 不在数据包 raw_data 的代码集合中' % raw,
                                   '位置': '%s | …%s… | offset=%d' % (sec, ctx, m.start())})
            continue
        # 四位年份（概念名如"2026中报预增"、年份上下文）
        if re.fullmatch(r'(19|20)\d{2}', raw):
            continue
        # 小整数（序号/计数/天数等修辞数字）
        if '.' not in raw and abs(val) <= 31:
            continue
        if any(abs(val - pv) <= 0.01 for pv in nums):
            continue
        key = ('num', raw)
        if key not in seen_fab:
            seen_fab.add(key)
            violations.append({'type': '编造数字(数据包中无此值)',
                               'detail': '数字 %s 在数据包 raw_data 中找不到（±0.01容差内无匹配）' % raw,
                               '位置': '%s | …%s… | offset=%d' % (sec, ctx, m.start())})
    return violations


# ---------------- ② 输出范式 ----------------
def check_paradigm(report):
    """六段齐全+顺序正确+结论不得先于论据。缺段时仍继续查已存在段的相对顺序。"""
    violations = []
    markers = [(name, report.find(name)) for name in SECTION_ORDER]
    present = [(n, i) for n, i in markers if i >= 0]
    missing = [name for name, idx in markers if idx < 0]
    if missing:
        violations.append({'type': '输出范式缺失段落',
                           'detail': '缺少段落: %s（L3范式六段必须齐全）' % ' '.join(missing),
                           '位置': '全文'})
    # 段序: 标记在文中的物理出现顺序须与其在L3范式中的次序一致（论据在前，结论最后）
    # t22回归修复: 原实现按范式序构造present导致ranks恒为升序、检查永不触发（死代码），
    # 现改为按文中物理位置排序后再比对，bad_report类"结论在前论据在后"的乱序才真正被拦截
    order_rank = {n: r for r, n in enumerate(SECTION_ORDER)}
    present_by_pos = sorted(present, key=lambda t: t[1])
    ranks = [order_rank[n] for n, _ in present_by_pos]
    if len(present) >= 2 and ranks != sorted(ranks):
        wrong = '→'.join(n.strip('【】') for n, _ in present_by_pos)
        violations.append({'type': '输出范式段序错误',
                           'detail': '段落出现顺序 %s 与范式要求 %s 不符（结论不得先于论据）' % (
                               wrong, '→'.join(n.strip('【】') for n in SECTION_ORDER)),
                           '位置': '全文结构'})
    # 结论先行：首个段标记之前的前言中出现结论性表述
    first = present[0][1] if present else len(report)
    head = report[:first].strip()
    if head and CONCLUSION_LEAD_RE.search(head):
        hit = CONCLUSION_LEAD_RE.search(head).group(0)
        violations.append({'type': '结论先于论据',
                           'detail': '前言即出现结论性表述「%s…」，论据段尚未出现（L3: 先论据后结论）' % head[:40],
                           '位置': '前言(offset=%d)' % head.find(hit)})
    return violations, markers


# ---------------- ③ 反向信号非空 ----------------
def check_counter_signal(report, markers):
    """只有正向无反向 → 驳回。"""
    violations = []
    idx = report.find('【反向信号】')
    if idx < 0:
        return violations  # 段缺失已由②计
    nxt = report.find('【', idx + 6)
    body = report[idx + 6: nxt if nxt > 0 else len(report)].strip()
    if not body or re.fullmatch(r'(无|暂无|略|没有|未见|未发现|无。|暂无。|—|-|N/?A)[\s。:：、,，]*', body):
        violations.append({'type': '反向信号缺失(只有正向无反向)',
                           'detail': '【反向信号】段为空或占位（%r）——任何标的必有反向证据，单向论证不予放行' % (body[:20] or ''),
                           '位置': '【反向信号】段(offset=%d)' % idx})
    return violations


# ---------------- ④ 预判标注 ----------------
def check_prediction_tag(report):
    """含预判词但无【预判，存在不确定性】标记 → 驳回。"""
    violations = []
    if PREDICTION_TAG_RE.search(report):
        return violations
    hits = PREDICTION_RE.findall(report)
    if hits:
        m = PREDICTION_RE.search(report)
        uniq = sorted(set(hits))
        violations.append({'type': '预判未标注不确定性',
                           'detail': '出现预判词 %s，但全文无「【预判，存在不确定性】」标记' % '/'.join(uniq[:5]),
                           '位置': '…%s… | offset=%d' % (_context(report, m.start()), m.start())})
    return violations


# ---------------- ⑤ 编造来源检测 ----------------
def check_sources(report, pkg, markers):
    """结论/全文引用的 库名.表名 必须在数据包 sources 中。"""
    violations = []
    legit = set()
    for src in pkg.get('sources', []):
        db = str(src.get('db', '')).lower().removesuffix('.db')
        table = str(src.get('table', '')).lower().split('(')[0]
        legit.add((db, table))
    seen = set()
    for m in DB_TABLE_RE.finditer(report):
        db_raw = m.group(1).lower()
        table = m.group(2).lower()
        if table in FILE_EXT_SKIP:
            continue
        db = db_raw.removesuffix('.db')
        if (db, table) in legit:
            continue
        # 库名启发式：无下划线且非db结尾的短标识符视为普通句点，不误报
        if '_' not in db and not db.endswith('db'):
            continue
        key = (db, table)
        if key in seen:
            continue
        seen.add(key)
        sec = _section_at(markers, m.start())
        violations.append({'type': '编造来源(库.表不在数据包sources)',
                           'detail': '引用 %s.%s 不在数据包 sources（合法: %s）' % (
                               m.group(1), m.group(2),
                               '; '.join('%s.%s' % (d, t) for d, t in sorted(legit)) or '无'),
                           '位置': '%s | …%s… | offset=%d' % (sec, _context(report, m.start()), m.start())})
    return violations


# ---------------- L5 违规日志 ----------------
def append_violation_log(agent, task, violations):
    """按类型合并写入violation_log.jsonl，count=该agent历史累计（L5处罚梯度依据）。"""
    p = Path(VIOLATION_LOG)
    counts = {}
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            try:
                rec = json.loads(line)
                a = rec.get('agent')
                counts[a] = counts.get(a, 0) + 1
            except Exception:
                pass
    base = counts.get(agent, 0)
    grouped = {}
    for v in violations:
        grouped.setdefault(v['type'], []).append(v)
    now = datetime.now().isoformat(timespec='seconds')
    written = 0
    with p.open('a', encoding='utf-8') as f:
        for i, (vtype, items) in enumerate(grouped.items()):
            detail = items[0]['detail']
            if len(items) > 1:
                detail += '（共%d处，如%s）' % (len(items), items[1].get('位置', ''))
            rec = {'ts': now, 'agent': agent, 'task': task, 'violation': vtype,
                   'detail': detail[:400], 'action': '驳回重算', 'count': base + i + 1}
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            written += 1
    return written


# ---------------- 核心入口 ----------------
def verify(report: str, package_path: str, agent: str = 'unknown', task: str = 'unknown',
           write_log: bool = True) -> dict:
    """校验分析报告 vs 标准数据包。返回 {verdict, violations, ...}。

    report:       报告全文（md纯文本）
    package_path: m1生成的标准数据包JSON路径
    agent/task:   被审agent与任务号（写违规日志溯源用）
    """
    pp = Path(package_path)
    if not pp.exists():
        return {'verdict': 'ERROR', 'violations': [{'type': '数据包不可用',
                                                    'detail': '找不到数据包: %s' % package_path, '位置': '-'}],
                'log_written': 0}
    try:
        pkg = json.loads(pp.read_text(encoding='utf-8'))
    except Exception as e:
        return {'verdict': 'ERROR', 'violations': [{'type': '数据包解析失败',
                                                    'detail': '%s: %s' % (package_path, e), '位置': '-'}],
                'log_written': 0}
    if 'raw_data' not in pkg or 'sources' not in pkg:
        return {'verdict': 'ERROR', 'violations': [{'type': '数据包不合规',
                                                    'detail': '缺少 raw_data/sources 字段，非pkg_builder标准包', '位置': '-'}],
                'log_written': 0}

    nums, codes = collect_pkg_values(pkg['raw_data'])
    paradigm_v, markers = check_paradigm(report)
    markers_valid = [(n, i) for n, i in markers if i >= 0]

    violations = []
    violations += check_number_trace(report, pkg, nums, codes, markers_valid)
    violations += paradigm_v
    violations += check_counter_signal(report, markers_valid)
    violations += check_prediction_tag(report)
    violations += check_sources(report, pkg, markers_valid)

    verdict = 'PASS' if not violations else 'REJECTED'
    log_written = 0
    if verdict == 'REJECTED' and write_log:
        log_written = append_violation_log(agent, task, violations)

    return {
        'verdict': verdict,
        'violations': violations,
        'package_id': pkg.get('package_id'),
        'fingerprint': pkg.get('fingerprint'),
        'package_values': len(nums),
        'violation_count': len(violations),
        'log_written': log_written,
    }


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    ap = argparse.ArgumentParser(description='分析报告校验器(m7校验官核心武器, 反臆想L2/L3/L5)')
    ap.add_argument('--report', required=True, help='报告文件路径(.md/.txt)')
    ap.add_argument('--package', required=True, help='m1标准数据包JSON路径(pkg_xxx.json)')
    ap.add_argument('--agent', default='unknown', help='被审agent编号(写违规日志), 如 m4')
    ap.add_argument('--task', default='unknown', help='任务号, 如 t18')
    ap.add_argument('--no-log', action='store_true', help='REJECTED也不写violation_log(仅测试用)')
    args = ap.parse_args()

    rp = Path(args.report)
    if not rp.exists():
        print('⛔ 报告文件不存在: %s' % args.report)
        return 2
    report = rp.read_text(encoding='utf-8', errors='ignore')

    result = verify(report, args.package, agent=args.agent, task=args.task,
                    write_log=not args.no_log)

    if result['verdict'] == 'ERROR':
        print('⛔ %s' % result['violations'][0]['detail'])
        return 2

    pkg_id = result.get('package_id')
    if result['verdict'] == 'PASS':
        print('✅ PASS 放行（数据包%s, 指纹%s, 包内可核值%d个）—— 逐条论据核对通过，m7盖章'
              % (pkg_id, result.get('fingerprint'), result.get('package_values', 0)))
        return 0

    print('⛔ REJECTED 驳回（数据包%s）—— %d处违规:'
          % (pkg_id, result['violation_count']))
    for i, v in enumerate(result['violations'], 1):
        print('  %d. [%s] %s' % (i, v['type'], v['detail']))
        print('     位置: %s' % v.get('位置', '-'))
    if result.get('log_written'):
        print('📝 已按类型追加%d条违规记录 → %s' % (result['log_written'], VIOLATION_LOG))
    print('处理: 驳回重算。请基于数据包逐条补论据后重新提交。')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
