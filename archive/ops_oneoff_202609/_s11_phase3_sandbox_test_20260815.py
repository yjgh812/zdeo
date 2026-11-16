#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_s11_phase3_sandbox_test_20260815.py — 修改后 phase3_update_signals 端到端沙盒测试
在临时库上真实调用 tulong_daily_update.phase3_update_signals, 验证:
  T1 新行同时写入旧 s11_financial(期末as-of, 语义不变) 与新 s11_pit(披露截止as-of)
  T2 前视窗口日: 旧s11用了尚未到法定披露截止的期次; s11_pit回退到已披露期次
  T3 幂等: 重跑迁移不报错(列已存在)
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"D:\workbuddyspace")
import tulong_daily_update as T

tmp = Path(tempfile.mkdtemp(prefix="s11_sandbox_"))
db = tmp / "scratch.db"

con = sqlite3.connect(str(db))
con.executescript("""
CREATE TABLE stock_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code VARCHAR(10) NOT NULL, date INT NOT NULL,
    s1_sentiment REAL, s2_auction REAL, s3_momentum REAL, s4_macd REAL, s5_kdj REAL,
    s6_boll REAL, s7_volume REAL, s8_volatility REAL, s9_ma REAL, s10_leader REAL,
    s11_financial REAL, s12_weak_strong REAL, s13_capital REAL, s14_sector REAL,
    s15_risk REAL, s16_oversold REAL, composite_score REAL,
    ai_prob_5d REAL, ai_prob_20d REAL, ai_prob_120d REAL, v375_prob REAL,
    UNIQUE(code, date));
CREATE TABLE stock_financial (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code VARCHAR(10) NOT NULL, report_date INT NOT NULL,
    pe REAL, pb REAL, ps REAL, roe REAL, roa REAL, gross_margin REAL, net_margin REAL,
    revenue_growth REAL, profit_growth REAL, debt_ratio REAL, current_ratio REAL,
    basic_eps REAL, bps REAL, total_shares REAL, revenue_ttm REAL,
    UNIQUE(code, report_date));
CREATE TABLE stock_factors (
    code TEXT, date INTEGER, ma5 REAL, macd_hist REAL, k REAL, d REAL, j REAL,
    boll_upper REAL, boll_mid REAL, boll_lower REAL, vol_ratio REAL, rsi6 REAL,
    rsi12 REAL, atr REAL, ret1 REAL, ret5 REAL, ret20 REAL, momentum REAL,
    ma_bull REAL, smart_money REAL);
CREATE TABLE stock_daily (code TEXT, date INTEGER, open REAL, close REAL, change_pct REAL);
""")
# 财务: 000001 2024Q3(截止20241031, 已披露) 与 2024年报(截止20250430, 2025-01-15尚未披露)
con.execute("INSERT INTO stock_financial (code,report_date,roe,pe,pb,debt_ratio,revenue_growth,profit_growth)"
            " VALUES ('000001',20240930,10,20,2,50,25,30)")   # score=0.15+0.25+0.2+0.15+0.15+0.1=1.0
con.execute("INSERT INTO stock_financial (code,report_date,roe,pe,pb,debt_ratio,revenue_growth,profit_growth)"
            " VALUES ('000001',20241231,2,150,12,80,-5,-8)")  # score=-0.2-0.15=-0.35
# 因子/日线: 一个交易日 20250115
con.execute("INSERT INTO stock_factors VALUES ('000001',20250115,10,0.1,40,50,45,11,10,9,1.0,50,55,0.2,0.01,0.02,0.03,0.5,1,0.6)")
con.execute("INSERT INTO stock_daily VALUES ('000001',20250115,10.0,10.1,0.01)")
con.commit()
con.close()

# 重定向主库到沙盒并真实执行 Phase3
T.DB_MAIN = db
T.DB_META = tmp / "nonexistent_meta.db"   # 触发无meta_db的fallback路径
ok = T.phase3_update_signals(20250115, dry_run=False)
assert ok, "phase3 返回 False"

con = sqlite3.connect(str(db))
row = con.execute("SELECT s11_financial, s11_pit FROM stock_signals WHERE code='000001' AND date=20250115").fetchone()
print("写入行 (s11_financial, s11_pit) =", row)
exp_old = max(-1, min(1, (-0.2) + (-0.15)))          # 年报期末as-of: roe=2无分,pe=150扣,pb=12扣
exp_pit = max(-1, min(1, 0.15 + 0.25 + 0.2 + 0.15 + 0.15 + 0.1))  # 2024Q3披露截止已过
t1 = row[0] is not None and row[1] is not None
t2 = abs(row[0] - exp_old) < 1e-12 and abs(row[1] - exp_pit) < 1e-12
# T3: 幂等重跑(模拟次日增量: 追加新交易日)
con.execute("INSERT INTO stock_factors VALUES ('000001',20250116,10,0.1,40,50,45,11,10,9,1.0,50,55,0.2,0.01,0.02,0.03,0.5,1,0.6)")
con.execute("INSERT INTO stock_daily VALUES ('000001',20250116,10.1,10.2,0.01)")
con.commit()
con.close()
ok2 = T.phase3_update_signals(20250116, dry_run=False)
con = sqlite3.connect(str(db))
row2 = con.execute("SELECT s11_financial, s11_pit FROM stock_signals WHERE code='000001' AND date=20250116").fetchone()
t3 = ok2 and row2[0] is not None and row2[1] is not None
fin_pit = con.execute("SELECT DISTINCT pit_avail_date FROM stock_financial ORDER BY 1").fetchall()
print("次日新行 =", row2, "| stock_financial.pit_avail_date =", fin_pit)
print(f"T1 新行双列写入: {'PASS' if t1 else 'FAIL'}")
print(f"T2 口径正确(s11=旧期末as-of {exp_old}, s11_pit=披露截止as-of {exp_pit}): {'PASS' if t2 else 'FAIL'}")
print(f"T3 增量幂等重跑: {'PASS' if t3 else 'FAIL'}")
import shutil
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if (t1 and t2 and t3) else 1)
