# -*- coding: utf-8 -*-
"""
kell_reentry_cost_model_20260828.py — K1 重入分歧·成本建模重估（研究，只读）
============================================================
背景：K1 交叉验证在 ML20 短线簿上重入 A 出现分歧（A@N20 +20d +7.73% vs 基线 +5.00%，二次止损 28%）。
prereg 承诺"出现正期望须复核数据口径差异并成本建模后重估，不得直接立项"——本脚本执行该步。

本脚本把重入从"+20d 标记收益"改为【真实交易模拟】：
  触发日收盘进场 → 此后若收盘 ≤ 进场价×0.92 则 S-5 实际退出（实现约 -8%）→ 否则持有至 +20d 收盘退出
  往返交易成本按 round10 实测 0.26%（成交额比例，median）
判据：重入实现净收益（扣成本）是否仍 > 0（现金基线），以及相对"永不重入"的股票 +20d 标记。
"""
import csv, glob, os, sqlite3, statistics, re, sys, time
from collections import defaultdict

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT = r"D:\zcodesj\reports\K1_REENTRY_COST_MODEL_20260828.md"
COST   = 0.0026          # round10 实测往返成本率(median 0.261%)
LOOKBACKS = (5, 10, 20)

def load_events(pat):
    evs = []
    for fp in sorted(glob.glob(pat)):
        with open(fp, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            for r in reader:
                reason = (r.get("exit_reason") or r.get("卖出触发理由") or "").strip()
                if reason != "stop_loss":
                    continue
                if "exit_reason" in cols:
                    evs.append({"code": r["symbol"].strip().zfill(6),
                                "stop_date": r["exit_date"].replace("-", ""),
                                "stop_price": float(r["exit_price"])})
                else:
                    evs.append({"code": r["股票代码"].strip().zfill(6),
                                "stop_date": r["卖出日期"].replace("-", ""),
                                "stop_price": float(r["卖出价格"])})
    return evs

def load_qfq(codes):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT code, date, high, low, close, volume, change_pct FROM stock_daily_meta_qfq "
        "WHERE code IN (%s) AND date >= '20150101' ORDER BY code, date" % ",".join("?" * len(codes)),
        codes).fetchall()
    con.close()
    d = {}
    for code, date, hi, lo, close, vol, chg in rows:
        d.setdefault(code, []).append({"date": date, "high": hi, "low": lo, "close": close,
                                       "volume": vol, "change_pct": chg})
    return d

def limit_up(chg, code):
    if chg is None: return False
    return chg >= (19.8 if code[:2] in ("30", "68") else 9.8)

def ma_k(series, i, k):
    if i + 1 < k: return None
    return sum(r["close"] for r in series[i+1-k:i+1]) / k

def simulate_reentry(ev, series, lookback, anchor):
    """真实交易模拟：触发→进场→S-5实际退出或持有+20d。返回 dict 或 None。"""
    idx = [i for i, r in enumerate(series) if r["date"] <= ev["stop_date"]]
    if not idx: return None
    i0 = idx[-1]
    if i0 >= len(series) - 1: return None
    ref_high = series[i0]["high"]
    # 左桌（永不重入，股票 +20d 标记）
    left = {}
    for k in (5, 10, 20):
        j = i0 + k
        if j < len(series):
            left[k] = (series[j]["close"] - ev["stop_price"]) / ev["stop_price"]
    for k in range(1, lookback + 1):
        j = i0 + k
        if j >= len(series): break
        r = series[j]
        if limit_up(r["change_pct"], ev["code"]): continue
        vol_ratio = None
        base = [series[j-t]["volume"] for t in range(1, 6) if j-t >= 0 and series[j-t]["volume"]]
        if base and r["volume"]:
            vol_ratio = r["volume"] / (sum(base) / len(base))
        ma10, ma20 = ma_k(series, j, 10), ma_k(series, j, 20)
        hit = None
        if vol_ratio is not None and vol_ratio >= 1.5 and r["close"] >= ref_high:
            hit = "A"
        elif ma10 is not None and ma20 is not None and r["close"] > ma10 and ma10 > ma20:
            hit = "B"
        if hit != anchor: continue
        # 进场（触发日收盘）→ 持有模拟至 +20d 或 S-5
        entry = r["close"]
        exit_px, exit_type = None, None
        for kk in range(1, 21):
            jj = j + kk
            if jj >= len(series): break
            if series[jj]["close"] <= entry * 0.92:
                exit_px, exit_type = series[jj]["close"], "S5"
                break
        if exit_px is None:
            jj = min(j + 20, len(series) - 1)
            exit_px, exit_type = series[jj]["close"], "HOLD20"
        ret = (exit_px - entry) / entry - COST
        return {"hit": hit, "ret": ret, "exit_type": exit_type, "left": left,
                "entry_date": r["date"], "entry_price": entry}
    return None

