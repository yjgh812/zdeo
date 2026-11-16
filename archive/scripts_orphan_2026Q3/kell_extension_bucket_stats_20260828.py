# -*- coding: utf-8 -*-
"""
kell_extension_bucket_stats_20260828.py — K3 快验证（P0 第一步，纯描述性）
============================================================
问题：买入时点相对 20 日均线的偏离度（ext20）与已实现收益的关系？
对应计划书 K3（买入偏离度统一约束）§5.4 快验证。

数据源（只读）：
  - trade_records / position_history : D:\\zcodesj\\attribution\\attribution.db
  - qfq 价格源                     : D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db
                                       (stock_daily_meta_qfq: code/date/open/high/low/close/volume/amount/change_pct)

输出：reports/K3_EXTENSION_BUCKET_QUICK_20260828.md（描述性统计，不构成验收线）

反臆想：样本窗口 7/22-8/27 很短，结论标注"描述性/低置信度"；本脚本只读，不写生产库。
"""
import sqlite3, statistics, datetime, re, sys

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB  = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT  = r"D:\zcodesj\reports\K3_EXTENSION_BUCKET_QUICK_20260828.md"

def norm(code):
    """sz000506 / sh600984 / 000506 → 000506"""
    m = re.match(r"([a-z]+)?(\d{6})", code.strip())
    return m.group(2) if m else code.strip()

