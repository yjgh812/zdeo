#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屠龙系统·闸门全链路交易执行器 v1.0（8/13全维度自省 S-1 + M-5）

========== 核心设计 ==========
所有买入/卖出操作必须经过此执行器，不可绕过。
它是屠龙系统的"唯一交易入口"，内置完整的闸门链：

买入链路（5步）:
  Step 0: m7分析报告校验(t21, --verify-report携带即强制, 报告违规=物理拒绝)
  Step 1: trade_time_guard(时间窗口)
  Step 2: pre_trade_check_v2(R1-R18闸门全链)
  Step 2.5: m5_impulse_blocker(M-5冲动拦截)
  Step 3: trade_state(状态更新)
  Step 4: westock MCP(下单)

卖出链路（5步）:
  Step 0: m7分析报告校验(t21, 同上; 紧急止损可无报告, 显式降级留痕)
  Step 1: trade_time_guard(S-2尾盘窗口)
  Step 2: pre_sell_check(C1-C4防卖飞)
  Step 2.5: m5_impulse_blocker(M-5冲动拦截)
  Step 3: trade_state(状态更新)
  Step 4: westock MCP(下单)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

REPORT_DIR = r"D:\zcodesj\reports"
PRE_TRADE_CHECK = r"D:\zcodesj\pre_trade_check_v2.py"
PRE_SELL_CHECK = r"D:\zcodesj\pre_sell_check.py"
TRADE_STATE = r"D:\zcodesj\trade_state.py"
TIME_GUARD = r"D:\zcodesj\trade_time_guard.py"
IMPULSE_BLOCKER = r"D:\zcodesj\m5_impulse_blocker.py"
VERIFY_AGENT_OUTPUT = r"D:\zcodesj\verify_agent_output.py"  # t21: m7分析报告校验器
SIGNATURE_TIMEOUT_MINUTES = 30  # t9: 签名有效期30分钟（防重放攻击）
EXECUTOR_LOG_DIR = r"D:\zcodesj\.executor_logs"


