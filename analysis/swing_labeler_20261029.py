# -*- coding: utf-8 -*-
"""swing_labeler_20261029.py — 波段标注器 Phase1(主人波段样本框架立项·三段式m1供数)

按主人框架落地:
  ① Zig-Zag(申万参数): 转折阈值10%, 趋势波段确认=年化收益≥20% 且 时长≥63交易日,
     不满足者为震荡(osc)。上涨波段起点=底部转折点, 下跌波段起点=顶部转折点。
  ② Binseg断点修正(手写简化): 趋势波段内穷举单断点, 断点后段斜率绝对值<前段×0.5
     且SSE降幅>20% → 判趋势衰竭, 后半段重标 osc_revised。
  ③ HMM 4状态(GaussianHMM): 观测=宇宙等权日对数收益率+20日年化波动率,
     近10年拟合(2016-10起), 全历史解码; 按状态收益均值排序映射 涨趋势/跌趋势,
     其余两态按波动率映射 宽幅/窄幅震荡。全市场共享。
产物: swing_labels.db
  swing_segments(code, seg_type, start_date, end_date, days, ret, ann_ret, revised)
  mkt_state(date, state, label)
  meta(key, value)
"""
import os, sys, json, sqlite3, time
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT = os.path.join(ROOT, "data", "swing_labels.db")
D0, THR, MIN_ANN, MIN_DAYS = 20150101, 0.10, 0.20, 63

