# -*- coding: utf-8 -*-
"""全市场波段普查（2026-09-19 主人令"每只股票的波段识别"批量版）

调度: schtasks WaveCensus_Sat_0600 每周六 06:00（波段状态变化慢, 周频足够; 避开全部交易链）
产出: reports/wave_census_YYYYMMDD.md（全市场底部/顶部/中段分布 + 底部强名单 + 顶部强名单）
时长: 约 2-2.5 小时（5,789 只×~1.5s）
定位: 只读普查件零交易权; 与 mainline_startup_scan(日线)/holdings_risk_scan(日线)互补的周频全景。
"""
from __future__ import annotations
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
OUT_MD = os.path.join(ROOT, "reports", f"wave_census_{datetime.now().strftime('%Y%m%d')}.md")


def main() -> int:
    db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20260901'").fetchall()]
    db.close()
    states = []
    for i, code in enumerate(codes):
        if i % 500 == 0:
            print(f"  ...{i}/{len(codes)}", flush=True)
        try:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "wave_feature_engine.py"),
                                "--code", code, "--last", "1"],
                               capture_output=True, text=True, timeout=45,
                               encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines():
                if "底部区域特征强" in line:
                    states.append((code, line, "BOT"))
                    break
                if "顶部区域特征强" in line:
                    states.append((code, line, "TOP"))
                    break
                if "中段" in line:
                    states.append((code, line, "MID"))
                    break
        except Exception:
            continue
    cnt = Counter(s[2] for s in states)
    bots = [s for s in states if s[2] == "BOT"]
    tops = [s for s in states if s[2] == "TOP"]
    L = [f"# 全市场波段普查（{datetime.now().strftime('%Y-%m-%d %H:%M')}·周频常驻件）",
         "",
         f"> 覆盖 {len(states)}/{len(codes)} 只 ｜ 分布: 🟢底部强 {cnt.get('BOT',0)} 只 / 🔴顶部强 {cnt.get('TOP',0)} 只 / ⚪中段 {cnt.get('MID',0)} 只",
         f"> 底部强=主升候选池上游(配合 MainlineScan 日筛); 顶部强=利润风险/回避池(配合持仓快照)",
         "",
         "## 🟢 底部区域特征强（得分≥3，扫描器候选上游）",
         ""]
    L += [f"- {s[1]}" for s in bots]
    L += ["", "## 🔴 顶部区域特征强（得分≥3，回避/持仓风险）", ""]
    L += [f"- {s[1]}" for s in tops]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print(f"[CENSUS-OK] {OUT_MD} | 底{cnt.get('BOT',0)} 顶{cnt.get('TOP',0)} 中{cnt.get('MID',0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
