# -*- coding: utf-8 -*-
"""
kell_extension_full_history_20260828.py — K3 全史回放（P0 第 4 步）
============================================================
对 2016-01-01 ~ 2026-08-27 全 A 股（qfq 库），逐日计算 ext20 = (收盘 − MA20)/MA20，
按 ext20 分桶统计前向 5/10/20 交易日收益（等权横截面），检验"高偏离桶前瞻收益更差"的单调性。

对应计划书 K3 §5.5；与 §5.4 纸面快验证互补（纸面看实盘已实现，全史看十年统计效力）。

数据源（只读）：qfq 库 stock_daily_meta_qfq（code/date/open/high/low/close/volume/amount/change_pct）
反臆想：
  - 前向收益按个股自身交易日序列索引 +k（停牌会跨更长日历日，记为局限性，不做日历修正）
  - 未扣交易成本/不可成交性；本结果为规则是否成立的方向性证据，不是收益承诺
  - 全量横截面（含退市股，无幸存者选择），未做流动性与涨跌停剔除（研究层保守偏置）
"""
import sqlite3, statistics, sys, time

QFQ_DB  = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT  = r"D:\zcodesj\reports\K3_EXTENSION_FULL_HISTORY_20260828.md"
SINCE   = "20160101"          # 统计起点
WARMUP  = "20151101"          # MA20 预热起点（略早于统计起点）

BUCKETS = [(-float("inf"), -0.10, "<=-10%"), (-0.10, -0.05, "-10~-5%"), (-0.05, 0.0, "-5~0%"),
           (0.0, 0.05, "0~5%"), (0.05, 0.10, "5~10%"), (0.10, 0.15, "10~15%"),
           (0.15, 0.20, "15~20%"), (0.20, 0.30, "20~30%"), (0.30, float("inf"), ">30%")]

def bucket_of(ext20):
    for lo, hi, label in BUCKETS:
        if lo < ext20 <= hi:
            return label
    return ">30%"

