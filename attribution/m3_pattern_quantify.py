#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
范式库量化特征增强 M-3（8/13全维度自省）
======================================

当前问题：
  limit_up_patterns.feature_tags = ["光通信","CPO","中报预增"] → 仅有定性标签，无量化触发条件
  Qlib 无法引用、闸门无法自动匹配。

M-3 修复：
  1. ALTER TABLE 增加 6 个量化触发字段
  2. 基于 feature_tags 和历史数据自动回填现有 44 条范式
  3. 输出 JSON 格式供 pre_trade_check_v2 和 mainline_radar 消费

新增字段：
  trigger_min_volume_ratio   REAL    — 最小量比（如 1.5、2.0）
  trigger_max_time_window    TEXT    — 最晚首封时间（如 "11:00"、"14:00"）
  trigger_min_sector_lu      INTEGER — 板块最少涨停数（如 3、5）
  trigger_min_predictability REAL    — 最低可预判性（如 5.0、7.0）
  trigger_require_board_rank TEXT    — 板块排名要求（"top3" / "top5" / "any"）
  trigger_tags_quantified    TEXT    — 量化后的 JSON（示例见下）

用法：
  python m3_pattern_quantify.py              # 执行迁移+回填
  python m3_pattern_quantify.py --dry-run    # 仅预览变更
  python m3_pattern_quantify.py --export-json  # 导出为JSON供系统消费
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

DB = r"D:\zcodesj\attribution\attribution.db"
OUT_DIR = r"D:\zcodesj\reports"

# ===== 量化映射规则 =====
# 根据 feature_tags 自动推断量化参数
TAG_VOLUME_MAP = {
    "放量首板": 2.0, "放量": 1.8, "量价齐升": 2.0,
    "缩量板": 0.8, "一字板": 0.5,
}
TAG_TIME_MAP = {
    "早盘封": "09:45", "盘中封": "11:00", "尾盘封": "14:30",
    "竞价封": "09:25", "午后封": "13:30",
}
TAG_SECTOR_MAP = {
    "板块联动": 5, "板块龙头": 5, "板块共振": 5,
    "主线": 5, "支线": 3, "独立": 1,
}
TAG_PREDICT_MAP = {
    "外盘催化": 8.0, "公告催化": 8.0, "业绩驱动": 8.0,
    "政策驱动": 7.0, "涨价驱动": 7.0,
    "跟随": 5.0, "补涨": 5.0,
    "消息驱动": 6.0, "资金驱动": 6.0,
}
TAG_BOARD_MAP = {
    "板块龙头": "top3", "中军": "top3", "龙头": "top3",
    "弹性": "top5", "补涨": "any", "跟风": "any",
}


def infer_volume_ratio(tags: list[str]) -> float:
    for t in tags:
        for k, v in TAG_VOLUME_MAP.items():
            if k in t:
                return v
    return 1.5  # 默认


def infer_time_window(tags: list[str], seal_status: str) -> str:
    for t in tags:
        for k, v in TAG_TIME_MAP.items():
            if k in t:
                return v
    # 从 seal_status 推断
    if seal_status == "竞价封":
        return "09:25"
    elif seal_status == "早盘封":
        return "10:00"
    elif seal_status == "盘中封":
        return "11:00"
    elif seal_status == "尾盘封":
        return "14:00"
    return "14:00"


def infer_sector_lu(tags: list[str]) -> int:
    for t in tags:
        for k, v in TAG_SECTOR_MAP.items():
            if k in t:
                return v
    return 3


def infer_predictability(tags: list[str], original_score: float) -> float:
    if original_score is not None and original_score > 0:
        return original_score
    for t in tags:
        for k, v in TAG_PREDICT_MAP.items():
            if k in t:
                return v
    return 5.0


def infer_board_rank(tags: list[str]) -> str:
    for t in tags:
        for k, v in TAG_BOARD_MAP.items():
            if k in t:
                return v
    return "any"


