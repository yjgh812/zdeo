#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L-3: 组合优化体系（bp+ep最优组合构建）
========================================

问题背景：
  bp+ep月度选股输出Top20候选，但"怎么买"没有约束：
  - 行业过于集中（2024年Top20全是银行股）
  - 个股间相关性未知（同行业同涨同跌）
  - 仓位分配凭感觉（等权或按排名随意）

L-3解决：
  基于bp+ep候选+历史相关性+行业约束，输出最优权重分配。

方法：
  1. 读bp+ep月度Top20候选（含代码+行业+得分）
  2. 从历史K线计算候选间的相关性矩阵
  3. 行业约束：单行业≤30%
  4. 个股约束：单只≤20%，最低≥1%
  5. 优化目标：等风险贡献(ERC) 或 均值-方差(Mean-Variance)
  6. 输出最优权重+风险预算

用法：
    python l3_portfolio_optimizer.py                    # 默认ERC优化
    python l3_portfolio_optimizer.py --method meanvar   # 均值-方差优化
    python l3_portfolio_optimizer.py --input picks.csv  # 从CSV输入候选
    python l3_portfolio_optimizer.py --json             # JSON输出
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_DIR = r"D:\zcodesj\reports"

SECTOR_MAX_PCT = 0.30
STOCK_MAX_PCT = 0.20
STOCK_MIN_PCT = 0.01

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")

def norm_code(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6)

def load_picks_from_csv(path: str) -> list[dict]:
    """从CSV读取bp+ep候选。"""
    import csv
    picks = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = norm_code(row.get("code", ""))
            if code:
                picks.append({
                    "code": code,
                    "name": row.get("name", row.get("名称", "")),
                    "score": float(row.get("score", row.get("综合得分", 0))),
                    "sector": row.get("sector", row.get("行业", "")),
                })
    return picks

