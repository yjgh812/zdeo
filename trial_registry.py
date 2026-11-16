#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trial_registry.py — A-STAT 试验登记台账管理工具
================================================
管理 D:\\zcodesj\\experiment_log.json（n_trials 试验登记）。

铁律：
  - 任何策略进 DSR 门禁（dsr_gate.py）前必须先 register 登记；
  - n_trials 必须诚实申报（DSR 成立前提，少报 = 自欺）；
  - set_returns_csv 自动按 trials 矩阵 CSV 列数校验 n_trials。

与 combo_experiment_tracker.py（attribution.db combo_experiment 表）分工：
  - 本台账 = 研究期试验登记（回测阶段，含 n_trials/gate 结果，可审计）
  - combo_experiment = 上线后 A/B 实测追踪（真实持仓阶段）

用法:
  python trial_registry.py register --name droe_pit_Top50_le30 \
      --category factor_combo --n-trials 50 --purpose "droe vs droe+roa A/B" \
      [--universe "主板60/00+30/68 非ST ≤30元 qfq"] \
      [--period-start 20180102 --period-end 20260810] [--cost-bps 15] [--owner quant-researcher]
  python trial_registry.py add-trial --exp-id EXP-20260816-001 --trial-id t01 --desc "Top50 单因子 droe" --params '{"top_n":50}'
  python trial_registry.py set-returns --exp-id EXP-... --csv reports/droe_pit_Top50_le30_pit_trials.csv
  python trial_registry.py gate --exp-id EXP-... --verdict WARN --dsr-n3 0.971 --dsr-n5 0.942 \
      --pbo 0.18 --haircut 0.31 --mintrl-days 380 --n-obs 1100 --report-json reports/ASTAT_x.json
  python trial_registry.py get --exp-id EXP-...
  python trial_registry.py list [--category factor_combo]
  python trial_registry.py validate            # 全量体检；有孤儿条目 exit 1
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

EXPERIMENT_LOG = Path(r"D:\zcodesj\experiment_log.json")
DEFAULT_OWNER = "quant-researcher"

# Windows 坑 #1 修复：显式 UTF-8 输出（GBK 控制台打印 emoji 会 UnicodeEncodeError）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ═══════════════════════════════════════════════════════
#  底层读写（原子写）
# ═══════════════════════════════════════════════════════

def _load() -> dict:
    if not EXPERIMENT_LOG.exists():
        raise SystemExit(f"[A-STAT] 台账不存在: {EXPERIMENT_LOG}（请先创建空台账）")
    try:
        with open(EXPERIMENT_LOG, encoding="utf-8") as f:
            log = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[A-STAT] 台账 JSON 损坏: {e}")
    if "experiments" not in log:
        log["experiments"] = []
    return log


