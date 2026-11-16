#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trade_decision_logger.py — 决策日志自动落盘 (t12)

屠龙三层决策体系之决策留痕层：
  strategy_gate.py 每跑完一批候选 → log_decision() 自动追加写入按日聚合的决策日志
  D:/zcodesj/.gate_logs/trade_decision_log_YYYYMMDD.json

日志结构（按日聚合, decisions 数组随每次决策自动追加）:
  {
    "date": "2026-08-17",
    "decisions": [
      {
        "timestamp": "2026-08-17T15:45:21",
        "candidates_total": 160,
        "passed":  ["601609", "600198"],
        "warned":  ["300411"],
        "rejected":["600272"],
        "action": "scan_only" / "executed_buy",
        "executed_orders": [{"code","price","qty","orderId"}],
        "top_scores": [{"code","name","score"} ...前5],
        "gate_version": "v2.1"
      }
    ]
  }

与 strategy_gate.py 集成:
  - 批量模式(--candidates): 默认开启决策落盘 (--no-decision-log 可关)
  - 单股/回放模式: 加 --log-decision 强制开启
  - --action executed_buy + --executed-orders '[{"code":"601609","price":11.57,"qty":200,"orderId":"..."}]'
    (executed-orders 也接受 JSON 文件路径)

用法:
  # 独立调用: 把既有闸门结果日志转成决策日志
  python trade_decision_logger.py --gate-log D:/zcodesj/.gate_logs/20260817_191637_gate_result.json --action scan_only

  # 重建今日演示日志 (早间批次 + 金田最终买入 + ST长园G0拦截)
  python trade_decision_logger.py --rebuild-today

v2.1 = strategy_gate v2.0 分层评分体系 + t16 G0-ST硬底线修补
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# ===== 常量 =====
GATE_VERSION = "v2.1"          # strategy_gate v2.0 + t16(G0-ST底线) => v2.1
LOG_DIR = r"D:\zcodesj\.gate_logs"
VALID_ACTIONS = ("scan_only", "executed_buy")
TOP_SCORES_N = 5

os.makedirs(LOG_DIR, exist_ok=True)


# ========== 内部工具 ==========

def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _log_path(date: str | None = None) -> str:
    """按日聚合: trade_decision_log_YYYYMMDD.json"""
    d = date or _today_str()
    stamp = d.replace("-", "")
    return os.path.join(LOG_DIR, f"trade_decision_log_{stamp}.json")


def _extract_results(gate_output) -> list[dict]:
    """从各种闸门输出形态中提取逐股结果列表。

    兼容:
      - batch_process() 输出          {"timestamp","total","verdicts",...,"results":[...]}
      - write_gate_log 写出的单股日志  {"single": {...}}
      - 回放日志                       {"replay": "E1", "result": {...}}
      - 结果列表                       [ {...}, ... ]
      - 单个结果                       {"code","verdict",...}
    """
    if gate_output is None:
        return []
    if isinstance(gate_output, list):
        return [r for r in gate_output if isinstance(r, dict)]
    if not isinstance(gate_output, dict):
        return []
    if isinstance(gate_output.get("results"), list):
        return gate_output["results"]
    if isinstance(gate_output.get("single"), dict):
        return [gate_output["single"]]
    if isinstance(gate_output.get("result"), dict):
        return [gate_output["result"]]
    if "code" in gate_output and "verdict" in gate_output:
        return [gate_output]
    return []


def _codes(results: list[dict], verdict: str) -> list[str]:
    return [str(r.get("code", "")) for r in results if r.get("verdict") == verdict]


def _reject_details(results: list[dict]) -> list[dict]:
    """被拒原因明细(归因复盘用): 含硬底线一票否决(G0/G1/G3/G8/G9)与评分拒绝原因。"""
    details = []
    for r in results:
        if r.get("verdict") != "REJECT":
            continue
        hard = r.get("hard_gates") or r.get("gate_results") or {}
        hard_fail_reasons = {}
        for k, v in hard.items():
            if isinstance(v, dict) and not v.get("pass", True):
                hard_fail_reasons[k] = v.get("reason", "")
        details.append({
            "code": str(r.get("code", "")),
            "name": r.get("name", ""),
            "score": r.get("score", 0.0),
            "hard_fails": r.get("hard_fails") or list(hard_fail_reasons.keys()),
            "reason": r.get("verdict_detail", ""),
            "hard_fail_reasons": hard_fail_reasons or None,
        })
    return details


