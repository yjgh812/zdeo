#!/usr/bin/env python3
"""涨停板块分析 v2.2 — 三项口径修正版
修正历史：
  v2.0 (8/15): 板块分阈值+名称查库+程序化候选
  v2.1 (8/15): 连板高度追溯（4板/3板/2板分层）
  v2.2 (8/15): ①新股前5交易日无涨跌幅限制→剔除 ②ST主板±5%口径(市场统计用)
               ③qfq交叉复核除权假信号(分红季防线) ④B股不涉及(主人8/15确认)
"""
import sqlite3
from collections import Counter, defaultdict

MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PRICE_LIMIT = 30.0
TARGET = '20260814'
NEW_STOCK_NO_LIMIT_DAYS = 5  # 注册制新股前5交易日无涨跌幅限制


def limit_pct(code: str, is_st: bool = False) -> float:
    """按板块返回涨停判定阈值（留0.1%容差）。
    注：B股不涉及（主人8/15确认）；北交所新股上市首日无限制（同剔除逻辑）。"""
    if code[:2] in ('30', '68'):
        return 19.9
    if code[0] in ('8', '4') and code[:2] != '68':
        return 29.9
    return 4.9 if is_st else 9.9  # ST主板±5%


with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
    # ── 1. 行情+名称+行业+上市日+ST标记 ──
    rows = c.execute(f'''
        SELECT t.code, i.name, i.industry, t.close, i.is_st, i.list_date,
        (t.close - y.close) / y.close * 100 as pct14,
        (y.close - z.close) / z.close * 100 as pct13,
        (z.close - w.close) / w.close * 100 as pct12,
        (w.close - v.close) / v.close * 100 as pct11
        FROM stock_daily_hsj t
        JOIN stock_daily_hsj y ON t.code = y.code AND y.date = 20260813
        JOIN stock_daily_hsj z ON y.code = z.code AND z.date = 20260812
        JOIN stock_daily_hsj w ON z.code = w.code AND w.date = 20260811
        JOIN stock_daily_hsj v ON w.code = v.code AND v.date = 20260810
        LEFT JOIN stock_info i ON t.code = i.code
        WHERE t.date = {TARGET}''').fetchall()

    # 新股识别：预计算市场交易日历（一次查询），再按上市日计数
    _cal = [str(r[0]) for r in c.execute(
        f'SELECT DISTINCT date FROM stock_daily_hsj WHERE date <= {TARGET} ORDER BY date DESC LIMIT 30'
    ).fetchall()]  # 近30个交易日足够覆盖5日窗口
    _cal_set = set(_cal)

    def trading_days_since(list_date):
        if not list_date:
            return 999
        ld = str(list_date)
        return sum(1 for d in _cal_set if ld <= d <= TARGET)

# ── 2. 判定涨停 + 连板高度 + 新股剔除 ──
limit_ups = []
board_height = {}
new_stocks_excluded = []
st_limit_ups = []  # ST涨停（仅市场统计，不入候选——铁律）
for code, name, ind, close, is_st, list_date, p14, p13, p12, p11 in rows:
    if name is None:
        continue
    st_flag = str(is_st) == '1'
    # 新股前5交易日无涨跌幅限制——涨多少都不算涨停
    td = trading_days_since(list_date)
    if td <= NEW_STOCK_NO_LIMIT_DAYS:
        if p14 >= 9.9:  # 大涨的新股才值得记录说明
            new_stocks_excluded.append((code, name, p14, td))
        continue
    th = limit_pct(code, st_flag)
    if p14 >= th:
        entry = (code, name, ind or '未知', close, p14)
        if st_flag:
            st_limit_ups.append(entry)  # ST不入候选但计入市场情绪
        else:
            limit_ups.append(entry)
        streak = 1
        for p in (p13, p12, p11):
            if p is not None and p >= th:
                streak += 1
            else:
                break
        board_height[code] = streak

