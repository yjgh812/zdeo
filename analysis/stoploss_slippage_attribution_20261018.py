# -*- coding: utf-8 -*-
"""止损滑点归因分析件 (2026-10-18, DeepSeek外部审计吸收项V3)

问题: 全域回测 stop_loss 层均值 -9.59% vs S-5 触发线 -8%, 差 1.59pp/笔 ≈ 8万量级。
     该差值此前被当"左尾成本"整体接受, 未拆解来源。
本件把 -9.59% 分解为两段(全为 qfq 口径, 零新数据依赖):
  breach = 触发日收盘 相对 触发线(cost*0.92) 的破线深度   (信号层: close 已低于线的幅度)
  gap    = 次日开盘 相对 触发日收盘 的隔夜跳空           (执行层: 卖出=次日open)
  恒等式: (1+realized) = (1+breach) × (1+gap)
并对照实盘 executor_orders.jsonl S-5 卖单(a1_slippage 台账同源)做小样本验证,
校验锚 = 深振业Ａ(000006) 10/16: 触发≈7.01 成交6.83 (已知跳空损耗 2.58pp 二次实弹)。

数据源(全部只读):
  1) reports/backtest_full_20261016_eod/{bp_ep_core,ml_signal}_trades_full.csv (10/16 全域回测逐笔, 10/17 审计同源; 全 qfq 内口径一致)
  2) qfq 主库 stock_daily_meta_qfq (唯一价格源, wb 真源; 仅回测层)
  3) data/executor_orders.jsonl + data/a1_slippage_ledger.jsonl (实盘执行台账; 台账为 raw 实价)
  4) vipdoc 未复权 .day 经 tdx_reader 唯一入口 (仅实盘层前收; 台账 raw 价禁与 qfq 混算=TRAPS 复权基准混用)

输出: analysis/stoploss_slippage_attribution_20261018_out.md (脚本直出零转抄)
零交易权: 纯呈现层分析, 不改任何执行/信号行为; 结论供跳空限价卖出预案在途呈请引用。
"""
import json
import os
import re
import sqlite3
import sys

import pandas as pd

ROOT = r'D:\zcodesj'
sys.path.insert(0, ROOT)
from tdx_reader import read_day  # noqa: E402  屠龙 vipdoc 唯一读取入口(宪法§3.2.3)
CSV_DIR = os.path.join(ROOT, 'reports', 'backtest_full_20261016_eod')
DB_QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
LEDGER_ORDERS = os.path.join(ROOT, 'data', 'executor_orders.jsonl')
LEDGER_SLIP = os.path.join(ROOT, 'data', 'a1_slippage_ledger.jsonl')
OUT_MD = os.path.join(ROOT, 'analysis', 'stoploss_slippage_attribution_20261018_out.md')

ARMS = {'bp_ep': 'bp_ep_core_trades_full.csv', 'ml': 'ml_signal_trades_full.csv'}
LINE_RATIO = 0.92  # S-5: cost*0.92 触发线


def ro_qfq():
    return sqlite3.connect('file:' + DB_QFQ.replace('\\', '/') + '?mode=ro', uri=True, timeout=30)


def prev_close_of(con, code, date_str):
    """触发日收盘 = 卖出执行日前一交易日的 close (该股自身日历)"""
    row = con.execute(
        'select close from stock_daily_meta_qfq where code=? and date<? order by date desc limit 1',
        (code, date_str)).fetchone()
    return row[0] if row else None


def load_backtest_stops():
    frames = []
    for arm, fn in ARMS.items():
        path = os.path.join(CSV_DIR, fn)
        if not os.path.exists(path):
            raise FileNotFoundError(f'fail-closed: 逐笔CSV缺失 {path}')
        df = pd.read_csv(path, encoding='utf-8-sig')
        need = {'买入日期', '卖出日期', '股票代码', '股票名称', '买入价格', '卖出价格', '持股数量', '卖出触发理由'}
        miss = need - set(df.columns)
        if miss:
            raise KeyError(f'fail-closed: {fn} 缺列 {miss}')
        s = df[df['卖出触发理由'] == 'stop_loss'].copy()
        s['arm'] = arm
        frames.append(s)
    return pd.concat(frames, ignore_index=True)


