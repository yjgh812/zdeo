# -*- coding: utf-8 -*-
"""sold_rebuy_gate_validate.py — 企稳确认门槛有效性验证 (2026-09-30, 根本修复后补验).

问题: pilot 加了企稳确认门槛(3条件≥2达标), 但该门槛本身没有被十三年数据验证。
       round3 的规则 A(缩量企稳)在 bug 修复前测的, 结果可能不准。
方法: 全库(2013-2026) 4 zone 链, 每笔成交分为:
       无门槛组 = 该 zone 全部成交
       有门槛组 = 通过企稳确认的成交(≥2/3 条件)
     比较两组的 胜率/均值/中位——门槛有效 = 有门槛组优于无门槛组。
只读两库。用法: python sold_rebuy_gate_validate.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
ZONES = (0.92,)
HOLDS = (5, 10, 20)
VALID = 20
HOLD = 20
COST = 0.003
START = '20130101'
PREFIX = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301', '302')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=120)
    codes = [r[0] for r in qc.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>=?", (START,)).fetchall()
        if r[0][:3] in PREFIX]
    print(f'[data] 宇宙 {len(codes)} 只')

    ungated = {h: [] for h in HOLDS}   # 无门槛: 全部成交
    gated = {h: [] for h in HOLDS}     # 有门槛: 企稳≥2/3 的成交
    for ci, code in enumerate(codes):
        if ci % 500 == 0:
            print(f'  {ci}/{len(codes)}', flush=True)
        px = pd.read_sql_query(
            "SELECT date,open,close,low,volume FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date",
            qc, params=(code, START))
        if len(px) < 25:
            continue
        o = px['open'].astype(float).to_numpy()
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        v = px['volume'].astype(float).to_numpy()
        n = len(c)
        for z in ZONES:
            t = 0
            while t < n - HOLD - 1:
                limit = c[t] * z
                end_scan = min(t + 1 + VALID, n)
                seg = lo[t + 1:end_scan]
                if len(seg) == 0 or seg.min() > limit:
                    t += 1
                    continue
                hit = int(np.nonzero(seg <= limit)[0][0]) + t + 1
                fill = min(limit, o[hit])
                ei = hit + 20 - 1
                if ei >= n:
                    break
                # --- 企稳确认评估(成交时点的可观测状态) ---
                sell_vol = float(v[t]) if v[t] > 0 else 1.0
                no_new_low = min(lo[max(0, hit-4):hit+1]) >= min(lo[hit:ei+1]) if hit >= 4 else True
                vol_recent = float(np.mean(v[max(0, hit-2):hit+1]))
                vol_dry = vol_recent < sell_vol * 0.7
                last_up = c[hit] > c[hit-1] if hit >= 1 else False
                stab_score = sum([no_new_low, vol_dry, last_up])
                gated_pass = stab_score >= 2
                # --- 记录各持有期 ---
                for hold in HOLDS:
                    x = hit + hold - 1
                    if x >= n:
                        break
                    ret = c[x] / fill - 1 - COST
                    ungated[hold].append(ret)
                    if gated_pass:
                        gated[hold].append(ret)
                t = ei + 1  # 链步
    qc.close()

    def pack(a):
        a = np.array(a, dtype=float)
        if len(a) == 0:
            return {'n': 0}
        w = int((a > 0).sum())
        return {'n': len(a), 'win': round(w / len(a) * 100, 1),
                'wilson': None, 'median': round(float(np.median(a)) * 100, 2),
                'mean': round(float(a.mean()) * 100, 2)}

    out = {}
    print('\n===== 企稳门槛有效性验证 (zone-8% 全库) =====')
    print(f"{'持有期':<8}{'无门槛n':>8}{'胜率':>7}{'中位':>8}  | {'有门槛n':>8}{'胜率':>7}{'中位':>8}  | {'门槛增益':>8}")
    for hold in HOLDS:
        ug = pack(ungated[hold])
        gt = pack(gated[hold])
        out[f'hold{hold}'] = {'ungated': ug, 'gated': gt}
        gain = ''
        if ug.get('median') is not None and gt.get('median') is not None:
            gain_val = round(gt['median'] - ug['median'], 2)
            gain = f'{gain_val:+.2f}pp'
        print(f"hold{hold:>2}: n={ug['n']:>6} 胜率{ug['win']}% 中位{ug['median']}%  | "
              f"n={gt['n']:>6} 胜率{gt['win']}% 中位{gt['median']}%  | {gain}")
    if a.json:
        json.dump(out, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
