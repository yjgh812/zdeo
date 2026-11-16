#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_anti_hallucination_pipeline.py — 每日反臆想三段式流水线 v1.0 (t27, m5)
================================================================================
背景: 反臆想三件套已齐(pkg_builder.py / verify_agent_output.py / executor m7关卡),
但每日选股流程未强制经过三段式——工具"可用"不等于"每日必用"。本脚本把三段式
封装为每日选股的强制环节, 一条命令跑完三段并落全链日志。

三段式(与反臆想体系 L1-L5 对齐):
  step1【数据读取】调 pkg_builder.build_package() 生成当日候选标准数据包。
          候选来源优先级: ①现有选股清单(bp_ep_monthly/momvol/ml, 复用
          daily_trade_plan.py 的读取函数) ②兜底: qfq库全市场最新交易日涨幅前30。
  step2【模板生成】六段范式报告骨架: 数据包数字机械填充(逐项可溯源), 定性部分
          显式标注[待分析Agent填写], 由 m2/m4/m6 补全。
          step2纪律与pkg_builder一致: 仅原始值直取/机械布尔/计数, 零定性零加工。
  step3【校验】调 verify_agent_output.verify() 逐条核对模板数字↔数据包
          (默认等价 CLI --no-log 演示模式; --with-log 开启驳回写违规日志L5闭环)。

日志: D:/zcodesj/.gate_logs/daily_pipeline_YYYYMMDD/
        pkg_<时间戳>.json            step1 数据包
        report_template_<时间戳>.md  step2 报告模板
        verify_result_<时间戳>.json  step3 校验结果
        pipeline_summary.json        最新一次三段汇总
        run_history.jsonl            每次运行追加(审计留痕)

用法:
    python daily_anti_hallucination_pipeline.py --run                    # 三段完整跑
    python daily_anti_hallucination_pipeline.py --run --max-codes 30     # 数据包覆盖前30只
    python daily_anti_hallucination_pipeline.py --run --candidates market  # 强制全市场涨幅前30
    python daily_anti_hallucination_pipeline.py --run --with-log         # step3驳回写违规日志

挂载建议(t27交付, 不新增cron, 由队长决定):
    现有 09:20 daily_trade_plan.py 生成作战清单后、09:30 开盘前追加执行本流水线
    (如 09:25), 使"数据包→模板→校验"成为每日选股固定前置; 亦可挂在其选股清单
    生成 cron(每月1日 09:00/09:05)之后。本脚本自身不注册任何 cron。

退出码: 0=三段全通 / 1=任一段失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

ZCODESJ = 'D:/zcodesj'
sys.path.insert(0, ZCODESJ)

GATE_LOG_ROOT = os.path.join(ZCODESJ, '.gate_logs')
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'

# step1 指标集: pkg_builder v2.0 全量快照类(v2.0资金/盘口/涨停/概念全覆盖)
INDICATORS = [
    'close', 'change_pct', 'volume', 'amount', 'turnover_rate', 'volume_ratio',
    'buy_sell_ratio', 'inner_volume', 'outer_volume', 'daily_limit', 'is_limit_up',
    'concepts_count', 'name', 'concept',
]

PENDING_TAG = '[待分析Agent填写]'


def _today() -> str:
    return datetime.now().strftime('%Y%m%d')


def _hms() -> str:
    return datetime.now().strftime('%H%M%S')


# ============================================================
# step0: 候选收集(只决定"看谁", 不做任何强弱判断)
# ============================================================

def _clean_code(raw) -> str | None:
    """提取纯6位代码(仅格式清洗, 非数据加工)。"""
    m = re.search(r'\d{6}', str(raw or ''))
    return m.group(0) if m else None


def candidates_from_lists(date_str: str) -> list[dict]:
    """现有选股清单候选: 复用 daily_trade_plan.py 的三个读取器, 去重保序。"""
    picks: list[dict] = []
    try:
        import daily_trade_plan as dtp
        for reader in (dtp.read_bp_ep_top20, dtp.read_momvol_top20, dtp.read_ml_top20):
            try:
                picks += (reader(date_str) if reader is dtp.read_bp_ep_top20 else reader()) or []
            except Exception:
                continue
    except Exception:
        return []
    seen, out = set(), []
    for p in picks:
        c = _clean_code(p.get('code'))
        if c and c not in seen:
            seen.add(c)
            out.append({'code': c, 'source': str(p.get('source') or 'stock_lists')})
    return out


