# -*- coding: utf-8 -*-
"""
kell_exit_anchor_backtest_20260828.py — K2 卖出锚升级·退出规则重放（研究）
============================================================
问题：R5=MA5 卖出锚过敏感（8/25-8/27 实证踏空主源）；更慢锚（MA10/MA20/双锚）是否提升持有期期望？
方法：对 round9 已成交 bp_ep 逐笔交易（1030 笔, 2016-2026, qfq, next-bar, -8% 止损模型）重放退出锚，
  选股不变、只换"卖出锚"这一个变量 → 干净隔离锚效应。这是方向性证据，不是验收线。

退出锚（全部叠加 S-5 硬止损=-8%从entry价, 全部遵守 T+1 次日才可卖, 跌停日不可成交顺延）：
  ma5   当前 R5 语义的等效重放（收盘跌破 MA5 → 清仓）
  ma10  K2-C1 单锚（破 MA10 清仓）
  ma20  K2-C2 单锚（破 MA20 清仓）
  dual  K2-C3 双锚（破 MA10 减半 + 破 MA20 清仓，Kell 式）
  orig  原回测实际退出（月频调仓 rebalance / -8% stop / 样本末）——真实基线
上限：entry 后 500 交易日未触发 → OPEN@cap 结算（cap 收盘价），报告中如实标注 open 占比。
成本：不含滑点/交易成本（相对比较，各锚成本方向同向）；0.995 贴现纪律会整体劣化所有锚，方向一致。
"""
import csv, os, sqlite3, statistics, sys, time
from collections import Counter, defaultdict

TRADES_CSV = r"D:\zcodesj\reports\bp_ep_monthly_production\bp_ep_monthly_trades.csv"
QFQ_DB    = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT    = r"D:\zcodesj\reports\K2_EXIT_ANCHOR_BACKTEST_20260828.md"
CAP       = 500          # 持仓上限交易日（~2年）

def load_trades(path=None):
    """读逐笔交易 CSV（兼容英文列 bp_ep_monthly 与中文列 round10）。
    path 语义：含 * 通配 → 直接 glob；目录 → *_trades_20*.csv；否则单文件。"""
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
    rows = []
    for fp in files:
        with open(fp, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            if "exit_reason" in cols:
                for r in reader:
                    rows.append({
                        "code": r["symbol"].strip().zfill(6),
                        "entry_date": r["entry_date"].replace("-", ""),
                        "entry_price": float(r["entry_price"]),
                        "orig_pnl": float(r["pnl_pct"]),
                        "exit_reason": r["exit_reason"],
                    })
            else:
                for r in reader:
                    rows.append({
                        "code": r["股票代码"].strip().zfill(6),
                        "entry_date": r["买入日期"].replace("-", ""),
                        "entry_price": float(r["买入价格"]),
                        "orig_pnl": float(r["盈亏比例%"]),
                        "exit_reason": r["卖出触发理由"],
                    })
    return rows

def load_qfq(codes):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT code, date, high, low, close, change_pct FROM stock_daily_meta_qfq "
        "WHERE code IN (%s) AND date >= '20150101' ORDER BY code, date" % ",".join("?" * len(codes)),
        codes).fetchall()
    con.close()
    d = {}
    for code, date, hi, lo, close, chg in rows:
        d.setdefault(code, []).append({"date": date, "close": close, "change_pct": chg})
    return d

def limit_down(chg, code):
    if chg is None: return False
    thr = -19.8 if code[:2] in ("30", "68") else -9.8
    return chg <= thr

def ma_arrays(series, k):
    out = [None] * len(series)
    s = 0.0
    for i, r in enumerate(series):
        s += r["close"]
        if i >= k: s -= series[i-k]["close"]
        out[i] = s / k if (i + 1) >= k else None
    return out

