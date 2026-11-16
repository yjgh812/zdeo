# -*- coding: utf-8 -*-
"""
kell_reentry_full_history_20260828.py — K1 止损后重入 · 全史矩阵回放（主样本）
============================================================
事件源：round9 回测导出的 bp_ep 逐笔交易（reports/bp_ep_monthly_production/bp_ep_monthly_trades.csv），
  exit_reason == 'stop_loss' 的 294 笔止损事件（2016-2026, qfq 库, next-bar, -8% 止损模型）。
对每笔止损事件回放三种重入触发（A 收复止损日高点+量 / B 站回MA10且MA10>MA20 / C 20日新高），
含 A股约束（涨停封板不追、T+1 次日才可卖），度量：
  1) 重入触发命中率（按 N 窗口 5/10/20 日）
  2) 重入后前向 5/10/20 日收益（每事件首触发进场）
  3) 永不重入基线 = 左桌收益（止损价前向）
  4) 重入后再次触发 -8% 的比例（二次送止损风险）

多重检验处理（8/25 WFA 学习档案准则）：N×触发 全组合全报，禁止只报最优格；无参数调优。
对应计划书 K1 §3.4-3.5；本结果满足样本>30 统计门槛，可进入 prereg/WFA 门禁评审。
"""
import csv, os, sqlite3, statistics, re, sys, time
from collections import defaultdict

TRADES_CSV = r"D:\zcodesj\reports\bp_ep_monthly_production\bp_ep_monthly_trades.csv"
QFQ_DB    = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT    = r"D:\zcodesj\reports\K1_REENTRY_REPLAY_FULL_20260828.md"
LOOKBACKS = (5, 10, 20)

