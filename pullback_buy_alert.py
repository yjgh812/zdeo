#!/usr/bin/env python3
# ⚠️ FALSIFIED(2026-09-09): ENGINE-B2-PULLBACK-R31 口径已证伪(falsified_rules.json), 本脚本降级为存档勿运行勿接调度; 重启用须新prereg
# 2026-09-24 收口: 9/9 证伪时 PullbackAlert_0915 调度漏禁(遗留不一致), 且白名单本体已随「全面废除TopN名单限制」停机(源断供)——schtasks PullbackAlert_0915 本日禁用, 与证伪档案对齐。

# -*- coding: utf-8 -*-
"""回踩买点提示器（2026-09-08 主人令「继续」落地；业界 first-pullback/MA20 体系只读实现）。

语义（全网调研 20260908 共识）: 强势股回调至上升 MA20 附近 = 共识低风险买点;
对应 R31 语义——不拦强势股, 在共识买点主动提示。零信号权零拦截权零自动交易:
仅提示主人, 买入仍走 pre_trade+trade_executor 唯一入口。

条件(白名单内股票):
  强势历史  ret20 = close/20交易日前close - 1 >= +8%
  均线上行  MA20今值 >= MA20的5日前值 * 0.999 (走平也算)
  贴线      |close/MA20 - 1| <= 3%   (R31 视角偏离<=3%, 买入必然过闸)
输出: reports/pullback_alerts_YYYYMMDD.{md,json} + notify toast 提醒。
调度: schtasks PullbackAlert_0915 (工作日09:15, 白名单生成后)。
"""
import csv
import glob
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DIR = os.path.join(BASE, "reports")
RET20_MIN = 0.08          # 强势历史: 20日动量>=+8%
MA20_SLOPE = 0.999        # MA20 走平/上行
NEAR_MA = 0.03            # 贴线: 偏离<=±3%
NEED_BARS = 26            # 20日动量基期+MA20的5日前


def _load_whitelist(today8):
    files = sorted(glob.glob(os.path.join(OUT_DIR, "buy_whitelist_*.csv")))
    if not files:
        return []
    f = files[-1]
    rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
    print(f"[info] 白名单: {os.path.basename(f)} {len(rows)}只")
    return rows


def main():
    wl = _load_whitelist(None)
    if not wl:
        print("[WARN] 白名单缺失 → 无提示(fail-open)")
        return 0
    codes = [r["code"] for r in wl]
    names = {r["code"]: r["name"] for r in wl}

    q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=30)
    dates = [str(r[0]) for r in q.execute(
        "select distinct date from stock_daily_meta_qfq order by date desc limit ?", (NEED_BARS + 2,))]
    dates.sort()
    T = dates[-1]
    hist = {}
    ph = {}
    for code, close, d in q.execute(
            "select code,close,date from stock_daily_meta_qfq where date>=?", (dates[0],)):
        c6 = code[-6:] if isinstance(code, str) else ""
        if c6 in set(codes) and close:
            hist.setdefault(c6, {})[str(d)] = float(close)
    # MA20 的 5 日前值: 需要倒数第 6~25 日的 20 日均线——直接取 d5=dates[-6] 前 20 日窗口
    q.close()

    alerts = []
    for c6 in codes:
        dm = hist.get(c6, {})
        if len(dm) < NEED_BARS or str(T) not in dm:
            continue
        ds = sorted(dm)
        closes = [dm[d] for d in ds]
        px = closes[-1]
        if len(closes) < 21 or not closes[-21]:
            continue
        ret20 = px / closes[-21] - 1
        if ret20 < RET20_MIN:
            continue
        ma20_now = sum(closes[-20:]) / 20
        w5 = closes[-25:-5]
        if len(w5) < 20:
            continue
        ma20_5ago = sum(w5) / 20
        if ma20_now < ma20_5ago * MA20_SLOPE:
            continue  # MA20 下行, 非上升回调体系
        dev = px / ma20_now - 1
        if abs(dev) > NEAR_MA:
            continue
        alerts.append({"date": T, "code": c6, "name": names.get(c6, ""),
                       "price": px, "ma20": round(ma20_now, 3),
                       "dev_ma20": round(dev * 100, 2), "ret20": round(ret20 * 100, 1)})

    today = datetime.now().strftime("%Y%m%d")
    jout = os.path.join(OUT_DIR, f"pullback_alerts_{today}.json")
    json.dump({"date": T, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "rule": f"白名单内 ret20>={RET20_MIN*100:.0f}% + MA20走平上行 + |偏离MA20|<={NEAR_MA*100:.0f}%",
               "n": len(alerts), "alerts": alerts}, open(jout, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    mout = os.path.join(OUT_DIR, f"pullback_alerts_{today}.md")
    with open(mout, "w", encoding="utf-8") as f:
        f.write(f"# 回踩买点提示 {today}（截面{T}）\n\n"
                f"规则: 白名单内 20日动量≥+8% + MA20走平/上行 + 现价贴线(偏离≤±3%)。\n"
                f"语义: 业界共识低风险买点提示, 只读零自动交易; 买入仍走 pre_trade+executor 唯一入口。\n\n")
        if alerts:
            f.write("| 代码 | 名称 | 现价 | MA20 | 偏离 | 20日动量 |\n|---|---|---|---|---|---|\n")
            for a in alerts:
                f.write(f"| {a['code']} | {a['name']} | {a['price']} | {a['ma20']} | "
                        f"{a['dev_ma20']:+.2f}% | +{a['ret20']}% |\n")
        else:
            f.write("（今日白名单内无贴线强势股——如实报告不编造）\n")
    print(f"[ok] {mout} | 提示 {len(alerts)} 只")
    if alerts:
        msg = "回踩买点提示: " + " ".join(f"{a['code']}{a['name']}(偏离MA20 {a['dev_ma20']:+.1f}%)" for a in alerts[:8])
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                            "--title", "回踩买点提示", "--content", msg, "--level", "warning"],
                           cwd=BASE, capture_output=True, text=True, timeout=30)
        except Exception as e:
            print(f"[WARN] 提醒推送失败: {type(e).__name__} {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
