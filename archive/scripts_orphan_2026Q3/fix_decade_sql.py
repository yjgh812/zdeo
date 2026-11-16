# Fix SQL issues in decade_backtest_validator.py
p = 'D:/zcodesj/decade_backtest_validator.py'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()

# Fix R7: date2 -> date (wrong column alias)
c = c.replace('date2 < s.date', 'date < s.date')

# Fix R6: wrong result indices
# The SELECT returns: code(0) date(1) close(2) high(3) low(4) pos_ratio(5) nd_close(6)
c = c.replace('pos_ratio = r[4]', 'pos_ratio = r[5]')
c = c.replace('nd_close = r[5]', 'nd_close = r[6]')

with open(p, 'w', encoding='utf-8') as f:
    f.write(c)
print('SQL fixes applied')