def run_one(series, i_entry, entry_px, anchor, ma_cache, code):
    exits = []
    remaining = 1.0
    halved = False
    n = len(series)
    cap_end = min(i_entry + 1 + CAP, n)
    for j in range(i_entry + 1, cap_end):
        r = series[j]
        # S-5 硬止损（所有锚统一）
        if r["close"] <= entry_px * 0.92:
            exits.append((r["date"], r["close"], remaining, "S5"))
            remaining = 0.0
            break
        ma5, ma10, ma20 = ma_cache["ma5"][j], ma_cache["ma10"][j], ma_cache["ma20"][j]
        if anchor == "ma5":
            cond = ma5 is not None and r["close"] < ma5
        elif anchor == "ma10":
            cond = ma10 is not None and r["close"] < ma10
        elif anchor == "ma20":
            cond = ma20 is not None and r["close"] < ma20
        else:  # dual
            if not halved and ma10 is not None and r["close"] < ma10:
                exits.append((r["date"], r["close"], 0.5, "MA10半"))
                halved = True
                remaining = 0.5
                if ma20 is not None and r["close"] < ma20:
                    exits.append((r["date"], r["close"], remaining, "MA20"))
                    remaining = 0.0
                    break
                continue
            cond = ma20 is not None and r["close"] < ma20
        if cond:
            if limit_down(r["change_pct"], code):
                continue          # 跌停不可成交，顺延
            exits.append((r["date"], r["close"], remaining, anchor))
            remaining = 0.0
            break
    if remaining > 0:
        jcap = cap_end - 1
        exits.append((series[jcap]["date"], series[jcap]["close"], remaining, "OPEN@cap"))
        open_flag = True
    else:
        open_flag = False
    pnl = sum(frac * (px - entry_px) / entry_px for _d, px, frac, _r in exits)
    return pnl, exits, open_flag