# ========== 核心API ==========

def log_decision(gate_output: dict, action: str = "scan_only",
                 executed: list | None = None, note: str | None = None,
                 ts: str | None = None, trigger: str = "batch") -> str:
    """闸门跑完后调用: 把本次决策追加写入按日聚合的决策日志, 返回日志文件路径。

    参数:
      gate_output : strategy_gate 的输出 (batch_process 结果 / 单股结果 / 结果列表)
      action      : "scan_only"(仅扫描) 或 "executed_buy"(已执行买入)
      executed    : 已执行订单 [{"code","price","qty","orderId"}] (action=executed_buy 时填写)
      note        : 备注归因上下文(可选)
      ts          : 覆盖时间戳(复盘重建历史批次时用, 默认当前时间)
      trigger     : batch / single / replay / manual
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"action 必须是 {VALID_ACTIONS} 之一, 收到: {action!r}")

    results = _extract_results(gate_output)
    executed = executed or []

    # top_scores: 按分数降序前5
    ranked = sorted(results, key=lambda r: r.get("score", 0.0) or 0.0, reverse=True)
    top_scores = [
        {"code": str(r.get("code", "")), "name": r.get("name", ""), "score": r.get("score", 0.0)}
        for r in ranked[:TOP_SCORES_N]
    ]

    decision = {
        "timestamp": ts or datetime.now().isoformat(timespec="seconds"),
        "candidates_total": len(results),
        "passed": _codes(results, "PASS"),
        "warned": _codes(results, "PASS_WITH_WARN"),
        "rejected": _codes(results, "REJECT"),
        "action": action,
        "executed_orders": [
            {
                "code": str(o.get("code", "")),
                "price": o.get("price"),
                "qty": o.get("qty"),
                "orderId": o.get("orderId"),
                **({"name": o["name"]} if o.get("name") else {}),
            }
            for o in executed if isinstance(o, dict)
        ],
        "top_scores": top_scores,
        "gate_version": GATE_VERSION,
    }
    # 归因扩展字段
    if note:
        decision["note"] = note
    decision["trigger"] = trigger
    scores = [r.get("score", 0.0) or 0.0 for r in results]
    if scores:
        decision["avg_score"] = round(sum(scores) / len(scores), 1)
    rej = _reject_details(results)
    if rej:
        decision["rejected_details"] = rej

    path = append_decision(decision)
    return path


def append_decision(decision: dict, date: str | None = None) -> str:
    """按日聚合追加: 读取当日文件(无则新建) → decisions 追加 → 原子写回。"""
    date = date or _today_str()
    path = _log_path(date)

    data = {"date": date, "decisions": []}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data.get("decisions"), list):
                data["decisions"] = []
        except (json.JSONDecodeError, OSError):
            data = {"date": date, "decisions": []}  # 损坏则重建, 不阻断决策

    data["decisions"].append(decision)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


# ========== 今日演示重建 (t12) ==========

def rebuild_today() -> str:
    """用 2026-08-17 当日真实闸门留痕, 重建三段决策日志:
      1) 早间批量买入批次  (候选池取自当日上午闸门日志, 用 v2.1 复评)
      2) 金田股份最终买入批次 (100分通过 + 已执行 + orderId 留痕)
      3) ST长园(600525) 被 G0 一票否决拦截记录 (t16 修补后全量复扫)
    """
    import strategy_gate as sg  # 延迟导入避免环

    gl = LOG_DIR
    date = "2026-08-17"
    out_path = _log_path(date)
    if os.path.isfile(out_path):
        os.remove(out_path)  # 干净重建

    # ---- 1) 早间批量买入批次: 候选池 = 当日上午闸门日志可考证的7只 ----
    with open(os.path.join(gl, "final_candidates.json"), "r", encoding="utf-8") as f:
        morning_cands = json.load(f)          # 12:32 批次6只(开开实业/金田/大唐/京东方A/盈新/皇氏)
    morning_cands.append(dict(sg.REPLAY_CASES["E2"]))  # + 天娱数科(12:33批, E2错误案例)

    batch = sg.batch_process(morning_cands, mainlines=["算力/东数西算", "光纤通信/CPO/F5G", "芯片/半导体"])
    p1 = log_decision(
        batch, action="executed_buy",
        executed=[{"code": c["code"], "price": c.get("price"), "qty": None, "orderId": None}
                  for c in morning_cands],
        ts="2026-08-17T12:32:29", trigger="batch",
        note=("早间批量买入批次(演示重建): 候选池取自当日上午闸门日志留痕7只"
              "(开开实业/金田股份/大唐电信/京东方A/盈新发展/皇氏集团/天娱数科),"
              "以v2.1复评留结果; 订单明细以券商流水为准, orderId仅金田股份最终批次有留痕"),
    )

    # ---- 2) 金田股份最终买入批次: 15:45 E2E 全市场扫描, 金田/大唐双百 ----
    with open(os.path.join(gl, "20260817_154521_gate_result.json"), "r", encoding="utf-8") as f:
        e2e = json.load(f)
    finalists = [r for r in e2e["results"] if r.get("verdict") == "PASS"]  # 金田100 + 大唐100
    p2 = log_decision(
        {"timestamp": e2e.get("timestamp"), "total": len(finalists), "results": finalists},
        action="executed_buy",
        executed=[{"code": "601609", "name": "金田股份", "price": 11.57, "qty": None,
                   "orderId": "100015682608171233450063057410"}],
        ts="2026-08-17T15:45:21", trigger="batch",
        note=("金田股份(601609)最终买入批次: 全市场160候选E2E扫描100分通过(量比6.30满分+首板加分+趋势满分),"
              "已执行买入, orderId=100015682608171233450063057410(主人留痕);"
              "同批大唐电信(600198)亦100分但未在本批下单"),
    )

    # ---- 3) ST长园 G0 拦截记录: 19:16 t16修补后全量复扫40只 ----
    with open(os.path.join(gl, "20260817_191637_gate_result.json"), "r", encoding="utf-8") as f:
        full40 = json.load(f)
    p3 = log_decision(
        full40, action="scan_only", executed=[],
        ts="2026-08-17T19:16:37", trigger="batch",
        note=("G0-ST硬底线(t16)上线后全量复扫: ST长园(600525)由19:06批的70.7分PASS(漏洞)"
              "→本批0分REJECT(G0一票否决, 退市风险拦截); 同批ST如意/ST金鸿顺等ST股一并归零"),
    )

    print(f"重建完成: {out_path}")
    for p in (p1, p2, p3):
        print(f"  entry -> {p}")
    return out_path


# ========== CLI ==========

def _parse_executed(s: str | None) -> list:
    if not s:
        return []
    if os.path.isfile(s):
        with open(s, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(s)
    return data if isinstance(data, list) else []


def main() -> int:
    p = argparse.ArgumentParser(description="trade_decision_logger.py — 决策日志自动落盘 (t12)")
    p.add_argument("--gate-log", dest="gate_log",
                   help="既有闸门结果日志路径(转成决策日志追加落盘)")
    p.add_argument("--action", choices=list(VALID_ACTIONS), default="scan_only",
                   help="决策动作: scan_only 仅扫描 / executed_buy 已执行买入")
    p.add_argument("--executed-orders", dest="executed_orders",
                   help="已执行订单JSON(字符串或文件路径): [{code,price,qty,orderId}]")
    p.add_argument("--note", default=None, help="归因备注")
    p.add_argument("--trigger", default="manual", help="触发来源: manual/batch/single/replay")
    p.add_argument("--rebuild-today", action="store_true", dest="rebuild_today",
                   help="重建2026-08-17演示决策日志(早间批次+金田最终买入+ST长园G0拦截)")
    args = p.parse_args()

    if args.rebuild_today:
        rebuild_today()
        return 0

    if not args.gate_log:
        p.print_help()
        return 0

    with open(args.gate_log, "r", encoding="utf-8") as f:
        gate_output = json.load(f)

    path = log_decision(
        gate_output, action=args.action,
        executed=_parse_executed(args.executed_orders),
        note=args.note, trigger=args.trigger,
    )
    print(f"决策已落盘: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
