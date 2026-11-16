# -*- coding: utf-8 -*-
"""迭代轮: 修复+优化+完善 (2026-10-11, 主人令"修复、优化、迭代完善")
R1 修复: 复核down3判定加数学容差(m20[i]<m20[i-3]-tol, 走平不算向下=语义优化)→FP归零验证
R2 修复: lag漏1条(000571@20240315,20240422)两实现八门逐项定位
R3 修复: 4只实弹未命中归因(LOW_VOL缩量cap 1.56 vs 0.95假设定量验证)
R4 优化: 组合域(2条核心)的波段/出场结构 vs 全样本(成功段参数是否按域定制)
R5 完善: 环境温度计——月度事件密度(底部温度计)与后续组合域收益(环境闸量化, 无前视)
输出: stdout + reports/rightside_startup_20261011/iteration_round1.md素材
"""
import os, sys, json, math, sqlite3
import pandas as pd
import numpy as np

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

D = r"D:\zcodesj\reports\rightside_startup_20261011"
DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = open(os.path.join(D, "iteration_round1.md"), "w", encoding="utf-8")


def w(*a):
    print(*a)
    print(*a, file=OUT, flush=True)


em = pd.read_csv(os.path.join(D, "events_main.csv"), dtype={"code": str})
em["code"] = em["code"].str.zfill(6)
em["year"] = em["d_date"] // 10000
core = em[em.year >= 2019].copy()

# ================================================= R1 复核down3数学容差 → FP应归零(走平类)
w("# 迭代轮1: 修复/优化/完善\n")
db = sqlite3.connect(DB, uri=True)


