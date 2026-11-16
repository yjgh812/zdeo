#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a1_slippage_ledger.py — A1 事件驱动限价卖出兜底的滑点测量台账（2026-10-16 收口批）。

背景（10/16 第三轮全维自省 E24）：A1 机制（owner_decisions_20261012 批准，executor
挂单注记 A1_EVENT_SELL + ≥10:00 未成交转市价建议）10/15 起实弹在跑，但无测量台账——
10/15 自省报告 P0 验收条件「A1 满 10 笔核对报告产出」无计量基础。本件补上测量层：

  --scan    缺省。扫 open_orders/executor_orders 中带 A1_EVENT_SELL 注记的卖单，
            按 order_id 配 westock 近 3 月成交明细，计算滑点
            slippage_pp = (成交价 − 委托价) / 委托价 × 100（卖单负值=劣化），
            UPSERT 幂等落 data/a1_slippage_ledger.jsonl（order_id 主键）。
  --report  n≥10 时产出 reports/a1_slippage_report_YYYYMMDD.md（中位/均值/分位+
            全表）；n<10 不出报告（如实披露样本不足，拒绝小样本伪结论）。
  --alarm   执行质量判据探针（2026-11-07 增补）：台账逐日滑点序列→单日样本≥3 且
            |中位|>阈值（默认0.5pp）记超标日；最近3个有数据日全部超标→toast+
            notifications.log+exit 2。模拟盘限价成交结构性滑点≈0 属正常，本件为
            过渡实盘阶段预置的执行质量探针（判据源=方法论指南阶段一）。

fail-closed：成交明细拉取失败 exit 3（拒绝假成功）。零交易权（纯测量）。
退出码：0 正常（含 n<10）/ 3 数据源失败。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")  # westock_client 住址（与 fill_confirm_monitor 同源）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from executor_order_ledger import EXEC_ORDERS, OPEN_ORDERS  # noqa: E402


def norm6(code: str) -> str:
    """统一裸 6 位代码（与 fill_confirm_monitor.norm6 同口径）。"""
    c = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.split(".")[0].zfill(6)

LEDGER = r"D:\zcodesj\data\a1_slippage_ledger.jsonl"
REPORT_DIR = r"D:\zcodesj\reports"
EVENT_SELL_TAG = "A1_EVENT_SELL"


def _load_tagged_orders() -> list[dict]:
    """收集带 A1_EVENT_SELL 注记的卖单（open_orders c6_note + executor note 双源并集）。"""
    out: dict[str, dict] = {}
    for path in (OPEN_ORDERS, EXEC_ORDERS):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    r = json.loads(s)
                except json.JSONDecodeError:
                    continue
                note = str(r.get("c6_note") or r.get("note") or "")
                if EVENT_SELL_TAG not in note or (r.get("action") or "").upper() != "SELL":
                    continue
                oid = str(r.get("order_id") or "")
                if not oid or oid in out:
                    continue
                out[oid] = {"order_id": oid, "trade_date": str(r.get("trade_date") or ""),
                            "code6": norm6(r.get("code", "")), "name": r.get("name") or "",
                            "order_price": float(r.get("price") or 0),
                            "qty": int(r.get("qty") or 0)}
    return list(out.values())


def _fetch_fills() -> list[dict]:
    from westock_client import WeStockClient  # noqa: PLC0415
    ws = WeStockClient()
    r = ws.call("portfolio_paper_history", {"range": "recent", "limit": 500})
    if not isinstance(r, dict) or not r.get("ok"):
        raise RuntimeError(f"portfolio_paper_history 返回异常: {str(r)[:200]}")
    return (r.get("data") or {}).get("records") or []


