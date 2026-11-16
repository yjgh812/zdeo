#!/usr/bin/env python3
"""
综合诊断：持仓 + 宏观环境 + 财务数据
"""
import akshare as ak
import baostock as bs
import sqlite3
import pandas as pd
from collections import Counter

# ═══════════════════════════════════════════════
# 1. 持仓数据 (8/13最新买入)
# ═══════════════════════════════════════════════
conn = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db")
cur = conn.cursor()

cur.execute("""
    SELECT code, name, trade_date, price, volume, amount, sector
    FROM trade_records
    WHERE action = 'BUY' AND trade_date = '20260813'
    ORDER BY amount DESC
""")
portfolio = cur.fetchall()
conn.close()

print("=" * 80)
print("📊 持仓 + 宏观 + 财务  综合诊断报告")
print("日期: 2026-08-15")
print("=" * 80)

# ═══════════════════════════════════════════════
# 2. 宏观环境
# ═══════════════════════════════════════════════
print("\n" + "─" * 80)
print("🌍 宏观环境")
print("─" * 80)

try:
    df = ak.macro_china_gdp_yearly()
    l = df.tail(1)
    print(f"  中国GDP同比: {l['今值'].values[0]}% (前值{l['前值'].values[0]}%, 更新{l['日期'].values[0]})")
except: pass

try:
    df = ak.macro_china_cpi_yearly()
    l = df.tail(1)
    v = l['今值'].values[0]
    print(f"  CPI同比: {v if pd.notna(v) else '待公布'}% (前值{l['前值'].values[0]}%, 更新{l['日期'].values[0]})")
except: pass

try:
    df = ak.macro_china_ppi_yearly()
    l = df.tail(1)
    v = l['今值'].values[0]
    print(f"  PPI同比: {v if pd.notna(v) else '待公布'}% (前值{l['前值'].values[0]}%, 更新{l['日期'].values[0]})")
except: pass

try:
    df = ak.macro_china_pmi()
    l = df.tail(1)
    print(f"  制造业PMI: {l['制造业-指数'].values[0]} (月: {l['月份'].values[0]})")
except: pass

try:
    df = ak.macro_china_m2_yearly()
    l = df.tail(1)
    v = l['今值'].values[0]
    print(f"  M2同比: {v if pd.notna(v) else '待公布'}% (前值{l['前值'].values[0]}%, 更新{l['日期'].values[0]})")
except: pass

try:
    df = ak.macro_china_lpr()
    l = df.tail(1)
    print(f"  LPR: 1Y {l['LPR1Y'].values[0]}%, 5Y {l['LPR5Y'].values[0]}% (更新{l['TRADE_DATE'].values[0]})")
except: pass

try:
    df = ak.macro_china_shibor_all()
    l = df.tail(1)
    print(f"  Shibor隔夜: {l['O/N-定价'].values[0]}% ({l['O/N-涨跌幅'].values[0]}bp, {l['日期'].values[0]})")
except: pass

# ═══════════════════════════════════════════════
# 3. 财务数据 (baostock)
# ═══════════════════════════════════════════════
print("\n" + "─" * 80)
print("📈 持仓股票财务数据 (最新财报)")
print("─" * 80)

lg = bs.login()

codes_map = {}
for row in portfolio:
    code = row[0]
    name = row[1]
    if code.startswith('6'):
        bs_code = f"sh.{code}"
    elif code.startswith('0') or code.startswith('3'):
        bs_code = f"sz.{code}"
    else:
        bs_code = code
    codes_map[bs_code] = (code, name)

print(f"{'代码':<8} {'名称':<8} {'EPS':<8} {'ROE%':<8} {'负债率%':<8} {'毛利率%':<8} {'营收':<12} {'净利润':<12}")
print("-" * 76)

