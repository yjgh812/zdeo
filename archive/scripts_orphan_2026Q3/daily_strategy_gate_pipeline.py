#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_strategy_gate_pipeline.py — 每日9:10盘前选股三段式闸门流水线 v1.0 (t36/团队t35, m5)
================================================================================
使命: 把盘前cron(automation-f184f68b)第七步的选股筛选从"逐只 pre_trade_check_v2.py"
升级为 pkg_builder + strategy_gate + verify_agent_output 三段式流水线, 与实盘唯一入口
guarded_trade_executor.py(t9签名/t21 m7校验)口径统一。

链路(改造方案详见 D:/zcodesj/reports/cron_upgrade_plan_20260818.md):
  step0 候选: --codes 显式 / --candidates-file / --auto(复用t27清单读取, 空则全市场前30)
  step1 数据供给: gate_data_fetcher.py 拉闸门字段(fetcher输出{code:dict}系strategy_gate
          原生兼容格式, t31) + pkg_builder.py 生成m1标准数据包(m7核对基准)
  step2 闸门判决: strategy_gate.batch_process(G0-G18硬底线+六维评分+判决)
          gate_state=读当日真实state批内就地更新不落盘(扫描零污染, G15不被误抬)
  step3 报告校验: 六段范式盘前报告(数字仅取数据包, 闸门评分不入报告防m7误判编造)
          → verify_agent_output.verify()
  产出  PASS逐股executor签名JSON(补price/gates_passed/gate_count/rejected_gates四键,
          strategy_gate裸result所缺) + gate_result.json + 校验留痕

签名时效纪律: executor校验签名30分钟有效期。盘前(9:10)签名仅作审计留痕, 盘中下单前
  须用 strategy_gate --single 或本脚本 --codes <单码> 现铸新签名(t9既定流程)。

用法:
  python daily_strategy_gate_pipeline.py --codes 600272,002354 --source mainline_radar \
      --mainlines "半导体,国企改革"
  python daily_strategy_gate_pipeline.py --auto
  python daily_strategy_gate_pipeline.py --candidates-file cand.json
退出码: 0=流水线完整跑完(含候选被REJECT, 属正常判决) / 1=基础设施失败 / 2=参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

ZCODESJ = 'D:/zcodesj'
sys.path.insert(0, ZCODESJ)

GATE_LOG_ROOT = os.path.join(ZCODESJ, '.gate_logs')
FETCHER = os.path.join(ZCODESJ, 'gate_data_fetcher.py')
SIG_DIR = os.path.join(GATE_LOG_ROOT, 'signatures')

# 与t27流水线一致的数据包指标集(pkg_builder v2.0全量快照类)
INDICATORS = [
    'close', 'change_pct', 'volume', 'amount', 'turnover_rate', 'volume_ratio',
    'buy_sell_ratio', 'inner_volume', 'outer_volume', 'daily_limit', 'is_limit_up',
    'concepts_count', 'name', 'concept',
]


def _today() -> str:
    return datetime.now().strftime('%Y%m%d')


def _hms() -> str:
    return datetime.now().strftime('%H%M%S')


# ============================================================
# step0: 候选收集
# ============================================================

