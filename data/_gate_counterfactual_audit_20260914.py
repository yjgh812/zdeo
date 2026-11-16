# -*- coding: utf-8 -*-
"""闸门反事实验证 20260914
方法: guard_precheck_log 每条判定 -> 解析触发闸门 -> 用 qfq 库算拦截/放行样本的
fwd5/10/20 收益 -> 按闸门聚合: 拦截组收益<0=拦对(挡亏), >0=拦错(挡钱)。
基准价: price 列优先(与当日收盘差>8%弃用回退收盘), 缺失用当日收盘。
同日同票去重: verdict 取最强(REJECT>PASS_WITH_WARN>PASS), 闸门触发取并集。
"""
import sqlite3, json, re, statistics, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PRE = 'file:D:/zcodesj/attribution/attribution.db?mode=ro'
QFQ = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'

# ---- 1. 拉判定日志 ----
con = sqlite3.connect(PRE, uri=True)
rows = con.execute(
    "SELECT ts, trade_date, code, name, price, source, verdict, rejects, warnings "
    "FROM guard_precheck_log WHERE verdict IN ('REJECT','PASS_WITH_WARN','PASS') "
    "ORDER BY id").fetchall()
con.close()
print("判定记录:", len(rows))

GATE_PAT = re.compile(r'^(admit闸门|R-S5|R1[0-9]|R[2-9][0-9]?[a-zA-Z]?|R[3-9])')
def gate_names(text_field):
    """从 rejects/warnings JSON 列表提取闸门名"""
    out = set()
    if not text_field: return out
    try: items = json.loads(text_field)
    except Exception: return out
    for it in items:
        m = re.match(r'^(admit闸门|R-S5|R19|R2[0-9]|R3[0-9]|R1[0-9]|R[3-9])[^:：]*', it)
        if m:
            g = m.group(1)
            g = g.rstrip('：:') if g != 'admit闸门' else g
            out.add(g)
    return out

# ---- 2. 同日同票去重 ----
best = {}
for ts, td, code, name, price, src, verdict, rej, warn in rows:
    key = (td, code)
    rank = {'REJECT': 3, 'PASS_WITH_WARN': 2, 'PASS': 1}[verdict]
    gates_rej, gates_warn = gate_names(rej), gate_names(warn)
    if key not in best or rank > best[key][0]:
        best[key] = [rank, td, code, name, price, verdict, set(), set(), src]
    best[key][6] |= gates_rej
    best[key][7] |= gates_warn
samples = list(best.values())
print("去重后样本:", len(samples))

# ---- 3. qfq 价格序列(只拉需要的票) ----
codes = list({s[2] for s in samples})
con = sqlite3.connect(QFQ, uri=True)
px = {}
for c in codes:
    rs = con.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (c,)).fetchall()
    px[c] = rs
con.close()

def fwd(code, td, n):
    """返回(基准价, fwd_n 收益%) ; 基准=td当日收盘; 不足n根返回None"""
    series = px.get(code, [])
    dates = [d for d, _ in series]
    if td not in dates: return None, None
    i = dates.index(td)
    base = series[i][1]
    if base is None or base <= 0 or i + n >= len(series): return base, None
    return base, (series[i + n][1] / base - 1) * 100

def exec_price(s):
    """执行价: price列优先, 与当日收盘差>8%视为口径异常回退收盘"""
    td, code, price = s[1], s[2], s[4]
    series = px.get(code, [])
    dates = [d for d, _ in series]
    close_td = None
    if td in dates:
        i = dates.index(td)
        close_td = series[i][1]
    if price and close_td and abs(price / close_td - 1) <= 0.08:
        return price
    return close_td

# ---- 4. 按闸门聚合 ----
# verdict样本池
rej_s = [s for s in samples if s[5] == 'REJECT']
pass_s = [s for s in samples if s[5] in ('PASS_WITH_WARN', 'PASS')]
print("REJECT样本(去重): %d, 放行样本: %d" % (len(rej_s), len(pass_s)))

# 放行组整体基准
def grp_stats(group, label):
    r5, r10, r20 = [], [], []
    for s in group:
        _, a = fwd(s[2], s[1], 5)
        _, b = fwd(s[2], s[1], 10)
        _, c = fwd(s[2], s[1], 20)
        if a is not None: r5.append(a)
        if b is not None: r10.append(b)
        if c is not None: r20.append(c)
    def st(x): return "均值%+.2f%% 中位%+.2f%% 胜率%.0f%% n=%d" % (
        statistics.mean(x), statistics.median(x), 100*sum(1 for v in x if v > 0)/len(x), len(x)) if x else "n=0"
    print("  %s  fwd5[%s]  fwd10[%s]  fwd20[%s]" % (label, st(r5), st(r10), st(r20)))
    return r5, r10, r20

print("\n===== 放行组(对照组=实际执行的) =====")
grp_stats(pass_s, "全部放行")

print("\n===== 按闸门聚合(REJECT 触发闸门 -> 若强行买入的反事实收益) =====")
gate_hits = {}
for s in rej_s:
    for g in s[6]:
        gate_hits.setdefault(g, []).append(s)
results = {}
for g, ss in sorted(gate_hits.items(), key=lambda kv: -len(kv[1])):
    print("\n【%s】拦截%d笔(去重)" % (g, len(ss)))
    r5, r10, r20 = grp_stats(ss, "  拦截组")
    results[g] = (len(ss), r5, r10, r20)

# ---- 5. WARN 闸门信息量验证(留痕放行组, 按是否触发该WARN分组) ----
print("\n===== WARN闸门信息量(触发了该WARN但被放行的样本 vs 未触发放行样本) =====")
warn_gates = {}
for s in pass_s:
    for g in s[7]:
        warn_gates.setdefault(g, []).append(s)
for g, ss in sorted(warn_gates.items(), key=lambda kv: -len(kv[1])):
    if len(ss) < 5: continue
    print("\n【%s】WARN命中%d笔" % (g, len(ss)))
    grp_stats(ss, "  WARN组")

# ---- 6. 汇总判定表 ----
print("\n===== 汇总: 闸门反事实验证总表 =====")
print("闸门 | 拦截n | fwd5均 | fwd10均 | fwd20均 | 拦对判定(fwd<0=拦对)")
for g, (n, r5, r10, r20) in sorted(results.items(), key=lambda kv: -kv[1][0]):
    m5 = statistics.mean(r5) if r5 else None
    m10 = statistics.mean(r10) if r10 else None
    m20 = statistics.mean(r20) if r20 else None
    verdict = "拦对✅" if (m10 is not None and m10 < 0) else ("拦错❌" if m10 is not None else "窗口不足")
    print("%s | %d | %s | %s | %s | %s" % (g, n,
        ("%+.2f%%" % m5) if m5 is not None else "-",
        ("%+.2f%%" % m10) if m10 is not None else "-",
        ("%+.2f%%" % m20) if m20 is not None else "-", verdict))
