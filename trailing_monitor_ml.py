# -*- coding: utf-8 -*-
"""M-3 日线 trailing 移动止盈监控器 (ml 腿专属, 只读提示型)
=====================================================================
背景: 主人 2026-09-04 裁决「ml M-3 复活」(git 8a560ef), CONCLUSION 必备清单
遗留项=「日线 trailing 监控器须于 10/1 月度调仓前补建」。本脚本即该补建件。

设计:
- 数据源: tulong_production_paper.db positions WHERE source='ml_signal' (组合腿
  归属权威标记; 10/1 首次 ml 调仓前为空 -> 空扫描属正常)
- 参数单一真源: production_combo_monthly.get_ml_leg_overrides()
  (trailing_stop_pct=0.10, trail_activation_pct=10, 指纹 a41fbe252a3af5b7641a)
- 价格: qfq 库 (唯一价格源铁律); peak=entry 日后最高 high; 触发=close <= peak*(1-0.10)
- activation: entry 后最高 high 相对成本浮盈 >= trail_activation_pct% 才激活跟踪
- 合规边界: 只读提示器, 不下单不写 westock —— trailing 卖出属卖出侧, 必须主人裁决
  + pre_sell 前置 + trade_executor 唯一入口 (铁律2); 触发清单供 14:30 链/主人裁决消费
- 已知近似 (诚实标注): 回测引擎 (SingleStrategyBacktest) 的 trailing 内部实现以
  close 序列驱动, 本监控器 peak 用 high 更保守 (更早触发); 首月 ml 调仓后与回测
  引擎做一次逐日对账, 若口径偏差>1% 回来钉死统一口径

用法: python trailing_monitor_ml.py [--date YYYYMMDD]
输出: reports/trailing_scan_YYYYMMDD.md + stdout 摘要; exit 0 正常 / 2 参数守卫失败
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, r"D:\zcodesj")

LEDGER_DB = r"D:\zcodesj\tulong_production_paper.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT_DIR = r"D:\zcodesj\reports"


def load_overrides():
    """参数单一真源: 复用守卫函数; 守卫 fail-closed 则本监控器退出(不猜参数)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pcm", r"D:\zcodesj\production_combo_monthly.py")
    pcm = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(pcm)
    except SystemExit:
        pass
    ov = pcm.get_ml_leg_overrides()
    if not ov or "trailing_stop_pct" not in ov:
        print("[trailing] M-3 登记参数不可得(守卫fail-closed), 监控器拒绝运行")
        sys.exit(2)
    return ov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()
    date8 = int(args.date.replace("-", ""))

    ov = load_overrides()
    trail_pct_prereg = float(ov["trailing_stop_pct"])   # 0.10 = PREREG 指纹口径, 仅对账不改
    act_pct = float(ov.get("trail_activation_pct", 10)) / 100.0  # 0.10 激活门槛
    # 主人令 2026-11-07「止盈不是硬止盈, 是波动止盈: 最高点下跌8%就止盈, 让利润奔跑」:
    # 提示口径 8pp; PREREG 结算口径仍 10pp(指纹 a41fbe 不动), 两者分离并同时打印便于对账。
    HINT_DD_PCT = 0.08
    print(f"[trailing] 提示口径(主人令11-07): trail={HINT_DD_PCT:.0%} activation={act_pct:.0%} | "
          f"PREREG对账口径 trail={trail_pct_prereg:.0%}")

    # 1. ml 腿持仓 (账本权威标记)
    con = sqlite3.connect("file:%s?mode=ro" % LEDGER_DB.replace("\\", "/"), uri=True)
    holds = con.execute(
        "SELECT code, name, shares, cost, buy_date FROM positions "
        "WHERE source='ml_signal' AND shares>0").fetchall()
    con.close()
    print(f"[trailing] ml来源持仓: {len(holds)} 只 (10/1 首次调仓前为空属正常)")

    # 2. qfq 价格扫描
    qc = sqlite3.connect("file:%s?mode=ro" % QFQ_DB.replace("\\", "/"), uri=True)
    rows_out = []
    for code, name, shares, cost, buy_date in holds:
        try:
            bd = int(str(buy_date).replace("-", ""))
            rows = qc.execute(
                "SELECT date, high, close FROM stock_daily_meta_qfq "
                "WHERE code=? AND date>=? AND date<=? ORDER BY date",
                (code, bd, date8)).fetchall()
            if not rows:
                rows_out.append({"code": code, "name": name, "status": "N/A(qfq无数据)", "na": True})
                continue
            peak_high = max(r[1] for r in rows)
            last_close = rows[-1][2]
            last_date = rows[-1][0]
            gain_at_peak = peak_high / float(cost) - 1 if cost else np.nan
            activated = gain_at_peak == gain_at_peak and gain_at_peak >= act_pct
            drawdown = 1 - last_close / peak_high if peak_high else np.nan
            trigger = bool(activated and drawdown == drawdown and drawdown >= HINT_DD_PCT)
            trigger_prereg = bool(activated and drawdown == drawdown and drawdown >= trail_pct_prereg)
            rows_out.append({
                "code": code, "name": name, "shares": shares, "cost": cost,
                "entry": bd, "last_date": last_date, "last_close": last_close,
                "peak_high": peak_high, "gain_at_peak": round(gain_at_peak, 4) if gain_at_peak == gain_at_peak else None,
                "activated": activated, "drawdown": round(drawdown, 4) if drawdown == drawdown else None,
                "trigger": trigger, "trigger_prereg10": trigger_prereg,
                "status": ("触发" if trigger else ("已激活·未触发" if activated else "未激活")), "na": False})
        except Exception as e:
            rows_out.append({"code": code, "name": name, "status": "N/A(%s)" % str(e)[:40], "na": True})
    qc.close()

    triggers = [r for r in rows_out if r.get("trigger")]
    # 3. 落盘 + 摘要 (只读提示器: 触发≠卖出, 卖出走主人裁决+pre_sell+executor)
    out = os.path.join(REPORT_DIR, "trailing_scan_%s.md" % args.date)
    lines = [
        "# ml 腿 trailing 止盈监控 %s (只读提示型)" % args.date,
        "",
        "> 参数: trail=%.0f%% / activation=%.0f%% (M-3登记 %s) | ml持仓: %d 只 | 触发: %d 只" % (
            trail_pct * 100, act_pct * 100, ov.get("gate_fingerprint", "?"), len(holds), len(triggers)),
        "",
    ]
    if not holds:
        lines.append("ml 来源持仓为空 (10/1 首次调仓前属正常) — 无可监控标的。")
    elif rows_out:
        lines.append("| 代码 | 名称 | 股数 | 成本 | 最新close | entry后peak | peak浮盈 | 激活 | 回撤 | 状态 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in rows_out:
            if r.get("na"):
                lines.append("| %s | %s | - | - | - | - | - | - | - | %s |" % (r["code"], r["name"], r["status"]))
            else:
                lines.append("| %s | %s | %s | %.3f | %.2f | %.2f | %s | %s | %s | **%s** |" % (
                    r["code"], r["name"], r["shares"], r["cost"], r["last_close"], r["peak_high"],
                    ("%.1f%%" % (r["gain_at_peak"] * 100)) if r["gain_at_peak"] is not None else "N/A",
                    "是" if r["activated"] else "否",
                    ("%.1f%%" % (r["drawdown"] * 100)) if r["drawdown"] is not None else "N/A",
                    r["status"]))
    if triggers:
        lines += ["", "## ⚠️ 触发清单 (供主人裁决; 卖出必须 pre_sell 前置 + trade_executor 唯一入口, 禁止直接 MCP 卖出)"]
        for r in triggers:
            lines.append("- %s(%s) peak浮盈%.1f%% → 回撤%.1f%% ≥ %.0f%%" % (
                r["name"], r["code"], r["gain_at_peak"] * 100, r["drawdown"] * 100, trail_pct * 100))
    lines += ["", "> 已知近似: peak 用 high(盘中)较回测 close 口径更保守更早触发; 首月 ml 调仓后与回测引擎逐日对账。"]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[trailing] 报告: %s | 触发 %d 只" % (out, len(triggers)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
