#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portfolio_snapshot.py — B9 组合快照落地件（买入成交回报必附）。

背景: B9 条款要求买入成交回报必须附组合快照。westock 的
portfolio_paper_portfolio / portfolio_paper_positions 只能由交互会话调 MCP 获取,
脚本不能直连 MCP, 故采用"喂入模式": 会话把两个 MCP 返回的 JSON 原样转存文件后
喂给本脚本, 纯本地计算输出 markdown 快照段(无 SQL、无网络)。

用法:
  python portfolio_snapshot.py \
      --portfolio-json data/feed_portfolio.json \
      --positions-json data/feed_positions.json
  可选: --out reports/portfolio_snapshot_YYYYMMDD.md 同时落盘

输入字段容错(westock MCP 原始键 / snake_case / 中文别名均可):
  组合: data 包装或裸 dict; totalAssets/total_assets/总资产,
        marketValue/market_value/市值, availableFunds/cash/可用资金/现金
  持仓: data.positions / data.records / 顶层 list;
        code/name, marketValue/市值, profitRate/收益率/盈亏比例
        (小数与百分数自动判别: |x|<=3 视为小数×100), 缺失时用价格×数量兜底,
        价格仍缺则从 costPrice+latestPrice 兜底; industry/行业(可选, 供簇统计)

输出(markdown 段):
  ① 总资产/市值/现金+现金占比(<1% 标🔴近满仓)
  ② 单票集中度 TOP5(占比=市值/总资产, >20% 标🔴)
  ③ 止损带: profitRate<=-8% 票清单(无则"止损带0只")
  ④ 深亏带: -8%>profitRate>=-5% 票数(只报数)
  ⑤ 高乖离浮盈带: profitRate>=+25% 票清单(去弱留强/回吐保护关注)
  ⑥ 簇统计: 同行业>=3只的簇(positions 无行业字段则跳过并标注)

退出码: 0=成功 / 1=输入文件缺失或无法解析。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

STOP_BAND = -8.0      # 止损带阈值(%)
DEEP_LOSS_HI = -5.0   # 深亏带上界(不含): -8% < pr <= -5%
DEVIATION_BAND = 25.0  # 高乖离浮盈带阈值(%)
CONCENTRATION_RED = 20.0  # 单票集中度红线(%)
CASH_FULL_RED = 1.0   # 现金占比近满仓红线(%)
CLUSTER_MIN = 3       # 同行业簇最小只数


def _g(d: dict, *keys, default=None):
    """按候选键序取第一个非空值(键统一小写下划线匹配)。"""
    for k in keys:
        for kk, v in d.items():
            if str(kk).strip().lower().replace(" ", "") == k:
                if v is not None and v != "":
                    return v
    return default


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct(x) -> float | None:
    """收益率归一为百分数: |x|<=3 视为小数比例(0.26→26, -0.09→-9), 否则原值。"""
    v = _f(x)
    if v is None:
        return None
    return round(v * 100.0, 2) if abs(v) <= 3.0 else round(v, 2)


def norm6(code: str) -> str:
    c = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
            break
    return c.split(".")[0].zfill(6)