def _tencent_snapshot(code: str) -> dict | None:
    """腾讯实时快照: 现价/昨收/最高/最低。失败返回None（fail-open由调用方显式标注）。
    非交易时段返回最近收盘快照，R6/R7按最后收盘判定属合理近似。"""
    import urllib.request
    ts = ("sh" if code.startswith(("6", "9")) else
          "bj" if code.startswith(("4", "8")) else "sz") + code
    try:
        req = urllib.request.Request(
            f"https://qt.gtimg.cn/q={ts}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
        f = raw.split("=")[1].strip('"').split("~")
        snap = {"price": float(f[3] or 0), "prev_close": float(f[4] or 0),
                "high": float(f[33] or 0), "low": float(f[34] or 0)}
        return snap if snap["price"] > 0 and snap["prev_close"] > 0 else None
    except Exception:
        return None


def _tencent_index_pct() -> dict | None:
    """腾讯指数快照: 上证(sh000001)/创业板指(sz399006) 当日涨跌幅%%。
    R25大盘择时(买入)与C9系统风险日冷静期(卖出)共用。失败返回None（调用方显式标注跳过）。
    2026-08-24 P0修复新增: 此前guarded两条链均不取指数，R25/C9在guarded路径恒不生效。"""
    import urllib.request
    out: dict = {}
    try:
        req = urllib.request.Request(
            "https://qt.gtimg.cn/q=sh000001,sz399006",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
        for chunk in raw.split(";"):
            if "=" not in chunk:
                continue
            f = chunk.split("=")[1].strip('"').split("~")
            if len(f) < 33:
                continue
            sym = chunk.split("=")[0].split("_")[-1].lower()
            pct = f[32]
            try:
                v = float(pct)
            except (TypeError, ValueError):
                continue
            if "000001" in sym and sym.startswith("sh"):
                out["sh"] = v
            elif "399006" in sym:
                out["cyb"] = v
    except Exception:
        return None
    return out or None


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


# ---- t31: G12实验源分级降仓的executor强制落地 ----
# 2026-08-24 枚举收敛配套: 真源=_common.py(经strategy_gate透传)。导入失败不再用
# 本地副本（已三度漂移: bp_ep缝隙/full_market缺失/dip_mode不一致），改空集fail-closed
# ——空集=所有source按非PROVEN处理（全部降半仓+计入实验暴露20%熔断），方向安全。
try:
    from strategy_gate import PROVEN_EDGE_SOURCES as _PROVEN_SOURCES
except Exception as _e:
    _PROVEN_SOURCES = frozenset()
    print("  ⚠️ 无法从strategy_gate导入PROVEN_EDGE_SOURCES(%s) → 空集降级: "
          "全部source按实验源处理(降半仓+20%%熔断), 不会误放行" % _e, file=sys.stderr)

MAX_EXPERIMENT_DAILY_RATIO = 0.20  # t31: 当日实验源买入合计≤总资产20%(卫星上限惯例)


def _experimental_position_exposure() -> tuple[float, list[str]]:
    """t33: 实验源持仓暴露台账(买卖对账近似口径)。

    扫 .executor_logs 全部执行日志: 买入累计数量/成本, 卖出按数量核减(成本同比),
    status 含'REJECT'的失败执行排除。剩余仓位中 source∉PROVEN 的成本合计=暴露值。
    已知局限(注释声明): 台账为近似——手工单/模拟盘部分成交/外部卖出不在此对账,
    正式持仓以westock MCP实时为准(数据真相源铁律); 未来接MCP时替换台账源。
    返回: (暴露金额, 明细行列表)。异常/无日志→(0.0, [])。"""
    ledger: dict[str, dict] = {}
    try:
        files = sorted(f for f in os.listdir(EXECUTOR_LOG_DIR)
                       if f.endswith("_execution.json"))
    except OSError:
        return 0.0, []
    for fn in files:
        try:
            with open(os.path.join(EXECUTOR_LOG_DIR, fn), encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        status = str(rec.get("status", ""))
        if "REJECT" in status.upper():
            continue
        code = str(rec.get("code") or "")
        if not code:
            continue
        src = rec.get("source") or ""
        ent = ledger.setdefault(code, {"qty": 0.0, "cost": 0.0, "source": src,
                                       "name": rec.get("name", "")})
        if src and src not in _PROVEN_SOURCES:
            ent["source"] = src  # 实验源标记以最近一次买入为准
        if rec.get("action") == "buy":
            amt, px, qty = rec.get("amount"), rec.get("price"), rec.get("quantity")
            try:
                if amt and px and float(px) > 0:
                    q = float(amt) / float(px)
                elif qty:
                    q, amt = float(qty), float(qty) * (float(px) if px else 0.0)
                else:
                    continue
            except (TypeError, ValueError):
                continue
            ent["qty"] += q
            ent["cost"] += float(amt or 0.0)
        elif rec.get("action") == "sell":
            qty = rec.get("quantity")
            try:
                q = float(qty) if qty else 0.0
            except (TypeError, ValueError):
                q = 0.0
            if q > 0 and ent["qty"] > 0:
                ratio = min(1.0, q / ent["qty"])
                ent["qty"] -= ent["qty"] * ratio
                ent["cost"] -= ent["cost"] * ratio
    exposure = 0.0
    detail = []
    for code, ent in ledger.items():
        if ent["qty"] > 0 and ent["source"] and ent["source"] not in _PROVEN_SOURCES:
            exposure += ent["cost"]
            detail.append(f"{ent['name'] or code}{code} src={ent['source']} "
                          f"qty≈{ent['qty']:.0f} cost≈{ent['cost']:.0f}")
    return exposure, detail


def apply_g12_half_position(args) -> None:
    """t31 G12①: 签名携带amount_adjusted(闸门对非PROVEN源×0.5)→物理强制降额。

    主人8/17裁决"降半仓不否决"——闸门只标记, executor是强制落地点:
    amount与quantity同步降至一半(整手向下取整), 防上游绕过。"""
    sig = getattr(args, "_sig_data", {}) or {}
    adjusted = sig.get("amount_adjusted")
    if not adjusted or not getattr(args, "amount", None):
        return
    if args.amount <= float(adjusted):
        return  # 已低于降额线, 无需调整
    old_amt, old_qty = args.amount, args.quantity
    args.amount = float(adjusted)
    if args.quantity and args.price:
        # 整手向下取整(100股/手)
        args.quantity = max(0, int(args.amount / args.price / 100)) * 100
        args.amount = args.quantity * args.price
    print(f"  ⚠️ G12实验源降半仓强制执行: {old_amt:.0f}→{args.amount:.0f}"
          + (f" (qty {old_qty}→{args.quantity})" if old_qty else "")
          + f" [sig.amount_adjusted={adjusted:.0f}, 主人8/17裁决]")


def check_g12_experimental_cap(args) -> bool:
    """t31 G12② + t33持仓级溯源: 实验源占用 ≤ 总资产×20% → 拒绝超限。

    t31(当日口径): .executor_logs 当日执行日志中 source∉PROVEN 的 buy 合计+本笔。
    t33(持仓口径): 买卖台账对账得实验源剩余持仓成本(_experimental_position_exposure,
    近似口径——手工单/外部卖出不在账, 正式以MCP持仓为准, 未来接MCP替换)。
    两口径合计 vs 0.2×total_assets。--total-assets缺失→fail-open打印明细。
    返回: True=通过/跳过, False=超限拒绝。"""
    source = getattr(args, "source", "") or ""
    if source in _PROVEN_SOURCES:
        return True  # PROVEN源不占实验额度
    amount = getattr(args, "amount", None) or 0.0
    try:
        logs = [p for p in os.listdir(EXECUTOR_LOG_DIR)
                if p.startswith(_today()) and p.endswith("_execution.json")]
    except OSError:
        logs = []
    exp_today = 0.0
    for fn in logs:
        try:
            with open(os.path.join(EXECUTOR_LOG_DIR, fn), encoding="utf-8") as f:
                rec = json.load(f)
            if (rec.get("action") == "buy"
                    and (rec.get("source") or "") not in _PROVEN_SOURCES
                    and "REJECT" not in str(rec.get("status", "")).upper()):
                exp_today += float(rec.get("amount") or 0.0)
        except Exception:
            continue
    exp_pos, pos_detail = _experimental_position_exposure()
    total_assets = getattr(args, "total_assets", None)
    if not total_assets:
        print(f"  ⚠️ G12②: 缺--total-assets无法校验20%上限(fail-open), "
              f"当日实验源已买入{exp_today:.0f}+本笔{amount:.0f}"
              f"+实验源持仓台账≈{exp_pos:.0f}")
        return True
    new_total = exp_today + float(amount) + exp_pos
    cap = float(total_assets) * MAX_EXPERIMENT_DAILY_RATIO
    if new_total > cap:
        print(f"  ❌ G12②: 实验源合计{new_total:.0f}(当日新增{exp_today:.0f}"
              f"+本笔{amount:.0f}+持仓台账{exp_pos:.0f}) > 总资产20%上限{cap:.0f}"
              f" → 拒绝执行(实验源卫星上限, 主人8/17裁决+t33持仓溯源)")
        for line in pos_detail[:5]:
            print(f"     · {line}")
        return False
    print(f"  ✅ G12②: 实验源合计{new_total:.0f}(含持仓台账{exp_pos:.0f}) ≤ 上限{cap:.0f}")
    return True


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def validate_signature(sig_path: str, args) -> dict | None:
    """加载并校验 strategy_gate.py 输出签名（t9 执行审计官）。

    校验项：
      1. 文件存在且为合法 JSON
      2. timestamp（或文件 mtime）在 30 分钟内
      3. 含 code/verdict，verdict 必须为 PASS
      4. code 与 --code 一致
      5. 提取 gate_results 摘要

    返回：通过 → 签名字典；失败 → None（已打印错误原因）
    """
    if not sig_path or not os.path.exists(sig_path):
        print(f"  ❌ 签名文件不存在: {sig_path}", file=sys.stderr)
        return None

    try:
        with open(sig_path, "r", encoding="utf-8") as f:
            sig = json.load(f)
    except Exception as e:
        print(f"  ❌ 签名文件解析失败: {e}", file=sys.stderr)
        return None

    # ---- 1. Timestamp 校验 ----
    ts_str = sig.get("timestamp")
    if ts_str:
        try:
            sig_ts = datetime.fromisoformat(ts_str)
        except Exception:
            print(f"  ❌ 签名 timestamp 格式无效: {ts_str}", file=sys.stderr)
            return None
    else:
        sig_ts = datetime.fromtimestamp(os.path.getmtime(sig_path))
        print(f"  ⚠️ 签名无 timestamp 字段，回退至文件 mtime: {sig_ts.isoformat()}")

    now = datetime.now()
    delta = (now - sig_ts).total_seconds()
    if delta < 0:
        print(f"  ❌ 签名 timestamp 在未来: {sig_ts.isoformat()}（当前={now.isoformat()}）",
              file=sys.stderr)
        return None
    if delta > SIGNATURE_TIMEOUT_MINUTES * 60:
        print(f"  ❌ 签名已过期: {delta/60:.0f} 分钟前（最大 {SIGNATURE_TIMEOUT_MINUTES} 分钟）",
              file=sys.stderr)
        return None
    print(f"  ✅ 签名时间戳: {delta/60:.1f} 分钟前（有效期 {SIGNATURE_TIMEOUT_MINUTES} 分钟内）")

    # ---- 2. 提取 result（兼容批量/单股/回放多种输出格式）----
    result = sig
    if "result" in sig:
        result = sig["result"]
    elif "results" in sig:
        pass_list = [r for r in sig["results"] if r.get("verdict") == "PASS"]
        if not pass_list:
            print(f"  ❌ 批量签名中无 PASS 结果（共 {len(sig['results'])} 个全部被拒）",
                  file=sys.stderr)
            return None
        result = pass_list[0]
        print(f"  ℹ️  批量签名: 取首个 PASS 结果 {result.get('code')}")

    # ---- 3. Verdict 必须 PASS ----
    verdict = result.get("verdict")
    if verdict != "PASS":
        print(f"  ❌ 签名 verdict={verdict}（需要 PASS）", file=sys.stderr)
        if result.get("rejected_gates"):
            print(f"     被拒闸门: {result['rejected_gates']}", file=sys.stderr)
        return None

    # ---- 4. Code 一致性 ----
    sig_code = result.get("code")
    if sig_code and sig_code != args.code:
        print(f"  ❌ 签名 code={sig_code} 与 --code={args.code} 不匹配", file=sys.stderr)
        return None

    # ---- 5. Price/Amount 交叉核对（偏差 >1% 仅警告，不阻断）----
    sig_price = result.get("price")
    if args.price and sig_price and abs(args.price - sig_price) / max(sig_price, 0.01) > 0.01:
        print(f"  ⚠️  签名 price={sig_price} 与 --price={args.price} 偏差 >1%")

    # ---- 6. Gate 摘要 ----
    gp = result.get("gates_passed", 0)
    gc = result.get("gate_count", 0)
    rg = result.get("rejected_gates", [])
    print(f"  ✅ 闸门摘要: {gp}/{gc} 通过, verdict=PASS"
          + (f", 拒绝闸门(日志追溯): {rg}" if rg else ""))

    return result


def run_m7_verify(args) -> dict:
    """t21: m7 分析报告校验关卡（m5执行审计官）。

    把 verify_agent_output.py 的 PASS 变成下单前置物理条件：
      - 提供了 --verify-report → subprocess 调用 m7校验器，
        exit code 非0（REJECTED/数据包错误/超时/异常）= 物理拒绝，executor exit 1
      - 未提供 --verify-report → 打印警告但不阻断（向后兼容：
        紧急止损等场景可能无分析报告；跳过属显式降级并留痕于执行日志）

    返回: {"status": "PASS"/"REJECTED"/"SKIPPED", "detail": str, ...}
    """
    report = getattr(args, "verify_report", None)
    package = getattr(args, "verify_package", None)

    if not report:
        print("  ⚠️ 未提供分析报告校验，跳过m7关卡（建议始终携带）")
        return {"status": "SKIPPED",
                "detail": "未提供 --verify-report，m7关卡跳过（向后兼容紧急场景）"}

    if not package:
        print("  ❌ 提供了 --verify-report 但缺少 --verify-package，m7校验无法执行",
              file=sys.stderr)
        return {"status": "REJECTED", "returncode": None,
                "detail": "缺少 --verify-package（m7校验必须基于数据包核对论据）"}

    if not os.path.exists(VERIFY_AGENT_OUTPUT):
        print(f"  ❌ m7校验器不存在: {VERIFY_AGENT_OUTPUT}", file=sys.stderr)
        return {"status": "REJECTED", "returncode": None,
                "detail": f"m7校验器缺失: {VERIFY_AGENT_OUTPUT}"}
    if not os.path.exists(report):
        print(f"  ❌ 分析报告不存在: {report}", file=sys.stderr)
        return {"status": "REJECTED", "returncode": None,
                "detail": f"分析报告不存在: {report}"}

    cmd = [sys.executable, VERIFY_AGENT_OUTPUT,
           "--report", report, "--package", package, "--no-log"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        print("  ❌ m7校验超时(60s)，按拒绝处理（fail-closed）", file=sys.stderr)
        return {"status": "REJECTED", "returncode": None,
                "detail": "m7校验超时(60s)，fail-closed拒绝"}
    except Exception as e:
        print(f"  ❌ m7校验调用异常: {e}（fail-closed）", file=sys.stderr)
        return {"status": "REJECTED", "returncode": None,
                "detail": f"m7校验调用异常: {e}"}

    for line in (r.stdout or "").strip().splitlines()[-3:]:
        print(f"  │ {line}")

    if r.returncode == 0:
        print(f"  ✅ m7关卡通过: 分析报告论据核对PASS（报告={report}）")
        return {"status": "PASS", "returncode": 0,
                "detail": "verify_agent_output PASS",
                "report": report, "package": package}

    print(f"  ❌ m7关卡拒绝: verify_agent_output exit={r.returncode}（报告未过校验）",
          file=sys.stderr)
    return {"status": "REJECTED", "returncode": r.returncode,
            "detail": "verify_agent_output 非0退出（报告违规/数据包错误），物理拒绝",
            "report": report, "package": package}


def _write_exec_log(args, action: str, order_path: str, status: str = "DEMO") -> str:
    """t9: 执行完成后写入 .executor_logs/YYYYMMDD_HHMMSS_execution.json"""
    os.makedirs(EXECUTOR_LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sig = getattr(args, "_sig_data", {})
    log = {
        "execution_id": ts,
        "action": action,
        "code": args.code,
        "name": args.name or "",
        "price": args.price,
        "quantity": args.quantity,
        "amount": args.amount,
        "source": args.source or "",
        "signature_file": args.signature,
        "signature_verdict": sig.get("verdict", "UNKNOWN"),
        "gate_summary": {
            "gates_passed": sig.get("gates_passed", 0),
            "gate_count": sig.get("gate_count", 0),
            "rejected_gates": sig.get("rejected_gates", []),
        },
        "order_path": order_path,
        # ---- t21: m7 分析报告校验结果留痕 ----
        "m7_verify": getattr(args, "_m7_verify", None) or {"status": "NOT_RUN"},
        "status": status,  # t9: 演示模式，不向券商下达真实委托; t21: M7_REJECTED=报告校验拒绝
        "timestamp": _now_str(),
    }
    filename = f"{ts}_execution.json"
    filepath = os.path.join(EXECUTOR_LOG_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"  📋 执行日志已写入: {filepath}")
    return filepath


def run_check(script: str, args: list[str]) -> tuple[int, dict]:
    """运行闸门检查脚本。"""
    if not os.path.exists(script):
        return 2, {"error": f"闸门脚本不存在: {script}", "verdict": "HARD_REJECT"}

    try:
        r = subprocess.run(
            [sys.executable, script] + args,
            capture_output=True, text=True, timeout=30,
        )
        stdout = r.stdout.strip()
        json_start = stdout.find("{")
        json_end = stdout.rfind("}")
        if json_start >= 0 and json_end > json_start:
            result = json.loads(stdout[json_start:json_end + 1])
        else:
            result = {"raw_output": stdout[:500], "verdict": "UNKNOWN"}
        return r.returncode, result
    except subprocess.TimeoutExpired:
        return 2, {"error": "闸门超时(30s)", "verdict": "HARD_REJECT"}
    except json.JSONDecodeError as e:
        return 2, {"error": f"JSON解析失败: {e}", "verdict": "HARD_REJECT"}
    except Exception as e:
        return 2, {"error": str(e), "verdict": "HARD_REJECT"}


def exec_buy(args) -> int:
    """买入5步链。"""
    code = args.code
    name = args.name or ""
    price = args.price
    amount = args.amount
    source = args.source or "manual"

    # ---- t31: G12实验源两项强制(签名降额落地+当日合计20%熔断) ----
    apply_g12_half_position(args)
    if not check_g12_experimental_cap(args):
        _write_exec_log(args, "buy", "", status="G12_EXP_CAP_REJECTED")
        return 1
    amount = args.amount  # 半仓强制后可能已降额

    # Step 1: 时间窗口检查（收盘后/午休拦截，交易时段全放行）
    print(f"\n🔐 Step 1/5: 时间窗口检查...")
    _, tw_result = run_check(TIME_GUARD, ["check-buy"])
    tw_v = tw_result.get("verdict", "HARD_REJECT")
    if tw_v == "HARD_REJECT":
        print(f"  ❌ 提示: {tw_result.get('reason', '')}")
        return 1
    elif tw_v == "SOFT_REJECT":
        print(f"  ⚠️ 时间提示: {tw_result.get('reason', '')}")
    else:
        print(f"  ✅ 时间窗口通过")

    # Step 2: R1-R18 闸门全链
    print(f"\n🔐 Step 2/5: R1-R18 闸门全链检查...")
    check_args = [
        "--code", code, "--name", name,
        "--price", str(price) if price else "",
        "--source", source, "--amount", str(amount) if amount else "",
    ]
    if args.holdings:
        check_args += ["--holdings", str(args.holdings)]
    if args.total_cash:
        check_args += ["--total-cash", str(args.total_cash)]
    if args.pct_60d is not None:
        check_args += ["--pct-60d", str(args.pct_60d)]
    # ---- R24板块高潮禁追转发（2026-08-24 P0修复: guarded买入链此前不转发--sector，
    # pre_trade_check_v2 恒 no_sector → R24 fail-open静默失效；对照trade_executor同日已修）----
    if getattr(args, "sector", ""):
        check_args += ["--sector", str(args.sector)]
    # ---- R25大盘择时转发（2026-08-24 P0修复: 自动取上证/创业板涨跌幅，与trade_executor对齐）----
    _idx = _tencent_index_pct()
    if _idx:
        if _idx.get("sh") is not None:
            check_args += ["--sh-index-pct", str(_idx["sh"])]
        if _idx.get("cyb") is not None:
            check_args += ["--cyb-index-pct", str(_idx["cyb"])]
        print(f"  📡 指数快照: 上证{_idx.get('sh', 'NA'):+.2f}% 创业板{_idx.get('cyb', 'NA'):+.2f}%（R25已激活）"
              if _idx.get("sh") is not None else f"  📡 指数快照: {_idx}（R25已激活）")
    else:
        print("  ⚠️ 指数快照获取失败: R25大盘择时将跳过（fail-open显式标注）")
    # ---- R23 持仓加仓显式确认（8/20 TCL科技裸奔加仓案加固）----
    if args.allow_add_position:
        check_args += ["--allow-add-position"]
    # [2026-08-24 P0修复-已删除] R19-R21 转发段：--hammer-bottom/--hammer-score/--v-reversal/
    # --v-reversal-score/--halt-resume/--post-halt-boards/--post-halt-turnover 均为
    # pre_trade_check_v2 于 8/14 v2.1 已移除的旧参数，转发必致闸门 argparse exit=2、
    # stdout 无 JSON、verdict=UNKNOWN——而旧放行逻辑把 UNKNOWN 当"通过"（fail-open 伪放行）。

    # R6/R7盘口数据自动补全（2026-08-16全量巡检P0修复：executor此前不传
    # --prev-close/--close/--high/--low，导致R6收在高点、R7超买追高两条硬拦截静默跳过）
    snap = _tencent_snapshot(code)
    if snap:
        check_args += ["--prev-close", str(snap["prev_close"])]
        if snap["high"] > snap["low"]:
            check_args += ["--close", str(snap["price"]),
                           "--high", str(snap["high"]), "--low", str(snap["low"])]
        print(f"  📡 盘口快照已补全: 昨收{snap['prev_close']} 现{snap['price']} "
              f"高{snap['high']} 低{snap['low']}（R6/R7已激活）")
    else:
        print(f"  ⚠️ 盘口快照拉取失败: R6/R7缺当日数据将跳过（fail-open显式标注）")

    gate_rc, gate_result = run_check(PRE_TRADE_CHECK, check_args)
    g_v = gate_result.get("verdict", "")
    rejects = gate_result.get("rejects", [])
    warnings = gate_result.get("warnings", [])
    for r in rejects:
        print(f"  ❌ 拒: {r}")
    for w in warnings:
        print(f"  ⚠️ 警: {w}")

    # 2026-08-24 P0修复: fail-closed — 闸门崩溃/超时/参数不识别/输出无JSON(rc≠0 或
    # verdict∉{PASS,PASS_WITH_WARN})一律拒绝下单，禁止 UNKNOWN/HARD_REJECT 以外的
    # 异常态被当"通过"放行（旧逻辑只挡字面 REJECT）。
    if g_v == "REJECT" or gate_rc != 0 or g_v not in ("PASS", "PASS_WITH_WARN"):
        err = gate_result.get("error") or gate_result.get("raw_output", "")
        print(f"\n⛔ 闸门拒绝 (verdict={g_v or '无输出'}, rc={gate_rc})")
        if err:
            print(f"  ❌ 闸门异常: {str(err)[:300]}")
        return 1
    print(f"  ✅ 闸门全链通过 ({g_v})")

    # Step 2.5: 冲动拦截 M-5
    print(f"\n🔐 Step 2.5/5: 冲动拦截检查...")
    _, impulse_result = run_check(IMPULSE_BLOCKER, [
        "--action", "buy", "--code", code,
        "--price", str(price) if price else "",
        "--source", source, "--skip-cooldown",
    ])
    i_v = impulse_result.get("verdict", "PASS")
    i_reasons = impulse_result.get("reasons", [])
    i_warnings = impulse_result.get("warnings", [])
    for r in i_reasons:
        print(f"  ❌ {r}")
    for w in i_warnings:
        print(f"  ⚠️ {w}")
    if i_v != "PASS":
        print(f"\n⛔ 冲动拦截触发 ({len(i_reasons)} 条)")
        return 1
    print(f"  ✅ 冲动拦截通过")

    # Step 3: trade_state
    print(f"\n📝 Step 3/5: 更新交易状态...")
    subprocess.run(
        [sys.executable, TRADE_STATE, "--record-buy", code],
        capture_output=True, text=True, timeout=15,
    )
    print(f"  ✅ trade_state 已更新")

    # Step 4: 下单
    print(f"\n🚀 Step 4/5: 执行买入...")
    print(f"  标的: {name}({code}) | 价格: {price} | 金额: {amount}")

    order = {
        "action": "BUY", "code": code, "name": name,
        "price": price, "amount": amount,
        "timestamp": _now_str(),
        "guard_verdict": g_v,
        "guard_rejects": rejects,
        "guard_warnings": warnings,
        "time_window": tw_result,
        "impulse_check": impulse_result,
    }

    orders_dir = os.path.join(REPORT_DIR, "pending_orders")
    os.makedirs(orders_dir, exist_ok=True)
    order_path = os.path.join(orders_dir, f"buy_{code}_{_today()}.json")
    with open(order_path, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)

    print(f"  ✅ 买入指令已生成: {order_path}")

    # ---- t9: 执行日志落盘 ----
    _write_exec_log(args, "buy", order_path)

    print(f"\n🎯 交易执行成功")
    return 0


def exec_sell(args) -> int:
    """卖出5步链。"""
    code = args.code
    name = args.name or ""
    quantity = args.quantity
    loss_pct = args.loss_pct
    owner_emergency = args.owner_emergency
    source = getattr(args, "source", None) or "manual_confirmed"

    # Step 1: 时间窗口检查（收盘后拦截；主人8/14裁决任何时间可卖）
    print(f"\n🔐 Step 1/5: 时间窗口检查...")
    sw_args = ["check-sell"]
    if loss_pct is not None:
        sw_args += ["--loss-pct", str(loss_pct)]
    if owner_emergency:
        sw_args += ["--emergency"]
    _, tw_result = run_check(TIME_GUARD, sw_args)
    tw_v = tw_result.get("verdict", "HARD_REJECT")

    if tw_v == "HARD_REJECT":
        print(f"  ❌ 时间拦截: {tw_result.get('reason', '')}")
        return 1
    elif tw_v == "SOFT_REJECT":
        if tw_result.get("is_stop_loss_emergency"):
            print(f"  ⚠️ 止损紧急通道启用")
        else:
            print(f"  ⚠️ 时间提示: {tw_result.get('reason', '')}")
    else:
        print(f"  ✅ 时间窗口通过")

    # Step 2: pre_sell_check 防卖飞（五检查含C5趋势验证）
    print(f"\n🔐 Step 2/5: 防卖飞五检查（C1-C5，C5趋势自动核验）...")

    # 自动获取趋势数据（trend_checker v2.0, YTD禁用铁律）
    trend_ma5 = trend_ma20 = None
    try:
        import re as _re
        tr_run = subprocess.run(
            [sys.executable, r"D:\zcodesj\trend_checker.py", "--code", code],
            capture_output=True, text=True, timeout=30,
        )
        tr_out = tr_run.stdout
        m5 = _re.search(r"MA5=([\d.]+)", tr_out)
        m20 = _re.search(r"MA20=([\d.]+)", tr_out)
        if m5:
            trend_ma5 = float(m5.group(1))
        if m20:
            trend_ma20 = float(m20.group(1))
        print(f"  📈 趋势自动核验: MA5={trend_ma5} MA20={trend_ma20}（C5防YTD误判）")
    except Exception as e:
        print(f"  ⚠️ 趋势获取失败: {e}（C5跳过，其余检查继续）")

    # ---- C8 高位退潮自动数据（2026-08-23复盘线索：8/21 13家跌停全为高位/断板股）----
    # 连板数取自归因库当日涨停记录；20日涨幅取自生产qfq库。纯提示性(risk_hints)，
    # 任一数据源失败静默跳过，绝不阻断卖出流程。
    boards = chg_20d = None
    try:
        import sqlite3 as _sq
        _ad = _sq.connect(r"D:\zcodesj\attribution\attribution.db", timeout=5)
        _row = _ad.execute(
            "SELECT limit_count FROM limit_up_patterns WHERE trade_date=? AND code=? "
            "ORDER BY id DESC LIMIT 1", (_today(), code)).fetchone()
        _ad.close()
        if _row:
            boards = int(_row[0])
    except Exception:
        boards = None
    try:
        import sqlite3 as _sq
        _qfq = _sq.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro",
            uri=True, timeout=10)
        _rows = _qfq.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? "
            "ORDER BY date DESC LIMIT 22", (code,)).fetchall()
        _qfq.close()
        if len(_rows) >= 21 and float(_rows[-1][0]) > 0:
            chg_20d = (float(_rows[0][0]) / float(_rows[-1][0]) - 1) * 100
    except Exception:
        chg_20d = None

    sc_args = ["--code", code, "--name", name]
    if args.price:
        sc_args += ["--price", str(args.price)]
    if trend_ma5 is not None:
        sc_args += ["--trend-ma5", str(trend_ma5)]
    if trend_ma20 is not None:
        sc_args += ["--trend-ma20", str(trend_ma20)]
    if loss_pct is not None:
        sc_args += ["--loss-pct", str(loss_pct)]
    if owner_emergency:
        sc_args += ["--owner-emergency"]
    # ---- C5 复牌顶部参数转发 ----
    if args.halt_resume:
        sc_args += ["--halt-resume"]
    if args.post_halt_boards is not None:
        sc_args += ["--post-halt-boards", str(args.post_halt_boards)]
    if args.post_halt_turnover is not None:
        sc_args += ["--post-halt-turnover", str(args.post_halt_turnover)]
    # ---- C8 高位退潮参数转发（自动计算） ----
    if boards is not None:
        sc_args += ["--boards", str(boards)]
    if chg_20d is not None:
        sc_args += ["--chg-20d", f"{chg_20d:.1f}"]
    # ---- C7-R28 持有期标签自动补全（2026-08-24 分层守卫，失败不阻断）----
    try:
        from holding_horizon import query_position
        _pos = query_position(code)
        if _pos["found"]:
            if _pos["hold_days"] is not None:
                sc_args += ["--hold-days", str(_pos["hold_days"])]
            if _pos["horizon"] in ("long", "short"):
                sc_args += ["--horizon", _pos["horizon"]]
            print(f"  🏷️ 持有周期: {_pos['horizon']} 持有{_pos['hold_days']}天（{_pos['source']} {str(_pos['buy_date'])}买入）")
    except Exception:
        pass
    # ---- C9 大盘涨跌幅（2026-08-24 P1修复: 此前仅调用方显式传入才转发，生产卖出链
    # 默认不传 → 系统风险日冷静期在guarded路径永不触发；现自动取腾讯指数快照，
    # 取不到才显式标注跳过）----
    m_pct = getattr(args, "market_pct", None)
    if m_pct is None:
        _idx = _tencent_index_pct()
        m_pct = (_idx or {}).get("sh")
        if m_pct is not None:
            print(f"  📡 自动获取大盘涨跌幅: {m_pct:+.2f}%（C9已激活）")
    if m_pct is not None:
        sc_args += ["--market-pct", str(m_pct)]
    else:
        print("  ⚠️ 大盘涨跌幅获取失败: C9冷静期将跳过（fail-open显式标注）")

    sell_rc, sell_result = run_check(PRE_SELL_CHECK, sc_args)
    holds = sell_result.get("hold_reasons", [])
    for h in holds:
        print(f"  ⏳ HOLD: {h}")
    # R6 已验证风险特征提示（2026-08-18验证，提示性展示，不改变流程/verdict）
    for rh in (sell_result.get("risk_hints") or []):
        print(f"  ⚠️ R6风险提示: {rh}")

    if sell_result.get("verdict") == "HOLD":
        if owner_emergency and loss_pct is not None and loss_pct <= -0.08:  # 卖出三铁律-8%不变
            print(f"  ⚠️ 防卖飞命中但止损紧急单豁免")
        else:
            print(f"\n⛔ 防卖飞拦截 ({len(holds)} 条)")
            return 1
    # 2026-08-24 P1修复(卖出侧同步fail-closed): 此前只挡字面"HOLD"，闸门崩溃/超时/
    # 参数不识别/输出无JSON时 run_check 返回 HARD_REJECT/UNKNOWN 且 rc被丢弃——
    # 一律被当"通过"继续卖出（镜像8/24买入侧P0修复）。
    _sv = sell_result.get("verdict", "")
    if sell_rc != 0 or _sv not in ("SELL_OK", "HOLD"):
        err = sell_result.get("error") or sell_result.get("raw_output", "")
        print(f"\n⛔ 防卖飞检查异常 (verdict={_sv or '无输出'}, rc={sell_rc})，拒绝卖出")
        if err:
            print(f"  ❌ {str(err)[:300]}")
        return 1
    print(f"  ✅ 防卖飞检查通过")

    # Step 2.5: 冲动拦截 M-5
    print(f"\n🔐 Step 2.5/5: 冲动拦截检查...")
    _, impulse_result = run_check(IMPULSE_BLOCKER, [
        "--action", "sell", "--code", code,
        "--source", source, "--skip-cooldown",
    ])
    i_v = impulse_result.get("verdict", "PASS")
    i_reasons = impulse_result.get("reasons", [])
    i_warnings = impulse_result.get("warnings", [])
    for r in i_reasons:
        print(f"  ❌ {r}")
    for w in i_warnings:
        print(f"  ⚠️ {w}")
    if i_v != "PASS":
        print(f"\n⛔ 冲动拦截触发 ({len(i_reasons)} 条)")
        return 1
    print(f"  ✅ 冲动拦截通过")

    # Step 3: trade_state
    print(f"\n📝 Step 3/5: 更新交易状态...")
    subprocess.run(
        [sys.executable, TRADE_STATE, "--record-sell", code],
        capture_output=True, text=True, timeout=15,
    )
    print(f"  ✅ trade_state 已更新")

    # Step 4: 下单
    print(f"\n🚀 Step 4/5: 执行卖出...")
    print(f"  标的: {name}({code}) | 数量: {quantity} | 亏损: {loss_pct*100 if loss_pct else 0:.1f}%")

    order = {
        "action": "SELL", "code": code, "name": name,
        "quantity": quantity, "loss_pct": loss_pct,
        "owner_emergency": owner_emergency,
        "timestamp": _now_str(),
        "time_window": tw_result,
        "sell_check": sell_result,
        "impulse_check": impulse_result,
    }

    orders_dir = os.path.join(REPORT_DIR, "pending_orders")
    os.makedirs(orders_dir, exist_ok=True)
    order_path = os.path.join(orders_dir, f"sell_{code}_{_today()}.json")
    with open(order_path, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)

    print(f"  ✅ 卖出指令已生成: {order_path}")

    # ---- t9: 执行日志落盘 ----
    _write_exec_log(args, "sell", order_path)

    print(f"\n🎯 交易执行成功")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙系统·闸门全链路交易执行器 S-1+M-5")
    p.add_argument("--action", required=True, choices=["buy", "sell"])
    p.add_argument("--code", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--price", type=float)
    p.add_argument("--quantity", type=int)
    p.add_argument("--amount", type=float)
    p.add_argument("--source", default="",
                   help="信号源(bp_ep_core/mainline_radar/pattern_match/manual_confirmed)")
    p.add_argument("--sector", default="",
                   help="申万L1板块名（R24板块高潮禁追；2026-08-24 P0修复新增转发）")
    p.add_argument("--holdings", type=int)
    p.add_argument("--total-cash", type=float)
    p.add_argument("--total-assets", type=float,
                   help="总资产(t31 G12②: 当日实验源买入合计≤总资产20%%熔断; 缺失则fail-open提示)")
    p.add_argument("--pct-60d", type=float)
    p.add_argument("--loss-pct", type=float)
    p.add_argument("--market-pct", type=float,
                   help="大盘当日涨跌幅%%（C9系统风险日冷静期，未传则C9跳过）")
    p.add_argument("--owner-emergency", action="store_true")
    p.add_argument("--allow-add-position", action="store_true",
                   help="允许对已持仓标的加仓(R23豁免；8/20 TCL科技裸奔加仓案加固)")
    # [2026-08-24 已删除] R19-R21 参数(--hammer-bottom/--hammer-score/--v-reversal/
    # --v-reversal-score/--halt-resume/--post-halt-boards/--post-halt-turnover)：
    # pre_trade_check_v2 8/14 v2.1 已移除对应检查项，保留参数只会造成转发即崩。
    # ---- t9: 签名校验（必需）----
    p.add_argument("--signature", default="",
                   help="strategy_gate.py 输出的签名 JSON 文件路径（必需，无签名拒绝执行）")
    # ---- t21: m7 分析报告校验（可选携带，携带即强制）----
    p.add_argument("--verify-report", default="",
                   help="分析报告(.md)路径；提供后必须通过 verify_agent_output.py(m7)校验才允许下单")
    p.add_argument("--verify-package", default="",
                   help="m1标准数据包(pkg_*.json)路径，与 --verify-report 配套使用")
    a = p.parse_args()

    if a.action == "buy" and not a.price:
        print("❌ 买入必须指定 --price")
        return 2
    if a.action == "sell" and not a.quantity:
        print("❌ 卖出必须指定 --quantity")
        return 2

    # ---- t9: 签名校验（必需）----
    if not a.signature:
        print("❌ 拒绝执行：缺少 --signature（必须经由 strategy_gate.py 闸门签名后才能执行）")
        return 1
    sig_data = validate_signature(a.signature, a)
    if sig_data is None:
        return 1
    a._sig_data = sig_data

    print(f"{'='*60}")
    print(f"  屠龙闸门全链路执行器 S-1+M-5")
    print(f"  操作: {'🟢 买入' if a.action == 'buy' else '🔴 卖出'} {a.name}({a.code})")
    print(f"  时间: {_now_str()}")
    print(f"{'='*60}")

    # ---- t21: m7 分析报告校验关卡（报告造假 → 物理拒绝）----
    print(f"\n🔐 m7关卡: 分析报告校验（反臆想，t21）...")
    m7_result = run_m7_verify(a)
    a._m7_verify = m7_result
    if m7_result["status"] == "REJECTED":
        _write_exec_log(a, a.action, "", status="M7_REJECTED")
        print(f"\n⛔ m7校验未通过：分析报告存在违规（或校验链异常），拒绝执行交易")
        return 1

    if a.action == "buy":
        return exec_buy(a)
    else:
        return exec_sell(a)


if __name__ == "__main__":
    raise SystemExit(main())