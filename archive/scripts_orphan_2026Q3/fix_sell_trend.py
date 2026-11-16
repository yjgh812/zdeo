import os

p = "D:/zcodesj/pre_sell_check.py"
c = open(p, "r", encoding="utf-8").read()

# 1. 在check函数中加入趋势验证（作为C5检查）
old = '''    # 特殊规则: 好想你条款——PE<5 且 股息>4% 同时命中=强制保留
    pe_extreme = pe is not None and 0 < pe < 5'''

new = '''    # C5 趋势验证（铁律: YTD不能作为趋势判定依据，2026-08-14广哈通信卖飞闭环）
    trend_verdict = None
    if args.trend_ma5 and args.trend_ma20 and args.price:
        ma5, ma20 = args.trend_ma5, args.trend_ma20
        if args.price > ma5 > ma20:
            holds.append(f"C5趋势验证: 价格{args.price} > MA5({ma5}) > MA20({ma20}) → 趋势完好，建议保留")
        elif args.price < ma20:
            holds.append(f"C5趋势验证: 价格{args.price} < MA20({ma20}) → 20日线下方，趋势偏弱可卖")
        else:
            holds.append(f"C5趋势验证: 价格{args.price} vs MA5({ma5})/MA20({ma20}) → 中性，谨慎")
    elif args.ytd is not None and args.price:
        # 若只提供YTD → 提示不能作为依据
        holds.append(f"⚠️ YTD={args.ytd}% 不能作为趋势判定依据（铁律），请提供MA5/MA20后再判定")

    # 特殊规则: 好想你条款——PE<5 且 股息>4% 同时命中=强制保留
    pe_extreme = pe is not None and 0 < pe < 5'''

c = c.replace(old, new)

# 2. 添加CLI参数
old_args = '''    p.add_argument("--loss-pct", type=float, help="当前亏损百分比(负值)如-0.095（S-2尾盘卖出锁用）")'''
new_args = '''    p.add_argument("--loss-pct", type=float, help="当前亏损百分比(负值)如-0.095")
    p.add_argument("--trend-ma5", type=float, help="MA5均线值（C5趋势验证）")
    p.add_argument("--trend-ma20", type=float, help="MA20均线值（C5趋势验证）")
    p.add_argument("--ytd", type=float, help="YTD涨跌幅% — 仅参考，不作为趋势判定依据")'''
c = c.replace(old_args, new_args)

# 3. 更新docstring
c = c.replace("四检查（任一命中即 WARN）", "五检查（任一命中即 WARN，C5为趋势验证）")
c = c.replace("""  C4 主力流入: 近5日主力资金净流入 > 0""", """  C4 主力流入: 近5日主力资金净流入 > 0
  C5 趋势验证: 价格 vs MA5/MA20 排列（YTD禁用铁律）""")

open(p, "w", encoding="utf-8").write(c)
print("pre_sell_check.py: C5趋势验证已接入")