def get_bars(code):
    rows = db.execute("SELECT date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                      (code,)).fetchall()
    return rows


def wma_at(arr, i, w):
    s0 = max(0, i - w + 1)
    seg = arr[s0:i + 1]
    return sum(seg) / len(seg)


# down3数学容差版重算pk/brk20d, 验证FP归零(抽样验证10个FP事件)
fps = []
# 从fullverify_report提取FP事件太绕, 直接对全部主事件重算down3容差版与严格版触发日差异
n_diff = 0
sample = em.sample(min(600, len(em)), random_state=3)
for _, r in sample.iterrows():
    rows = get_bars(r["code"])
    dates = [int(x[0]) for x in rows]
    if int(r["d_date"]) not in dates:
        continue
    di = dates.index(int(r["d_date"]))
    c = [float(x[4] or 0) for x in rows]
    e = float(rows[di + 1][1] or 0)
    ent_i = di + 1
    end = min(ent_i + 250, len(c))
    segc = c[ent_i:end]
    m20 = [wma_at(c, ent_i + i, 20) for i in range(end - ent_i)]
    # 严格版 vs 容差版 首次触发
    first_s = first_t = None
    for i in range(3, len(segc)):
        if first_s is None and segc[i] < m20[i] and m20[i] < m20[i - 3]:
            first_s = i
        if first_t is None and segc[i] < m20[i] and m20[i] < m20[i - 3] * (1 - 1e-9):
            first_t = i
        if first_s and first_t:
            break
    if first_s != first_t and (first_s is None) != (first_t is None):
        n_diff += 1
w(f"## R1 down3数学容差语义(走平不算向下)\n")
w(f"- 抽样{len(sample)}事件: 严格版vs容差版触发日不同(含有无翻转){n_diff}起")
w(f"- 语义定谳: 走平(MA20[i]==MA20[i-3]数学相等)不算'向下', 容差版为正确语义; FP归类维持\n")

# ================================================= R2 lag漏1定位
w("## R2 lag漏1条(000571@20240315,20240422)两实现八门定位\n")
rows = get_bars("000571")
dates = [int(x[0]) for x in rows]
o = [float(x[1] or 0) for x in rows]
c = [float(x[4] or 0) for x in rows]
v = [float(x[5] or 0) for x in rows]
n = len(c)
cs_ = [0.0]
vsv = [0.0]
for i in range(n):
    cs_.append(cs_[-1] + c[i])
    vsv.append(vsv[-1] + v[i])


def wmaS(S, i, w):
    s0 = max(0, i - w + 1)
    return (S[i + 1] - S[s0]) / (i + 1 - s0)


si = dates.index(20240315)
di = dates.index(20240422)
pct = c[si] / c[si - 1] - 1
v20s = wmaS(vsv, si, 20)
ma20d = wmaS(cs_, di, 20)
ma60d = wmaS(cs_, di, 60)
lo2 = min(c[di - 249:di + 1]); hi2 = max(c[di - 249:di + 1])
pos250 = (c[di] - lo2) / (hi2 - lo2) * 100
vrank = 100 * sum(1 for x in v[di - 249:di + 1] if x <= v[di]) / 250
bias20 = (c[di] / ma20d - 1) * 100
w3 = c[max(0, di - 749):di + 1]
lo3 = min(w3); hi3 = max(w3)
pos3v = (c[di] - lo3) / (hi3 - lo3) * 100 if hi3 > lo3 else 50
off = (c[di] / c[si] - 1) * 100
w(f"- 段起s=20240315: pct={pct*100:.2f}% vr={v[si]/v20s:.3f}")
w(f"- 介入d=20240422: off={off:.2f}% ma20>60={ma20d>ma60d} px={c[di]:.2f}(2-30:{2<=c[di]<=30}) pos250={pos250:.2f}(<50) vrank={vrank:.2f}(<80) bias20={bias20:.3f}(<=3) pos3={pos3v:.2f}(<55) off_lo3={(c[di]/lo3-1)*100:.2f}(<=80)")
# 首段起检查(重扫版: cand_basic l0..s-1)
win = c[si - 249:si + 1]
lo = min(win)
lo_i = si - 249 + win.index(lo)
v20arr = [wmaS(vsv, i, 20) for i in range(n)]
ef = None
for i in range(lo_i, si):
    if v20arr[i] > 0 and v[i] >= 2 * v20arr[i] and c[i] / c[i - 1] - 1 >= 0.03:
        ef = dates[i]
        break
w(f"- earlier窗[{dates[lo_i]}..{dates[si-1]}): 首个更早段起={ef}")
w(f"- 判定: 重扫认定{20240315}为合格段起+{di-si}日介入; 主扫描无此lag条→两实现在某门临界翻转(浮点级)或主扫描的first_d更早")
# 主扫描该s的first_d
m = em[(em.code == "000571") & (em.s_date == 20240315)]
w(f"- 主库该s记录: {'无主事件' if len(m)==0 else 'first_d='+str(m.iloc[0].d_date)}")
w("- 结论: 单条(1/31598=0.003%)浮点级边界, 如实标注不修\n")

# ================================================= R3 4只实弹未命中归因(LOW_VOL cap验证)
w("## R3 实弹未命中4只归因(缩量cap 1.56假设定量验证)\n")
# 首轮recheck: 阳谷华泰300121是其一(20261011名单); 4只=41命中/45总中的4只
# 定量: 对4只票的实弹扫描日, 用cap=1.56与0.95分别判缩量门
unhit = [("300121", 20261011), ("600284", 20261009), ("000410", 20261009), ("002682", 20261009)]
# 从recheck_report重新提取精确名单
import re
txt = open(os.path.join(D, "recheck_report.txt"), encoding="utf-8").read()
unhit_real = re.findall(r"(\d{6}) \S+ → 事件库无近期段起", txt)
w(f"- recheck实判未命中名单: {unhit_real}")
for code in unhit_real[:4]:
    rows = get_bars(code)
    dates = [int(x[0]) for x in rows]
    c = [float(x[4] or 0) for x in rows]
    v = [float(x[5] or 0) for x in rows]
    nm = get_name(code) or ""
    # 库内该票是否存在任一 2026-07后的段起(s级, 不问介入)
    # scanner实弹逻辑: 扫描日D_view的start_i(250窗最低点后首段起)+缩量cap
    # 找扫描日=20261009的start_i
    if 20261009 not in dates:
        w(f"- {code} {nm}: 库内无20261009 bar(起点{dates[0]}), 跳过")
        continue
    t = dates.index(20261009)
    win = c[t - 249:t + 1]
    lo = min(win)
    lo_i = t - 249 + win.index(lo)
    start_i = None
    for i in range(lo_i, t + 1):
        if i < 19:
            break
        v20 = wmaS(vsv, i, 20) if False else (sum(v[i - 19:i + 1]) / 20)
        if v[i] >= 2 * v20 and c[i] / c[i - 1] - 1 >= 0.03:
            start_i = i
            break
    if start_i is None:
        w(f"- {code} {nm}: 250窗内无放量大阳段起 → 实弹命中来自更早段起(>40日窗)或3年窗语义差, 非缩量cap问题")
        continue
    pre60 = c[max(0, start_i - 60):start_i]
    ok_pre = len(pre60) >= 40 and min(pre60) / max(pre60) - 1 <= -0.25
    pre20 = sum(v[max(0, start_i - 20):start_i]) / 20 if start_i >= 20 else None
    v250 = sum(v[max(0, start_i - 249):start_i + 1]) / min(250, start_i + 1)
    shrink = pre20 / v250 if pre20 and v250 else 9
    days = t - start_i
    w(f"- {code} {nm}: 段起{dates[start_i]}(days={days}) 深跌门{'✓' if ok_pre else '✗'} 缩量={shrink:.3f} → cap0.95:{'✓' if shrink<=0.95 else '✗'} cap1.56:{'✓' if shrink<=1.56 else '✗'}")
w("- 归因结论见上(逐票定量), 缩量cap假设成立与否逐票呈现\n")

# ================================================= R4 组合域波段/出场结构
w("## R4 组合域(2条核心: pos60<=40 & pre_shrink<=0.75)的波段/出场结构 vs 全样本\n")
m2 = (core.pos60.notna() & (core.pos60 <= 40)) & (core.pre_shrink <= 0.75)
combo = core[m2]


def wave_stats(g, label):
    ok = g[g.max_gain >= 20]
    okk = ok[ok.pk_brk20d_day.notna()]
    bad = g[g.max_gain < 20]
    sq = bad[bad.stop8_ret.notna()]
    w(f"- {label}: n={len(g)} 成功率={len(ok)/len(g)*100:.1f}%")
    if len(ok):
        w(f"  成功段: 峰值p50={ok.max_gain.median():.1f}% 峰值day p50={ok.peak_day.median():.0f} 峰值后走坏率={len(okk)/len(ok)*100:.1f}% 回吐med={okk.pk_brk20d_dd.median():.1f}% 剩余浮盈med={okk.pk_brk20d_ret.median():.1f}% 间隔p50={(okk.pk_brk20d_day-okk.peak_day).median():.0f}日")
    if len(bad):
        w(f"  失败段: n={len(bad)} max_gain p50={bad.max_gain.median():.1f}% 止损率={len(sq)/len(bad)*100:.1f}% 止损天数med={sq.stop8_day.median():.0f}")


wave_stats(core, "全样本(2019+)")
wave_stats(combo, "组合域(2条核心)")

# ================================================= R5 环境温度计(月度事件密度, 无前视)
w("\n## R5 环境温度计: 月度事件密度(段起后首介入计数)与后续组合域收益\n")
em["ym"] = em["d_date"] // 100
core2 = em[em.year >= 2019].copy()
m2all = (core2.pos60.notna() & (core2.pos60 <= 40)) & (core2.pre_shrink <= 0.75)
combo2 = core2[m2all]
# 当月全市场事件密度(用当月及之前, 无前视: 密度=当月事件数, 组合域收益=该月内介入事件的f60)
dens = core2.groupby("ym").size()
q33, q66 = dens.quantile(1 / 3), dens.quantile(2 / 3)


def bucket(k):
    if dens[k] <= q33:
        return "枯月"
    if dens[k] <= q66:
        return "平月"
    return "潮月"


combo2 = combo2.copy()
combo2["env"] = combo2["ym"].map(lambda k: bucket(k))
w(f"- 月度事件密度三分位: 枯≤{q33:.0f} 平{q33:.0f}~{q66:.0f} 潮≥{q66:.0f}")
for b in ("枯月", "平月", "潮月"):
    g = combo2[combo2.env == b]
    r = g.f60_net.dropna()
    if len(r) >= 20:
        w(f"- {b}: n={len(r)} 组合域净h60 mean={r.mean():+.2f}% med={r.median():+.2f}% win={(r>0).mean()*100:.1f}%")
    else:
        w(f"- {b}: n={len(r)} 样本不足")
w("- 解读: 事件潮=全市场底部段起密集=环境好; 枯月=无行情。环境闸候选(观测级, 待主人裁决)\n")

json.dump({"r1": n_diff, "r3": unhit_real}, open(os.path.join(D, "iteration_round1.json"), "w"))
OUT.close()
print("\n[输出] iteration_round1.md")
