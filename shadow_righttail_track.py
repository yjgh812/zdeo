# -*- coding: utf-8 -*-
"""右尾集中影子跟踪器 (prereg RIGHT-TAIL-WEIGHT-V1, 指纹 efe85a6bd1a71ac0)
================================================================
设计: 与 bt7 引擎 bp_ep_core 月度信号同源。信号层复刻引擎管线(引擎 tulong_backtest_full_report.py
L495-531/L848-901 语义): PIT merge_asof(backward, by=code) + 日度重算 ep/bp(=raw*ref_close/close)
+ 当日 qfq close<=30 + tradable 剔除(688/689/8/4/92/43 前缀, is_st=0) + score=(ep_rank+bp_rank)/2 Top20。
硬锚(prereg 放行前置): 引擎当月新买入码必须 ⊆ 本 tracker Top20 (precision>=90%)——
引擎对延续持仓不重复买入, 故锚为单侧 precision; 锚不过拒绝出数(exit 2)。
影子层唯一新语义: Top20 内 score-rank 线性加权(clip 2x 等权) vs 等权, next-bar qfq 计益, 差值法免成本。
backfill(2016-2026)=样本内 sanity(有前视, 非证据); 影子月(>=202610)写台账 data/righttail_shadow_ledger.jsonl。
用法: python shadow_righttail_track.py [--backfill]
"""
import sys, os, json, csv, argparse, sqlite3
from datetime import datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PREREG = r"D:\zcodesj\data\prereg_righttail_weight_20260925.json"
BT = r"D:\zcodesj\reports\drift_check_20260924a_0924end"
MASTER_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = r"D:\zcodesj\data\righttail_shadow_ledger.jsonl"
OUT_DIR = os.path.join(BT, "righttail_shadow")
ANCHOR_MIN = 0.90
TOPN = 20
CLIP_MULT = 2.0

def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

def load_prereg():
    d = json.load(open(PREREG, encoding="utf-8"))
    assert d["arm"] == "RIGHT-TAIL-WEIGHT-V1" and d["fingerprint"], "prereg 不完整"
    return d

def rebalance_days():
    rows = list(csv.DictReader(open(os.path.join(BT, "bp_ep_core_trades_full.csv"), encoding="utf-8-sig")))
    by_month, codes = {}, {}
    for r in rows:
        d = r["买入日期"]; m = d[:7]
        if m not in by_month or d < by_month[m]:
            by_month[m] = d
    days = sorted(by_month.values())
    for r in rows:
        m = r["买入日期"][:7]
        codes.setdefault(by_month[m], set()).add(str(r["股票代码"]).zfill(6)[-6:])
    return days, codes

def load_tradable():
    con = ro(MASTER_DB)
    si = pd.read_sql("SELECT code, is_st FROM stock_info", con)
    con.close()
    si["code"] = si["code"].astype(str).str.zfill(6).str[-6:]
    si = si[~si["code"].str.startswith(("688", "689", "8", "4", "92", "43"))]
    si = si[pd.to_numeric(si["is_st"], errors="coerce").fillna(0) == 0]
    return set(si["code"])

def load_pit():
    con = ro(PIT_DB)
    pit = pd.read_sql(
        "SELECT code, pit_avail_date, ep, bp, ref_close FROM stock_factors_fundamental_pit_v2", con)
    con.close()
    pit["code"] = pit["code"].astype(str).str.zfill(6).str[-6:]
    pit = pit.dropna(subset=["ep", "bp", "ref_close"])
    pit = pit[pit["code"].isin(load_tradable())]
    pit["avail_num"] = pit["pit_avail_date"].astype(str).str.replace("-", "").astype(int)
    return pit.sort_values("avail_num")

def load_qfq_close(tradable):
    con = ro(QFQ_DB)
    df = pd.read_sql("SELECT code, date, close, open FROM stock_daily_meta_qfq", con)
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6).str[-6:]
    df = df[df["code"].isin(tradable)]
    df["date"] = df["date"].astype(str)
    return df.sort_values("date")