def migrate_schema(dry_run: bool = False) -> list[str]:
    """执行数据库迁移——增加量化触发字段。"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    migrations = []

    new_columns = [
        ("trigger_min_volume_ratio", "REAL"),
        ("trigger_max_time_window", "TEXT"),
        ("trigger_min_sector_lu", "INTEGER"),
        ("trigger_min_predictability", "REAL"),
        ("trigger_require_board_rank", "TEXT"),
        ("trigger_tags_quantified", "TEXT"),
    ]

    # 检查已有列
    c.execute("PRAGMA table_info(limit_up_patterns)")
    existing = {r[1] for r in c.fetchall()}

    for col_name, col_type in new_columns:
        if col_name not in existing:
            sql = f"ALTER TABLE limit_up_patterns ADD COLUMN {col_name} {col_type}"
            if dry_run:
                migrations.append(f"[DRY-RUN] {sql}")
            else:
                c.execute(sql)
                migrations.append(f"[OK] {sql}")
        else:
            migrations.append(f"[SKIP] {col_name} 已存在")

    if not dry_run:
        conn.commit()
    conn.close()
    return migrations


def backfill_patterns(dry_run: bool = False) -> list[dict]:
    """基于现有 feature_tags 回填量化参数。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    rows = c.execute(
        "SELECT id, code, name, feature_tags, seal_status, predictability_score, pattern_type "
        "FROM limit_up_patterns"
    ).fetchall()

    updates = []
    for r in rows:
        tags_raw = r["feature_tags"] or "[]"
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        except json.JSONDecodeError:
            tags = []

        vol = infer_volume_ratio(tags)
        tw = infer_time_window(tags, r["seal_status"] or "")
        sl = infer_sector_lu(tags)
        pred = infer_predictability(tags, r["predictability_score"])
        br = infer_board_rank(tags)
        q_tags = {
            "volume_ratio": vol,
            "time_window": tw,
            "sector_lu": sl,
            "predictability": pred,
            "board_rank": br,
            "source_tags": tags,
        }

        updates.append({
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "trigger_min_volume_ratio": vol,
            "trigger_max_time_window": tw,
            "trigger_min_sector_lu": sl,
            "trigger_min_predictability": pred,
            "trigger_require_board_rank": br,
            "trigger_tags_quantified": json.dumps(q_tags, ensure_ascii=False),
        })

        if not dry_run:
            c.execute(
                """UPDATE limit_up_patterns SET
                    trigger_min_volume_ratio=?,
                    trigger_max_time_window=?,
                    trigger_min_sector_lu=?,
                    trigger_min_predictability=?,
                    trigger_require_board_rank=?,
                    trigger_tags_quantified=?
                WHERE id=?""",
                (vol, tw, sl, pred, br, json.dumps(q_tags, ensure_ascii=False), r["id"]),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    return updates


def export_json(date_str: str | None = None) -> str:
    """导出为 JSON 供 pre_trade_check_v2 / mainline_radar 消费。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM limit_up_patterns ORDER BY predictability_score DESC"
    ).fetchall()
    conn.close()

    patterns = []
    for r in rows:
        p = dict(r)
        # 时间戳转字符串
        for k in ("created_at",):
            if k in p:
                del p[k]
        patterns.append(p)

    output = {
        "export_time": datetime.now().isoformat(),
        "total_patterns": len(patterns),
        "patterns": patterns,
        "usage_tip": "mainline_radar 读此 JSON → 匹配开盘前催化 → 输出主线候选",
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"patterns_quantified_{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"📄 JSON已导出: {path} ({len(patterns)}条范式)")
    return path


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="范式库量化特征增强 M-3")
    p.add_argument("--dry-run", action="store_true", help="仅预览不执行")
    p.add_argument("--export-json", action="store_true", help="导出量化范式为JSON")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD（仅用于导出文件名）")
    a = p.parse_args()

    if a.export_json:
        export_json(a.date)
        return 0

    print("=" * 60)
    print(f"  范式库量化特征增强 M-3")
    print(f"  模式: {'🔍 DRY-RUN' if a.dry_run else '⚡ 执行'}")
    print("=" * 60)

    # Step 1: 数据库迁移
    print("\n📐 Step 1/3: 数据库迁移（新增量化触发字段）...")
    migrations = migrate_schema(dry_run=a.dry_run)
    for m in migrations[:10]:
        print(f"  {m}")
    if len(migrations) > 10:
        print(f"  ... 共 {len(migrations)} 项")

    # Step 2: 回填
    print("\n📊 Step 2/3: 基于 feature_tags 回填量化参数...")
    updates = backfill_patterns(dry_run=a.dry_run)
    print(f"  已回填: {len(updates)} 条范式")

    if a.dry_run:
        for u in updates[:5]:
            print(f"  {u['code']} {u['name']}: "
                  f"量比≥{u['trigger_min_volume_ratio']} / "
                  f"首封≤{u['trigger_max_time_window']} / "
                  f"板块涨停≥{u['trigger_min_sector_lu']} / "
                  f"板块排名{u['trigger_require_board_rank']}")
        if len(updates) > 5:
            print(f"  ... 共 {len(updates)} 条（仅显示前5）")

    # Step 3: 导出 JSON
    print("\n📦 Step 3/3: 导出量化范式 JSON...")
    path = export_json(a.date)

    print(f"\n{'='*60}")
    print(f"  M-3 完成！数据库迁移+回填+JSON导出三步到位。")
    print(f"  pre_trade_check_v2 和 mainline_radar 可直接引用: {path}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())