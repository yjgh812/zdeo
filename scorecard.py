#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scorecard.py — 纪律与体系月度记分卡（M1，2026-09-29 全维度自省执行件）。

来源=自省报告 reports/full_dim_self_review_20260928.md §五M1 + §六长效自省机制：
十指标月更，趋势恶化≥2项触发专项深审。缺数据=如实 N/A 并登记缺口，禁止占位值冒充。

用法: python scorecard.py [--month 202609]
输出: reports/scorecard_<month>.md ；stdout 摘要。
口径（每指标含数据源）:
  1 裸奔笔数           violation_log.jsonl type=naked_trade
  2 宪法类违规笔数     violation_log.jsonl type∈(constitution,no_reasoning_gate)
  3 止损滞后(分钟)     reports/stop_loss_lag_<date>.json（M2 测量器产物；未产出=N/A）
  4 追高买入占比       attribution.trade_records BUY × qfq 昨收 ≥+3%（日级近似口径,注记）
  5 卖飞底距中位数     data/sold_rider_recon_ledger.jsonl（仅当月卖单样本）
  6 台账传播新增diff   ledger_status_recon 最新行（>0=传播失效）
  7 PD OVERDUE         reports/pending_aging_*.md 当月末次 OVERDUE 计数
  8 静默失效条数       reports/health_check_*.md 当月 ERROR 行计数（schtasks/cron/巡检）
  9 数据新鲜度告警天数 reports/health_check_*.md 当月 vipdoc/画像/panel WARN 天数
 10 闸门治理           data/gate_registry.json UNKNOWN 数 + 当月 falsified 净增（档案周转）
执行差距(账户vs系统同口径)不在本卡自动计算=呈报行指针（重算需回测引擎，见
reports/execution_gap_20260925/），标 N/A+指针，禁止折算成估算数。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
from datetime import datetime

ROOT = r"D:\zcodesj"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"


def _month_dates(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[4:6])
    start = f"{y}{m:02d}01"
    end = f"{y + (m == 12)}{(m % 12) + 1:02d}01"
    return start, end


def m1_m2_violations(month: str) -> dict:
    naked, const = 0, 0
    pfx = month[:4] + "-" + month[4:6]
    fp = os.path.join(ROOT, "violation_log.jsonl")
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8"):
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = str(j.get("date") or j.get("ts") or "")
            if not d.startswith(pfx):
                continue
            t = str(j.get("type") or "")
            if t == "naked_trade":
                naked += 1
            elif t in ("constitution", "no_reasoning_gate"):
                const += 1
    return {"裸奔笔数": naked, "宪法类违规笔数": const}


def m3_stoploss_lag(month: str) -> dict:
    """读 M2 测量器产物（reports/stop_loss_lag_<YYYYMM>*.json），无=N/A。"""
    fs = sorted(glob.glob(os.path.join(ROOT, "reports", f"stop_loss_lag_{month}*.json")))
    if not fs:
        return {"止损滞后中位(交易日)": "N/A", "缺口": "M2 测量器未产出(stop_loss_lag_*.json 缺)"}
    j = json.load(open(fs[-1], encoding="utf-8"))
    return {"止损滞后中位(交易日)": j.get("median_lag_days", "N/A"),
            "止损滞后P90(交易日)": j.get("p90_lag_days", "N/A"),
            "样本数": j.get("n_measured", "N/A"), "数据源": os.path.basename(fs[-1])}


def m4_chase_ratio(month: str) -> dict:
    """当月 BUY 笔数中 成交价≥买入日昨收×1.03 的占比（日级近似：以昨收为基准，
    非盘中时点价；口径限制如实注记。qfq 昨收仅用于比值=口径无关复权差）。"""
    start, _ = _month_dates(month)
    buys = []
    try:
        c = sqlite3.connect(ATTR_DB)
        buys = c.execute(
            "SELECT code, trade_date, price FROM trade_records "
            "WHERE action='BUY' AND trade_date>=? AND trade_date<?",
            (start, start[:6] + "40")).fetchall()
        c.close()
    except sqlite3.Error as e:
        return {"追高买入占比": f"N/A(读库失败 {e})"}
    if not buys:
        return {"追高买入占比": "N/A(当月无买入)", "买入笔数": 0}
    try:
        q = sqlite3.connect(QFQ_DB, uri=True)
    except sqlite3.Error as e:
        return {"追高买入占比": f"N/A(qfq 不可用 {e})"}
    chase, n = 0, 0
    cal = [str(r[0]) for r in q.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
    cidx = {d: i for i, d in enumerate(cal)}
    for code, td, price in buys:
        bare = str(code)[-6:]
        i = cidx.get(str(td))
        if i is None or i == 0 or not price:
            continue
        prev = q.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                         (bare, cal[i - 1])).fetchone()
        if not prev or not prev[0]:
            continue
        n += 1
        if float(price) >= float(prev[0]) * 1.03:
            chase += 1
    q.close()
    if n == 0:
        return {"追高买入占比": "N/A(可配对样本0)", "买入笔数": len(buys)}
    return {"追高买入占比": f"{chase}/{n} = {chase / n * 100:.1f}%",
            "口径注记": "日级昨收对比近似(非盘中时点), qfq仅用于比值", "可配对买入笔数": n}


