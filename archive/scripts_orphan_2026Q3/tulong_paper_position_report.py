#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯自选股(westock)模拟盘持仓分析（2026-08-21 建，屠龙系统新增功能②）

工作日 15:40 生成，收盘后持仓体检报告：
  1. 组合总览 —— westock portfolio_paper_portfolio（总资产/市值/现金/累计盈亏）
  2. 逐只持仓明细 —— MCP 持仓 + 腾讯批量行情（当日涨跌%）+ qfq 库（20日涨跌/60日峰值）
  3. 组合结构 —— 东财板块 sector_member 反查，按板块聚合市值占比
  4. 风控预警 —— 屠龙标准 v1.0 止盈止损（-8%硬止损/移动止损/阶梯止盈 10-30%回撤10%/>30%回撤6%）
  5. 当日成交 —— portfolio_paper_history(range=today)

数据源优先级（数据真相源铁律）：
  持仓/账户/成交 = westock MCP 实时（WeStockClient 优先，失败直连 HTTP MCP 兜底）
  行情 = 腾讯 qt.gtimg.cn 批量（与 seal_snapshot 同源）
  历史 = qfq 库只读

用法：
    python tulong_paper_position_report.py                 # 今日
    python tulong_paper_position_report.py --date 20260821 # 指定日期（行情/历史取该日截面）
    python tulong_paper_position_report.py --json          # JSON 输出
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime

REPORT_DIR = r"D:\zcodesj\reports"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SECTOR_DB = r"D:\zcodesj\eastmoney_quant\sector\sector_data.db"

# 屠龙标准 v1.0 止盈止损（与 position_risk_card 同源常量）
FIXED_STOP = 0.08        # 固定止损 -8% (卖出三铁律不变)
TRAIL_10_30 = 0.10       # 浮盈10-30%档：峰值回撤10%
TRAIL_30 = 0.06          # 浮盈>30%档：峰值回撤6%