def main():
    t0 = time.time()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="", help="逐笔交易 CSV/目录/glob（默认 round9 bp_ep_monthly 主样本）")
    ap.add_argument("--tag", default="", help="报告标签（如 TOP15_PITV2 / ML20）")
    aa = ap.parse_args()
    tag = aa.tag or "MAIN"
    trades = load_trades(aa.csv or None)
    codes = sorted({t["code"] for t in trades})
    qfq = load_qfq(codes)
    print(f"[K2] 事件源={aa.csv or TRADES_CSV} 交易 {len(trades)} 笔, qfq {len(qfq)} 只, {time.time()-t0:.0f}s")

    # 预计算 MA 数组 + entry 索引
    anchors = ("orig", "ma5", "ma10", "ma20", "dual")
    # result[anchor] = {subset: {pnl:[], exits:Counter, open:0}}
    res = {a: {"full": {"pnl": [], "exits": Counter(), "open": 0},
               "reb":  {"pnl": [], "exits": Counter(), "open": 0}} for a in anchors}

    for t in trades:
        series = qfq.get(t["code"])
        if not series:
            continue
        idx = [i for i, r in enumerate(series) if r["date"] == t["entry_date"]]
        if not idx:
            # entry 日停牌等, 取最近前一日
            idx = [i for i, r in enumerate(series) if r["date"] <= t["entry_date"]]
        if not idx:
            continue
        i_entry = idx[-1]
        ma_cache = {k: ma_arrays(series, {"ma5": 5, "ma10": 10, "ma20": 20}[k]) for k in ("ma5", "ma10", "ma20")}
        is_reb = t["exit_reason"] == "rebalance"
        for a in anchors:
            if a == "orig":
                pnl = t["orig_pnl"] / 100.0   # CSV 已是百分数, 统一为分数与锚对齐
                exits = [("", 0, 1, t["exit_reason"])]
                open_flag = False
            else:
                # 成本基差用当前 qfq 入场日收盘（自洽于当前 qfq 时点，避免与 round9 老快照的复权基准错位）
                pnl, exits, open_flag = run_one(series, i_entry, series[i_entry]["close"], a, ma_cache, t["code"])
            for subset in ("full", "reb"):
                if subset == "reb" and not is_reb:
                    continue
                res[a][subset]["pnl"].append(pnl)
                res[a][subset]["exits"].update(e[3] for e in exits)
                if open_flag:
                    res[a][subset]["open"] += 1
    print(f"[K2] 完成 {time.time()-t0:.0f}s")

    md = []
    md.append(f"# K2 卖出锚升级 · 退出规则重放（bp_ep {tag}, 2016-2026）")
    md.append("")
    md.append("> **日期**：2026-08-28 | **性质**：K 计划 K2 研究（方向性证据，非验收线）")
    md.append(f"> **方法**：已成交交易（事件源 {aa.csv or TRADES_CSV}，{len(trades)} 笔）选股不变、只换退出锚；S-5 硬止损(-8%)对所有锚统一；T+1 次日可卖、跌停日顺延；持仓上限 {CAP} 交易日(OPEN@cap 结算)")
    md.append("> **诚实声明**：不含滑点/成本（相对比较方向一致）；止盈/月频调仓语义仅在 orig 基线体现；锚效应为同批持仓下的相对差异")
    md.append("")
    md.append("## 全样本（1030 笔，含 294 笔 -8% 止损：各锚在该子集表现相同）")
    md.append("")
    md.append("| 锚 | n | 均值% | p25% | 中位% | p75% | 胜率 | S5止损 | OPEN | 退出分布 |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for a in anchors:
        d = res[a]["full"]
        if not d["pnl"]: continue
        pn = sorted(d["pnl"])
        ex = ", ".join(f"{k}:{v}" for k, v in d["exits"].most_common(4))
        q = lambda p: pn[min(len(pn)-1, int(len(pn)*p))]
        md.append(f"| {a} | {len(pn)} | {statistics.mean(pn)*100:+.2f} | {q(0.25)*100:+.2f} | {statistics.median(pn)*100:+.2f} | {q(0.75)*100:+.2f} | "
                  f"{sum(1 for x in pn if x>0)/len(pn)*100:.1f}% | {d['exits'].get('S5',0)} | {d['open']} | {ex} |")
    md.append("")
    md.append("## rebalance 子集（717 笔，锚真正作用的持仓；orig 基线=月频调仓退出）")
    md.append("")
    md.append("| 锚 | n | 均值% | p25% | 中位% | p75% | 胜率 | S5止损 | OPEN | 退出分布 |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for a in anchors:
        d = res[a]["reb"]
        if not d["pnl"]: continue
        pn = sorted(d["pnl"])
        ex = ", ".join(f"{k}:{v}" for k, v in d["exits"].most_common(4))
        q = lambda p: pn[min(len(pn)-1, int(len(pn)*p))]
        md.append(f"| {a} | {len(pn)} | {statistics.mean(pn)*100:+.2f} | {q(0.25)*100:+.2f} | {statistics.median(pn)*100:+.2f} | {q(0.75)*100:+.2f} | "
                  f"{sum(1 for x in pn if x>0)/len(pn)*100:.1f}% | {d['exits'].get('S5',0)} | {d['open']} | {ex} |")
    md.append("")
    md.append("## 判读（按计划书 K2 §4.7）")
    md.append("- rebalance 子集上：慢锚（ma10/ma20/dual）相对 ma5 是否提升均值/胜率？是否出现「回吐权衡」（持有更久=单笔利润回吐增加）？")
    md.append("- 与 orig（真实月频调仓基线）对比：live R5 若改为某锚，能否接近/超过回测自然退出？")
    md.append("- 若慢锚在 rebalance 子集显著优于 ma5 且不劣于 orig → K2-Cx 候选进 prereg+WFA；否则归档（铁律8/计划书§4.7）。")
    md.append("- 反向风险：慢锚在 A 股高波动下可能放大 -8% 内回撤（S-5 兜底仍有效）；dual 的减半语义在生产需「卖多少问主人」流程适配。")
    out = REPORT.replace("ANCHOR_BACKTEST", f"ANCHOR_BACKTEST_{tag}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    # 控制台快速摘要
    for subset in ("reb",):
        print(f"== {subset} ==")
        for a in anchors:
            d = res[a][subset]
            if d["pnl"]:
                print(f"  {a:5s} n={len(d['pnl'])} mean={statistics.mean(d['pnl'])*100:+.2f}% med={statistics.median(d['pnl'])*100:+.2f}% win={sum(1 for x in d['pnl'] if x>0)/len(d['pnl'])*100:.1f}% open={d['open']}")
    print(f"[K2] 报告已写: {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
