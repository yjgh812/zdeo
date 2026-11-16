# -*- coding: utf-8 -*-
"""统一事件研究流水线·第一批全本地事件 (20261025, 主人「继续」令)。

依据: data/PREREG_EVENT_PIPELINE_LOCAL_20261025.json (跑前冻结)。
E1=ST摘帽(买侧, st_history本地) / E2=商誉减值突变(排雷侧, goodwill PIT本地) /
E3=商誉高位>=50%净资产(排雷侧)。入场=事件日次日收盘(c1), t20e(-8%止损/T+20),
基准=同日全宇宙同口径。只读qfq+st_history+event_calendar, 零交易权。
输出: analysis/event_pipeline_local_20261025_result.json
"""
import hashlib
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
GWDB = r"file:D:\zcodesj\event_calendar.db?mode=ro"
PREREG = r"D:\zcodesj\data\PREREG_EVENT_PIPELINE_LOCAL_20261025.json"
OUT = r"D:\zcodesj\analysis\event_pipeline_local_20261025_result.json"

START, END = 20160101, 20261023
HOLD, STOP = 20, 0.92

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["events_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"prereg指纹={fp}")

# ---------- 价格底座 ----------
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close,volume FROM stock_daily_meta_qfq ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64")
pos_of = {int(x): i for i, x in enumerate(d_sorted)}
codes_list = list(piv.columns)
close_by_code = {c: piv[c].to_numpy(dtype=float) for c in codes_list}
log(f"qfq 行={len(df)} 股={len(codes_list)}")

# ---------- ST标记(事件提取+宇宙过滤共用) ----------
st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date,is_st FROM st_daily ORDER BY code,date", st_con)
st_con.close()
st_rows["code"] = st_rows["code"].map(norm_code)
st_rows["d"] = st_rows["date"].astype("int64")
st_set = set(zip(st_rows.loc[st_rows["is_st"] == 1, "code"], st_rows.loc[st_rows["is_st"] == 1, "d"]))

# ---------- E1: ST摘帽转换日 ----------
st_g = st_rows.sort_values(["code", "d"])
prev_st = st_g.groupby("code")["is_st"].shift(1)
uncap = st_g[(st_g["is_st"] == 0) & (prev_st == 1) & (st_g["d"] >= START) & (st_g["d"] <= END)]
ev1 = [(c, int(d)) for c, d in zip(uncap["code"], uncap["d"])]
# 冷却去重(同股20交易日内的连续记录只计首个; st_daily按日记录, 转换日本身唯一, 直接按code去重相邻)
seen, ev1_dedup = {}, []
for c, d in ev1:
    if c in seen and d - seen[c] < 20000000:  # 同年内的重复转换(罕见)跳过粗去重
        continue
    seen[c] = d
    ev1_dedup.append((c, d))
ev1 = ev1_dedup
log(f"E1摘帽事件={len(ev1)}")

# ---------- E2/E3: 商誉事件(PIT) ----------
gw_con = sqlite3.connect(GWDB, uri=True)
gw = pd.read_sql_query(
    "SELECT code,report_date,notice_date,pit_avail_date,goodwill_scale,goodwill_previous,gw_to_net_assets "
    "FROM goodwill_stock_hist", gw_con)
gw_con.close()
gw["code"] = gw["code"].map(norm_code)
gw = gw[~gw["code"].str.startswith(("688", "689", "8", "4", "92"))]
gw = gw[gw["code"].str[0].isin(["6", "0", "3"])]
for col in ("notice_date", "pit_avail_date"):
    gw[col] = gw[col].astype(str).str.replace("-", "", regex=False)
gw["d_ev"] = gw[["notice_date", "pit_avail_date"]].max(axis=1).astype("int64", errors="ignore")
gw = gw[(gw["d_ev"] >= START) & (gw["d_ev"] <= END)]
gw = gw.drop_duplicates(subset=["code", "report_date"])

gwp = pd.to_numeric(gw["goodwill_previous"], errors="coerce")
gws = pd.to_numeric(gw["goodwill_scale"], errors="coerce")
gwr = pd.to_numeric(gw["gw_to_net_assets"], errors="coerce")
imp_mask = (gwp > 0) & (gws < gwp * 0.7)
ev2 = [(c, int(d)) for c, d in zip(gw.loc[imp_mask, "code"], gw.loc[imp_mask, "d_ev"])]
high_mask = gwr >= 0.50
ev3 = [(c, int(d)) for c, d in zip(gw.loc[high_mask, "code"], gw.loc[high_mask, "d_ev"])]
log(f"E2商誉减值突变={len(ev2)}  E3商誉高位>=50%={len(ev3)}")

# ---------- 事件 → c1/t20e ----------
def event_outcome(code, d_ev):
    """事件日d_ev(可能非交易日→次一交易bar) → 次一bar收盘入场 → t20e。"""
    if code not in close_by_code:
        return None
    arr = close_by_code[code]
    i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted):
        return None
    if (code, int(d_sorted[i])) in st_set:      # 事件日仍ST则非屠龙可交易
        return None
    e = i + 1
    if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
        return None
    entry = arr[e]
    end = min(e + HOLD, len(arr) - 1)
    if end <= e:
        return None
    w = arr[e + 1: end + 1]
    broke = w <= entry * STOP
    xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
    if not np.isfinite(xp):
        return None
    return {"d": int(d_sorted[i]), "win": bool(xp > entry), "ret": xp / entry - 1.0}


def build_events(raw):
    out = []
    for c, d in raw:
        r = event_outcome(c, d)
        if r:
            out.append({"code": c, **r})
    return pd.DataFrame(out)