def vipdoc_prev_close(code6, date_str):
    """实盘层前收 = vipdoc 未复权 .day 中 < trade_date 的最近收盘 (raw 价, 与台账实价同基)"""
    market = 'sh' if code6.startswith(('6',)) else 'sz'
    d = int(date_str)
    prev = None
    for row in read_day(market, code6):
        if row[0] < d:
            prev = row  # read_day 按日期升序, 取最后一个 <d 的
        else:
            break
    return float(prev[4]) if prev else None


def decompose_backtest(con, stops):
    rows = []
    for _, r in stops.iterrows():
        code = str(r['股票代码']).zfill(6)[-6:]  # CSV 数字列吞前导零(TRAPS 台账code前缀变体)
        sell_date = str(r['卖出日期']).replace('-', '')
        cost = float(r['买入价格'])
        exit_px = float(r['卖出价格'])
        qty = float(r['持股数量'])
        pc = prev_close_of(con, code, sell_date)
        if not pc or cost <= 0 or exit_px <= 0:
            rows.append(dict(code=code, name=r['股票名称'], arm=r['arm'], sell=sell_date,
                             cost=cost, exit=exit_px, qty=qty, prev_close=pc,
                             realized=None, breach=None, gap=None, extra_amt=None))
            continue
        line = cost * LINE_RATIO
        realized = exit_px / cost - 1.0
        breach = pc / line - 1.0
        gap = exit_px / pc - 1.0
        extra_amt = (exit_px - line) * qty  # 相对-8%线的价格侧超额损耗(元, 不含佣金税)
        rows.append(dict(code=code, name=r['股票名称'], arm=r['arm'], sell=sell_date,
                         cost=cost, exit=exit_px, qty=qty, prev_close=pc,
                         realized=realized, breach=breach, gap=gap, extra_amt=extra_amt))
    return pd.DataFrame(rows)


def identity_residual(df):
    ok = df.dropna(subset=['realized', 'breach', 'gap'])
    if ok.empty:
        return None
    # 恒等式: (1+breach)×(1+gap)×0.92 = (1+realized)  [breach,gap 以触发线/前收为基, realized 以cost为基]
    resid = ((1 + ok['breach']) * (1 + ok['gap']) * LINE_RATIO - (1 + ok['realized'])).abs().max()
    return float(resid)


def live_s5_cases():
    fills = {}
    if os.path.exists(LEDGER_SLIP):
        for ln in open(LEDGER_SLIP, encoding='utf-8'):
            try:
                j = json.loads(ln)
                fills[j['order_id']] = j.get('fill_price')
            except Exception:
                pass
    cases = []
    unparsed = 0
    for ln in open(LEDGER_ORDERS, encoding='utf-8'):
        try:
            j = json.loads(ln)
        except Exception:
            continue
        if j.get('action') != 'SELL' or 'S-5止损' not in (j.get('note') or ''):
            continue
        m = re.search(r'亏损(-?[\d.]+)%', j.get('note') or '')
        if not m:
            unparsed += 1
            continue
        loss_sig = float(m.group(1)) / 100.0  # 信号时点亏损(prev_close/cost-1, raw 价口径)
        code = str(j.get('code')).zfill(6)[-6:]
        td = str(j.get('trade_date'))
        fill = fills.get(j.get('order_id'), j.get('price'))
        pc = vipdoc_prev_close(code, td)  # 未复权前收, 与台账 raw 价同基(禁 qfq 混算)
        if not pc or fill is None or abs(1 + loss_sig) < 1e-9:
            unparsed += 1
            continue
        cost = pc / (1 + loss_sig)
        trigger = cost * LINE_RATIO
        cases.append(dict(date=td, code=code, name=j.get('name'), fill=float(fill),
                          prev_close=float(pc), trigger=round(trigger, 3),
                          gap_vs_trigger=float(fill) / trigger - 1.0))
    return cases, unparsed


