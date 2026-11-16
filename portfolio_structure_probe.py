#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合结构只读探针 — O-N3（2026-09-08 第八轮自省 B9 收口）

========== 背景 ==========
组合级结构此前无 owner：38 只持仓/Top3 集中度 46.8%（且为 9/3 违规批残留）/
约 26 只 <1.5 万碎片仓，无人可见无人告警。持仓≤50 规则只防数量不防结构；
集中度硬 cap 已被 round12a/12b 证伪关闭（主人裁决），**本探针只做可见性告警，
不做任何拦截**——不复活已证伪方向。

========== 指标（v0 五项+一项 N/A） ==========
1. 持仓数            WARN > 45（规则上限 50 的缓冲带）
2. Top5 集中度(占市值) WARN > 40%
3. 单票最大占比(占市值) WARN > 20%（宪法级单票上限锚）
4. 均仓(市值/只)
5. 碎片仓家数(市值<1.5万) WARN >= 20
6. 现金比例          N/A（positions_latest.json 快照无现金字段, 如实标注不编造;
                     现金真值=westock MCP portfolio 实时, 归 22:00 链 LLM 段汇报）

数据源: data/positions_latest.json（09:10 链 write_positions_snapshot.py 唯一写入方,
当日晨间快照; 盘后同日新交易以次日快照反映——探针如实标注快照时点）。

用法: python portfolio_structure_probe.py
退出码: 0=全部OK / 1=有WARN(链路容许, 日志留痕) / 3=数据源缺失(不伪造)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SNAPSHOT = Path(r"D:\zcodesj\data\positions_latest.json")
REPORT_DIR = Path(r"D:\zcodesj\reports")
WARN_TOP5 = 40.0
WARN_SINGLE = 20.0
WARN_COUNT = 45
WARN_FRAGMENT = 20
FRAGMENT_LINE = 15000.0
# M2 (2026-10-31 主人裁决批准): 再平衡可见性闸两字段 — 亏损簇/正贡献簇市值占比
# 口径: 亏损簇=浮盈率<=-4%持仓; 正贡献簇=浮盈率>=+6%持仓 (daily_review §1.2 风险分层的自动化)
WARN_LOSS_CLUSTER = 25.0
WARN_WIN_CLUSTER = 10.0
LOSS_CLUSTER_LINE = -4.0
WIN_CLUSTER_LINE = 6.0


