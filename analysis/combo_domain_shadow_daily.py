# -*- coding: utf-8 -*-
"""组合优势域每日影子 (PREREG_COMBODOMAIN_SHADOW_20261011, 主人令立项 2026-10-11)
语义: 每日对全市场跑当日扫描(scanner式当日命中, 780窗), 命中=当日t满足:
  段起锚(250窗最低点后首段起, ties取最近=scanner原版倒序list.index语义) + 深跌/缩量门
  + days=t-start_i∈[0,40] + off_start窗 + 介入八门(多头/pos250<50/价格2-30/pos3<55/距低<=80/量位<80/乖离<=3)
  + 组合过滤(段起pre_shrink<=0.75 且 当日pos60<=40)
→ UPSERT台账按(code,s_date)保留最早d_date(首个D语义), 幂等; 到期事件回填f5/f20/f60(净口径)。
参数单一真源 = data/PREREG_COMBODOMAIN_SHADOW_20261011.json params, 缺字段 fail-closed 退出。
新鲜度闸: qfq库尾 != 运行日 → skip(实弹scanner同款)。
验收(60交易日): G1 净h60均值>=+3.0%且win>=48% / G2 无过滤对照差>=2pp / G3 n>=30; kill=前20个均值<0。
零交易权: 只产出台账与快照。
"""
import os, sys, json, sqlite3, time
import numpy as np

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PREREG = r"D:\zcodesj\data\PREREG_COMBODOMAIN_SHADOW_20261011.json"
LEDGER = r"D:\zcodesj\data\combo_domain_shadow_ledger.jsonl"
STATE = r"D:\zcodesj\data\combo_domain_shadow_state.json"
DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(zc副本停更致夜链aux恒skip, SOP一百三十二)
EXCLUDE_PREFIX = ("688", "689", "8", "4", "9")


def log(*a):
    print(*a, flush=True)


P = json.load(open(PREREG, encoding="utf-8")).get("params")
REQ = ["pos60_max", "pre_shrink_max", "price_min", "price_max", "off_start_lo", "off_start_hi",
       "pos3_max", "off_lo3_max", "vrank_max", "bias20_max", "pre_dd_max", "shrink_cap",
       "days_max", "fee_net"]
if P is None or any(k not in P for k in REQ):
    log(f"[combo] 🔴 fail-closed: prereg params 缺字段")
    sys.exit(2)

today = int(time.strftime("%Y%m%d"))
FORCE_DATE = None
DRY_RUN = "--dry-run" in sys.argv
if len(sys.argv) > 2 and sys.argv[1] == "--date":
    FORCE_DATE = int(sys.argv[2])  # 测试用: 显式历史日, 跳过新鲜度闸