def m5_sold_rider(month: str) -> dict:
    fp = os.path.join(ROOT, "data", "sold_rider_recon_ledger.jsonl")
    vals = []
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8"):
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            sd = str(j.get("sell_date") or "")
            if sd.startswith(month):
                bd = j.get("bottom_dist_pct")
                if isinstance(bd, (int, float)):
                    vals.append(float(bd))
    if not vals:
        return {"卖飞底距中位数": "N/A(当月无样本)", "卖飞样本数": len(vals)}
    vals.sort()
    med = vals[len(vals) // 2]
    return {"卖飞底距中位数": f"{med:.2f}%", "样本数": len(vals)}


def m6_ledger_diff() -> dict:
    fp = os.path.join(ROOT, "ledger_status_recon.py")
    import subprocess
    try:
        r = subprocess.run(["python", fp], capture_output=True, text=True, timeout=120,
                           cwd=ROOT)
        line = next((l for l in (r.stdout or "").splitlines() if l.startswith("[RECON]")), "")
        kv = dict(tok.split("=", 1) for tok in line.replace("[RECON]", "").split() if "=" in tok)
        return {"新增diff": kv.get("new_diff", "N/A"), "存量diff": kv.get("diff", "N/A")}
    except Exception as e:
        return {"新增diff": f"N/A({e})"}


def m7_pd_overdue(month: str) -> dict:
    fs = sorted(glob.glob(os.path.join(ROOT, "reports", f"pending_aging_{month}*.md")))
    if not fs:
        return {"PD_OVERDUE": "N/A(当月无 aging 报告)"}
    t = open(fs[-1], encoding="utf-8").read()
    m = re.search(r"OVERDUE=(\d+)", t)
    return {"PD_OVERDUE": int(m.group(1)) if m else "N/A", "数据源": os.path.basename(fs[-1])}


def m8_m9_health(month: str) -> dict:
    errs, warn_days = 0, 0
    fs = sorted(glob.glob(os.path.join(ROOT, "reports", f"health_check_{month}*.md")))
    for f in fs:
        t = open(f, encoding="utf-8").read()
        errs += len(re.findall(r"^\|\s*ERROR\s*\|", t, re.M)) + len(re.findall(r"❌", t))
        if re.search(r"vipdoc.*(未更新|WARN)|画像.*未刷新|panel_v6.*WARN", t):
            warn_days += 1
    return {"静默失效ERROR行数(月内累计)": errs if fs else "N/A",
            "数据新鲜度告警天数": warn_days if fs else "N/A",
            "health报告数": len(fs)}


def m10_gate_governance(month: str) -> dict:
    unknown = "N/A"
    try:
        g = json.load(open(os.path.join(ROOT, "data", "gate_registry.json"), encoding="utf-8"))
        unknown = g.get("counts", {}).get("UNKNOWN", "N/A")
    except (OSError, json.JSONDecodeError):
        pass
    # falsified 净增：档案条目数快照对上月（无上月快照=仅报当期数）
    try:
        d = json.load(open(os.path.join(ROOT, "data", "falsified_rules.json"), encoding="utf-8"))
        total = len(d.get("rules", {}))
    except (OSError, json.JSONDecodeError):
        total = "N/A"
    return {"闸UNKNOWN欠账数": unknown, "证伪档案总条数": total}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=datetime.now().strftime("%Y%m"))
    a = ap.parse_args()
    metrics = {
        "1 裸奔/宪法违规": m1_m2_violations(a.month),
        "2 止损滞后(M2)": m3_stoploss_lag(a.month),
        "3 追高买入占比": m4_chase_ratio(a.month),
        "4 卖飞底距": m5_sold_rider(a.month),
        "5 台账传播": m6_ledger_diff(),
        "6 PD超时": m7_pd_overdue(a.month),
        "7-8 静默失效/新鲜度": m8_m9_health(a.month),
        "9 闸门治理": m10_gate_governance(a.month),
        "10 执行差距": {"账户vs系统同口径": "N/A",
                        "指针": "需回测引擎重算,流程=reports/execution_gap_20260925/ 同口径",
                        "禁止": "不得以估算冒充"},
    }
    lines = [f"# 纪律与体系记分卡 {a.month}（生成 {datetime.now().strftime('%Y-%m-%d %H:%M')}）", "",
             "> 口径真源=reports/full_dim_self_review_20260928.md §五M1/§六；缺数据=N/A+缺口登记，禁止占位冒充。",
             "> 触深审条款：环比恶化指标≥2 项 → 专项深审（首卡无环比，自 202610 卡起生效）。", ""]
    for g, kv in metrics.items():
        lines.append(f"## {g}")
        for k, v in kv.items():
            lines.append(f"- {k}: **{v}**")
        lines.append("")
    out = os.path.join(ROOT, "reports", f"scorecard_{a.month}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[SCORECARD] {a.month} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
