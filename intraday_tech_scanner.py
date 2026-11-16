#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intraday_tech_scanner.py — 盘中技术信号扫描器（TA-Lib 驱动）

定时扫描持仓/候选池的技术指标变化，输出信号预警。
可集成进每日盘中路（10:00 / 11:00 / 14:00）。

数据源:
  优先腾讯 MCP (stockbuddy) 实时 K 线
  fallback baostock / 本地数据库

用法:
    python intraday_tech_scanner.py --positions              # 扫描持仓（从 westock MCP）
    python intraday_tech_scanner.py --candidates              # 扫描候选池（从归因库）
    python intraday_tech_scanner.py --codes 600519,000858     # 指定代码
    python intraday_tech_scanner.py --positions --alert-only  # 只看有信号的
    python intraday_tech_scanner.py --positions --json        # JSON 输出
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any

import numpy as np
import talib

# ── MCP 数据源（复用 trend_checker 的腾讯 stockbuddy 接口）──
try:
    from trend_checker import get_kline
except ImportError:
    # 兜底: 直接复制的 get_kline
    import urllib.request

    def get_kline(code, limit=90):
        URL = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
        TOKEN = "0a04c746006600da402983c037dae94424751e880d4eb6b3e334a41ae6abd95d"
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "data_kline", "arguments": {"codes": code, "ktype": "day", "limit": limit}},
        }
        # SSRF 防线: 仅 https + 固定公网 host; 解析并阻断私网/环回/链路本地/保留地址(防 DNS rebinding)
        from urllib.parse import urlparse
        import ipaddress
        import socket
        _u = urlparse(URL)
        if _u.scheme not in ("http", "https") or _u.hostname not in ("stockbuddy.qq.com",):
            raise ValueError(f"blocked url: {URL}")
        for _ai in socket.getaddrinfo(_u.hostname, None, proto=socket.IPPROTO_TCP):
            _ip = ipaddress.ip_address(_ai[4][0])
            if _ip.is_private or _ip.is_loopback or _ip.is_link_local or _ip.is_reserved or _ip.is_multicast:
                raise ValueError(f"blocked host ip: {_ip}")
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        nodes = data.get("result", {}).get("content", [{}])[0].get("text", "")
        nodes = json.loads(nodes).get("data", {}).get("nodes", [])
        return nodes[::-1]


# ── qfq 库备源 (2026-08-23 接入: 主源(腾讯 MCP)失败时降级读本地 qfq 库日K, 消除单点依赖) ──
def qfq_kline(code: str, limit: int = 120):
    """读 dragon_quant_qfq.db 最近 N 根日K, 返回与 get_kline 同构的节点列表[{open,high,low,last,volume}]。"""
    import sqlite3
    from pathlib import Path
    qfq_db = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
    if not Path(qfq_db).exists():
        return []
    con = sqlite3.connect(f"file:{qfq_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date DESC LIMIT ?", (code, limit)
        ).fetchall()
    finally:
        con.close()
    return [{"date": r[0], "open": r[1], "high": r[2], "low": r[3], "last": r[4], "volume": r[5]}
            for r in reversed(rows)]


# ── 信号定义 ──
SIGNAL_BUY = "🟢 买入信号"
SIGNAL_SELL = "🔴 卖出信号"
SIGNAL_WATCH = "🟡 关注信号"
SIGNAL_NONE = "—"


