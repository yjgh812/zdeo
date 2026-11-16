# -*- coding: utf-8 -*-
"""同态历史概率标定（2026-09-19 从对方系统学习落地）
原理: 给每只股票每天定义一个状态向量(4维), 统计每个状态下未来20日上涨的历史频率。
用途: 输入当前股票状态 → 查同状态历史胜率 = 量化"上涨概率"(非画像匹配分)。

状态向量(4维, 全部当日可见无前视):
  trend: 多头(MA20>MA60)=1 / 空头=0
  shrink: 缩量<=0.7 / 常态0.7-1.3 / 放量>1.3 (近20日量/年均量)
  pos: 低位<20% / 中低20-50% / 中高50-80% / 高位>80% (pos250)
  mom: 跌(r20<-5%) / 平(-5~+5%) / 涨(>5%) (20日动量)
共 2×3×4×3 = 72 种状态。
"""
from __future__ import annotations
import json, os, sqlite3, sys, statistics
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
CACHE = os.path.join(ROOT, "data", "state_lookup_5yr.json")


def f3(x):
    try:
        return float(x)
    except Exception:
        return None


def build_lookup():
    """从5年全市场数据构建状态→历史胜率查找表。"""
    db = sqlite3.connect(QFQ, uri=True)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20210901'").fetchall()]
    codes = [c for c in codes if not c.startswith('688')]
    # state -> [count, wins, sum_ret, all_rets]
    table = defaultdict(lambda: [0, 0, 0.0, []])
    total_days = 0
    for ci, code in enumerate(codes):
        if ci % 500 == 0:
            print(f'  ...{ci}/{len(codes)} states={len(table)}', flush=True)
        h = db.execute(
            "SELECT date, close, volume FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>='20220101' AND date<='20260701' ORDER BY date ASC",
            (code,)).fetchall()
        rows = [(d, f3(c), f3(v)) for d, c, v in h if f3(c) is not None]
        if len(rows) < 320:
            continue
        dates = [r[0] for r in rows]; closes = [r[1] for r in rows]; vols = [r[2] or 0 for r in rows]
        n = len(closes); avg_v = sum(vols) / n
        if not avg_v:
            continue
        ma20 = [None]*n; ma60 = [None]*n
        for i in range(59, n):
            ma20[i] = sum(closes[i-19:i+1])/20
            ma60[i] = sum(closes[i-59:i+1])/60
        yr = closes[:250] if n >= 250 else closes
        yr_hi, yr_lo = max(yr), min(yr)
        for i in range(260, n - 25):
            c = closes[i]
            if not ma20[i] or not ma60[i]:
                continue
            w250 = closes[i-249:i+1]
            hi250, lo250 = max(w250), min(w250)
            if hi250 <= lo250:
                continue
            pos250 = 100*(c-lo250)/(hi250-lo250)
            r20 = (c/closes[i-20]-1)*100 if closes[i-20] else 0
            sh = sum(vols[i-19:i+1])/20/avg_v
            trend = 1 if ma20[i] > ma60[i] else 0
            s_shrink = 0 if sh <= 0.7 else (1 if sh <= 1.3 else 2)
            s_pos = 0 if pos250 < 20 else (1 if pos250 < 50 else (2 if pos250 < 80 else 3))
            s_mom = 0 if r20 < -5 else (1 if r20 <= 5 else 2)
            state = f"{trend}|{s_shrink}|{s_pos}|{s_mom}"
            fwd20 = (closes[min(i+20, n-1)] / c - 1) * 100
            b = table[state]
            b[0] += 1; b[1] += (fwd20 > 0); b[2] += fwd20; b[3].append(fwd20)
            total_days += 1
    db.close()
    # 整理输出
    out = {}
    for state, (cnt, wins, sret, rets) in sorted(table.items()):
        if cnt < 30:
            continue  # 样本太少不输出
        out[state] = {
            'n': cnt,
            'win_rate': round(100*wins/cnt, 1),
            'mean_fwd20': round(sret/cnt, 2),
            'median_fwd20': round(statistics.median(rets), 2),
        }
    json.dump(out, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'[LOOKUP-OK] {CACHE} | 状态数 {len(out)} | 总样本 {total_days:,}')
    return out


# ===== 共享函数（供 preflight / direction_census / holdings_risk_scan 导入） =====

_table_cache = None