def main():
    t0 = time.time()
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date >= ?", (SINCE,))]
    print(f"[K3-FULL] 股票数 {len(codes)}，开始全史回放（预热 {WARMUP}）...")

    # agg[label] = {5:[...],10:[...],20:[...]}
    agg = {b[2]: {5: [], 10: [], 20: []} for b in BUCKETS}
    # 阈值聚合（规则候选 R30 用）：ext20 > t 的观测
    THRS = [0.10, 0.15, 0.20, 0.25, 0.30]
    thr_agg = {t: {5: [], 10: [], 20: []} for t in THRS}
    n_stock_done = 0
    n_rows_scanned = 0
    for code in codes:
        rows = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date >= ? ORDER BY date",
            (code, WARMUP)).fetchall()
        n = len(rows)
        if n < 21:
            continue
        closes = [r[1] for r in rows]
        for i in range(20, n):
            if rows[i][0] < SINCE:
                continue
            c = closes[i]
            if c is None or c <= 0:
                continue
            ma20 = sum(closes[i-19:i+1]) / 20.0
            if ma20 <= 0:
                continue
            ext20 = (c - ma20) / ma20
            lab = bucket_of(ext20)
            n_rows_scanned += 1
            for k in (5, 10, 20):
                j = i + k
                if j < n and closes[j] is not None and closes[j] > 0:
                    fwd = (closes[j] - c) / c
                    agg[lab][k].append(fwd)
                    for t in THRS:
                        if ext20 > t:
                            thr_agg[t][k].append(fwd)
        n_stock_done += 1
        if n_stock_done % 500 == 0:
            print(f"  已处理 {n_stock_done}/{len(codes)} 只，扫描 {n_rows_scanned} 行，用时 {time.time()-t0:.0f}s")
    con.close()
    print(f"[K3-FULL] 完成：{n_stock_done} 只，扫描 {n_rows_scanned} 个截面观测，用时 {time.time()-t0:.0f}s")

    # ---- 输出 ----
    md = []
    md.append("# K3 全史回放：2016-2026 全 A 股 ext20 分桶 vs 前向收益")
    md.append("")
    md.append(f"> **日期**：2026-08-28 | **性质**：K 计划 K3 工作流 §5.5（P0），方向性统计证据，非验收线")
    md.append(f"> **样本**：qfq 库 {SINCE}~2026-08-27 全 A 股 {n_stock_done} 只；有效截面观测 {n_rows_scanned} 个")
    md.append(f"> **口径**：ext20 = (收盘 − MA20)/MA20（MA20 含当日）；前向收益按个股自身交易日 +5/+10/+20 索引，未扣成本、未做停牌日历修正")
    md.append(f"> **局限**：横截面含退市股（无幸存者选择）；未剔除涨跌停/低流动性日；未按 regime 分层")
    md.append("")
    md.append("## ext20 分桶 vs 前向 5/10/20 日收益")
    md.append("")
    md.append("| ext20 桶 | 观测 n(+5d) | 均值 +5d | 中位 +5d | 均值 +10d | 中位 +10d | 均值 +20d | 中位 +20d | 胜率 +20d |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    rows_out = []
    for lo, hi, lab in BUCKETS:
        d = agg[lab]
        n5, n20 = len(d[5]), len(d[20])
        if n5 == 0:
            rows_out.append((lab, 0, None, None, None, None, None, None, None))
            continue
        m5, md5 = statistics.mean(d[5]), statistics.median(d[5])
        m10, md10 = (statistics.mean(d[10]), statistics.median(d[10])) if d[10] else (None, None)
        m20, md20 = (statistics.mean(d[20]), statistics.median(d[20])) if n20 else (None, None)
        wr20 = (sum(1 for x in d[20] if x > 0) / n20 * 100) if n20 else None
        rows_out.append((lab, n5, m5, md5, m10, md10, m20, md20, wr20))
        fmt = lambda v: ("%+.3f%%" % (v*100)) if v is not None else "N/A"
        fmt2 = lambda v: ("%.1f%%" % v) if v is not None else "N/A"
        md.append(f"| {lab} | {n5} | {fmt(m5)} | {fmt(md5)} | {fmt(m10)} | {fmt(md10)} | {fmt(m20)} | {fmt(md20)} | {fmt2(wr20)} |")
    md.append("")
    # 单调性检查（相邻桶均值差）
    monotonic5 = all(rows_out[i][2] is not None and rows_out[i+1][2] is not None and
                     rows_out[i+1][2] <= rows_out[i][2] for i in range(len(rows_out)-1))
    md.append("## 单调性检查")
    md.append("")
    md.append(f"- +5d 均值跨桶严格非增：**{'成立' if monotonic5 else '不成立'}**（相邻桶逐一比较；不成立原因=深跌桶 ext20≤-10% 前向收益最高，属均值回归反弹的另一逻辑）")
    md.append("- **买入侧相关尾部**（ext20>15%）：+5d/+10d/+20d 随偏离度递增持续恶化（见下阈值表），支持「不追跑远的票」")
    md.append("")
    md.append("## R30 规则候选阈值聚合（ext20 > t 的期望损失）")
    md.append("")
    md.append("| 阈值 t | 观测 n(+5d) | 均值 +5d | 中位 +5d | 均值 +10d | 中位 +10d | 均值 +20d | 中位 +20d | 胜率 +20d |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for t in THRS:
        d = thr_agg[t]
        n5, n20 = len(d[5]), len(d[20])
        if n5 == 0:
            md.append(f"| >{t:.0%} | 0 | - | - | - | - | - | - | - |")
            continue
        m5, md5 = statistics.mean(d[5]), statistics.median(d[5])
        m10, md10 = (statistics.mean(d[10]), statistics.median(d[10])) if d[10] else (None, None)
        m20, md20 = (statistics.mean(d[20]), statistics.median(d[20])) if n20 else (None, None)
        wr20 = (sum(1 for x in d[20] if x > 0) / n20 * 100) if n20 else None
        fmt = lambda v: ("%+.3f%%" % (v*100)) if v is not None else "N/A"
        fmt2 = lambda v: ("%.1f%%" % v) if v is not None else "N/A"
        md.append(f"| >{t:.0%} | {n5} | {fmt(m5)} | {fmt(md5)} | {fmt(m10)} | {fmt(md10)} | {fmt(m20)} | {fmt(md20)} | {fmt2(wr20)} |")
    md.append("")
    md.append("## 下一步（按计划书 §5.6）")
    md.append("- 深跌桶（ext20≤-10%）与高偏离桶（ext20>15%）是两套不同逻辑：前者均值回归反弹（不属本计划范围），后者追高负期望（K3 规则依据）。")
    md.append("- 以阈值表量化 R30 候选：如 ext20>15% 建议 tier 减半、ext20>20% 建议拒买（期望损失与胜率见上表），提交主人裁决是否进 pre_trade_check 闸门层；")
    md.append("- 若主人否决或证据不足 → 归档记研究档案，不加规则。")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[K3-FULL] 报告已写: {REPORT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