def scan_stock(code: str, name: str = "") -> dict:
    """对单只股票进行全量 TA-Lib 技术扫描，返回信号列表"""
    try:
        nodes = get_kline(code, limit=120)
    except Exception:
        nodes = []
    if not nodes or len(nodes) < 35:
        nodes = qfq_kline(code, limit=120)   # 主源空/失败降级本地 qfq 库
    if not nodes or len(nodes) < 35:
        return {"code": code, "name": name, "error": "K线数据不足(需≥35日)", "signals": [], "alert": SIGNAL_NONE}

    # 提取 OHLCV
    closes = np.array([n["last"] for n in nodes], dtype=float)
    highs = np.array([n["high"] for n in nodes], dtype=float)
    lows = np.array([n["low"] for n in nodes], dtype=float)
    volumes = np.array([n.get("volume", 0) or 0 for n in nodes], dtype=float)
    opens = np.array([n.get("open", n["last"]) for n in nodes], dtype=float)

    c = closes[-1]
    signals = []

    # ── 1. MACD 金叉/死叉 ──
    macd, macd_sig, macd_hist = talib.MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)
    # 金叉: MACD 上穿信号线（前一根 MACD<Signal，当前 MACD>Signal）
    if len(macd) >= 2 and not np.isnan(macd[-2]) and not np.isnan(macd_sig[-2]):
        if macd[-2] <= macd_sig[-2] and macd[-1] > macd_sig[-1]:
            signals.append({
                "type": SIGNAL_BUY, "indicator": "MACD",
                "detail": f"金叉(MACD={macd[-1]:+.4f}上穿DEA={macd_sig[-1]:+.4f})",
            })
        elif macd[-2] >= macd_sig[-2] and macd[-1] < macd_sig[-1]:
            signals.append({
                "type": SIGNAL_SELL, "indicator": "MACD",
                "detail": f"死叉(MACD={macd[-1]:+.4f}下穿DEA={macd_sig[-1]:+.4f})",
            })
    # MACD 零轴穿越
    if not np.isnan(macd[-1]) and not np.isnan(macd[-2]):
        if macd[-2] <= 0 < macd[-1]:
            signals.append({
                "type": SIGNAL_BUY, "indicator": "MACD",
                "detail": f"上穿零轴(MACD={macd[-1]:+.4f}从负转正)",
            })
        elif macd[-2] >= 0 > macd[-1]:
            signals.append({
                "type": SIGNAL_SELL, "indicator": "MACD",
                "detail": f"下穿零轴(MACD={macd[-1]:+.4f}从正转负)",
            })

    # ── 2. RSI 超买/超卖 ──
    rsi14 = talib.RSI(closes, timeperiod=14)
    rsi6 = talib.RSI(closes, timeperiod=6)
    if not np.isnan(rsi14[-1]):
        if rsi14[-1] > 75:
            signals.append({
                "type": SIGNAL_SELL, "indicator": "RSI",
                "detail": f"RSI14={rsi14[-1]:.1f}>75(超买区,回调风险)",
            })
        elif rsi14[-1] < 25:
            signals.append({
                "type": SIGNAL_BUY, "indicator": "RSI",
                "detail": f"RSI14={rsi14[-1]:.1f}<25(超卖区,反弹机会)",
            })
    if not np.isnan(rsi6[-1]):
        if rsi6[-1] > 80:
            signals.append({
                "type": SIGNAL_SELL, "indicator": "RSI6",
                "detail": f"RSI6={rsi6[-1]:.1f}>80(短超买)",
            })
        elif rsi6[-1] < 20:
            signals.append({
                "type": SIGNAL_BUY, "indicator": "RSI6",
                "detail": f"RSI6={rsi6[-1]:.1f}<20(短超卖)",
            })
        # RSI6 从超买回落（原 RSI6>70 → 当前 <70）= 超买衰竭信号
        if not np.isnan(rsi6[-2]):
            if rsi6[-2] > 70 and rsi6[-1] <= 70:
                signals.append({
                    "type": SIGNAL_WATCH, "indicator": "RSI6",
                    "detail": f"超买衰竭(前{rsi6[-2]:.0f}→现{rsi6[-1]:.0f}跌破70)",
                })

    # ── 3. ADX 趋势强度判定 ──
    adx = talib.ADX(highs, lows, closes, timeperiod=14)
    pdi = talib.PLUS_DI(highs, lows, closes, timeperiod=14)
    mdi = talib.MINUS_DI(highs, lows, closes, timeperiod=14)
    if not np.isnan(adx[-1]):
        if adx[-1] > 25 and not np.isnan(pdi[-1]) and not np.isnan(mdi[-1]):
            if pdi[-1] > mdi[-1]:
                # 上升趋势
                if adx[-1] > 40:
                    signals.append({
                        "type": SIGNAL_WATCH, "indicator": "ADX",
                        "detail": f"强上升趋势(ADX={adx[-1]:.1f}>40,+DI={pdi[-1]:.1f}>-DI={mdi[-1]:.1f})",
                    })
            else:
                signals.append({
                    "type": SIGNAL_SELL, "indicator": "ADX",
                    "detail": f"下降趋势(ADX={adx[-1]:.1f}>25,-DI={mdi[-1]:.1f}>+DI={pdi[-1]:.1f})",
                })
        elif adx[-1] < 20:
            # ADX<20 震荡区，前值>25 则趋势衰竭
            if not np.isnan(adx[-2]) and adx[-2] > 25:
                signals.append({
                    "type": SIGNAL_WATCH, "indicator": "ADX",
                    "detail": f"趋势衰竭(ADX从{adx[-2]:.1f}降至{adx[-1]:.1f}<20,转为震荡)",
                })

    # ── 4. 布林带位置 ──
    upper, middle, lower = talib.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2)
    if not np.isnan(upper[-1]) and not np.isnan(lower[-1]):
        if c >= upper[-1]:
            signals.append({
                "type": SIGNAL_SELL, "indicator": "Bollinger",
                "detail": f"触及上轨(c={c:.2f}≥上轨{upper[-1]:.2f},超买压力)",
            })
        elif c <= lower[-1]:
            signals.append({
                "type": SIGNAL_BUY, "indicator": "Bollinger",
                "detail": f"触及下轨(c={c:.2f}≤下轨{lower[-1]:.2f},超卖支撑)",
            })

    # ── 5. 唐奇安通道突破 ──
    prior_high20 = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])
    prior_low20 = min(lows[-21:-1]) if len(lows) > 21 else min(lows[:-1])
    if c > prior_high20:
        signals.append({
            "type": SIGNAL_BUY, "indicator": "Donchian",
            "detail": f"唐奇安20日突破(c={c:.2f}>前20日高{prior_high20:.2f})",
        })
    elif c < prior_low20:
        signals.append({
            "type": SIGNAL_SELL, "indicator": "Donchian",
            "detail": f"跌破唐奇安20日低(c={c:.2f}<前20日低{prior_low20:.2f})",
        })

    # ── 6. K 线形态识别 ──
    # 识别关键的反转形态
    cdl_engulfing = talib.CDLENGULFING(opens, highs, lows, closes)
    cdl_hammer = talib.CDLHAMMER(opens, highs, lows, closes)
    cdl_star = talib.CDLDOJISTAR(opens, highs, lows, closes)
    cdl_3black = talib.CDL3BLACKCROWS(opens, highs, lows, closes)
    cdl_3white = talib.CDL3WHITESOLDIERS(opens, highs, lows, closes)

    if cdl_engulfing[-1] != 0:
        label = "多头吞没" if cdl_engulfing[-1] > 0 else "空头吞没"
        sig_type = SIGNAL_BUY if cdl_engulfing[-1] > 0 else SIGNAL_SELL
        signals.append({"type": sig_type, "indicator": "形态", "detail": label})
    if cdl_hammer[-1] != 0:
        signals.append({"type": SIGNAL_BUY, "indicator": "形态", "detail": "锤子线(底部反转)"})
    if cdl_3white[-1] != 0:
        signals.append({"type": SIGNAL_BUY, "indicator": "形态", "detail": "三白兵(上升持续)"})
    if cdl_3black[-1] != 0:
        signals.append({"type": SIGNAL_SELL, "indicator": "形态", "detail": "三乌鸦(下降持续)"})

    # ── 聚合信号等级 ──
    buys = [s for s in signals if s["type"] == SIGNAL_BUY]
    sells = [s for s in signals if s["type"] == SIGNAL_SELL]
    watches = [s for s in signals if s["type"] == SIGNAL_WATCH]

    if len(sells) >= 2:
        overall = SIGNAL_SELL
    elif len(buys) >= 2:
        overall = SIGNAL_BUY
    elif sells:
        overall = SIGNAL_SELL
    elif buys:
        overall = SIGNAL_BUY
    elif watches:
        overall = SIGNAL_WATCH
    else:
        overall = SIGNAL_NONE

    # 当前价格和技术值
    ma5 = talib.SMA(closes, timeperiod=5)[-1]
    ma10 = talib.SMA(closes, timeperiod=10)[-1]
    ma20 = talib.SMA(closes, timeperiod=20)[-1]
    ma60 = talib.SMA(closes, timeperiod=60)[-1] if len(closes) >= 60 else None

    return {
        "code": code,
        "name": name,
        "close": round(c, 2),
        "ma5": round(ma5, 2) if not np.isnan(ma5) else None,
        "ma10": round(ma10, 2) if not np.isnan(ma10) else None,
        "ma20": round(ma20, 2) if not np.isnan(ma20) else None,
        "ma60": round(ma60, 2) if ma60 is not None and not np.isnan(ma60) else None,
        "rsi14": round(rsi14[-1], 1) if not np.isnan(rsi14[-1]) else None,
        "rsi6": round(rsi6[-1], 1) if not np.isnan(rsi6[-1]) else None,
        "adx": round(adx[-1], 1) if not np.isnan(adx[-1]) else None,
        "macd": round(macd[-1], 4) if not np.isnan(macd[-1]) else None,
        "macd_signal": round(macd_sig[-1], 4) if not np.isnan(macd_sig[-1]) else None,
        "signals": signals,
        "overall": overall,
    }