def candidates_from_market(top: int = 30) -> list[dict]:
    """兜底: qfq库全市场最新交易日按 change_pct 排序前30(纯排序取数, 零筛选逻辑)。"""
    try:
        con = sqlite3.connect('file:' + QFQ_DB.replace('\\', '/') + '?mode=ro', uri=True)
        try:
            rows = con.execute(
                'SELECT code FROM stock_daily_meta_qfq '
                'WHERE date=(SELECT MAX(date) FROM stock_daily_meta_qfq) '
                'AND change_pct IS NOT NULL '
                'ORDER BY change_pct DESC LIMIT ?', (top,)).fetchall()
        finally:
            con.close()
    except Exception:
        return []
    return [{'code': r[0], 'source': 'market_top30'} for r in rows if _clean_code(r[0])]


def collect_candidates(mode: str, date_str: str):
    """mode: auto=清单优先/空则市场; lists=仅清单; market=仅全市场前30。"""
    if mode in ('auto', 'lists'):
        lists = candidates_from_lists(date_str)
        if lists:
            return lists, 'stock_lists(bp_ep/momvol/ml现有选股清单去重)'
        if mode == 'lists':
            return [], 'stock_lists(清单为空)'
    mkt = candidates_from_market(30)
    return mkt, 'market_top30(qfq全市场最新交易日涨幅前30)'


# ============================================================
# step1: 数据包(pkg_builder, 只读零加工)
# ============================================================

def step1_build_package(cands: list[dict], max_codes: int, sources: list[str], logdir: str):
    import pkg_builder
    codes = [c['code'] for c in cands][:max_codes]
    pkg = pkg_builder.build_package(codes, INDICATORS, sources or None)
    pkg_path = pkg_builder.save_package(pkg, logdir)
    return pkg, pkg_path, codes


# ============================================================
# step2: 六段范式报告模板(数字机械填充, 定性待填)
# ============================================================

def _f(v, nd=2) -> str:
    """数值渲染: null如实标注; 原值round(容差内可溯源), 禁单位换算。"""
    if v is None or isinstance(v, bool):
        return 'null'
    try:
        return str(round(float(v), nd))
    except Exception:
        return 'null'


def _b(v) -> str:
    """布尔渲染(true/false/null, 无数字)。"""
    if v is None:
        return 'null'
    return 'true' if v else 'false'


def _mech_observations(row: dict) -> tuple[list[str], list[str]]:
    """机械观察(仅 与0比较 的布尔判断, 零定性): 返回(正向, 反向)。"""
    pos, neg = [], []

    def _num(x):
        return x if (isinstance(x, (int, float)) and not isinstance(x, bool)) else None

    cp = _num(row.get('change_pct'))
    if cp is None:
        neg.append('当日涨跌幅快照缺失（null，如实标注待补）')
    elif cp > 0:
        pos.append('当日涨跌幅为正（+%s%%）' % _f(cp))
    elif cp < 0:
        neg.append('当日涨跌幅为负（%s%%）' % _f(cp))
    else:
        neg.append('当日涨跌幅为零（%s%%）' % _f(cp))

    ilu = row.get('is_limit_up')
    if ilu is True:
        pos.append('现价触及涨停价（is_limit_up=true，机械比较）')
    elif ilu is False:
        neg.append('现价未触涨停价（is_limit_up=false，机械比较）')
    else:
        neg.append('触涨停状态无法机械判定（daily_limit或price缺失null）')

    bs = _num(row.get('buy_sell_ratio'))
    if bs is None:
        neg.append('委比快照缺失（null）')
    elif bs > 0:
        pos.append('委比为正（%s）' % _f(bs))
    elif bs < 0:
        neg.append('委比为负（%s）' % _f(bs))

    tr = row.get('turnover_rate')
    if tr is None:
        neg.append('换手率快照缺失（null）')

    if not neg:  # 反向段兜底: 保证任何标的必有反向观察(m7硬规则)
        neg.append('本轮快照机械比较无负向项，独立反向证据由分析Agent基于数据包补充')
    return pos, neg


