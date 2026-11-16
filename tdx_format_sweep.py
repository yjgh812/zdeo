# -*- coding: utf-8 -*-
"""
tdx_format_sweep.py — 通达信二进制解析格式串漂移扫描（防回归）
================================================================
扫描 D:\\zcodesj 与 D:\\workbuddyspace 下所有 .py，找出解析通达信本地数据
（.day 日线 / .lc1/.lc5 分钟线）的 struct 格式串与 numpy dtype，
与规范口径（tdx_reader.py 单一真源）比对，标记漂移点。

规范口径（2026-08-31 全量验证，与 mootdx TdxLCMinBarReader 一致）：
  .day    <IIIIIfII   date,open,high,low,close(int×100),amount(float32),volume,reserved
  .lc1/5  <HHfffffII  date_num,time_min,open,high,low,close,amount(float32),volume,reserved

识别两种形态：
  A) struct 格式串字面量  "<HHfffffII>" / "<IIIIIfII>"  （多字段，非 numpy 单字段类型）
  B) numpy dtype 字段      ('date','<u4'), ... → 字段类型序列映射为 struct 编码后判定

用法：
  python tdx_format_sweep.py            # 扫全部
  python tdx_format_sweep.py --roots A B  # 指定根目录
退出码：0=无漂移；1=发现漂移/疑似点（需人工复核）。
"""
from __future__ import annotations

import argparse
import re
import struct as _s
from pathlib import Path

ROOTS = [Path(r"D:\zcodesj"), Path(r"D:\workbuddyspace")]

# 规范口径
DAY_OK = "<IIIIIfII"
MIN_OK = "<HHfffffII"

# 已知漂移/错误格式（勘察确认）
KNOWN_BAD = {
    "<HHffffIII": "amount 被当 uint32 读(应为 float32), 且多解一个 uint32 —— sync_tdx_minute 旧格式",
    "<HHffffdI": "amount 被当 double 读(应为 float32) —— sync_tdx_minute 文档串",
    "<HHfffff": "只解 24 字节, 缺 volume/reserved —— chanlun_engine 旧实现(已迁移)",
    "<IIIIIIII": "amount 被当 uint32 读(应为 float32) —— build_qfq_meta_tdx_20260811 旧版",
}

# 已审计死代码登记（2026-08-31 活性审计：mtime 停在 8/05-8/09、零 import 引用、
# 不在 SOP/cron 台账）。**2026-08-31 主人裁决升级：修复优化**——其中 17 处
# `<IIIIIIII` 已修复为 `<IIIIIfII`（amount 对拍验证：修复前畸变值 1,328,742,056 →
# 修复后 3,003,033,600 与 tdx_reader 权威一致）+ sync_tdx_minute 三修（格式串+/100 语义
# +docstring）；**仅剩 temp/probe_lc1*.py 2 处豁免**（格式探针脚本，`<IIIIIIII`
# 是故意列举的实验假设对象，修复会破坏脚本意图）。文件仍为弃用死代码，
# 禁止复活进生产；复活前必先改用 tdx_reader。
# 登记文件的 DRIFT 降级为 DEAD-REG（不判失败）。
AUDITED_DEAD = {
    r"D:\workbuddyspace\factor_engine\core\tech_analysis\main_wave_signals.py",
    r"D:\workbuddyspace\factor_engine\Scripts\strategy_main_wave.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\complete_backtest_system.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step1_data_filter.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step2_factor_mining.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step4_simulation_trading.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step4_simulation_v2.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step4_simulation_v3.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step5_automation.py",
    r"D:\workbuddyspace\factor_engine\Scripts\high_winrate_system\step5_automation_fast.py",
    r"D:\workbuddyspace\factor_engine\Scripts\optimization\optimization_final.py",
    r"D:\workbuddyspace\factor_engine\Scripts\optimization\optimization_v2.py",
    r"D:\workbuddyspace\factor_engine\Scripts\optimization\optimization_v3.py",
    r"D:\workbuddyspace\factor_engine\Scripts\optimization\unified_optimizer.py",
    r"D:\workbuddyspace\temp\tdx_metrics.py",
    r"D:\workbuddyspace\temp\probe_lc1.py",
    r"D:\workbuddyspace\temp\probe_lc1b.py",
    r"D:\zcodesj\build_qfq_meta_tdx_20260811.py",
    r"D:\workbuddyspace\sync_tdx_minute.py",
    # 2026-09-10 D2(第六轮审计): 错位内联解析器登记 DEAD-REG——字段互换
    # (raw[20:24] vol/amount 错位) + Excel 序列号日期解读(偏移约 11 个月) + 旧日
    # 数据被日期过滤丢弃; 复活前必先改走 tdx_reader.read_min1。
    r"D:\workbuddyspace\factor_engine\core\multi_factor\minute_smart_money.py",
    r"D:\workbuddyspace\factor_engine\core\minute_backtest.py",
    # tdx_cli .day 内联解析以 7 变量接 6 元组必抛 ValueError(实际已坏)
    r"D:\zcodesj\tdx_cli.py",
}

# numpy 字段类型 -> struct 编码（用于把 dtype 字段序列映射成等价格式串判定）
_NP2ST = {"u4": "I", "u2": "H", "f4": "f", "i4": "i", "f2": "h", "i2": "h",
          "u1": "B", "i1": "b", "f8": "d"}