def load_picks_from_db(date_str: str) -> list[dict]:
    """从数据库读取bp+ep最新候选。"""
    if not os.path.exists(DB):
        return []
    try:
        con = sqlite3.connect(DB)
        cursor = con.execute(
            "SELECT code, name, composite_score, sector FROM stock_factors "
            "WHERE date = (SELECT MAX(date) FROM stock_factors) "
            "ORDER BY composite_score DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        con.close()
        return [
            {"code": norm_code(r[0]), "name": r[1] or "", "score": r[2] or 0, "sector": r[3] or ""}
            for r in rows
        ]
    except sqlite3.Error:
        return []

def build_correlation_matrix(codes: list[str]) -> dict:
    """从历史日线计算候选间的相关系数矩阵。"""
    if not os.path.exists(DB) or len(codes) < 2:
        return {}
    try:
        con = sqlite3.connect(DB)
        # 取最近60个交易日的日线
        placeholders = ",".join("?" * len(codes))
        rows = con.execute(
            f"SELECT code, date, change_pct FROM stock_daily_meta "
            f"WHERE code IN ({placeholders}) AND date >= "
            f"(SELECT MIN(date) FROM (SELECT date FROM stock_daily_meta "
            f"WHERE code IN ({placeholders}) ORDER BY date DESC LIMIT 60))"
            f"ORDER BY code, date",
            codes * 2
        ).fetchall()
        con.close()

        # 组织为 {code: [returns]}
        ret_dict: dict[str, list[float]] = {}
        for r in rows:
            code = r[0]
            pct = r[2]
            if pct is None:
                continue
            ret_dict.setdefault(code, []).append(pct)

        # 计算相关系数矩阵
        corr_matrix = {}
        for c1 in codes:
            corr_matrix[c1] = {}
            r1 = ret_dict.get(c1, [])
            if not r1:
                continue
            for c2 in codes:
                if c1 == c2:
                    corr_matrix[c1][c2] = 1.0
                    continue
                r2 = ret_dict.get(c2, [])
                if not r2:
                    corr_matrix[c1][c2] = 0.0
                    continue
                # 对齐长度
                min_len = min(len(r1), len(r2))
                if min_len < 5:
                    corr_matrix[c1][c2] = 0.0
                    continue
                a, b = r1[:min_len], r2[:min_len]
                n = min_len
                mean_a, mean_b = sum(a)/n, sum(b)/n
                cov = sum((a[i]-mean_a)*(b[i]-mean_b) for i in range(n)) / n
                std_a = (sum((x-mean_a)**2 for x in a)/n)**0.5
                std_b = (sum((x-mean_b)**2 for x in b)/n)**0.5
                corr = cov / (std_a * std_b) if std_a * std_b > 0 else 0
                corr_matrix[c1][c2] = round(max(-1, min(1, corr)), 4)
        return corr_matrix
    except sqlite3.Error:
        return {}

def optimize_erc(picks: list[dict], corr: dict) -> list[dict]:
    """等风险贡献(ERC)优化——使每只股票对组合风险的贡献相等。"""
    n = len(picks)
    if n == 0:
        return []

    codes = [p["code"] for p in picks]

    # 简单ERC近似：相关性越低权重越高
    # 实际ERC需要迭代求解，这里用启发式近似
    weights = []
    for i, c in enumerate(codes):
        avg_corr = 0.0
        count = 0
        for j, c2 in enumerate(codes):
            if i != j:
                avg_corr += abs(corr.get(c, {}).get(c2, 0))
                count += 1
        avg_corr = avg_corr / count if count > 0 else 0.5

        # 低相关性→高权重，高相关性→低权重
        raw_w = 1.0 / (avg_corr + 0.3)
        weights.append(raw_w)

    # 归一化
    total = sum(weights)
    weights = [w / total for w in weights]

    # 应用约束
    result = []
    sector_weights: dict[str, float] = {}
    for i, p in enumerate(picks):
        w = weights[i]
        p["weight"] = round(w, 4)
        p["corr_to_avg"] = round(
            sum(abs(corr.get(p["code"], {}).get(c, 0))
                for c in codes if c != p["code"])
            / max(len(codes)-1, 1), 4
        )
        result.append(p)
        sector = p.get("sector", "")
        sector_weights[sector] = sector_weights.get(sector, 0) + w

    # 行业约束裁剪
    for sector, sw in sector_weights.items():
        if sw > SECTOR_MAX_PCT:
            scale = SECTOR_MAX_PCT / sw
            for p in result:
                if p.get("sector") == sector:
                    p["weight_capped"] = round(p["weight"] * scale, 4)
                else:
                    p["weight_capped"] = p["weight"]

            # 重新归一化
            total_capped = sum(p.get("weight_capped", 0) for p in result)
            for p in result:
                p["weight_final"] = round(p.get("weight_capped", 0) / total_capped, 4)
        else:
            for p in result:
                p["weight_final"] = p["weight"]

    # 个股上限/下限约束
    for p in result:
        p["weight_final"] = max(STOCK_MIN_PCT, min(STOCK_MAX_PCT, p["weight_final"]))

    total_final = sum(p["weight_final"] for p in result)
    for p in result:
        p["weight_final"] = round(p["weight_final"] / total_final, 4)

    return result

def optimize_meanvar(picks: list[dict], corr: dict) -> list[dict]:
    """均值-方差优化(最大分散度)。"""
    n = len(picks)
    if n == 0:
        return []

    codes = [p["code"] for p in picks]

    # 最大分散度：最小化平均相关性
    weights = []
    for i, c in enumerate(codes):
        avg_corr = 0.0
        count = 0
        for j, c2 in enumerate(codes):
            if i != j:
                avg_corr += abs(corr.get(c, {}).get(c2, 0))
                count += 1
        avg_corr = avg_corr / count if count > 0 else 0.5
        # 得分加权：高得分+低相关性→高权重
        score = picks[i].get("score", 0)
        raw_w = (1.0 + score) / (avg_corr + 0.3)
        weights.append(raw_w)

    total = sum(weights)
    weights = [w / total for w in weights]

    result = []
    for i, p in enumerate(picks):
        p["weight"] = round(weights[i], 4)
        p["corr_to_avg"] = round(
            sum(abs(corr.get(p["code"], {}).get(c, 0))
                for c in codes if c != p["code"])
            / max(len(codes)-1, 1), 4
        )
        p["weight_final"] = max(STOCK_MIN_PCT, min(STOCK_MAX_PCT, p["weight"]))
        result.append(p)

    total_final = sum(p["weight_final"] for p in result)
    for p in result:
        p["weight_final"] = round(p["weight_final"] / total_final, 4)

    return result

def generate_report(
    picks: list[dict], method: str, corr: dict
) -> str:
    """生成组合优化报告。"""
    lines = []
    lines.append("# L-3 组合优化报告\n")
    lines.append(f"**方法**: {method} | **日期**: {_today()}\n")
    lines.append(f"**候选数**: {len(picks)}\n")
    lines.append(f"**约束**: 单行业≤{SECTOR_MAX_PCT*100:.0f}% | 单只≤{STOCK_MAX_PCT*100:.0f}% | 单只≥{STOCK_MIN_PCT*100:.0f}%\n\n")

    # 权重分布
    lines.append("## 权重分配\n\n")
    lines.append("| 代码 | 名称 | 行业 | 得分 | 平均相关性 | 权重 |\n")
    lines.append("|------|------|------|------|------------|------|\n")
    for p in picks:
        lines.append(
            f"| {p['code']} | {p['name']} | {p.get('sector', '-')} "
            f"| {p.get('score', '-')} | {p.get('corr_to_avg', '-')} "
            f"| {p['weight_final']*100:.1f}% |\n"
        )

    # 行业分布
    lines.append("\n## 行业分布\n\n")
    sector_w: dict[str, float] = {}
    for p in picks:
        s = p.get("sector", "未知")
        sector_w[s] = sector_w.get(s, 0) + p["weight_final"]
    lines.append("| 行业 | 总权重 |\n|------|--------|\n")
    for s, w in sorted(sector_w.items(), key=lambda x: -x[1]):
        flag = "⚠️" if w > SECTOR_MAX_PCT else ""
        lines.append(f"| {s} | {w*100:.1f}% {flag} |\n")

    # 相关性矩阵摘要
    if corr:
        lines.append("\n## 相关性矩阵摘要\n\n")
        codes = [p["code"] for p in picks]
        header = "| 代码 | " + " | ".join(c[-4:] for c in codes) + " |\n"
        sep = "|------|" + "|".join("------" for _ in codes) + "|\n"
        lines.append(header)
        lines.append(sep)
        for c1 in codes:
            row = f"| {c1[-4:]} "
            corr_vals = corr.get(c1, {})
            for c2 in codes:
                v = corr_vals.get(c2, 0)
                row += f"| {v:.2f} "
            row += "|\n"
            lines.append(row)

    lines.append("\n---\n*报告由L-3组合优化器自动生成*\n")
    return "".join(lines)

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="L-3 组合优化器")
    p.add_argument("--method", default="erc", choices=["erc", "meanvar"],
                   help="优化方法: erc(等风险贡献) / meanvar(均值方差)")
    p.add_argument("--input", default=None, help="输入候选CSV(默认从数据库读取)")
    p.add_argument("--json", action="store_true", help="JSON输出")
    a = p.parse_args()

    # 读取候选
    if a.input:
        picks = load_picks_from_csv(a.input)
    else:
        picks = load_picks_from_db(_today())

    if not picks:
        print("未读取到候选数据", file=sys.stderr)
        return 1

    print(f"候选数: {len(picks)}")

    # 计算相关性
    codes = [p["code"] for p in picks]
    corr = build_correlation_matrix(codes)
    print(f"相关性矩阵: {len(corr)} x {len(corr)}")

    # 优化
    if a.method == "meanvar":
        result = optimize_meanvar(picks, corr)
    else:
        result = optimize_erc(picks, corr)

    # 输出
    if a.json:
        output = {
            "method": a.method,
            "date": _today(),
            "total_candidates": len(result),
            "holdings": result,
            "sector_distribution": {},
        }
        for p in result:
            s = p.get("sector", "未知")
            output["sector_distribution"][s] = \
                output["sector_distribution"].get(s, 0) + p["weight_final"]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        report = generate_report(result, a.method, corr)
        print(report)

    # 写入文件
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"l3_portfolio_{_today()}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(generate_report(result, a.method, corr))
    print(f"报告已写入: {path}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())