def _render_sources_line(pkg: dict) -> str:
    """动态渲染数据包实际命中的来源; calc源不带 库.表 格式避免误触发来源校验。"""
    parts = []
    for s in pkg.get('sources', []):
        db = str(s.get('db', ''))
        if db.startswith('calc'):
            parts.append('calc(静态规则查表/机械布尔/计数)')
        else:
            parts.append('%s.%s' % (db, s.get('table', '')))
    return ' | '.join(parts) if parts else '（数据包sources为空）'


def step2_render_template(pkg: dict, cand_meta: dict, run_meta: dict) -> tuple[str, int]:
    """渲染六段范式模板。返回(报告全文, 待填写标记数)。"""
    raw = pkg.get('raw_data', {})
    date_str = run_meta['date']
    iso = '%s-%s-%s' % (date_str[:4], date_str[4:6], date_str[6:])
    L = []
    pending = 0

    def pend(line: str):
        nonlocal pending
        pending += 1
        L.append('%s %s' % (PENDING_TAG, line))

    L.append('# 屠龙每日反臆想三段式分析报告（模板）· %s' % iso)
    L.append('')
    L.append('- 数据包: %s（指纹 %s）' % (pkg.get('package_id'), pkg.get('fingerprint')))
    L.append('- 候选来源: %s（本次入包 %d 只）' % (cand_meta['desc'], len(raw)))
    L.append('- 生成器: daily_anti_hallucination_pipeline.py step2（数字由数据包机械填充，零定性）')
    L.append('- 纪律: 表格数字全部来自数据包 raw_data，逐项可溯源；%s 标注处由分析Agent(m2/m4/m6)'
             '补定性内容，m7校验通过前本报告不作为交易依据。' % PENDING_TAG)
    L.append('')

    # ---- ①【原始数据】 ----
    L.append('## 【原始数据】')
    L.append('')
    L.append('来源: %s' % _render_sources_line(pkg))
    L.append('')
    L.append('| 代码 | 名称 | 收盘 | 涨跌幅% | 换手率% | 成交额 | 委比 | 内盘 | 外盘 | 涨停价 | 触涨停 | 概念数 |')
    L.append('|------|------|------|--------|--------|--------|------|------|------|--------|--------|--------|')
    for code, row in raw.items():
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            code,
            row.get('name') if row.get('name') else 'null',
            _f(row.get('close')), _f(row.get('change_pct')), _f(row.get('turnover_rate')),
            _f(row.get('amount')), _f(row.get('buy_sell_ratio')),
            _f(row.get('inner_volume'), 0), _f(row.get('outer_volume'), 0),
            _f(row.get('daily_limit')), _b(row.get('is_limit_up')),
            (str(int(v)) if (v := row.get('concepts_count')) is not None else 'null'),
        ))
    L.append('')
    pend('上述字段之外的补充论据，须先由m1扩充数据包再引用，禁止凭记忆或第三方来源报数。')
    L.append('')

    # ---- ②【规则核对】 ----
    L.append('## 【规则核对】')
    L.append('')
    L.append('| 代码 | 触涨停(is_limit_up) | 涨跌方向(change_pct与零比较) | 委比方向(buy_sell_ratio与零比较) |')
    L.append('|------|--------------------|-----------------------------|--------------------------------|')
    for code, row in raw.items():
        cp = row.get('change_pct')
        if isinstance(cp, (int, float)) and not isinstance(cp, bool):
            d = '涨(+%s%%)' % _f(cp) if cp > 0 else ('跌(%s%%)' % _f(cp) if cp < 0 else '平(%s%%)' % _f(cp))
        else:
            d = 'null'
        bs = row.get('buy_sell_ratio')
        if isinstance(bs, (int, float)) and not isinstance(bs, bool):
            b = '正(+%s)' % _f(bs) if bs > 0 else ('负(%s)' % _f(bs) if bs < 0 else '零(%s)' % _f(bs))
        else:
            b = 'null'
        L.append('| %s | %s | %s | %s |' % (code, _b(row.get('is_limit_up')), d, b))
    L.append('')
    pend('对照R1-R18闸门与交易准则逐条核对，核对所引数字须在【原始数据】表中。')
    L.append('')

    # ---- ③【正向信号】 ----
    L.append('## 【正向信号】')
    L.append('')
    L.append('（机械观察，仅数据包字段与零的比较结果，非定性论断：）')
    has_pos = False
    for code, row in raw.items():
        pos, _ = _mech_observations(row)
        if pos:
            has_pos = True
            L.append('- %s（%s）: %s' % (code, row.get('name') or 'null', '；'.join(pos)))
    if not has_pos:
        L.append('- （本轮机械正向观察为空）')
    pend('基于数据包补充该候选的主线地位、催化与逻辑论据（论据数字须在数据包内）。')
    L.append('')

    # ---- ④【反向信号】 ----
    L.append('## 【反向信号】')
    L.append('')
    L.append('（机械观察，任何标的必有反向证据——单向论证m7不予放行：）')
    for code, row in raw.items():
        _, neg = _mech_observations(row)
        L.append('- %s（%s）: %s' % (code, row.get('name') or 'null', '；'.join(neg)))
    pend('补充每只候选的独立反向证据；本段留空或以"无/暂无"占位将被m7直接驳回。')
    L.append('')

    # ---- ⑤【结论】 ----
    L.append('## 【结论】')
    L.append('')
    pend('基于【原始数据】【规则核对】【正向信号】【反向信号】四段论据给出操作性结论；'
         '模板阶段零结论，论据不足不得下结论。')
    L.append('')

    # ---- ⑥【置信度】 ----
    L.append('## 【置信度】')
    L.append('')
    pend('按团队置信度模板给出评级与依据条数；含预判表述必须标注【预判，存在不确定性】。')
    L.append('')

    return '\n'.join(L), pending


