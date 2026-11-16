#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_step4_probe.py — 22:00 链第四步(盘前信号预生成)产物探针与自愈（2026-08-28 落地）

背景（E62 三夜链停滞 + 8/27 夜链实测）：22:00 链第四步三产物——
  ① zt_flow_leading_{date}.json（昨涨停板块资金）
  ② breakout_baseline_{date}.json（横盘突破命中率基线）
  ③ reports/hotspot_breakout_latest.csv（热点突破候选，latest 命名）
——任一缺失，次日 09:10 作战清单"昨涨停资金/横盘基线"两段即"未生成"且无告警
（8/27 夜链 ①② 实际缺失，产物探针未覆盖，本会话手动补跑才恢复）。

本探针：检测目标交易日三产物是否齐备；--fix 时对缺失的幂等生成器重跑
（zt_flow/breakout 均只读本地数据写 json，hotspot 读 qfq 写 csv，安全可重跑）。

用法：
  python chain_step4_probe.py --date 20260827            # 检测，退出码 0=齐/1=缺
  python chain_step4_probe.py --date 20260827 --fix      # 缺则补跑，仍缺退出 2（fail-closed）
  python chain_step4_probe.py --json
缺省 --date：取 vipdoc 最新交易日（与 09:10 预期交易日同源）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import datetime

BASE = r"D:\zcodesj"
REPORTS = os.path.join(BASE, "reports")
VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh600519.day"


def expected_trading_date() -> str | None:
    """探针预期交易日（2026-09-03 A2 修正）：
    22:00 链产物当日生成；链未跑窗口（vipdoc 最新日==今天 且 当前<22:00）应核对
    昨夜链产物=上一交易日，否则盘后 15:00-22:00 恒误报缺失（9/3 19:37 实证）。
    其余场景（≥22:00 预期今晚产物 / 最新日≠今天即非交易日或数据滞后）取 vipdoc 最新日。
    """
    try:
        import struct
        with open(VIPDOC_DAY, "rb") as f:
            f.seek(-64, 2)
            raw = f.read(64)
        dates = [str(struct.unpack_from("<I", raw, i)[0]) for i in (0, 32)]
        dates = [d for d in dates if int(d) > 19900101]
        if not dates:
            return None
        latest = dates[-1]
        if (latest == datetime.date.today().strftime("%Y%m%d")
                and datetime.datetime.now().hour < 22
                and len(dates) >= 2):
            return dates[-2]
        return latest
    except Exception:
        return None


def probe(date_str: str) -> dict:
    zt = os.path.join(REPORTS, f"zt_flow_leading_{date_str}.json")
    bb = os.path.join(REPORTS, f"breakout_baseline_{date_str}.json")
    hs = os.path.join(REPORTS, "hotspot_breakout_latest.csv")
    zt_ok = os.path.exists(zt) and os.path.getsize(zt) > 10
    bb_ok = os.path.exists(bb) and os.path.getsize(bb) > 10
    # hotspot 为 latest 命名：校验 mtime 不早于该日 20:00（链运行窗口），且非空
    hs_ok = False
    if os.path.exists(hs) and os.path.getsize(hs) > 10:
        hs_day = datetime.datetime.fromtimestamp(os.path.getmtime(hs)).strftime("%Y%m%d")
        hs_ok = hs_day == date_str or hs_day >= date_str
    missing = []
    if not zt_ok:
        missing.append("zt_flow_leading")
    if not bb_ok:
        missing.append("breakout_baseline")
    if not hs_ok:
        missing.append("hotspot_breakout")
    return {"date": date_str, "zt_flow_ok": zt_ok, "breakout_ok": bb_ok,
            "hotspot_ok": hs_ok, "missing": missing, "all_ok": not missing}


FIX_CMDS = {
    "zt_flow_leading": [sys.executable, os.path.join(BASE, "attribution", "zt_flow_leading.py"),
                        "--date", "{date}"],
    "breakout_baseline": [sys.executable,
                          os.path.join(BASE, "attribution", "breakout_hitrate_baseline.py"),
                          "--days", "20", "--date", "{date}"],
    "hotspot_breakout": [sys.executable, os.path.join(BASE, "tulong_hotspot_breakout_picker.py"),
                         "--date", "{date}"],
}


def fix(date_str: str, missing: list[str]) -> dict:
    """对缺失产物重跑生成器。返回 {步: 成功/失败}。"""
    results = {}
    for step in missing:
        cmd = [a.format(date=date_str) if isinstance(a, str) else a for a in FIX_CMDS[step]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                               encoding="utf-8", shell=False,
                               env=dict(os.environ, PYTHONUTF8="1"))
            ok = r.returncode == 0 and os.path.exists(
                os.path.join(REPORTS, f"zt_flow_leading_{date_str}.json")
            ) if step == "zt_flow_leading" else r.returncode == 0
            results[step] = "ok" if ok else f"failed(rc={r.returncode}) {r.stderr[-120:]}"
        except Exception as e:
            results[step] = f"exception {type(e).__name__}: {e}"
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="22:00链第四步产物探针/自愈")
    ap.add_argument("--date", default=None, help="目标交易日 YYYYMMDD（默认 vipdoc 最新交易日）")
    ap.add_argument("--fix", action="store_true", help="缺产物则重跑对应幂等生成器")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    a = ap.parse_args()
    date_str = a.date or expected_trading_date()
    if not date_str:
        print(json.dumps({"error": "无法推导目标交易日(vipdoc 读取失败)"}))
        return 2
    p = probe(date_str)
    if a.fix and p["missing"]:
        p["fix_results"] = fix(date_str, p["missing"])
        p = probe(date_str)  # 重测
        p["fix_results"] = p.get("fix_results", {})
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print(f"步4产物探针 {date_str}: zt_flow={'✅' if p['zt_flow_ok'] else '❌'} "
              f"breakout={'✅' if p['breakout_ok'] else '❌'} "
              f"hotspot={'✅' if p['hotspot_ok'] else '❌'}"
              + (f" 缺失={p['missing']}" if p["missing"] else ""))
        if p.get("fix_results"):
            print("自愈结果:", p["fix_results"])
    return 0 if p["all_ok"] else (1 if not a.fix else 2)


if __name__ == "__main__":
    sys.exit(main())