def scan_positions() -> list[dict]:
    """从 westock MCP 读取持仓列表并扫描"""
    try:
        sys.path.insert(0, r"D:/workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
        pos_data = ws.positions().get("data", {}).get("positions", [])
        codes = []
        for p in pos_data:
            qty = int(p.get("quantity", 0))
            if qty > 0:
                code = p.get("code", "")
                name = p.get("name", "")
                codes.append((code, name))
    except ImportError:
        # westock 不可用，返回空
        print("⚠️ westock_client 不可用，无法读取持仓", file=sys.stderr)
        return []

    results = []
    for code, name in codes:
        r = scan_stock(code, name)
        results.append(r)
        time.sleep(0.1)  # MCP 调用限流
    return results


def scan_candidates() -> list[dict]:
    """从归因库 limit_up_patterns 读取昨日涨停候选扫描"""
    try:
        import sqlite3
        db = r"D:\zcodesj\attribution\attribution.db"
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT code, name FROM limit_up_patterns "
            "WHERE trade_date=(SELECT MAX(trade_date) FROM limit_up_patterns)"
        ).fetchall()
        conn.close()
        codes = [(r[0], r[1]) for r in rows]
    except Exception as e:
        print(f"⚠️ 无法读取归因库候选: {e}", file=sys.stderr)
        return []

    results = []
    for code, name in codes:
        r = scan_stock(code, name)
        results.append(r)
        time.sleep(0.15)
    return results


