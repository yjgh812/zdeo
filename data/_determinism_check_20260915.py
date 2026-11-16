# -*- coding: utf-8 -*-
"""复跑非确定性验证协议判读器 (2026-09-15, SOP(三十)①登记的 PYTHONHASHSEED=0 双跑)。
前置: 三输入库 mtime 均 <= 9/14 20:19 (即 9/14 23:59 基准跑之后零写入, 输入状态与基准跑一致)。
判读表:
  A==B 且 A!=REF(散点同构) -> 哈希序/跨进程非确定性实锤, 修复=固定种子(零代码)
  A==B==REF               -> 当前环境完全确定, 9/12 vs 9/14 漂移源=窗口内输入变更(唯一候选 fundamental_data.db 9/14 21:13)
  A!=B                    -> 深层非确定性(调度/IO序), 升级排查
打印仅 ASCII。
"""
import glob
import hashlib
import os
import sys

import pandas as pd

A = r"D:\zcodesj\reports\drift_check_20260915seed0a_0914end"
B = r"D:\zcodesj\reports\drift_check_20260915seed0b_0914end"
REF = r"D:\zcodesj\reports\drift_check_20260914e_0914end"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_hashes(d):
    out = {}
    for p in glob.glob(os.path.join(d, "*")):
        b = os.path.basename(p)
        if os.path.isfile(p) and (b.endswith(".csv") or b.endswith(".json")):
            out[b] = md5(p)
    return out


def cmp_dirs(d1, d2, tag):
    h1, h2 = dir_hashes(d1), dir_hashes(d2)
    common = sorted(set(h1) & set(h2))
    diff = [b for b in common if h1[b] != h2[b]]
    only1 = sorted(set(h1) - set(h2))
    only2 = sorted(set(h2) - set(h1))
    print("[%s] common=%d diff=%d only1=%d only2=%d" % (tag, len(common), len(diff), len(only1), len(only2)))
    if diff:
        print("  diff_files:", ", ".join(diff[:12]) + (" ..." if len(diff) > 12 else ""))
    if only1 or only2:
        print("  only1:", only1[:4], "only2:", only2[:4])
    return set(common), set(diff)


def combo_drift(d1, d2, tag):
    try:
        e1 = pd.read_csv(os.path.join(d1, "portfolio_combo_equity.csv"))
        e2 = pd.read_csv(os.path.join(d2, "portfolio_combo_equity.csv"))
        m = e1.merge(e2, on="date", suffixes=("_1", "_2"))
        m["rel"] = (m["equity_2"] - m["equity_1"]) / m["equity_1"] * 100
        nz = m[m["rel"].abs() > 1e-9]
        print("[%s] combo overlap=%d first_drift=%s max_rel=%.4f%% end_rel=%.4f%%"
              % (tag, len(m), nz["date"].iloc[0] if len(nz) else "NONE",
                 nz["rel"].abs().max() if len(nz) else 0.0, m["rel"].iloc[-1]))
    except Exception as e:
        print("[%s] combo ERR %s" % (tag, str(e)[:60]))


def bp_ep_key_diff(d1, d2, tag):
    try:
        t1 = pd.read_csv(os.path.join(d1, "bp_ep_core_trades_full.csv"), encoding="utf-8-sig")
        t2 = pd.read_csv(os.path.join(d2, "bp_ep_core_trades_full.csv"), encoding="utf-8-sig")
        k = ["买入日期", "卖出日期", "股票代码"]
        s1 = set(map(tuple, t1[k].values))
        s2 = set(map(tuple, t2[k].values))
        print("[%s] bp_ep old=%d new=%d only1=%d only2=%d" % (tag, len(t1), len(t2), len(s1 - s2), len(s2 - s1)))
    except Exception as e:
        print("[%s] bp_ep ERR %s" % (tag, str(e)[:60]))


def main():
    for d in (A, B, REF):
        if not os.path.isdir(d):
            sys.stderr.write("missing dir: %s\n" % d)
            return 2
    cb, db_ = cmp_dirs(A, B, "A_vs_B(seed0 vs seed0)")
    cr, dr = cmp_dirs(A, REF, "A_vs_REF(seed0 vs random914)")
    cmp_dirs(B, REF, "B_vs_REF(seed0 vs random914)")
    print()
    combo_drift(A, B, "A_vs_B")
    combo_drift(A, REF, "A_vs_REF")
    bp_ep_key_diff(A, B, "A_vs_B")
    bp_ep_key_diff(A, REF, "A_vs_REF")
    print()
    if db_ == set() and dr:
        print("VERDICT: H_hashseed CONFIRMED (fixed seed deterministic; differs from random-seed run)")
    elif db_ == set() and not dr:
        print("VERDICT: fully deterministic now; 9/12->9/14 drift must be window input change (check fundamental_data.db hist)")
    else:
        print("VERDICT: deep nondeterminism (A!=B) — escalate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
