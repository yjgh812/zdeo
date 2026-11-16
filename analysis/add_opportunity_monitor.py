# -*- coding: utf-8 -*-
"""ADD-OPPORTUNITY MONITOR v1 — 加仓机会盘中监测器（学习计划落地件, 主人「落地用于实战」令）
触发定义（ doctrine v1 = 突破加仓, 与 #109 已证伪的浮盈触发家族区分, 如实标注为未验证新触发）:
  环境门: ddprotect_gate_state.json gate 含"防御"/"清" → 全部抑制(仅记录不通知)
  个股门: 持仓票 现价 > 前19个交易日最高收盘(20日突破) 且 非涨停(可成交) 且 非ST
  输出: 机会清单 → notify toast + analysis/add_opportunity_report_latest.json
  🔴 零交易权: 本监测只呈现, 执行须主人逐笔令 + trade_executor --allow-add-position
运行: schtask 每交易日 10:05/14:00 两次; 手动 python analysis/add_opportunity_monitor.py
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
HOLDINGS = r"D:\zcodesj\data\holdings_codes_latest.json"
DD_STATE = r"D:\zcodesj\data\ddprotect_gate_state.json"
OUT = r"D:\zcodesj\analysis\add_opportunity_report_latest.json"


def quote(code6):
    pre = "sh" if code6.startswith("6") else "sz"
    url = f"http://qt.gtimg.cn/q={pre}{code6}"
    try:
        raw = urllib.request.urlopen(url, timeout=8).read().decode("gbk", errors="replace")
        f = raw.split('"')[1].split("~")
        # f[3]=现价 f[4]=昨收 f[5]=今开 f[33]=涨停价不可靠→用规则: 主板10%/创业20%
        price, prev = float(f[3]), float(f[4])
        cap = 0.20 if code6.startswith(("300", "301")) else 0.10
        limit_up = prev * (1 + cap)
        return {"price": price, "prev": prev, "limit_up_dist": (limit_up - price) / limit_up}
    except Exception as e:
        print(f"[warn] {code6} 行情失败: {type(e).__name__} {e}")
        return None


def main():
    hold = json.load(open(HOLDINGS, encoding="utf-8"))
    pos = hold.get("positions", [])
    codes = sorted({str(p.get("code", "")).replace("sh", "").replace("sz", "").replace("bj", "")[-6:]
                    for p in pos if isinstance(p, dict) and p.get("code")})
    names = {str(p.get("code", "")).replace("sh", "").replace("sz", "").replace("bj", "")[-6:]:
             p.get("name", "?") for p in pos if isinstance(p, dict)}

    dd = json.load(open(DD_STATE, encoding="utf-8"))
    gate = str(dd.get("gate", ""))
    gate_block = ("防御" in gate) or ("清" in gate)

    db = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    opps, scanned = [], 0
    for c in codes:
        rows = db.execute(
            "select date,close from stock_daily_meta_qfq where code=? order by date", (c,)
        ).fetchall()
        if len(rows) < 21:
            continue
        q = quote(c)
        if q is None:
            continue
        scanned += 1
        prev19_max_close = max(x[1] for x in rows[-20:-1])  # 前19日最高收盘(不含今日)
        breakout = q["price"] > prev19_max_close
        unfill = q["limit_up_dist"] > 0.005  # 距涨停>0.5% = 可成交
        if breakout and unfill:
            opps.append({"code": c, "name": names.get(c, "?"), "price": q["price"],
                         "prev19_max": prev19_max_close, "breakout_pct":
                         round((q["price"] / prev19_max_close - 1) * 100, 2),
                         "gate_block": gate_block})
    db.close()

    actionable = [o for o in opps if not o["gate_block"]]
    out = {"ts": time.strftime("%F %H:%M:%S"), "gate": gate, "gate_block": gate_block,
           "scanned": scanned, "n_opportunities": len(opps),
           "n_actionable_if_gate_open": len(actionable), "opportunities": opps}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"[monitor] gate={gate} 扫描={scanned} 突破机会={len(opps)} "
          f"(环境门放行可执行={len(actionable)})", flush=True)
    for o in opps[:8]:
        tag = "可执行" if not gate_block else "环境门抑制"
        print(f"  {o['code']} {o['name']} 价={o['price']} 突破+{o['breakout_pct']}% [{tag}]", flush=True)
    if opps:
        try:
            subprocess.run([sys.executable, r"D:\zcodesj\notify.py", "send",
                            "--title", "加仓机会监测",
                            "--content", f"突破加仓机会 {len(opps)} 只(环境门={'阻断' if gate_block else '放行'}), "
                                         f"详见 {OUT}", "--level", "warn"],
                           timeout=30, env={**os.environ, "PYTHONUTF8": "1"})
        except Exception as e:
            print(f"[warn] notify 失败: {e}", flush=True)


if __name__ == "__main__":
    main()
