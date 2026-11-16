#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_weekly_winrate_report.py — 方法胜率周榜 + T+20 结算进度周报

2026-11-03 重接线(主人令「打分器修复」): 旧数据源 .gate_logs/*_gate_result.json 为
strategy_gate 评估记录, 自 8/21 买入流迁移至 trade_executor+pre_trade_check_v2 后零写入
(最后记录 20260821), 周报静默哑火至今。新数据源=两个活源:
  1) data/group_pnl_report_YYYYMMDD.json — 各方法当前持仓胜率快照(每日 22:00 组盈亏任务产出)
  2) data/settle_t20_report_*.json        — T+20 满窗结算(OBSERVE_SOURCES 注记钦定唯一权威口径;
                                             当前各批未满窗=全 pending, 首批满窗≈11月下旬=赛马首考窗口)
输出: reports/gate_winrate_weekly_YYYYMMDD.md (幂等覆盖)。用法不变: [--days N](兼容保留, 仅影响标题窗口文案)。
旧闸门评估记录事件研究法已随数据源一并退役(闸门质量改由裸奔审计/对账面承担)。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from trade_day_guard import is_trading_day  # noqa: E402
from _common import METHOD_ADMISSION_INTERIM_SOURCES, SOURCE_CN_NAMES  # noqa: E40

REPORT_DIR = r"D:\zcodesj\reports"
DATA_DIR = r"D:\zcodesj\data"


def _latest(pattern: str) -> str | None:
    files = sorted(glob.glob(os.path.join(DATA_DIR, pattern)))
    return files[-1] if files else None


def _cn(source: str) -> str:
    return SOURCE_CN_NAMES.get(source, source)


def load_latest_group_pnl() -> tuple[str, dict]:
    """最新组盈亏日报的 per-source 统计 {source: {n,win,wr,avg}}。"""
    path = _latest("group_pnl_report_*.json")
    if not path:
        raise FileNotFoundError("data/group_pnl_report_*.json 缺失 — 组盈亏任务(22:00)未产出, 打分器 fail-loud")
    d = json.load(open(path, encoding="utf-8"))
    rows = {}
    for sec in ("groups_source", "groups_nine_appendix"):
        for name, g in (d.get(sec) or {}).items():
            if isinstance(g, dict) and g.get("n"):
                old = rows.get(name)
                if old is None or (g.get("n") or 0) >= (old.get("n") or 0):
                    rows[name] = {"n": g.get("n", 0), "win": g.get("win", 0),
                                  "wr": g.get("wr"), "avg": g.get("avg")}
    return os.path.basename(path), rows


def load_latest_settle_t20() -> tuple[str, dict]:
    """最新 T+20 结算报告 per-source {settled_n, pending, total_builds}。
    只认日期 <= 今天的报告(防测试残留未来日期文件, 2026-11-03 发现 settle_t20_report_20270601)。"""
    today = datetime.now().strftime("%Y%m%d")
    files = sorted(glob.glob(os.path.join(DATA_DIR, "settle_t20_report_*.json")))
    files = [f for f in files
             if os.path.basename(f)[len("settle_t20_report_"):-len(".json")].isdigit()
             and os.path.basename(f)[len("settle_t20_report_"):-len(".json")] <= today]
    if not files:
        return "(无)", {}
    path = files[-1]
    d = json.load(open(path, encoding="utf-8"))
    rows = {}
    for name, s in (d.get("sources") or {}).items():
        if isinstance(s, dict):
            settled = s.get("settled") or {}
            rows[name] = {"settled_n": settled.get("n", 0),
                          "pending": s.get("pending", 0),
                          "total_builds": s.get("total_builds", 0)}
    return os.path.basename(path), rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    if is_trading_day() is False:
        print("[gate-weekly] 休市日(交易日历判定), 优雅跳过")
        return 0

    pnl_file, pnl = load_latest_group_pnl()
    settle_file, settle = load_latest_settle_t20()
    if not pnl:
        print("组盈亏日报无任何源统计 → 无从打分, 不产出空报告")
        return 0

    today = datetime.now().strftime("%Y%m%d")
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"gate_winrate_weekly_{today}.md")
    lines: list[str] = [
        f"# 方法胜率周榜 + T+20结算进度（{today}）",
        "",
        f"- 持仓快照源: {pnl_file}（每日22:00组盈亏任务, 当日持仓口径）",
        f"- T+20权威源: {settle_file}（满窗前 pending 占多数, 首批满窗≈11月下旬=赛马首考窗口）",
        f"- 暂行方法准入(2026-11-03主人令, 复审点11/26): {'、'.join(sorted(METHOD_ADMISSION_INTERIM_SOURCES))}",
        "",
        "## 一、各方法当前持仓胜率排行（快照口径, 小样本仅供观察）",
        "",
        "| 方法 | 中文名 | 样本 | 胜率 | 均盈 | 白名单 |",
        "|---|---|---|---|---|---|",
    ]

    def wr_key(r):
        return r.get("wr") if isinstance(r.get("wr"), (int, float)) else -1.0

    for name, r in sorted(pnl.items(), key=lambda kv: wr_key(kv[1]), reverse=True):
        wr = r.get("wr")
        wr_s = f"{wr*100:.1f}%" if isinstance(wr, (int, float)) else "N/A"
        star = " ⭐暂行实买" if name in METHOD_ADMISSION_INTERIM_SOURCES else ""
        lines.append(f"| {name} | {_cn(name)} | {r['n']} | {wr_s} | {r.get('avg')} |{'是' if name in METHOD_ADMISSION_INTERIM_SOURCES else '观察'}{star} |")

    lines += ["", "## 二、T+20 满窗结算进度（权威口径; settled_n>0 才计入考核）", "",
              "| 方法 | 已结算 | 待满窗 | 累计建仓 |", "|---|---|---|---|"]
    for name, s in sorted(settle.items(), key=lambda kv: -(kv[1].get("total_builds") or 0)):
        if not s.get("total_builds"):
            continue
        lines.append(f"| {name} | {s.get('settled_n', 0)} | {s.get('pending', 0)} | {s.get('total_builds', 0)} |")

    wl = [n for n in pnl if n in METHOD_ADMISSION_INTERIM_SOURCES and isinstance(pnl[n].get("wr"), (int, float))]
    obs = [n for n in pnl if n not in METHOD_ADMISSION_INTERIM_SOURCES and isinstance(pnl[n].get("wr"), (int, float)) and (pnl[n].get("n") or 0) >= 5]
    lines += ["", "## 三、判读", ""]
    if wl:
        wavg = sum(pnl[n]["wr"] for n in wl) / len(wl)
        lines.append(f"- 白名单七组加权胜率(快照): **{wavg*100:.1f}%**（{len(wl)}组有样本）")
    if obs:
        oavg = sum(pnl[n]["wr"] for n in obs) / len(obs)
        lines.append(f"- 观察组(n>=5)加权胜率(快照): {oavg*100:.1f}%（{len(obs)}组）")
    if settle:
        tot_pend = sum(s.get("pending", 0) for s in settle.values())
        tot_settled = sum(s.get("settled_n", 0) for s in settle.values())
        lines.append(f"- T+20 全源累计: 已结算 {tot_settled} / 待满窗 {tot_pend}"
                     f" — 首考(11/26)以 T+20 满窗结算为唯一权威, 满窗前白名单调整只看快照趋势+主人裁决")
    lines.append("")
    lines.append(f"---\n*生成: {datetime.now().isoformat(timespec='seconds')}; 幂等覆盖; "
                 f"快照口径小样本仅供观察, 淘汰判罚以 T+20 满窗结算为准(11/26 首考)。*")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[报告] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
