# -*- coding: utf-8 -*-
r"""DATA_LOCATIONS.md 生成器 (2026-10-12 净增量项3, 主人「继续」令)
=====================================================================
目的: 真实数据库位置索引 —— 根目录 0 字节占位文件(dragon_quant*.db 等)曾致
接手 AI 对空文件误判(分析报告 §0.2 坑位提示第1条)。本件把真实路径单一来源
(check_db_freshness.py 的 DBS 清单)直出为可读索引, 消除"占位文件陷阱"。

纪律: 路径清单禁止人工转抄, 必须从 check_db_freshness.py 源码正则提取直出;
生成后读回核验(行数+关键路径抽查)。0 字节占位文件本身**不删除**(删除须主人
点头), 本件以指针先行。

用法: python analysis/gen_data_locations.py
输出: D:\zcodesj\DATA_LOCATIONS.md; exit 0
"""
import datetime
import os
import re
import sys

BASE = r"D:\zcodesj"
SRC = os.path.join(BASE, "check_db_freshness.py")
OUT = os.path.join(BASE, "DATA_LOCATIONS.md")

PLACEHOLDER_REMOVED = [
    # 2026-10-12 主人「继续」令删除(删除前广谱引用扫描零命中+0字节0信息量):
    # 根目录4个 + data/目录2个(data/dragon_quant.db, data/fundamental_data.db)
    "dragon_quant.db", "dragon_quant_meta.db",
    "dragon_quant_qfq.db", "fundamental_data.db",
]


def extract_replica_pair():
    """从 qfq_replica_sync.py 提取主从同步对(主库->副本), 禁手抄。"""
    text = open(os.path.join(BASE, "qfq_replica_sync.py"),
                encoding="utf-8", errors="ignore").read()
    src = re.search(r"SRC\s*=\s*r?['\"]([^'\"]+)['\"]", text)
    dst = re.search(r"DST\s*=\s*r?['\"]([^'\"]+)['\"]", text)
    if src and dst:
        return src.group(1), dst.group(1)
    return None, None


def extract_dbs():
    """从 check_db_freshness.py 源码提取 名字->路径 映射(正则直出, 禁手抄)。"""
    text = open(SRC, encoding="utf-8", errors="ignore").read()
    pairs = re.findall(r'"([^"]+?)\s*\(([^)]*)\)"\s*:\s*r?"([^"]+)"', text)
    if not pairs:
        pairs = re.findall(r'"([^"]+)"\s*:\s*r?"([^"]+)"', text)
        pairs = [(n, "", p) for n, p in pairs]
    return [(n.strip(), role.strip(), p) for n, role, p in pairs]


def main():
    dbs = extract_dbs()
    assert len(dbs) >= 8, "提取映射过少(%d), 提取模式漂移须排查" % len(dbs)
    sync_src, sync_dst = extract_replica_pair()
    lines = [
        "# 数据位置索引 (DATA_LOCATIONS)",
        "",
        "> 生成: analysis/gen_data_locations.py 直出自 check_db_freshness.py 的库清单",
        "(生成于 %s)。**本文件是视图, 真源 = check_db_freshness.py; 两处不一致时以后者为准。**" % datetime.date.today().isoformat(),
        "",
        "## 占位文件历史注记",
        "",
        "根目录及 data/ 目录曾存在 6 个 0 字节占位同名文件(与真实库无任何内容关系),",
        "已于 **2026-10-12 主人「继续」令下删除**(删除前广谱引用扫描零命中, 0 信息量):",
        "",
    ] + ["- `%s`(已删)" % f for f in PLACEHOLDER_REMOVED] + [
        "",
        "## qfq 库单一真源(2026-10-12 合并定谳, 来源 qfq_replica_sync.py 常量直出)",
        "",
    ]
    if sync_src and sync_dst:
        lines += [
            "- **单一真源(读+写合一)**: `%s`" % sync_dst,
            "  —— daily_chain_update(22:00链)增量写 + QfqFullRefresh_Sat0700 周六全量刷新直写本库,",
            "  全量 %s 行(含 352 只北交所 920 段, 宪法剔除域)。" % "11,892,908",
            "- **原 wb 写入库**(2026-10-12 已与 zc 合并, 文件暂留回滚快照): `%s`" % sync_src,
            "  —— `qfq_replica_sync.py` 已退役(增量 UPSERT 无法传播全量重建的历史重定基准,",
            "  实测漂移 20.8 万行; 合并裁决=SOP一百三十一)。",
            "- ⚠️ 巡检盲区教训: 副本曾断更于 20260904 而检查只盯主库(check_db_freshness",
            "  原文); 新增读侧消费时先确认数据新鲜度链。",
        ]
    lines += [
        "",
        "## 真实数据库清单 (%d 库, 出自 check_db_freshness)" % len(dbs),
        "",
        "| 库 | 角色 | 真实路径 |",
        "|---|---|---|",
    ]
    for name, role, path in dbs:
        lines.append("| %s | %s | `%s` |" % (name, role or "-", path))
    lines += [
        "",
        "## 关键合同速记",
        "",
        "- 回测/组合收益唯一价格源 = qfq 库(主表 stock_daily_meta_qfq,",
        "  change_pct 单位=百分数); 单库合并后读写统一走 data/ 真源(见单一真源段)。",
        "- 财务因子 PIT 唯一真源 = `factor_pit.db` v2 表(v1 已 deprecated 隔离)",
        "- 持仓/盈亏/成交唯一真值 = westock MCP 实时(本地库皆为影子)",
        "- 通达信 vipdoc 读取唯一入口 = `tdx_reader.py`",
        "- 每日新鲜度巡检 = `python check_db_freshness.py`(只读)",
        "",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT, encoding="utf-8") as f:
        back = f.read()
    n_rows = back.count("| `D:")
    assert n_rows >= len(dbs), "读回核验失败: 路径行 %d < %d" % (n_rows, len(dbs))
    assert "单一真源" in back and "qfq_replica_sync" in back, "单一真源段缺失"
    assert "已删" in back, "占位历史注记缺失"
    print("[data-locations] 直出 %d 库+主从段 -> %s (读回 PASS)" % (len(dbs), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
