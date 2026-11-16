# -*- coding: utf-8 -*-
"""pit_surgery_snapshot_20261028.py — PIT 手术定向快照（方案C, 不触碰主人 10-18 备份暂停令）

手术内容回顾(2026-10-28 凌晨, SOP一百八十五⑨⑪):
  S1 stock_financial 回填: report 20260331/20260630 的 revenue_ttm/total_shares 空值补齐
     (akshare TTM 合成 + daily_basic 股本; 幂等只填空)
  S2 stock_factors_fundamental sp 补空重算: 报告窗口 20260331/20260630/20260930 内 sp IS NULL 行
     (=1/PS_TTM 公式, 仅填空)
  S3 v2 四列加列+全史重算: sp_v2/lev_v2/ato_v2/pm_v2 (幂等可再生成: pit_factor_v2_backfill_20261028.py)
快照内容:
  A stock_financial 手术相关报告期全切片(含回填后值, ~2.2万行)
  B 当日截面(20261027)四 v2 列 + sp 全量(5542 行)
  C 回滚配方清单(每项手术的可逆操作与再生成命令)
快照不等于全量备份(全量备份处主人 10-18 暂停令下, 恢复=删 backups/.backup_paused)。
"""
from __future__ import annotations
import json, sys, os, sqlite3, hashlib, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
OUT_DIR = os.path.join(r"D:\zcodesj", "data", "pit_surgery_snapshot_20261028")
os.makedirs(OUT_DIR, exist_ok=True)


def export(con, sql, path):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join("" if v is None else str(v) for v in r) + "\n")
    md5 = hashlib.md5(open(path, "rb").read()).hexdigest()
    return len(rows), md5


def main():
    t0 = time.time()
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    manifest = {"surgery_date": "2026-10-28", "snapshot_type": "targeted(方案C)",
                "files": {}, "rollback_recipes": []}

    n, md5 = export(con,
        "select id, code, report_date, roe, bps, revenue_ttm, total_shares, net_margin, debt_ratio, pit_avail_date "
        "from stock_financial where report_date in (20260331, 20260630) order by code, report_date",
        os.path.join(OUT_DIR, "A_stock_financial_surgery_slice.csv"))
    manifest["files"]["A_stock_financial_slice"] = {"rows": n, "md5": md5}
    print(f"[A] stock_financial 手术切片: {n} 行 md5={md5[:12]}")

    n, md5 = export(con,
        "select code, date, report_date, close, ep, bp, roe, sp, sp_v2, lev_v2, ato_v2, pm_v2 "
        "from stock_factors_fundamental where date=20261027 order by code",
        os.path.join(OUT_DIR, "B_cross_section_20261027.csv"))
    manifest["files"]["B_cross_section_20261027"] = {"rows": n, "md5": md5}
    print(f"[B] 当日截面(v2+sp): {n} 行 md5={md5[:12]}")

    manifest["rollback_recipes"] = [
        {"surgery": "S3 v2四列加列+重算", "rollback": "ALTER TABLE stock_factors_fundamental DROP COLUMN sp_v2/lev_v2/ato_v2/pm_v2 (SQLite 3.35+)",
         "regenerate": "python analysis/pit_factor_v2_backfill_20261028.py (幂等再生成)"},
        {"surgery": "S2 sp补空重算", "rollback": "update stock_factors_fundamental set sp=null where report_date in (20260331,20260630,20260930) and created_at<'2026-10-28' (重算仅填空, 旧行 created_at 未动)",
         "regenerate": "python analysis/pit_fundamental_backfill_20261028.py (幂等再生成)"},
        {"surgery": "S1 stock_financial 回填", "rollback": "恢复=快照文件A逆向(将回填值置回 NULL; 回填前 Q1 rev_ttm=1509/Q2=0)",
         "regenerate": "python analysis/pit_fundamental_backfill_20261028.py"},
    ]
    manifest["note"] = "定向快照(方案C)≠全量备份; 全量备份处主人2026-10-18暂停令下(恢复=删 backups/.backup_paused)"

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"[manifest] {OUT_DIR}/manifest.json")
    print(f"[耗时] {time.time()-t0:.0f}s")
    con.close()


if __name__ == "__main__":
    main()