# A) struct 多字段格式串: "<IIIIIfII" 等（至少 3 字符且非纯单字段 numpy 类型）
_FMT_RE = re.compile(r"""["'](<[0-9A-Za-z]{3,})["']""")
# B) numpy dtype 字段: ('name', '<u4') 连续出现
_DTYPE_FIELD_RE = re.compile(r"""\(\s*['"][A-Za-z_]+['"]\s*,\s*['"](<[0-9A-Za-z]+)['"]\s*\)""")
# 触发判定: 行内含 TDX 相关词（含 struct 调用本身）
_CTX_RE = re.compile(r'(lday|fzline|minline|\.lc1|\.lc5|\.day|DAY_DTYPE|MIN_DTYPE|'
                     r'_LC5|_LC1|frombuffer|vipdoc|通达信|struct\.|unpack)', re.IGNORECASE)


def classify(fmt: str) -> tuple[str, str]:
    """返回 (判定, 说明)。判定: OK | DRIFT | OTHER"""
    if fmt in KNOWN_BAD:
        return "DRIFT", KNOWN_BAD[fmt]
    if fmt == DAY_OK or fmt == MIN_OK:
        return "OK", "规范口径"
    try:
        sz = _s.calcsize(fmt)
    except Exception:
        return "OTHER", "无法解析"
    if sz == 32:
        return "OTHER", "32字节但非规范串(需人工复核)"
    return "OTHER", f"{sz}字节(非32定长, 或为其它用途)"


def dtype_to_fmt(field_types: list[str]) -> str:
    """numpy 字段类型序列 -> struct 等价格式串；含未知类型返回空。"""
    chars = []
    for t in field_types:
        c = _NP2ST.get(t)
        if c is None:
            return ""
        chars.append(c)
    return "<" + "".join(chars)


def sweep_file(path: Path) -> list[tuple[int, str, str, str]]:
    """返回 [(lineno, fmt, 判定, 说明)]"""
    hits = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return hits
    lines = text.splitlines()
    has_ctx = bool(_CTX_RE.search(text))

    # A) struct 多字段格式串（逐行）
    for i, ln in enumerate(lines, 1):
        if not _CTX_RE.search(ln):
            continue
        for m in _FMT_RE.finditer(ln):
            fmt = m.group(1)
            if not fmt.startswith("<"):
                continue
            verdict, note = classify(fmt)
            if verdict != "OK":
                hits.append((i, fmt, verdict, note))

    # B) numpy dtype 字段序列（文件级聚合）
    if has_ctx:
        pairs = _DTYPE_FIELD_RE.findall(text)   # 单捕获组 -> list[str]（字段类型）
        if len(pairs) >= 6:
            field_types = list(pairs)
            fake = dtype_to_fmt(field_types)
            if fake:
                verdict, note = classify(fake)
                if verdict in ("DRIFT", "OTHER"):
                    ln = text[:text.find(pairs[0])].count("\n") + 1
                    hits.append((ln, fake, verdict, note + " (numpy dtype 字段)"))
    return hits


def main():
    ap = argparse.ArgumentParser(description="TDX 格式串漂移扫描")
    ap.add_argument("--roots", nargs="*", default=[str(r) for r in ROOTS])
    args = ap.parse_args()

    total_scan = total_drift = total_other = total_deadreg = 0
    drift_lines: list[tuple[str, int, str, str, str]] = []
    for root_s in args.roots:
        root = Path(root_s)
        if not root.is_dir():
            print(f"[skip] 目录不存在: {root}")
            continue
        for p in sorted(root.rglob("*.py")):
            if any(part in {"node_modules", ".venv", "site-packages", "archive",
                            "Claw", "workspaces", "__pycache__", ".zcode",
                            "comfyui"}
                   for part in p.parts):
                continue
            hits = sweep_file(p)
            if not hits:
                continue
            total_scan += 1
            for lineno, fmt, verdict, note in hits:
                if verdict == "DRIFT" and str(p) in AUDITED_DEAD:
                    # 已审计死代码: 降级登记, 不判失败(复活前必先修)
                    total_deadreg += 1
                    drift_lines.append((str(p), lineno, fmt, "DEAD-REG",
                                        note + " | 已审计死代码(8/31), 复活前必先修/改用 tdx_reader"))
                elif verdict == "DRIFT":
                    total_drift += 1
                    drift_lines.append((str(p), lineno, fmt, verdict, note))
                else:
                    total_other += 1
                    drift_lines.append((str(p), lineno, fmt, verdict, note))

    print(f"扫描到 {total_scan} 个含疑似 TDX 格式串的文件")
    print(f"判定: DRIFT={total_drift}(活代码,须修), DEAD-REG={total_deadreg}(已审计死代码,复活前必先修), 其它待复核={total_other}")
    for f, ln, fmt, v, note in drift_lines:
        print(f"  [{v}] {f}:{ln}  {fmt}  —  {note}")
    if total_drift:
        print("\n=== DRIFT 汇总 ===")
        seen = {}
        for f, ln, fmt, v, note in drift_lines:
            if v == "DRIFT":
                seen.setdefault(fmt, []).append(f"{f}:{ln}")
        for fmt, locs in seen.items():
            print(f"  {fmt}: {len(locs)} 处 — {locs[0]}" + (f" 等{len(locs)}处" if len(locs) > 1 else ""))
    print("\nDONE")
    return 1 if total_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
