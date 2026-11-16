# -*- coding: utf-8 -*-
"""pit_factor_v2_backfill_20261028.py — 三段式立项(主人「继续」令批准) sp_v2/lev_v2/ato_v2/pm_v2 加列+全史重算

语义(m2 设计, 量纲全部正确):
  sp_v2  = rev_ttm_万元×1e4 / (close × shares_股)        = 1/PS_TTM   (与旧 sp 同语义, 全史补齐)
  lev_v2 = 1 / (1 − debt_ratio/100)                       = 总资产/净资产 (真杠杆, TDX 原生 debt_ratio)
  pm_v2  = net_margin / 100                               = 销售净利率  (TDX 原生, 当期口径)
  ato_v2 = rev_ttm_万元×1e4 / (bp × close × shares_股 × lev_v2)
         = TTM营收 / 总资产   (权益桥=因子表 bp×close×shares, bp 为无量纲 1/PB, 99% 覆盖)
旧列 sp/lev/ato/pm 一字不动(留档); 本脚本幂等(重复跑仅重算 v2 列, 可随时重跑)。
报告行选取: stock_financial 按 (code,report_date) 分组 MAX(每列独立取非空最大值)。
用法: python analysis/pit_factor_v2_backfill_20261028.py [--chunk 1000000]
"""
from __future__ import annotations
import sys, os, sqlite3, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
CHUNK = 1000000
if "--chunk" in sys.argv:
    CHUNK = int(sys.argv[sys.argv.index("--chunk") + 1])

V2_COLS = ["sp_v2", "lev_v2", "ato_v2", "pm_v2"]


def main():
    t0 = time.time()
    con = sqlite3.connect(MAIN)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    c = con.cursor()

    # 1) 加列迁移(幂等, 循 pit_avail_date 20260815 加列先例)
    cols = [r[1] for r in c.execute("PRAGMA table_info(stock_factors_fundamental)")]
    added = []
    for col in V2_COLS:
        if col not in cols:
            c.execute(f"ALTER TABLE stock_factors_fundamental ADD COLUMN {col} REAL")
            added.append(col)
    con.commit()
    print(f"[迁移] 新增列: {added or '无(已存在)'}")

    # 2) 报告期映射表(每列独立 MAX 非空)
    c.execute("DROP TABLE IF EXISTS _v2map")
    c.execute(
        "CREATE TEMP TABLE _v2map AS "
        "SELECT code, report_date, MAX(revenue_ttm) rev, MAX(total_shares) shares, "
        "MAX(debt_ratio) debt, MAX(net_margin) nm "
        "FROM stock_financial WHERE roe IS NOT NULL GROUP BY code, report_date")
    n_map = c.execute("select count(*) from _v2map").fetchone()[0]
    n_rev = c.execute("select count(*) from _v2map where rev>0 and shares>0").fetchone()[0]
    print(f"[映射] _v2map {n_map} 组 / rev×shares 齐备 {n_rev} 组")

    # 3) 分块 UPDATE-FROM 全史重算
    max_rid = c.execute("SELECT MAX(rowid) FROM stock_factors_fundamental").fetchone()[0] or 0
    done = 0
    lo = 0
    while lo <= max_rid:
        hi = lo + CHUNK
        c.execute(
            """
            UPDATE stock_factors_fundamental AS f SET
              sp_v2  = CASE WHEN m.rev>0 AND f.close>0 AND m.shares>0
                            THEN m.rev*1e4/(f.close*m.shares) END,
              lev_v2 = CASE WHEN m.debt IS NOT NULL AND m.debt<100
                            THEN 1.0/(1.0-m.debt/100.0) END,
              pm_v2  = CASE WHEN m.nm IS NOT NULL THEN m.nm/100.0 END,
              ato_v2 = CASE WHEN m.rev>0 AND f.bp>0 AND f.close>0 AND m.shares>0
                             AND m.debt IS NOT NULL AND m.debt<100
                            THEN m.rev*1e4/(f.bp*f.close*m.shares*(1.0/(1.0-m.debt/100.0))) END
            FROM _v2map AS m
            WHERE f.code=m.code AND f.report_date=m.report_date
              AND f.rowid>? AND f.rowid<=?
            """, (lo, hi))
        done += c.rowcount if c.rowcount and c.rowcount > 0 else 0
        con.commit()
        print(f"[重算] rowid {lo}..{hi} done={done} t={time.time()-t0:.0f}s")
        lo = hi

    # 4) 覆盖率验收
    total = c.execute("select count(*) from stock_factors_fundamental").fetchone()[0]
    for col in V2_COLS:
        n = c.execute(f"select count({col}) from stock_factors_fundamental").fetchone()[0]
        print(f"[覆盖] {col}: {n}/{total} = {n/total:.1%}")

    # 5) 抽检: 茅台(高价股) + 银行(低PB杠杆) + 随机一只
    for code in ("600519", "601939", "000001"):
        r = c.execute(
            f"select date, close, sp_v2, lev_v2, pm_v2, ato_v2 from stock_factors_fundamental "
            f"where code=? and date=20261027", (code,)).fetchone()
        if r:
            d, cl, sp2, lv2, pm2, at2 = r
            ps = round(1 / sp2, 1) if sp2 else None
            print(f"[抽检] {code}: close={cl} sp_v2={sp2}(PS={ps}) lev_v2={lv2} pm_v2={pm2} ato_v2={at2}")

    c.execute("DROP TABLE IF EXISTS _v2map")
    con.commit()
    con.close()
    print(f"[耗时] {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
