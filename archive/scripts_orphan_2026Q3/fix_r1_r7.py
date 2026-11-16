p = 'D:/zcodesj/pre_trade_check_v2.py'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()

c = c.replace(
    '"R1追高提示: 60日分位>70% + 非涨停价买 + 主升末端，注意高位风险"',
    '"R1追高拒绝: 60日分位>70% + 非涨停价买 + 主升末端，高位接盘风险"'
)

c = c.replace(
    'warns.append(f"R7超买提示(涨停价追入)',
    'rejects.append(f"R7超买拒绝(涨停价追入)'
)
c = c.replace(
    'warns.append(f"R7超买提示(高位追入)',
    'rejects.append(f"R7超买拒绝(高位追入)'
)
c = c.replace(
    'R7超买提示(涨停价追入): 买入价',
    'R7超买拒绝(涨停价追入): 买入价'
)
c = c.replace(
    'R7超买提示(高位追入): 买入价涨幅',
    'R7超买拒绝(高位追入): 买入价涨幅'
)

with open(p, 'w', encoding='utf-8') as f:
    f.write(c)
print('R1 text fixed, R7 restored to REJECT')
