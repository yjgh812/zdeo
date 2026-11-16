#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
博腾股份(300363)验证性买入一次性执行脚本（主人 9/16 裁决方案A · 验证单契约）
触发: schtasks BostengVerify_Exec_0921 (2026-09-21 周一 09:33, 交易时段内)
契约要点:
  - 幂等: result json 已 executed=true 则跳过
  - 高开守卫(验证单条款): 实时价 >23.00 → 跳过+notify 呈报主人(追高成本取舍归主人)
  - DD_GATE: 不带特批旗标——闸门以周一实时状态判定(9/17 dry-run 已自然放行; 若周一
    仍防御档 REJECT → 如实记录+notify 呈报, 须主人明示"特批"方可加旗标重试)
  - 限价: 不传 --limit-price → executor 自动取实时价(BUY_MKT_CAP=+0.5% 上限保护,
    对应验证单 9/16 更新"按实时价+1%重设限价"条款)
  - 止损: 19.52 锚纳入 S-5 止损链自动执行(持仓成本入库后止损链自动覆盖)
输出: data/_bosteng_300363_exec_result.json
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
RESULT = ROOT / "data" / "_bosteng_300363_exec_result.json"
GAP_CEILING = 23.00
CMD_BASE = [sys.executable, str(ROOT / "trade_executor.py"), "buy",
            "--code", "300363", "--name", "博腾股份",
            "--source", "manual_confirmed", "--sector", "医药生物", "--volume", "1000"]


def log(msg):
    print(f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} [bosteng_exec] {msg}", flush=True)


def notify(title, content, level="info"):
    try:
        subprocess.run([sys.executable, str(ROOT / "notify.py"), "send",
                        "--title", title, "--content", content, "--level", level],
                       capture_output=True, text=True, timeout=30)
    except Exception as e:
        log(f"notify 失败(不阻断): {e}")


def realtime_price():
    """腾讯实时价(300363 创业板), 失败返回 None(则跳过高开守卫交由闸门 BUY_MKT_CAP 兜底)"""
    try:
        req = urllib.request.Request("http://qt.gtimg.cn/q=sz300363",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read().decode("gbk", errors="ignore")
        vals = data.split("=", 1)[1].strip('"').split("~")
        return float(vals[3])  # 当前价
    except Exception as e:
        log(f"实时价获取失败(高开守卫降级放行, BUY_MKT_CAP 兜底): {e}")
        return None


def main():
    if RESULT.exists():
        prev = json.loads(RESULT.read_text(encoding="utf-8"))
        if prev.get("executed"):
            log(f"幂等跳过: 已成交(prev result executed=true @ {prev.get('filled_price')})")
            return 0
    px = realtime_price()
    log(f"实时价: {px}")
    if px is not None and px > GAP_CEILING:
        RESULT.write_text(json.dumps({"executed": False, "skipped": "gap_up",
                                      "realtime_price": px, "ceiling": GAP_CEILING},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"高开守卫触发: {px} > {GAP_CEILING} → 跳过执行, notify 呈报主人(验证单条款)")
        notify("博腾验证单未执行:高开超限", f"实时价 {px} > 高开上限 {GAP_CEILING}, "
                                          "按验证单条款跳过, 追高成本取舍待主人裁决", "warn")
        return 0

    cmd = CMD_BASE + ["--execute"]
    log(f"执行: {' '.join(cmd[1:])}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    out_text = (proc.stdout or "") + (proc.stderr or "")
    executed, reason, filled_price = False, out_text[-300:], None
    try:
        start = proc.stdout.index("{")
        gate_out = json.loads(proc.stdout[start:])
        executed = bool(gate_out.get("executed"))
        reason = gate_out.get("reason", "")
        # 成交价从 executor 输出提取(兼容不同字段)
        for k in ("filled_price", "price", "deal_price"):
            if gate_out.get(k):
                filled_price = gate_out[k]
                break
    except Exception:
        pass
    RESULT.write_text(json.dumps({"executed": executed, "reason": reason[:500],
                                  "filled_price": filled_price,
                                  "realtime_price": px, "raw_tail": out_text[-1500:]},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    if executed:
        log(f"✅ 已成交: {filled_price} × 1000 股; 止损锚 19.52 纳入 S-5 链")
        notify("博腾验证单已成交", f"300363 博腾股份 {filled_price}×1000股, "
                                   "止损锚19.52(S-5自动); 20交易日可证伪判定到期约10/21", "info")
    else:
        log(f"未成交: {reason[:200]}")
        notify("博腾验证单未成交", f"reason: {reason[:200]}(如DD_GATE防御档→须主人明示'特批'方可加旗标重试)",
               "warn")
    return 0 if executed else 1


if __name__ == "__main__":
    sys.exit(main())
