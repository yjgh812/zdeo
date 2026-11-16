#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归因→选股反馈过滤器 v1.0（8/13全维度自省 M-2）

========== 用途 ==========
bp+ep 月度选股输出候选后，过此过滤器：
1. 读 attribution.db 中 trade_attribution 表的历史交易归因
2. 对每个候选标的检查历史表现（胜率/平均盈亏/归因主因）
3. 标记风险标签：🔴重亏历史 / 🟡亏损频率高 / 🟢表现良好 / ⚪无历史
4. 输出过滤后候选清单 + 风险提示

========== 打通归因→选股的断裂 ==========
当前断裂：归因库记录了57笔交易但选股流程完全不读。
本脚本是第一个"归因反馈选股"的物理实现。

========== 用法 ==========
    # 从 CSV 文件过滤（bp+ep 月度输出）
    python attribution_feedback_filter.py --input picks.csv --output filtered.csv

    # 从 JSON 文件过滤
    python attribution_feedback_filter.py --input picks.json --output filtered.json

    # 从 stdin 管道输入（每行一个代码）
    echo "002582" | python attribution_feedback_filter.py --pipe --output filtered.json

    # 只查单只股票的历史归因
    python attribution_feedback_filter.py --query 002582

    # 生成归因反馈报告
    python attribution_feedback_filter.py --report
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any

ATTRIBUTION_DB = r"D:\zcodesj\attribution\attribution.db"

# ===== 风险阈值（2026-08-15 v1.1：修复单位bug——pnl_pct在库内存百分数如-1.98=-1.98%，
#       旧RISK_AVG_LOSS=-0.03按比例设计导致任何负均值都触发🟡；胜率阈值与
#       workbuddyspace/attribution_feedback.py 统一为35%）=====
RISK_LOSS_COUNT = 2        # 历史亏损次数 ≥2 → 🟡
RISK_TOTAL_LOSS = -5000    # 累计亏损 ≤-5000元 → 🔴
RISK_AVG_LOSS = -3.0       # 平均每笔收益 ≤-3%（百分数口径）→ 🟡
RISK_WIN_RATE = 0.35       # 胜率 <35% → 🟡（与attribution_feedback.py统一）

