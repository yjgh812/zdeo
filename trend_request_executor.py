#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trend_request_executor.py — 走坏提请次日执行器（PD-024① 2026-10-01 主人「继续」批准落地）

语义（PD-024①裁定）: trend_checker v2.2 判定走坏成立(BEARISH)落提请台账（data/trend_request_ledger.jsonl，
唯一写入方=trend_checker，判定源不扩权）后，**次晨强制弹执行清单，未否决即自动卖出**:
  09:10 链 --list 呈现清单 + 否决指引（弹清单=强制呈现，非可选项）;
  否决窗口 = 呈现起至 09:40 链执行前，主人可 --veto 当次否决（否决权保留，销单留痕）;
  09:40 链 --execute 对未否决项走 trade_executor 唯一入口自动清仓
    （R闸全跑 + pre_sell 防线保留 + M2 同日卖单幂等; 全仓口径与 S-5/走坏清仓先例一致）。
  判定源不扩权（仍 trend_checker v2.2）; 伪封/炸板类不并入（走 PD-018 规则 v2 另一轨）。
依据: 走坏卖出17笔正确率94.1% + 双塔拖延成本-6.9% + 执行差距7.66pp主因为拖延。

状态机: data/trend_request_exec_state.jsonl（本脚本唯一写入方; 台账提请行仍归 trend_checker 所有）
  每行 {request_date, code, name, action: executed_submitted|vetoed|skipped_not_held|failed|excluded_v2b, ts, detail}
  同 (request_date, code) 幂等: 已有终态行不再处理（重复执行安全）。
  excluded_v2b(B1 2026-10-10)=非终态观察行: v2_action=hold_reclaim_check 的提请本次跳过但保持 pending,
  次晨由 0940 链做收回判定（收回→--veto --detail 放弃 / 未收回→随执行卖出=T+2开盘卖）。
  成交确认仍由既有 22:00 链 reconcile/fill_confirm 闭环（本脚本只管"提交"，不自称成交）。

用法:
  python trend_request_executor.py --list              # 呈现待执行清单（0910 链）
  python trend_request_executor.py --veto 601866 ...   # 主人当次否决（销单留痕; --detail 自定义留痕说明）
  python trend_request_executor.py --execute           # 执行未否决项（0940 链）
  python trend_request_executor.py --execute --exclude-codes 600719,603098
                                                       # 执行但排除指定码（v2_B 观察期分流, 非终态留痕）
退出码: 0=正常（含无待执行/非交易日守卫跳过） / 3=westock 真值源失败 fail-closed 不执行 / 2=部分执行失败
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "trend_request_ledger.jsonl")
STATE = os.path.join(ROOT, "data", "trend_request_exec_state.jsonl")
TRADE_EXECUTOR = os.path.join(ROOT, "trade_executor.py")
SELL_REASON = "趋势走坏(走坏次日执行,PD-024①)"