def run_book(tag, pat):
    evs = load_events(pat)
    qfq = load_qfq(sorted({e["code"] for e in evs}))
    agg = {N: {"A": {"ret": [], "s5": 0, "n": 0}, "B": {"ret": [], "s5": 0, "n": 0},
               "left": []} for N in LOOKBACKS}
    for ev in evs:
        series = qfq.get(ev["code"])
        if not series: continue
        for N in LOOKBACKS:
            for a in ("A", "B"):
                res = simulate_reentry(ev, series, N, a)
                if not res: continue
                agg[N][a]["n"] += 1
                agg[N][a]["ret"].append(res["ret"])
                if res["exit_type"] == "S5":
                    agg[N][a]["s5"] += 1
            # 左桌只收一次
    # 左桌单独收集（N=20 口径，每事件一次）
    left_all = defaultdict(list)
    for ev in evs:
        series = qfq.get(ev["code"])
        if not series: continue
        idx = [i for i, r in enumerate(series) if r["date"] <= ev["stop_date"]]
        if not idx: continue
        i0 = idx[-1]
        for k in (5, 10, 20):
            j = i0 + k
            if j < len(series):
                left_all[k].append((series[j]["close"] - ev["stop_price"]) / ev["stop_price"])
    return agg, left_all, len(evs)

def main():
    books = [
        ("ML20(短线)", r"D:\zcodesj\reports\round10_adopted_combo_20260827\ml_hold20_pitv2_trades_20*.csv"),
        ("TOP15",      r"D:\zcodesj\reports\round10_adopted_combo_20260827\bp_ep_top15_pitv2_trades_20*.csv"),
    ]
    md = ["# K1 重入分歧 · 成本建模重估（真实交易模拟）", "",
          f"> **日期**：2026-08-28 | **性质**：K1-ML20 分歧复核（prereg 20260828-k1k2-crossval 承诺的第 2 步）",
          f"> **口径**：触发日收盘进场 → S-5(≤0.92×进场价)实际退出 或 持有至+20d 收盘退出；往返成本 {COST*100:.2f}%（round10 实测 0.26%）",
          "> **判据**：重入实现净收益（扣成本）> 0（现金基线）且相对永不重入有增量，才有讨论价值。", ""]
    for tag, pat in books:
        agg, left_all, n_ev = run_book(tag, pat)
        md.append(f"## {tag}（止损事件 {n_ev} 笔）")
        md.append("")
        md.append("### 永不重入参考（股票 +N 日标记，扣成本前）")
        md.append("")
        md.append("| 前向 | n | 均值 | 中位 | 胜率 |")
        md.append("|---|---|---|---|---|")
        for k in (5, 10, 20):
            a = left_all[k]
            md.append(f"| +{k}d | {len(a)} | {statistics.mean(a)*100:+.2f}% | {statistics.median(a)*100:+.2f}% | "
                      f"{sum(1 for x in a if x>0)/len(a)*100:.1f}% |")
        md.append("")
        md.append("### 重入真实交易模拟（扣 0.26% 成本）")
        md.append("")
        md.append("| N 窗口 | 触发 | 命中 | 实现均值(净) | 实现中位(净) | 胜率 | S5二次止损率 |")
        md.append("|---|---|---|---|---|---|---|")
        for N in LOOKBACKS:
            for a in ("A", "B"):
                d = agg[N][a]
                if not d["n"]:
                    md.append(f"| {N} | {a} | 0 | - | - | - | - |")
                    continue
                md.append(f"| {N} | {a} | {d['n']} | {statistics.mean(d['ret'])*100:+.2f}% | "
                          f"{statistics.median(d['ret'])*100:+.2f}% | "
                          f"{sum(1 for x in d['ret'] if x>0)/d['n']*100:.1f}% | {d['s5']/d['n']*100:.1f}% |")
        md.append("")
    md.append("## 判读（按 prereg 承诺）")
    md.append("- ML20 上重入 A 实现净收益（扣成本后）是否仍 > 0 且 > 永不重入参考？")
    md.append("- 二次止损率（S5 占比）是真实实现的成本，不是纸面标记——28% 意味着约三成重入会再吃 -8%。")
    md.append("- 若扣成本后净收益转负或与现金基线无差异 → 分歧关闭（并入 BT-FAIL-007 家族）；若仍显著为正 → 分歧成立，上报主人按独立 prereg+WFA 走。")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[K1-COST] 报告已写: {REPORT}")

if __name__ == "__main__":
    main()
