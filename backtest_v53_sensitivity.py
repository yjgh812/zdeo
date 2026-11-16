# -*- coding: utf-8 -*-
"""v5.3 敏感性: v5.2(最优介入+5~10%) × [大盘前置env_pos250 / 剔2022] 逐臂验证 (2026-09-19)
指令源: 9/19 v5.2定稿留档"进一步优化方向=大盘前置/主线过滤/2022排除须逐一回测验证"
设计: 复用v5.0六层完整逻辑(基线汇总须对齐data/backtest_v5_result.json证复现), 逐笔增记
  off_start(距段起%)与入场日env_pos250(上证250日窗口分位,data/sh_index_5yr_20260919.json),
  一次扫描事后分层(臂=分组不重扫):
  基线v5.0 / v5.2(+5~10%) / 臂A=v5.2∧env<=50 / 臂B=v5.2∧env<=31(当前档) / 臂C=v5.2∧剔2022 /
  臂D=v5.2∧env<=50∧剔2022 / 诊断: v5.2按env十分位桶
v5.1教训对照: 各臂报n衰减率(信号枯竭警戒线n<50)。
"""
import sqlite3, sys, os, json, statistics
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"

def f3(x):
    try: return float(x)
    except Exception: return None

sh = json.load(open(r"D:\zcodesj\data\sh_index_5yr_20260919.json", encoding="utf-8"))
ENV = dict(zip(sh["dates"], sh["env_pos250"]))

con = sqlite3.connect(QFQ, uri=True)
codes = [r[0] for r in con.execute(
    "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20210901'").fetchall()]
all_trades = []
n_scanned = 0
for code in codes:
    if not code.startswith(("60", "00", "30")):
        continue
    n_scanned += 1
    rows = con.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>='20210901' AND date<='20260918' ORDER BY date ASC",
        (code,)).fetchall()
    if len(rows) < 260:
        continue
    dates = [r[0] for r in rows]
    closes = [f3(r[1]) for r in rows]
    vols = [r[2] or 0 for r in rows]
    n = len(closes)
    if not (sum(vols) / n):
        continue
    ma20 = [None]*n; ma60 = [None]*n; v20 = [None]*n
    for i in range(59, n):
        ma20[i] = sum(closes[i-19:i+1])/20
        ma60[i] = sum(closes[i-59:i+1])/60
        v20[i] = sum(vols[i-19:i+1])/20
    in_position = False; entry_px = 0; entry_i = 0; entry_date = ''; entry_off = 0; entry_env = 0
    for i in range(120, n-2):
        c = closes[i]
        if not c or c > 30 or c < 2:
            continue
        if ma20[i] is None or ma60[i] is None or v20[i] is None:
            continue
        if in_position:
            days_held = i - entry_i
            ret = (c / entry_px - 1) * 100
            if ret <= -8:
                all_trades.append({'code': code, 'entry': entry_date, 'exit': dates[i], 'ret': ret,
                                   'days': days_held, 'exit_type': '止损', 'off': entry_off, 'env': entry_env})
                in_position = False
            elif days_held >= 20:
                all_trades.append({'code': code, 'entry': entry_date, 'exit': dates[i], 'ret': ret,
                                   'days': days_held, 'exit_type': '到期', 'off': entry_off, 'env': entry_env})
                in_position = False
            continue
        pre60 = closes[max(0, i-60):i]
        if len(pre60) < 40:
            continue
        if (min(pre60) / max(pre60) - 1) * 100 > -25:
            continue
        yr_idx = list(range(max(0, i-249), i+1))
        yr_closes = [closes[j] for j in yr_idx]
        if len(yr_closes) < 250:
            continue
        lo = min(yr_closes); lo_pos = yr_closes.index(lo); lo_g = yr_idx[lo_pos]
        start_i = None
        for j in range(i, max(i-40, lo_g), -1):
            if j >= n or j < 1 or not closes[j-1]:
                continue
            vw = vols[j:j+20]
            if len(vw) < 20:
                break
            v20j = sum(vw) / 20
            if v20j and vols[j] >= 2 * v20j and (closes[j] / closes[j-1] - 1) >= 0.03:
                start_i = j
                break
        if start_i is None:
            continue
        off_start = (c / closes[start_i] - 1) * 100
        if off_start > 17 or off_start < -3:
            continue
        days_since = i - start_i
        if days_since > 40 or days_since < 0:
            continue
        if ma20[i] <= ma60[i]:
            continue
        yr = closes[max(0, i-749):i+1]
        if len(yr) < 500:
            continue
        yq = sorted(yr)
        import bisect
        p3y = bisect.bisect_left(yq, c) / len(yq) * 100
        if p3y >= 55:
            continue
        if (c / lo - 1) * 100 >= 80:
            continue
        if (c / ma20[i] - 1) * 100 > 3:
            continue
        # 量分位排除(近3年成交量分位>=80)
        vv = sorted(vols[max(0, i-749):i+1])
        if len(vv) >= 500:
            if bisect.bisect_left(vv, vols[i]) / len(vv) >= 0.80:
                continue
        in_position = True
        entry_px = c; entry_i = i; entry_date = dates[i]
        entry_off = off_start; entry_env = ENV.get(dates[i], None)