t0 = time.time()
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=? order by date", (D0,))})
dmin, dmax = dates[0], dates[-1]
rows = con.execute(
    "select code,date,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, c, v in rows:
    s = series.setdefault(code, {"d": [], "c": [], "v": []})
    s["d"].append(int(d)); s["c"].append(float(c)); s["v"].append(float(v or 0))
print(f"数据: {dmin}~{dmax} {len(dates)}日 {len(series)}只 ({time.time()-t0:.0f}s)", flush=True)

date_idx = {d: i for i, d in enumerate(dates)}


def zigzag_pivots(cl, thr=THR):
    """确认式zigzag: 反向超过thr确认前pivot, 返回pivot索引序列(交替高低点)"""
    n = len(cl)
    if n < 2:
        return [0, n-1]
    pivs = [0]; trend = 0; cur = 0
    for i in range(1, n):
        r = cl[i]/cl[cur] - 1
        if trend >= 0 and r <= -thr:
            pivs.append(cur); trend = -1; cur = i
        elif trend <= 0 and r >= thr:
            pivs.append(cur); trend = 1; cur = i
        else:
            if (trend == 0 and (cl[i] > cl[cur])) or (trend >= 0 and cl[i] > cl[cur]) or (trend <= 0 and cl[i] < cl[cur]):
                cur = i
    pivs.append(n-1)  # 未确认尾段
    return pivs


def slope_sse(y):
    """线性拟合: 返回斜率与残差平方和"""
    n = len(y)
    if n < 3:
        return 0.0, float(np.sum((y - np.mean(y))**2)) if n else 0.0
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = ((x-xm)**2).sum()
    if sxx <= 0:
        return 0.0, float(np.sum((y-ym)**2))
    b = ((x-xm)*(y-ym)).sum()/sxx
    return b, float(np.sum((y - ym - b*(x-xm))**2))


def binseg_refine(cl, s, e):
    """趋势波段[s,e]内穷举单断点: 后段|斜率|<前段×0.5 且 SSE降幅>20% → 返回断点(衰竭)"""
    seg = cl[s:e+1]
    L = len(seg)
    if L < 40:
        return None
    _, sse_full = slope_sse(seg)
    best = None; best_score = 0.0
    for k in range(15, L-15):
        b1, s1 = slope_sse(seg[:k])
        b2, s2 = slope_sse(seg[k:])
        drop = 1 - (s1+s2)/max(sse_full, 1e-12)
        if drop > best_score:
            best_score = drop; best = k
    if best is None or best_score <= 0.20:
        return None
    b1, _ = slope_sse(seg[:best]); b2, _ = slope_sse(seg[best:])
    if abs(b2) < abs(b1)*0.5 and np.sign(b1) == np.sign(b2):
        return s + best
    return None


def ann_ret(r, days):
    if days <= 0 or r <= -1:
        return 0.0
    return (1+r)**(250.0/days) - 1


# ── 全宇宙 zigzag + binseg ──
segs_out = []
for ci, (code, s) in enumerate(series.items(), 1):
    if len(s["c"]) < 130:
        continue
    if code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    cl = np.array(s["c"])
    pivs = zigzag_pivots(cl)
    for j in range(len(pivs)-1):
        a, b = pivs[j], pivs[j+1]
        days = b - a
        r = cl[b]/cl[a] - 1
        ar = ann_ret(r, days)
        if r > 0 and ar >= MIN_ANN and days >= MIN_DAYS:
            typ = "up"
        elif r < 0 and ar <= -MIN_ANN and days >= MIN_DAYS:
            typ = "dn"
        else:
            typ = "osc"
        rev = 0
        if typ in ("up", "dn"):
            bp = binseg_refine(cl, a, b)
            if bp is not None:
                rev = 1
                # 主段收缩到断点, 后半段为修正震荡
                segs_out.append((code, typ, int(s["d"][a]), int(s["d"][bp]), bp-a, round(float(cl[bp]/cl[a]-1), 4), round(ann_ret(cl[bp]/cl[a]-1, bp-a), 3), 0))
                segs_out.append((code, "osc", int(s["d"][bp]), int(s["d"][b]), b-bp, round(float(cl[b]/cl[bp]-1), 4), round(ann_ret(cl[b]/cl[bp]-1, b-bp), 3), 1))
                continue
        segs_out.append((code, typ, int(s["d"][a]), int(s["d"][b]), days, round(float(r), 4), round(float(ar), 3), rev))
    if ci % 500 == 0:
        print(f"...zigzag {ci}/{len(series)}", flush=True)
print(f"zigzag+binseg完成: {len(segs_out)}段 ({time.time()-t0:.0f}s)", flush=True)

# ── HMM 市场状态(宇宙等权指数) ──
from hmmlearn.hmm import GaussianHMM
n = len(dates)
eq = np.zeros(n); cnt = np.zeros(n)
for code, s in series.items():
    if len(s["c"]) < 130 or code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    g = np.array([date_idx[d] for d in s["d"]])
    eq[g] += np.array(s["c"]); cnt[g] += 1
eq = np.where(cnt > 0, eq/np.maximum(cnt, 1), np.nan)
# 缺口前向填充
for i in range(1, n):
    if np.isnan(eq[i]):
        eq[i] = eq[i-1]
lr = np.diff(np.log(eq))
vol20 = np.array([np.std(lr[max(0, i-19):i+1])*np.sqrt(250)*100 for i in range(len(lr))])
X = np.column_stack([lr*100, vol20])[20:]  # 跳过预热期
fit_start = np.searchsorted(dates, 20161001) - 20
X_fit = X[max(fit_start, 0):]
hmm = GaussianHMM(n_components=4, covariance_type="full", n_iter=300, random_state=42)
hmm.fit(X_fit)
states = hmm.predict(X)
# 映射: 按状态收益均值排序 → 最高=涨趋势 最低=跌趋势; 其余按波动率 高=宽幅 低=窄幅
mean_ret = {k: X[states == k, 0].mean() for k in range(4)}
order = sorted(range(4), key=lambda k: -mean_ret[k])
up_s, dn_s = order[0], order[-1]
mid = order[1:3]
mean_vol = {k: X[states == k, 1].mean() for k in mid}
wide_s = max(mid, key=lambda k: mean_vol[k]); narrow_s = min(mid, key=lambda k: mean_vol[k])
lab = {up_s: "上涨趋势", dn_s: "下跌趋势", wide_s: "宽幅震荡", narrow_s: "窄幅震荡"}
mkt_rows = [(int(dates[i+20]), int(st), lab[st]) for i, st in enumerate(states)]
state_day = {lab[k]: int((states == k).sum()) for k in range(4)}
print(f"HMM状态分布: {state_day}", flush=True)

# ── 落库 ──
if os.path.exists(OUT):
    os.remove(OUT)
db = sqlite3.connect(OUT)
db.executescript("""
create table swing_segments(code text, seg_type text, start_date int, end_date int,
    days int, ret real, ann_ret real, revised int);
create index ix_seg_code on swing_segments(code, start_date);
create index ix_seg_type on swing_segments(seg_type);
create table mkt_state(date int primary key, state int, label text);
create table meta(key text primary key, value text);
""")
db.executemany("insert into swing_segments values(?,?,?,?,?,?,?,?)", segs_out)
db.executemany("insert into mkt_state values(?,?,?)", mkt_rows)
db.executemany("insert into meta values(?,?)", [
    ("params", json.dumps({"zigzag_thr": THR, "min_ann": MIN_ANN, "min_days": MIN_DAYS,
                            "binseg_sse_drop": 0.20, "binseg_slope_ratio": 0.5,
                            "hmm_components": 4, "hmm_fit_start": 20161001, "data_start": dmin})),
    ("state_map", json.dumps(lab)),
    ("built_at", time.strftime("%Y-%m-%d %H:%M")),
])
db.commit(); db.close()

# ── 统计快照 ──
from collections import Counter
tc = Counter(x[1] for x in segs_out)
ups = [x for x in segs_out if x[1] == "up" and not x[7]]
dns = [x for x in segs_out if x[1] == "dn" and not x[7]]
print(f"\n=== Phase1 标注快照 ===")
print(f"总段数: {len(segs_out)} | 类型分布: {dict(tc)}")
print(f"涨波段(未修正): {len(ups)}个 | 跌波段: {len(dns)}个 | 修正震荡段: {tc.get('osc',0)}(其中revised={sum(1 for x in segs_out if x[7])})")
if ups:
    ud = np.array([x[4] for x in ups]); ur = np.array([x[5] for x in ups])
    print(f"涨波段: 时长中位{np.median(ud):.0f}日 涨幅中位{np.median(ur)*100:.0f}%")
if dns:
    dd = np.array([x[4] for x in dns]); dr = np.array([x[5] for x in dns])
    print(f"跌波段: 时长中位{np.median(dd):.0f}日 跌幅中位{np.median(dr)*100:.0f}%")
print(f"HMM: {lab} | 分布{state_day}")
print(f"落库: {OUT}")
