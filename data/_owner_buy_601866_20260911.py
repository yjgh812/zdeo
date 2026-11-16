# -*- coding: utf-8 -*-
"""主人 2026-09-10 逐笔特批遗留买单: 中远海发(601866) 25000 元, 排 2026-09-11 09:31 执行(一次性)。

裁决链: 9/10 早主人特批 9 只批次(交通/煤炭) -> 6 成交/海峡+海发+南油未成交 -> 主人午间指令
"海峡不追, 未成交的重新下单成交" -> 招商南油当日已成交, 中远海发重挂遭 R30a 行业集中度闸拒
(当日交通运输已新买3只) -> 主人晚间"急需继续"确认推进 -> R30a 特批豁免口落地
(pre_trade_check_v2 + trade_executor 三处同构, 与 R-S5/R6/R31 同模式) -> 盘后时段硬闸拦截
(09:30-15:00 物理闸, 正确行为) -> 本脚本排次一交易日开盘后执行。

铁律合规: 走 trade_executor.py buy 唯一入口(双层防线+归因落库), source=manual_confirmed
(主人逐笔特批); 不带 --price 由 executor 取实时价确保成交(隔夜价差防死单);
R30a 豁免带上作 fail-safe(新交易日当日计数重置后本应自然过闸)。
幂等: 当日台账已有 601866 非 CANCELLED 买单则跳过; 提交异常重试 1 次(westock 断供 9/6 先例),
闸门 REJECT 不重试(判定确定性)。结果落 data/_owner_buy_601866_20260911_result.json。
退出码: 成功/已存在=0, 失败=1。
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

# GBK console 崩链防护(9/10 GBK崩链同族): stdout/stderr 强制 UTF-8, 不可编码字符替换不抛异常
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXECUTOR = os.path.join(BASE, "trade_executor.py")
LEDGER = os.path.join(BASE, "data", "executor_orders.jsonl")
OUT = os.path.join(BASE, "data", "_owner_buy_601866_20260911_result.json")
CODE, NAME, SECTOR = "601866", "中远海发", "交通运输"
AMOUNT = 25000


def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def already_ordered_today() -> str:
    """当日台账已有该码非 CANCELLED 买单则返回描述, 否则空串(幂等)。"""
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if (str(r.get("code")) == CODE and r.get("action") == "BUY"
                        and str(r.get("trade_date")) == today_str()
                        and (r.get("status") or "OPEN") != "CANCELLED"):
                    return f"order_id={r.get('order_id')} status={r.get('status')} price={r.get('price')}"
    except FileNotFoundError:
        return ""
    return ""


def run_buy() -> dict:
    cmd = [sys.executable, EXECUTOR, "buy",
           "--code", CODE, "--name", NAME,
           "--source", "manual_confirmed", "--sector", SECTOR,
           "--amount", str(AMOUNT),
           "--owner-approve-r30a", "--execute"]
    # 跨进程编码统一(9/10 GBK崩链教训): 强制 executor 子进程 stdout 走 UTF-8,
    # 本侧 utf-8 解码——否则 GBK 字节按 UTF-8 强解出 mojibake
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=BASE,
                       encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    verdict = re.search(r'"gate_verdict"\s*:\s*"([^"]+)"', out)
    executed = re.search(r'"executed"\s*:\s*(true|false)', out)
    reason = re.search(r'"reason"\s*:\s*"([^"]+)"', out)
    order_id = re.search(r'"order_id"\s*:\s*"([^"]+)"', out)
    fill = re.search(r'"fill_status"\s*:\s*"([^"]+)"', out)
    return {
        "returncode": p.returncode,
        "gate_verdict": verdict.group(1) if verdict else "PARSE_FAIL",
        "executed": executed.group(1) == "true" if executed else False,
        "reason": reason.group(1) if reason else "",
        "order_id": order_id.group(1) if order_id else "",
        "fill_status": fill.group(1) if fill else "",
        "tail": out[-600:],
    }


def main() -> int:
    dup = already_ordered_today()
    if dup:
        result = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "action": "SKIP", "reason": f"当日已有买单(幂等): {dup}"}
        print(json.dumps(result, ensure_ascii=False))
        _write(result)
        return 0
    r = run_buy()
    if not r["executed"] and r["gate_verdict"] not in ("REJECT", "PARSE_FAIL"):
        r = {"retry": True, **run_buy()}  # 提交异常重试1次(幂等由 executor 台账兜底)
    ok = bool(r["executed"])
    result = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "code": CODE, "name": NAME, "ok": ok,
              "gate_verdict": r["gate_verdict"], "executed": r["executed"],
              "reason": r["reason"], "order_id": r["order_id"],
              "fill_status": r["fill_status"], "tail": r["tail"]}
    _write(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _write(result: dict) -> None:
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
