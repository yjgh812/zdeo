"""
持仓缠论验证
==============
查询 westock 实时持仓，对每只跑缠论分解，输出结构报告。

使用：
  python -m chanlun_engine.verify.holdings_check
  python -m chanlun_engine.verify.holdings_check --codes 600519,000858
"""
from __future__ import annotations
import sys
import json
from pathlib import Path
from typing import List, Dict, Optional

from ..analyzer import ChanAnalyzer
from ..data_loader import get_daily_kline

# westock_client 路径
_WESTOCK_DIR = Path(r"D:\workbuddyspace")
if str(_WESTOCK_DIR) not in sys.path:
    sys.path.insert(0, str(_WESTOCK_DIR))


def fetch_holdings() -> List[Dict]:
    """
    查询 westock 实时持仓。
    返回 [{code, name, qty, cost_price, current_price, pnl_pct}, ...]
    """
    try:
        from westock_client import WeStockClient
        ws = WeStockClient()
        raw = ws.positions()
        if not raw:
            return []
        # 解析 MCP 返回格式
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict) and "data" in raw:
            raw = raw["data"]
        if isinstance(raw, list):
            return raw
        return []
    except ImportError:
        print("[WARN] westock_client 不可用，请用 --codes 手动传入")
        return []
    except Exception as e:
        print(f"[WARN] 持仓查询失败: {e}")
        return []


def analyze_holding(code: str, name: str = "",
                    lookback: int = 500) -> Dict:
    """
    对单只持仓做缠论分析。

    返回 dict:
      code, name, latest_price,
      n_strokes, n_pivots, n_bsp,
      current_pivot: {zg, zd, direction, evolution, position},
      current_trend: {type, direction},
      latest_bsp: {type, price, details},
      divergences: [...],
      health: "healthy"/"caution"/"danger"
    """
    df = get_daily_kline(code)
    if df is None or df.empty:
        return {"code": code, "name": name, "error": "无K线数据"}

    analyzer = ChanAnalyzer(pivot_source="stroke")
    result = analyzer.analyze(df)

    latest_price = result.latest_price
    info = {
        "code": code,
        "name": name,
        "latest_price": latest_price,
        "n_strokes": len(result.strokes),
        "n_pivots": len(result.pivots),
        "n_bsp": len(result.buy_sell_points),
    }

    # 当前中枢
    cp = result.current_pivot
    if cp:
        info["current_pivot"] = {
            "zg": round(cp.zg, 2),
            "zd": round(cp.zd, 2),
            "zz": round(cp.zz, 2),
            "direction": cp.direction,
            "evolution": cp.evolution,
            "position": cp.position_vs_pivot(latest_price),
        }

    # 当前走势
    ct = result.current_trend
    if ct:
        info["current_trend"] = {
            "type": ct.type,
            "direction": ct.direction,
            "n_pivots": ct.n_pivots,
        }

    # 最近买卖点
    if result.buy_sell_points:
        bsp = result.buy_sell_points[-1]
        info["latest_bsp"] = {
            "type": bsp.type,
            "price": round(bsp.price, 2),
            "details": bsp.details,
        }

    # 背驰
    if result.divergences:
        info["divergences"] = [
            {
                "type": d.type,
                "direction": d.direction,
                "ratio": round(d.strength_ratio, 2),
                "valid": d.valid,
            }
            for d in result.divergences[-2:]
        ]

    # 健康度评估
    info["health"] = _assess_health(info)

    return info


def _assess_health(info: Dict) -> str:
    """
    评估持仓的缠论结构健康度。

    healthy : 上涨趋势/三买后/中枢上方
    caution : 震荡/中枢内/接近上沿
    danger  : 下跌趋势/顶背驰/中枢下方/一卖
    """
    health = "caution"

    trend = info.get("current_trend", {})
    pivot = info.get("current_pivot", {})
    bsp = info.get("latest_bsp", {})
    divs = info.get("divergences", [])

    # Danger 条件
    if trend.get("direction") == "down":
        health = "danger"
    elif any(d.get("direction") == "top" for d in divs):
        health = "danger"
    elif bsp.get("type", "").startswith("sell"):
        health = "danger"
    elif pivot.get("position") == "below":
        health = "danger"
    # Healthy 条件
    elif trend.get("direction") == "up" and pivot.get("position") == "above":
        health = "healthy"
    elif bsp.get("type") in ("buy2", "buy3"):
        health = "healthy"

    return health


