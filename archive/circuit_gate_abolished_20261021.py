#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[已废除 2026-10-21 主人令] 熔断+冷却期买入闸（原 PD-037，2026-10-07 批准）。

废除理由：模拟盘(practice_190)=研究训练场，防连亏刹车属实盘机制；
本闸于 2026-10-21 整体废除（trade_executor.py 消费点同步移除），
详见 PROJECT_BOUNDARY §6 已否定路线。恢复须经主人重新立项。
本文件归档留存仅供历史审计，禁止直接运行或重新接线。
================================================================================
[以下为废除前原文件内容]
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""熔断+冷却期买入闸（PD-037，主人 2026-10-07 批准落地）。

唯一 owner：本文件。trade_executor.py 买入端在会话闸之后调用本闸；
卖出/持仓管理完全不受影响；调仓换股豁免走 --owner-override-circuit 逐笔特批。

规则（PD-037 呈报批准口径）：
  1) 当日熔断：当日已实现亏损 ≤ -CIRCUIT_LOSS_PCT×ACCOUNT_BASE → 当日禁止新开仓；
  2) 冷却期：前一交易日触发（当日已实现亏损 ≤ -COOLDOWN_LOSS_PCT×ACCOUNT_BASE
     或 当日 S-5 类止损平仓 ≥ COOLDOWN_STOP_CNT 笔）→ 次一交易日禁止新开仓。

数据源（只读）：attribution/attribution.db position_history（当日平仓记录，含 pnl 与
退出理由）+ data/dragon_quant_qfq.db（用 000001 的交易日序列推「前一交易日」，仅取日期
不取价格）。月度回撤熔断需主人定阈值 X 后另补（PD-037 呈报原文留白，未擅定）。

fail-closed：任一数据源不可用 → REJECT（理由=circuit_gate_unavailable），
与 westock token 异常「最坏 fail-closed 零风险」同一哲学；确需交易走 owner 豁免旗。

用法：
  python circuit_gate.py                 # 按今天判定（executor 同款调用）
  python circuit_gate.py --date 20260915 # 历史回放（验收：9/14 七连止损次日应 REJECT）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
# 20261011批4: 改读 workbuddyspace 主库(与 pre_trade R31/R32/R37 单一价格源对齐)——此前读
# zcodesj/data 副本且无新鲜度校验(实测副本 mtime 落后主库 31 分钟), 副本滞后时冷却期窗口
# 判错=fail-open 方向。主库不可用 → _prev_trading_day 异常 → check_circuit fail-closed REJECT。
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CAL_CODE = "000001"  # 平安银行：成交连续，其日期序列作交易日历代理（仅用日期不用价格）

# ---- 阈值常量（PD-037 批准口径；调整须主人裁决并回写本注释）----
ACCOUNT_BASE = 900_000.0     # 账户规模近似基准（元），用于把百分比阈值换算成金额
COOLDOWN_LOSS_PCT = 0.02     # 冷却期触发：当日已实现亏损 ≥ 2%×基准
CIRCUIT_LOSS_PCT = 0.03      # 当日熔断触发：当日已实现亏损 ≥ 3%×基准
COOLDOWN_STOP_CNT = 2        # 冷却期触发：当日 S-5 类止损平仓 ≥ 2 笔


def _norm_code(c: str) -> str:
    c = (c or "").strip()
    return c[2:] if c[:2] in ("sh", "sz", "bj") else c


def _is_stop_reason(reason: str) -> bool:
    r = reason or ""
    return ("S-5" in r) or ("S5_stoploss" in r) or ("止损" in r)