def _load_table():
    global _table_cache
    if _table_cache is None:
        if os.path.exists(CACHE):
            _table_cache = json.load(open(CACHE, encoding='utf-8'))
        else:
            build_lookup()
            _table_cache = json.load(open(CACHE, encoding='utf-8'))
    return _table_cache


def compute_state_vector(closes, vols, avg_v):
    """从倒序 closes/vols 计算4维状态向量字符串。
    返回 (state_str, detail_dict)。"""
    c = closes[0]
    ma20 = sum(closes[:20]) / 20 if len(closes) >= 20 else c
    ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else c
    yr = closes[:250] if len(closes) >= 250 else closes
    hi_yr, lo_yr = max(yr), min(yr)
    pos250 = 100 * (c - lo_yr) / (hi_yr - lo_yr) if hi_yr > lo_yr else 50
    r20 = (c / closes[20] - 1) * 100 if len(closes) > 20 and closes[20] else 0
    v250 = sum(vols[:250]) / 250 if len(vols) >= 250 else avg_v
    v20 = sum(vols[:20]) / 20 if len(vols) >= 20 else v250
    sh = v20 / v250 if v250 else 1
    trend = 1 if ma20 > ma60 else 0
    s_shrink = 0 if sh <= 0.7 else (1 if sh <= 1.3 else 2)
    s_pos = 0 if pos250 < 20 else (1 if pos250 < 50 else (2 if pos250 < 80 else 3))
    s_mom = 0 if r20 < -5 else (1 if r20 <= 5 else 2)
    state = f"{trend}|{s_shrink}|{s_pos}|{s_mom}"
    detail = {'trend': trend, 'shrink': round(sh, 2), 'pos250': round(pos250), 'r20': round(r20, 1)}
    return state, detail


def lookup_state(state_str):
    """查同态历史统计。返回 dict 或 None。"""
    table = _load_table()
    return table.get(state_str)


def query_state(code: str):
    """查询单票当前状态的历史胜率。"""
    if not os.path.exists(CACHE):
        build_lookup()
    table = json.load(open(CACHE, encoding='utf-8'))
    db = sqlite3.connect(QFQ, uri=True)
    h = db.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date DESC LIMIT 780", (code,)).fetchall()
    db.close()
    rows = [(d, f3(c), f3(v)) for d, c, v in h if f3(c) is not None]
    if len(rows) < 300:
        print(f'{code} 数据不足'); return
    closes = [r[1] for r in rows]; vols = [r[2] or 0 for r in rows]
    c = closes[0]; n = len(closes); avg_v = sum(vols)/n
    ma20 = sum(closes[:20])/20; ma60 = sum(closes[:60])/60
    yr = closes[:250]; hi_yr, lo_yr = max(yr), min(yr)
    pos250 = 100*(c-lo_yr)/(hi_yr-lo_yr) if hi_yr > lo_yr else 50
    r20 = (c/closes[20]-1)*100 if closes[20] else 0
    sh = sum(vols[:20])/20/avg_v if avg_v else 1
    trend = 1 if ma20 > ma60 else 0
    s_shrink = 0 if sh <= 0.7 else (1 if sh <= 1.3 else 2)
    s_pos = 0 if pos250 < 20 else (1 if pos250 < 50 else (2 if pos250 < 80 else 3))
    s_mom = 0 if r20 < -5 else (1 if r20 <= 5 else 2)
    state = f"{trend}|{s_shrink}|{s_pos}|{s_mom}"
    nm = get_name(code) or '?'
    info = table.get(state)
    if info:
        print(f'{code} {nm} 状态[{state}] (多头={trend} 缩量={s_shrink} 位置={s_pos} 动量={s_mom})')
        print(f'  同状态历史: n={info["n"]:,} 胜率={info["win_rate"]}% 均值={info["mean_fwd20"]:+.2f}% 中位={info["median_fwd20"]:+.2f}%')
    else:
        print(f'{code} {nm} 状态[{state}] 无历史样本')


def main():
    ap = __import__('argparse').ArgumentParser()
    ap.add_argument('--build', action='store_true', help='构建查找表')
    ap.add_argument('--query', default='', help='查询代码')
    args = ap.parse_args()
    if args.build or not os.path.exists(CACHE):
        build_lookup()
    if args.query:
        for c in args.query.split(','):
            query_state(c.strip())


if __name__ == '__main__':
    main()