def load_portfolio(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("data") if isinstance(doc, dict) and isinstance(doc.get("data"), dict) else doc


def load_positions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        data = doc.get("data") if isinstance(doc.get("data"), dict) else doc
        for key in ("positions", "records", "list", "items"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def snapshot_md(portfolio: dict, positions: list[dict]) -> str:
    """核心计算, 返回 markdown 快照段文本。纯本地, 无副作用。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # ---- 持仓结构化 ----
    rows = []
    for p in positions:
        code6 = norm6(str(_g(p, "code", "ts_code", "symbol", default="")))
        name = str(_g(p, "name", "名称", default="")) or code6
        mv = _f(_g(p, "marketvalue", "market_value", "市值", "totalmarketvalue"))
        if not mv or mv <= 0:
            px = _f(_g(p, "price", "latestprice", "currentprice", "现价"))
            qty = _f(_g(p, "quantity", "volume", "数量", "持有数量"))
            if (not px or px <= 0 or not qty or qty <= 0):
                px = _f(_g(p, "costprice", "成本价"))
            mv = round(px * qty, 2) if (px and qty) else None
        pr = _pct(_g(p, "profitrate", "profit_rate", "收益率", "盈亏比例", "盈亏率"))
        ind = _g(p, "industry", "行业", "sector")
        rows.append({"code": code6, "name": name, "mv": mv, "pr": pr,
                     "ind": str(ind).strip() if ind else ""})

    total = _f(_g(portfolio, "totalassets", "total_assets", "总资产", "totalasset"))
    mkt = _f(_g(portfolio, "marketvalue", "market_value", "市值"))
    cash = _f(_g(portfolio, "availablefunds", "available_funds", "cash", "可用资金", "现金", "funds"))
    if total is None:
        total = round((mkt or 0) + (cash or 0), 2) or None
    if cash is None:
        cash = round((total or 0) - (mkt or 0), 2)
    if mkt is None:
        mkt = round(sum(r["mv"] or 0 for r in rows), 2) or None
    cash_ratio = round(cash / total * 100, 2) if (cash is not None and total) else None

    L: list[str] = [f"## 组合快照（B9）{now}", ""]
    # ① 总资产/市值/现金
    if total is None:
        L.append("- **① 组合层**: 组合json缺少可识别的总资产/市值/现金字段, 请核对字段名")
    else:
        flag = " 🔴近满仓" if (cash_ratio is not None and cash_ratio < CASH_FULL_RED) else ""
        cr = f"{cash_ratio}%" if cash_ratio is not None else "N/A"
        L.append(f"- **① 组合**: 总资产 {total:,.2f} | 持仓市值 {mkt or 0:,.2f} "
                 f"| 现金 {cash or 0:,.2f}（占比 {cr}）{flag}")
    # ② 单票集中度 TOP5
    ranked = sorted((r for r in rows if r["mv"] and total), key=lambda r: -r["mv"])
    L.append("- **② 单票集中度 TOP5**（占比=市值/总资产, >20%标🔴）:")
    if ranked:
        for r in ranked[:5]:
            c = r["mv"] / total * 100
            flag = " 🔴" if c > CONCENTRATION_RED else ""
            L.append(f"  - {r['code']} {r['name']} 市值 {r['mv']:,.2f} 占比 {c:.2f}%{flag}")
    else:
        L.append("  - （无有效持仓市值）")
    # ③ 止损带
    stops = [r for r in rows if r["pr"] is not None and r["pr"] <= STOP_BAND]
    if stops:
        L.append(f"- **③ 止损带**（profitRate<=-8%）{len(stops)} 只:")
        for r in stops:
            L.append(f"  - {r['code']} {r['name']} {r['pr']}%")
    else:
        L.append("- **③ 止损带0只**")
    # ④ 深亏带
    deep = [r for r in rows if r["pr"] is not None and STOP_BAND < r["pr"] <= DEEP_LOSS_HI]
    L.append(f"- **④ 深亏带**（-8%>profitRate>=-5%）: {len(deep)} 只")
    # ⑤ 高乖离浮盈带
    highs = [r for r in rows if r["pr"] is not None and r["pr"] >= DEVIATION_BAND]
    if highs:
        L.append(f"- **⑤ 高乖离浮盈带**（profitRate>=+25%）{len(highs)} 只（关注去弱留强/回吐保护）:")
        for r in highs:
            L.append(f"  - {r['code']} {r['name']} {r['pr']}%")
    else:
        L.append("- **⑤ 高乖离浮盈带0只**")
    # ⑥ 簇统计
    with_ind = [r for r in rows if r["ind"]]
    if not with_ind:
        L.append("- **⑥ 簇统计**: positions 无行业字段, 跳过簇统计")
    else:
        clusters: dict[str, list[dict]] = {}
        for r in with_ind:
            clusters.setdefault(r["ind"], []).append(r)
        big = {k: v for k, v in clusters.items() if len(v) >= CLUSTER_MIN}
        if big:
            L.append(f"- **⑥ 同行业≥{CLUSTER_MIN}只簇** {len(big)} 个:")
            for k, v in sorted(big.items(), key=lambda kv: -len(kv[1])):
                names = "、".join(f"{x['code']}{x['name']}" for x in v)
                L.append(f"  - {k}（{len(v)}只）: {names}")
        else:
            L.append(f"- **⑥ 簇统计**: 无同行业≥{CLUSTER_MIN}只的簇")
    if not rows:
        L.append("- ⚠ positions 为空或无法解析——以上各带均为空口径")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="B9 组合快照(喂入模式, 纯本地)")
    ap.add_argument("--portfolio-json", required=True, help="portfolio_paper_portfolio 返回json存档")
    ap.add_argument("--positions-json", required=True, help="portfolio_paper_positions 返回json存档")
    ap.add_argument("--out", default="", help="可选: 快照md同时落盘路径")
    a = ap.parse_args()
    try:
        portfolio = load_portfolio(a.portfolio_json)
        positions = load_positions(a.positions_json)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[错误] 输入文件读取失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    md = snapshot_md(portfolio, positions)
    print(md)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"📄 快照已落盘: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
