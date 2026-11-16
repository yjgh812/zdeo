#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trend_alert_daily.py — 大盘趋势预警分级+复盘首节两问直出（2026-10-15，主人令
「复盘报告应该提醒」「每次复盘要判断什么趋势，应该什么仓位」）。

职责：每日收盘后程序化直出复盘报告首节「大盘趋势预警」的数据与结论，
      杜绝人工转抄（计数纪律：tmp生成器直出+读回核验）。零交易权：只输出建议仓位，
      禁止任何下单/卖出/改仓，禁止触碰 trade_executor。

数据源（全部本地/既有，零新外部面）：
  - vipdoc sh/lday/sh000001.day 与 sz/lday/sz399006.day（解析与 ftd_shadow_daily 同构）
  - data/emotion_phase_v2_ledger.jsonl（情绪相位，10/6 G8 接入件）
  - data/timing_state_ledger.jsonl（PD-034 择时档位）
  - westock HTTP MCP（实际仓位，复用 stop_loss_executor 同构 rpc）

分级规则（SOP 一百四十二⑦；回撤窗口=120日收盘高点，修正自初版20日口径——
20日窗口在震荡市会漏判 7 月顶级别的中期回撤，10/15 首判即证）：
  🔴红：收盘<MA20 ∧ MA5<MA10<MA20；或 距120日收盘高点回撤≥8%
  🟠橙：收盘<MA20（未构成空头排列）
  🟡黄：相位∈{diverging,ebbing} 或 择时档位=中档40-60
  🟢绿：其余
  取最严档。创业板/深成回撤作结构注记（大小盘分化/共振判读辅助）。

用法：python analysis/trend_alert_daily.py [--no-toast]
退出码：0=正常；2=数据源缺失/解析异常(fail-loud)；分级变更自动 toast(--no-toast 关闭)。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3  # noqa: F401  (预留; 当前数据源为vipdoc+jsonl)
import struct
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIPDOC_SH = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
VIPDOC_SZ_CYB = r"D:\anzhuang\tdx\vipdoc\sz\lday\sz399006.day"
PHASE_LEDGER = os.path.join(HERE, "data", "emotion_phase_v2_ledger.jsonl")
TIMING_LEDGER = os.path.join(HERE, "data", "timing_state_ledger.jsonl")
OUT_TXT = os.path.join(HERE, "data", "trend_alert_latest.txt")
OUT_LEDGER = os.path.join(HERE, "data", "trend_alert_ledger.jsonl")

DRAWDOWN_WINDOW = 120   # 收盘高点回撤窗口（修正口径）
LEVEL_ORDER = {"绿": 0, "黄": 1, "橙": 2, "红": 3}
LEVEL_POS_CAP = {"红": "20-30%", "橙": "40-50%", "黄": "60-70%", "绿": "常规(止损链照常)"}


