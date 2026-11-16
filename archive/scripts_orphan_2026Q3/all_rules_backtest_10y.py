#!/usr/bin/env python3
"""全量拦截规则十年验证 — R1-R18 + S级全部规则"""

import sqlite3, os, sys
from datetime import datetime
from collections import defaultdict

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
ADB = r"D:\zcodesj\attribution\attribution.db"

con = sqlite3.connect(DB)
c = con.cursor()

# ============ 数据加载 ============
# 获取所有交易日
c.execute("SELECT DISTINCT date FROM stock_daily_hsj ORDER BY date")
all_dates = [r[0] for r in c.fetchall()]
print(f"Total trading days: {len(all_dates)}")
print(f"Date range: {all_dates[0]} -> {all_dates[-1]}")

# 抽样: 每年约60个交易日(月初+月中)，覆盖2016-2026
sample_dates = []
for i, d in enumerate(all_dates):
    ds = str(d)
    if 2016 <= int(ds[:4]) <= 2026:
        if ds.endswith("01") or ds.endswith("02") or ds.endswith("03") or ds.endswith("15") or ds.endswith("16"):
            sample_dates.append(d)
print(f"Sample dates: {len(sample_dates)}")

# 预加载样本日期数据(10年覆盖)
print("Loading sample dates data...")
# 补充加载: 每个样本日期的前后各5个交易日(用于连板/振幅/分位计算)
all_needed_dates = set(sample_dates)
c.execute("SELECT DISTINCT date FROM stock_daily_hsj")
all_dates_list = [str(r[0]) for r in c.fetchall()]
for sd in sample_dates:
    try:
        idx = all_dates_list.index(str(sd))
        for j in range(max(0, idx-60), min(len(all_dates_list), idx+10)):
            all_needed_dates.add(int(all_dates_list[j]))
    except ValueError:
        pass

placeholders = ','.join('?' * len(all_needed_dates))
rows = c.execute(f"""
    SELECT code, date, open, high, low, close, vol
    FROM stock_daily_hsj
    WHERE date IN ({placeholders})
""", sorted(all_needed_dates)).fetchall()
print(f"Loaded {len(rows)} rows across {len(all_needed_dates)} dates")

# 建立索引: date -> [(code, open, high, low, close, vol)]
date_index = defaultdict(list)
for code, date, o, h, l, cl, v in rows:
    date_index[date].append((code, o, h, l, cl, v))

# code -> {date: close} 用于历史序列
close_by_code = defaultdict(dict)
for code, date, o, h, l, cl, v in rows:
    close_by_code[code][date] = cl

all_codes = list(close_by_code.keys())
print(f"Total codes: {len(all_codes)}")

# ============ 验证函数 ============
def get_forward_returns(code, date, days=5):
    """获取未来N日收益"""
    closes = close_by_code.get(code, {})
    if date not in closes:
        return None
    base = closes[date]
    if base <= 0:
        return None
    # 找到未来第N个交易日的价格
    dates_sorted = sorted(closes.keys())
    idx = dates_sorted.index(date)
    if idx + days >= len(dates_sorted):
        return None
    future = closes[dates_sorted[idx + days]]
    if future <= 0:
        return None
    return (future - base) / base * 100

def get_price_history(code, date, lookback=40):
    """获取过去N日的价格序列"""
    closes = close_by_code.get(code, {})
    dates_sorted = sorted(closes.keys())
    if date not in closes:
        return None
    idx = dates_sorted.index(date)
    start = max(0, idx - lookback)
    hist = [closes[dates_sorted[i]] for i in range(start, idx + 1)]
    if len(hist) < 20:
        return None
    return hist

def get_amplitude_40d(code, date):
    """40日振幅"""
    hist = get_price_history(code, date, 40)
    if not hist:
        return None
    high = max(hist)
    low = min(hist)
    if low <= 0:
        return None
    return (high - low) / low

