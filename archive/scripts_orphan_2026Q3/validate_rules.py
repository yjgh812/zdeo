import sqlite3
DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
con = sqlite3.connect(DB)
c = con.cursor()

lat = c.execute('SELECT MAX(date) FROM stock_daily_hsj').fetchone()[0]
print('Latest date:', lat)

closes = {}
for r in c.execute('SELECT code, close FROM stock_daily_hsj WHERE date = ?', (lat,)).fetchall():
    closes[r[0]] = r[1]

prev_date = c.execute('SELECT MAX(date) FROM stock_daily_hsj WHERE date < ?', (lat,)).fetchone()[0]
prev_closes = {}
for r in c.execute('SELECT code, close FROM stock_daily_hsj WHERE date = ?', (prev_date,)).fetchall():
    prev_closes[r[0]] = r[1]

# Price rule
price_groups = {}
for code, close in closes.items():
    if close <= 0:
        continue
    bucket = '<=10' if close <= 10 else ('<=20' if close <= 20 else ('<=30' if close <= 30 else ('<=50' if close <= 50 else '>50')))
    if bucket not in price_groups:
        price_groups[bucket] = {'rets': [], 'count': 0}
    if code in prev_closes and prev_closes[code] > 0:
        ret = (close - prev_closes[code]) / prev_closes[code] * 100
        price_groups[bucket]['rets'].append(ret)
        price_groups[bucket]['count'] += 1

print('\n=== S-3 PRICE RULE (30元上限) ===')
for b in ['<=10', '<=20', '<=30', '<=50', '>50']:
    if b in price_groups:
        g = price_groups[b]
        avg = sum(g['rets']) / len(g['rets']) if g['rets'] else 0
        wins = sum(1 for r in g['rets'] if r > 0)
        print(f'  Price {b}: {g["count"]} stocks, avg {avg:.2f}%, win {wins/g["count"]*100:.0f}%')

# R4 Defense
print('\n=== R4 DEFENSE SECTOR CHECK ===')
for sec in ['银行', '煤炭', '电力', '铁路', '石油', '电信', '航运']:
    rows = []
    for code, close in closes.items():
        if code in prev_closes and prev_closes[code] > 0:
            info = c.execute('SELECT industry FROM stock_info WHERE code = ?', (code,)).fetchone()
            if info and sec in (info[0] or ''):
                ret = (close - prev_closes[code]) / prev_closes[code] * 100
                rows.append(ret)
    if len(rows) >= 3:
        avg = sum(rows) / len(rows)
        wins = sum(1 for r in rows if r > 0)
        print(f'  {sec}: {len(rows)} stocks, avg {avg:.2f}%, win {wins/len(rows)*100:.0f}%')

con.close()

# S-5 Stop-loss
print('\n=== S-5 STOP LOSS VALIDATION ===')
adb = sqlite3.connect('D:/zcodesj/attribution/attribution.db')
ac = adb.cursor()
pnl = [r[0] for r in ac.execute('SELECT pnl_pct FROM trade_attribution').fetchall() if r[0] is not None]

for th in [3, 5, 8, 10, 15]:
    cnt = sum(1 for p in pnl if p <= -th/100)
    pct = cnt / len(pnl) * 100 if pnl else 0
    print(f'  -{th}%: {cnt} trades ({pct:.1f}%)')

total_loss = ac.execute('SELECT SUM(pnl_amount) FROM trade_attribution WHERE pnl_amount < 0').fetchone()[0] or 0
loss_over_8 = ac.execute('SELECT SUM(pnl_amount) FROM trade_attribution WHERE pnl_pct <= -0.08').fetchone()[0] or 0
if total_loss != 0:
    print(f'  Total loss: {total_loss:.0f}')
    print(f'  Loss over -8%: {loss_over_8:.0f} ({loss_over_8/total_loss*100:.1f}%)')

adb.close()
print('\n=== VALIDATION COMPLETE ===')
