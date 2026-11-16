import os

p = "D:/zcodesj/guarded_trade_executor.py"
c = open(p, "r", encoding="utf-8").read()

# 1. 在卖出链 Step 2 前插入趋势自动获取
old = '''    # Step 2: pre_sell_check 防卖飞
    print(f"\n🔐 Step 2/5: 防卖飞四检查...")
    sc_args = ["--code", code, "--name", name]'''

new = '''    # Step 2: pre_sell_check 防卖飞（含C5趋势验证）
    print(f"\n🔐 Step 2/5: 防卖飞四检查+C5趋势验证...")
    
    # 自动获取趋势数据（trend_checker, 防YTD误判铁律）
    trend_ma5 = trend_ma20 = None
    try:
        import subprocess as sp
        tr = sp.run(
            [sys.executable, r"D:\zcodesj\trend_checker.py", "--code", code],
            capture_output=True, text=True, timeout=30,
        )
        tr_out = tr.stdout
        import re
        m5 = re.search(r"MA5=([\d.]+)", tr_out)
        m20 = re.search(r"MA20=([\d.]+)", tr_out)
        if m5: trend_ma5 = float(m5.group(1))
        if m20: trend_ma20 = float(m20.group(1))
        print(f"  📈 趋势数据: MA5={trend_ma5} MA20={trend_ma20}")
    except Exception as e:
        print(f"  ⚠️ 趋势获取失败: {e}")
    
    sc_args = ["--code", code, "--name", name]
    if trend_ma5 is not None:
        sc_args += ["--trend-ma5", str(trend_ma5)]
    if trend_ma20 is not None:
        sc_args += ["--trend-ma20", str(trend_ma20)]'''

c = c.replace(old, new)

open(p, "w", encoding="utf-8").write(c)
print("guarded_trade_executor: C5趋势验证已自动接入（卖出自动拉MA5/MA20）")
