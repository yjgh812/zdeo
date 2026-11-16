#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8/14晨间全流程：bp+ep候选→闸门过滤→组合优化→作战清单更新
   集成 jin10 快讯/日历 + eastmoney 数据源验证 — 端到端打通
"""
import json, csv, os, subprocess, sys, asyncio
from datetime import datetime

BASE = r"D:\zcodesj"
REPORTS = os.path.join(BASE, "reports")
CSV_PATH = os.path.join(BASE, "multifactor/reports/daily_top20/bp_ep_monthly_20260812_20260814.csv")

# ===== 数据源连通性验证 (打通端到端) =====
DATA_SOURCES = {
    "jin10":       {"desc": "金十快讯/日历",   "verify": lambda: _check_jin10()},
    "eastmoney":   {"desc": "东方财富股票/板块", "verify": lambda: _check_eastmoney()},
    "tdx_local":   {"desc": "通达信本地K线",   "verify": lambda: os.path.exists(r"D:\anzhuang\tdx\vipdoc")},
    "westock":     {"desc": "腾讯模拟盘MCP",   "verify": lambda: os.path.exists(r"D:\workbuddyspace\westock_trades.db") or os.path.exists(r"D:\workbuddyspace\databases\westock_trades.db")},
}

def _check_jin10() -> bool:
    """验证金十快讯可达 (同步包装器，兼容有无事件循环两种场景)"""
    try:
        sys.path.insert(0, os.path.join(BASE, "mcp_servers"))
        from jin10_mcp import get_flash_news
        try:
            r = json.loads(asyncio.run(get_flash_news(limit=1)))
        except RuntimeError:
            # 已在事件循环中 → 用新线程跑
            import threading
            result = []
            def _run():
                import asyncio as aio
                r = aio.run(get_flash_news(limit=1))
                result.append(r)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=15)
            if result:
                r = json.loads(result[0])
            else:
                return False
        return isinstance(r, list) and len(r) > 0
    except Exception:
        return False

def _check_eastmoney() -> bool:
    """验证东方财富本地库可达"""
    try:
        import sqlite3
        db = r"D:\zcodesj\eastmoney_quant\stock\stock_data.db"
        if not os.path.exists(db):
            return False
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM meta")
        cnt = cur.fetchone()[0]
        conn.close()
        return cnt >= 3
    except Exception:
        return False

def verify_data_sources() -> dict:
    """全数据源连通性验证"""
    results = {}
    print("=" * 60)
    print("📡 数据源连通性验证 (端到端)")
    print("=" * 60)
    for name, cfg in DATA_SOURCES.items():
        ok = cfg["verify"]()
        results[name] = ok
        icon = "✅" if ok else "❌"
        print(f"  {icon} [{name}] {cfg['desc']}")
    print("=" * 60)
    return results

def fetch_today_events() -> list:
    """获取今日重要财经事件 (jin10日历)"""
    try:
        sys.path.insert(0, os.path.join(BASE, "mcp_servers"))
        from jin10_mcp import get_economic_calendar
        try:
            cal_raw = asyncio.run(get_economic_calendar())
        except RuntimeError:
            import threading
            result = []
            def _run():
                import asyncio as aio
                r = aio.run(get_economic_calendar())
                result.append(r)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=15)
            if result:
                cal_raw = result[0]
            else:
                return []
        cal_data = json.loads(cal_raw)
        if isinstance(cal_data, list):
            return [e for e in cal_data if int(e.get("重要性", 0)) >= 2]
    except Exception:
        pass
    return []

def print_calendar(events: list):
    """打印今日重要事件"""
    print("=" * 60)
    print("📅 今日重要财经事件 (jin10日历)")
    print("=" * 60)
    if not events:
        print("  今日无高重要性财经事件 或 日历数据不可用")
        return
    for e in events[:8]:
        time_str = e.get("时间", "")
        event_str = e.get("事件", "")
        country = e.get("地区", "")
        imp = e.get("重要性", "")
        actual = e.get("公布", "")
        prev = e.get("前值", "")
        consensus = e.get("预期", "")
        flag = "✅" if (actual and actual != "nan") else "⏳"
        if actual and actual != "nan":
            print(f"  {flag} {time_str} {country} {event_str} 实际:{actual} 前值:{prev}")
        else:
            print(f"  {flag} {time_str} {country} {event_str} (重要性:{imp}星) 预期:{consensus}")

def norm_code(code):
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p): c = c[len(p):]
    return c.zfill(6) if c.isdigit() else c


# ========== 主流程 ==========

print(f"\n{'='*60}")
print(f"  屠龙系统·晨间全流程  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"{'='*60}\n")

# Step 0: 数据源连通性验证
src_ok = verify_data_sources()
all_ok = all(src_ok.values())
if not all_ok:
    failed = [k for k, v in src_ok.items() if not v]
    print(f"⚠️ 数据源离线: {', '.join(failed)} — 相关功能受限")
else:
    print("✅ 全数据源在线")
print()

# Step 0.5: 获取今日重要财经事件（jin10日历）
today_events = fetch_today_events()
print_calendar(today_events)
print()

# Step 1: 读取bp+ep候选
picks = []
if os.path.exists(CSV_PATH):
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = norm_code(row.get("code", ""))
            if code:
                picks.append({
                    "code": code,
                    "name": row.get("name", ""),
                    "score": float(row.get("composite_score", row.get("score", 0))),
                    "sector": row.get("sector", row.get("industry", "")),
                    "price": float(row.get("close", row.get("price", 0))),
                })

print(f"bp+ep月度候选: {len(picks)}只")
if picks:
    # 用 eastmoney 数据丰富候选信息
    try:
        import sqlite3
        em_db = r"D:\zcodesj\eastmoney_quant\stock\stock_data.db"
        if os.path.exists(em_db):
            conn = sqlite3.connect(em_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # 补全候选的行业/股价信息（如果候选CSV数据不全）
            enriched = 0
            for p in picks:
                code_for_db = p["code"]
                if code_for_db.startswith("6"):
                    code_for_db = code_for_db  # 沪市
                cur.execute(
                    "SELECT symbol, name, latest_price FROM stock_spot WHERE symbol=?",
                    (code_for_db,)
                )
                row = cur.fetchone()
                if row:
                    if not p["name"] and row["name"]:
                        p["name"] = row["name"]
                        enriched += 1
            conn.close()
            if enriched:
                print(f"  eastmoney 补全 {enriched} 项候选信息")
    except Exception as e:
        print(f"  eastmoney 数据丰富失败: {e}")
print()

# Step 2: 逐只过闸门
PTC = os.path.join(BASE, "pre_trade_check_v2.py")
gate_results = []
for p in picks[:20]:
    cmd = [sys.executable, PTC, "--code", p["code"], "--name", p["name"],
           "--source", "bp_ep_core", "--skip-time-window"]
    if p["price"]:
        cmd += ["--price", str(p["price"])]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = r.stdout.strip()
        start = out.find("{")
        end = out.rfind("}")
        if start >= 0 and end > start:
            result = json.loads(out[start:end+1])
            verdict = result.get("verdict", "UNKNOWN")
            rejects = result.get("rejects", [])
        else:
            verdict = "UNKNOWN"
            rejects = []
    except Exception as e:
        verdict = "ERROR"
        rejects = [str(e)]

    p["gate_verdict"] = verdict
    p["gate_rejects"] = rejects
    gate_results.append(p)
    status = "PASS" if verdict in ("PASS", "PASS_WITH_WARN") else "REJECT"
    print(f"  {status} {p['code']} {p['name']}: {verdict}")
    for r in rejects[:2]:
        print(f"    REJECT: {r}")

# Step 3: 过滤
passed = [p for p in gate_results if p.get("gate_verdict") in ("PASS", "PASS_WITH_WARN")]
rejected = [p for p in gate_results if p.get("gate_verdict") not in ("PASS", "PASS_WITH_WARN")]

print(f"\n闸门结果: PASS {len(passed)} / REJECT {len(rejected)}")
for r in rejected:
    print(f"  REJECT: {r['code']} {r['name']}: {'; '.join(r.get('gate_rejects', [])[:2])}")

# Step 4: 行业分布
sector_weights = {}
for p in passed:
    s = p.get("sector", "unknown")
    sector_weights[s] = sector_weights.get(s, 0) + 1

print(f"\n行业分布(通过闸门的):")
for s, cnt in sorted(sector_weights.items(), key=lambda x: -x[1]):
    pct = cnt / len(passed) * 100 if passed else 0
    print(f"  {s}: {cnt}只({pct:.0f}%)")

# Step 5: 输出候选清单
plan_path = os.path.join(REPORTS, "candidates_20260814.md")
with open(plan_path, "w", encoding="utf-8") as f:
    f.write(f"# bp+ep月度候选清单(闸门过滤后) 20260814\n\n")
    f.write(f"PASS: {len(passed)}只 | REJECT: {len(rejected)}只\n\n")
    f.write("## PASS(可买入)\n\n")
    f.write("| 代码 | 名称 | 行业 | 价格 | 得分 |\n|------|------|------|------|------|\n")
    for p in passed:
        f.write(f"| {p['code']} | {p['name']} | {p.get('sector','')} | {p.get('price','')} | {p.get('score','')} |\n")
    f.write("\n## REJECT(被拦截)\n\n")
    f.write("| 代码 | 名称 | 拒绝原因 |\n|------|------|----------|\n")
    for r in rejected:
        reasons = "; ".join(r.get("gate_rejects", [])[:2])
        f.write(f"| {r['code']} | {r['name']} | {reasons} |\n")

print(f"\n候选清单已写入: {plan_path}")
print(f"\n{'='*60}")
print(f"  晨间全流程完成")
print(f"{'='*60}")
print(f"  bp+ep候选: {len(picks)}")
print(f"  闸门通过: {len(passed)}")
print(f"  闸门拦截: {len(rejected)}")
print(f"{'='*60}")