def norm_code(code: str) -> str:
    """标准化股票代码（去 sh/sz/bj 前缀，6位）。"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6) if c.isdigit() else c


def _tencent_suffix(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_tencent_batch(codes: list[str]) -> dict[str, dict]:
    """腾讯批量行情（北交所不支持，跳过）。返回 {6位code: {price,pct,prev,high,low,amount}}。"""
    q = ",".join(_tencent_suffix(c) for c in codes)
    url = f"https://qt.gtimg.cn/q={q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
    out: dict[str, dict] = {}
    for seg in raw.strip().split(";"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        f = seg.split("=")[1].strip('"').split("~")
        if len(f) < 40:
            continue
        try:
            out[f[2]] = {
                "name": f[1], "price": float(f[3] or 0), "prev": float(f[4] or 0),
                "pct": float(f[32] or 0), "high": float(f[33] or 0),
                "low": float(f[34] or 0), "amount_wan": float(f[37] or 0),
            }
        except (ValueError, IndexError):
            continue
    return out


def get_positions_via_westock() -> tuple[list[dict], dict]:
    """westock MCP 实时持仓 + 账户概览（WeStockClient 优先，直连兜底）。"""
    try:
        sys.path.insert(0, r"D:/workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
        pf = ws.portfolio().get("data", {}).get("portfolio", {}) or {}
        pos = ws.positions().get("data", {}).get("positions", []) or []
        return pos, pf
    except Exception as e:
        print(f"⚠️ WeStockClient 失败（{e}），切直连模式...", file=sys.stderr)
    return _fetch_positions_via_mcp_direct()


def _fetch_positions_via_mcp_direct() -> tuple[list[dict], dict]:
    """直连 westock HTTP MCP（复制 stop_loss_executor 8/20 修复版模式）。"""
    import json as _json
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    token = ""
    try:
        cfg = _json.load(open(r"C:\Users\yujie\.zcode\cli\config.json", encoding="utf-8"))
        ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
        token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    except OSError:
        pass
    if not token:
        token = os.environ.get("WESTOCK_TOKEN", "")

    def rpc(payload):
        body = _json.dumps(payload).encode()
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")

    try:
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "tulong-position-report", "version": "1.0"}}})
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "portfolio_paper_positions", "arguments": {}}})
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                d = _json.loads(line[5:].strip())
                if "result" in d:
                    for c in d["result"].get("content", []):
                        if c.get("type") == "text":
                            obj = _json.loads(c["text"])
                            data = obj.get("data", {})
                            return data.get("positions", []) or [], {}
    except Exception as e:
        # 2026-08-24 P0修复: 直连兜底也失败时返回失败原因，禁止空列表冒充"0只持仓"
        return [], {"_fetch_error": f"MCP直连失败: {type(e).__name__}: {e}"}
    return [], {"_fetch_error": "MCP直连响应中未解析到positions字段"}


def get_today_trades() -> list[dict]:
    """当日成交记录（westock MCP portfolio_paper_history）。失败返回 []。"""
    try:
        sys.path.insert(0, r"D:/workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
        r = ws.call("portfolio_paper_history", {"range": "today", "limit": 200})
        data = r.get("data", r)
        # 宽松解析：兼容 trades/records/list 键
        for k in ("trades", "records", "list", "items"):
            if isinstance(data.get(k), list):
                return data[k]
        if isinstance(data, list):
            return data
    except Exception as e:
        # 2026-08-24 P2修复: 打印降级原因（区分"今日无成交"与"接口失败"，报告显示0笔时不再歧义）
        print(f"[warn] 当日成交拉取失败(报告将显示今日成交0笔): {type(e).__name__}: {e}", file=sys.stderr)
    return []


def qfq_stats(codes: list[str], date_str: str) -> dict[str, dict]:
    """qfq 库：{code: {ret20, peak_close, last_close}}。
    ret20 = 最近 21 个交易日首尾收盘涨幅；peak_close = 最近 40 自然日最高收盘
    （峰值窗口限 40 日，避免数月前旧高/异常数据抬高移动止损线）。"""
    import datetime as _dt
    out: dict[str, dict] = {}
    try:
        d0 = (_dt.datetime.strptime(date_str, "%Y%m%d") - _dt.timedelta(days=40)).strftime("%Y%m%d")
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
        marks = ",".join("?" * len(codes))
        rows = con.execute(
            f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({marks}) "
            f"AND date <= ? ORDER BY code, date", codes + [date_str]).fetchall()
        con.close()
    except Exception:
        return out
    by_code: dict[str, list[tuple]] = {}
    for code, d, close in rows:
        by_code.setdefault(code, []).append((d, close))
    for code, seq in by_code.items():
        closes = [c for _, c in seq if c]
        if len(closes) < 2:
            continue
        w20 = closes[-21:]
        out[code] = {
            "ret20": (w20[-1] / w20[0] - 1) * 100 if len(w20) >= 2 and w20[0] else None,
            "peak_close": max([c for d, c in seq if c and d >= d0], default=None),
            "last_close": closes[-1],
        }
    return out


def sector_profile(codes: list[str]) -> dict[str, dict]:
    """东财板块反查（sector_member JOIN sector_basic 取板块名）：{板块名: {type, stocks}}。"""
    agg: dict[str, dict] = {}
    try:
        con = sqlite3.connect(f"file:{SECTOR_DB}?mode=ro", uri=True, timeout=20)
        marks = ",".join("?" * len(codes))
        rows = con.execute(
            f"SELECT m.stock_code, b.sector_name, b.sector_type FROM sector_member m "
            f"JOIN sector_basic b ON b.sector_code = m.sector_code "
            f"WHERE m.stock_code IN ({marks})", codes).fetchall()
        con.close()
    except Exception:
        return agg
    for stock, board, btype in rows:
        if not board:
            continue
        agg.setdefault(board, {"type": btype, "stocks": []})
        agg[board]["stocks"].append(stock)
    return agg


def build_risk_card(cost: float, cur: float, peak_close: float | None) -> dict:
    """屠龙标准 v1.0 止损/止盈判定。
    stop_line = 固定 -8% 硬底线（成本×0.92，与 stop_loss_executor 执行口径一致）；
    trail_line = 移动止损线（max(成本, 40日峰值)×0.92，参考线，高于固定线才有效）。"""
    pnl_pct = cur / cost - 1 if cost else 0
    stop_line = round(cost * (1 - FIXED_STOP), 3)
    trail_line = None
    if peak_close and max(cost, peak_close) * (1 - FIXED_STOP) > stop_line:
        trail_line = round(max(cost, peak_close) * (1 - FIXED_STOP), 3)
    if pnl_pct < 0.10:
        tp_rule = "浮盈<10%：不止盈，仅看止损"
        tp_line = None
    elif pnl_pct < 0.30:
        tp_line = round(cur * (1 - TRAIL_10_30), 3)
        tp_rule = "浮盈10-30%：峰值回撤10%止盈"
    else:
        tp_line = round(cur * (1 - TRAIL_30), 3)
        tp_rule = "浮盈>30%：峰值回撤6%止盈"

    alerts = []
    if pnl_pct <= -FIXED_STOP:
        alerts.append("🔴触发-8%硬止损")
    elif pnl_pct <= -0.06:
        alerts.append("🟠接近止损线(≤-6%)")
    elif pnl_pct <= -0.04:
        alerts.append("🟡浮亏>-4%关注")
    if tp_line and cur <= tp_line:
        alerts.append("🔴触发阶梯止盈线")
    return {"pnl_pct": round(pnl_pct * 100, 2), "stop_line": stop_line,
            "trail_line": trail_line,
            "take_profit_rule": tp_rule, "take_profit_line": tp_line,
            "alert": " ".join(alerts) if alerts else "🟢正常"}


def build_report(date_str: str) -> dict:
    positions_raw, portfolio = get_positions_via_westock()
    fetch_error = str(portfolio.get("_fetch_error", "")) if isinstance(portfolio, dict) else ""
    positions = []
    for p in positions_raw:
        qty = int(float(p.get("quantity") or 0))
        if qty <= 0:
            continue
        positions.append({
            "code": norm_code(p.get("code", "")),
            "name": p.get("name", ""),
            "qty": qty,
            "avail": int(float(p.get("availableQty") or 0)),
            "cost": float(p.get("costPrice") or 0),
            "cur": float(p.get("currentPrice") or 0),
            "profit": float(p.get("profit") or 0),
            "pnl_pct": float(p.get("profitRate") or 0),
            "today_profit": float(p.get("todayProfit") or 0),
        })
    codes = [p["code"] for p in positions]
    quotes = fetch_tencent_batch(codes) if codes else {}
    stats = qfq_stats(codes, date_str) if codes else {}
    profile = sector_profile(codes) if codes else {}

    rows = []
    for p in positions:
        q = quotes.get(p["code"], {})
        s = stats.get(p["code"], {})
        cur = q.get("price") or p["cur"] or 0
        day_pct = q.get("pct")
        pnl_pct = (cur / p["cost"] - 1) * 100 if p["cost"] else p["pnl_pct"]
        rc = build_risk_card(p["cost"], cur, s.get("peak_close") if s else None)
        rows.append({
            "code": p["code"], "name": p["name"], "qty": p["qty"], "avail": p["avail"],
            "cost": p["cost"], "cur": cur, "day_pct": day_pct,
            "pnl_pct": round(pnl_pct, 2), "profit": p["profit"],
            "ret20": round(s["ret20"], 2) if s.get("ret20") is not None else None,
            "stop_line": rc["stop_line"], "trail_line": rc["trail_line"],
            "tp_rule": rc["take_profit_rule"],
            "tp_line": rc["take_profit_line"], "alert": rc["alert"],
            "sectors": sorted(b for b in profile if p["code"] in profile[b].get("stocks", [])),
        })
    rows.sort(key=lambda r: r["pnl_pct"])

    # 组合结构：按板块聚合市值占比
    board_mv: dict[str, float] = {}
    mv_total = 0.0
    for r in rows:
        mv = r["cur"] * r["qty"]
        mv_total += mv
        for b in r["sectors"]:
            board_mv[b] = board_mv.get(b, 0) + mv
    board_share = sorted(
        [{"board": b, "mv": v, "share_pct": round(v / mv_total * 100, 1) if mv_total else 0}
         for b, v in board_mv.items()],
        key=lambda x: -x["mv"])[:10]

    # 风控预警
    alerts = [r for r in rows if r["alert"] != "🟢正常"]
    trades = get_today_trades()

    summary = {
        "date": date_str,
        "portfolio": portfolio or {},
        "n_positions": len(rows),
        "data_source_error": fetch_error,
        "positions": rows,
        "board_top": board_share,
        "alerts": alerts,
        "n_trades_today": len(trades),
    }
    return summary


def render_md(s: dict) -> str:
    pf = s["portfolio"]
    L = []
    L.append(f"# 📊 腾讯模拟盘持仓分析 · {s['date'][:4]}-{s['date'][4:6]}-{s['date'][6:]}")
    L.append("")
    L.append(f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 数据源：westock MCP 实时 + 腾讯行情 + qfq 库")
    L.append("")

    # 一、组合总览
    L.append("## 一、组合总览")
    L.append("")
    if pf:
        L.append("| 指标 | 数值 |")
        L.append("|---|---|")
        L.append(f"| 总资产 | {float(pf.get('totalAssets') or 0):,.2f} |")
        L.append(f"| 持仓市值 | {float(pf.get('marketValue') or 0):,.2f} |")
        L.append(f"| 可用现金 | {float(pf.get('availableCash') or 0):,.2f} |")
        L.append(f"| 累计盈亏 | {pf.get('totalProfit', '-')}（{pf.get('totalProfitRate', '-')}%） |")
        L.append(f"| 持仓数 | {s['n_positions']} 只 |")
        L.append(f"| 仓位 | {float(pf.get('marketValue') or 0) / float(pf.get('totalAssets') or 1) * 100:.1f}% |")
    else:
        L.append("- 账户概览未取到")
    L.append("")

    # 二、逐只持仓
    L.append(f"## 二、逐只持仓明细（{s['n_positions']} 只，按浮盈升序）")
    L.append("")
    L.append("| 代码 | 名称 | 数量 | 成本 | 现价 | 当日% | 持仓盈亏% | 20日% | 止损线 | 移动线 | 止盈线 | 状态 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in s["positions"]:
        day = f"{r['day_pct']:+.2f}" if r["day_pct"] is not None else "--"
        ret20 = f"{r['ret20']:+.2f}" if r["ret20"] is not None else "--"
        tp = f"{r['tp_line']:.3f}" if r["tp_line"] else "-"
        trail = f"{r['trail_line']:.3f}" if r["trail_line"] else "-"
        L.append(f"| {r['code']} | {r['name']} | {r['qty']} | {r['cost']:.3f} | {r['cur']:.2f} | {day} | "
                 f"{r['pnl_pct']:+.2f} | {ret20} | {r['stop_line']:.3f} | {trail} | {tp} | {r['alert']} |")
    L.append("")

    # 三、组合结构
    L.append("## 三、组合结构（东财板块归属 Top10，按持仓市值占比）")
    L.append("")
    if s["board_top"]:
        L.append("| 板块 | 归属市值 | 占比 |")
        L.append("|---|---|---|")
        for b in s["board_top"]:
            L.append(f"| {b['board']} | {b['mv']:,.0f} | {b['share_pct']}% |")
        L.append("")
        L.append("> 归属数据源：东财板块成员快照（sector_member，可能与当前日期有数日滞后）")
    else:
        L.append("- 未能反查板块归属")
    L.append("")

    # 四、风控预警
    L.append(f"## 四、风控预警（{len(s['alerts'])} 只触发）")
    L.append("")
    if s["alerts"]:
        for r in s["alerts"]:
            L.append(f"- **{r['name']}（{r['code']}）** {r['alert']} ｜ 浮盈 {r['pnl_pct']:+.2f}% ｜ "
                     f"止损线 {r['stop_line']:.2f}（成本 {r['cost']:.3f}）")
        L.append("")
        L.append("> 屠龙标准 v1.0：-8% 硬止损（移动止损=max(成本,60日最高收盘)×0.92）；"
                 "浮盈10-30%峰值回撤10%止盈；>30%回撤6%止盈。触发🔴须当日处置，T+1新仓延次日。")
    else:
        L.append("- 无触发（全部🟢）")
    L.append("")

    # 五、当日成交
    L.append(f"## 五、当日成交（{s['n_trades_today']} 笔）")
    L.append("")
    if s["n_trades_today"]:
        L.append("- 详见 westock 模拟盘成交记录（知微汇报时核对，此处明细见 MCP history）")
    else:
        L.append("- 今日无成交")
    L.append("")

    # 六、风险提示
    L.append("## ⚠️ 风险提示")
    L.append("- 本报告为持仓体检，非交易指令；卖出须符合规则（趋势走坏/-8%止损）并过 pre_sell_check。")
    L.append("- 北交所标的腾讯行情不支持时当日%缺失；qfq 库无历史时 20日%/60日峰值缺失。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="腾讯模拟盘持仓分析")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    _today_s = datetime.now().strftime('%Y%m%d')
    if a.date != _today_s:
        # 2026-08-24 修复: --date 历史日期时持仓/现价仍是运行时刻实时数据(docstring宣称截面), 显式标注
        print(f'[warn] --date {a.date} 为历史日期: 持仓/现价/今日成交为运行时刻实时数据, '
              f'仅 qfq 统计段为该日截面', file=sys.stderr)

    s = build_report(a.date)
    if a.json:
        print(json.dumps({k: v for k, v in s.items() if k != "positions"},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, f"paper_positions_{a.date}.md")
    body = render_md(s)
    # 2026-08-24 P0修复: westock MCP 两级取数均失败时，报告头强制标注并以 exit 3 失败退出，
    # 禁止空持仓冒充"0只持仓"（8/22 误报根因；风控预警在取数失败时不得显示"全部🟢"）
    if s.get("data_source_error"):
        _err = s["data_source_error"]
        banner = (
            "> ⛔ **数据源失败**：westock MCP 持仓取数两级均失败（" + _err + "）" + NL + NL +
            "> 本报告 **不包含真实持仓与风控预警**，禁止据此得出「持仓0只/无风险」结论。" + NL + NL
        )
        with open(out, "w", encoding="utf-8") as f:
            f.write(banner + body)
        print(f"⛔ 持仓取数失败：{s['data_source_error']}", file=sys.stderr)
        print(f"   失败报告已生成（含警示头）：{out}")
        return 3
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"✅ 持仓分析已生成：{out}")
    n_red = sum(1 for x in s["alerts"] if "🔴" in x["alert"])
    print(f"   持仓 {s['n_positions']} 只 ｜ 预警 {len(s['alerts'])} 只（🔴{n_red}）｜ 今日成交 {s['n_trades_today']} 笔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())