def _prev_trading_day(date8: str) -> str | None:
    """qfq 库 000001 日期序列中严格小于 date8 的最大交易日（无则 None）。"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq WHERE code=? AND date<?",
            (CAL_CODE, date8)).fetchone()
        return row[0] if row and row[0] else None
    finally:
        con.close()


def _day_exits(con, date8: str) -> list[dict]:
    """当日全部平仓记录（按 code+entry_date+exit_date 去重，防多来源导入重复计数）。"""
    rows = con.execute(
        "SELECT code, entry_date, exit_date, pnl_amount, pnl_pct, exit_reason "
        "FROM position_history WHERE status='CLOSED' AND exit_date=?",
        (date8,)).fetchall()
    seen, out = set(), []
    for code, entry_date, exit_date, pnl_amount, pnl_pct, reason in rows:
        # 20261011批4: 去重键补 pnl——同日同票两笔真实平仓(当日回补再止损)此前被折叠成
        # 一笔, 已实现亏损低估 → 熔断/冷却该触发不触发(fail-open 方向)。
        # round(2dp) 吸收重导入浮点噪声, 同源重复仍为同键照常去重。
        key = (_norm_code(code), entry_date, exit_date, round(float(pnl_amount or 0.0), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": key[0], "pnl_amount": float(pnl_amount or 0.0),
                    "pnl_pct": float(pnl_pct or 0.0), "reason": reason or ""})
    return out


def check_circuit(trade_date: str | None = None) -> dict:
    """买入端熔断+冷却期判定。返回 {verdict, reasons, details}；任何异常 fail-closed。"""
    today = trade_date or datetime.now().strftime("%Y%m%d")
    details: dict = {"gate": "circuit", "trade_date": today,
                     "thresholds": {"account_base": ACCOUNT_BASE,
                                    "cooldown_loss_pct": COOLDOWN_LOSS_PCT,
                                    "circuit_loss_pct": CIRCUIT_LOSS_PCT,
                                    "cooldown_stop_cnt": COOLDOWN_STOP_CNT}}
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        try:
            exits_today = _day_exits(con, today)
            prev_day = _prev_trading_day(today)
            exits_prev = _day_exits(con, prev_day) if prev_day else []
        finally:
            con.close()
    except Exception as e:  # fail-closed：数据不可用不放行
        details["error"] = f"{type(e).__name__}: {e}"
        return {"verdict": "REJECT",
                "reasons": [f"circuit_gate_unavailable(fail-closed): {details['error']}"],
                "details": details}

    loss_today = sum(x["pnl_amount"] for x in exits_today)
    stops_today = sum(1 for x in exits_today if _is_stop_reason(x["reason"]))
    loss_prev = sum(x["pnl_amount"] for x in exits_prev)
    stops_prev = sum(1 for x in exits_prev if _is_stop_reason(x["reason"]))
    details.update({"today": {"realized_loss": round(loss_today, 2), "stop_exits": stops_today,
                              "exit_cnt": len(exits_today)},
                    "prev_trading_day": prev_day,
                    "prev_day": {"realized_loss": round(loss_prev, 2), "stop_exits": stops_prev,
                                 "exit_cnt": len(exits_prev)}})

    reasons: list[str] = []
    if loss_today <= -CIRCUIT_LOSS_PCT * ACCOUNT_BASE:
        reasons.append(f"当日熔断: 今日已实现亏损 {loss_today:.0f}元 ≤ "
                       f"-{CIRCUIT_LOSS_PCT:.0%}×基准{ACCOUNT_BASE:.0f} → 当日禁止新开仓")
    if prev_day:
        trig = []
        if loss_prev <= -COOLDOWN_LOSS_PCT * ACCOUNT_BASE:
            trig.append(f"已实现亏损{loss_prev:.0f}元≥{COOLDOWN_LOSS_PCT:.0%}×基准")
        if stops_prev >= COOLDOWN_STOP_CNT:
            trig.append(f"S-5类止损平仓{stops_prev}笔≥{COOLDOWN_STOP_CNT}笔")
        if trig:
            reasons.append(f"冷却期: 前一交易日{prev_day}触发（{'；'.join(trig)}）→ 今日禁止新开仓")

    details["verdict_input_ok"] = True
    return {"verdict": "REJECT" if reasons else "ALLOW", "reasons": reasons, "details": details}


def main() -> int:
    ap = argparse.ArgumentParser(description="熔断+冷却期买入闸（PD-037）")
    ap.add_argument("--date", default=None, help="回放判定日期 YYYYMMDD（默认今天）")
    a = ap.parse_args()
    out = check_circuit(a.date)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["verdict"] == "ALLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