# ===== 归因类型权重 =====
# 选股层面亏损（selection）→ 最危险，此类亏损说明选股逻辑有问题
# 执行层面亏损（execution）→ 部分可控，但高频出现说明该票常被误操作
# 风控层面亏损（risk）→ 风控问题
HIGH_RISK_ATTRIBUTION_TYPES = {"selection", "execution"}
MEDIUM_RISK_ATTRIBUTION_TYPES = {"risk"}


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def norm_code(code: str) -> str:
    """标准化代码为6位纯数字。"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6)


def get_stock_history(code: str, db_path: str = ATTRIBUTION_DB) -> dict[str, Any] | None:
    """从归因库读取单只股票的历史交易记录。

    返回:
        {
            "code": "002582",
            "name": "好想你",
            "total_trades": 3,
            "wins": 1,
            "losses": 2,
            "win_rate": 0.33,
            "total_pnl": -1341.0,
            "avg_pnl_per_trade": -447.0,
            "avg_pnl_pct": -0.018,
            "max_loss_pct": -0.055,
            "max_win_pct": 0.032,
            "attribution_types": {"execution": 2, "selection": 1},
            "risk_label": "🟡",  # 🔴/🟡/🟢/⚪
            "risk_reasons": ["亏损频率高", "平均亏损-3%"],
            "recent_trades": [...]
        }
    """
    if not os.path.exists(db_path):
        return None

    code6 = norm_code(code)
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        # 查 trade_attribution 表（不包含方向/日期——需 JOIN position_history）
        # 注：库内code为带前缀格式(sh600199)，2026-08-15修复：兼容裸6位码查询
        # 2026-08-29: position_history=冻结历史表(写入链路死亡, kb/rules/data-field-semantics.md §五)
        # LEFT JOIN 缺行时 entry/exit_date 为 NULL 属预期降级, 勿当缺失事故
        rows = con.execute(
            "SELECT t.code, t.name, t.pnl_amount, t.pnl_pct, "
            "t.primary_attribution, t.secondary_attribution, t.root_cause, "
            "t.ability_luck_verdict, p.entry_date, p.exit_date "
            "FROM trade_attribution t "
            "LEFT JOIN position_history p ON t.position_id = p.id "
            "WHERE t.code IN (?, 'sh'||?, 'sz'||?, 'bj'||?) "
            "ORDER BY COALESCE(p.exit_date, p.entry_date, '') DESC",
            (code6, code6, code6, code6)
        ).fetchall()

        if not rows:
            con.close()
            return {
                "code": code6,
                "name": "",
                "total_trades": 0,
                "risk_label": "⚪",
                "risk_reasons": ["无历史交易记录"],
            }

        # 统计分析（列名适配 attribution.db 实际 schema）
        total = len(rows)
        wins = sum(1 for r in rows if (r["pnl_amount"] or 0) > 0)
        losses = total - wins
        total_pnl = sum(r["pnl_amount"] or 0 for r in rows)
        avg_pnl = total_pnl / total if total > 0 else 0
        avg_pnl_pct = sum(r["pnl_pct"] or 0 for r in rows) / total if total > 0 else 0

        pnl_values = [r["pnl_amount"] or 0 for r in rows]
        pnl_pct_values = [r["pnl_pct"] or 0 for r in rows]
        max_loss_pct = min(pnl_pct_values) if pnl_pct_values else 0
        max_win_pct = max(pnl_pct_values) if pnl_pct_values else 0

        # 归因类型分布（primary_attribution）
        attr_types: dict[str, int] = {}
        for r in rows:
            at = r["primary_attribution"] or "unknown"
            attr_types[at] = attr_types.get(at, 0) + 1

        # 最近3笔交易
        recent = [
            {
                "date": r["exit_date"] or r["entry_date"] or "",
                "direction": "SELL",
                "pnl": r["pnl_amount"],
                "pnl_pct": r["pnl_pct"],
                "attribution": r["primary_attribution"],
                "root_cause": r["root_cause"],
            }
            for r in rows[:3]
        ]

        # 风险判定
        risk_label = "🟢"
        risk_reasons = []

        if total_pnl <= RISK_TOTAL_LOSS:
            risk_label = "🔴"
            risk_reasons.append(f"累计亏损{total_pnl:.0f}元 ≤ {RISK_TOTAL_LOSS}元线")
        elif losses >= RISK_LOSS_COUNT and avg_pnl_pct <= RISK_AVG_LOSS:
            risk_label = "🟡"
            risk_reasons.append(f"亏损{losses}次≥{RISK_LOSS_COUNT}次 + 平均收益{avg_pnl_pct:.1f}%≤{RISK_AVG_LOSS}%")

        if losses > 0 and (wins / total) < RISK_WIN_RATE:
            if risk_label == "🟢":
                risk_label = "🟡"
            risk_reasons.append(f"胜率{wins/total*100:.0f}% < {RISK_WIN_RATE*100:.0f}%")

        # 归因类型风险
        high_risk_count = sum(attr_types.get(t, 0) for t in HIGH_RISK_ATTRIBUTION_TYPES)
        if high_risk_count >= 2 and risk_label in ("🟢", "🟡"):
            risk_label = "🔴"
            risk_reasons.append(f"选股/执行层亏损{high_risk_count}次——系统层面问题")

        con.close()

        return {
            "code": code6,
            "name": rows[0]["name"] or "",
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "avg_pnl_pct": round(avg_pnl_pct, 4),
            "max_loss_pct": round(max_loss_pct, 4),
            "max_win_pct": round(max_win_pct, 4),
            "attribution_types": attr_types,
            "risk_label": risk_label,
            "risk_reasons": risk_reasons,
            "recent_trades": recent,
        }

    except sqlite3.Error as e:
        print(f"⚠️ 数据库错误: {e}", file=sys.stderr)
        return None


def filter_picks(
    picks: list[dict[str, Any]],
    db_path: str = ATTRIBUTION_DB,
    exclude_red: bool = True,
) -> list[dict[str, Any]]:
    """对选股候选列表进行归因过滤。

    Args:
        picks: 候选列表，每项至少含 "code" 字段
        db_path: 归因数据库路径
        exclude_red: 是否剔除 🔴 高风险标的

    Returns:
        过滤后的候选列表（每项加入 risk_label 等信息）
    """
    filtered = []
    removed_red = []
    removed_yellow = []

    for pick in picks:
        code = norm_code(pick.get("code", ""))
        if not code:
            continue

        history = get_stock_history(code, db_path)
        if history:
            pick["risk_label"] = history["risk_label"]
            pick["risk_reasons"] = history["risk_reasons"]
            pick["history"] = {
                "total_trades": history["total_trades"],
                "win_rate": history["win_rate"],
                "total_pnl": history["total_pnl"],
                "attribution_types": history["attribution_types"],
            }
        else:
            pick["risk_label"] = "⚪"
            pick["risk_reasons"] = ["无历史数据或数据库不可用"]
            pick["history"] = {}

        if pick["risk_label"] == "🔴" and exclude_red:
            removed_red.append(pick)
            continue
        elif pick["risk_label"] == "🟡":
            removed_yellow.append(pick)
            # 🟡 仍保留但标记警告
            filtered.append(pick)
        else:
            filtered.append(pick)

    return filtered


def filter_csv(input_path: str, output_path: str, exclude_red: bool = True) -> int:
    """从 CSV 读候选，过滤后写回 CSV。"""
    if not os.path.exists(input_path):
        print(f"❌ 输入文件不存在: {input_path}", file=sys.stderr)
        return 2

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            picks = list(reader)
    except Exception as e:
        print(f"❌ CSV 读取失败: {e}", file=sys.stderr)
        return 2

    if not picks:
        print("⚠️ 候选列表为空，直接输出空文件")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("code,name,risk_label,risk_reasons\n")
        return 0

    # 标准化字段名
    normalized = []
    for p in picks:
        np = {}
        for k, v in p.items():
            k_lower = k.strip().lower()
            if k_lower in ("code", "代码", "股票代码", "symbol"):
                np["code"] = norm_code(v)
            elif k_lower in ("name", "名称", "股票名称", "stockname"):
                np["name"] = v
            else:
                np[k.strip()] = v
        normalized.append(np)

    filtered = filter_picks(normalized, exclude_red=exclude_red)

    # 写输出
    try:
        fieldnames = list(picks[0].keys()) + ["risk_label", "risk_reasons"]
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for fp in filtered:
                fp["risk_reasons"] = "; ".join(fp.get("risk_reasons", []))
                writer.writerow(fp)
    except Exception as e:
        print(f"❌ 写入失败: {e}", file=sys.stderr)
        return 2

    red_count = sum(1 for p in normalized if p.get("risk_label") == "🔴")
    yellow_count = sum(1 for p in normalized if p.get("risk_label") == "🟡")
    green_count = sum(1 for p in normalized if p.get("risk_label") == "🟢")
    white_count = sum(1 for p in normalized if p.get("risk_label") == "⚪")

    print(f"\n{'='*50}")
    print(f"  归因过滤报告")
    print(f"{'='*50}")
    print(f"  输入候选: {len(normalized)} 只")
    print(f"  输出候选: {len(filtered)} 只 (剔除 {len(normalized) - len(filtered)} 只)")
    print(f"  🔴 高风险(剔除): {red_count} 只")
    print(f"  🟡 中等风险(保留): {yellow_count} 只")
    print(f"  🟢 表现良好: {green_count} 只")
    print(f"  ⚪ 无历史: {white_count} 只")
    print(f"{'='*50}")
    print(f"📄 已输出: {output_path}")

    return 0


def generate_report(db_path: str = ATTRIBUTION_DB) -> str:
    """生成归因反馈报告——统计各代码的历史表现，输出风险清单。"""
    if not os.path.exists(db_path):
        return "❌ 归因数据库不存在"

    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        # 获取所有有历史记录的代码
        rows = con.execute(
            "SELECT code, name, COUNT(*) as cnt, "
            "SUM(CASE WHEN pnl_amount>0 THEN 1 ELSE 0 END) as wins, "
            "SUM(pnl_amount) as total_pnl, AVG(pnl_pct) as avg_pnl_pct, "
            "primary_attribution "
            "FROM trade_attribution "
            "GROUP BY code "
            "ORDER BY total_pnl ASC"
        ).fetchall()

        con.close()

        if not rows:
            return "⚠️ 归因库无已平仓记录"

        lines = []
        lines.append("# 归因反馈报告\n")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append(f"数据源: {db_path}\n")
        lines.append(f"有历史记录的标的: {len(rows)} 只\n\n")
        lines.append("## 高风险标的（🔴 建议买入前谨慎）\n\n")
        lines.append("| 代码 | 名称 | 交易次数 | 胜率 | 累计盈亏 | 均收益% | 主归因 |\n")
        lines.append("|------|------|----------|------|----------|---------|--------|\n")

        high_risk_count = 0
        for r in rows:
            total = r["cnt"] or 0
            wins = r["wins"] or 0
            losses = total - wins
            win_rate = wins / total if total > 0 else 0
            total_pnl = r["total_pnl"] or 0
            avg_pnl_pct = r["avg_pnl_pct"] or 0

            # 风险判定
            if total_pnl <= RISK_TOTAL_LOSS or (losses >= RISK_LOSS_COUNT and avg_pnl_pct <= RISK_AVG_LOSS):
                high_risk_count += 1
                lines.append(
                    f"| {r['code']} | {r['name'] or ''} | {total} | "
                    f"{win_rate*100:.0f}% | {total_pnl:+.0f}元 | "
                    f"{avg_pnl_pct:+.1f}% | {r['primary_attribution'] or ''} |\n"
                )

        if high_risk_count == 0:
            lines.append("| — | 无高风险标的 | — | — | — | — | — |\n")

        # 按胜率排序的统计
        lines.append("\n## 历史胜率概览\n\n")
        lines.append("| 胜率区间 | 标的数 | 备注 |\n")
        lines.append("|----------|--------|------|\n")

        win_rates = []
        for r in rows:
            total = r["cnt"] or 0
            wins = r["wins"] or 0
            win_rate = wins / total if total > 0 else 0
            win_rates.append(win_rate)

        if win_rates:
            wr_high = sum(1 for wr in win_rates if wr >= 0.5)
            wr_mid = sum(1 for wr in win_rates if 0.2 <= wr < 0.5)
            wr_low = sum(1 for wr in win_rates if 0 < wr < 0.2)
            wr_zero = sum(1 for wr in win_rates if wr == 0)
            lines.append(f"| ≥50% (表现良好) | {wr_high} | 🟢 可持续关注 |\n")
            lines.append(f"| 20-50% (一般) | {wr_mid} | 🟡 需结合盈亏比 |\n")
            lines.append(f"| <20% (较差) | {wr_low} | 🟡 谨慎买入 |\n")
            lines.append(f"| 0% (全亏) | {wr_zero} | 🔴 建议回避 |\n")

        lines.append("\n---\n")
        lines.append("*报告自动生成，高风险标的为累计亏损≤-5000元或亏损≥2次+均亏≤-3%*\n")

        return "".join(lines)

    except sqlite3.Error as e:
        return f"❌ 数据库错误: {e}"


def query_stock(code: str, db_path: str = ATTRIBUTION_DB) -> None:
    """查询单只股票归因历史。"""
    history = get_stock_history(code, db_path)
    if not history:
        print(f"⚠️ 无法查询 {code}（代码格式或数据库问题）")
        return

    print(f"\n{'='*50}")
    print(f"  归因历史查询: {history['name']}({history['code']})")
    print(f"{'='*50}")
    print(f"  风险标签: {history.get('risk_label', '⚪')}")
    for reason in history.get("risk_reasons", []):
        print(f"  理由: {reason}")
    print(f"  总交易: {history['total_trades']} 笔")
    print(f"  胜率: {history.get('win_rate', 0)*100:.1f}% ({history.get('wins', 0)}胜/{history.get('losses', 0)}负)")
    print(f"  累计盈亏: {history.get('total_pnl', 0):+.0f}元")
    print(f"  均收益/笔: {history.get('avg_pnl_pct', 0):+.2f}%")
    print(f"  最大亏损: {history.get('max_loss_pct', 0):+.2f}%")
    print(f"  最大盈利: {history.get('max_win_pct', 0):+.2f}%")
    print(f"  归因分布: {history.get('attribution_types', {})}")
    print(f"  最近交易:")
    for t in history.get("recent_trades", []):
        print(f"    {t['date']} | {t['direction']} | {t.get('pnl', 0):+.0f}元 | {t.get('pnl_pct', 0):+.2f}% | {t.get('attribution', '')} | {t.get('root_cause', '')}")


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="归因→选股反馈过滤器 M-2")
    p.add_argument("--input", help="输入候选文件 (CSV/JSON)")
    p.add_argument("--output", default="", help="输出过滤后文件")
    p.add_argument("--pipe", action="store_true", help="从 stdin 管道输入（每行一个代码）")
    p.add_argument("--query", help="查询单只股票归因历史")
    p.add_argument("--report", action="store_true", help="生成归因反馈报告")
    p.add_argument("--include-red", action="store_true", help="不剔除🔴高风险标的")
    p.add_argument("--db", default=ATTRIBUTION_DB, help="归因数据库路径")
    a = p.parse_args()

    if a.query:
        query_stock(a.query, a.db)
        return 0

    if a.report:
        report = generate_report(a.db)
        print(report)
        # 输出到文件
        report_path = os.path.join(r"D:\zcodesj\reports",
                                   f"attribution_feedback_report_{_today()}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"📄 报告已写入: {report_path}")
        return 0

    if a.pipe:
        codes = [line.strip() for line in sys.stdin if line.strip()]
        picks = [{"code": c, "name": ""} for c in codes]
        filtered = filter_picks(picks, db_path=a.db, exclude_red=not a.include_red)

        if a.output:
            ext = os.path.splitext(a.output)[1].lower()
            if ext == ".json":
                with open(a.output, "w", encoding="utf-8") as f:
                    json.dump(filtered, f, ensure_ascii=False, indent=2)
                print(f"📄 输出: {a.output}")
            else:
                with open(a.output, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["code", "name", "risk_label", "risk_reasons",
                                "history.total_trades", "history.win_rate"])
                    for fp in filtered:
                        h = fp.get("history", {})
                        w.writerow([
                            fp["code"], fp.get("name", ""),
                            fp.get("risk_label", ""),
                            "; ".join(fp.get("risk_reasons", [])),
                            h.get("total_trades", 0),
                            h.get("win_rate", ""),
                        ])
                print(f"📄 输出: {a.output}")
        else:
            # 输出到终端
            for fp in filtered:
                label = fp.get("risk_label", "⚪")
                reasons = "; ".join(fp.get("risk_reasons", []))
                h = fp.get("history", {})
                print(f"{label} {fp['code']} {fp.get('name', '')} "
                      f"(交易{h.get('total_trades', 0)}笔 胜率{h.get('win_rate', 0)*100:.0f}%)")
                if reasons:
                    print(f"    ⚠️ {reasons}")
        return 0

    if a.input:
        ext = os.path.splitext(a.input)[1].lower()
        if ext == ".csv":
            output = a.output or a.input.replace(".csv", "_filtered.csv")
            return filter_csv(a.input, output, exclude_red=not a.include_red)
        elif ext == ".json":
            with open(a.input, "r", encoding="utf-8") as f:
                picks = json.load(f)
            filtered = filter_picks(picks, db_path=a.db, exclude_red=not a.include_red)
            output = a.output or a.input.replace(".json", "_filtered.json")
            with open(output, "w", encoding="utf-8") as f:
                json.dump(filtered, f, ensure_ascii=False, indent=2)
            print(f"📄 输出: {output}")
            return 0
        else:
            print(f"❌ 不支持的文件格式: {ext}", file=sys.stderr)
            return 2

    # 无参数：显示帮助
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())