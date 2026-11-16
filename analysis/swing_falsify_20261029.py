# -*- coding: utf-8 -*-
"""swing_falsify_20261029.py — Phase3 第二批证伪探针(主人框架第三份·六个新命题)

  P6 超跌反弹: 20日累计跌≥15% 的后验(框架: 唯一显著正形态 +5.40pp) + HMM环境分层
  P7 涨停后验与开盘溢价分层(框架: 全样本-0.33%/44.8% 9年全负; 低开(-3%~0%)子集+2.8%/56%)
     换手率分层缺流通股本, 如实标注暂缓
  P8 周线vs日线突破假信号率(框架: 日线>60% vs 周线<20%)
  P9 地量裸买 vs 地量+低位叠加(框架: 裸买48% vs +低估值77%)
  P10 环境开关: 前20日全市场等权收益弱/强势 × 放量新高追入(框架: -4.79%/37.2% vs 0.00%/49.8%)
  P11 缩量回踩20日线: 裸缩量回踩 vs 突破确认后缩量回踩(框架: 裸缩量回踩负边际,
      回踩增量来自突破确认前置而非缩量本身)
  P12 布林极致收敛→向上突破转化率(框架: 56%)
所有口径 next-bar: fwd20 = c[t+21]/c[t+1]-1(特别注明除外)。
"""
import os, sys, json, sqlite3, time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SW = os.path.join(ROOT, "data", "swing_labels.db")
t0 = time.time()

sw = sqlite3.connect(f"file:{SW}?mode=ro", uri=True)
mkt = {int(r[0]): r[2] for r in sw.execute("select date,state,label from mkt_state")}
sw.close()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20150101 order by date")})
dmin = dates_all[0]
gidx = {d: i for i, d in enumerate(dates_all)}
rows = con.execute(
    "select code,date,open,high,low,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v in rows:
    d = int(d)
    s = series.setdefault(code, {"g": [], "o": [], "h": [], "l": [], "c": [], "v": []})
    s["g"].append(gidx[d]); s["o"].append(float(o)); s["h"].append(float(h))
    s["l"].append(float(l)); s["c"].append(float(c)); s["v"].append(float(v or 0))
for s in series.values():
    for k in ("o","h","l","c","v","g"):
        s[k] = np.array(s[k])
N = len(dates_all)
print(f"数据: {N}日 {len(series)}只 ({time.time()-t0:.0f}s)", flush=True)

# 全市场等权收益序列(P10环境划分)
eq = np.zeros(N); cnt = np.zeros(N)
for code, s in series.items():
    if len(s["c"]) < 130 or code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    eq[s["g"]] += s["c"]; cnt[s["g"]] += 1
eq = eq/np.maximum(cnt, 1)
for i in range(1, N):
    if np.isnan(eq[i]): eq[i] = eq[i-1]
mkt_r20 = np.full(N, np.nan)
mkt_r20[20:] = eq[20:]/eq[:-20] - 1
env_of_day = np.where(mkt_r20 < -0.03, "weak", np.where(mkt_r20 >= 0.03, "strong", "mid"))


def roll(a, w, fn):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = fn(sliding_window_view(a, w), axis=1)
    return out


def fwd_stats(env_pairs):
    """[(环境标签, fwd20)] → 按环境分层统计"""
    from collections import defaultdict
    by = defaultdict(list)
    for e, f in env_pairs:
        by[e].append(f)
    out = {}
    for e, arr in by.items():
        a = np.array(arr)
        out[e] = {"n": len(a), "mean": round(float(a.mean())*100, 2),
                  "win": round(float((a > 0).mean())*100, 1),
                  "med": round(float(np.median(a))*100, 2)}
    return out


R = {}  # 结果集

# ── P6 超跌反弹 + HMM分层 ──
p6 = []
# ── P7 涨停 + 次日开盘溢价分层 ──
p7_all, p7_gap = [], []
# ── P8 周线突破 ──
wk_fail = wk_ok = 0
wk_f20_ok, wk_f20_fail = [], []
# ── P9 地量(裸 vs +低位) ──
p9_dry, p9_dry_lowpos = [], []
# ── P10 环境×放量新高 ──
p10 = []
# ── P11 缩量回踩20日线(裸 vs 突破后) ──
p11_bare, p11_after = [], []
# ── P12 布林收敛 ──
conv_n = conv_up = 0

for ci, (code, s) in enumerate(series.items(), 1):
    c, v, o, g = s["c"], s["v"], s["o"], s["g"]
    n = len(c)
    if n < 130:
        continue
    r20 = np.full(n, np.nan); r20[20:] = c[20:]/c[:-20] - 1
    hi60c = np.full(n, np.nan); hi60c[1:] = roll(c[:-1], 60, np.max)
    v20 = roll(v, 20, np.mean); v60 = roll(v, 60, np.mean); v5 = roll(v, 5, np.mean)
    ma20 = roll(c, 20, np.mean)
    mid = ma20; sd20 = roll(c, 20, np.std)
    bupper = mid + 2*sd20; blower = mid - 2*sd20
    bw = (bupper - blower)/np.maximum(mid, 1e-9)
    lo250 = roll(c, 250, np.min); hi250 = roll(c, 250, np.max)
    with np.errstate(invalid="ignore"):
        pos250 = (c - lo250)/np.maximum(hi250 - lo250, 1e-9)*100
        oversold = r20 <= -0.15
        zt = np.zeros(n, bool); zt[1:] = (c[1:]/c[:-1] - 1) >= 0.097
        newhigh = (c > hi60c*1.005) & (v >= 1.5*v20)
        dry = (v5/np.maximum(v60, 1e-9)) < 0.4
        # 回踩20日线: 近20日内有过newhigh, 今日|c/ma20-1|<=2%且c>=ma20*0.98
        cs = np.cumsum(newhigh.astype(int))
        recent_nh = np.zeros(n, bool)
        recent_nh[20:] = (cs[20:] - cs[:-20]) > 0
        touch20 = (~np.isnan(ma20)) & (np.abs(c/ma20 - 1) <= 0.02) & (c >= ma20*0.98)
        shrink = v < 0.8*v20
        pb_bare = touch20 & shrink
        pb_after = touch20 & shrink & recent_nh
        # 布林收敛: 当前bw为过去250日10%分位以下
        conv = np.zeros(n, bool)
        for i in range(260, n):
            hist = bw[i-250:i]
            if bw[i] <= np.nanpercentile(hist, 10):
                conv[i] = True
    def F(i):
        if i + 21 >= n:
            return None
        return c[i+21]/c[i+1] - 1
    for i in np.where(oversold)[0]:
        f = F(i)
        if f is not None:
            lb = mkt.get(dates_all[g[i]], "")
            p6.append((lb or "NA", f))
    for i in np.where(zt)[0]:
        f = F(i)
        if f is None:
            continue
        p7_all.append(f)
        if i + 1 < n and o[i+1] > 0:
            gap = o[i+1]/c[i] - 1
            bucket = ("low_open" if -0.03 <= gap < 0 else
                      "high_open" if gap >= 0.05 else "mid_open")
            p7_gap.append((bucket, f))
    for i in np.where(newhigh)[0]:
        f = F(i)
        if f is None:
            continue
        p10.append((env_of_day[g[i]], f))
        # P8 日线假突破: 5日内收盘跌回突破日收盘下方
        seg = c[i+1:min(i+6, n)]
        if len(seg) == 5:
            fail = bool((seg < c[i]).any())
            if fail:
                wk_fail += 1
            else:
                wk_ok += 1
    for i in np.where(dry)[0]:
        f = F(i)
        if f is None:
            continue
        p9_dry.append(f)
        if not np.isnan(pos250[i]) and pos250[i] <= 30:
            p9_dry_lowpos.append(f)
    for i in np.where(pb_bare)[0]:
        f = F(i)
        if f is not None:
            p11_bare.append(f)
    for i in np.where(pb_after)[0]:
        f = F(i)
        if f is not None:
            p11_after.append(f)
    for i in np.where(conv)[0]:
        conv_n += 1
        up_break = bool((c[i+1:min(i+6, n)] > bupper[i]).any()) if i + 6 <= n else False
        if up_break:
            conv_up += 1
    if ci % 1000 == 0:
        print(f"...{ci}/{len(series)}", flush=True)

print(f"事件扫描完成 ({time.time()-t0:.0f}s)", flush=True)

def st(a):
    a = np.array(a)
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2)} if len(a) else {"n": 0}

