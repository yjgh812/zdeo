# -*- coding: utf-8 -*-
"""PD-014 环境闸门前置（2026-09-22，主人「现在就干/继续」令实施 C-条件门控版）

条款对应: v5.3 双prereg UP_BUCKET_ENV_GATE(206ca4b1) + REGIME_BUDGET_GATE(dcfa9a31),
env<40 六层停跑。门控信号 = data/market_state_probe.json 的 state（每日20:00更新）:
  state == LOW_VOL（闸门关）→ exit 7 → MondayBuy 转 --dry 影子, 不下单
  state != LOW_VOL（恢复）   → exit 0 → 照常实弹
  探测文件缺失/损坏/任何异常 → exit 7（保守方向: regime 不明不实弹）

口径注记: 9/19-9/22 probe LOW_VOL 与 census env=31 持续同向; 若两口径背离, 以呈报主人
裁决为准, 本脚本不自行裁断。回退: 从 monday_buy_run.cmd 删除 p014 两行即恢复 9/18 原行为。
本脚本只输出退出码, 永不下单、不改任何状态。
"""
import json
import os
import sys

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "market_state_probe.json")


def main() -> int:
    try:
        d = json.load(open(PROBE, encoding="utf-8"))
        state = (d.get("state") or "").strip()
        if not state:
            print("[P014-GATE] probe state 缺失 -> 保守转影子")
            return 7
        if state == "LOW_VOL":
            print("[P014-GATE] state=LOW_VOL(env<40 六层停跑条款生效) -> exit 7 转影子")
            return 7
        print(f"[P014-GATE] state={state} 闸门开 -> exit 0 照常执行")
        return 0
    except Exception as e:
        print(f"[P014-GATE] probe 不可读({e}) -> 保守转影子")
        return 7


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
