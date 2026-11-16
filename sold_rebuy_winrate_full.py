# -*- coding: utf-8 -*-
"""sold_rebuy_winrate_full.py — 深贴回补·十年全维胜率测试 (2026-09-29, 主人令「用十年数据,全面测试胜率」).

数据: qfq 全库 20130101 起(60/00/30 前缀, 剔 688/689/北交所), 实际覆盖 2013-2026 ≈14 年(超出十年下限)。
方法: 每股一次性载全史 bar, 跑 4 条独立区域链(zone∈{-6%,-8%,-10%,-12%}, 锚=空仓日收盘,
  次日起20bar内 low<=anchor*zone 首触, 成交价=min(anchor*zone, open), 出口=成交+hold-1 bar 收盘,
  链式: 平仓次bar方可立新锚, 链步长=出口bar+1)。基准链(zone=-8%)的每笔成交同时记录
  5/10/20 三种持有的收益(同批样本测持有期维度), 及 事件级元数据(年/月/regime/价位段)。
口径: 成本双边0.3%; 胜=净收益>0; 超额胜=净收益-市场同期fwd10>0。
维度: zone×hold 全组合 | 分年(基准) Wilson95 | 分regime | 分价位段(≤10/10-20/20-30/>30) |
  分月 | 最长连败 | 2026自家卖单种群(对照)。
只读两库+落盘JSON。用法: python sold_rebuy_winrate_full.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
OUT_DEFAULT = r'D:\zcodesj\data\sold_rebuy_winrate_full_20260929.json'
START = '20130101'
ZONES = (0.94, 0.92, 0.90, 0.88)
HOLDS = (5, 10, 20)
BASE_ZONE = 0.92
VALID = 20
HOLD = 20
COST = 0.003
PREFIX = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301', '302')


def wilson(win, n, z=1.96):
    if n == 0:
        return None
    p = win / n
    d = 1 + z * z / n
    ctr = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((ctr - half) / d * 100, 1), round((ctr + half) / d * 100, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=OUT_DEFAULT)
    a = ap.parse_args()
    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=120)
    mkt = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>=? GROUP BY date ORDER BY date", qc, params=(START,))
    mkt['date'] = mkt['date'].astype(str)
    mkt['idx'] = (1 + mkt['ret'].astype(float)).cumprod()
    mkt['ma20'] = mkt['idx'].rolling(20).mean()
    regime = dict(zip(mkt['date'], np.where(mkt['idx'] > mkt['ma20'], '强', '弱')))
    # 市场同期 fwd10 (与个股成交日对齐)
    mk_idx = mkt['idx'].to_numpy()
    mk_dates = mkt['date'].to_numpy()
    mk_fwd = {}
    for i in range(len(mk_idx) - 10):
        mk_fwd[mk_dates[i]] = float(mk_idx[i + 10] / mk_idx[i] - 1)
    codes = [r[0] for r in qc.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>=?", (START,)).fetchall()
        if r[0][:3] in PREFIX]
    print(f'[data] 宇宙 {len(codes)} 只, 市场regime/超额序列就绪')

    # 账户卖单(2026对照种群)
    ac = sqlite3.connect(ATTR_URI, uri=True, timeout=60)
    own = pd.read_sql_query(
        "SELECT code,trade_date FROM trade_records WHERE action IN ('SELL','PARTIAL_SELL')", ac)
    ac.close()
    own['code'] = own['code'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True).str.zfill(6)
    own['d'] = own['trade_date'].astype(str).str.replace('-', '').str[:8]
    own_set = set(zip(own['code'], own['d']))

    # 轻量聚合(9/29 MemoryError教训: 4.2M dict≈2GB+炸commit, 改tuple流式)
    base_rows = []   # (year, month, regime, pbucket, excess, ret5, ret10, ret20, own)
    grid_rets = {(z, h): [] for z in ZONES for h in HOLDS}
    n_fills = {z: 0 for z in ZONES}
    for ci, code in enumerate(codes):
        if ci % 500 == 0:
            print(f'  {ci}/{len(codes)}', flush=True)
        px = pd.read_sql_query(
            "SELECT date,open,close,low FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date",
            qc, params=(code, START))
        if len(px) < 25:
            continue
        o = px['open'].astype(float).to_numpy()
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        d = px['date'].astype(str).to_numpy()
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
                n_fills[z] += 1
                for hold in HOLDS:
                    x = hit + hold - 1
                    if x >= n:
                        break
                    grid_rets[(z, hold)].append(c[x] / fill - 1 - COST)
                if z == BASE_ZONE:
                    mk = mk_fwd.get(d[hit])
                    fc = c[hit]
                    pb = 0 if fc <= 10 else (1 if fc <= 20 else (2 if fc <= 30 else 3))
                    r5 = c[hit + 4] / fill - 1 - COST if hit + 4 < n else None
                    r10 = c[hit + 9] / fill - 1 - COST if hit + 9 < n else None
                    r20 = c[hit + 19] / fill - 1 - COST if hit + 19 < n else None
                    ex = (r10 - mk) if (mk is not None and r10 is not None) else None
                    base_rows.append((d[hit][:4], d[hit][4:6], regime.get(d[hit], '?'), pb,
                                      ex, r5, r10, r20, (code, d[hit]) in own_set))
                t = ei + 1  # 链步: 成交后跳至出口bar+1(必须在while内, 否则无限循环)
        import gc
        gc.collect()  # 2026-09-29 修: 4924只累积OOM, 每股释放
    qc.close()

    def pack(arr):
        a = np.array([x for x in arr if x is not None], dtype=float)
        if len(a) == 0:
            return {'n': 0}
        w = int((a > 0).sum())
        return {'n': len(a), 'win': round(w / len(a) * 100, 1),
                'wilson': wilson(w, len(a)), 'median': round(float(np.median(a)) * 100, 2),
                'mean': round(float(a.mean()) * 100, 2)}

    def longest_loss_streak(arr):
        s = m = 0
        for x in arr:
            if x is None:
                continue
            if x <= 0:
                s += 1
                m = max(m, s)
            else:
                s = 0
        return m

    out = {'n_fills': {f'z{int((1-z)*100)}': n_fills[z] for z in ZONES}}
    print('\n===== 全维胜率测试 (2013-2026) =====')
    print('--- zone × hold 全组合 (净收益口径) ---')
    grid = {}
    for z in ZONES:
        for hold in HOLDS:
            p = pack(grid_rets[(z, hold)])
            grid[f'z{int((1-z)*100)}_h{hold}'] = p
            print(f"zone-{int((1-z)*100)}% hold{hold:>2}: n={p['n']:>6} 胜率{p['win']}% "
                  f"CI{p['wilson']} 中位{p['median']}%")
    out['grid'] = grid

    # 基准(-8%, hold10) 全维细分 (base_rows: idx 0年 1月 2regime 3pb 4excess 5r5 6r10 7r20 8own)
    r10s = [r[6] for r in base_rows if r[6] is not None]
    bp = pack(r10s)
    streak = longest_loss_streak(r10s)
    out['base'] = {**bp, 'longest_loss_streak': streak}
    print('\n--- 基准 zone-8%/hold10 全维 ---')
    print(f"总体: n={bp['n']} 胜率{bp['win']}% CI{bp['wilson']} 中位{bp['median']}% | 最长连败{streak}")

    by_year = {}
    for y in sorted({r[0] for r in base_rows}):
        rs10 = [r[6] for r in base_rows if r[0] == y and r[6] is not None]
        exs = [r[4] for r in base_rows if r[0] == y and r[4] is not None]
        p = pack(rs10)
        e = pack(exs)
        by_year[y] = {**p, 'excess_win': e['win'], 'excess_median': e['median']}
    out['base_by_year'] = by_year
    print(f"{'年':<6}{'n':>6}{'胜率':>7}{'Wilson':>18}{'中位':>8}{'超额胜':>8}{'超额中位':>9}")
    for y, p in by_year.items():
        print(f"{y:<6}{p['n']:>6}{p['win']:>7}{str(p['wilson']):>18}{p['median']:>8}{p['excess_win']:>8}{p['excess_median']:>9}")

    for dim, coli, labels in (
            ('regime', 2, None),
            ('价位段', 3, ('≤10', '10-20', '20-30', '>30')),
            ('月份', 1, None)):
        groups = {}
        for r in base_rows:
            if r[6] is None:
                continue
            raw = r[coli]
            k = labels[raw] if (labels and isinstance(raw, (int, np.integer))) else str(raw)
            groups.setdefault(k, []).append(r[6])
        seg = {k: pack(v) for k, v in sorted(groups.items())}
        out[f'base_by_{dim}'] = seg
        print(f"--- 按{dim} ---")
        for k, p in seg.items():
            print(f"  {k}: n={p['n']} 胜率{p['win']}% 中位{p['median']}%")

    own_rs = [r[6] for r in base_rows if r[8] and r[6] is not None]
    out['own2026'] = pack(own_rs)
    p = out['own2026']
    print(f"2026自家卖单种群(对照): n={p['n']} 胜率{p['win']}% 中位{p['median']}%")

    if a.json:
        json.dump(out, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