def get_close_position(code, date):
    """当日收盘位置 (close-low)/(high-low)"""
    for row in date_index.get(date, []):
        if row[0] == code:
            _, o, h, l, cl, v = row
            if h > l:
                return (cl - l) / (h - l)
            return None
    return None

def get_60d_percentile(code, date):
    """60日分位"""
    hist = get_price_history(code, date, 60)
    if not hist or len(hist) < 30:
        return None
    current = hist[-1]
    pct = sum(1 for x in hist if x <= current) / len(hist)
    return pct

def get_5d_gain(code, date):
    """5日涨幅"""
    hist = get_price_history(code, date, 5)
    if not hist or len(hist) < 5:
        return None
    return (hist[-1] - hist[0]) / hist[0] * 100

def get_turnover_rate(code, date):
    """换手率近似(当日成交量/近20日平均成交量)"""
    closes = close_by_code.get(code, {})
    dates_sorted = sorted(closes.keys())
    if date not in closes:
        return None
    idx = dates_sorted.index(date)
    # 找到当日成交量
    for row in date_index.get(date, []):
        if row[0] == code:
            vol = row[5]
            # 前20日均量
            vols = []
            for i in range(max(0, idx-20), idx):
                d = dates_sorted[i]
                for r2 in date_index.get(d, []):
                    if r2[0] == code:
                        vols.append(r2[5])
                        break
            avg_vol = sum(vols) / len(vols) if vols else 1
            if avg_vol > 0:
                return vol / avg_vol
            return None
    return None

def validate_rule(name, condition_fn, sample_dates, desc=""):
    """通用验证: 满足条件 vs 不满足条件的未来收益"""
    filtered_rets = []
    unfiltered_rets = []
    filtered_count = 0
    total_count = 0

    for date in sample_dates:
        for code, o, h, l, cl, v in date_index.get(date, [])[:2000]:  # 限制每日期样本
            ret5 = get_forward_returns(code, date, 5)
            if ret5 is None:
                continue
            total_count += 1
            try:
                cond = condition_fn(code, date)
            except Exception:
                cond = None
            if cond:
                filtered_rets.append(ret5)
                filtered_count += 1
            else:
                unfiltered_rets.append(ret5)

    if not filtered_rets:
        return {"rule": name, "filtered_n": 0, "total_n": total_count,
                "filtered_avg5d": 0, "unfiltered_avg5d": 0,
                "filtered_win": 0, "unfiltered_win": 0,
                "diff": 0, "verdict": "NO_SAMPLES", "desc": desc}

    f_avg = sum(filtered_rets) / len(filtered_rets)
    u_avg = sum(unfiltered_rets) / len(unfiltered_rets) if unfiltered_rets else 0
    f_win = sum(1 for r in filtered_rets if r > 0) / len(filtered_rets) * 100
    u_win = sum(1 for r in unfiltered_rets if r > 0) / len(unfiltered_rets) * 100 if unfiltered_rets else 0

    # 判定: 拦截规则应使被拦截样本收益更差(规则合理)或更好(规则错误)
    diff = u_avg - f_avg  # 被拦截 vs 放行的差异
    if diff > 1.0:
        verdict = "CORRECT"  # 被拦截的确实更差
    elif diff < -1.0:
        verdict = "WRONG"   # 被拦截的反而更好
    else:
        verdict = "NEUTRAL"

    return {
        "rule": name, "filtered_n": filtered_count, "total_n": total_count,
        "filtered_avg5d": round(f_avg, 2), "unfiltered_avg5d": round(u_avg, 2),
        "filtered_win": round(f_win, 1), "unfiltered_win": round(u_win, 1),
        "diff": round(diff, 2), "verdict": verdict, "desc": desc,
    }

results = []

# ============ 1. S-3 价格≤30 ============
def cond_price(code, date):
    for row in date_index.get(date, []):
        if row[0] == code:
            return row[4] <= 30  # close <= 30
    return False

results.append(validate_rule("S-3价格<=30", cond_price, sample_dates, "价格<=30元拦截"))

# ============ 2. R5 振幅>50% ============
def cond_amplitude(code, date):
    amp = get_amplitude_40d(code, date)
    return amp is not None and amp > 0.50

