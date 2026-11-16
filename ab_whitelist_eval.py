#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""白名单 A/B 实验评估（2026-09-08 主人裁决「跑一段时间看看效果」配套）。

分臂口径: guard_precheck_log.source 判臂——
  A臂 = 白名单源(bp_ep/master_hist/ml_signal/bp_ep_core/turnover_mvp/mass_scan/full_market/mainline_radar)
  B臂 = ab_freeroute(名单豁免)
成交确认: 仅统计与 executor_orders.jsonl 实际 BUY 提交匹配的检查记录(code+trade_date)。
收益: qfq 库 T+5/T+10 收盘 vs 成交价, 同时算同期全市场等权基准 → 超额。
纯只读; 输出 reports/AB_WHITELIST_EVAL_{date}.md。中期检视 {mid}, 终评 {end}。
"""
import csv
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ATTR_DB = os.path.join(BASE, "attribution", "attribution.db")
ORDERS = os.path.join(BASE, "data", "executor_orders.jsonl")
A_SOURCES = {"bp_ep", "master_hist", "ml_signal", "bp_ep_core", "turnover_mvp",
             "mass_scan", "full_market", "mainline_radar",
             # 2026-09-10 B6 修复: 9/8 白名单宇宙化后 A 臂主来源=universe(1040/1154 行),
             # 缺失致 A 臂样本被静默丢弃(arm=None)
             "universe"}
FWD_DAYS = (5, 10)


def _code6(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-6:]


def _trading_dates_after(con, date8, n_list):
    """date8 之后的交易日序列（升序，取 max(n_list) 个）→ {5: date5, 10: date10}"""
    rows = con.execute("select distinct date from stock_daily_meta_qfq where date>? order by date",
                       (date8,)).fetchall()
    dates = [r[0] for r in rows]
    out = {}
    for n in n_list:
        out[n] = dates[n - 1] if len(dates) >= n else None
    return out


def _market_mean(con, date8, target):
    """全市场**等权** fwd 收益基准(2026-09-10 B6 修复): 原 avg(close) 之比被高价股
    主导(1400 元股票权重≈100 只 14 元股票); 等权=每码 (target收盘/date8收盘-1) 均值。"""
    if not target:
        return None
    r = con.execute(
        "select avg(b.close/a.close - 1) from stock_daily_meta_qfq a "
        "join stock_daily_meta_qfq b on a.code=b.code "
        "where a.date=? and b.date=? and a.close>0 and b.close>0",
        (date8, target)).fetchone()[0]
    return float(r) if r is not None else None


def main():
    cfg_path = os.path.join(BASE, "data", "ab_whitelist_experiment.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    start = str(cfg["start_date"])
    mid, end = str(cfg.get("mid_review_date", "")), str(cfg["end_date"])

    # 实际成交 BUY 索引 → {(code6, trade_date): 成交价}。2026-09-10 修正: 撤单/未成交
    # 提交不算"实际买入"(双塔 9/8 B臂挂单 4.56 撤单未成交——executor_orders 有提交行
    # 但非成交), 成交真值=open_orders FILLED + trade_records BUY 双源合并。
    fills = {}
    try:
        with open(os.path.join(BASE, "data", "open_orders.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("action") == "BUY" and r.get("status") == "FILLED":
                    fills[(_code6(r.get("code")), str(r.get("trade_date", "")))] = r.get("price")
    except OSError:
        pass
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        for code, td, price in con.execute(
                "select code, trade_date, price from trade_records where action='BUY'"):
            key = (_code6(code), str(td))
            if key not in fills:
                fills[key] = price
        con.close()
    except Exception as e:
        print(f"[WARN] trade_records 成交读取失败: {type(e).__name__}: {e}", file=sys.stderr)

    con_a = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    checks = con_a.execute(
        "select trade_date, code, name, source, verdict from guard_precheck_log "
        "where trade_date>=? order by trade_date", (start,)).fetchall()
    con_a.close()

    qfq = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    arms = {"A": [], "B": []}
    seen = set()
    for td, code, name, src, verdict in checks:
        code6 = _code6(code)
        # 2026-09-10 B1 修复: 原 key=(td, code6) 与 fills 的 (code6, td) 顺序颠倒
        # → 匹配恒 0(实验评估零样本哑弹); 统一 (code6, td)
        key = (code6, td)
        if key in seen or key not in fills:
            continue
        seen.add(key)
        arm = "B" if src == "ab_freeroute" else ("A" if src in A_SOURCES else None)
        if not arm:
            continue
        if "PASS" not in str(verdict).upper():
            continue
        # B6: entry 优先 executor_orders 真实成交价, 缺失回退 qfq 当日收盘
        _opx = fills.get(key)
        if _opx and float(_opx) > 0:
            entry = float(_opx)
        else:
            px = qfq.execute("select close from stock_daily_meta_qfq where code=? and date=?",
                             (code6, td)).fetchone()
            if not px or not px[0]:
                continue
            entry = float(px[0])
        tgt = _trading_dates_after(qfq, td, FWD_DAYS)
        rec = {"date": td, "code": code6, "name": name, "source": src}
        for n in FWD_DAYS:
            d = tgt[n]
            pxn = qfq.execute("select close from stock_daily_meta_qfq where code=? and date=?",
                              (code6, d)).fetchone() if d else None
            bm = _market_mean(qfq, td, d)
            rec[f"fwd{n}"] = (float(pxn[0]) / entry - 1) if (pxn and pxn[0]) else None
            rec[f"exc{n}"] = (rec[f"fwd{n}"] - bm) if (rec.get(f"fwd{n}") is not None and bm is not None) else None
        arms[arm].append(rec)
    qfq.close()

    today = datetime.now().strftime("%Y%m%d")
    out = os.path.join(BASE, "reports", f"AB_WHITELIST_EVAL_{today}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 白名单A/B实验评估 {today}\n\n实验窗 {start}~{end}（中期检视 {mid}）；"
                f"仅统计 pre_trade PASS 且 executor 实际提交 BUY 的记录。\n")
        for arm, label in (("A", "A臂·名单制(现行)"), ("B", "B臂·名单豁免(ab_freeroute)")):
            rows = arms[arm]
            f.write(f"\n## {label}\n\n样本 n={len(rows)}\n")
            if not rows:
                f.write("\n（暂无样本——B臂买入发生后本表自动积累；如实报告不编造数字）\n")
                continue
            for n in FWD_DAYS:
                vals = [r[f"fwd{n}"] for r in rows if r.get(f"fwd{n}") is not None]
                exs = [r[f"exc{n}"] for r in rows if r.get(f"exc{n}") is not None]
                if vals:
                    win = sum(1 for v in vals if v > 0) / len(vals)
                    f.write(f"- fwd{n}: 均值 {sum(vals)/len(vals)*100:+.2f}% / 胜率 {win*100:.0f}% "
                            f"(n={len(vals)})")
                    if exs:
                        f.write(f" / 超额均值 {sum(exs)/len(exs)*100:+.2f}%")
                    f.write("\n")
                else:
                    f.write(f"- fwd{n}: 未到期（如实标注，不编造）\n")
            f.write("\n明细:\n\n| 日期 | 代码 | 名称 | source | fwd5 | fwd10 |\n|---|---|---|---|---|---|\n")
            for r in rows:
                f5 = f"{r['fwd5']*100:+.2f}%" if r.get("fwd5") is not None else "未到期"
                f10 = f"{r['fwd10']*100:+.2f}%" if r.get("fwd10") is not None else "未到期"
                f.write(f"| {r['date']} | {r['code']} | {r['name']} | {r['source']} | {f5} | {f10} |\n")
    print(f"[ok] {out} | A臂n={len(arms['A'])} B臂n={len(arms['B'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