ev_dfs = {}
for name, raw in (("E1_st_uncap", ev1), ("E2_gw_impair", ev2), ("E3_gw_high", ev3)):
    ev_dfs[name] = build_events(raw)
    log(f"{name}: 有效事件={len(ev_dfs[name])}")

# ---------- 匹配基准(事件日期集) ----------
need_dates = sorted({int(r["d"]) for e in ev_dfs.values() for r in e.to_dict("records")})
log(f"基准日期数={len(need_dates)}")


def universe_outcome_at(T):
    i = pos_of.get(T)
    if i is None:
        return []
    entry_arr = piv.iloc[i].to_numpy(dtype=float)
    wins, rets = [], []
    for ci in range(len(codes_list)):
        e_val = entry_arr[ci]
        if not np.isfinite(e_val) or e_val <= 0 or e_val > 30.0:
            continue
        cx = codes_list[ci]
        if (cx, T) in st_set:
            continue
        arr = close_by_code[cx]
        e = i + 1
        if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
            continue
        entry = arr[e]
        end = min(e + HOLD, len(arr) - 1)
        w = arr[e + 1: end + 1]
        broke = w <= entry * STOP
        xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
        if np.isfinite(xp):
            wins.append(xp > entry)
            rets.append(xp / entry - 1.0)
    return wins, rets


base_by_d = {}
for T in need_dates:
    wins, rets = universe_outcome_at(T)
    if wins:
        base_by_d[T] = {"bw": float(np.mean(wins)), "br": float(np.mean(rets)), "bn": len(wins)}
log("基准完成")


def stat_event(name):
    e = ev_dfs[name]
    out = {"n": int(len(e))}
    if len(e) == 0:
        return out
    w = e["win"].astype(float)
    bw = e["d"].map(lambda x: base_by_d.get(x, {}).get("bw"))
    br = e["d"].map(lambda x: base_by_d.get(x, {}).get("br"))
    ok = bw.notna()
    out["win"] = round(float(w[ok].mean()), 4)
    out["base_win"] = round(float(bw[ok].mean()), 4)
    out["win_excess_pp"] = round((out["win"] - out["base_win"]) * 100, 2)
    out["mean_ret"] = round(float(e.loc[ok, "ret"].mean()), 4)
    out["base_mean_ret"] = round(float(br[ok].mean()), 4)
    out["ret_excess_pp"] = round((out["mean_ret"] - out["base_mean_ret"]) * 100, 2)
    e2 = e[ok].assign(year=e["d"] // 10000)
    by = {}
    for y, s in e2.groupby("year"):
        if len(s) >= 5:
            b = s["d"].map(lambda x: base_by_d.get(x, {}).get("bw")).mean()
            by[int(y)] = {"n": int(len(s)),
                          "win_exc_pp": round((float(s["win"].mean()) - float(b)) * 100, 2)}
    out["by_year"] = by
    out["n_pos_years"] = int(np.sum([v["win_exc_pp"] > 0 for v in by.values()]))
    out["n_neg_years"] = int(np.sum([v["win_exc_pp"] < 0 for v in by.values()]))
    return out


res = {name: stat_event(name) for name in ev_dfs}

# ---------- 门禁判定 ----------
g1 = res["E1_st_uncap"]
gates = {
    "E1_descriptive(n>=100, exc>=+2.0)": (g1.get("n", 0) >= 100 and g1.get("win_excess_pp", -99) >= 2.0),
    "E1_escalated(n>=150, exc>=+2.5, ret>=+1.0, years>=6/11)": (
        g1.get("n", 0) >= 150 and g1.get("win_excess_pp", -99) >= 2.5
        and g1.get("ret_excess_pp", -99) >= 1.0 and g1.get("n_pos_years", 0) >= 6),
    "E2_escalated(exc<=-1.5, neg_years>=6)": (
        res["E2_gw_impair"].get("win_excess_pp", 0) <= -1.5
        and res["E2_gw_impair"].get("n_neg_years", 0) >= 6),
    "E3_escalated(exc<=-1.5, neg_years>=6)": (
        res["E3_gw_high"].get("win_excess_pp", 0) <= -1.5
        and res["E3_gw_high"].get("n_neg_years", 0) >= 6),
}
e1_verdict = "PASS_CANDIDATE(提请)" if gates["E1_escalated(n>=150, exc>=+2.5, ret>=+1.0, years>=6/11)"] else "FAIL"
e2_verdict = "排雷确认(避雷价值成立)" if gates["E2_escalated(exc<=-1.5, neg_years>=6)"] else "未确认(中性登记)"
e3_verdict = "排雷确认(避雷价值成立)" if gates["E3_escalated(exc<=-1.5, neg_years>=6)"] else "未确认(中性登记)"

out = {"meta": {"generated_at": "2026-10-25", "order": "继续→统一事件流水线·第一批本地事件",
                "prereg": PREREG, "fingerprint": fp,
                "blocked_events_note": "增持/回购/定增两节点/大宗折价/PEAD长窗/调研/控制权变更/连续并购"
                "=数据通道阻塞缓测(本地token无权限, MCP批量不可行; akshare stock_yjyg_em已探明可全史拉=解锁路径)",
                "zero_trade_authority": True},
       "events": res, "gates": {k: bool(v) for k, v in gates.items()},
       "verdicts": {"E1_st_uncap_buy": e1_verdict, "E2_gw_impairment": e2_verdict,
                    "E3_gw_high": e3_verdict}}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:3000])