def print_report(results: list[dict], alert_only: bool = False):
    """输出 Markdown 格式报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(results)
    has_signal = [r for r in results if r.get("overall") != SIGNAL_NONE and "error" not in r]
    errors = [r for r in results if "error" in r]

    print(f"# 📡 盘中技术信号扫描报告")
    print(f"**生成时间**: {now}")
    print(f"**扫描标的**: {total}只 | **有信号**: {len(has_signal)}只 | **错误**: {len(errors)}只")
    if alert_only:
        print(f"**模式**: 仅显示有信号的标的\n")
    else:
        print()

    if errors:
        print("## ⚠️ 数据不足/错误")
        for r in errors:
            print(f"- {r['code']} {r.get('name','')}: {r['error']}")
        print()

    # 按信号等级分组
    sells = [r for r in has_signal if r["overall"] == SIGNAL_SELL]
    buys = [r for r in has_signal if r["overall"] == SIGNAL_BUY]
    watches = [r for r in has_signal if r["overall"] == SIGNAL_WATCH]
    no_signal = [r for r in results if r.get("overall") == SIGNAL_NONE and "error" not in r]

    if sells:
        print("## 🔴 卖出信号")
        for r in sells:
            detail = "; ".join(s["detail"] for s in r["signals"] if s["type"] == SIGNAL_SELL)
            print(f"- **{r['code']} {r['name']}** (收{r['close']})")
            print(f"  - {detail}")
        print()

    if buys:
        print("## 🟢 买入信号")
        for r in buys:
            detail = "; ".join(s["detail"] for s in r["signals"] if s["type"] == SIGNAL_BUY)
            print(f"- **{r['code']} {r['name']}** (收{r['close']})")
            print(f"  - {detail}")
        print()

    if watches:
        print("## 🟡 关注信号")
        for r in watches:
            detail = "; ".join(s["detail"] for s in r["signals"] if s["type"] == SIGNAL_WATCH)
            print(f"- **{r['code']} {r['name']}** (收{r['close']})")
            print(f"  - {detail}")
        print()

    if not alert_only and no_signal:
        print("## 无信号")
        codes_str = ", ".join(f"{r['code']} {r['name']}" for r in no_signal[:10])
        if len(no_signal) > 10:
            codes_str += f" ... 及其他 {len(no_signal)-10} 只"
        print(f"- {codes_str}")
        print()

    print("---")
    print(f"*扫描工具: intraday_tech_scanner.py (TA-Lib {talib.__version__})*")


def main() -> int:
    ap = argparse.ArgumentParser(description="盘中技术信号扫描器（TA-Lib 驱动）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--positions", action="store_true", help="扫描持仓（westock MCP）")
    group.add_argument("--candidates", action="store_true", help="扫描候选池（归因库涨停）")
    group.add_argument("--codes", type=str, help="指定代码（逗号分隔）")
    ap.add_argument("--alert-only", action="store_true", help="仅显示有信号的标的")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    results = []
    if a.positions:
        results = scan_positions()
    elif a.candidates:
        results = scan_candidates()
    elif a.codes:
        code_list = [c.strip() for c in a.codes.split(",")]
        results = [scan_stock(c) for c in code_list]
    else:
        ap.print_help()
        return 1

    if a.json:
        # JSON 模式同样尊重 alert-only：只保留有信号(且非错误)的标的
        if a.alert_only:
            results = [r for r in results
                       if r.get("overall") != SIGNAL_NONE and "error" not in r]
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results, alert_only=a.alert_only)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())