def top20_at(pit, day_close_df, day8):
    """信号日 T 截面: 当日 qfq close + PIT merge_asof(backward by code) + 日度重算 ep/bp (引擎 L495-531 语义)"""
    day_df = day_close_df[day_close_df["date"] == day8]
    if len(day_df) == 0:
        return None
    day_df = day_df.copy()
    day_df["date_num"] = int(day8)
    m = pd.merge_asof(day_df, pit, left_on="date_num", right_on="avail_num", by="code", direction="backward")
    m = m.dropna(subset=["ep", "bp", "ref_close", "close"])
    m = m[m["close"] <= 30]
    if len(m) < TOPN:
        return None
    m = m.copy()
    m["ep_d"] = m["ep"] * m["ref_close"] / m["close"]   # 日度重算(引擎同款)
    m["bp_d"] = m["bp"] * m["ref_close"] / m["close"]
    m["score"] = (m["ep_d"].rank(pct=True) + m["bp_d"].rank(pct=True)) / 2
    return m.nlargest(TOPN, "score")

def next_close_row(qfq_dates_sorted, piv_close, day8, last_resort):
    idx = qfq_dates_sorted
    pos = idx.searchsorted(day8, side="right")
    if pos >= len(idx):
        return last_resort
    d = idx[pos]
    return d, piv_close.loc[d]

def period_return(open_piv, codes_w, exec_day, next_exec_day):
    """T+1 开盘价成交(引擎语义): exec_day 开盘买入 → next_exec_day 开盘卖出; 末期无 next 时用样本末日收盘"""
    if exec_day not in open_piv.index:
        return 0.0, len(codes_w)
    b_row = open_piv.loc[exec_day]
    if next_exec_day and next_exec_day in open_piv.index:
        s_row = open_piv.loc[next_exec_day]
    else:
        s_row = open_piv.iloc[-1]
    tot_w, port, misses = 0.0, 0.0, 0
    for c, w in codes_w.items():
        try:
            r = float(s_row[c]) / float(b_row[c]) - 1.0
            if not np.isfinite(r):
                raise ValueError("non-finite")
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            misses += 1
            continue
        port += w * r; tot_w += w
    return (port / tot_w if tot_w > 0 else 0.0), misses

def weights_equal(codes):
    return {c: 1.0 / len(codes) for c in codes}

def _rank_weights(values_by_code):
    """values 降序排名 1..N 线性加权, clip<=2x等权, 归一化; nan 排最末"""
    ew = 1.0 / TOPN
    items = sorted(values_by_code.items(), key=lambda kv: (np.isnan(kv[1]), -kv[1]))
    raw = {c: (TOPN - i) for i, (c, _) in enumerate(items)}
    s = sum(raw.values())
    w = {c: min(v / s, CLIP_MULT * ew) for c, v in raw.items()}
    s2 = sum(w.values())
    return {c: v / s2 for c, v in w.items()}

def weights_rank(top):
    return _rank_weights({c: float(TOPN - i) for i, c in enumerate(top["code"])})  # score 降序即 rank

def weights_momentum(top, ret20_row):
    vals = {}
    for c in top["code"]:
        v = ret20_row.get(c)
        vals[c] = float(v) if v is not None and np.isfinite(v) else float("nan")
    return _rank_weights(vals)

def nw_t(series, lag=3):
    x = np.asarray(series, dtype=float); n = len(x)
    if n < 5: return float("nan")
    e = x - x.mean()
    g0 = (e @ e) / n; s = g0
    for l in range(1, lag + 1):
        gl = (e[l:] @ e[:-l]) / n
        s += 2 * (1 - l / (lag + 1)) * gl
    se = (s / n) ** 0.5
    return float(x.mean() / se) if se > 0 else float("nan")

