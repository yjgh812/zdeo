# -*- coding: utf-8 -*-
"""每日晨报(一键出全部关键信息) 20261026 — 只读零交易权。
五段: 市场方向/组合概览/分组表现/阶段诊断/今日警报
用法: python analysis/daily_brief.py
输出: 控制台 + data/daily_brief_latest.json
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")


def main():
    print("=" * 70)
    print(f"  屠龙每日晨报 | {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 70)

    # ---- [1] 市场方向(行业轮动) ----
    print("\n[1] 市场方向")
    try:
        sr = json.load(open("data/sector_rotation_latest.json", encoding="utf-8"))
        verdict = sr.get("filter_verdict", "?")
        mkt_today = sr.get("market_today", 0)
        wl = sr.get("whitelist", [])
        bl = sr.get("blacklist", [])
        print(f"  判定: {verdict}")
        print(f"  大盘当日: {mkt_today:+.2%}")
        print(f"  白名单({len(wl)}): {', '.join(wl[:6])}")
        print(f"  黑名单({len(bl)}): {', '.join(bl[:4])}")
    except Exception as e:
        print(f"  (行业轮动数据不可用: {e})")

    # ---- [2] 组合概览 ----
    print("\n[2] 组合概览")
    try:
        from stop_loss_executor import _fetch_positions_via_mcp, norm_code
        pos = _fetch_positions_via_mcp()
        if not pos:
            print("  (持仓拉取失败)")
        else:
            n = len(pos)
            win = sum(1 for p in pos if p["float_pnl_pct"] > 0)
            total_mv = sum(p["quantity"] * p["current_price"] for p in pos)
            avg_pnl = sum(p["float_pnl_pct"] for p in pos) / n
            best = max(pos, key=lambda p: p["float_pnl_pct"])
            worst = min(pos, key=lambda p: p["float_pnl_pct"])
            print(f"  持仓: {n}只 | 胜率: {win}/{n}({win/n:.0%}) | 均值: {avg_pnl:+.2f}%")
            print(f"  持仓市值: {total_mv:,.0f}元")
            print(f"  最佳: {best['name']} {best['float_pnl_pct']:+.2f}%")
            print(f"  最差: {worst['name']} {worst['float_pnl_pct']:+.2f}%")
    except Exception as e:
        print(f"  (组合数据不可用: {e})")

    # ---- [3] 分组表现 ----
    print("\n[3] 实验组表现")
    try:
        from stop_loss_executor import _fetch_positions_via_mcp
        pos2 = {p["code"][-6:]: p for p in (_fetch_positions_via_mcp() or [])}
        groups = {
            "HIGHWIN": ["300397","300424","605088","300560","300873","001234","603949"],
            "超跌探底": ["600522","000938","603993","603668"],
            "低位启动": ["300059","000100","601601","300380","603638"],
            "中枢对照": ["601166","000560","300017","601288"],
            "横盘超跌": ["000582","600359","600108"],
            "排雷双清": ["300110","603636","601899"],
            "驱动力对垒": ["000498","000011","600887"],
            "替补": ["600547","002241","000725","000007","000029","002354"],
        }
        for gname, codes in groups.items():
            pnls = [pos2[c]["float_pnl_pct"] for c in codes if c in pos2]
            if pnls:
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                avg = sum(pnls) / len(pnls)
                bar = "+" * int(abs(avg) * 10) if avg > 0 else "-" * int(abs(avg) * 10)
                print(f"  {gname:<12} {len(pnls)}只 胜率{wr:.0%} 均值{avg:+.2f}% {bar}")
    except Exception as e:
        print(f"  (分组数据不可用: {e})")

    # ---- [4] 阶段诊断(简化) ----
    print("\n[4] 阶段健康度")
    try:
        diag = json.load(open("analysis/holdings_phase_diagnosis_20261026.json", encoding="utf-8"))
        red = sum(1 for d in diag if d.get("flag") == "红")
        green = sum(1 for d in diag if d.get("flag") == "绿")
        gray = sum(1 for d in diag if d.get("flag") == "灰")
        red_names = [d["name"] for d in diag if d.get("flag") == "红"]
        print(f"  红(卖出考虑): {red}只 {'/'.join(red_names) if red_names else '无'}")
        print(f"  绿(强相位): {green}只")
        print(f"  灰(中性): {gray}只")
    except Exception as e:
        print(f"  (阶段数据不可用: {e})")

    # ---- [5] 止损状态 ----
    print("\n[5] 止损/警报")
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, "stop_loss_executor.py", "--dry-run"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env=dict(os.environ, PYTHONUTF8="1"))
        for ln in r.stdout.splitlines():
            if "触发" in ln or "止损" in ln:
                print(f"  {ln.strip()}")
            if "无止损" in ln:
                print(f"  ✅ 无止损触发(全部安全)")
    except Exception as e:
        print(f"  (止损数据不可用: {e})")

    print("\n" + "=" * 70)
    print("  下一关键节点: 11/20 首批到期结算")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