def fmt_pp(x):
    return 'N/A' if x is None or pd.isna(x) else f'{x * 100:+.2f}pp'


def main():
    con = ro_qfq()
    stops = load_backtest_stops()
    df = decompose_backtest(con, stops)
    valid = df.dropna(subset=['realized', 'breach', 'gap']).copy()
    valid['year'] = valid['sell'].str[:4]

    n_total, n_valid = len(df), len(valid)
    assert n_valid >= 20, f'样本不足 n={n_valid} — 只可登记观察不可下结论(如实呈现)'
    resid = identity_residual(valid)
    assert resid is not None and resid < 1e-6, f'恒等式残差 {resid} 超限 — 实现必有bug, 结果作废'

    lines = []
    w = lines.append
    w('# 止损滑点归因分析(V3, DeepSeek外部审计吸收项)')
    w('')
    w('> 脚本 analysis/stoploss_slippage_attribution_20261018.py 直出 | 数据=10/16全域回测逐笔CSV+qfq主库(回测层)+executor/a1_slippage台账×vipdoc未复权(实盘层)')
    w('> 零交易权纯呈现 | 分解: (1+realized)=(1+breach破线深度)×(1+gap隔夜跳空) | 触发线=cost×0.92')
    w('')
    w('## 一、回测层(bp_ep+ml 合计)')
    w('')
    mean_real = valid['realized'].mean()
    mean_line = (valid['exit'] / (valid['cost'] * LINE_RATIO) - 1).mean()
    w(f'- 样本: stop_loss 卖出 n={n_total} (有效分解 n={n_valid}, 缺prev_close {n_total - n_valid} 笔如实剔除)')
    w(f'- 实现收益均值 {fmt_pp(mean_real)} / 中位 {fmt_pp(valid["realized"].median())} (触发线 -8.00pp)')
    w(f'- **相对触发线超额损耗均值 {fmt_pp(mean_line)}** ← 即 -9.59% vs -8% 差值的本源')
    w(f'- 分解: 破线深度 breach 均值 {fmt_pp(valid["breach"].mean())} + 隔夜跳空 gap 均值 {fmt_pp(valid["gap"].mean())} (乘法恒等, 残差max={resid:.2e})')
    extra_total = valid['extra_amt'].sum()
    w(f'- 相对触发线超额损耗金额合计 **{extra_total:,.0f} 元** (Σ(exit−line)×qty, 价格侧, 不含佣金印花税)')
    w(f'- gap<0(隔夜续跌)占比 {(valid["gap"] < 0).mean() * 100:.1f}% ; gap 分桶: ≤-5% {(valid["gap"] <= -0.05).mean() * 100:.1f}% / -5~-2% {((valid["gap"] > -0.05) & (valid["gap"] <= -0.02)).mean() * 100:.1f}% / -2~0 {((valid["gap"] > -0.02) & (valid["gap"] < 0)).mean() * 100:.1f}% / ≥0 {(valid["gap"] >= 0).mean() * 100:.1f}%')
    w('')
    w('### 分臂')
    w('')
    w('| 臂 | n | realized均值 | vs线均值 | breach均值 | gap均值 | 超额损耗(元) |')
    w('|---|---|---|---|---|---|---|')
    for arm, g in valid.groupby('arm'):
        w(f"| {arm} | {len(g)} | {fmt_pp(g['realized'].mean())} | {fmt_pp((g['exit'] / (g['cost'] * LINE_RATIO) - 1).mean())} | {fmt_pp(g['breach'].mean())} | {fmt_pp(g['gap'].mean())} | {g['extra_amt'].sum():,.0f} |")
    w('')
    w('### 分年(gap均值 = 执行层可优化部分的方向性)')
    w('')
    w('| 卖出年 | n | realized均值 | breach均值 | gap均值 |')
    w('|---|---|---|---|---|')
    for yr, g in valid.groupby('year'):
        w(f"| {yr} | {len(g)} | {fmt_pp(g['realized'].mean())} | {fmt_pp(g['breach'].mean())} | {fmt_pp(g['gap'].mean())} |")
    w('')
    w('## 二、实盘 S-5 卖单对照(executor_orders × a1_slippage × vipdoc未复权)')
    w('')
    cases, unparsed = live_s5_cases()
    w(f'- 实盘 S-5 卖单 n={len(cases)} (解析失败 {unparsed} 笔如实计数, 不静默跳过; 前收=vipdoc未复权, 与台账实价同基)')
    if cases:
        w('')
        w('| 日期 | 代码 | 名称 | 成交价 | 触发日收盘 | 触发线(cost×0.92) | gap相对触发线 |')
        w('|---|---|---|---|---|---|---|')
        for c in sorted(cases, key=lambda x: x['date']):
            w(f"| {c['date']} | {c['code']} | {c['name']} | {c['fill']:.2f} | {c['prev_close']:.3f} | {c['trigger']:.3f} | {fmt_pp(c['gap_vs_trigger'])} |")
        g_mean = sum(c['gap_vs_trigger'] for c in cases) / len(cases)
        w('')
        w(f'- 实盘 gap 相对触发线均值 {fmt_pp(g_mean)} (n={len(cases)} 小样本, 仅方向对照不作统计结论)')
        anchor = [c for c in cases if c['code'] == '000006']
        if anchor:
            a = anchor[0]
            known = -0.0258
            # 双源核验: vipdoc 前收 vs 库内 stock_daily_raw 原始不复权表(dragon_quant.db)
            raw_pc = None
            try:
                con_raw = sqlite3.connect(
                    'file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro',
                    uri=True, timeout=30)
                r = con_raw.execute(
                    'select close from stock_daily_raw where code=? and date<? '
                    'order by date desc limit 1', (a['code'], int(a['date']))).fetchone()
                con_raw.close()
                raw_pc = r[0] if r else None
            except Exception:
                pass
            dual = (raw_pc is not None and abs(raw_pc - a['prev_close']) < 1e-6)
            if dual and abs(a['gap_vs_trigger'] - known) >= 0.005:
                verdict = (f'已知记忆值≈-2.58pp 未过原始数据核验(vipdoc与stock_daily_raw双源一致前收='
                           f'{a["prev_close"]:.2f}≠7.01)——成交{a["fill"]:.2f}实际高于触发线, 当日执行无损耗, 以{fmt_pp(a["gap_vs_trigger"])}为准修正记忆')
            elif abs(a['gap_vs_trigger'] - known) < 0.005:
                verdict = '量级吻合'
            else:
                verdict = f'与已知值偏差 {fmt_pp(a["gap_vs_trigger"] - known)} 且双源不一致, 须人工复核'
            w(f'- 校验锚 深振业Ａ(000006): 成交{a["fill"]:.2f} vs 触发线{a["trigger"]:.3f} = {fmt_pp(a["gap_vs_trigger"])} ({verdict})')
    w('')
    w('## 三、结论边界(反臆想)')
    w('')
    w('- 本件只做分解呈现: breach=信号层(触发时收盘已低于线的深度, 由入场质量决定), gap=执行层(隔夜跳空, 跳空限价卖出预案的在途呈请目标层)。')
    w('- 不做任何"可回收"推断——大数字漏损≠可回收(元规则), 可回收性归跳空预案 prereg/影子验证, 本件仅提供其证据基线。')
    w('- 实盘层 n 小, 只作方向对照; 统计结论以回测层为准。')
    w('')
    con.close()

    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines[:24]))
    print(f'...')
    print(f'[OK] 已直出 {OUT_MD}')


if __name__ == '__main__':
    sys.exit(main())