def load_qfq():
    """code -> sorted [(date, close, volume, high, low), ...]"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq WHERE date >= '20260601' ORDER BY code, date").fetchall()
    con.close()
    d = {}
    for code, date, _o, hi, lo, close, vol in rows:
        d.setdefault(code, []).append((date, close, vol, hi, lo))
    return d

def ma20_ext(qfq_rows, on_date, px):
    """在 on_date（含）往前取至多 20 个交易日的收盘计算 MA20；返回 ext20, pos20, n"""
    series = [r for r in qfq_rows if r[0] <= on_date]
    win = series[-20:]
    if len(win) < 10:
        return None, None, len(win)
    closes = [r[1] for r in win]
    highs  = [r[3] for r in win]
    lows   = [r[4] for r in win]
    ma20 = sum(closes) / len(closes)
    ext20 = (px - ma20) / ma20
    hi, lo = max(highs), min(lows)
    pos20 = (px - lo) / (hi - lo) if hi > lo else None
    return ext20, pos20, len(win)

def main():
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    buys = con.execute(
        "SELECT code, name, trade_date, price, volume, strategy_version FROM trade_records WHERE action='BUY' ORDER BY trade_date").fetchall()
    closed = con.execute(
        "SELECT code, name, entry_date, entry_price, exit_date, exit_reason, pnl_pct, holding_days "
        "FROM position_history WHERE status='CLOSED' ORDER BY entry_date").fetchall()
    con.close()

    qfq = load_qfq()
    print(f"[K3] 加载 BUY {len(buys)} 条, CLOSED {len(closed)} 条, qfq 覆盖 {len(qfq)} 只")

    # ---- Stat A：全部买入的 ext20 分布 ----
    statA = []   # (code, name, buy_date, price, ext20, pos20, source)
    for code, name, tdate, price, _v, src in buys:
        d8 = tdate.replace("-", "")
        rows = qfq.get(norm(code), [])
        if not rows:
            continue
        ext20, pos20, n = ma20_ext(rows, d8, price)
        if ext20 is None:
            continue
        statA.append((code, name, d8, price, ext20, pos20, src))

    exts = [r[4] for r in statA]
    exts_sorted = sorted(exts)
    def pct(p): 
        return exts_sorted[min(len(exts_sorted)-1, int(len(exts_sorted)*p))]
    over = lambda x: sum(1 for e in exts if e > x)
    print(f"\n[Stat A] 可算 BUY n={len(statA)}")
    print(f"  ext20: min={min(exts):.4f} p25={pct(0.25):.4f} med={pct(0.5):.4f} p75={pct(0.75):.4f} max={max(exts):.4f} mean={statistics.mean(exts):.4f}")
    print(f"  >5%: {over(0.05)}  >10%: {over(0.10)}  >15%: {over(0.15)}  >20%: {over(0.20)}  >25%: {over(0.25)}")

    # ---- Stat B：已平仓按 ext20 分桶 vs pnl ----
    # 用 position_history 自身 entry_price 计算 ext20（与 trade_records 解耦，规避 E59 污染）
    buckets = {"<=0%": [], "0-10%": [], "10-20%": [], ">20%": []}
    undef = []
    for code, name, edate, eprice, xdate, reason, pnl, hold in closed:
        d8 = edate.replace("-", "")
        rows = qfq.get(norm(code), [])
        if not rows:
            undef.append((code, name, reason, pnl)); continue
        ext20, _pos, n = ma20_ext(rows, d8, eprice)
        if ext20 is None:
            undef.append((code, name, reason, pnl)); continue
        if ext20 <= 0:      buckets["<=0%"].append((pnl, hold, reason))
        elif ext20 <= 0.10: buckets["0-10%"].append((pnl, hold, reason))
        elif ext20 <= 0.20: buckets["10-20%"].append((pnl, hold, reason))
        else:               buckets[">20%"].append((pnl, hold, reason))

    print(f"\n[Stat B] 已平仓可算 n={sum(len(v) for v in buckets.values())}, 不可算(缺qfq/数据不足)={len(undef)}")
    lines = []
    for k, v in buckets.items():
        if not v:
            print(f"  {k}: n=0")
            continue
        pnls = [x[0] for x in v if x[0] is not None]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  {k}: n={len(v)} mean={statistics.mean(pnls):.2f}% med={statistics.median(pnls):.2f}% win={wins}/{len(pnls)}")

    # ---- 快验证补充：止损类退出的 ext20 前置画像（服务 K1）----
    stop_related = [x for x in closed if re.search(r"止损|S-5|破位|R5", str(x[5]))]
    stop_ext = []
    for code, name, edate, eprice, xdate, reason, pnl, hold in stop_related:
        rows = qfq.get(norm(code), [])
        if not rows: continue
        ext20, _pos, n = ma20_ext(rows, edate.replace("-",""), eprice)
        if ext20 is not None:
            stop_ext.append((code, name, edate, eprice, ext20, pnl, reason))
    print(f"\n[Stat C] 止损/破位类退出 n={len(stop_ext)}")
    for r in sorted(stop_ext, key=lambda x: x[4]):
        print(f"  {r[1]}({norm(r[0])}) 入场{r[2]} @{r[3]:.2f} ext20={r[4]:+.1%} 结果{r[5]:+.2f}% [{r[6]}]")

    # ---- 写报告 ----
    today = "2026-08-28"
    md = []
    md.append("# K3 快验证：买入偏离度 ext20 与已实现收益（描述性统计）")
    md.append("")
    md.append(f"> **日期**：{today} | **性质**：K 计划 K3 工作流 P0 第一步，纯描述性，不构成验收线")
    md.append("> **数据**：trade_records BUY %d 条 / position_history CLOSED %d 条（attribution.db）；qfq 价格源（qfq 库）" % (len(buys), len(closed)))
    md.append("> **口径**：ext20 = (入场价 − 20日均线) / 20日均线（用实际成交价，非收盘价）；pos20 = 20日箱体位置分位")
    md.append("> **反臆想**：样本窗口 2026-07-22 ~ 08-27 很短，结论仅描述性、低置信度；不推翻/不落地任何规则")
    md.append("")
    md.append("## Stat A：全部买入的 ext20 分布（我们通常追多高）")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    md.append(f"| 可算买入 n | {len(statA)} |")
    md.append(f"| min / p25 / 中位 / p75 / max | {min(exts):+.1%} / {pct(0.25):+.1%} / {pct(0.5):+.1%} / {pct(0.75):+.1%} / {max(exts):+.1%} |")
    md.append(f"| mean | {statistics.mean(exts):+.1%} |")
    md.append(f"| 买入时 ext20>5% / >10% / >15% / >20% / >25% 笔数 | {over(0.05)} / {over(0.10)} / {over(0.15)} / {over(0.20)} / {over(0.25)} |")
    md.append("")
    md.append("## Stat B：已平仓按入场 ext20 分桶 vs 已实现收益")
    md.append("")
    md.append("| 分桶 | n | 平均收益% | 中位收益% | 胜率 |")
    md.append("|---|---|---|---|---|")
    for k in ["<=0%", "0-10%", "10-20%", ">20%"]:
        v = buckets[k]
        if not v:
            md.append(f"| {k} | 0 | - | - | - |")
            continue
        pnls = [x[0] for x in v if x[0] is not None]
        wins = sum(1 for p in pnls if p > 0)
        md.append(f"| {k} | {len(v)} | {statistics.mean(pnls):+.2f} | {statistics.median(pnls):+.2f} | {wins}/{len(pnls)} |")
    md.append(f"| 不可算（缺qfq/窗口不足） | {len(undef)} | - | - | - |")
    md.append("")
    md.append("## Stat C：止损/破位类退出的入场 ext20 前置画像（服务 K1）")
    md.append("")
    md.append("| 股票 | 入场日 | 入场价 | ext20 | 结果 | 退出原因 |")
    md.append("|---|---|---|---|---|---|")
    for code, name, edate, eprice, ext20, pnl, reason in sorted(stop_ext, key=lambda x: x[4]):
        md.append(f"| {name}({norm(code)}) | {edate} | {eprice:.2f} | {ext20:+.1%} | {pnl:+.2f}% | {reason} |")
    md.append("")
    md.append("## 观察（描述性，非结论）")
    md.append("- 分桶单调性是否成立（高 ext20 桶收益是否更差）以本表为准；样本小，需 K3 全史回放（2016-2026 qfq）验证单调性。")
    md.append("- Stat C 供 K1 参考：止损票的入场 ext20 前置是否普遍偏高（追高→止损链条的定量线索）。")
    md.append("")
    md.append("## 下一步")
    md.append("- K3 全史回放：2016-2026 qfq 对 bp_ep 全史按 ext20 分桶前瞻 5/10/20 日收益（P0 第 4 步）。")
    md.append("- 单调性成立 → 议 pre_trade_check 闸门层 R30；不成立 → 归档不加规则。")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n[K3] 报告已写: {REPORT}")

if __name__ == "__main__":
    sys.exit(main())
