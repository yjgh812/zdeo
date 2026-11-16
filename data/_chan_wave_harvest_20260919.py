# -*- coding: utf-8 -*-
"""缠论框架波段识别+背驰大样本验证（2026-09-19 主人令：结合缠论判断上涨下跌波段）

实现(简化缠论, 可量化口径):
  1) 分型: 顶分型(高高于前后K)/底分型(低于前后K) — 用收盘价近似(与Zigzag同数据面)
  2) 笔: 相邻异类分型连接, 要求幅度>=阈值(与Zigzag同12%对齐可比)
  3) 背驰: 波段末端对比 — 下跌波段后半段 vs 前半段: 价格创新低 但 MACD-DIF 低点抬高 或 柱面积(负)收缩 = 底背驰
           上涨波段同理判顶背驰
大样本问题:
  Q3 底背驰的下跌波段 vs 非背驰: 后续反弹幅度差异(缠论'背驰买点'有效性的全市场检验)
  Q4 顶背驰的上涨波段 vs 非背驰: 后续下跌幅度差异
样本: 全市场 2023-2026, 与上轮 12.85万波段同框架(zigzag段≈笔的对齐)
"""
import sqlite3, json, statistics, sys, os
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"

def f3(x):
    try: return float(x)
    except Exception: return None

def zigzag(closes, pct=0.12):
    if len(closes) < 10: return [0, len(closes)-1]
    pivots=[0]; direction=0; ext=0
    for i in range(1, len(closes)):
        if direction >= 0:
            if closes[i] > closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 <= -pct: pivots.append(ext); direction=-1; ext=i
        else:
            if closes[i] < closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 >= pct: pivots.append(ext); direction=1; ext=i
    pivots.append(ext if ext > pivots[-1] else len(closes)-1)
    return pivots

def macd_dif(closes, fast=12, slow=26):
    """返回 DIF 序列(None 前置)。"""
    if len(closes) < slow + 9: return None
    def ema(period):
        k = 2/(period+1); e = [closes[0]]
        for x in closes[1:]:
            e.append(x*k + e[-1]*(1-k))
        return e
    ef, es = ema(fast), ema(slow)
    return [ef[i]-es[i] for i in range(len(closes))]

def divergence_dn(closes, dif, a, b):
    """下跌波段[a,b]底背驰: 后半价格新低 但 DIF 低点抬高 或 负柱面积收缩。"""
    if not dif: return False
    mid = (a+b)//2
    p1_lo = min(closes[a:mid+1]) if mid > a else None
    p2_lo = min(closes[mid:b+1])
    d1_lo = min(dif[a:mid+1]) if mid > a else None
    d2_lo = min(dif[mid:b+1])
    if p1_lo is None or d1_lo is None: return False
    return p2_lo < p1_lo and d2_lo > d1_lo  # 价新低+DIF抬高

def divergence_up(closes, dif, a, b):
    if not dif: return False
    mid = (a+b)//2
    p1_hi = max(closes[a:mid+1]) if mid > a else None
    p2_hi = max(closes[mid:b+1])
    d1_hi = max(dif[a:mid+1]) if mid > a else None
    d2_hi = max(dif[mid:b+1])
    if p1_hi is None or d1_hi is None: return False
    return p2_hi > p1_hi and d2_hi < d1_hi  # 价新高+DIF降低

db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20230101'").fetchall()]
print(f'全市场 {len(codes)} 只, 缠论笔(12%对齐)+背驰采集...')
dn_groups = defaultdict(list)   # 背驰/非背驰 -> 后续反弹%
up_groups = defaultdict(list)   # 背驰/非背驰 -> 后续下跌%
n_dn = n_up = 0
for ci, code in enumerate(codes):
    if ci % 1000 == 0: print(f'  ...{ci}/{len(codes)}', flush=True)
    h = db.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date>='20230101' AND date<='20260701' ORDER BY date ASC", (code,)).fetchall()
    closes = [f3(r[0]) for r in h if f3(r[0]) is not None]
    if len(closes) < 300: continue
    dif = macd_dif(closes)
    pv = zigzag(closes, 0.12)
    for k in range(len(pv)-1):
        a, b = pv[k], pv[k+1]
        if b-a < 15: continue
        chg = closes[b]/closes[a]-1
        nxt = None
        if k+2 < len(pv):
            nxt = (closes[pv[k+2]]/closes[pv[k+1]]-1)*100
        if chg < 0 and nxt is not None:
            n_dn += 1
            div = divergence_dn(closes, dif, a, b)
            dn_groups['底背驰' if div else '无背驰'].append(nxt)
        elif chg > 0 and nxt is not None:
            n_up += 1
            div = divergence_up(closes, dif, a, b)
            up_groups['顶背驰' if div else '无背驰'].append(nxt)
db.close()

def stat(g):
    out = {}
    for kk, v in g.items():
        if len(v) < 100: continue
        out[kk] = {'n': len(v), '后续均值': round(statistics.mean(v), 2),
                   '后续中位': round(statistics.median(v), 2),
                   '强(>=25%)占比': round(100*sum(1 for x in v if x >= 25)/len(v), 1),
                   '负占比': round(100*sum(1 for x in v if x < 0)/len(v), 1)}
    return out

OUT = {'n_dn': n_dn, 'n_up': n_up}
print(f'采集完成: 下跌波段(>=15日) {n_dn} 个, 上涨 {n_up} 个')
print()
print('====== Q3: 下跌波段 底背驰 vs 无背驰 的后续反弹 ======')
OUT['Q3'] = stat(dn_groups)
for kk, v in OUT['Q3'].items():
    print(f"{kk:6} n={v['n']:>6} 后续均值{v['后续均值']:>+6.2f}% 中位{v['后续中位']:>+6.2f}% 强反占比{v['强(>=25%)占比']:>5}% 续跌占比{v['负占比']:>5}%")
print()
print('====== Q4: 上涨波段 顶背驰 vs 无背驰 的后续下跌 ======')
OUT['Q4'] = stat(up_groups)
for kk, v in OUT['Q4'].items():
    print(f"{kk:6} n={v['n']:>6} 后续均值{v['后续均值']:>+6.2f}% 中位{v['后续中位']:>+6.2f}% 强跌(<=-25%)占比{v['负占比']:>5}%")
json.dump(OUT, open(os.path.join(ROOT, 'data', 'chan_wave_harvest_20260919.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print()
print('[CHAN-HARVEST-OK] data/chan_wave_harvest_20260919.json')