con.close()
print(f"扫描 {n_scanned} 只, 交易 {len(all_trades)} 笔")

def arm(name, ts):
    if not ts:
        print(f"[{name}] n=0 信号枯竭"); return
    rets = [t['ret'] for t in ts]
    wins = [r for r in rets if r > 0]; losses = [r for r in rets if r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else float('inf')
    # 最大连亏
    mc = c = 0
    for r in rets:
        c = c + 1 if r <= 0 else 0
        mc = max(mc, c)
    yr = {}
    for t in ts:
        yr.setdefault(t['entry'][:4], []).append(t['ret'])
    yr_s = " ".join(f"{y}:{sum(v)/len(v):+.1f}%(n{len(v)})" for y, v in sorted(yr.items()))
    print(f"[{name}] n={len(ts)} 胜率={len(wins)/len(ts)*100:.1f}% 均值={statistics.mean(rets):+.2f}% "
          f"中位={statistics.median(rets):+.2f}% PF={pf:.2f} 最大连亏={mc}")
    print(f"    分年: {yr_s}")

print("="*80)
arm("基线v5.0(全条件)", all_trades)
v52 = [t for t in all_trades if t['off'] is not None and 5 <= t['off'] <= 10]
arm("v5.2(最优介入5~10%)", v52)
arm("臂A v5.2∧env_pos250≤50", [t for t in v52 if t['env'] is not None and t['env'] <= 50])
arm("臂B v5.2∧env_pos250≤31(当前档)", [t for t in v52 if t['env'] is not None and t['env'] <= 31])
arm("臂C v5.2∧剔2022(诊断)", [t for t in v52 if not t['entry'].startswith('2022')])
arm("臂D v5.2∧env≤50∧剔2022", [t for t in v52 if t['env'] is not None and t['env'] <= 50 and not t['entry'].startswith('2022')])
print("-"*80)
print("诊断: v5.2 按 env_pos250 十分位桶 (均值%/胜率%/n)")
import collections
buckets = collections.defaultdict(list)
for t in v52:
    if t['env'] is not None:
        buckets[t['env'] // 10].append(t['ret'])
for b in sorted(buckets):
    v = buckets[b]
    print(f"  env[{b*10}~{b*10+9}]: 均={statistics.mean(v):+.2f}% 胜={(sum(1 for x in v if x>0)/len(v))*100:.0f}% n={len(v)}")
# 逐笔落盘供后续复用
with open(r"D:\zcodesj\data\backtest_v53_trades.json", "w", encoding="utf-8") as f:
    json.dump(all_trades, f, ensure_ascii=False)
print("逐笔明细已落盘 data/backtest_v53_trades.json")
