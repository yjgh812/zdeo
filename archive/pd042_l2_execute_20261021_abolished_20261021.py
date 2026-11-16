# -*- coding: utf-8 -*-
"""[已废除 2026-10-21 主人令] PD-042 红档减仓 L2 碎仓清理执行器。

废除理由：模拟盘=研究训练场，碎仓持仓本身是研究样本（选股有效性、买卖点反馈的
实证数据源），清理碎仓等于销毁实验数据；实证缺陷=2026-10-21 上午在兆讯传媒
当日 +14% 主升浪中无差别卖出（卖飞实证入档）。
主人令后碎仓清理整体废除，本脚本归档留存仅供历史审计，禁止再运行。
详见 PROJECT_BOUNDARY §6 已否定路线。
================================================================================
[以下为废除前原文件内容]
"""
# -*- coding: utf-8 -*-
"""PD-042 红档减仓 L2 碎仓清理执行器（主人 2026-10-20「现在执行」令；一次性，明晨 09:35 交易时段内运行）
用法: python analysis/pd042_l2_execute_20261021.py [--force]
  - 交易时段守卫: 09:30-14:55 之外直接退出(executor 自身也会时段拒绝, 双保险)
  - 定价: qt.gtimg 开盘价×0.995(开盘未出用昨收×0.995), 两位小数向下取
  - 幂等: 当日结果文件已存在则跳过(--force 覆盖)
  - 持有保护类 REJECT(C1/C3)自动加 --owner-override-hold 重试一次(授权链=主人令, 先例 10/15 博汇)
  - 只做下列 12 笔, 禁买禁其他卖; L3/L4 未授权禁碰
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")

PY = sys.executable
EXECUTOR = r"D:\zcodesj\trade_executor.py"
HOLDINGS = r"D:\zcodesj\data\holdings_codes_latest.json"
RESULT = rf"D:\zcodesj\analysis\pd042_l2_exec_result_{time.strftime('%Y%m%d')}.json"

ORDERS = [
    ("600694", "大商股份", 300), ("600035", "楚天高速", 1300),
    ("002174", "游族网络", 100), ("600827", "百联股份", 200),
    ("300612", "宣亚国际", 400), ("002291", "遥望科技", 500),
    ("000670", "盈方微", 500), ("300071", "福石控股", 500),
    ("301102", "兆讯传媒", 500), ("601019", "山东出版", 500),
    ("603803", "瑞斯康达", 500),
]
# 20261021 10:1x修订: 隆华科技300263今晨已被S-5哨出清(qty=0)剔除; 全部11只经westock MCP实仓核验在持
REASON = "PD-042红档减仓L2碎仓清理+主人20261020「现在执行」令"


def quote(code6):
    """qt.gtimg 盘中源: 返回 (open, prev_close, current)；失败返回 None"""
    import urllib.request
    pre = "sh" if code6.startswith(("6",)) else "sz"
    url = f"http://qt.gtimg.cn/q={pre}{code6}"
    try:
        raw = urllib.request.urlopen(url, timeout=8).read().decode("gbk", errors="replace")
        f = raw.split('"')[1].split("~")
        o, pc, cur = float(f[5]), float(f[4]), float(f[3])
        return o, pc, cur
    except Exception as e:
        print(f"[warn] {code6} 行情获取失败: {type(e).__name__} {e}")
        return None


def limit_price(code6):
    q = quote(code6)
    if q is None:
        return None
    o, pc, cur = q
    base = o if o > 0 else pc
    if base <= 0:
        return None
    import math
    return math.floor(base * 0.995 * 100) / 100


def run_sell(code6, name, qty, px, override):
    cmd = [PY, "-u", EXECUTOR, "sell", "--code", code6, "--name", name,
           "--source", "pd042_l2", "--volume", str(qty), "--price", str(px),
           "--reason", REASON, "--execute"]
    if override:
        cmd.append("--owner-override-hold")
    r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONUTF8": "1"})
    out = r.stdout or ""
    hold_block = ("HARD_REJECT" in out and ("C1" in out or "C3" in out or "低估值" in out or "高股息" in out))
    return r.returncode, out, hold_block


def main():
    force = "--force" in sys.argv
    if os.path.exists(RESULT) and not force:
        print(f"[skip] 当日结果已存在: {RESULT} (幂等; --force 覆盖)")
        return
    now = time.localtime()
    if not (now.tm_hour == 9 and now.tm_min >= 30 or 10 <= now.tm_hour <= 14
            or (now.tm_hour == 14 and now.tm_min <= 55)):
        print(f"[exit] 非交易时段({time.strftime('%H:%M')})——请在 09:30-14:55 运行; "
              f"盘外执行器会时段拒绝且 westock 盘后单会被系统撤单。", flush=True)
        sys.exit(2)

    try:
        held_raw = json.load(open(HOLDINGS, encoding="utf-8"))
        # 20261021修: 快照positions为list且code带交易所前缀, 统一剥前缀取裸6位
        held_keys = {str(p.get("code", "")).replace("sh", "").replace("sz", "").replace("bj", "")[-6:]
                     for p in held_raw.get("positions", []) if isinstance(p, dict)}
    except Exception:
        held_keys = None  # 快照缺失不阻断, 交由 executor/westock 实仓校验

    results = []
    for code6, name, qty in ORDERS:
        if held_keys is not None and held_keys and code6 not in held_keys:
            results.append({"code": code6, "name": name, "status": "SKIP_NOT_IN_SNAPSHOT"})
            print(f"[skip] {code6} {name}: 不在持仓快照(隔夜已出?)", flush=True)
            continue
        px = limit_price(code6)
        if px is None:
            results.append({"code": code6, "name": name, "status": "SKIP_NO_QUOTE"})
            print(f"[skip] {code6} {name}: 无行情", flush=True)
            continue
        rc, out, hold_block = run_sell(code6, name, qty, px, override=False)
        if rc != 0 and hold_block:
            rc, out, hold_block = run_sell(code6, name, qty, px, override=True)
            note = "hold_block→owner_override重试"
        else:
            note = ""
        placed = ("submitted" in out.lower() or "OPEN" in out or "FILLED" in out
                  or '"executed": true' in out)
        status = "PLACED" if (rc == 0 or placed) else f"REJECT_rc{rc}"
        results.append({"code": code6, "name": name, "qty": qty, "limit": px,
                        "status": status, "note": note})
        print(f"[{status}] {code6} {name} {qty}股 limit={px} {note}", flush=True)
        time.sleep(1)

    ok = [r for r in results if r.get("status") == "PLACED"]
    summary = {
        "date": time.strftime("%Y-%m-%d"), "orders": len(ORDERS),
        "placed": len(ok), "released_est": round(sum(
            r["limit"] * r["qty"] for r in results if r.get("status") == "PLACED"), 0),
        "results": results,
    }
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"[done] placed={len(ok)}/12 释放估算≈{summary['released_est']:.0f}元 → {RESULT}", flush=True)
    print("[next] 成交核验=westock MCP portfolio_paper_history; 未成交用现价×0.99 重挂一次(不追价超一次)")


if __name__ == "__main__":
    main()