results.append(validate_rule("R5振幅>50%", cond_amplitude, sample_dates, "40日振幅>50%拦截"))

# ============ 3. R6 收盘位置>=70% ============
def cond_close_pos(code, date):
    pos = get_close_position(code, date)
    return pos is not None and pos >= 0.70

results.append(validate_rule("R6收高>=70%", cond_close_pos, sample_dates, "收盘位置>=70%拦截"))

# ============ 4. R1 60日分位>70% ============
def cond_high_60d(code, date):
    pct = get_60d_percentile(code, date)
    return pct is not None and pct > 0.70

results.append(validate_rule("R1追高60日分位>70%", cond_high_60d, sample_dates, "60日分位>70%拦截"))

# ============ 5. R7 5日涨幅>20% ============
def cond_overbought_5d(code, date):
    gain = get_5d_gain(code, date)
    return gain is not None and gain > 20

results.append(validate_rule("R7超买5日>20%", cond_overbought_5d, sample_dates, "5日涨幅>20%拦截"))

# ============ 6. R16 换手率>3倍 ============
def cond_high_turnover(code, date):
    tr = get_turnover_rate(code, date)
    return tr is not None and tr > 3.0

results.append(validate_rule("R16高换手>3x", cond_high_turnover, sample_dates, "换手率>3倍拦截"))

# ============ 7. 涨停次日追入 ============
def cond_limit_up_yesterday(code, date):
    """昨日涨停"""
    closes = close_by_code.get(code, {})
    dates_sorted = sorted(closes.keys())
    if date not in closes:
        return False
    idx = dates_sorted.index(date)
    if idx < 1:
        return False
    prev_date = dates_sorted[idx - 1]
    prev_close = closes[prev_date]
    cur_close = closes[date]
    if prev_close <= 0:
        return False
    return (cur_close - prev_close) / prev_close >= 0.095

results.append(validate_rule("R7涨停次日", cond_limit_up_yesterday, sample_dates, "昨日涨停今日买入"))

# ============ 8. 低价<3元 ============
def cond_cheap(code, date):
    for row in date_index.get(date, []):
        if row[0] == code:
            return row[4] < 3
    return False

results.append(validate_rule("低价<3元", cond_cheap, sample_dates, "价格<3元"))

# ============ 9. 新股<60天 ============
def cond_new_stock(code, date):
    """上市未满60天"""
    closes = close_by_code.get(code, {})
    dates_sorted = sorted(closes.keys())
    if date not in closes:
        return False
    idx = dates_sorted.index(date)
    return idx < 40  # 前40个交易日视为次新

results.append(validate_rule("次新股<40交易日", cond_new_stock, sample_dates, "上市未满60天"))

# ============ 10. ST ============
def cond_st(code, date):
    return code.startswith("ST") or code.startswith("*ST")

results.append(validate_rule("ST/*ST", cond_st, sample_dates, "ST股票"))

# ============ 输出 ============
print("\n" + "=" * 80)
print("  全量拦截规则十年验证报告")
print("=" * 80)
print(f"{'规则':<25} {'被拦截样本':>8} {'总样本':>8} {'拦截5日均':>8} {'放行5日均':>8} {'拦截胜率':>8} {'放行胜率':>8} {'判定':>10}")
print("-" * 80)

for r in results:
    print(f"{r['rule']:<25} {r['filtered_n']:>8} {r['total_n']:>8} "
          f"{r['filtered_avg5d']:>7.2f}% {r['unfiltered_avg5d']:>7.2f}% "
          f"{r['filtered_win']:>7.1f}% {r['unfiltered_win']:>7.1f}% "
          f"{r['verdict']:>10}")

print("-" * 80)
print("\n判定说明: CORRECT=被拦截样本收益确实更差(规则合理)")
print("          WRONG=被拦截样本收益反而更好(规则不合理)")
print("          NEUTRAL=差异不显著(规则无影响)")
print("\n" + "=" * 80)

con.close()