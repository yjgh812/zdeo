#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pd018_v2_check.py — PD-018 规则v2 尾盘检查器（Phase 1 提请-确认，零下单）
================================================================================
裁决依据：reports/PD018_RULE_V2_VERDICT_PACK_20261001.md，主人 2026-10-01「按推荐继续」
批准规则 v2 接入卖出链；Phase 1 = 提请-确认模式（本脚本只提请+记账，禁止任何下单），
转全自动节奏由主人另行定。

规则 v2 语义（LOCKED，呈报 §二）：
  持仓股 ∧ 昨日收盘连板数≥2（昨日与前日均为收盘涨停）∧ 今日盘中触板（high 曾达涨停价）
  ∧ 当前未封（现价 < 涨停价）→ 提请"炸板尾盘离场"（终态由 seal_snapshot 复核）。
  不管：买入端 / 伪封（收盘仍涨停）/ 替代 S-5（S-5 优先）/ 高度<2 首板。

调度（Phase 1）：交易日 14:30 ZCode 止损链内加步调用（接线不入链逻辑）；
  ZCode 应用未运行时当日提请缺失由哨兵告警兜底（提请非硬止损，可接受并已呈报）。
台账：data/pd018_v2_ledger.jsonl（幂等：同 date+code 不重复）；次日复盘用
  seal_snapshot 终态校准（回封=miss/false_positive 标注）。

用法：
  python pd018_v2_check.py                 # 盘中实弹检查（提请-确认）
  python pd018_v2_check.py --dry-run --date 20260930 --codes 600719
                                           # 历史验收（显式传码，不查 westock）
退出码：0=正常（含无触发）；2=异常（已告警）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# westock_client 在 D:\workbuddyspace(与 write_positions_snapshot/trend_request_executor 同源):
# 缺此行时 main() 内 from westock_client import 会 ModuleNotFoundError(10/2彩排抓出,
# 将炸 14:30 链第七步B; 此前9/30 dry系在其他cwd侥幸通过)
sys.path.insert(0, r"D:\workbuddyspace")

import sqlite3

import price_limits  # noqa: E402  涨停判定唯一口径
from seal_snapshot import fetch_batch  # noqa: E402  复用腾讯批量快照（同款接口）

MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
LEDGER = Path(r'D:\zcodesj\data') / 'pd018_v2_ledger.jsonl'


def notify(title: str, content: str) -> None:
    import subprocess
    try:
        subprocess.run([sys.executable, os.path.join(HERE, 'notify.py'), 'send',
                        '--title', title, '--content', content, '--level', 'warning'],
                       timeout=60)
    except Exception as e:
        print(f'[v2] notify 发送失败: {e}（fail-loud）')


def prev_trade_dates(c: sqlite3.Connection, today: int, n: int = 2) -> list[int]:
    """今日(或指定日)之前 n 个 hsj 交易日，降序返回。"""
    rows = c.execute(
        'SELECT DISTINCT date FROM stock_daily_hsj WHERE date < ? ORDER BY date DESC LIMIT ?',
        (today, n)).fetchall()
    return [r[0] for r in rows]


def day_limit_up(c: sqlite3.Connection, code: str, d: int, name: str) -> bool:
    """d 日收盘涨停——**与影子台账(shadow_pdbreak_track.py height函数)同款算法**
    close >= round(prev_close×1.1,2) 主板 / ×1.2 创业板（32例证据口径一致性优先；
    price_limits 容差法与影子口径在西陇科学002584等样本存在分歧，边界=ST档与
    除权日近似如实声明，持仓ST由R闸禁买兜底）。"""
    row = c.execute(
        'SELECT close FROM stock_daily_hsj WHERE code=? AND date=?', (code, d)).fetchone()
    prev = c.execute(
        'SELECT close FROM (SELECT close FROM stock_daily_hsj WHERE code=? AND date<? '
        'ORDER BY date DESC LIMIT 1)', (code, d)).fetchone()
    if not row or not prev or not row[0] or not prev[0]:
        return False
    lim = 1.2 if code.startswith(('30', '68')) else 1.1
    return float(row[0]) >= round(float(prev[0]) * lim, 2)


