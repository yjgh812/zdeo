#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pit_h1_refresh.py — H1中报披露截止后的 factor_pit 增量刷新编排（2026-08-20 设计）

背景：H1(0630)财报法定披露截止 8/31。9/1 09:00 月度引擎(droe清单/ML信号/影子验证)
消费 factor_pit.db；若8/31晚不刷新，9/1裁决只能用 Q1 数据。

链路与依赖（全部为既有脚本，本文件只做编排，不改任何策略逻辑）：
  1. TDX cw 财务文件 (D:/anzhuang/tdx/vipdoc/cw/gpcw20260630.dat)
     —— 需通达信客户端"财务数据下载"先落盘；20字节=占位未下载
  2. tdx_finance_full_parser.py      cw -> dragon_quant.stock_financial
  3. tulong_daily_update.py --phase 4  stock_financial -> stock_factors_fundamental(含pit_avail_date)
  4. build_factor_pit_20260815.py --v2  -> factor_pit.db (幂等增量, 分区先删后插)
  5. 自检: --selftest-v2

用法:
  python pit_h1_refresh.py            # 检查+刷新(幂等, 无新数据则报告后退出)
  python pit_h1_refresh.py --force    # 跳过cw新鲜度检查直接跑链路
"""
from __future__ import annotations
import os, sqlite3, subprocess, sys
from datetime import datetime
from pathlib import Path

CW_H1 = Path(r"D:/anzhuang/tdx/vipdoc/cw/gpcw20260630.dat")
PARSER = r"D:/workbuddyspace/tdx_finance_full_parser.py"
DAILY_UPDATE = r"D:/workbuddyspace/tulong_daily_update.py"
PIT_BUILD = r"D:/zcodesj/build_factor_pit_20260815.py"
MAIN_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
REPORT = Path(r"D:/zcodesj/reports/pit_h1_refresh_latest.md")
CW_MIN_BYTES = 1_000_000  # cw全量财报正常~13MB，20字节为占位


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)


def cw_ready() -> tuple[bool, str]:
    if not CW_H1.exists():
        return False, "gpcw20260630.dat 不存在"
    size = CW_H1.stat().st_size
    if size < CW_MIN_BYTES:
        return False, f"gpcw20260630.dat 仅 {size} 字节(占位文件), 通达信尚未下载H1财务数据"
    return True, f"gpcw20260630.dat {size/1e6:.1f}MB 就绪"


def h1_already_in_pit() -> int:
    """factor_pit.db 中 H1(20260630) 分区行数"""
    try:
        con = sqlite3.connect(r"file:D:/zcodesj/factor_pit.db?mode=ro", uri=True)
        n = con.execute(
            "SELECT COUNT(*) FROM stock_factors_fundamental_pit_v2 WHERE report_date=20260630"
        ).fetchone()[0]
        con.close()
        return n
    except Exception:
        return -1


def run(cmd: list[str], timeout: int = 3600) -> tuple[bool, str]:
    log(f"RUN: python {' '.join(os.path.basename(c) if c.endswith('.py') else c for c in cmd)}")
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    tail = (r.stdout or "")[-1500:]
    if r.returncode != 0:
        return False, tail + "\n" + (r.stderr or "")[-800:]
    return True, tail


def main() -> int:
    force = "--force" in sys.argv
    lines = [f"# H1 PIT 刷新报告 {datetime.now():%Y-%m-%d %H:%M}\n"]

    n0 = h1_already_in_pit()
    lines.append(f"- 刷新前 factor_pit_v2 H1分区行数: {n0}")
    log(f"刷新前 PIT H1 行数: {n0}")

    ok, why = cw_ready()
    lines.append(f"- cw文件检查: {'✅' if ok else '❌'} {why}")
    if not ok and not force:
        lines.append("\n**结论: H1财务数据未就绪。请在通达信客户端执行 财务数据下载 后重跑本脚本。**")
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        log("H1数据未就绪，退出(幂等)")
        print("\n".join(lines[-3:]))
        return 2

    # 1. cw -> stock_financial
    ok1, out1 = run([PARSER], timeout=3600)
    lines.append(f"\n## 1. TDX财务解析 {'✅' if ok1 else '❌'}\n```\n{out1[-600:]}\n```")
    if not ok1:
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        return 1

    # 2. Phase4 -> stock_factors_fundamental
    ok2, out2 = run([DAILY_UPDATE, "--phase", "4"], timeout=7200)
    lines.append(f"\n## 2. Phase4基本面因子 {'✅' if ok2 else '❌'}\n```\n{out2[-600:]}\n```")
    if not ok2:
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        return 1

    # 3. PIT v2 增量构建
    ok3, out3 = run([PIT_BUILD, "--v2"], timeout=7200)
    lines.append(f"\n## 3. factor_pit v2 增量构建 {'✅' if ok3 else '❌'}\n```\n{out3[-600:]}\n```")

    n1 = h1_already_in_pit()
    lines.append(f"\n## 结果\n- 刷新后 factor_pit_v2 H1分区行数: {n1} (前: {n0})")
    if n1 > 3000:
        lines.append("- 判定: ✅ H1分区覆盖正常(>3000只)")
    elif n1 > 0:
        lines.append("- 判定: ⚠️ H1分区覆盖偏少, 披露仍在进行中, 可后续重跑(幂等)")
    else:
        lines.append("- 判定: ❌ H1分区仍为空, 需排查上游")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    log(f"报告写入 {REPORT}")
    # 2026-08-24 P1修复: 链路ok但H1分区为空/过少也必须非零退出——否则9/1月度引擎
    # 只拿到Q1而调度层认为刷新成功（静默断流）。
    return 0 if (ok3 and n1 > 3000) else 1


if __name__ == "__main__":
    sys.exit(main())