db = sqlite3.connect(DB, uri=True)
qfq_max = int(db.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
if FORCE_DATE:
    T = FORCE_DATE
    if T > qfq_max:
        log(f"[combo] 🔴 --date{T} 超过库尾{qfq_max}")
        sys.exit(2)
    log(f"[combo] 🧪 测试模式 T={T} (跳过新鲜度闸)")
elif qfq_max != today:
    log(f"[combo] 🟡 qfq库尾{qfq_max}≠运行日{today} → skip(夜链前运行同款闸)")
    sys.exit(0)
else:
    T = qfq_max

codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq ORDER BY code")]
from stock_name_lookup import get_name
uni_set, uni_name = set(), {}
for cd in codes:
    if cd[:3] in EXCLUDE_PREFIX or cd[:1] in ("4", "8", "9"):
        continue
    nm = get_name(cd) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    uni_set.add(cd)
    uni_name[cd] = nm

_cache = {}


def get_bars(code):
    if code not in _cache:
        _cache[code] = db.execute(
            "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            (code,)).fetchall()
    return _cache[code]


# ---- 台账载入(UPSERT基线) ----
ledger = {}
if os.path.exists(LEDGER):
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = (r["code"], int(r["s_date"]))
            if k not in ledger or int(r["d_date"]) < int(ledger[k]["d_date"]):
                ledger[k] = r

hits = []
t0 = time.time()
cur = db.execute("SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq ORDER BY code,date")
cur_code, buf = None, []


def flush(code, nm, buf):
    n = len(buf)
    if n < 400:
        return
    dt = np.array([int(r[0]) for r in buf])
    o = np.array([float(r[1] or 0) for r in buf])
    c = np.array([float(r[4] or 0) for r in buf])
    v = np.array([float(r[5] or 0) for r in buf])
    if (c <= 0).any():
        first = int(np.argmax(c > 0))
        if not (c > 0)[-1]:
            return
        dt, o, c, v = (x[first:] for x in (dt, o, c, v))
        n = len(c)
        if n < 400:
            return
    t = n - 1
    while dt[t] > T:
        t -= 1
    if dt[t] != T or t < 249:
        return
    pct = np.zeros(n)
    pct[1:] = c[1:] / c[:-1] - 1.0
    cs = np.concatenate([[0.0], np.cumsum(c)])
    vs = np.concatenate([[0.0], np.cumsum(v)])

    def sma(arr, w, pad):
        d = (arr[w:] - arr[:-w]) / w
        return np.concatenate([np.full(pad, np.nan), d])

    ma20, ma60 = sma(cs, 20, 19), sma(cs, 60, 59)
    v20 = sma(vs, 20, 19)
    v250 = sma(vs, 250, 249)
    W = np.lib.stride_tricks.sliding_window_view(c, 250)
    lo250 = np.concatenate([np.full(249, np.nan), W.min(1)])
    hi250 = np.concatenate([np.full(249, np.nan), W.max(1)])
    pos250 = 100.0 * (c - lo250) / np.where(hi250 - lo250 > 0, hi250 - lo250, np.nan)
    lo_rel_rev = W[:, ::-1].argmin(1)
    lo_day = np.concatenate([np.full(249, -1), np.arange(249, n) - lo_rel_rev])
    W750 = np.lib.stride_tricks.sliding_window_view(c, 750) if n >= 750 else None
    fa, fb = np.maximum.accumulate(c), np.minimum.accumulate(c)
    if W750 is not None:
        hi3 = np.concatenate([np.full(749, np.nan), W750.max(1)])
        lo3 = np.concatenate([np.full(749, np.nan), W750.min(1)])
        hi3 = np.where(np.isnan(hi3), fa, hi3)
        lo3 = np.where(np.isnan(lo3), fb, lo3)
    else:
        hi3, lo3 = fa, fb
    pos3 = 100.0 * (c - lo3) / np.where(hi3 - lo3 > 0, hi3 - lo3, np.nan)
    off_lo3 = (c / np.where(lo3 > 0, lo3, np.nan) - 1) * 100
    cand_basic = (pct >= 0.03) & (v >= 2 * v20) & (v20 > 0)
    # 当日介入八门(先查便宜门, 段起锚后查)
    if np.isnan(pos250[t]) or pos250[t] >= 50:
        return
    if not (P["price_min"] <= c[t] <= P["price_max"]):
        return
    if not (ma20[t] > ma60[t]):
        return
    bias20 = (c[t] / ma20[t] - 1) * 100
    if bias20 > P["bias20_max"]:
        return
    Vw = v[t - 249:t + 1]
    vrank = 100.0 * sum(1 for x in Vw if x <= v[t]) / 250
    if vrank >= P["vrank_max"]:
        return
    if pos3[t] >= P["pos3_max"] or off_lo3[t] > P["off_lo3_max"]:
        return
    # 段起锚: 250窗最低点(ties取最近)之后首段起, days窗
    l0 = lo_day[t]
    if l0 < 0:
        return
    start_i = None
    for i in range(max(l0, 19), t + 1):
        if cand_basic[i]:
            start_i = i
            break
    if start_i is None or l0 > start_i:
        return  # 段起须在最低点当日或之后
    days = t - start_i
    if days > P["days_max"]:
        return
    if cand_basic[max(l0, 0):start_i].any():
        return  # 更早段起(首段起)
    pre60 = c[max(0, start_i - 60):start_i]
    if len(pre60) >= 40:
        if pre60.min() / pre60.max() - 1 > P["pre_dd_max"] / 100:
            return
        pre20 = v[max(0, start_i - 20):start_i].mean()
        v250s = v250[start_i] if not np.isnan(v250[start_i]) else v.mean()
        if v250s <= 0 or pre20 / v250s > P["shrink_cap"]:
            return
    else:
        return  # 影子不豁免次新(保守, prereg已声明与回测语义差)
    pre_shrink = (v[max(0, start_i - 20):start_i].mean() /
                  (v250[start_i] if not np.isnan(v250[start_i]) and v250[start_i] > 0 else v.mean()))
    if pre_shrink > P["pre_shrink_max"]:
        return
    off_start = (c[t] / c[start_i] - 1) * 100
    if off_start > P["off_start_hi"] or off_start < P["off_start_lo"]:
        return
    # 组合过滤: 当日pos60<=40
    w60 = c[max(0, t - 59):t + 1]
    if len(w60) < 20 or w60.max() <= w60.min():
        return
    pos60 = 100.0 * (c[t] - w60.min()) / (w60.max() - w60.min())
    if pos60 > P["pos60_max"]:
        return
    e = o[t + 1] if t + 1 < n and o[t + 1] > 0 else None  # 影子日T=库尾, D+1次日通常不存在→entry挂起, 回填补
    hits.append({
        "code": code, "name": nm, "s_date": int(dt[start_i]), "d_date": T,
        "days": days, "entry": round(float(e), 3) if e else None,
        "px_D": round(float(c[t]), 3), "pos60": round(float(pos60), 1),
        "pre_shrink": round(float(pre_shrink), 3), "off_start": round(float(off_start), 2),
        "vr_s": round(float(v[start_i] / v20[start_i]), 2), "pct_s": round(float(pct[start_i] * 100), 2),
        "pos250": round(float(pos250[t]), 1), "pos3": round(float(pos3[t]), 1),
        "f5": None, "f20": None, "f60": None, "f5_net": None, "f20_net": None, "f60_net": None,
        "registered": T,
    })


while True:
    chunk = cur.fetchmany(400_000)
    if not chunk:
        break
    for code, date_, o_, h_, l_, cl_, v_ in chunk:
        if code != cur_code:
            if cur_code is not None and cur_code in uni_set:
                flush(cur_code, uni_name.get(cur_code, ""), buf)
            cur_code, buf = code, []
        buf.append((date_, o_, h_, l_, cl_, v_))
if cur_code is not None and cur_code in uni_set:
    flush(cur_code, uni_name.get(cur_code, ""), buf)

# ---- 合并台账(UPSERT: 保留最早d_date) ----
if DRY_RUN:
    log(f"[combo] 🧪 dry-run: 命中{len(hits)}条不写台账")
    for h in hits[:20]:
        log(f"  {h['code']} {h['name']} s={h['s_date']} days={h['days']} px={h['px_D']} pos60={h['pos60']} shrink={h['pre_shrink']}")
    log(f"[combo] 耗时{time.time()-t0:.0f}s")
    sys.exit(0)
merged = dict(ledger)
new_n = 0
for h in hits:
    k = (h["code"], int(h["s_date"]))
    if k not in merged:
        merged[k] = h
        new_n += 1
    elif int(h["d_date"]) < int(merged[k]["d_date"]):
        old = merged[k]
        for kk in ("f5", "f20", "f60", "f5_net", "f20_net", "f60_net"):
            h[kk] = old.get(kk)
        merged[k] = h

# ---- 到期回填 ----
filled = 0
for (code, s_date), r in merged.items():
    if r.get("f60") is not None and r.get("f5") is not None:
        continue
    rows = get_bars(code)
    dates = [int(x[0]) for x in rows]
    if r["d_date"] not in dates:
        continue
    di = dates.index(r["d_date"])
    e = r.get("entry")
    if not e and di + 1 < len(rows) and float(rows[di + 1][2] or 0) > 0:
        e = float(rows[di + 1][2])
        r["entry"] = round(e, 3)
    if not e:
        continue
    for kk in (5, 20, 60):
        if r.get(f"f{kk}") is None and di + kk < len(rows):
            cl = float(rows[di + kk][4] or 0)
            if cl > 0:
                gross = (cl / e - 1) * 100
                r[f"f{kk}"] = round(gross, 2)
                r[f"f{kk}_net"] = round(gross - P["fee_net"] * 100, 2)
                filled += 1

with open(LEDGER, "w", encoding="utf-8") as f:
    for r in sorted(merged.values(), key=lambda x: (x["d_date"], x["code"])):
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
json.dump({"last_run": T, "rows": len(merged), "new_today": new_n, "filled": filled,
           "hits_today": len(hits), "elapsed": round(time.time() - t0, 1)},
          open(STATE, "w", encoding="utf-8"))
log(f"[combo] 当日命中{len(hits)} 新增{new_n} 回填{filled} 台账{len(merged)}行 耗时{time.time()-t0:.0f}s")
