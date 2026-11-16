#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""VERIFY5 影子 T+20 结算器（PREREG_VERIFY5_SHADOW_SETTLE_20261028 唯一执行器）

职责（宪法§5 单一 owner）：
  1. 按 data/PREREG_VERIFY5_SHADOW_SETTLE_20261028.json 冻结条款对五票做影子记账（零交易权，不产生任何委托）；
  2. 价格唯一源 = qfq 库（宪法§7）；入场价与 qfq 开盘偏差>0.5% 输出结构化警告（冻结价仍为准）；
  3. 快照模式（默认）：以最近已收盘 bar 输出在途状态；
  4. 终局模式（最新 bar >= exit_eval_date）：逐票 verdict + 双臂裁决(F1/F2)；
     - 回写合同 status=settled 与终局字段；
     - 触发证伪条件时按预注册条款机械翻转 falsified_rules.json 的 VERIFY5-PWIN-VS-BREAKOUT（幂等，只翻一次）；
  5. 每次运行追加 data/verify5_shadow_settle_ledger.jsonl（同日同模式去重）。

用法：python verify5_shadow_settle.py [--contract <path>] [--mode snapshot|settle]
退出码：0=正常（快照或终局），1=结构化失败（合同丢失/库不可读/条款缺字段）。
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
CONTRACT_DEFAULT = os.path.join(ROOT, "data", "PREREG_VERIFY5_SHADOW_SETTLE_20261028.json")
LATEST = os.path.join(ROOT, "data", "verify5_shadow_settle_latest.json")
LEDGER = os.path.join(ROOT, "data", "verify5_shadow_settle_ledger.jsonl")
FALSIFIED = os.path.join(ROOT, "data", "falsified_rules.json")
ENTRY_SINCE = "20261022"
STOP_RATIO = 0.92  # S-5 硬止损 -8%
ENTRY_TOL = 0.005


def fail(msg):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        fail(f"{what}不存在: {path}")
    except json.JSONDecodeError as e:
        fail(f"{what}损坏(JSONDecodeError): {path}: {e}")


def qfq_bars(codes):
    try:
        con = sqlite3.connect(QFQ_DB)
        cur = con.cursor()
        out = {}
        for code in codes:
            cur.execute(
                "select date,open,high,low,close from stock_daily_meta_qfq "
                "where code=? and date>=? order by date",
                (code, ENTRY_SINCE),
            )
            out[code] = cur.fetchall()
        con.close()
        return out
    except sqlite3.Error as e:
        fail(f"qfq库不可读: {QFQ_DB}: {e}")


