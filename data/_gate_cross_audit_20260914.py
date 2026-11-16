# -*- coding: utf-8 -*-
"""闸门交叉验证: 共现矩阵+独有拦截反事实+时间分布 20260914"""
import sqlite3, json, re, statistics, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PRE = 'file:D:/zcodesj/attribution/attribution.db?mode=ro'
QFQ = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'

con = sqlite3.connect(PRE, uri=True)
rows = con.execute(
    "SELECT ts, trade_date, code, name, price, source, verdict, rejects, warnings "
    "FROM guard_precheck_log WHERE verdict IN ('REJECT','PASS_WITH_WARN','PASS') "
    "ORDER BY id").fetchall()
con.close()

def gate_names(tf):
    out = set()
    if not tf: return out
    try: items = json.loads(tf)
    except Exception: return out
    for it in items:
        m = re.match(r'^(admit闸门|R-S5|R19|R2[0-9]|R3[0-9]|R1[0-9]|R[3-9])[^:：]*', it)
        if m: out.add(m.group(1))
    return out

best = {}
for ts, td, code, name, price, src, verdict, rej, warn in rows:
    key = (td, code)
    rank = {'REJECT': 3, 'PASS_WITH_WARN': 2, 'PASS': 1}[verdict]
    gr, gw = gate_names(rej), gate_names(warn)
    if key not in best or rank > best[key][0]:
        best[key] = [rank, td, code, name, price, verdict, set(), set()]
    best[key][6] |= gr
    best[key][7] |= gw
samples = list(best.values())

con = sqlite3.connect(QFQ, uri=True)
px = {}
for c in {s[2] for s in samples}:
    px[c] = con.execute("SELECT date, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (c,)).fetchall()
con.close()

def fwd(code, td, n):
    series = px.get(code, [])
    dates = [d for d, _ in series]
    if td not in dates: return None
    i = dates.index(td)
    if i + n >= len(series): return None
    return (series[i+n][1] / series[i][1] - 1) * 100

rej_s = [s for s in samples if s[5] == 'REJECT']

# 1. 各闸门拦截样本的时间分布
from collections import Counter
gate_hits = {}
for s in rej_s:
    for g in s[6]:
        gate_hits.setdefault(g, []).append(s)
print("=== 拦截样本时间集中度(闸门: 各日期笔数, 只列n>=5) ===")
for g, ss in sorted(gate_hits.items(), key=lambda kv: -len(kv[1])):
    if len(ss) < 5: continue
    cnt = Counter(s[1] for s in ss)
    top3 = cnt.most_common(3)
    print("%s: n=%d, 集中top3=%s (单日最大占比%.0f%%)" % (
        g, len(ss), top3, 100*top3[0][1]/len(ss)))

# 2. 共现矩阵: 重点闸门两两重叠
print("\n=== 重点闸门拦截样本共现(Jaccard) ===")
focus = ['R18', 'R26', 'admit闸门', 'R23', 'R7', 'R6', 'R22', 'R31', 'R3', 'R19']
sets = {g: {id(s) for s in ss} for g, ss in gate_hits.items()}
for i, a in enumerate(focus):
    if a not in sets: continue
    line = []
    for b in focus[i+1:]:
        if b not in sets: continue
        inter = len(sets[a] & sets[b])
        union = len(sets[a] | sets[b])
        if union: line.append("%s:%d(%.0f%%)" % (b, inter, 100*inter/union))
    print("%s(n=%d) <-> %s" % (a, len(sets[a]), ", ".join(line) if line else "-"))

# 3. 独有拦截反事实(只有该闸拦,无其他闸拦的样本)
print("\n=== 独有拦截反事实(剔除多闸并罚共线) ===")
print("闸门 | 独有n | fwd5均 | fwd10均 | fwd20均")
for g, ss in sorted(gate_hits.items(), key=lambda kv: -len(kv[1])):
    solo = [s for s in ss if len(s[6]) == 1]
    if not solo: continue
    r5 = [fwd(s[2], s[1], 5) for s in solo]; r5 = [x for x in r5 if x is not None]
    r10 = [fwd(s[2], s[1], 10) for s in solo]; r10 = [x for x in r10 if x is not None]
    r20 = [fwd(s[2], s[1], 20) for s in solo]; r20 = [x for x in r20 if x is not None]
    def m(x): return ("%+.2f%%" % statistics.mean(x)) if x else "-"
    print("%s | %d | %s | %s | %s" % (g, len(solo), m(r5), m(r10), m(r20)))

# 4. R18 深挖: 时间分布+样本明细
print("\n=== R18 拦截明细 ===")
for s in gate_hits.get('R18', []):
    f5, f10 = fwd(s[2], s[1], 5), fwd(s[2], s[1], 10)
    print("%s %s(%s) 闸门集=%s fwd5=%s fwd10=%s" % (
        s[1], s[2], s[3], ",".join(sorted(s[6])),
        ("%+.1f%%" % f5) if f5 is not None else "-",
        ("%+.1f%%" % f10) if f10 is not None else "-"))

# 5. R-S5 REJECT 10笔的时间(9/8裁决前?)
print("\n=== R-S5 REJECT 时间分布 ===")
for s in gate_hits.get('R-S5', []):
    f5 = fwd(s[2], s[1], 5)
    print("%s %s(%s) fwd5=%s" % (s[1], s[2], s[3], ("%+.1f%%" % f5) if f5 is not None else "-"))