# P8 周线口径: 每5交易日聚合收盘, 周收盘>前60周最高×1.005, 突破后5日跌回=失败
wk_up_rate = wk_ok/max(wk_ok+wk_fail, 1)*100
R["P6_oversold_by_hmm"] = fwd_stats(p6)
R["P6_oversold_all"] = st([f for _, f in p6])
R["P7_zt_all"] = st(p7_all)
R["P7_zt_by_open_gap"] = fwd_stats(p7_gap)
R["P8_break_fail"] = {"daily_5d_fail_pct": round(wk_fail/max(wk_fail+wk_ok,1)*100, 1),
                      "daily_5d_ok_n": wk_ok, "fail_n": wk_fail,
                      "note": "周线口径待v2, 本轮为日线5日存活率"}
R["P9_dry"] = st(p9_dry)
R["P9_dry_lowpos"] = st(p9_dry_lowpos)
R["P10_newhigh_by_env"] = fwd_stats(p10)
R["P11_pullback20_bare"] = st(p11_bare)
R["P11_pullback20_after_break"] = st(p11_after)
R["P12_boll_conv"] = {"n_conv": conv_n, "n_up_break": conv_up,
                      "up_rate_pct": round(conv_up/max(conv_n,1)*100, 1)}

out = os.path.join(ROOT, "data", "swing_falsify_20261029.json")
json.dump({"built_at": time.strftime("%Y-%m-%d %H:%M"), **R},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n=== P6 超跌反弹(fwd20) 按HMM状态 ===")
print("全体:", R["P6_oversold_all"])
for k, v in R["P6_oversold_by_hmm"].items():
    print(f"  {k}: {v}")
print("P7 涨停全体:", R["P7_zt_all"])
for k, v in R["P7_zt_by_open_gap"].items():
    print(f"  开盘{k}: {v}")
print("P8:", R["P8_break_fail"])
print("P9 地量裸:", R["P9_dry"], "| +低位(pos250<=30):", R["P9_dry_lowpos"])
print("P10 放量新高×环境:", {k: f"{v['mean']}%/{v['win']}%" for k, v in R["P10_newhigh_by_env"].items()})
print("P11 缩量回踩20日线 裸:", R["P11_pullback20_bare"])
print("P11 缩量回踩20日线 突破确认后:", R["P11_pullback20_after_break"])
print("P12 布林收敛→向上突破:", R["P12_boll_conv"])
print(f"落盘: {out} ({time.time()-t0:.0f}s)")