def load_events(path=None):
    """读逐笔交易 CSV（兼容英文列 bp_ep_monthly 与中文列 round10）。
    path 语义：含 * 的通配 → 直接 glob；目录 → 该目录下 *_trades_20*.csv（按年文件，避免与 _full 重复）；
    否则视为单文件。取 exit_reason=='stop_loss' 事件。"""
    import glob as _glob
    path = path or TRADES_CSV
    if "*" in path or "?" in path:
        files = sorted(_glob.glob(path))
    elif path.endswith(("/", "\\")):
        files = sorted(_glob.glob(path + "*trades_20*.csv"))
    elif os.path.isdir(path):
        files = sorted(_glob.glob(os.path.join(path, "*trades_20*.csv")))
    else:
        files = [path]
    evs = []
    for fp in files:
        with open(fp, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            if "exit_reason" in cols:
                def gr(r):
                    return {"code": r["symbol"].strip().zfill(6), "name": r.get("symbol_name", ""),
                            "stop_date": r["exit_date"].replace("-", ""), "stop_price": float(r["exit_price"]),
                            "entry_date": r["entry_date"].replace("-", ""), "entry_price": float(r["entry_price"])}
            else:  # 中文列
                def gr(r):
                    return {"code": r["股票代码"].strip().zfill(6), "name": r.get("股票名称", ""),
                            "stop_date": r["卖出日期"].replace("-", ""), "stop_price": float(r["卖出价格"]),
                            "entry_date": r["买入日期"].replace("-", ""), "entry_price": float(r["买入价格"])}
            for r in reader:
                reason = (r.get("exit_reason") or r.get("卖出触发理由") or "").strip()
                if reason == "stop_loss":
                    evs.append(gr(r))
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

def limit_up(chg_pct, code):
    if chg_pct is None: return False
    thr = 19.8 if code[:2] in ("30", "68") else 9.8
    return chg_pct >= thr

def ma_k(series, i, k):
    if i + 1 < k: return None
    return sum(r["close"] for r in series[i+1-k:i+1]) / k

def replay_event(ev, series, lookback):
    """返回 (触发命中?, {fwd5,fwd10,fwd20}, 左桌{fwd}, 再止损?) 或 None(数据不足)。"""
    idx = [i for i, r in enumerate(series) if r["date"] <= ev["stop_date"]]
    if not idx:
        return None
    i0 = idx[-1]
    if i0 >= len(series) - 1:
        return None
    ref_high = series[i0]["high"]
    left = {}
    for k in (5, 10, 20):
        j = i0 + k
        if j < len(series):
            left[k] = (series[j]["close"] - ev["stop_price"]) / ev["stop_price"]
    for k in range(1, lookback + 1):
        j = i0 + k
        if j >= len(series):
            break
        r = series[j]
        if limit_up(r["change_pct"], ev["code"]):
            continue
        vol_ratio = None
        base = [series[j-t]["volume"] for t in range(1, 6) if j-t >= 0 and series[j-t]["volume"]]
        if base and r["volume"]:
            vol_ratio = r["volume"] / (sum(base) / len(base))
        ma10, ma20 = ma_k(series, j, 10), ma_k(series, j, 20)
        high20 = max(rr["high"] for rr in series[max(0, j-19):j+1])
        hit = None
        if vol_ratio is not None and vol_ratio >= 1.5 and r["close"] >= ref_high:
            hit = "A"
        elif ma10 is not None and ma20 is not None and r["close"] > ma10 and ma10 > ma20:
            hit = "B"
        elif r["close"] > max(rr["close"] for rr in series[max(0, j-20):j]):
            hit = "C"   # 收盘创前20日收盘新高（原版误用 high 对比, 0命中, 已修）
        if hit:
            entry = r["close"]
            fwd = {}
            restop = False
            for kk in range(1, lookback + 1):
                jj = j + kk
                if jj < len(series):
                    if kk in (5, 10, 20):
                        fwd[kk] = (series[jj]["close"] - entry) / entry
                    if series[jj]["close"] <= entry * 0.92:
                        restop = True
            return {"hit": hit, "fwd": fwd, "left": left, "restop": restop,
                    "entry_date": r["date"], "entry_price": entry}
    return {"hit": None, "fwd": {}, "left": left, "restop": False}

def main():
    t0 = time.time()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="", help="逐笔交易 CSV（默认 round9 bp_ep_monthly 主样本）")
    ap.add_argument("--tag", default="", help="报告标签（如 TOP15_PITV2）")
    aa = ap.parse_args()
    tag = aa.tag or "MAIN"
    evs = load_events(aa.csv or None)
    print(f"[K1-FULL] 事件源={aa.csv or TRADES_CSV} 止损事件 {len(evs)} 笔")
    qfq = load_qfq(sorted({e["code"] for e in evs}))
    print(f"[K1-FULL] qfq 加载 {len(qfq)} 只, 用时 {time.time()-t0:.0f}s")

    # agg[N][trigger] -> (hit_n, fwd5[], fwd10[], fwd20[], restop_n)
    agg = {N: {"A": defaultdict(list), "B": defaultdict(list), "C": defaultdict(list),
               "left": defaultdict(list)} for N in LOOKBACKS}
    left_all = defaultdict(list)
    hit_by = defaultdict(int)
    n_eval = 0
    for ev in evs:
        series = qfq.get(ev["code"], [])
        if not series:
            continue
        n_eval += 1
        for N in LOOKBACKS:
            res = replay_event(ev, series, N)
            if not res:
                continue
            if N == 5:   # 左桌基线按事件收集一次（N=5 时采集，避免 3× 重复计数）
                for k, v in res["left"].items():
                    left_all[k].append(v)
            if res["hit"]:
                agg[N][res["hit"]]["hit_n"] = agg[N][res["hit"]].get("hit_n", 0) + 1
                for k in (5, 10, 20):
                    if k in res["fwd"]:
                        agg[N][res["hit"]][f"f{k}"].append(res["fwd"][k])
                if res["restop"]:
                    agg[N][res["hit"]]["restop_n"] = agg[N][res["hit"]].get("restop_n", 0) + 1
                if N == 5:
                    hit_by[res["hit"]] += 1

    print(f"[K1-FULL] 有效事件 {n_eval}, 完成用时 {time.time()-t0:.0f}s")

    def fmt(v, pct=True, d=3):
        if v is None: return "N/A"
        return ("%+.3f%%" % (v*100)) if pct else ("%.1f%%" % (v*100))

    md = []
    md.append(f"# K1 止损后重入 · 全史矩阵回放（bp_ep {tag}, 2016-2026）")
    md.append("")
    md.append(f"> **日期**：2026-08-28 | **性质**：K 计划 K1 主样本/交叉验证 | **事件源**：{aa.csv or TRADES_CSV}")
    md.append(f"> **止损事件**：{len(evs)} 笔（-8% 止损模型, qfq, next-bar）")
    md.append(f"> **有效事件**：{n_eval}（qfq 可覆盖）| 三触发全矩阵全报，无参数调优")
    md.append("> **A股约束**：涨停封板日不触发（顺延）；重入后 -8% 记二次止损")
    md.append("")
    md.append("## 永不重入基线（左桌收益：止损价前向实际收盘，未扣可成交性）")
    md.append("")
    md.append("| 前向 | n | 均值 | 中位 | 胜率 |")
    md.append("|---|---|---|---|---|")
    for k in (5, 10, 20):
        a = left_all[k]
        if not a: continue
        md.append(f"| +{k}d | {len(a)} | {fmt(statistics.mean(a))} | {fmt(statistics.median(a))} | {sum(1 for x in a if x>0)/len(a)*100:.1f}% |")
    md.append("")
    md.append("## 重入触发命中率（N 日内首触发，N=5）")
    md.append("")
    md.append("| 触发 | 命中 | 命中率 |")
    md.append("|---|---|---|")
    for t in ("A", "B", "C"):
        md.append(f"| {t} | {hit_by[t]} | {hit_by[t]/max(n_eval,1)*100:.1f}% |")
    md.append("")
    md.append("## 重入后前向收益（按 N 窗口 × 触发，均值/中位；命中后从触发日收盘进场）")
    md.append("")
    for N in LOOKBACKS:
        md.append(f"### 窗口 N={N}")
        md.append("")
        md.append("| 触发 | 命中n | +5d均值 | +5d中位 | +10d均值 | +20d均值 | 二次止损率 |")
        md.append("|---|---|---|---|---|---|---|")
        for t in ("A", "B", "C"):
            d = agg[N][t]
            hn = d.get("hit_n", 0)
            if not hn:
                md.append(f"| {t} | 0 | - | - | - | - | - |")
                continue
            m5 = statistics.mean(d["f5"]) if d["f5"] else None
            md5 = statistics.median(d["f5"]) if d["f5"] else None
            m10 = statistics.mean(d["f10"]) if d["f10"] else None
            m20 = statistics.mean(d["f20"]) if d["f20"] else None
            rs = d.get("restop_n", 0) / hn * 100
            md.append(f"| {t} | {hn} | {fmt(m5)} | {fmt(md5)} | {fmt(m10)} | {fmt(m20)} | {rs:.1f}% |")
        md.append("")
    md.append("## 结论（按计划书 K1 §3.5 门禁）")
    md.append("- 判读：重入策略期望收益 vs 左桌基线（止损后原地等待）+ 二次止损率（whipsaw 成本）；与主样本(bp_ep_monthly, BT-FAIL-007 不立项)结论对比。")
    if tag != "MAIN":
        md.append(f"- **交叉验证（{tag}）**：与主样本一致（重入不跑赢基线）→ 强化 BT-FAIL-007 普适性；若出现正期望 → 先复核数据口径差异再上报主人（prereg 20260828-k1k2-crossval-top15pitv2，不得直接立项）。")
    md.append("- 反向风险（须全报）：重入命中率低（n 小）的格不判；二次止损率高的触发 = 震荡市双倍损耗。")
    tag = aa.tag or "MAIN"
    out_path = REPORT.replace("FULL", f"FULL_{tag}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[K1-FULL] 报告已写: {out_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