def collect_codes(args) -> tuple[list[str], str, dict | None]:
    """返回(纯6位代码列表, 来源描述, 文件自带候选dict或None)。

    --candidates-file 且条目为含字段的dict时, 直接作为闸门候选(跳过fetcher,
    供回放/合成候选/上游已取数场景); 仅代码列表时仍走fetcher补字段。
    """
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',') if c.strip()]
        return codes, '--codes显式指定', None
    if args.candidates_file:
        with open(args.candidates_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        file_map = None
        if isinstance(data, dict):
            vals = list(data.values())
            if vals and isinstance(vals[0], dict):
                file_map = {str(v.get('code', '')).strip(): v for v in vals}
                codes = [c for c in file_map.keys()]
            else:
                codes = [str(k).strip() for k in data.keys()]
        else:
            if data and isinstance(data[0], dict):
                file_map = {str(x.get('code', '')).strip(): x for x in data}
                codes = [c for c in file_map.keys()]
            else:
                codes = [str(x).strip() for x in data]
        codes = [c for c in codes if len(c) == 6 and c.isdigit()]
        if file_map:
            file_map = {c: file_map[c] for c in codes if c in file_map}
        return codes, 'candidates-file:%s' % args.candidates_file, file_map
    # --auto: 复用t27流水线的清单读取(空则全市场涨幅前30)
    import daily_anti_hallucination_pipeline as dahp
    cands, desc = dahp.collect_candidates('auto', args.date or _today())
    return [c['code'] for c in cands], desc, None


# ============================================================
# step1a: fetcher供闸门字段
# ============================================================

def fetch_gate_candidates(codes: list[str], args) -> dict:
    """subprocess调gate_data_fetcher, 返回{code: entry}。失败返回{}(闸门fail-closed兜底)。"""
    if not codes:
        return {}
    cmd = [sys.executable, FETCHER, '--codes', ','.join(codes),
           '--source', args.source]
    if args.mainlines:
        cmd += ['--mainlines', args.mainlines]
    if args.holdings:
        cmd += ['--holdings', args.holdings]
    if args.today_ordered:
        cmd += ['--today-ordered', args.today_ordered]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            print('[warn] fetcher exit=%d: %s' % (r.returncode, (r.stderr or '')[:200]),
                  file=sys.stderr)
            return {}
        return json.loads(r.stdout)
    except Exception as e:
        print('[warn] fetcher异常: %s' % str(e)[:200], file=sys.stderr)
        return {}


def _fallback_candidates(codes: list[str], pkg: dict, source: str) -> dict:
    """fetcher失败兜底: 用数据包快照构造最小候选(缺pct_20d等→G4 fail-closed拒, 显式降级)。"""
    out = {}
    for code in codes:
        row = (pkg.get('raw_data') or {}).get(code) or {}
        out[code] = {
            'code': code,
            'name': row.get('name') or '',
            'price': row.get('close'),
            'change_pct': row.get('change_pct'),
            'volume_ratio': row.get('volume_ratio'),
            'daily_limit': row.get('daily_limit'),
            'concept': ','.join(row.get('concept') or []) if isinstance(row.get('concept'), list)
                       else (row.get('concept') or ''),
            'source': source,
        }
    return out


# ============================================================
# step2: strategy_gate批量判决
# ============================================================

def run_gate(cands_map: dict, args) -> dict:
    """读当日gate_state批内就地更新但不落盘(扫描零污染实盘计数)。"""
    import strategy_gate as sg
    candidates = list(cands_map.values())
    holdings = [h.strip() for h in (args.holdings or '').split(',') if h.strip()] or None
    ordered = [h.strip() for h in (args.today_ordered or '').split(',') if h.strip()] or None
    mainlines = [m.strip() for m in (args.mainlines or '').split(',') if m.strip()] or None
    state = sg.load_gate_state(args.date or _today())  # 只读引用, own_state=False不落盘
    return sg.batch_process(candidates, holdings=holdings, today_ordered=ordered,
                            mainlines=mainlines, gate_state=state)


# ============================================================
# 签名落盘(executor兼容四键补齐)
# ============================================================

def write_signatures(batch: dict, cands_map: dict, sig_dir: str, date_str: str) -> list[str]:
    """为每个PASS候选写executor兼容签名。返回落盘路径列表。

    strategy_gate per-candidate result缺 price/gates_passed/gate_count/rejected_gates,
    executor validate_signature/_write_exec_log 需要, 在此补齐(映射非加工)。
    """
    os.makedirs(sig_dir, exist_ok=True)
    paths = []
    ts = datetime.now().isoformat(timespec='seconds')
    for r in batch.get('results', []):
        if r.get('verdict') != 'PASS':
            continue
        code = r.get('code') or ''
        cand = cands_map.get(code) or {}
        gates = r.get('hard_gates') or {}
        sig = {
            'timestamp': ts,
            'signature_kind': 'daily_strategy_gate_pipeline',
            'result': {
                'code': code,
                'name': r.get('name') or cand.get('name') or '',
                'verdict': r.get('verdict'),
                'score': r.get('score'),
                'price': cand.get('price'),
                'source': cand.get('source') or '',
                'gates_passed': sum(1 for g in gates.values() if g.get('pass')),
                'gate_count': len(gates),
                'rejected_gates': list(r.get('hard_fails') or []),
            },
        }
        if r.get('amount_adjusted') is not None:  # G12降半仓标记, executor强制落地
            sig['result']['amount_adjusted'] = r['amount_adjusted']
            sig['result']['amount_note'] = r.get('amount_note')
        path = os.path.join(sig_dir, 'sig_%s_%s.json' % (code, date_str))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sig, f, ensure_ascii=False, indent=2)
        paths.append(path)
    return paths


# ============================================================
# step3: 六段范式报告(复用t27渲染器+闸门判决表) + m7校验
# ============================================================

