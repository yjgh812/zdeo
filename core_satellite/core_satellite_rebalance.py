# -*- coding: utf-8 -*-
"""
core_satellite_rebalance.py — 屠龙系统·核心-卫星月度调仓执行器（2026-08-11 知微落地）

【背景】
  主人 8/11 决策：自然出清 + 9月建仓。9月1日（月度调仓日）执行：
    1. 卖出"表现最差"的持仓（按收益率升序，最多 20 只）
    2. 买入 bp+ep 等权rank Top20 建核心底仓（每只 ≈ 2%，总 40%）
    3. 与 bp+ep 重叠的持仓保留（不卖不买）

【铁律内嵌】
  1. 默认 dry-run；--execute 需显式传参（卖出三铁律：卖多少必须主人确认）
  2. 卖出走 14:45-15:00 尾盘窗口（卖出执行纪律；非窗口 --execute 拒绝执行）
  3. 分批执行：每批 5 只、批间间隔 60s（禁同一分钟批量砸盘）
  4. 凡股票代码必须带名称
  5. 绝不使用 cancel_all（8/11 事故教训）

【用法】
  python core_satellite_rebalance.py                 # dry-run 输出买卖清单
  python core_satellite_rebalance.py --execute       # 14:45-15:00 尾盘窗口实际执行
  python core_satellite_rebalance.py --total 1017843 # 指定总资产（默认实时查询）

【挂载】
  9/1 14:45 一次性 cron：python core_satellite_rebalance.py --execute
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 依赖模块
_WS = Path(r"D:\workbuddyspace")
_MF = Path(r"D:\zcodesj\multifactor")
sys.path.insert(0, str(_WS))
sys.path.insert(0, str(_MF))

# ============ 配置 ============
CORE_WEIGHT = 0.40        # 核心底仓占总资产 40%
CORE_N = 20               # 20 只
MAX_REPLACE = 20          # 最多替换 20 只
BATCH_SIZE = 5            # 每批 5 只
BATCH_INTERVAL_S = 60     # 批间隔 60s（禁批量砸盘）
TAIL_WINDOW = ("14:45", "15:00")  # 卖出尾盘窗口
LOT = 100                 # A股 100 股整数倍


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ============ 1. 实时持仓 ============
def load_positions():
    """westock 实时持仓（铁律：不读本地缓存）。
    返回 [{code, name, cost_price, current_price, profit, profit_rate, shares}]
    """
    from westock_client import WeStockClient
    ws = WeStockClient()
    raw = ws.positions()
    if not isinstance(raw, dict) or not raw.get("ok"):
        raise RuntimeError(f"westock positions 失败: {str(raw)[:200]}")
    data = raw.get("data", {})
    items = data.get("positions", []) if isinstance(data, dict) else []
    positions = []
    for r in items:
        code = str(r.get("code", ""))
        name = str(r.get("name", code))
        shares = int(float(r.get("quantity", 0) or 0))
        if not code or shares <= 0:
            continue
        cost = float(r.get("costPrice", 0) or 0)
        cur = float(r.get("currentPrice", 0) or 0)
        profit = float(r.get("profit", 0) or 0)
        profit_rate = float(r.get("profitRate", 0) or 0)
        if cost <= 0:
            # 嶒泰类数据 bug：从历史买入价回填（复用 enforce_stop_loss 逻辑）
            try:
                h = ws.call("portfolio_paper_history",
                            {"code": code, "direction": "buy",
                             "range": "recent", "limit": 3})
                records = h.get("data", {}).get("records", []) if isinstance(h, dict) else []
                if records:
                    cost = float(records[0].get("price", 0) or 0)
                    profit_rate = (cur - cost) / cost * 100 if cost > 0 else 0
            except Exception:
                pass
        positions.append({
            "code": code, "name": name, "shares": shares,
            "cost_price": cost, "current_price": cur,
            "profit": profit, "profit_rate": profit_rate,
        })
    return positions


def load_total_assets():
    """总资产 = 市值 + 现金（实时）"""
    from westock_client import WeStockClient
    ws = WeStockClient()
    raw = ws.portfolio()
    data = raw.get("data", {})
    p = data.get("portfolio", data)
    total = float(p.get("totalAssets", 0) or 0)
    return total


# ============ 2. bp+ep TopN（复用生产 picks 逻辑）============
def load_bp_ep_top20():
    """加载 bp/ep 等权rank 合成，取 Top40（20 只买入 + 20 只顺延候选）。
    逻辑与 bp_ep_monthly_picks.py 一致（生产验证过）。
    高价股（一手超单只预算）从 21-40 名顺延替代。
    """
    import config
    import factor_loader
    import stock_filter

    from config import FACTOR_REGISTRY

    date = factor_loader.get_recent_trade_date()
    log(f"bp+ep 选股日: {date}")

    pieces = []
    for name in ("bp", "ep"):
        fdef = [f for f in FACTOR_REGISTRY if f["name"] == name][0]
        dt_int = int(date)
        start = str(dt_int - 5)
        raw = factor_loader.load_factor_values(fdef, start, date)
        raw["factor_value"] = pd_to_numeric(raw["factor_value"])
        raw = raw.dropna(subset=["factor_value"])
        raw["date"] = raw["date"].astype(str)
        latest = raw["date"].max()
        raw = raw[raw["date"] == latest]
        if fdef["dir"] == -1:
            raw["factor_value"] = -raw["factor_value"]
        raw[f"rank_{name}"] = raw["factor_value"].rank(pct=True)
        pieces.append(raw[["code", f"rank_{name}"]])
        log(f"  {name}: {len(raw)}只 (日期{latest})")

    merged = pieces[0].merge(pieces[1], on="code", how="inner")
    merged["composite_score"] = merged[["rank_bp", "rank_ep"]].mean(axis=1)
    merged = merged.sort_values("composite_score", ascending=False).reset_index(drop=True)

    # 股票池过滤（流动性/ST 等，与生产一致）
    filtered = stock_filter.apply_filters(merged)
    log(f"  过滤后: {len(filtered)}只")

    top = filtered.head(40).copy()
    top["rank"] = range(1, len(top) + 1)

    # 补充名称/行业（meta 无 close 列，价格另查）
    try:
        meta = factor_loader.get_stock_meta()[["code", "name", "industry"]]
        top = top.merge(meta, on="code", how="left")
    except Exception as e:
        log(f"  ⚠️ meta 补充失败: {e}")
    # 补充价格（最近收盘）
    try:
        codes = top["code"].astype(str).tolist()
        kline = factor_loader.get_daily_kline(codes, date, date)
        if not kline.empty:
            latest = kline.sort_values("date").groupby("code").tail(1)
            top = top.merge(latest[["code", "close"]], on="code", how="left")
    except Exception as e:
        log(f"  ⚠️ 价格补充失败: {e}")
    return top


def pd_to_numeric(s):
    import pandas as pd
    return pd.to_numeric(s, errors="coerce")


# ============ 3. 调仓计划 ============
def build_plan(positions, top20, total_assets, top_candidates=None):
    """生成调仓计划。

    top_candidates: 候选池（默认 = top20）。Top20 中一手金额超预算的
    高价股（如茅台 17万/手）顺延到 21+ 名次替代，保证 20 只满仓。
    策略不变：仍是因子得分最高的一批，只是跳过不可买的。

    Returns:
        dict: {sells: [...], buys: [...], keeps: [...], skipped_expensive: [...]}
    """
    pos_by_code = {p["code"]: p for p in positions}
    top_codes = set(top20["code"].astype(str).str.zfill(6))

    # 持仓标准化为 6 位
    pos_std = {}
    for p in positions:
        pos_std[p["code"][-6:]] = p

    # 1) 识别最差持仓（按收益率升序），最多 20 只；重叠的排除在外
    worst = sorted(
        [p for p in positions if p["code"][-6:] not in top_codes],
        key=lambda p: p["profit_rate"],
    )
    sells = worst[:MAX_REPLACE]

    # 2) 买入目标：bp+ep Top20，一手超预算的顺延替代
    candidates = top_candidates if top_candidates is not None else top20
    target_amount = total_assets * CORE_WEIGHT / CORE_N  # 每只 2%

    buys = []
    skipped_expensive = []
    used = set()
    for _, row in candidates.iterrows():
        if len(buys) >= CORE_N:
            break
        code6 = str(row["code"]).zfill(6)
        if code6 in used:
            continue
        used.add(code6)
        name = str(row.get("name", code6))
        cur_price = float(row.get("close", 0) or 0)
        if cur_price <= 0:
            log(f"  ⚠️ {code6} {name} 无价格，跳过")
            continue
        existing = pos_std.get(code6)
        have_shares = existing["shares"] if existing else 0
        target_shares = int(target_amount / cur_price / LOT) * LOT
        buy_shares = max(target_shares - have_shares, 0)
        if buy_shares >= LOT:
            buys.append({
                "code": code6, "name": name, "shares": buy_shares,
                "price": cur_price, "amount": buy_shares * cur_price,
                "have": have_shares, "target": target_shares,
            })
        else:
            # 不买入原因分类：已持满 vs 一手超预算
            if have_shares > 0:
                skipped_expensive.append((code6, name, cur_price, "已持满"))
            else:
                skipped_expensive.append((code6, name, cur_price, "一手超预算"))

    # 3) 重叠保留
    keeps = [p for p in positions if p["code"][-6:] in top_codes]

    return {
        "sells": sells,
        "buys": buys,
        "keeps": keeps,
        "skipped_expensive": skipped_expensive,
        "target_amount": target_amount,
    }


# ============ 4. 输出 ============
def print_plan(plan, total_assets):
    print("=" * 72)
    print(" 核心-卫星月度调仓计划（dry-run）")
    print(f" 总资产: {total_assets:,.0f} 元 | 核心目标: {total_assets*0.40:,.0f} 元 "
          f"({CORE_N}只×{total_assets*0.40/CORE_N:,.0f}元/只)")
    print("=" * 72)

    print(f"\n【卖出】{len(plan['sells'])} 只（表现最差，按收益率升序）:")
    for i, s in enumerate(plan["sells"], 1):
        print(f"  {i:2d}. {s['code']} {s['name']} | 收益率 {s['profit_rate']:+.1f}% "
              f"| 盈亏 {s['profit']:+,.0f} | {s['shares']}股 @ {s['current_price']:.2f}")

    print(f"\n【买入】{len(plan['buys'])} 只（bp+ep Top20，每只≈2%）:")
    for i, b in enumerate(plan["buys"], 1):
        print(f"  {i:2d}. {b['code']} {b['name']} | 买 {b['shares']}股 "
              f"≈{b['amount']:,.0f}元 @ {b['price']:.2f}"
              f"{'（已持'+str(b['have'])+'股，补差）' if b['have'] else ''}")

    if plan.get("skipped_expensive"):
        print(f"\n【候选跳过】{len(plan['skipped_expensive'])} 只:")
        for code6, name, price, reason in plan["skipped_expensive"]:
            if reason == "一手超预算":
                print(f"  {code6} {name} @ {price:.2f}（一手 {price*100:,.0f}元 > 预算 {plan['target_amount']:,.0f}元，顺延替代）")
            else:
                print(f"  {code6} {name} @ {price:.2f}（已持约满 2% 仓位，无需加仓）")

    if plan["keeps"]:
        print(f"\n【重叠保留】{len(plan['keeps'])} 只（bp+ep 已在持仓中）:")
        for k in plan["keeps"]:
            print(f"  {k['code']} {k['name']} | {k['shares']}股 收益率 {k['profit_rate']:+.1f}%")

    sell_amount = sum(s["shares"] * s["current_price"] for s in plan["sells"])
    buy_amount = sum(b["amount"] for b in plan["buys"])
    print(f"\n预计卖出回笼: {sell_amount:,.0f} 元 | 预计买入: {buy_amount:,.0f} 元")
    print(f"现金变化: {sell_amount - buy_amount:+,.0f} 元")
    print("\n⚠️ 加 --execute 在 14:45-15:00 尾盘窗口实际执行")
    print("⚠️ 执行前必须经主人确认（卖出三铁律）")


# ============ 5. 执行 ============
# 价格折溢价（确保限价单成交）
SELL_DISCOUNT = 0.99   # 卖出价 = 实时价 × 0.99（折价 1% 促成交）
BUY_PREMIUM = 1.01     # 买入价 = 实时价 × 1.01（溢价 1% 促成交）


def check_tail_window():
    """卖出走 14:45-15:00 尾盘窗口"""
    now = datetime.now().strftime("%H:%M")
    return TAIL_WINDOW[0] <= now <= TAIL_WINDOW[1]


def get_realtime_price(ws, code: str, fallback: float = 0.0) -> float:
    """取 9/1 当日实时价（不用过期收盘价）。
    复用 enforce_stop_loss 的 _extract_price 嵌套解析（8/11 P0 修复过）。
    """
    try:
        q = ws.quote(code)
        if isinstance(q, dict):
            # 顶层
            p = float(q.get("price", q.get("latest", 0)) or 0)
            if p > 0:
                return p
            # data 嵌套（ westock 实际结构 ）
            data = q.get("data", {})
            if isinstance(data, dict):
                if code in data and isinstance(data[code], dict):
                    data = data[code]
                p = float(data.get("price", data.get("latest", 0)) or 0)
                if p > 0:
                    return p
    except Exception as e:
        log(f"  ⚠️ {code} 实时价查询失败: {e}")
    return fallback


def execute_plan(plan):
    """分批执行：卖出 5只/批 → 间隔60s → 买入 5只/批。

    价格策略（修复 2026-08-12）：
      - 卖出价 = 当日实时价 × 0.99（折价确保成交，限价单 ≤ 现价）
      - 买入价 = 当日实时价 × 1.01（溢价确保成交，限价单 ≥ 现价）
      - 之前用 currentPrice/close 直接挂，模拟盘可能不成交
    """
    from westock_client import WeStockClient
    ws = WeStockClient()

    if not check_tail_window():
        now = datetime.now().strftime("%H:%M")
        print(f"❌ 当前 {now} 不在尾盘窗口 {TAIL_WINDOW[0]}-{TAIL_WINDOW[1]}，拒绝执行")
        print("   （卖出执行纪律：调仓卖出必须走尾盘窗口）")
        return 1

    # --- 卖出 ---
    print(f"\n【执行卖出】{len(plan['sells'])} 只，每批 {BATCH_SIZE} 只，批间隔 {BATCH_INTERVAL_S}s")
    errs = []
    for i in range(0, len(plan["sells"]), BATCH_SIZE):
        batch = plan["sells"][i:i + BATCH_SIZE]
        for s in batch:
            # 实时价（持仓接口 currentPrice 是 14:45 实时；这里再查一次行情保险）
            rt = get_realtime_price(ws, s["code"], fallback=s["current_price"])
            price = round(rt * SELL_DISCOUNT, 2)
            try:
                r = ws.sell(s["code"], price, s["shares"])
                r_ok = (isinstance(r, dict) and r.get("ok") is True
                        and not r.get("error"))
                if r_ok:
                    log(f"[SELL 成交] {s['code']} {s['name']} {s['shares']}股 "
                        f"@ {price:.2f}（实时 {rt:.2f} × 0.99）")
                else:
                    log(f"[SELL 失败] {s['code']} {s['name']}: {str(r)[:120]}")
                    errs.append(s["code"])
            except Exception as e:
                log(f"[SELL 异常] {s['code']} {s['name']}: {e}")
                errs.append(s["code"])
        if i + BATCH_SIZE < len(plan["sells"]):
            log(f"  批间等待 {BATCH_INTERVAL_S}s...")
            time.sleep(BATCH_INTERVAL_S)

    # --- 买入 ---
    print(f"\n【执行买入】{len(plan['buys'])} 只")
    for i in range(0, len(plan["buys"]), BATCH_SIZE):
        batch = plan["buys"][i:i + BATCH_SIZE]
        for b in batch:
            # 实时价（b['price'] 是 8/29 收盘，9/1 实时可能差很多）
            rt = get_realtime_price(ws, b["code"], fallback=b["price"])
            price = round(rt * BUY_PREMIUM, 2)
            # 重新按实时价算股数（避免预算超支或不足）
            target_shares = int(b["target"] * b["price"] / rt / LOT) * LOT if rt > 0 else b["shares"]
            shares = max(target_shares, LOT)
            try:
                r = ws.buy(b["code"], price, shares)
                r_ok = (isinstance(r, dict) and r.get("ok") is True
                        and not r.get("error"))
                if r_ok:
                    log(f"[BUY 成交] {b['code']} {b['name']} {shares}股 "
                        f"@ {price:.2f}（实时 {rt:.2f} × 1.01）")
                else:
                    log(f"[BUY 失败] {b['code']} {b['name']}: {str(r)[:120]}")
                    errs.append(b["code"])
            except Exception as e:
                log(f"[BUY 异常] {b['code']} {b['name']}: {e}")
                errs.append(b["code"])
        if i + BATCH_SIZE < len(plan["buys"]):
            log(f"  批间等待 {BATCH_INTERVAL_S}s...")
            time.sleep(BATCH_INTERVAL_S)

    # 结果汇总
    print("\n" + "=" * 72)
    if errs:
        print(f"❌ 执行完成，{len(errs)} 只有问题: {errs}")
        return 1
    print(f"✅ 执行完成: 卖 {len(plan['sells'])} 只 / 买 {len(plan['buys'])} 只，全部成功")
    print("   待 15:00 后核对持仓（用 MCP 实时查询）")
    return 0


# ============ main ============
def main():
    ap = argparse.ArgumentParser(description="核心-卫星月度调仓执行器")
    ap.add_argument("--execute", action="store_true", help="实际执行（默认 dry-run）")
    ap.add_argument("--total", type=float, default=0, help="指定总资产（默认实时查询）")
    args = ap.parse_args()

    print("核心-卫星月度调仓执行器")
    print("=" * 72)

    # 1. 实时持仓
    log("加载实时持仓...")
    positions = load_positions()
    log(f"  持仓 {len(positions)} 只")
    if not positions:
        print("❌ 无持仓，退出")
        return 1

    # 2. 总资产
    total_assets = args.total if args.total > 0 else load_total_assets()
    log(f"  总资产 {total_assets:,.0f} 元")

    # 3. bp+ep Top40（20 只买入 + 顺延候选）
    log("计算 bp+ep Top40...")
    top40 = load_bp_ep_top20()
    top20 = top40.head(20)
    log(f"  Top40 完成（{len(top40)} 只）")

    # 4. 调仓计划
    plan = build_plan(positions, top20, total_assets, top_candidates=top40)

    # 5. 输出/执行
    print_plan(plan, total_assets)
    if args.execute:
        return execute_plan(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