def generate_report(holdings: List[Dict]) -> str:
    """生成 Markdown 报告"""
    lines = ["# 持仓缠论分析报告\n"]
    lines.append(f"持仓数: {len(holdings)}\n")

    # 汇总
    healthy = [h for h in holdings if h.get("health") == "healthy"]
    caution = [h for h in holdings if h.get("health") == "caution"]
    danger = [h for h in holdings if h.get("health") == "danger"]

    lines.append("## 健康度汇总\n")
    lines.append(f"| 状态 | 数量 | 占比 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| 🟢 健康 | {len(healthy)} | {len(healthy)/max(len(holdings),1)*100:.0f}% |")
    lines.append(f"| 🟡 警告 | {len(caution)} | {len(caution)/max(len(holdings),1)*100:.0f}% |")
    lines.append(f"| 🔴 危险 | {len(danger)} | {len(danger)/max(len(holdings),1)*100:.0f}% |\n")

    # 明细
    lines.append("## 持仓明细\n")
    lines.append("| 代码 | 名称 | 最新价 | 中枢ZG | 中枢ZD | 位置 | 方向 | 走势 | 最近信号 | 健康度 |")
    lines.append("|------|------|--------|--------|--------|------|------|------|----------|--------|")

    for h in holdings:
        code = h.get("code", "")
        name = h.get("name", "")
        price = h.get("latest_price", 0)
        pivot = h.get("current_pivot", {})
        zg = pivot.get("zg", "-")
        zd = pivot.get("zd", "-")
        pos = pivot.get("position", "-")
        pdir = pivot.get("direction", "-")
        trend = h.get("current_trend", {})
        ttype = trend.get("type", "-")
        bsp = h.get("latest_bsp", {})
        bsp_str = f"{bsp.get('type', '-')}" if bsp else "-"
        health = h.get("health", "-")
        emoji = {"healthy": "🟢", "caution": "🟡", "danger": "🔴"}.get(health, "")
        lines.append(
            f"| {code} | {name} | {price:.2f} | {zg} | {zd} | "
            f"{pos} | {pdir} | {ttype} | {bsp_str} | {emoji}{health} |"
        )

    # 危险持仓详情
    if danger:
        lines.append("\n## ⚠️ 危险持仓详情\n")
        for h in danger:
            lines.append(f"### {h['code']} {h.get('name', '')}")
            lines.append(f"- 最新价: {h.get('latest_price', 0):.2f}")
            pivot = h.get("current_pivot", {})
            if pivot:
                lines.append(f"- 中枢: ZG={pivot.get('zg')} ZD={pivot.get('zd')} "
                             f"位置={pivot.get('position')} 方向={pivot.get('direction')}")
            bsp = h.get("latest_bsp", {})
            if bsp:
                lines.append(f"- 最近信号: {bsp.get('type')} @ {bsp.get('price')} {bsp.get('details')}")
            divs = h.get("divergences", [])
            if divs:
                for d in divs:
                    lines.append(f"- 背驰: {d.get('type')} {d.get('direction')} 力度比={d.get('ratio')}")
            lines.append("")

    return "\n".join(lines)


def main(codes: Optional[List[str]] = None):
    """主入口"""
    print("=" * 60)
    print("持仓缠论分析")
    print("=" * 60)

    if codes:
        # 手动传入代码
        holdings_raw = [{"code": c, "name": ""} for c in codes]
    else:
        # 查 westock 持仓
        holdings_raw = fetch_holdings()
        if not holdings_raw:
            print("[INFO] 无持仓数据。示例用法: --codes 600519,000858")
            return

    print(f"待分析: {len(holdings_raw)} 只\n")

    results = []
    for h in holdings_raw:
        code = h.get("code", "")
        name = h.get("name", h.get("stock_name", ""))
        # 标准化code（去后缀）
        code_clean = code.replace(".SH", "").replace(".SZ", "")
        code_clean = code_clean.replace("sh", "").replace("sz", "")

        print(f"分析 {code_clean} {name}...", end=" ")
        info = analyze_holding(code_clean, name)
        results.append(info)
        health = info.get("health", "?")
        print(f"{health}")

    # 生成报告
    report = generate_report(results)
    report_path = Path(r"D:\zcodesj\chanlun_engine\reports")
    report_path.mkdir(parents=True, exist_ok=True)
    out_file = report_path / "holdings_chanlun_report.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\n报告已保存: {out_file}")
    print("\n" + report)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=str, default=None,
                        help="逗号分隔的股票代码（如 600519,000858）")
    args = parser.parse_args()

    if args.codes:
        main([c.strip() for c in args.codes.split(",")])
    else:
        main()
