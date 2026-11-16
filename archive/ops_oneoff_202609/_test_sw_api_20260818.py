# -*- coding: utf-8 -*-
"""实测 akshare 申万行业指数接口可用性"""
import sys, time

PYDIR = r"D:\workbuddyspace\factor_engine\Scripts\python.exe"
import importlib.util
print("测试解释器:", sys.executable)
try:
    import akshare as ak
    print("akshare 版本:", ak.__version__)
except ImportError:
    print("当前解释器无 akshare，尝试 workbuddyspace env ...")
    import subprocess
    r = subprocess.run([PYDIR, __file__], capture_output=True, text=True, timeout=90)
    print(r.stdout)
    print(r.stderr)
    sys.exit(0)

# 1. 申万行业指数历史(akshare 经典接口)
t0 = time.time()
try:
    df = ak.index_hist_sw(symbol="801010", period="day")
    print("index_hist_sw(801010 农林牧渔): ✅ 行数", len(df), "最新", df.iloc[-1, 0] if len(df) else "-", f"{time.time()-t0:.1f}s")
except Exception as e:
    print("index_hist_sw: ❌", str(e)[:150])

# 2. 申万行业指数实时/行情接口
try:
    df2 = ak.sw_index_spot()
    print("sw_index_spot: ✅ 行数", len(df2))
except Exception as e:
    print("sw_index_spot: ❌", str(e)[:120])