def compute_stock(pos, bars, exit_date):
    entry = pos["entry_price"]
    stop_line = round(entry * STOP_RATIO, 4)
    if not bars:
        return {"code": pos["code"], "error": "qfq无该票区间数据"}
    e0 = bars[0]
    entry_dev = e0[1] / entry - 1
    stopped_on = None
    for d, _o, _h, low, _c in bars:
        if low <= stop_line:
            stopped_on = d
            break
    last = bars[-1]
    max_dd = min(b[3] for b in bars) / entry - 1
    settled = last[0] >= exit_date
    if stopped_on:
        exit_price, exit_date_used, exit_kind = stop_line, stopped_on, "hard_stop"
    elif settled:
        exit_bar = [b for b in bars if b[0] >= exit_date][0]
        exit_price, exit_date_used, exit_kind = exit_bar[4], exit_bar[0], "t20_close"
    else:
        exit_price, exit_date_used, exit_kind = last[4], last[0], "mark_to_market"
    ret = exit_price / entry - 1
    return {
        "code": pos["code"], "name": pos["name"], "arm": pos["arm"],
        "entry_price": entry, "entry_dev_qfq": round(entry_dev, 6),
        "max_dd": round(max_dd, 6),
        "stopped": stopped_on is not None, "stopped_on": stopped_on,
        "stop_line": stop_line,
        "exit_price": exit_price, "exit_date": exit_date_used, "exit_kind": exit_kind,
        "ret": round(ret, 6), "hit": ret > 0,
        "settled": settled,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", default=CONTRACT_DEFAULT)
    ap.add_argument("--mode", choices=["snapshot", "settle"], default=None,
                    help="缺省自动: 最新bar>=exit_eval_date则settle否则snapshot")
    args = ap.parse_args()

    contract = load_json(args.contract, "影子挂账合同")
    sc = contract.get("settlement_contract") or {}
    for k in ("exit_eval_date", "stop_rule", "hit_rule"):
        if not sc.get(k):
            fail(f"合同缺必填条款字段: settlement_contract.{k}")
    positions = contract.get("positions") or []
    if len(positions) != 5:
        fail(f"合同positions应为5票, 实得{len(positions)}")
    exit_date = sc["exit_eval_date"].replace("-", "")

    bars = qfq_bars([p["code"] for p in positions])
    stocks = [compute_stock(p, bars[p["code"]], exit_date) for p in positions]
    for s in stocks:
        if s.get("error"):
            fail(f"{s['code']} {s['error']}")
    warns = [{"code": s["code"], "entry_dev_qfq": s["entry_dev_qfq"]}
             for s in stocks if abs(s["entry_dev_qfq"]) > ENTRY_TOL]

    auto = "settle" if all(s["settled"] for s in stocks) else "snapshot"
    mode = args.mode or auto
    if mode == "settle" and not all(s["settled"] for s in stocks):
        fail("settle模式要求全部五票已到exit_eval_date, 当前未到, 请用snapshot")

    plan = {p["code"]: p["plan_amt"] for p in positions}
    total_amt = sum(plan.values())
    port_ret = sum(plan[s["code"]] * s["ret"] for s in stocks) / total_amt
    pwin = [s for s in stocks if s["arm"] == "pwin_rank"]
    obs = [s for s in stocks if s["arm"] == "breakout_observation"]
    pwin_mean = sum(s["ret"] for s in stocks if s["arm"] == "pwin_rank") / len(pwin)
    hits = sum(1 for s in stocks if s["hit"])
    f1 = (not obs[0]["stopped"]) and obs[0]["ret"] > pwin_mean
    f2 = sum(1 for s in pwin if s["hit"]) < 3 and obs[0]["hit"]

    result = {
        "ok": True,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "contract": os.path.basename(args.contract),
        "exit_eval_date": sc["exit_eval_date"],
        "asof_bar": max(s["exit_date"] for s in stocks),
        "stocks": stocks,
        "framework": {"hits": hits, "total": len(stocks),
                      "pass": hits >= 3, "rule": sc.get("framework_pass", "")},
        "portfolio": {"plan_amt_total": total_amt, "weighted_ret": round(port_ret, 6),
                      "weighted_pnl": round(port_ret * total_amt, 2)},
        "arms": {"pwin_rank_mean_ret": round(pwin_mean, 6),
                 "breakout_observation_ret": obs[0]["ret"],
                 "f1_obs_beats_pwin_arm": f1, "f2_pwin_fail_and_obs_hit": f2,
                 "falsified_trigger": bool(f1 or f2)},
        "entry_warnings": warns,
    }

    if mode == "settle":
        result["verdict"] = {
            "framework_pass": hits >= 3,
            "framework_revise": hits <= 2,
            "falsified_rule": "VERIFY5-PWIN-VS-BREAKOUT",
            "falsified_fired": bool(f1 or f2),
        }
        contract["status"] = "settled"
        contract["settled_at"] = result["ts"]
        contract["day20_verdict"] = result["verdict"]
        contract["final_stocks"] = stocks
        contract["final_arms"] = result["arms"]
        with open(args.contract, "w", encoding="utf-8") as fh:
            json.dump(contract, fh, ensure_ascii=False, indent=2)

        if f1 or f2:
            fl = load_json(FALSIFIED, "falsified_rules.json")
            rule = (fl.get("rules") or {}).get("VERIFY5-PWIN-VS-BREAKOUT")
            if rule is None:
                fail("falsified_rules.json 缺预注册条目 VERIFY5-PWIN-VS-BREAKOUT, 拒绝现场造规则")
            if rule.get("status") == "pending_verdict":
                rule["status"] = "falsified"
                rule["settled_at"] = result["ts"]
                rule["settle_evidence"] = {
                    "F1": f1, "F2": f2,
                    "obs_ret": obs[0]["ret"], "pwin_arm_mean_ret": round(pwin_mean, 6),
                    "obs_stopped": obs[0]["stopped"],
                    "pwin_hits": sum(1 for s in pwin if s["hit"]),
                    "contract": os.path.basename(args.contract),
                }
                prov = fl.get("provenance", "")
                mark = "VERIFY5影子T+20双臂对照"
                if mark not in prov:
                    fl["provenance"] = prov + (
                        f" | {mark}FALSIFIED登记见 rules.VERIFY5-PWIN-VS-BREAKOUT"
                        f"({datetime.now().strftime('%Y-%m-%d')}, 主人20261028「继续」批准的预注册条款机械翻转)"
                    )
                with open(FALSIFIED, "w", encoding="utf-8") as fh:
                    json.dump(fl, fh, ensure_ascii=False, indent=2)
                result["falsified_rules_updated"] = True

    with open(LATEST, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    line = json.dumps({"date": datetime.now().strftime("%Y%m%d"), "mode": mode,
                       "asof": result["asof_bar"], "hits": hits,
                       "port_ret": result["portfolio"]["weighted_ret"],
                       "trigger": bool(f1 or f2)}, ensure_ascii=False)
    prev = ""
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
            prev = lines[-1] if lines else ""
    if line != prev:
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