def _today() -> str:
    return time.strftime("%Y%m%d")


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _append_state(rows):
    with open(STATE, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def pending_requests():
    """待执行提请 = action=sell_request ∧ request_date<Today ∧ 无终态状态行（次晨语义）。

    终态 = executed_submitted / failed / skipped_not_held；vetoed 不算终态——
    否决项保留在 pending 中，清单显式标注、执行时显式打 SKIP-VETOED 留痕（否决必须可见）。
    同码归并: 该码已有 executed_submitted/skipped_not_held 终态时, 其余日期的旧提请视为被取代
    （一次清仓解决全部历史提请, 防僵尸条目）; failed 不取代（下轮重试）。"""
    state = _read_jsonl(STATE)
    terminal = {(str(s.get("request_date")), str(s.get("code"))) for s in state
                if s.get("action") in ("executed_submitted", "failed", "skipped_not_held",
                                       "blocked_pre_sell", "rejected_gate")}
    done_codes = {str(s.get("code")) for s in state
                  if s.get("action") in ("executed_submitted", "skipped_not_held")}
    out, seen = [], set()
    for r in _read_jsonl(LEDGER):
        if r.get("action") != "sell_request":
            continue
        key = (str(r.get("request_date")), str(r.get("code")))
        if key in terminal or key in seen:
            continue
        if str(r.get("code")) in done_codes:
            continue  # 同码已处理(成交/已不在持仓), 旧提请被取代
        if str(r.get("request_date")) >= _today():
            continue  # 当日落提请，次晨才进清单
        seen.add(key)
        out.append(r)
    return out


def _vetoed_keys():
    return {(str(s.get("request_date")), str(s.get("code")))
            for s in _read_jsonl(STATE) if s.get("action") == "vetoed"}


def _excluded_v2b_keys():
    """v2_B 观察期排除键（B1 2026-10-10, 非终态）: 被 --exclude-codes 排除过的提请。
    请求保持 pending; 消费端（0940 链）次晨按 qfq 收回判定决定放弃或执行。"""
    return {(str(s.get("request_date")), str(s.get("code")))
            for s in _read_jsonl(STATE) if s.get("action") == "excluded_v2b"}


def _load_portrait():
    """加载画像分数(呈现级辅助, 零拦截权; 10/2优化迭代)"""
    pf_path = os.path.join(ROOT, "data", "portrait_feed.json")
    try:
        pf = json.load(open(pf_path, encoding="utf-8"))
        out = {}
        for k, v in (pf.get("scores_short") or {}).items():
            if isinstance(v, dict) and "prob" in v:
                out[str(k).zfill(6)[-6:]] = (float(v["prob"]), float(v.get("pct", 0)),
                                             pf.get("snapshot_date", "?"))
        return out
    except Exception:
        return {}


def cmd_list() -> int:
    pend = pending_requests()
    if not pend:
        print("[LIST] 无待执行走坏提请")
        return 0
    pf = _load_portrait()
    vetoed = _vetoed_keys()
    excluded = _excluded_v2b_keys()
    print(f"[LIST] 待执行走坏提请 {len(pend)} 笔（次晨语义: 提请日<Today）:")
    for r in pend:
        key = (str(r.get("request_date")), str(r.get("code")))
        mark = " [已否决,执行时跳过]" if key in vetoed else ""
        if key in excluded:
            mark += " [v2_B观察中,次晨收回判定]"
        v2a = r.get("v2_action")
        if v2a:
            mark += f" [v2_action={v2a}]"
        pf_txt = ""
        pf_hit = pf.get(str(r.get("code")))
        if pf_hit:
            pf_txt = f"  画像: prob={pf_hit[0]:.4f} 前{pf_hit[1]:.0f}%"
            if pf_hit[1] >= 50:
                pf_txt += " 🟡画像看好(否决参考)"
        print(f"  {r.get('request_date')} {r.get('code')} {r.get('name', '')} {r.get('reason', '')}{mark}{pf_txt}")
    print("[VETO] 否决窗口=现在至09:40链执行前; 否决指令(任意会话): "
          "python D:\\zcodesj\\trend_request_executor.py --veto <代码>")
    print("[EXEC] 09:40 链将对未否决项走 trade_executor 唯一入口自动清仓(R闸全跑+pre_sell防线保留)")
    return 0


def cmd_veto(codes, detail=None) -> int:
    pend = pending_requests()
    by_code = {}
    for r in pend:
        by_code.setdefault(str(r.get("code")), []).append(r)
    already = _vetoed_keys()
    now = time.strftime("%F %T")
    rows = []
    for c in codes:
        c6 = "".join(ch for ch in str(c) if ch.isdigit()).zfill(6)[-6:]
        for r in by_code.get(c6, []):
            key = (str(r.get("request_date")), str(r.get("code")))
            if key in already:
                print(f"[VETO] {r.get('request_date')} {r.get('code')} {r.get('name', '')} 已否决过, 幂等跳过")
                continue
            rows.append({"request_date": r.get("request_date"), "code": r.get("code"),
                         "name": r.get("name", ""), "action": "vetoed", "ts": now,
                         "detail": detail or f"主人当次否决({c6})"})
    if not rows:
        print(f"[VETO] 无匹配的待执行提请: {list(codes)}（检查代码/是否已执行/提请日非昨日）")
        return 1
    _append_state(rows)
    for r in rows:
        print(f"[VETO] {r['request_date']} {r['code']} {r['name']} 已否决留痕")
    return 0


def _westock_held():
    """实时持仓 {code6: {name, qty}}; 真值源失败 raise（调用方 fail-closed）。"""
    if r"D:\workbuddyspace" not in sys.path:
        sys.path.insert(0, r"D:\workbuddyspace")
    from westock_client import WeStockClient
    ws = WeStockClient()
    pos = ((ws.positions().get("data") or {}).get("positions")) or []
    out = {}
    for p in pos:
        c6 = "".join(ch for ch in str(p.get("code", "")) if ch.isdigit()).zfill(6)[-6:]
        q = float(p.get("quantity") or p.get("qty") or 0)
        if c6 and q > 0:
            out[c6] = {"name": p.get("name", "") or "", "qty": q}
    return out


def cmd_execute(dry=False, exclude_codes=None) -> int:
    from trade_day_guard import is_trading_day
    today = _today()
    if not is_trading_day(today):
        print(f"[GUARD] {today} 非交易日 → 跳过执行（次晨=下一交易日 09:40 链）")
        return 0
    exclude_set = {"".join(ch for ch in str(c) if ch.isdigit()).zfill(6)[-6:]
                   for c in (exclude_codes or [])}
    pend = pending_requests()
    if not pend:
        print("[EXEC] 无待执行走坏提请")
        return 0
    try:
        held = _westock_held()
    except Exception as e:
        print(f"[FAIL-CLOSED] westock 持仓拉取失败({type(e).__name__}: {e}) → 本轮不执行任何卖出(fail-closed)")
        return 3
    now = time.strftime("%F %T")
    vetoed = _vetoed_keys()
    exit_rc = 0
    excluded_cnt = 0
    for r in pend:
        key = (str(r.get("request_date")), str(r.get("code")))
        c6, name = str(r.get("code")), str(r.get("name") or "")
        if key in vetoed:
            print(f"[SKIP-VETOED] {r.get('request_date')} {c6} {name} (主人已否决, 状态行已留痕)")
            continue
        if c6 not in held:
            print(f"[SKIP-NOT-HELD] {r.get('request_date')} {c6} {name} (不在westock实时持仓, 留痕)")
            _append_state([{"request_date": r.get("request_date"), "code": c6, "name": name,
                            "action": "skipped_not_held", "ts": now,
                            "detail": "westock实时持仓无此码(已卖/已清)"}])
            continue
        if c6 in exclude_set:
            print(f"[SKIP-EXCLUDED-V2B] {r.get('request_date')} {c6} {name} "
                  f"(v2_action=hold_reclaim_check 观察期, 非主人否决, 留痕不销单)")
            if not dry:
                _append_state([{"request_date": r.get("request_date"), "code": c6, "name": name,
                                "action": "excluded_v2b", "ts": now,
                                "detail": "v2_action=hold_reclaim_check 观察期(非主人否决)"}])
            excluded_cnt += 1
            continue
        print(f"[EXEC-SELL] {r.get('request_date')} {c6} {name} → trade_executor sell 全仓"
              f"(R闸+pre_sell防线+M2幂等)")
        if dry:
            print("  [DRY] 未提交")
            continue
        try:
            proc = subprocess.run(
                [sys.executable, TRADE_EXECUTOR, "sell", "--code", c6, "--name", name,
                 "--reason", SELL_REASON, "--execute", "--confirmed"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            lines = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()]
            tail = " | ".join(lines[-2:]) if lines else (proc.stderr or "").strip()[-160:]
            # 真状态解析(10/2彩排定谳): executor 被防线/闸门拦下时 rc=0 但 JSON executed=false——
            # 只看 rc 会把"被拦未卖"伪记成"已提交", 违反真状态原则
            # 10/8定谳补丁: rc=0 且 JSON 无 executed:true = fail-closed 拒绝(如"无有效价格拒绝盲卖")
            # 并非提交——10/8晨四票假阳性终态根因(被记成已执行,实际静默零卖出)。
            # 保守改判 failed(failed 不取代, 生成端次晨重判会出新日期提请自动重试)。
            low = (proc.stdout or "").lower()
            if '"executed": true' in low:
                action = "executed_submitted"
            elif "防卖飞拦截" in (proc.stdout or "") or "pre_sell" in low:
                action = "blocked_pre_sell"
            elif '"gate_verdict": "reject"' in low or "闸门拒绝" in (proc.stdout or ""):
                action = "rejected_gate"
            else:
                action = "failed"
            _append_state([{"request_date": r.get("request_date"), "code": c6, "name": name,
                            "action": action, "ts": now,
                            "detail": f"rc={proc.returncode}; {tail}"}])
            mark = {"executed_submitted": "✅ 已提交(成交确认走22:00链闭环)",
                    "blocked_pre_sell": "🛡️ pre_sell防线拦下未卖(防卖飞C1-C9, 留痕不重试)",
                    "rejected_gate": "⛔ 闸门拒绝(留痕)",
                    "failed": f"❌ rc={proc.returncode}"}.get(action, f"?{action}")
            print(f"  {mark} | {tail}")
            if action == "failed":
                exit_rc = 2
        except Exception as e:
            _append_state([{"request_date": r.get("request_date"), "code": c6, "name": name,
                            "action": "failed", "ts": now,
                            "detail": f"{type(e).__name__}: {e}"}])
            print(f"  ❌ 异常 {type(e).__name__}: {e}")
            exit_rc = 2
    print(f"[DONE] 待执行 {len(pend)} 笔处理完毕（其中 v2_B 观察排除 {excluded_cnt} 笔） → {STATE}")
    return exit_rc


def main() -> int:
    ap = argparse.ArgumentParser(description="走坏提请次日执行器(PD-024①, 提请=trend_checker唯一源)")
    ap.add_argument("--list", action="store_true", help="呈现待执行清单(0910链弹清单)")
    ap.add_argument("--veto", nargs="+", metavar="CODE", help="主人当次否决(代码, 可多只)")
    ap.add_argument("--detail", default=None, metavar="TEXT",
                    help="--veto 时自定义留痕说明(缺省=主人当次否决; v2_B收回放弃须如实标注)")
    ap.add_argument("--execute", action="store_true", help="执行未否决项(0940链, 自动清仓)")
    ap.add_argument("--exclude-codes", default="", metavar="CODE[,CODE...]",
                    help="本次执行排除的代码(v2_B观察期分流, 逗号分隔; 非终态, 次晨仍待判)")
    ap.add_argument("--dry-run", action="store_true", help="执行演练(不提交卖出, 测试用)")
    a = ap.parse_args()
    if a.list:
        return cmd_list()
    if a.veto:
        return cmd_veto(a.veto, detail=a.detail)
    if a.execute:
        raw = (a.exclude_codes or "").replace("，", ",").replace(" ", "")
        excl = [c for c in raw.split(",") if c]
        return cmd_execute(dry=a.dry_run, exclude_codes=excl or None)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
