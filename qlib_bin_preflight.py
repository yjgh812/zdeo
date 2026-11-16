# -*- coding: utf-8 -*-
"""
qlib_bin_preflight.py — 上游数据完整性闸门（根治"静默错误信号"）
================================================================================
背景: 8/22 全量重建修复了 qlib_bin 错位/滞后(4918 只尾部错位一天)。但信号管线本身
  没有任何上游数据检查 —— 若 qlib_bin 再次陈旧/错位, MASTER/HIST 会静默用错特征出信号。

本闸门: 在信号管线算特征前 检测 qlib_bin 是否 ①到目标日 ②与 raw 源逐点对齐(防错位)。
  失败 → 自动自愈(跑 qlib_bin_update_daily.py, 幂等) → 再查; 仍失败 → 返回 False,
  调用方(信号脚本)必须拒绝出信号(退出码2), 绝不静默输出。

用法(独立): python qlib_bin_preflight.py [--date YYYY-MM-DD]   # 0=通过 2=失败
作为模块:  from qlib_bin_preflight import run_preflight; ok, msg = run_preflight(target)
"""
import os
import sqlite3
import subprocess
import sys
import time

import numpy as np

QLIB = r"D:\workbuddyspace\qlib_data\qlib_bin"
META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
# 2026-08-22 完全根治: qlib_bin 基准切 qfq(系统正源), 探针对照源用 qfq 库
SRC_DB, SRC_TBL = QFQ_DB, "stock_daily_meta_qfq"
# 高流动性探针(巨无霸每日必有成交, 极少停牌; 单只无数据时跳过不判失败)
PROBE = ["600519", "600000", "000001", "600036", "601318", "300750", "300059"]
MIN_CSI300_AT_TARGET = 280   # csi300 ~300 只, 目标日有效数(剔停牌)应 >= 280


def _norm_date(s: str) -> str:
    """归一化日期为 YYYY-MM-DD; 兼容 YYYYMMDD 紧凑输入。"""
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _day_cal() -> list:
    with open(rf"{QLIB}\calendars\day.txt", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def _check(target: str):
    """返回 (ok, msg)。target 兼容 YYYY-MM-DD / YYYYMMDD"""
    target = _norm_date(target)
    cal = _day_cal()
    if not cal:
        return False, "day.txt 为空/缺失"
    if cal[-1] != target:
        return False, f"day.txt 末位 {cal[-1]} != 目标 {target}"
    if target not in cal:
        return False, f"目标 {target} 不在日历"

    # ① csi300 在 target 的有效 close 数(经 qlib 读 bin, 能反映滞后/长度不足)
    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=QLIB, region=REG_CN, kernels=1)
    from qlib.data import D
    f = D.features(D.instruments("csi300"), ["$close"], start_time=target, end_time=target)
    n = len(f)
    if n < MIN_CSI300_AT_TARGET:
        return False, f"csi300@{target} 仅 {n} 只有效(阈值{MIN_CSI300_AT_TARGET}, 疑似滞后/长度不足)"

    # ② 探针: bin close@target == raw 源逐点(防 8/14 槽存 8/21 值 这类错位)
    pos = cal.index(target)
    con = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    bad = []
    for code in PROBE:
        inst = ("sh" if code.startswith("6") else "sz") + code
        r_raw = con.execute(f"SELECT close FROM {SRC_TBL} WHERE code=? AND date=?",
                            (code, target.replace("-", ""))).fetchone()
        if r_raw is None:      # 该股 target 无交易(理论极罕见), 跳过
            continue
        bp = os.path.join(QLIB, "features", inst, "close.day.bin")
        if not os.path.exists(bp):
            bad.append((inst, "缺bin", float(r_raw[0])))
            continue
        raw_bin = np.fromfile(bp, dtype=np.float32)
        if len(raw_bin) < 2:
            bad.append((inst, "bin空", float(r_raw[0])))
            continue
        hdr = int(raw_bin[0])
        if pos - hdr < 0 or pos - hdr >= len(raw_bin) - 1:
            bad.append((inst, "bin长度不足", float(r_raw[0])))
            continue
        b = float(raw_bin[1 + pos - hdr])
        if abs(b - float(r_raw[0])) > 1e-3:
            bad.append((inst, f"bin={b:.4f} raw={float(r_raw[0]):.4f}", None))
    con.close()
    if bad:
        return False, f"探针错位: {bad}"
    return True, f"OK: csi300@{target} 共{n}只, 探针全部对齐"


def run_preflight(target: str, auto_heal: bool = True):
    """返回 (ok, msg)。失败先自愈(qlib_bin_update_daily.py)再查一次。"""
    ok, msg = _check(target)
    if ok:
        return True, msg
    if not auto_heal:
        return False, msg
    print(f"[preflight] 未过: {msg} → 尝试自愈(qlib_bin_update_daily.py)...", flush=True)
    try:
        r = subprocess.run([sys.executable, r"D:\zcodesj\qlib_bin_update_daily.py"],
                           capture_output=True, text=True, timeout=1800)
        tail = (r.stdout or "").strip().splitlines()
        err_tail = (r.stderr or "").strip().splitlines()
        print(f"[preflight] 自愈 rc={r.returncode} | stdout: {'; '.join(tail[-3:]) if tail else ''} | stderr: {'; '.join(err_tail[-3:]) if err_tail else ''}", flush=True)
    except Exception as e:
        return False, f"自愈异常 {type(e).__name__}: {e}"
    ok2, msg2 = _check(target)
    if ok2:
        return True, f"自愈后通过: {msg2}"
    return False, f"自愈后仍失败: 前[{msg}] 后[{msg2}]"


if __name__ == "__main__":
    target = None
    for a in sys.argv[1:]:
        if a.startswith("--date"):
            target = a.split("=", 1)[1]
    if target is None:
        with open(rf"{QLIB}\calendars\day.txt", encoding="utf-8") as f:
            target = [l.strip() for l in f if l.strip()][-1]
    t0 = time.time()
    ok, msg = run_preflight(target)
    print(f"[preflight] {target}: {msg} ({time.time()-t0:.0f}s)")
    sys.exit(0 if ok else 2)