def _save(log: dict) -> None:
    """原子写：临时文件 + os.replace，防并发/中断损坏。"""
    fd, tmp = tempfile.mkstemp(dir=str(EXPERIMENT_LOG.parent),
                               prefix="experiment_log.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(EXPERIMENT_LOG))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _next_exp_id(log: dict) -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"EXP-{today}-"
    used = [e["exp_id"] for e in log["experiments"] if e["exp_id"].startswith(prefix)]
    n = max((int(e.rsplit("-", 1)[1]) for e in used), default=0) + 1
    return f"{prefix}{n:03d}"


def _find(log: dict, exp_id: str) -> dict:
    for e in log["experiments"]:
        if e["exp_id"] == exp_id:
            return e
    raise SystemExit(f"[A-STAT] exp_id '{exp_id}' 不存在。")


# ═══════════════════════════════════════════════════════
#  业务操作
# ═══════════════════════════════════════════════════════

def register(name: str, category: str, n_trials: int, purpose: str,
             universe: str = "", period_start: str = "", period_end: str = "",
             cost_bps: int = 15, owner: str = DEFAULT_OWNER) -> str:
    log = _load()
    exp_id = _next_exp_id(log)
    log["experiments"].append({
        "exp_id": exp_id,
        "name": name,
        "category": category,
        "owner": owner,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "universe": universe,
        "period": {"start": period_start, "end": period_end},
        "cost_bps": cost_bps,
        "purpose": purpose,
        "n_trials": int(n_trials),
        "trials": [],
        "selected_trial": None,
        "returns_csv": None,
        "gate": None,
        "status": "in_progress",
    })
    _save(log)
    print(f"[A-STAT] registered {exp_id} ({name}, n_trials={n_trials})")
    return exp_id


def add_trial(exp_id: str, trial_id: str, desc: str, params: dict | None = None) -> None:
    log = _load()
    e = _find(log, exp_id)
    e["trials"].append({"id": trial_id, "desc": desc, "params": params or {}})
    _save(log)
    print(f"[A-STAT] {exp_id} += trial {trial_id}")


def set_returns_csv(exp_id: str, csv_path: str) -> None:
    """登记 trials 矩阵路径，并按 CSV 列数自动校验 n_trials。"""
    log = _load()
    e = _find(log, exp_id)
    p = Path(csv_path)
    if not p.exists():
        raise SystemExit(f"[A-STAT] CSV 不存在: {p}")
    with open(p, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        n_cols = None
        for row in reader:
            if row and any(c.strip() for c in row):
                n_cols = len(row)
                break
    if n_cols is None:
        raise SystemExit(f"[A-STAT] CSV 为空: {p}")
    declared = int(e["n_trials"])
    e["returns_csv"] = str(p)
    e["trials_n_cols"] = n_cols
    if n_cols != declared:
        print(f"[A-STAT] ⚠️ n_trials 不一致: 申报 {declared} vs CSV 列数 {n_cols}"
              f"（DSR 将以两者较大者为基准，请核对申报）")
    else:
        print(f"[A-STAT] ✅ n_trials 校验通过: {n_cols} == 申报 {declared}")
    _save(log)


def record_gate(exp_id: str, verdict: str, dsr_n3: float | None = None,
                dsr_n5: float | None = None, pbo: float | None = None,
                haircut: float | None = None, mintrl_days: float | None = None,
                n_obs: int | None = None, report_json: str = "",
                status: str = "gated") -> None:
    log = _load()
    e = _find(log, exp_id)
    e["gate"] = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "dsr_n3": dsr_n3, "dsr_n5": dsr_n5, "pbo": pbo, "haircut": haircut,
        "mintrl_days": mintrl_days, "n_obs": n_obs,
        "report_json": report_json, "verdict": verdict,
    }
    e["status"] = status
    _save(log)
    print(f"[A-STAT] {exp_id} gate recorded: {verdict} (status={status})")


def get(exp_id: str) -> None:
    log = _load()
    e = _find(log, exp_id)
    print(json.dumps(e, ensure_ascii=False, indent=2))


def list_exps(category: str | None = None) -> None:
    log = _load()
    exps = [e for e in log["experiments"]
            if category is None or e.get("category") == category]
    if not exps:
        print("[A-STAT] （无登记）")
        return
    for e in exps:
        g = e.get("gate") or {}
        print(f"  {e['exp_id']:<22} {e['name']:<32} {e.get('category',''):<14} "
              f"n_trials={e['n_trials']:<4} status={e.get('status','?'):<12} "
              f"gate={g.get('verdict','-')}")


def validate_all() -> int:
    """全量体检：孤儿条目 / 缺 returns_csv / 未过 gate。有异常 exit 1。"""
    log = _load()
    bad = 0
    for e in log["experiments"]:
        issues = []
        if not e.get("returns_csv"):
            issues.append("缺 returns_csv")
        if not e.get("gate"):
            issues.append("未过 gate")
        if not e.get("trials"):
            issues.append("trials 为空")
        if issues:
            bad += 1
            print(f"  ⚠️ {e['exp_id']} ({e['name']}): {', '.join(issues)}")
    if bad:
        print(f"[A-STAT] validate: {bad}/{len(log['experiments'])} 条有异常")
        return 1
    print(f"[A-STAT] validate: 全绿（{len(log['experiments'])} 条）")
    return 0


# ═══════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════

def cli() -> int:
    ap = argparse.ArgumentParser(description="A-STAT 试验登记台账管理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="登记新试验")
    p.add_argument("--name", required=True)
    p.add_argument("--category", required=True,
                   choices=["factor_combo", "param_sweep", "ml_model", "rule", "timing"])
    p.add_argument("--n-trials", type=int, required=True)
    p.add_argument("--purpose", required=True)
    p.add_argument("--universe", default="")
    p.add_argument("--period-start", default="")
    p.add_argument("--period-end", default="")
    p.add_argument("--cost-bps", type=int, default=15)
    p.add_argument("--owner", default=DEFAULT_OWNER)

    p = sub.add_parser("add-trial", help="追加配置轨道")
    p.add_argument("--exp-id", required=True)
    p.add_argument("--trial-id", required=True)
    p.add_argument("--desc", required=True)
    p.add_argument("--params", default="{}")

    p = sub.add_parser("set-returns", help="登记 trials 矩阵 CSV（自动校验 n_trials）")
    p.add_argument("--exp-id", required=True)
    p.add_argument("--csv", required=True)

    p = sub.add_parser("gate", help="登记门禁结果")
    p.add_argument("--exp-id", required=True)
    p.add_argument("--verdict", required=True, choices=["PASS", "WARN", "FAIL"])
    p.add_argument("--dsr-n3", type=float)
    p.add_argument("--dsr-n5", type=float)
    p.add_argument("--pbo", type=float)
    p.add_argument("--haircut", type=float)
    p.add_argument("--mintrl-days", type=float)
    p.add_argument("--n-obs", type=int)
    p.add_argument("--report-json", default="")
    p.add_argument("--status", default="gated")

    p = sub.add_parser("get")
    p.add_argument("--exp-id", required=True)

    p = sub.add_parser("list")
    p.add_argument("--category", default=None)

    p = sub.add_parser("validate")

    a = ap.parse_args()
    if a.cmd == "register":
        register(a.name, a.category, a.n_trials, a.purpose, a.universe,
                 a.period_start, a.period_end, a.cost_bps, a.owner)
    elif a.cmd == "add-trial":
        params = json.loads(a.params) if a.params else {}
        add_trial(a.exp_id, a.trial_id, a.desc, params)
    elif a.cmd == "set-returns":
        set_returns_csv(a.exp_id, a.csv)
    elif a.cmd == "gate":
        record_gate(a.exp_id, a.verdict, a.dsr_n3, a.dsr_n5, a.pbo, a.haircut,
                    a.mintrl_days, a.n_obs, a.report_json, a.status)
    elif a.cmd == "get":
        get(a.exp_id)
    elif a.cmd == "list":
        list_exps(a.category)
    elif a.cmd == "validate":
        return validate_all()
    return 0


if __name__ == "__main__":
    sys.exit(cli())