def cmd_scan() -> int:
    try:
        fills = _fetch_fills()
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL-CLOSED] 成交明细获取失败({type(e).__name__}: {e})——拒绝假成功",
              file=sys.stderr)
        return 3
    by_oid = {str(x.get("orderId") or ""): x for x in fills}
    orders = _load_tagged_orders()
    old: dict[str, dict] = {}
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    try:
                        d = json.loads(s)
                        old[d["order_id"]] = d
                    except json.JSONDecodeError:
                        continue
    added = updated = skipped = 0
    for o in orders:
        x = by_oid.get(o["order_id"])
        if x is None or str(x.get("status") or "") not in ("已成交", "部分成交", "成交"):
            skipped += 1  # 未成交/未在回报侧：不入台账（如实列示计数）
            continue
        fill_px = float(x.get("price") or 0)
        if o["order_price"] <= 0 or fill_px <= 0:
            skipped += 1
            continue
        slip = (fill_px - o["order_price"]) / o["order_price"] * 100
        rec = {**o, "fill_price": fill_px,
               "fill_qty": int(float(x.get("quantity") or 0)),
               "filled_at": str(x.get("filledAt") or ""),
               "slippage_pp": round(slip, 3),
               "recorded_at": datetime.now().isoformat(timespec="seconds")}
        if o["order_id"] in old:
            if old[o["order_id"]].get("filled_at") != rec["filled_at"]:
                updated += 1
        else:
            added += 1
        old[o["order_id"]] = rec
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for d in old.values():
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
    vals = sorted(d["slippage_pp"] for d in old.values())
    med = vals[len(vals) // 2] if vals else None
    print(f"[A1-LEDGER] 带注记卖单={len(orders)} 新增={added} 更新={updated} "
          f"未配={skipped} 台账总={len(old)} "
          f"滑点pp: med={med} min={vals[0] if vals else None} max={vals[-1] if vals else None}")
    return 0


def cmd_report() -> int:
    if not os.path.exists(LEDGER):
        print("[A1-REPORT] 台账为空（先 --scan）")
        return 0
    rows = [json.loads(s) for s in open(LEDGER, encoding="utf-8") if s.strip()]
    if len(rows) < 10:
        print(f"[A1-REPORT] n={len(rows)} < 10——样本不足不出报告"
              f"（10/15 自省 P0 验收条件=满 10 笔，拒绝小样本伪结论）")
        return 0
    vals = sorted(r["slippage_pp"] for r in rows)
    med = vals[len(vals) // 2]
    mean = sum(vals) / len(vals)
    path = os.path.join(REPORT_DIR, f"a1_slippage_report_{datetime.now():%Y%m%d}.md")
    lines = [f"# A1 事件驱动限价卖出滑点核对报告 {datetime.now():%Y-%m-%d %H:%M}", "",
             f"- n={len(rows)} med={med:.3f}pp mean={mean:.3f}pp "
             f"min={vals[0]:.3f} max={vals[-1]:.3f}",
             "- 口径：slippage=(成交价−委托价)/委托价，卖单负值=劣化；10/9 跳空回放对照="
             "急杀市价追卖滑点中位 -2.81pp。", "",
             "| 委托日 | 代码 | 名称 | 委托价 | 成交价 | 数量 | 滑点pp | 成交时刻 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["trade_date"]):
        lines.append(f"| {r['trade_date']} | {r['code6']} | {r['name']} | {r['order_price']} "
                     f"| {r['fill_price']} | {r['qty']} | {r['slippage_pp']} | {r['filled_at']} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[A1-REPORT] n={len(rows)} med={med:.3f}pp -> {path}")
    return 0



# ── 执行质量判据探针(2026-11-07 增补, 判据源=实盘过渡方法论指南阶段一) ──
ALARM_THRESHOLD_PP = 0.5   # 单日滑点中位超标线(pp)
ALARM_CONSEC_DAYS = 3      # 连续超标日数触发线(按台账自身日期序列)
ALARM_MIN_SAMPLES = 3      # 单日最少样本数，不足则该日不判超标（如实披露）
NOTIFICATIONS_LOG = r"D:\zcodesj\data\notifications.log"


def _toast(msg: str) -> None:
    try:  # 仅本机 toast, 照 9/8 裁决无外部推送
        import subprocess
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"New-BurntToastNotification -Text '{msg}'"],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def cmd_alarm(threshold_pp: float = ALARM_THRESHOLD_PP) -> int:
    """执行质量判据探针：台账逐日滑点序列→连续超标判据。零交易权，只读。
    样本不足/台账为空 → 如实披露不触发，拒绝伪健康。exit 0 未触发 / 2 已触发报警。"""
    if not os.path.exists(LEDGER):
        print("[A1-ALARM] 台账为空——无可测执行质量数据，判据不触发（如实披露）")
        return 0
    by_day: dict[str, list[float]] = {}
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                d = json.loads(s)
            except json.JSONDecodeError:
                continue
            day = str(d.get("trade_date") or "")
            v = d.get("slippage_pp")
            if day and isinstance(v, (int, float)) and not isinstance(v, bool):
                by_day.setdefault(day, []).append(float(v))
    if not by_day:
        print("[A1-ALARM] 台账无有效滑点行——判据不触发（如实披露）")
        return 0
    stats = []
    for day in sorted(by_day):
        vals = sorted(by_day[day])
        stats.append((day, len(vals), vals[len(vals) // 2]))
    last3 = stats[-3:]
    fired = (len(stats) >= ALARM_CONSEC_DAYS
             and all(n >= ALARM_MIN_SAMPLES and abs(med) > threshold_pp
                     for _, n, med in last3))
    tail = " ".join(f"{d}:{m:+.2f}pp(n={n})" for d, n, m in stats[-5:])
    if fired:
        text = (f"滑点超标: 最近3有数据日中位滑点 "
                f"{' '.join(f'{m:+.2f}pp' for _, _, m in last3)} 均>{threshold_pp}pp"
                f"→ 执行质量核查(指标层报警, 交易决定仍走闸门)")
        _toast(text)
        with open(NOTIFICATIONS_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | A1-ALARM | {text}\n")
        print(f"[A1-ALARM] 🔔 {text}")
        return 2
    print(f"[A1-ALARM] 有数据日={len(stats)} 近5日 {tail} → 未触发"
          f"（判据不满足/样本不足, 如实）")
    return 0


# ── a2 脚本级缓解(2026-10-19「按推荐继续」批准, SOP一百六十四a2项): 单实例守卫 ──
# 并发卫生守卫: 防本脚本被重复注册/手动双开时并发实例交错写同一台账
# (20261020 勘误: 初装依据「leader/swt 双注册」经查系清单扫描映射错位的假阳性——
# 二者实为不同脚本; 守卫保留作防御性卫生件, 正常单注册路径零触发)。
# 守卫=锁文件(PID|时间戳): 在位且持锁进程存活(或20分钟内不可判)→后到者 [SKIP]
# 让位 exit 0; 陈旧(进程死或 mtime>20分钟)→接管。psutil 判存活, 不可用退化
# mtime TTL 保守语义。退出 finally 清理(仅清自己 PID 的锁)。零交易语义变更。
_LOCK_PATH = r"D:\zcodesj\data\.a1_slippage_ledger.lock"
_LOCK_TTL_S = 1200


def _pid_alive(pid):
    try:
        pid = int(str(pid).strip())
    except Exception:
        return None
    if pid <= 0:
        return None
    try:
        import psutil
        return bool(psutil.pid_exists(pid))
    except Exception:
        return None  # psutil 不可用 → 退化仅按 mtime TTL 判陈旧(保守)


def _acquire_single_instance():
    import time as _time
    for _attempt in range(2):
        try:
            fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()}|{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            return _LOCK_PATH
        except FileExistsError:
            pass
        try:
            age = _time.time() - os.path.getmtime(_LOCK_PATH)
            with open(_LOCK_PATH, encoding="utf-8") as f:
                pid = f.read().strip().split("|")[0]
            alive = _pid_alive(pid)
            if age < _LOCK_TTL_S and (alive is None or alive):
                return None  # 在位且持锁进程存活(或不可判) → 让位
            os.remove(_LOCK_PATH)  # 陈旧(进程死或>20分钟) → 接管
        except OSError:
            if os.path.exists(_LOCK_PATH):
                try:
                    os.remove(_LOCK_PATH)
                except OSError:
                    return None
    return None


def _release_single_instance(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                pid = f.read().strip().split("|")[0]
            if str(pid) == str(os.getpid()):
                os.remove(path)
    except OSError:
        pass


def main() -> int:
    if "--report" in sys.argv:
        return cmd_report()
    if "--alarm" in sys.argv:
        return cmd_alarm()
    return cmd_scan()


if __name__ == "__main__":
    _lk = _acquire_single_instance()
    if _lk is None:
        print("[A1-LEDGER] [SKIP] single-instance: 活跃实例持锁, 让位退出(并发卫生)")
        raise SystemExit(0)
    try:
        raise SystemExit(main())
    finally:
        _release_single_instance(_lk)