def height_ge2(c: sqlite3.Connection, code: str, d1: int, d2: int, name: str) -> bool:
    """昨日(d1)与前日(d2)均为收盘涨停 → 连板数≥2。"""
    return day_limit_up(c, code, d1, name) and day_limit_up(c, code, d2, name)


def ledger_append(rec: dict) -> bool:
    """幂等追加（同 date+code 已存在则跳过）。返回是否新写。"""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding='utf-8').splitlines():
            try:
                old = json.loads(line)
                if old.get('date') == rec['date'] and old.get('code') == rec['code']:
                    return False
            except Exception:
                continue
    with LEDGER.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return True


def execute_sell(rec: dict, qty: int) -> dict:
    """Phase2 自动卖出（S-5 同级）：trade_executor 唯一入口 + --is-stop-loss
    （豁免确认与时间窗，仍走 pre_sell_check C1-C9 双层防线非裸卖）。
    返回 {'ok': bool, 'detail': str, 'status': str}。REJECT/失败均如实上账。"""
    import subprocess
    reason = f"PD-018v2炸板离场(高度{rec.get('height', '>=2')})"
    cmd = [sys.executable, os.path.join(HERE, 'trade_executor.py'), 'sell',
           '--code', rec['code'], '--name', rec['name'], '--price', str(rec['price']),
           '--volume', str(qty), '--reason', reason,
           '--execute', '--is-stop-loss']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           timeout=120, shell=False,
                           env=dict(os.environ, PYTHONUTF8="1"))
        ok = r.returncode == 0
        tail = (r.stdout or r.stderr or '').strip()[-160:]
        status = 'auto_sold' if ok else 'exec_failed'
        if not ok:
            notify(f"PD-018-v2 自动卖出失败({rec['date']} {rec['code']} {rec['name']})",
                   f"rc={r.returncode} {tail} —— 请人工核对归因库与持仓（报文层假失败先例=9/22宏桥）")
        return {'ok': ok, 'detail': tail, 'status': status}
    except Exception as e:
        notify(f"PD-018-v2 自动卖出异常({rec['code']} {rec['name']})", str(e)[:120])
        return {'ok': False, 'detail': str(e)[:120], 'status': 'exec_failed'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='', help='归属交易日 YYYYMMDD（默认=今日）')
    ap.add_argument('--codes', default='', help='dry-run：逗号分隔持仓码（不查 westock）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--execute', action='store_true',
                    help='Phase2 自动卖出（2026-10-01主人批准转全自动）：触发即按S-5同级自动执行，'
                         'trade_executor唯一入口+--is-stop-loss（仍走pre_sell C1-C9防线）；'
                         '缺省=False=Phase1仅提请')
    args = ap.parse_args()

    today = str(args.date) if args.date else None
    if not today:
        # 默认=主库最新交易日（非假期日历日）：假期日运行时快照定格上一交易日，
        # 用日历日会写错日期台账行（10/2 假期空转边角，2026-10-01 修复）
        _c0 = sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True)
        today = str(_c0.execute('SELECT MAX(date) FROM stock_daily_hsj').fetchone()[0])
        _c0.close()

    # 持仓码来源：实弹=westock 唯一真值；dry-run=显式传码
    if args.dry_run:
        codes = [x.strip() for x in args.codes.split(',') if x.strip()]
        if not codes:
            print('[v2] dry-run 需要 --codes')
            return 2
    else:
        from westock_client import WeStockClient
        ws = WeStockClient()
        positions = ws.positions().get('data', {}).get('positions', [])
        codes = [str(x.get('code', '')).zfill(6) for x in positions
                 if int(x.get('quantity', 0) or 0) > 0]
        qty_map = {str(x.get('code', '')).zfill(6): int(x.get('quantity', 0) or 0)
                   for x in positions if int(x.get('quantity', 0) or 0) > 0}
    if not codes:
        print('[v2] 持仓为空，无检查对象')
        return 0

    c = sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True)
    prevs = prev_trade_dates(c, int(today), 2)
    if len(prevs) < 2:
        print(f'[v2] 历史数据不足（{prevs}）')
        return 2
    d1, d2 = prevs  # 昨日、前日

    names = dict(c.execute('SELECT code, name FROM stock_info'))
    snaps = {s['code']: s for s in fetch_batch(codes)} if codes else {}

    triggers = []
    for code in codes:
        name = names.get(code, '')
        snap = snaps.get(code)
        if not snap or not snap.get('prev'):
            print(f'  [skip] {code} {name}: 无实时快照')
            continue
        if not height_ge2(c, code, d1, d2, name):
            continue
        # 价格法：今日触板（high 曾达）且当前未封
        pct = price_limits.board_limit_pct(code)
        try:
            dd = datetime.date(int(today[:4]), int(today[4:6]), int(today[6:]))
            is_st = price_limits.is_risk_warning_name(name)
            pct = price_limits.price_limit_pct(code, dd, is_risk_warning=is_st)
        except Exception:
            pass
        up_ret = float(snap['high']) / float(snap['prev']) - 1
        cur_ret = float(snap['price']) / float(snap['prev']) - 1
        touched = up_ret >= pct - 0.002
        not_sealed = cur_ret < pct - 0.002
        if touched and not_sealed:
            rec = {
                'date': today, 'code': code, 'name': name,
                'height_ge2': True, 'prev_dates': [str(d1), str(d2)],
                'prev_close': float(snap['prev']), 'high': float(snap['high']),
                'price': float(snap['price']), 'limit_pct': pct,
                'action': 'PROMPT_CONFIRM', 'status': 'pending',
                'mode': 'dry-run' if args.dry_run else 'live',
                'run_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            new = ledger_append(rec)
            triggers.append(rec)
            print(f"  [TRIGGER] {code} {name} 高度≥2 炸板未封 "
                  f"(high {up_ret:+.2%} 触板, 现 {cur_ret:+.2%} 未封){' [台账新增]' if new else ' [台账已在]'}")

    c.close()
    if triggers:
        msg = '; '.join(f"{t['code']} {t['name']}" for t in triggers)
        if args.execute and not args.dry_run:
            # Phase2 自动卖出（S-5 同级）：逐只执行并回写台账状态
            results = []
            for t in triggers:
                qty = qty_map.get(t['code'], 0)
                if qty <= 0:
                    t['status'] = 'exec_failed'
                    results.append(f"{t['code']} {t['name']}: 持仓数量不可得, 未执行")
                    continue
                res = execute_sell(t, qty)
                t['status'] = res['status']
                results.append(f"{t['code']} {t['name']}: {'✅已卖' if res['ok'] else '❌失败'} {res['detail'][:60]}")
            # 回写台账 status（重写匹配行）
            if LEDGER.exists():
                lines = []
                for line in LEDGER.read_text(encoding='utf-8').splitlines():
                    try:
                        o = json.loads(line)
                        m = next((t for t in triggers if t['date'] == o.get('date') and t['code'] == o.get('code')), None)
                        if m:
                            o['status'] = m['status']
                            lines.append(json.dumps(o, ensure_ascii=False))
                        else:
                            lines.append(line)
                    except Exception:
                        lines.append(line)
                LEDGER.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            print(f"[v2][Phase2自动执行] {today} 触发 {len(triggers)} 只:")
            for r in results:
                print('  ', r)
        else:
            if not args.dry_run:
                notify(f'PD-018-v2 触发提请({today})', f'{msg} —— 高度≥2 炸板尾盘未回封，规则v2提请离场确认（Phase1不自动卖）')
            print(f"[v2] 触发 {len(triggers)} 只{'（dry-run不发toast）' if args.dry_run else '，已 toast 提请（Phase 1 提请-确认，未下单）'}: {msg}")
    else:
        print(f"[v2] {today} 持仓 {len(codes)} 只无 v2 触发")
    return 0


if __name__ == '__main__':
    sys.exit(main())
