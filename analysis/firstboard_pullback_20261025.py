# -*- coding: utf-8 -*-
"""首板断板回调·四阶段状态机 预注册检验 (20261025, 主人「继续」令第一梯队第二项)。

依据: data/PREREG_FIRSTBOARD_PULLBACK_20261025.json (跑前冻结)。
状态机: D0首板(近30日首次涨停/非一字/量>=2xMA5量) -> D1..D5地量日(量<=D0/3, 不破D0最低价,
        无涨停插队) -> 地量日后[1,3]确认日(收阳+量>=1.2x前日) -> 次日收盘入场。
主臂=确认日入场(五门禁); 对照臂=地量日入场/严格量/分板(描述)。
出场: t5e与t20e(-8%止损/到期)。只读qfq+st_history, 零交易权。
输出: analysis/firstboard_pullback_20261025_result.json
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
PREREG = r"D:\zcodesj\data\PREREG_FIRSTBOARD_PULLBACK_20261025.json"
OUT = r"D:\zcodesj\analysis\firstboard_pullback_20261025_result.json"

START, END = 20160101, 20261023
COST_RT = 0.003
STOPS = {"t5e": (5, 0.92), "t20e": (20, 0.92)}
LOOKBACK_BOARD = 30
PULL_WINDOW = 5
CONFIRM_WINDOW = 3
COOLDOWN = 20

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["rule_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"prereg指纹={fp}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_by_code = {}
for c, d in zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")):
    st_by_code.setdefault(c, set()).add(int(d))

con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20150601' ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
df = df[df["d"] >= 20151001].reset_index(drop=True)   # 30日涨停回看预热
log(f"qfq 行={len(df)} 股={df['code'].nunique()}")

chg = df["change_pct"].to_numpy(dtype=float)
is_gem = df["code"].str.startswith(("300", "301")).to_numpy()
thr = np.where(is_gem, 19.8, 9.8)
df["is_limit"] = chg >= thr
df["is_one_word"] = (df["high"] - df["low"]).to_numpy() <= 0.05   # 一字近似
# 前5日均量
df["vma5"] = df.groupby("code")["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))

code_g = df["code"].to_numpy()
d_g = df["d"].to_numpy(dtype="int64")
o_g = df["open"].to_numpy(dtype=float)
h_g = df["high"].to_numpy(dtype=float)
l_g = df["low"].to_numpy(dtype=float)
c_g = df["close"].to_numpy(dtype=float)
v_g = df["volume"].to_numpy(dtype=float)
lim_g = df["is_limit"].to_numpy()
one_g = df["is_one_word"].to_numpy()
vma5_g = df["vma5"].to_numpy(dtype=float)


def fwd(idx_entry, hold, stop):
    """入场=idx_entry行收盘, 窗口=其后hold行(止损/到期)。返回(win, ret)或None。"""
    n_grp_end = grp_end[idx_entry]
    e = idx_entry
    if e >= n_grp_end:
        return None
    entry = c_g[e]
    if not np.isfinite(entry) or entry <= 0 or entry > 30.0:
        return None
    end = min(e + hold, n_grp_end - 1)
    if end <= e:
        return None
    w = c_g[e + 1: end + 1]
    broke = w <= entry * stop
    exit_px = w[int(np.argmax(broke))] if broke.any() else c_g[end]
    if not np.isfinite(exit_px):
        return None
    return (bool(exit_px > entry), exit_px / entry - 1.0)


# 组边界(同股连续行段)
grp_end = np.zeros(len(df), dtype=np.int64)
for _, gi in df.groupby("code", sort=False).indices.items():
    gi = np.sort(gi)
    grp_end[gi] = gi[-1] + 1
log("组边界完成")


def cooldown_ok(cand, last_by_code):
    key = code_g[cand]
    if key in last_by_code and cand - last_by_code[key] <= COOLDOWN:
        return False
    return True


events = []          # 主臂+对照臂同批提取
last_by_code = {}
grouped = list(df.groupby("code", sort=False))
log(f"状态机扫描开始 股数={len(grouped)}")
for gi, (code, g) in enumerate(grouped):
    gi_rows = np.sort(g.index.to_numpy())
    n = len(gi_rows)
    if n < 60:
        continue
    st_d = st_by_code.get(code, ())
    lim = lim_g[gi_rows]
    one = one_g[gi_rows]
    vv = v_g[gi_rows]
    vma5 = vma5_g[gi_rows]
    oo, hh, ll, cc = o_g[gi_rows], h_g[gi_rows], l_g[gi_rows], c_g[gi_rows]
    dd = d_g[gi_rows]
    i = LOOKBACK_BOARD
    while i < n - 8:
        # 冷却
        last = last_by_code.get(code)
        if last is not None and i - last <= COOLDOWN:
            i += 1
            continue
        if not (lim[i] and not one[i]):
            i += 1
            continue
        if lim[i - LOOKBACK_BOARD:i].any():          # 近30日已涨停
            i += 1
            continue
        if not np.isfinite(vma5[i]) or vv[i] < 2 * vma5[i]:
            i += 1
            continue
        if dd[i] < START:
            i += 1
            continue
        if st_d and int(dd[i]) in st_d:
            i += 1
            continue
        d0 = i
        vol0, low0 = vv[d0], ll[d0]
        # 阶段2: D1..D5 地量日
        quiet = None
        ok_struct = True
        for j in range(d0 + 1, min(d0 + 1 + PULL_WINDOW, n)):
            if lim[j]:                                 # 插队涨停=二板结构, 无效
                ok_struct = False
                break
            if ll[j] < low0:                           # 地板破
                ok_struct = False
                break
            if vv[j] <= vol0 / 3.0:
                quiet = j
                break
        if not ok_struct or quiet is None:
            i = d0 + 1
            continue
        # 阶段3: 地量日后[1,3]确认日
        conf = None
        for k in range(quiet + 1, min(quiet + 1 + CONFIRM_WINDOW, n)):
            if cc[k] > oo[k] and vv[k] >= 1.2 * vv[k - 1]:
                conf = k
                break
            if ll[k] < low0:
                break
        last_by_code[code] = d0
        if conf is None:
            i = d0 + 1
            continue
        if dd[conf] > END:
            i = d0 + 1
            continue
        ev = {"code": code, "date": int(dd[conf]), "d0": int(dd[d0]),
              "board": "gem" if code.startswith(("300", "301")) else "main",
              "strict_vol": bool(vv[conf] >= vol0)}
        # 主臂: 确认日次日收盘入场
        e_main = gi_rows[conf] + 1 if conf + 1 < n else -1
        if e_main >= 0 and e_main < len(df) and code_g[e_main] == code:
            for tag, (hold, stop) in STOPS.items():
                r = fwd(e_main, hold, stop)
                if r:
                    ev[f"{tag}_win"] = r[0]
                    ev[f"{tag}_ret"] = r[1]
            ev["entry_date"] = int(d_g[e_main])
            events.append(dict(ev, arm="confirm"))
        # 对照臂A: 地量日次日收盘
        e_q = gi_rows[quiet] + 1 if quiet + 1 < n else -1
        if e_q >= 0 and e_q < len(df) and code_g[e_q] == code:
            evq = {"code": code, "date": int(d_g[gi_rows[quiet]]), "d0": int(dd[d0]),
                   "board": ev["board"], "strict_vol": ev["strict_vol"], "arm": "quiet"}
            for tag, (hold, stop) in STOPS.items():
                r = fwd(e_q, hold, stop)
                if r:
                    evq[f"{tag}_win"] = r[0]
                    evq[f"{tag}_ret"] = r[1]
            events.append(evq)
        i = conf + 1
    if (gi + 1) % 800 == 0:
        log(f"  ... {gi+1}/{len(grouped)}")
log(f"事件提取完成 总数={len(events)}")

ev_df = pd.DataFrame(events)
ev_df = ev_df.dropna(subset=["t20e_win"])
log(f"含t20e结果事件={len(ev_df)} (主臂={int((ev_df['arm']=='confirm').sum())})")

# ---------- 基准: 同日全宇宙同口径(仅事件日期, piv逐code向量化) ----------
log("全宇宙前向基准(仅事件日期)…")
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64")
pos_of = {int(dd): i for i, dd in enumerate(d_sorted)}
codes_list = list(piv.columns)
close_by_code = {c: piv[c].to_numpy(dtype=float) for c in codes_list}
need_dates = sorted(set(int(x) for x in ev_df["date"]))
base_rows = []
for T in need_dates:
    i = pos_of.get(T)
    if i is None:
        continue
    entry_arr = piv.iloc[i].to_numpy(dtype=float)
    w5w, w20w, r20s = [], [], []
    for ci in range(len(codes_list)):
        e_val = entry_arr[ci]
        if not np.isfinite(e_val) or e_val <= 0 or e_val > 30.0:
            continue
        cx = codes_list[ci]
        st_d = st_by_code.get(cx, ())
        if st_d and T in st_d:
            continue
        arr = close_by_code[cx]
        e = i + 1
        if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
            continue
        entry = arr[e]
        end5 = min(e + 5, len(arr) - 1)
        w = arr[e + 1: end5 + 1]
        w = w[np.isfinite(w)]
        if len(w):
            broke = w <= entry * 0.92
            xp = w[int(np.argmax(broke))] if broke.any() else arr[end5]
            if np.isfinite(xp):
                w5w.append(xp > entry)
        end20 = min(e + 20, len(arr) - 1)
        w = arr[e + 1: end20 + 1]
        w = w[np.isfinite(w)]
        if len(w):
            broke = w <= entry * 0.92
            xp = w[int(np.argmax(broke))] if broke.any() else arr[end20]
            if np.isfinite(xp):
                w20w.append(xp > entry)
                r20s.append(xp / entry - 1.0)
    if w20w:
        base_rows.append({"d": T, "bw5": float(np.mean(w5w)) if w5w else None,
                          "bw20": float(np.mean(w20w)), "br20": float(np.mean(r20s)),
                          "bn": len(w20w)})
base_by_d = pd.DataFrame(base_rows).set_index("d")
log(f"基准完成 日数={len(base_by_d)}")


def arm_stats(sub, tag):
    out = {"n": int(len(sub))}
    if len(sub) == 0:
        return out
    w = sub[f"{tag}_win"].astype(float)
    r = sub[f"{tag}_ret"].astype(float)
    key = "bw5" if tag == "t5e" else "bw20"
    bw = sub["date"].map(base_by_d[key])
    out["win"] = round(float(w.mean()), 4)
    out["base_win"] = round(float(bw.mean()), 4)
    out["win_excess_pp"] = round((float(w.mean()) - float(bw.mean())) * 100, 2)
    if tag == "t20e":
        br = sub["date"].map(base_by_d["br20"])
        out["net_mean_excess_pp"] = round((float(r.mean()) - COST_RT - float(br.mean())) * 100, 2)
        out["mean_ret"] = round(float(r.mean()), 4)
    sub2 = sub.assign(year=sub["date"] // 10000)
    out["by_year_win_excess"] = {
        int(y): round((float(s[f"{tag}_win"].mean()) - float(s["date"].map(base_by_d[key]).mean())) * 100, 2)
        for y, s in sub2.groupby("year")}
    out["n_pos_years"] = int(np.sum([v > 0 for v in out["by_year_win_excess"].values()]))
    return out


main = ev_df[ev_df["arm"] == "confirm"]
ctrl = {f"ctrl_{k}": v for k, v in {
    "quiet": ev_df[ev_df["arm"] == "quiet"],
    "strict_vol": main[main["strict_vol"]],
    "main_board": main[main["board"] == "main"],
    "gem": main[main["board"] == "gem"]}.items()}

res_main = {"t5e": arm_stats(main, "t5e"), "t20e": arm_stats(main, "t20e")}
res_ctrl = {k: {"t20e": arm_stats(v, "t20e"), "t5e": arm_stats(v, "t5e")} for k, v in ctrl.items()}

g = res_main
gates = {
    "G1_events(n>=500)": len(main) >= 500,
    "G2_t20e_win_excess(>=+3.0pp)": g["t20e"]["win_excess_pp"] >= 3.0,
    "G3_t5e_win_excess(>=+2.0pp)": g["t5e"]["win_excess_pp"] >= 2.0,
    "G4_net_mean_excess(>=+1.5pp)": g["t20e"]["net_mean_excess_pp"] >= 1.5,
    "G5_by_year(>=6/11正)": g["t20e"]["n_pos_years"] >= 6,
}
verdict = "PASS_CANDIDATE" if all(gates.values()) else "FAIL"

out = {"meta": {"generated_at": "2026-10-25", "order": "继续→第一梯队第二项(首板回调状态机)",
                "prereg": PREREG, "fingerprint": fp,
                "gates_LOCKED": pre["acceptance_gates_LOCKED"], "zero_trade_authority": True},
       "main_arm_confirm": res_main, "control_arms": res_ctrl,
       "gates": {k: bool(v) for k, v in gates.items()}, "verdict": verdict}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
print(json.dumps({"verdict": verdict, "gates": gates,
                  "main_t20e": res_main["t20e"], "main_t5e": res_main["t5e"],
                  "ctrl_t20e": {k: v["t20e"]["win_excess_pp"] for k, v in res_ctrl.items()},
                  "ctrl_n": {k: v["t20e"]["n"] for k, v in res_ctrl.items()}},
                 ensure_ascii=False, indent=1, default=str))