# ============================================================
# step3: 校验(verify_agent_output, 等价CLI --no-log)
# ============================================================

def step3_verify(report_path: str, package_path: str, with_log: bool, task: str) -> dict:
    import verify_agent_output as vao
    report = open(report_path, 'r', encoding='utf-8').read()
    result = vao.verify(report, package_path,
                        agent='m5_daily_pipeline', task=task, write_log=with_log)
    return result


# ============================================================
# 主流程
# ============================================================

def run_pipeline(args) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    date_str = args.date or _today()
    logdir = os.path.join(GATE_LOG_ROOT, 'daily_pipeline_%s' % date_str)
    os.makedirs(logdir, exist_ok=True)
    hms = _hms()

    print('=== 屠龙每日反臆想三段式流水线 · %s ===' % date_str)
    print('日志目录: %s' % logdir)

    # step0 候选
    cands, cand_desc = collect_candidates(args.candidates, date_str)
    if not cands:
        print('⛔ step0 无候选（清单与市场均空），流水线中止')
        return 1
    print('step0 候选: %d 只（来源: %s），入包上限 %d' % (len(cands), cand_desc, args.max_codes))

    # step1 数据包
    print('--- step1 数据包生成（pkg_builder 纪律: 只读零加工）---')
    try:
        pkg, pkg_path, codes = step1_build_package(cands, args.max_codes, args.sources, logdir)
    except Exception as e:
        print('⛔ step1 失败: %s' % e)
        return 1
    n_valid = sum(1 for r in pkg.get('raw_data', {}).values() if r)
    print('✅ step1 数据包: %s | 指纹 %s | 入包 %d 只（非空行 %d）| 落盘 %s'
          % (pkg.get('package_id'), pkg.get('fingerprint'), len(codes), n_valid, pkg_path))
    if not pkg.get('raw_data'):
        print('⛔ step1 数据包 raw_data 为空，流水线中止')
        return 1

    # step2 模板
    print('--- step2 六段范式模板渲染（数字机械填充, 定性待填）---')
    report, pending = step2_render_template(
        pkg, {'desc': cand_desc}, {'date': date_str})
    report_path = os.path.join(logdir, 'report_template_%s.md' % hms)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print('✅ step2 模板: 六段范式齐全 | %s 标记 %d 处 | 落盘 %s'
          % (PENDING_TAG, pending, report_path))

    # step3 校验（默认 no-log 演示模式）
    print('--- step3 m7校验（verify_agent_output, %s）---'
          % ('写违规日志' if args.with_log else '--no-log 演示模式'))
    result = step3_verify(report_path, pkg_path, args.with_log, args.task)
    verify_path = os.path.join(logdir, 'verify_result_%s.json' % hms)
    with open(verify_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    verdict = result.get('verdict')
    if verdict == 'PASS':
        print('✅ step3 校验: PASS 放行（数据包%s, 指纹%s, 包内可核值%d个）'
              % (result.get('package_id'), result.get('fingerprint'),
                 result.get('package_values', 0)))
    else:
        print('⛔ step3 校验: %s（%d处违规）' % (verdict, result.get('violation_count', 0)))
        for i, v in enumerate(result.get('violations', [])[:5], 1):
            print('   %d. [%s] %s' % (i, v.get('type'), v.get('detail')))
    cli_eq = ('python D:/zcodesj/verify_agent_output.py --report %s --package %s '
              '--agent m5_daily_pipeline --task %s%s'
              % (report_path, pkg_path, args.task, '' if args.with_log else ' --no-log'))
    print('    CLI等价: %s' % cli_eq)

    all_pass = bool(pkg.get('raw_data')) and verdict == 'PASS'
    summary = {
        'run_ts': datetime.now().isoformat(timespec='seconds'),
        'date': date_str,
        'pipeline': 'daily_anti_hallucination_pipeline v1.0 (t27, m5)',
        'step0_candidates': {'count': len(cands), 'source_desc': cand_desc,
                             'packaged': len(codes)},
        'step1_package': {'package_id': pkg.get('package_id'),
                          'fingerprint': pkg.get('fingerprint'),
                          'codes': codes, 'path': pkg_path,
                          'sources': pkg.get('sources')},
        'step2_template': {'path': report_path, 'paradigm_sections': 6,
                           'pending_fill_markers': pending},
        'step3_verify': {'verdict': verdict,
                         'violation_count': result.get('violation_count'),
                         'package_values': result.get('package_values'),
                         'path': verify_path, 'cli_equivalent': cli_eq,
                         'log_mode': 'with-log' if args.with_log else 'no-log'},
        'all_pass': all_pass,
    }
    with open(os.path.join(logdir, 'pipeline_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(logdir, 'run_history.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'run_ts': summary['run_ts'], 'date': date_str,
                            'package_id': pkg.get('package_id'),
                            'verdict': verdict, 'all_pass': all_pass,
                            'codes': len(codes)}, ensure_ascii=False) + '\n')

    print('---')
    print('%s 三段式流水线: %s（汇总: %s）'
          % ('✅' if all_pass else '⛔', '三段全通' if all_pass else '未全通',
             os.path.join(logdir, 'pipeline_summary.json')))
    if all_pass:
        print('后续: 分析Agent在模板 %d 处%s标记处补定性内容 -> m7再次校验 -> 过闸门方可交易。'
              '建议挂载: 09:20 daily_trade_plan 之后(如09:25), 由队长决定。'
              % (pending, PENDING_TAG))
    return 0 if all_pass else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description='屠龙每日反臆想三段式流水线 v1.0 (t27, m5): '
                    'step1 pkg_builder数据包 -> step2 六段范式模板 -> step3 verify校验')
    ap.add_argument('--run', action='store_true', help='执行完整三段流水线')
    ap.add_argument('--date', default=None, help='日期 YYYYMMDD（默认今日）')
    ap.add_argument('--max-codes', type=int, default=10,
                    help='数据包入包候选上限（默认10; 候选池为清单去重或全市场前30）')
    ap.add_argument('--candidates', choices=['auto', 'lists', 'market'], default='auto',
                    help='候选来源: auto=清单优先空则市场 / lists=仅清单 / market=仅全市场涨幅前30')
    ap.add_argument('--sources', default=None,
                    help='pkg_builder可用源, 逗号分隔(默认 qfq,concept_map,westock_quote)')
    ap.add_argument('--with-log', action='store_true',
                    help='step3驳回时写violation_log(默认no-log演示模式)')
    ap.add_argument('--task', default='t27', help='任务号(校验日志溯源用)')
    args = ap.parse_args()
    if args.sources:
        args.sources = [s.strip() for s in args.sources.split(',') if s.strip()]
    if not args.run:
        ap.print_help()
        return 2
    return run_pipeline(args)


if __name__ == '__main__':
    raise SystemExit(main())
