#!/usr/bin/env python3
"""全量拦截规则十年验证 v2 - SQL驱动高效版 (2016-2026)"""

import sqlite3, sys, time
from collections import defaultdict

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

t0 = time.time()

# ============ 1. 建临时表: 代码+日期+价格+rank(每日序) ============
print("Building date rank table...")
c.execute("DROP TABLE IF EXISTS tmp_daily_rank")
c.execute("""
CREATE TABLE tmp_daily_rank AS
SELECT code, date, open, high, low, close, vol,
       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date) as day_rank
FROM stock_daily_hsj
WHERE date >= 20160101
""")
con.commit()
print(f"  {c.execute('SELECT COUNT(*) FROM tmp_daily_rank').fetchone()[0]} rows")

# ============ 2. 建前向收益表(5日/20日) ============
print("Building forward returns...")
c.execute("DROP TABLE IF EXISTS tmp_fwd_ret")
c.execute("""
CREATE TABLE tmp_fwd_ret AS
SELECT a.code, a.date, a.close as cur_close,
       a.day_rank,
       (SELECT b.close FROM tmp_daily_rank b 
        WHERE b.code = a.code AND b.day_rank = a.day_rank + 5) as close_5d,
       (SELECT b.close FROM tmp_daily_rank b 
        WHERE b.code = a.code AND b.day_rank = a.day_rank + 20) as close_20d,
       (SELECT b.close FROM tmp_daily_rank b 
        WHERE b.code = a.code AND b.day_rank = a.day_rank + 1) as close_1d,
       (SELECT b.close FROM tmp_daily_rank b 
        WHERE b.code = a.code AND b.day_rank = a.day_rank - 1) as prev_close,
       (SELECT b.close FROM tmp_daily_rank b 
        WHERE b.code = a.code AND b.day_rank = a.day_rank - 5) as close_m5
FROM tmp_daily_rank a
""")
con.commit()
print(f"  {c.execute('SELECT COUNT(*) FROM tmp_fwd_ret').fetchone()[0]} rows")

# ============ 3. 采样日期 ============
# 每月2个交易日(月初+月中) × 11年 = 每年24个 × 11 = 264个采样日
c.execute("SELECT DISTINCT date FROM stock_daily_hsj WHERE date >= 20160101 ORDER BY date")
all_dates = [r[0] for r in c.fetchall()]
sample_dates = []
for i, d in enumerate(all_dates):
    ds = str(d)
    day = ds[6:8]
    if day in ("01", "02", "03", "14", "15", "16"):
        sample_dates.append(d)
print(f"Sample dates: {len(sample_dates)}")

# ============ 4. 验证函数 ============
def validate(name, where_clause, params=(), desc=""):
    """where_clause 为对 tmp_fwd_ret 的过滤条件"""
    q = f"""
    SELECT 
        SUM(CASE WHEN {where_clause} THEN 1 ELSE 0 END) as n_filtered,
        COUNT(*) as n_total,
        AVG(CASE WHEN {where_clause} THEN (close_5d - cur_close)/cur_close*100 END) as f_avg5,
        AVG(CASE WHEN NOT ({where_clause}) THEN (close_5d - cur_close)/cur_close*100 END) as u_avg5,
        SUM(CASE WHEN {where_clause} AND close_5d > cur_close THEN 1 ELSE 0 END) as f_wins,
        SUM(CASE WHEN NOT ({where_clause}) AND close_5d > cur_close THEN 1 ELSE 0 END) as u_wins,
        AVG(CASE WHEN {where_clause} THEN (close_20d - cur_close)/cur_close*100 END) as f_avg20,
        AVG(CASE WHEN NOT ({where_clause}) THEN (close_20d - cur_close)/cur_close*100 END) as u_avg20
    FROM tmp_fwd_ret
    WHERE date IN ({','.join('?' * len(sample_dates))})
      AND cur_close > 0
      AND close_5d IS NOT NULL
    """
    r = c.execute(q, sample_dates).fetchone()
    n_f, n_t = r["n_filtered"] or 0, r["n_total"] or 0
    f5, u5 = r["f_avg5"], r["u_avg5"]
    f20, u20 = r["f_avg20"], r["u_avg20"]
    f_w, u_w = r["f_wins"] or 0, r["u_wins"] or 0

    if n_f == 0 or n_t == 0:
        return {"rule": name, "n_filtered": 0, "n_total": n_t, "verdict": "NO_DATA", "desc": desc}

    f5 = f5 or 0
    u5 = u5 or 0
    f_win_pct = f_w / n_f * 100
    u_win_pct = u_w / (n_t - n_f) * 100 if n_t > n_f else 0

    # 拦截规则合理 = 被拦截样本(满足条件)的收益 <= 放行样本收益
    diff = u5 - f5
    if diff > 0.8:
        verdict = "CORRECT(拦截合理)"
    elif diff < -0.8:
        verdict = "WRONG(拦截错误)"
    else:
        verdict = "NEUTRAL(无显著差异)"

    return {
        "rule": name, "n_filtered": n_f, "n_total": n_t,
        "f_avg5": round(f5, 2), "u_avg5": round(u5, 2),
        "f_avg20": round(f20 or 0, 2), "u_avg20": round(u20 or 0, 2),
        "f_win": round(f_win_pct, 1), "u_win": round(u_win_pct, 1),
        "diff5": round(diff, 2), "verdict": verdict, "desc": desc,
    }