def render_report(pkg: dict, batch: dict, cand_desc: str, date_str: str) -> str:
    """t27六段模板 + 在【规则核对】段末追加闸门判决表(仅verdict标签/闸门编号,
    评分数字不入报告——不在数据包内的数字会被m7判编造)。"""
    import daily_anti_hallucination_pipeline as dahp
    report, _pending = dahp.step2_render_template(
        pkg, {'desc': cand_desc}, {'date': date_str})

    lines = ['', '### 三段式闸门判决(strategy_gate G0-G18硬底线+六维评分, 评分明细见gate_result.json)', '',
             '| 代码 | 名称 | 闸门判决 | 硬底线未过 |', '|------|------|----------|------------|']
    names = {c: ((pkg.get('raw_data') or {}).get(c) or {}).get('name') or 'null'
             for c in (pkg.get('raw_data') or {})}
    for r in batch.get('results', []):
        code = r.get('code') or ''
        fails = ','.join(r.get('hard_fails') or []) or '无'
        lines.append('| %s | %s | %s | %s |' % (code, names.get(code, 'null'),
                                                r.get('verdict'), fails))
    v = batch.get('verdicts') or {}
    lines.append('')
    lines.append('判决统计: PASS %d 只 / PASS_WITH_WARN %d 只 / REJECT %d 只(共 %d 只候选)。'
                 % (v.get('PASS', 0), v.get('PASS_WITH_WARN', 0), v.get('REJECT', 0),
                    batch.get('total', 0)))
    lines.append('')
    block = '\n'.join(lines)

    marker = '## 【正向信号】'
    idx = report.find(marker)
    if idx > 0:
        return report[:idx] + block + '\n' + report[idx:]
    return report + '\n' + block


# ============================================================
# 主流程
# ============================================================

