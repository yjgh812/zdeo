#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regress_s11_pit_20260815.py — s11通道PIT修复回归验证
=====================================================
验证三件事:
  R1 旧s11逐位不变: 用"修复前同款"期末as-of逻辑(bisect_right(rd,d)-1)独立重算
     历史日期的s11, 与库内 s11_financial 逐位比对(允许NULL行单列统计)。
     同时比对修复前/后快照(COUNT/SUM)证明旧列未被写入。
  R2 新s11_pit用披露截止口径: 用 bisect_right(pit_avail_date, d)-1 独立重算,
     与库内 s11_pit(分段法回填) 逐位比对 —— 两种独立实现互证。
     并抽样展示 旧口径所选报告期 vs PIT所选报告期 的披露截止语义。
  R3 边界语义: PIT所选报告的 pit_avail_date <= d 恒成立; 前视窗口日(如年报
     披露截止前的04-15)旧口径用到尚未合法披露的期次, PIT口径回退到已披露期次。

抽样日期: 20250415(年报+Q1双前视窗), 20250115(年报前视窗), 20250715(中报前视窗),
          20240615(对照:应一致), 20260415(对照:应一致)。
用法: python regress_s11_pit_20260815.py
"""
import bisect
import json
import sqlite3
import sys

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
SNAP = r"D:\zcodesj\_s11_pre_snapshot_20260815.json"
DATES = [20250415, 20250115, 20250715, 20240615, 20260415]


def score11(roe, pe, pb, dr, rg, pg):
    """与 tulong_daily_update.py Phase3 打分规则逐条相同。"""
    score = 0
    if roe is not None and roe > 15: score += 0.3
    elif roe is not None and roe > 8: score += 0.15
    elif roe is not None and roe < 0: score -= 0.3
    if pe is not None and 5 <= pe <= 30: score += 0.25
    elif pe is not None and pe > 100: score -= 0.2
    if pb is not None and 0 < pb < 3: score += 0.2
    elif pb is not None and pb > 10: score -= 0.15
    if rg is not None and rg > 20: score += 0.15
    if pg is not None and pg > 20: score += 0.15
    if dr is not None and 0 < dr < 60: score += 0.1
    return max(-1, min(1, score))


def main():
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    c = con.cursor()

    # 预加载(与Phase3同款过滤)
    c.execute("""
        SELECT code, report_date, pit_avail_date, roe, pe, pb, debt_ratio, revenue_growth, profit_growth
        FROM stock_financial
        WHERE roe IS NOT NULL OR pe IS NOT NULL OR pb IS NOT NULL
        ORDER BY code, report_date
    """)
    fin = {}
    for code, rd, pit, roe, pe, pb, dr, rg, pg in c.fetchall():
        if rd is None:
            continue
        fin.setdefault(code, []).append(
            (int(rd), int(pit) if pit is not None else None, roe, pe, pb, dr, rg, pg))
    for code in fin:
        fin[code].sort(key=lambda x: x[0])
    print(f"预加载 stock_financial: {len(fin):,} 只代码")

    all_ok = True
    for d in DATES:
        rows = c.execute(
            "SELECT code, s11_financial, s11_pit FROM stock_signals WHERE date=?", (d,)).fetchall()
        if not rows:
            print(f"[{d}] 无信号行, 跳过")
            continue
        n = len(rows)
        old_mismatch = pit_mismatch = null_old = 0
        old_eq_pit = differ = 0
        pit_pick_violation = 0
        samples = []
        for code, s11_db, s11pit_db in rows:
            arr = fin.get(code)
            s11_re = s11pit_re = 0.0
            rd_old = rd_pit = None
            if arr:
                rds = [x[0] for x in arr]
                pits = [x[1] for x in arr]
                i = bisect.bisect_right(rds, d) - 1        # 旧口径: 期末as-of
                if i >= 0:
                    rd_old = rds[i]
                    _, _, roe, pe, pb, dr, rg, pg = arr[i]
                    s11_re = score11(roe, pe, pb, dr, rg, pg)
                j = bisect.bisect_right(pits, d) - 1       # 新口径: 披露截止as-of
                if j >= 0:
                    rd_pit = rds[j]
                    if pits[j] > d:
                        pit_pick_violation += 1            # R3: 不可能发生, 防御断言
                    _, _, roe, pe, pb, dr, rg, pg = arr[j]
                    s11pit_re = score11(roe, pe, pb, dr, rg, pg)
            if s11_db is None:
                null_old += 1
            elif s11_db != s11_re:
                old_mismatch += 1
            if s11pit_db is None or s11pit_db != s11pit_re:
                pit_mismatch += 1
            if s11_db is not None:
                if s11_db == s11pit_db:
                    old_eq_pit += 1
                else:
                    differ += 1
            if len(samples) < 3 and rd_old != rd_pit and rd_old and rd_pit:
                pit_j = pits[rds.index(rd_pit)] if rd_pit in rds else None
                samples.append((code, rd_old, rd_pit, pit_j, s11_db, s11pit_db))
        ok = (old_mismatch == 0 and pit_mismatch == 0 and pit_pick_violation == 0)
        all_ok &= ok
        status = "PASS" if ok else "FAIL"
        print(f"\n[{d}] {status}  行数={n:,}")
        print(f"  R1 旧口径重算 vs s11_financial: 不一致 {old_mismatch} (NULL {null_old}) -> "
              f"{'旧值逐位不变' if old_mismatch == 0 else '!!!逻辑漂移'}")
        print(f"  R2 PIT重算 vs s11_pit:          不一致 {pit_mismatch} -> "
              f"{'披露截止口径成立' if pit_mismatch == 0 else '!!!回填错误'}")
        print(f"  R3 PIT所选期披露截止<=当日 违例: {pit_pick_violation}")
        print(f"  旧==PIT {old_eq_pit:,} | 旧≠PIT {differ:,} "
              f"({differ / max(old_eq_pit + differ, 1) * 100:.1f}% 行受前视影响)")
        for code, rdo, rdp, pj, sv, pv in samples:
            print(f"    样例 {code}: 旧用报告期{rdo} -> PIT用{rdp}(截止{pj}) | s11 {sv} -> s11_pit {pv}")

    # R1b: 旧列快照不变
    print("\n[R1b] 旧列写入侵害检查(修复前快照 vs 当前):")
    pre = json.load(open(SNAP))
    cur = {
        "signals_total": c.execute("SELECT COUNT(*) FROM stock_signals").fetchone()[0],
        "s11_cnt": c.execute("SELECT COUNT(s11_financial) FROM stock_signals").fetchone()[0],
        "s11_sum": c.execute("SELECT TOTAL(s11_financial) FROM stock_signals").fetchone()[0],
        "comp_cnt": c.execute("SELECT COUNT(composite_score) FROM stock_signals").fetchone()[0],
        "comp_sum": c.execute("SELECT TOTAL(composite_score) FROM stock_signals").fetchone()[0],
        "fin_total": c.execute("SELECT COUNT(*) FROM stock_financial").fetchone()[0],
    }
    for k in ("s11_cnt", "s11_sum", "comp_cnt", "comp_sum", "fin_total"):
        same = pre[k] == cur[k]
        all_ok &= same
        print(f"  {k}: pre={pre[k]} cur={cur[k]} {'OK' if same else '!!!被改动'}")
    print(f"  (signals_total pre={pre['signals_total']} cur={cur['signals_total']}, "
          f"差值来自回填后新交易日写入, 非旧列改写)")

    # 全库统计
    diff = c.execute("SELECT COUNT(*) FROM stock_signals "
                     "WHERE s11_financial IS NOT NULL AND s11_financial <> s11_pit").fetchone()[0]
    s11n = c.execute("SELECT COUNT(s11_financial) FROM stock_signals").fetchone()[0]
    pit_null = c.execute("SELECT COUNT(*) FROM stock_signals WHERE s11_pit IS NULL").fetchone()[0]
    print(f"\n[全库] s11非NULL {s11n:,} | 旧≠PIT {diff:,} ({diff / max(s11n, 1) * 100:.1f}%) | "
          f"s11_pit仍NULL {pit_null:,}(预期0)")
    all_ok &= (pit_null == 0)
    print("\n总体: " + ("ALL PASS" if all_ok else "存在FAIL项"))
    con.close()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