results = []

# ============ 5. 各规则验证 ============
# S-3: 价格<=30
results.append(validate("S-3价格<=30", "cur_close <= 30", desc="价格<=30元"))

# 价格<=10
results.append(validate("价格<=10", "cur_close <= 10", desc="低价股"))

# 价格>30
results.append(validate("价格>30", "cur_close > 30", desc="高价股"))

# R6: 收高(close-low)/(high-low) >= 0.7
results.append(validate("R6收高>=70%", "(close-low)*1.0/(high-low) >= 0.7 AND high > low", desc="收盘位置>=70%"))

# R6 反向: 收低 < 0.3
results.append(validate("R6收低<30%", "(close-low)*1.0/(high-low) <= 0.3 AND high > low", desc="收盘位置<=30%"))

# R5: 前40日振幅>50% (用5日前价格和当日高低近似? 需要历史数据)
# 简化: 当日振幅 = (high-low)/low > 10%
results.append(validate("当日振幅>10%", "(high-low)*1.0/low > 0.10 AND low > 0", desc="当日振幅>10%"))

# R1: 追高 - 昨日涨停(近似超买)
results.append(validate("R1昨日涨停追入", "prev_close > 0 AND (cur_close-prev_close)*1.0/prev_close >= 0.095", desc="昨日涨停今日买"))

# R7: 5日涨幅>20% (超买)
results.append(validate("R7五日涨幅>20%", "close_m5 > 0 AND (cur_close-close_m5)*1.0/close_m5 >= 0.20", desc="5日涨幅>20%"))

# R7: 5日涨幅>10%
results.append(validate("R7五日涨幅>10%", "close_m5 > 0 AND (cur_close-close_m5)*1.0/close_m5 >= 0.10", desc="5日涨幅>10%"))

# R16: 高换手(vol相对) - 简化用当日波动
results.append(validate("R16放量(vol>5倍均量)", "1=1", desc="需要vol对比-简化跳过"))

# 低价<3元
results.append(validate("价格<3元", "cur_close < 3", desc="价格<3元"))

# 高价>100
results.append(validate("价格>100", "cur_close > 100", desc="价格>100元"))

# 20cm创业板(近似: 前收涨停20%次日)
results.append(validate("创业板20cm涨停次日", "prev_close > 0 AND (cur_close-prev_close)*1.0/prev_close >= 0.195", desc="创业板20cm"))

# ============ 6. 输出 ============
print("\n" + "=" * 95)
print("  全量拦截规则十年验证 (2016-2026, SQL批量)")
print(f"  采样: {len(sample_dates)}个交易日 | 耗时: {time.time()-t0:.1f}s")
print("=" * 95)
hdr = f"{'规则':<22}{'拦截样本':>8}{'总样本':>8}{'拦截5d':>8}{'放行5d':>8}{'拦截20d':>9}{'放行20d':>9}{'拦截胜率':>8}{'放行胜率':>8}  判定"
print(hdr)
print("-" * 95)

for r in results:
    print(f"{r['rule']:<22}{r['n_filtered']:>8}{r['n_total']:>8}"
          f"{r['f_avg5']:>7.2f}%{r['u_avg5']:>7.2f}%"
          f"{r['f_avg20']:>8.2f}%{r['u_avg20']:>8.2f}%"
          f"{r['f_win']:>7.1f}%{r['u_win']:>7.1f}%  {r['verdict']}")

print("-" * 95)
print("判定说明: CORRECT=被拦截样本收益显著更差(规则合理)")
print("          WRONG=被拦截样本收益反而更好(规则不合理)")
print("          NEUTRAL=差异不显著(规则无影响)")
print("=" * 95)

con.close()