# -*- coding: utf-8 -*-
"""verify_lstm_determinism_20261028.py — 迭代验证: 同种子两次重训, 排序必须逐字节一致"""
import importlib.util, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "w23", os.path.join(ROOT, "analysis", "revival_wave23_scanner_20261028.py"))
w23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w23)

OLD5 = {"300006", "300413", "300369", "603626", "300296"}
series, dates = w23.load_qfq()
uni, held = w23.base_universe(series, list(OLD5))

runs = []
for i in (1, 2):
    picks, meta = w23.m_lstm(series, uni, {"T": dates[-1], "dates": dates})
    runs.append((picks, meta))
    print(f"run{i}: n_train={meta.get('n_train')} 前4={[(c, r.split('=')[-1]) for c, r in picks[:4]]}")

identical = runs[0][0] == runs[1][0] and runs[0][1].get("n_train") == runs[1][1].get("n_train")
print("确定性验证:", "PASS 两轮逐字节一致 ✓" if identical else "FAIL 两轮不一致 ✗")
if not identical:
    for i, ((a, _), (b, _)) in enumerate(zip(runs[0][0][:12], runs[1][0][:12]), 1):
        if a != b:
            print(f"  首个差异@{i}: {a} vs {b}")
sys.exit(0 if identical else 2)