two_boards = [(c_, n, i, cl, p, board_height[c_]) for c_, n, i, cl, p in limit_ups if board_height[c_] >= 2]

# ── 3. qfq交叉复核（除权假信号防线——分红季6-7月关键）──
exdiv_removed = []
if limit_ups:
    with sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True) as q:
        verified = []
        for code, name, ind, close, p in limit_ups:
            rq = q.execute('''SELECT (t.close-y.close)/y.close*100
                FROM stock_daily_meta_qfq t
                JOIN stock_daily_meta_qfq y ON t.code=y.code AND y.date=20260813
                WHERE t.code=? AND t.date=20260814''', (code,)).fetchone()
            th = limit_pct(code)
            if rq is not None and rq[0] < th - 0.15:
                exdiv_removed.append((code, name, p, rq[0]))  # 未复权假涨停
            else:
                verified.append((code, name, ind, close, p))
        limit_ups = verified
        two_boards = [x for x in two_boards if x[0] in {v[0] for v in verified}]

print(f"{'='*60}")
print(f"涨停板块分析 v2.2 | 基准日 20260814")
print(f"{'='*60}")
print(f"\n今日涨停: {len(limit_ups)}只（剔ST/剔新股前5日/qfq复核除权）")
if st_limit_ups:
    print(f"另ST涨停: {len(st_limit_ups)}只（仅计入市场统计，不入候选——铁律）")
if new_stocks_excluded:
    print(f"新股剔除（前{NEW_STOCK_NO_LIMIT_DAYS}交易日无涨跌幅限制）:")
    for code, name, p, td in new_stocks_excluded:
        print(f"  {code} {name}: +{p:.1f}%（上市第{td}个交易日，无限制不算涨停）")
if exdiv_removed:
    print(f"⚠️ 除权假信号剔除（qfq复核不符）:")
    for code, name, pu, pq in exdiv_removed:
        print(f"  {code} {name}: 未复权+{pu:.1f}% → qfq仅+{pq:.1f}%")
else:
    print(f"qfq复核: 0只除权假信号 ✅")

print(f"2连板及以上: {len(two_boards)}只")

# 连板梯队展示（按高度分层）
from collections import defaultdict
ladder = defaultdict(list)
for _, name, ind, close, pct, h in two_boards:
    ladder[h].append(f"{name}({close:.1f}元)")
print(f"\n连板梯队:")
for h in sorted(ladder, reverse=True):
    print(f"  {h}连板({len(ladder[h])}只): {', '.join(ladder[h])}")

# ── 3. 板块统计 ──
sector_count = Counter(x[2] for x in limit_ups)
print(f"\n[TH-001] 条件① 板块涨停≥3只:")
mainline = [s for s, n in sector_count.items() if n >= 3]
for s, n in sector_count.most_common(12):
    mark = ' ✅' if n >= 3 else ''
    print(f"  {s}: {n}只{mark}")
print(f"\n条件② 连板梯队: {len(two_boards)}只2板+ {'✅' if two_boards else '❌'}")

# ── 4. 程序化候选表（板块∈主线 + 涨停 + ≤30元）──
print(f"\n{'='*60}")
print(f"主线内候选（自动过滤：主线板块 + 涨停 + ≤{PRICE_LIMIT}元）")
print(f"{'='*60}")
for code, name, ind, close, pct in sorted(limit_ups, key=lambda x: -x[4]):
    if ind in mainline and close <= PRICE_LIMIT:
        h = board_height.get(code, 1)
        tag = f'{h}连板' if h >= 2 else '首板'
        print(f"  {code} {name} ({ind}): {close:.2f}元 +{pct:.1f}% {tag}")

print(f"\n（超{PRICE_LIMIT}元已自动剔除: ", end='')
excluded = [f"{x[1]}({x[3]:.0f}元)" for x in limit_ups if x[3] > PRICE_LIMIT]
print('; '.join(excluded[:8]) + '...）' if excluded else '无）')