def main() -> int:
    # GBK 控制台双保险（复用 69574e24 Mon0941 既有修复模式）: ⚠️/✅ 字符在 GBK stdout 下崩溃
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not SNAPSHOT.exists():
        print("[FAIL] 持仓快照缺失: data/positions_latest.json（fail-closed 不伪造）", file=sys.stderr)
        print("[FAIL] 修复: 22:00 链 LLM 段或交互会话执行 python write_positions_snapshot.py", file=sys.stderr)
        return 3
    try:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[FAIL] 快照不可解析: {e}", file=sys.stderr)
        return 3
    positions = data.get("positions") or []
    snap_at = str(data.get("snapshot_at") or "")
    if not positions:
        print("[FAIL] 快照持仓为空（fail-closed; 若确已清仓请人工确认后处理）", file=sys.stderr)
        return 3

    vals = []
    for p in positions:
        code = str(p.get("code") or "")
        name = str(p.get("name") or "")
        try:
            mv = float(p.get("marketValue") or 0)
        except (TypeError, ValueError):
            mv = 0.0
        try:
            pr = float(p.get("profitRate") or 0)
        except (TypeError, ValueError):
            pr = 0.0
        vals.append((code, name, mv, pr))
    total_mv = sum(v for _, _, v, _ in vals)
    if total_mv <= 0:
        print("[FAIL] 快照市值合计<=0（fail-closed）", file=sys.stderr)
        return 3

    ranked = sorted(vals, key=lambda x: -x[2])
    top5_pct = sum(v for _, _, v, _ in ranked[:5]) / total_mv * 100
    single_pct = ranked[0][2] / total_mv * 100
    fragments = [x for x in vals if 0 < x[2] < FRAGMENT_LINE]
    frag_mv = sum(v for _, _, v, _ in fragments)
    # M2 两簇: 亏损簇(<=-4%) / 正贡献簇(>=+6%) 市值占比
    loss_cluster = [x for x in vals if x[3] <= LOSS_CLUSTER_LINE]
    win_cluster = [x for x in vals if x[3] >= WIN_CLUSTER_LINE]
    loss_mv = sum(v for _, _, v, _ in loss_cluster)
    win_mv = sum(v for _, _, v, _ in win_cluster)
    loss_pct = loss_mv / total_mv * 100
    win_pct = win_mv / total_mv * 100

    warns: list[str] = []
    n = len(vals)
    if n > WARN_COUNT:
        warns.append(f"持仓数{n}> {WARN_COUNT}")
    if top5_pct > WARN_TOP5:
        warns.append(f"Top5集中度{top5_pct:.1f}%> {WARN_TOP5:.0f}%")
    if single_pct > WARN_SINGLE:
        warns.append(f"单票最大{ranked[0][1]}{single_pct:.1f}%> {WARN_SINGLE:.0f}%")
    if len(fragments) >= WARN_FRAGMENT:
        warns.append(f"碎片仓{len(fragments)}只>= {WARN_FRAGMENT}")
    # M2 (2026-10-31 批准): 两簇失衡可见性
    if loss_pct > WARN_LOSS_CLUSTER:
        warns.append(f"亏损簇({len(loss_cluster)}只/{loss_pct:.1f}%市值)> {WARN_LOSS_CLUSTER:.0f}%")
    if win_pct < WARN_WIN_CLUSTER:
        warns.append(f"正贡献簇({len(win_cluster)}只/{win_pct:.1f}%市值)< {WARN_WIN_CLUSTER:.0f}%")

    today = datetime.now().strftime("%Y%m%d")
    lines = [
        f"# 组合结构探针 {today}",
        "",
        f"- 快照时点: {snap_at or 'N/A'}（{SNAPSHOT.name}, 09:10 链写入; 盘后同日交易次日反映）",
        f"- 持仓数: {n}",
        f"- 市值合计: {total_mv:,.0f} 元",
        f"- Top5 集中度: {top5_pct:.1f}%（{', '.join(f'{nm}{v/total_mv*100:.1f}%' for _, nm, v, _ in ranked[:5])}）",
        f"- 单票最大: {ranked[0][1]} {single_pct:.1f}%",
        f"- 均仓: {total_mv / n:,.0f} 元/只",
        f"- 碎片仓(<1.5万): {len(fragments)} 只, 合计 {frag_mv:,.0f} 元（占市值 {frag_mv/total_mv*100:.1f}%）",
        f"- 亏损簇(浮盈≤-4%): {len(loss_cluster)} 只, 市值占比 {loss_pct:.1f}%",
        f"- 正贡献簇(浮盈≥+6%): {len(win_cluster)} 只, 市值占比 {win_pct:.1f}%",
        "- 现金比例: N/A（快照无现金字段; 真值=westock MCP portfolio, 由 22:00 链汇报段呈现）",
        "",
        f"## 判定: {'⚠️ WARN ' + '; '.join(warns) if warns else '✅ 结构指标全部在阈值内'}",
        "",
        "> 只读告警不拦截。集中度硬 cap 已被 round12a/12b 证伪关闭（主人裁决），",
        "> 本探针不复活任何拦截语义；处置权=主人。阈值仅作可见性参考。",
    ]
    out = REPORT_DIR / f"portfolio_structure_{today}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n📄 {out}")
    return 1 if warns else 0


if __name__ == "__main__":
    raise SystemExit(main())
