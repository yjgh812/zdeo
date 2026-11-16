# -*- coding: utf-8 -*-
"""fix_qfq_bj_fraction_20261011 — qfq 库 BJ 段孤儿行分数口径定点修复(一次性)。

背景(20261011批1): --full 全量重建后主创板错位行清零, 但 92%/4/8 段残留"孤儿行"——
退市/缺源日期段不在 vipdoc, 重建 INSERT OR REPLACE 只覆盖新载荷键, 旧 fallback 时代
(20260906 vipdoc 重建期)写入的分数口径 change_pct 原地存活。

修复器(逐行证据判定, 非盲改):
  对错位 code 的每行算 r = implied / change_pct (implied = close 邻比推导百分数):
    r ∈ [70,140] → 分数口径铁证 → change_pct × 100
    其余(含 r≈1 百分行 / chg=0 / NULL / |implied|<0.05 证据不足行) → 不动
  只触 code 级证据成立(≥3 行铁证)的标的; 全程留痕, 支持 --dry-run。

用法: python fix_qfq_bj_fraction_20261011.py            # dry-run 只报告
      python fix_qfq_bj_fraction_20261011.py --execute  # 实修
"""
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CODE_MIN_EVIDENCE = 3   # code 级铁证行数下限
R_LO, R_HI = 70.0, 140.0


def main():
    execute = "--execute" in sys.argv
    con = sqlite3.connect(DB, timeout=120)
    con.execute("PRAGMA busy_timeout=60000")
    # 错位 code 集(92/4/8 三段 BJ)
    q = """
    SELECT code, change_pct, implied FROM (
      SELECT code, change_pct,
             (close*1.0/NULLIF(LAG(close) OVER (PARTITION BY code ORDER BY date),0)-1)*100 AS implied
      FROM stock_daily_meta_qfq
      WHERE code LIKE '92%' OR code LIKE '4%' OR code LIKE '8%')
    WHERE implied IS NOT NULL AND change_pct IS NOT NULL
      AND ABS(change_pct) > 1e-9 AND ABS(change_pct - implied) > 3"""
    rows = con.execute(q).fetchall()
    by_code: dict = {}
    for code, chg, implied in rows:
        r = implied / chg
        if R_LO <= r <= R_HI:
            by_code.setdefault(code, []).append((chg, implied, r))
    targets = {c: v for c, v in by_code.items() if len(v) >= CODE_MIN_EVIDENCE}
    n_evidence = sum(len(v) for v in targets.values())
    print(f"错位 code={len(by_code)} 达证据门槛 code={len(targets)} 铁证行={n_evidence}")
    if not targets:
        print("无可修目标, 退出")
        return
    patch_total = 0
    if not execute:
        for c, v in list(targets.items())[:10]:
            print(f"  [dry] {c}: 铁证{len(v)}行 样本chg={v[0][0]:.6f} implied={v[0][1]:.2f} r={v[0][2]:.1f}")
        print("dry-run 结束(--execute 实修)")
        return
    cur = con.cursor()
    for code, ev in targets.items():
        # code 级证据成立 → 该 code 全部 r∈[70,140] 的行(含首轮查询窗口外)一并修复:
        # 逐行重算比值(全行扫描), 比百分数阈值覆盖更全(小涨幅分数行同样可判)
        allrows = con.execute(
            """SELECT date, change_pct FROM stock_daily_meta_qfq WHERE code=?""",
            (code,)).fetchall()
        full = con.execute(
            """WITH s AS (SELECT date, close, LAG(close) OVER (ORDER BY date) prev
                         FROM stock_daily_meta_qfq WHERE code=?)
               SELECT date, (close*1.0/NULLIF(prev,0)-1)*100 FROM s""", (code,)).fetchall()
        implied_map = {d: x for d, x in full if x is not None}
        n_patch = 0
        for d, chg in allrows:
            if chg is None or abs(chg) < 1e-9:
                continue
            imp = implied_map.get(d)
            if imp is None:
                continue
            r = imp / chg
            if R_LO <= r <= R_HI and abs(chg * 100 - imp) < abs(chg - imp):
                cur.execute("UPDATE stock_daily_meta_qfq SET change_pct=? WHERE code=? AND date=?",
                            (chg * 100.0, code, d))
                n_patch += 1
        patch_total += n_patch
        if n_patch:
            print(f"  [fix] {code}: 修复 {n_patch} 行")
    con.commit()
    con.close()
    print(f"完成: 修复 {patch_total} 行 / {len(targets)} 只")


if __name__ == "__main__":
    main()