def read_vipdoc(path: str) -> list:
    """与 analysis/ftd_shadow_daily.py read_vipdoc 逐位同构(单一解析真源复用)。"""
    rows = []
    with open(path, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append({"date": str(d), "close": c / 100.0, "vol": vol})
    return rows


def ma(closes: list, n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def drawdown_from_high(closes: list, window: int) -> tuple[float, float]:
    """返回 (回撤%, 窗口内收盘高点)。窗口不足取全部可用。"""
    seg = closes[-window:] if len(closes) >= window else closes
    hi = max(seg)
    return (closes[-1] / hi - 1) * 100.0, hi


def idx_metrics(path: str, name: str) -> dict:
    rows = read_vipdoc(path)
    if len(rows) < 60:
        raise ValueError(f"{name} vipdoc 根数不足60: {len(rows)}")
    closes = [r["close"] for r in rows]
    last_date, close = rows[-1]["date"], closes[-1]
    m5, m10, m20, m60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)
    dd, hi = drawdown_from_high(closes, DRAWDOWN_WINDOW)
    return {"name": name, "date": last_date, "close": close,
            "ma5": round(m5, 2), "ma10": round(m10, 2),
            "ma20": round(m20, 2), "ma60": round(m60, 2),
            "dd_pct": round(dd, 2), "hi120": round(hi, 2),
            "below_ma20": close < m20,
            "bear_align": bool(close < m20 and m5 < m10 and m10 < m20)}


def last_jsonl(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                try:
                    last = json.loads(s)
                except json.JSONDecodeError:
                    continue
    return last


def fetch_position_snapshot() -> dict:
    """实际仓位快照（westock HTTP MCP，与 stop_loss_executor._fetch_positions_via_mcp 同构；
    增补 portfolio 接口取总资产。失败返回 {'error': ...}，不阻断分级（仓位行标缺失）。"""
    import urllib.request as _ur
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    token = ""
    try:
        cfg = json.load(open(os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Administrator"),
                                          ".zcode", "cli", "config.json"), encoding="utf-8"))
        token = (cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
                 .get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    except OSError:
        pass
    token = token or os.environ.get("WESTOCK_TOKEN", "")
    if not token:
        return {"error": "no token"}

    def rpc(payload):
        body = json.dumps(payload).encode()
        req = _ur.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        with _ur.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")

    def call_tool(name, args):
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {
                "name": "tulong-trend-alert", "version": "1.0"}}})
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": name, "arguments": args}})
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                d = json.loads(line[5:].strip())
                if "result" in d:
                    for c in d["result"].get("content", []):
                        if c.get("type") == "text":
                            return json.loads(c["text"]).get("data", {})
        return {}

    try:
        pos = call_tool("portfolio_paper_positions", {})
        pf = call_tool("portfolio_paper_portfolio", {}).get("portfolio", {})
        rows = []
        mv = 0.0
        for p in pos.get("positions", []):
            q = int(float(p.get("quantity") or 0))
            px = float(p.get("currentPrice") or 0)
            m = px * q
            mv += m
            rows.append({
                "code": (p.get("code") or "")[-6:], "name": p.get("name") or "",
                "qty": q, "price": px, "mv": round(m, 2),
                "pnl_pct": float(p.get("profitRate") or 0.0),
            })
        ta = pf.get("totalAssets")
        out = {"n_pos": len(rows), "market_value": round(mv, 2), "positions": rows}
        if ta is not None:
            ta = float(ta)
            out["total_assets"] = round(ta, 2)
            out["pos_pct"] = round(mv / ta * 100, 1) if ta > 0 else None
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def reduction_path(level: str, pos: dict) -> list:
    """档位减仓路径清单（PD-042，2026-10-16「继续」令立项；零交易权呈报层）。

    实际仓位>档位上限时产出机械四层清留清单+每步目标数字（呈报供主人裁决，
    执行域各异：L1=止损链自动域勿重复操作；L2-L4=主人裁决后走 trade_executor）。
    层序=机械规则非判断：L1 止损链域(显示口径≤-8%)→L2 碎仓域(mv<5000元)→
    L3 深亏裁决层(-8%<浮亏≤-3%, 亏多先卖)→L4 盈利层(盈多先卖)。整百股取整；
    目标=档位上限中值；每步给释放金额与累计仓位%。westock 显示口径注记：
    部分卖出剩仓以止损链 PD-040 原始成本口径为准，本清单仅排序参考。
    """
    caps = {"红": 30.0, "橙": 50.0, "黄": 70.0}
    cap = caps.get(level)
    ta = pos.get("total_assets")
    rows = [r for r in (pos.get("positions") or []) if r.get("qty", 0) > 0]
    if cap is None or not ta or not rows:
        return []
    pos_pct = pos.get("pos_pct") or 0.0
    if pos_pct <= cap:
        return []
    target_pct = cap - 5.0 if cap >= 50 else cap - 2.5   # 目标=上限下方留缓冲
    need_mv = pos["market_value"] - ta * target_pct / 100.0
    out = ["", f"问②路径: 实际{pos_pct}%>上限{cap:.0f}% → 目标≤{target_pct:.1f}%"
               f"(需释放≥{need_mv:,.0f}元) 机械四层清单:"]
    # L1 对齐止损链当日报告触发名单(PD-040 口径权威: 部分卖出剩仓以原始成本判,
    # 显示口径会误列博汇类); 报告缺失回退显示口径+注记
    l1_codes = None
    sl_path = os.path.join(HERE, "reports", f"stop_loss_scan_{datetime.now():%Y%m%d}.md")
    if os.path.exists(sl_path):
        l1_codes, in_trigger = set(), False
        with open(sl_path, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if "止损触发清单" in s:
                    in_trigger = True
                    continue
                if in_trigger and (s.startswith("#") or s.startswith("---")):
                    in_trigger = False  # 出触发表作用域(口径注记等后续节不采集)
                    continue
                if in_trigger and s.startswith("| ") and len(s) > 10:
                    tok = s.split("|")[1].strip()
                    if tok.isdigit() and len(tok) == 6:
                        l1_codes.add(tok)
        if not l1_codes:
            l1_codes = None
    if l1_codes:
        l1 = [r for r in rows if r["code"] in l1_codes]
        l1_note = "名单=止损链当日报告(口径权威)"
    else:
        l1 = [r for r in rows if r["pnl_pct"] <= -8.0]
        l1_note = "止损链当日报告缺失, 回退显示口径(部分卖出剩仓可能误列, 以止损链为准)"
    layers = [
        (f"L1 止损链域({l1_note}; 自动勿重复)", l1),
        ("L2 碎仓域(市值<5000元)", [r for r in rows if r["code"] not in {x['code'] for x in l1}
                                    and r["mv"] < 5000]),
        ("L3 深亏裁决层(-8%<浮亏≤-3%, 亏多先卖)",
         [r for r in rows if r["code"] not in {x['code'] for x in l1}
          and -8.0 < r["pnl_pct"] <= -3.0 and r["mv"] >= 5000]),
        ("L4 盈利层(盈多先卖)",
         [r for r in rows if r["code"] not in {x['code'] for x in l1}
          and r["pnl_pct"] > 0 and r["mv"] >= 5000]),
    ]
    sold = 0.0
    for label, members in layers:
        if not members or sold >= need_mv:
            continue
        if label.startswith("L3"):
            members = sorted(members, key=lambda r: r["pnl_pct"])
        elif label.startswith("L4"):
            members = sorted(members, key=lambda r: -r["pnl_pct"])
        out.append(f"  {label}:")
        for r in members:
            left = need_mv - sold
            if left <= 0:
                break
            need_shares = ((int(left // r["price"]) + 99) // 100) * 100  # 缺口向上取整手
            lot = min(r["qty"], max(100, need_shares))
            lot = (lot // 100) * 100 or min(r["qty"], 100)  # 整手; 零股仓全清兜底
            amt = lot * r["price"]
            sold += amt
            cum = (pos["market_value"] - sold) / ta * 100
            out.append(f"    {r['code']} {r['name']}: 卖{lot}股≈释放{amt:,.0f}元 "
                       f"→累计仓位{cum:.1f}%")
    if sold < need_mv:
        out.append(f"  ⚠️ 四层全动仍差{need_mv - sold:,.0f}元(现金冗余不足)——呈报主人扩层或接受超限")
    out.append("  (路径=机械呈报非指令; L2-L4 执行须主人裁决+trade_executor; L1 归止损链)")
    return out


def classify(sh: dict, phase: str | None, gear: str | None) -> tuple[str, list]:
    """返回 (等级, 命中依据列表)。取最严档。"""
    hits = {"红": [], "橙": [], "黄": []}
    if sh["bear_align"]:
        hits["红"].append(f"空头排列 收{sh['close']}<MA20 {sh['ma20']}且MA5<MA10<MA20")
    if sh["dd_pct"] <= -8.0:
        hits["红"].append(f"距120日收盘高{sh['hi120']}回撤{sh['dd_pct']}%≤-8%")
    if sh["below_ma20"]:
        hits["橙"].append(f"收盘{sh['close']}<MA20 {sh['ma20']}")
    if phase in ("diverging", "ebbing"):
        hits["黄"].append(f"情绪相位={phase}")
    if gear and "中档" in gear:
        hits["黄"].append(f"择时档位={gear}")
    for lv in ("红", "橙", "黄"):
        if hits[lv]:
            return lv, hits[lv]
    return "绿", []


def main() -> int:
    ap = argparse.ArgumentParser(description="大盘趋势预警分级直出（零交易权）")
    ap.add_argument("--no-toast", action="store_true", help="分级变更不弹 toast")
    a = ap.parse_args()

    try:
        sh = idx_metrics(VIPDOC_SH, "上证指数")
        cyb = idx_metrics(VIPDOC_SZ_CYB, "创业板指")
    except (OSError, ValueError) as e:
        print(f"❌ 指数数据源不可用: {e}——拒绝输出(fail-loud)", file=sys.stderr)
        return 2

    ph = last_jsonl(PHASE_LEDGER)
    phase = (ph or {}).get("phase")
    tg = last_jsonl(TIMING_LEDGER)
    gear = (tg or {}).get("gear_today")
    pos = fetch_position_snapshot()

    level, reasons = classify(sh, phase, gear)
    today = datetime.now().strftime("%Y%m%d")

    trend_text = {
        "红": "下跌趋势（破位确认，勿抄底勿接力）",
        "橙": "转弱/破位预警（趋势存疑，防守优先）",
        "黄": "高位转弱预警（谨慎，控制新增）",
        "绿": "无下跌趋势信号（常规环境）",
    }[level]
    struct_note = "共振下跌" if (cyb["dd_pct"] <= -8 and sh["dd_pct"] <= -8) else (
        "大小盘分化(权重强成长弱)" if cyb["dd_pct"] <= -8 else "结构正常")

    lines = [
        f"【大盘趋势预警 {today}】等级: {'🔴' if level=='红' else '🟠' if level=='橙' else '🟡' if level=='黄' else '🟢'}{level}",
        f"问①趋势: {trend_text}｜结构: {struct_note}(创业板距120日高{cyb['dd_pct']}%)",
        f"问②建议仓位: {LEVEL_POS_CAP[level]}",
        f"依据: {'; '.join(reasons) if reasons else '无下跌信号'}",
        f"上证: 收{sh['close']} MA5 {sh['ma5']} MA10 {sh['ma10']} MA20 {sh['ma20']} MA60 {sh['ma60']} "
        f"回撤{sh['dd_pct']}%(120日高{sh['hi120']}) @{sh['date']}",
        f"实际仓位: {pos.get('pos_pct','缺失')}%(市值{pos.get('market_value','?')}/总资产{pos.get('total_assets','?')}, "
        f"{pos.get('n_pos','?')}只)" if "error" not in pos else f"实际仓位: 缺失({pos.get('error')})",
        f"相位: {phase or '缺失'}｜档位: {gear or '缺失'}",
        ]
    lines += reduction_path(level, pos)
    lines.append("(观测层零交易权; 仓位为建议值非自动闸, 执行=主人裁决/止损链)")
    out = "\n".join(lines) + "\n"
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(out)
    print(out, end="")

    # 台账（date 主键幂等：先删后写）
    rows = []
    if os.path.exists(OUT_LEDGER):
        with open(OUT_LEDGER, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    prev_level = None
    for r in rows:
        if r.get("date") == today:
            prev_level = prev_level or r.get("level")
    rows = [r for r in rows if r.get("date") != today]
    rows.append({"date": today, "level": level, "reasons": reasons,
                 "sh_close": sh["close"], "sh_dd_pct": sh["dd_pct"],
                 "cyb_dd_pct": cyb["dd_pct"], "phase": phase, "gear": gear,
                 "pos_pct": pos.get("pos_pct"), "ts": datetime.now().isoformat(timespec="seconds")})
    with open(OUT_LEDGER, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 分级变更 toast（对照台账中今日之前的最后一条）
    hist = [r for r in rows if r.get("date") != today]
    if hist and hist[-1].get("level") != level and not a.no_toast:
        try:
            import subprocess
            subprocess.run([sys.executable, os.path.join(HERE, "notify.py"), "send",
                            "--title", f"趋势预警变档: {hist[-1].get('level')}→{level}",
                            "--content", f"{today} 大盘趋势等级 {hist[-1].get('level')}→{level}; "
                                         f"建议仓位 {LEVEL_POS_CAP[level]}",
                            "--level", "warning"], timeout=60)
            print("[trend-alert] 分级变更 toast 已发送")
        except Exception as e:
            print(f"[trend-alert] toast 失败(不阻断): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