def run(args) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    date_str = args.date or _today()
    logdir = os.path.join(GATE_LOG_ROOT, 'daily_gate_pipeline_%s' % date_str)
    os.makedirs(logdir, exist_ok=True)
    hms = _hms()

    print('=== 每日9:10盘前三段式闸门流水线 v1.0 (t36, m5) · %s ===' % date_str)
    print('日志目录: %s' % logdir)

    # step0
    codes, cand_desc, file_map = collect_codes(args)
    codes = codes[:args.max_codes]
    if not codes:
        print('⛔ step0 无候选, 流水线中止')
        return 1
    print('step0 候选: %d 只(来源: %s)' % (len(codes), cand_desc))

    # step1a fetcher(--candidates-file自带字段dict时跳过fetcher, 直接用文件候选)
    if file_map:
        cands_map = {c: file_map[c] for c in codes if c in file_map}
        for c in codes:
            cands_map.setdefault(c, {'code': c, 'source': args.source})
        fetcher_ok = True
        print('--- step1a 候选字段来自candidates-file(跳过fetcher) ---')
    else:
        print('--- step1a gate_data_fetcher 拉闸门字段 ---')
        cands_map = fetch_gate_candidates(codes, args)
        fetcher_ok = bool(cands_map)
    fetcher_path = os.path.join(logdir, 'candidates_fetcher_%s.json' % hms)
    with open(fetcher_path, 'w', encoding='utf-8') as f:
        json.dump(cands_map, f, ensure_ascii=False, indent=2, default=str)
    print('%s fetcher: %d/%d 只取到闸门字段 | %s'
          % ('✅' if fetcher_ok else '⚠️ 降级', len(cands_map), len(codes), fetcher_path))

    # step1b 数据包
    print('--- step1b pkg_builder 标准数据包 ---')
    import pkg_builder
    pkg = pkg_builder.build_package(codes, INDICATORS)
    pkg_path = pkg_builder.save_package(pkg, logdir)
    print('✅ 数据包: %s | 指纹 %s | %s' % (pkg.get('package_id'), pkg.get('fingerprint'), pkg_path))
    if not pkg.get('raw_data'):
        print('⛔ 数据包为空, 流水线中止')
        return 1

    # fetcher失败→数据包快照兜底构造候选(G4等缺字段fail-closed拒, 显式降级)
    if not fetcher_ok:
        cands_map = _fallback_candidates(codes, pkg, args.source)
        print('⚠️ fetcher全失败, 用数据包快照兜底候选(pct_20d/hot_rank等缺失→相关闸门fail-closed拒)')

    # step2 闸门
    print('--- step2 strategy_gate 批量判决(G0-G18+评分) ---')
    batch = run_gate(cands_map, args)
    gate_path = os.path.join(logdir, 'gate_result_%s.json' % hms)
    with open(gate_path, 'w', encoding='utf-8') as f:
        json.dump(batch, f, ensure_ascii=False, indent=2, default=str)
    v = batch.get('verdicts') or {}
    print('✅ 闸门: PASS %d / WARN %d / REJECT %d | 平均分 %s | %s'
          % (v.get('PASS', 0), v.get('PASS_WITH_WARN', 0), v.get('REJECT', 0),
             batch.get('avg_score'), gate_path))

    # 签名落盘
    sig_paths = write_signatures(batch, cands_map, SIG_DIR, date_str)
    for p in sig_paths:
        print('   签名: %s' % p)

    # step3 报告+m7
    print('--- step3 六段报告渲染 + m7校验(%s) ---' % ('写违规日志' if args.with_log else 'no-log演示'))
    report = render_report(pkg, batch, cand_desc, date_str)
    report_path = os.path.join(logdir, 'report_%s.md' % hms)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    import verify_agent_output as vao
    vres = vao.verify(report, pkg_path, agent='m5_daily_gate_pipeline',
                      task=args.task, write_log=args.with_log)
    verify_path = os.path.join(logdir, 'verify_result_%s.json' % hms)
    with open(verify_path, 'w', encoding='utf-8') as f:
        json.dump(vres, f, ensure_ascii=False, indent=2)
    if vres.get('verdict') == 'PASS':
        print('✅ m7校验: PASS(包内可核值%d个) | 报告 %s'
              % (vres.get('package_values', 0), report_path))
    else:
        print('⛔ m7校验: %s(%d处违规) | 报告 %s'
              % (vres.get('verdict'), vres.get('violation_count', 0), report_path))
        for i, vv in enumerate((vres.get('violations') or [])[:5], 1):
            print('   %d. [%s] %s' % (i, vv.get('type'), vv.get('detail')))

    # 汇总
    passed = [r for r in batch.get('results', []) if r.get('verdict') == 'PASS']
    summary = {
        'run_ts': datetime.now().isoformat(timespec='seconds'),
        'date': date_str,
        'pipeline': 'daily_strategy_gate_pipeline v1.0 (t36, m5)',
        'candidates': {'count': len(codes), 'source_desc': cand_desc,
                       'fetcher_ok': fetcher_ok},
        'package': {'id': pkg.get('package_id'), 'fingerprint': pkg.get('fingerprint'),
                    'path': pkg_path},
        'gate': {'verdicts': v, 'avg_score': batch.get('avg_score'), 'path': gate_path},
        'signatures': sig_paths,
        'report': report_path,
        'verify': {'verdict': vres.get('verdict'),
                   'violation_count': vres.get('violation_count'), 'path': verify_path},
    }
    with open(os.path.join(logdir, 'pipeline_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(logdir, 'run_history.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'run_ts': summary['run_ts'], 'date': date_str,
                            'candidates': len(codes), 'pass': v.get('PASS', 0),
                            'verify': vres.get('verdict')}, ensure_ascii=False) + '\n')

    print('---')
    print('PASS清单(盘前参考, 下单前须现铸30分钟内签名):')
    for r in passed:
        print('  ✅ %s %s (score=%s)' % (r.get('code'), r.get('name'), r.get('score')))
    if not passed:
        print('  (无PASS候选)')
    print('汇总: %s' % os.path.join(logdir, 'pipeline_summary.json'))
    # m7 REJECTED视为流水线未闭环(exit 1); 候选REJECT属正常判决(exit 0)
    return 0 if vres.get('verdict') == 'PASS' else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description='每日9:10盘前三段式闸门流水线(t36, m5): pkg_builder→strategy_gate→verify_agent_output')
    ap.add_argument('--codes', default=None, help='候选6位代码, 逗号分隔')
    ap.add_argument('--candidates-file', default=None, help='候选JSON文件(列表或{code:dict})')
    ap.add_argument('--auto', action='store_true', help='复用选股清单去重(空则全市场涨幅前30)')
    ap.add_argument('--source', default='mainline_radar', help='信号源(默认mainline_radar)')
    ap.add_argument('--mainlines', default='', help='当日主线, 逗号分隔')
    ap.add_argument('--holdings', default='', help='当前持仓代码, 逗号分隔(G9)')
    ap.add_argument('--today-ordered', default='', help='今日已下单代码, 逗号分隔(G9)')
    ap.add_argument('--max-codes', type=int, default=30, help='候选上限(默认30)')
    ap.add_argument('--date', default=None, help='日期YYYYMMDD(默认今日)')
    ap.add_argument('--with-log', action='store_true', help='m7驳回写violation_log(默认no-log)')
    ap.add_argument('--task', default='t36', help='任务号(校验日志溯源)')
    args = ap.parse_args()
    if not (args.codes or args.candidates_file or args.auto):
        ap.print_help()
        return 2
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