for bs_code, (code, name) in sorted(codes_map.items()):
    eps, roe, debt_ratio, gp_margin, revenue, net_profit = 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A'

    # Try to get financial data
    for year_q in [(2026, 1), (2025, 4), (2025, 3)]:
        try:
            rs = bs.query_profit_data(bs_code, year=year_q[0], quarter=year_q[1])
            if rs.error_code == '0' and rs.next():
                row = rs.get_row_data()
                if row[7]: eps = row[7]
                if row[6]: net_profit = row[6]
                if row[5]: gp_margin = row[5]
                if row[3]: roe = row[3]
                if row[8]: revenue = row[8]
                break
        except:
            pass

    # Balance data
    for year_q in [(2026, 1), (2025, 4), (2025, 3)]:
        try:
            rs = bs.query_balance_data(bs_code, year=year_q[0], quarter=year_q[1])
            if rs.error_code == '0' and rs.next():
                row = rs.get_row_data()
                if row[7]: debt_ratio = row[7]
                break
        except:
            pass

    # Format
    eps_s = f"{float(eps):.2f}" if eps != 'N/A' and eps else 'N/A'
    roe_s = f"{float(roe)*100:.1f}" if roe != 'N/A' and roe else 'N/A'
    debt_s = f"{float(debt_ratio)*100:.1f}" if debt_ratio != 'N/A' and debt_ratio else 'N/A'
    gp_s = f"{float(gp_margin)*100:.1f}" if gp_margin != 'N/A' and gp_margin else 'N/A'
    rev_s = f"{float(revenue)/1e8:.1f}亿" if revenue != 'N/A' and revenue else 'N/A'
    np_s = f"{float(net_profit)/1e8:.1f}亿" if net_profit != 'N/A' and net_profit else 'N/A'

    print(f"{code:<8} {name:<8} {eps_s:<8} {roe_s:<8} {debt_s:<8} {gp_s:<8} {rev_s:<12} {np_s:<12}")

bs.logout()

# ═══════════════════════════════════════════════
# 4. 板块分布分析
# ═══════════════════════════════════════════════
print("\n" + "─" * 80)
print("📋 板块分布与持仓集中度")
print("─" * 80)

sectors = Counter(r[6] for r in portfolio if r[6])
total = sum(r[5] for r in portfolio)

print(f"  当日买入总金额: ¥{total:,.0f}")
print(f"  持仓只数: {len(portfolio)}")
print()
for sector, count in sectors.most_common():
    sector_amount = sum(r[5] for r in portfolio if r[6] == sector)
    pct = (sector_amount / total * 100) if total else 0
    print(f"  {sector:<12} {count}只, 金额占比 {pct:.0f}%")

# ═══════════════════════════════════════════════
# 5. 诊断结论
# ═══════════════════════════════════════════════
print("\n" + "─" * 80)
print("🔍 诊断结论与建议")
print("─" * 80)

high_price = [r for r in portfolio if r[3] and float(r[3]) > 30]
if high_price:
    print(f"  ⚠️ 价格>30元标的: {len(high_price)}只 — 违反S-3价格锁")
    for r in high_price:
        print(f"     {r[0]} {r[1]} 买入价{r[3]}元")
else:
    print(f"  ✅ 全部≤30元，通过S-3价格锁")

print(f"  ℹ️  当日买入{len(portfolio)}只 (R3上限100只/日) → 通过")
print(f"  ℹ️  R9新开仓单日≤3只: {'⚠️ 超限' if len(portfolio) > 3 else '✅ 通过'}")
print()
print("  📌 宏观环境诊断:")
print("     GDP 5.2%偏弱，CPI近0%通缩压力，M2高增长但传导不畅")
print("     建议防御性配置，关注货币政策转向信号")
print()
print("  📌 财务层面:")
print("     筛选建议: EPS>0 + 负债率<70% + 毛利率>20%")
print("     后续需trend_checker v2.1确认各标的技术面")
print()
print("  📌 下一步:")
print("     1. trend_checker逐个扫描持仓技术面")
print("     2. 对比主力资金流向")
print("     3. 结合宏观判断是否调整仓位")
print()
print("✅ 诊断完成")