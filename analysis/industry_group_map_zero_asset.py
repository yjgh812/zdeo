# -*- coding: utf-8 -*-
"""分组器实验零号件: 行业分组映射快照资产 (analysis/ 研究层, 只读上游+独立落库)

任务线: TULONG-SELECTION-CHAIN 审计系列呈请A(2026-10-18 按"继续"令执行推荐边界)
  = 零号件申万PIT行业映射落地 + PREREG_INDUSTRY_NEUTRAL DRAFT起草(实验不开跑,待主人批准)

口径事实(2026-10-18 实测):
  - tushare index_member_all 无权限(40203); ftshare 成分历史匿名限流22h;
    akshare 无申万成分接口 → 申万成分PIT历史免费源不可得, 如实声明
  - 本地 fund 库 industry_map 的 SW2021 段=导入bug坏数据(31行全"美容护理")禁用
  - 主源 = industry_map_ths(同花顺行业映射快照, 5599行, 值为"一级-二级-三级"复合段,
    首段=申万2021同名一级分类), 生成时点≈2025-08(import_tsp_ext_data 常态化入库), 非PIT
  - PIT 边界: 快照仅代表建表时点归属; 同花顺/申万行业无2021版大迁移事件, 漂移年度级;
    prereg 主实验窗=2022-01-01 起, 前视风险作为已知近似入prereg声明, 两臂共用同一映射
    → 差值结论对映射误差稳健
产出: data/industry_group_map_snapshot.db
  - meta(口径/PIT边界/生成时间)  - l1_member(code,name,l1,path,ambiguous_flag)
边界: 零交易权; 不接生产链; 不改任何既有库; 实验实现待 prereg 批准后另立脚本
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SRC_DB = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_DB = Path(r"D:\zcodesj\data\industry_group_map_snapshot.db")


def main() -> int:
    src = sqlite3.connect(SRC_DB, uri=True)
    rows = src.execute("SELECT code, name, industry FROM industry_map_ths").fetchall()
    src.close()
    if not rows:
        print("❌ 源表 industry_map_ths 为空, fail-closed")
        return 2

    # 解析: code zfill6, l1=复合段首段; 同码多L1 → ambiguous(剔除)
    per_code: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for raw_code, name, industry in rows:
        code = str(raw_code).zfill(6)
        parts = str(industry).split("-")
        if not parts or not parts[0]:
            ambiguous.add(code)
            continue
        l1 = parts[0]
        rec = per_code.setdefault(code, {"name": name, "l1": l1, "path": industry})
        if rec["l1"] != l1:
            ambiguous.add(code)

    clean = {c: r for c, r in per_code.items() if c not in ambiguous}
    l1_names = sorted({r["l1"] for r in clean.values()})

    # qfq 覆盖与缺口构成
    qfq = sqlite3.connect(QFQ_DB, uri=True)
    qfq_codes = {str(r[0]).zfill(6) for r in qfq.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()}
    qfq.close()
    covered = qfq_codes & set(clean)
    miss = sorted(qfq_codes - set(clean))
    miss_bj = sum(1 for c in miss if c.startswith(("4", "8", "92")))
    miss_star = sum(1 for c in miss if c.startswith("688"))
    miss_other = len(miss) - miss_bj - miss_star

    # 落库(幂等: 重建单文件)
    if OUT_DB.exists():
        OUT_DB.unlink()
    out = sqlite3.connect(str(OUT_DB))
    out.executescript(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE l1_member (
            code TEXT PRIMARY KEY,
            name TEXT,
            l1 TEXT,
            path TEXT,
            ambiguous_flag INTEGER
        );
        """
    )
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "src": "workbuddyspace fundamental_data.db industry_map_ths (同花顺行业映射快照, ~2025-08 建表时点)",
        "taxonomy": "同花顺行业复合段首段=申万2021同名一级(L1); path 保留全三级",
        "pit_boundary": "非PIT: 快照代表建表时点归属; 无2021版大迁移事件, 漂移年度级; "
                        "分组器实验主窗=2022-01-01起, 两臂共用同一映射, 差值结论对映射误差稳健; "
                        "申万成分PIT历史免费源不可得(tushare无权限/ftshare限流/akshare无接口), 如实声明",
        "n_rows_src": len(rows),
        "n_codes_clean": len(clean),
        "n_codes_ambiguous": len(ambiguous),
        "n_l1": len(l1_names),
        "qfq_coverage": f"{len(covered)}/{len(qfq_codes)} ({100*len(covered)/len(qfq_codes):.1f}%)",
        "miss_compose": f"北交所类{miss_bj} 科创{miss_star} 其他{miss_other}",
    }
    out.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    out.executemany(
        "INSERT INTO l1_member VALUES (?,?,?,?,?)",
        [(c, r["name"], r["l1"], r["path"], 1 if c in ambiguous else 0)
         for c, r in sorted(per_code.items())],
    )
    out.commit()
    out.close()

    # 断言门(研究资产质量门, 不过即 fail-closed)
    cov_pct = 100 * len(covered) / len(qfq_codes)
    assert 25 <= len(l1_names) <= 40, f"L1 数量异常: {len(l1_names)}"
    assert cov_pct > 90, f"覆盖率过低: {cov_pct:.1f}%"
    print(f"✅ 落库 {OUT_DB}")
    print(f"   L1 分类数={len(l1_names)} | 干净映射={len(clean)} | 多归属剔除={len(ambiguous)}")
    print(f"   qfq 覆盖 {meta['qfq_coverage']} | 缺口: 北交所类{miss_bj} 科创{miss_star} 其他{miss_other}")
    print(f"   L1 样例: {l1_names[:8]} ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