def max_dd(monthly_rets):
    eq = np.cumprod(1 + np.asarray(monthly_rets))
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()
    prereg = load_prereg()
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"[PREREG] {prereg['arm']} fp={prereg['fingerprint']} frozen={prereg['frozen_at']}")

    days, eng_codes = rebalance_days()
    print(f"[CAL] 引擎调仓执行日 {len(days)} 个: {days[0]} ~ {days[-1]}")
    pit = load_pit()
    print(f"[PIT] {len(pit)} 行 (tradable 过滤后)")
    qfq_df = load_qfq_close(set(pit["code"].unique()))
    print(f"[QFQ] {len(qfq_df):,} 行")
    close_piv = qfq_df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    open_piv = qfq_df.pivot_table(index="date", columns="code", values="open", aggfunc="last").sort_index()
    trade_days = [d for d in close_piv.index if d >= "20160101"]
    # 引擎调度(strategy.py): 首个交易日即生成信号, 此后每22个交易日; T信号日收盘生成 → T+1 开盘执行
    sig_days = trade_days[0::22]
    exec_days = trade_days[1::22]
    print(f"[SCHED] 信号日 {len(sig_days)} 个(每22交易日): {sig_days[0]} ~ {sig_days[-1]}")

    recs, anchor_fails = [], []
    n_with_want = 0
    # 动量臂(prereg RIGHT-TAIL-MOM-W-V1): Top20 内 ret20 排名加权, 与 rank 臂同信号同执行同 gate
    mom_prereg_path = r"D:\zcodesj\data\prereg_righttail_momentum_20260925.json"
    mom_prereg = None
    ret20_df = None
    if os.path.exists(mom_prereg_path):
        mom_prereg = json.load(open(mom_prereg_path, encoding="utf-8"))
        assert mom_prereg["fingerprint"], "动量 prereg 指纹缺失"
        ret20_df = close_piv / close_piv.shift(20) - 1.0
        print(f"[MOM-ARM] {mom_prereg['arm']} fp={mom_prereg['fingerprint']} 已启用(试验计数=第2个加权方案)")
    for i, sig_day8 in enumerate(sig_days):
        if i + 1 >= len(exec_days):
            break
        exec_day8 = exec_days[i]
        next_exec_day8 = exec_days[i + 1] if i + 1 < len(exec_days) else None
        top = top20_at(pit, qfq_df, sig_day8)
        if top is None:
            continue
        got = set(top["code"])
        want = eng_codes.get(exec_day8[:4] + "-" + exec_day8[4:6] + "-" + exec_day8[6:], set())
        prec = len(got & want) / len(want) if want else 1.0
        if want:
            n_with_want += 1
        r_eq, m1 = period_return(open_piv, weights_equal(got), exec_day8, next_exec_day8)
        r_wt, m2 = period_return(open_piv, weights_rank(top), exec_day8, next_exec_day8)
        r_mom = None
        if mom_prereg is not None:
            r_mom, _ = period_return(open_piv, weights_momentum(top, ret20_df.loc[sig_day8]), exec_day8, next_exec_day8)
        kind = "shadow" if exec_day8 >= prereg["shadow_start"] else "backfill"
        passed = not want or prec >= ANCHOR_MIN
        if want and prec < ANCHOR_MIN:
            anchor_fails.append((exec_day8, f"precision={prec:.0%} 引擎买{len(want)}只我缺{sorted(want-got)[:6]}"))
        recs.append(dict(day=exec_day8, sig=sig_day8, kind=kind, precision=round(prec, 3),
                         r_eq=round(r_eq, 6), r_wt=round(r_wt, 6),
                         r_mom=(round(r_mom, 6) if r_mom is not None else None),
                         spread=round(r_wt - r_eq, 6),
                         miss_eq=m1, miss_wt=m2, anchor_pass=passed))
    # V2 锚: 达标月占比>=95% 方可出数; 未达标月两臂同剔(无偏倚)
    pass_rate = (n_with_want - len(anchor_fails)) / n_with_want if n_with_want else 0.0
    if pass_rate < 0.95:
        print(f"[ANCHOR-FAIL] V2 达标率 {pass_rate:.1%} < 95%, 拒绝出数 (失败 {len(anchor_fails)}/{n_with_want} 月):")
        for d, why in anchor_fails[:10]:
            print("  ", d, why)
        with open(os.path.join(OUT_DIR, "anchor_fail_list.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(f"{d}\t{why}" for d, why in anchor_fails))
        sys.exit(2)
    n_excl = len(anchor_fails)
    recs = [r for r in recs if r["anchor_pass"]]
    print(f"[ANCHOR] V2 达标率 {pass_rate:.1%} (={n_with_want - n_excl}/{n_with_want}) ✅ 同剔 {n_excl} 月: {[d for d, _ in anchor_fails]}")

    bf = [r for r in recs if r["kind"] == "backfill"]
    sh = [r for r in recs if r["kind"] == "shadow"]
    L = []
    A = L.append
    A(f"# 右尾集中影子跟踪 (prereg {prereg['arm']} fp={prereg['fingerprint']})\n")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} | 调仓月 n={len(recs)} (backfill {len(bf)} / shadow {len(sh)}) | 硬锚 precision>= {ANCHOR_MIN:.0%} 全过")
    A("> 口径: Top20 rank加权(clip 2x等权) vs 等权, next-bar qfq, 差值法免成本建模\n")
    for label, seg in [("backfill(样本内sanity, 有前视, 非证据)", bf), ("shadow(正式影子期)", sh)]:
        if not seg:
            A(f"## {label}\n\n- 无记录(影子期未开始)\n"); continue
        sp = [r["spread"] for r in seg]
        eq = [r["r_eq"] for r in seg]; wt = [r["r_wt"] for r in seg]
        t = nw_t(sp)
        def ann(rs):
            tot = np.prod(1 + np.asarray(rs)); yrs = len(rs) / 12.0
            return (tot ** (1 / yrs) - 1) * 100 if yrs > 0 else float("nan")
        gp = (len(seg) >= 12 and np.mean(sp) > 0 and t >= 2 and ann(wt) > ann(eq) and max_dd(wt) <= max_dd(eq))
        A(f"## {label}\n")
        A(f"- n={len(seg)} | 月度差 mean {np.mean(sp)*100:+.2f}pp | NW t={t:.2f} | 差胜率 {sum(1 for s in sp if s>0)/len(sp)*100:.0f}%")
        A(f"- 等权: 年化 {ann(eq):+.2f}% MDD {max_dd(eq)*100:.2f}% | 加权: 年化 {ann(wt):+.2f}% MDD {max_dd(wt)*100:.2f}%")
        A(f"- gate(n>=12且mean>0且NWt>=2且年化改善且MDD不恶化): **{'PASS' if gp else 'FAIL/未满'}**\n")
        if mom_prereg is not None and seg[0].get("r_mom") is not None:
            mo = [r["r_mom"] for r in seg]
            spm = [r["r_mom"] - r["r_eq"] for r in seg]
            tm = nw_t(spm)
            gpm = (len(seg) >= 12 and np.mean(spm) > 0 and tm >= 2 and ann(mo) > ann(eq) and max_dd(mo) <= max_dd(eq))
            A(f"- **动量臂({mom_prereg['arm']} fp={mom_prereg['fingerprint']})**: 月度差 mean {np.mean(spm)*100:+.2f}pp | NW t={tm:.2f} | 差胜率 {sum(1 for s in spm if s>0)/len(spm)*100:.0f}%")
            A(f"  - 动量加权: 年化 {ann(mo):+.2f}% MDD {max_dd(mo)*100:.2f}% | gate: **{'PASS' if gpm else 'FAIL/未满'}**\n")
    with open(os.path.join(OUT_DIR, "SHADOW_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    # 台账幂等: 已记录的 (day, prereg_fp) 跳过, 防重复运行污染
    seen = set()
    if os.path.exists(LEDGER):
        for ln in open(LEDGER, encoding="utf-8"):
            try:
                o = json.loads(ln)
                seen.add((o.get("day"), o.get("prereg_fp")))
            except Exception:
                continue
    n_new = 0
    with open(LEDGER, "a", encoding="utf-8") as f:
        for r in sh:
            for fp_, key in [(prereg["fingerprint"], "r_wt"), (mom_prereg["fingerprint"] if mom_prereg else None, "r_mom")]:
                if fp_ is None or key not in r or r[key] is None:
                    continue
                if (r["day"], fp_) in seen:
                    continue
                r2 = dict(r); r2["prereg_fp"] = fp_; r2["arm_key"] = key
                r2["ts"] = datetime.now().isoformat(timespec="seconds")
                f.write(json.dumps(r2, ensure_ascii=False) + "\n")
                n_new += 1
    print(f"[DONE] {OUT_DIR}/SHADOW_REPORT.md ; 台账新增 {n_new} 行(幂等去重后), 累计 shadow {len(sh)} 周期")

if __name__ == "__main__":
    main()
