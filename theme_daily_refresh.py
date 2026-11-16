#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme_daily_refresh.py — 主线五角色+联合日卡 观测层日更刷新器（零交易权）

流程（每交易日收盘后）:
  1) akshare 东财涨停池(近期日期有效; 历史深抓已证不可用) → 转 scanner input JSON
     口径降级声明: 东财池无 limit_up_reason, 以「所属行业」填充 → 题材聚类退化为行业聚类(如实标注)
  2) emotion_phase_v2 台账刷新(--days 5, 幂等 UPSERT): schtasks EmotionPhaseV2_2010(20:10)
     恒早于夜链 qfq 更新, 当日相位行只能由本步补写; rc!=0 不阻断, 缺行时 joint_card 自行 fail-closed
  3) theme_roles_scanner --date --input
  4) theme_joint_card --date
输出: data/theme_roles_input_*.json + theme_roles_*.json + theme_roles_*.md + theme_joint_card_*.md
退出码: 0 正常 / 2 数据缺失
边界: 零交易权; 不接闸门; 观测层描述性标注
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    a = ap.parse_args()
    d8 = a.date
    d10 = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"

    import akshare as ak
    try:
        df = ak.stock_zt_pool_em(date=d8)
    except Exception as e:
        print(f"[FAIL] 东财涨停池抓取失败: {e}", file=sys.stderr)
        return 2
    if df is None or df.empty:
        print(f"[EMPTY] {d8} 涨停池 0 行(非交易日或数据未出)")
        return 0

    items = []
    for _, r in df.iterrows():
        name = str(r.get("名称", ""))
        items.append({
            "ticker": str(r.get("代码", "")),
            "name": name,
            "continue_day_cnt": int(r.get("连板数", 1) or 1),
            # 口径降级: 东财池无涨停原因, 用申万行业填充(聚类退化为行业模式, 如实标注)
            "limit_up_reason": str(r.get("所属行业", "")),
            "seal_money": float(r.get("封板资金", 0) or 0),
            "limit_up_time": str(r.get("首次封板时间", "")),
            "is_st": "ST" in name.upper(),
            "last_price": float(r.get("最新价", 0) or 0),
        })
    inp = os.path.join(ROOT, "data", f"theme_roles_input_{d8}.json")
    json.dump({"date": d8, "source": "akshare stock_zt_pool_em (theme_daily_refresh 落盘)",
               "caliber_note": "行业聚类降级模式(无reason)", "item": items},
              open(inp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[1/4] input 落盘 {len(items)} 只 -> {inp}")

    r0 = subprocess.run([sys.executable, os.path.join(ROOT, "emotion_phase_v2.py"),
                         "--days", "5"], capture_output=True, text=True,
                        timeout=300, cwd=ROOT)
    print(f"[2/4] emotion_phase ledger refresh rc={r0.returncode}")
    if r0.returncode:
        print((r0.stderr or r0.stdout or "")[-200:], file=sys.stderr)

    r1 = subprocess.run([sys.executable, os.path.join(ROOT, "theme_roles_scanner.py"),
                         "--date", d8, "--input", inp], capture_output=True, text=True,
                        timeout=300, cwd=ROOT)
    print(f"[3/4] scanner rc={r1.returncode}")
    if r1.returncode:
        print((r1.stderr or r1.stdout or "")[-200:], file=sys.stderr)
    r2 = subprocess.run([sys.executable, os.path.join(ROOT, "theme_joint_card.py"),
                         "--date", d8], capture_output=True, text=True, timeout=120, cwd=ROOT)
    print(f"[4/4] joint_card rc={r2.returncode}")
    if r2.returncode:
        print((r2.stderr or r2.stdout or "")[-200:], file=sys.stderr)
    return 0 if (r1.returncode == 0 and r2.returncode == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
