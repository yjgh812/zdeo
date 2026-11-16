r"""
屠龙归因数据库初始化 v2
基于 workbuddyspace/attribution/init_attribution_db.py 扩展
新增2张表：trade_attribution（归因结果）+ experience_library（经验库）
DB路径：D:\zcodesj\attribution\attribution.db（遵循文件铁律）
"""
import sqlite3
import os

DB_PATH = r'D:\zcodesj\attribution\attribution.db'
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# ========== 原有5张表（保持兼容） ==========

# 1. 交易记录表
cursor.execute('''
CREATE TABLE IF NOT EXISTS trade_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    trade_date TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY','SELL','PARTIAL_SELL')),
    price REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL,
    strategy_version TEXT DEFAULT 'V23',
    signal_type TEXT CHECK(signal_type IN ('probability_threshold','top_n_ranking','manual')),
    subsystems TEXT,
    factors TEXT,
    pred_proba REAL,
    pred_class INTEGER,
    stop_loss REAL,
    take_profit REAL,
    trailing_profit REAL,
    expected_return_5d REAL,
    expected_return_20d REAL,
    market_env TEXT,
    sector TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)
''')

# 2. 持仓历史表
cursor.execute('''
CREATE TABLE IF NOT EXISTS position_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    current_price REAL,
    pnl_pct REAL,
    pnl_amount REAL,
    max_pnl_pct REAL DEFAULT 0,
    max_drawdown_pct REAL DEFAULT 0,
    status TEXT DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED','PARTIAL_CLOSED')),
    holding_days INTEGER DEFAULT 0,
    strategy_version TEXT DEFAULT 'V23',
    subsystems TEXT,
    pred_proba_at_entry REAL,
    market_env_at_entry TEXT,
    sector TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
)
''')

# 3. 月度归因汇总表
cursor.execute('''
CREATE TABLE IF NOT EXISTS attribution_monthly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL UNIQUE,
    total_trades INTEGER DEFAULT 0,
    buy_count INTEGER DEFAULT 0,
    sell_count INTEGER DEFAULT 0,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    win_rate REAL,
    avg_return REAL,
    avg_expected_return REAL,
    expectation_gap REAL,
    avg_holding_days REAL,
    stop_loss_hit_rate REAL,
    take_profit_hit_rate REAL,
    trailing_stop_hit_rate REAL,
    best_subsystem TEXT,
    worst_subsystem TEXT,
    best_factor TEXT,
    worst_factor TEXT,
    market_env_distribution TEXT,
    remarks TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
)
''')

# 4. 信号快照表
cursor.execute('''
CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    s01_trend INTEGER, s02_volatility INTEGER, s03_volume INTEGER,
    s04_macd INTEGER, s05_kdj INTEGER, s06_boll INTEGER,
    s07_smart_money INTEGER, s08_rsi INTEGER, s09_momentum INTEGER,
    s10_fundamental INTEGER, s11_financial INTEGER, s12_breakout INTEGER,
    s13_reversal INTEGER, s14_chip INTEGER, s15_sentiment INTEGER,
    s16_ml_signal INTEGER, s17_volume_break INTEGER, s18_shrink_callback INTEGER,
    s19_price_volume_divergence INTEGER,
    signal_count INTEGER,
    primary_signals TEXT,
    pred_proba REAL,
    pred_class INTEGER,
    close_price REAL,
    ma20 REAL, ma60 REAL,
    rsi14 REAL,
    macd_dif REAL, macd_dea REAL,
    volume_ratio REAL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
)
''')

# 5. 因子表现追踪表
cursor.execute('''
CREATE TABLE IF NOT EXISTS factor_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_name TEXT NOT NULL,
    date TEXT NOT NULL,
    ic REAL,
    ic_20d REAL,
    win_rate_20d REAL,
    long_return_20d REAL,
    short_return_20d REAL,
    long_short_spread REAL,
    turnover_20d REAL,
    UNIQUE(factor_name, date)
)
''')

# ========== v2 新增表 ==========

# 6. 归因结果表（核心：每笔交易的标准化归因）
cursor.execute('''
CREATE TABLE IF NOT EXISTS trade_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,          -- 关联 position_history.id
    code TEXT NOT NULL,
    name TEXT,
    pnl_pct REAL,
    pnl_amount REAL,
    -- 亏损归因4维度（盈利交易则一级归因为能力/运气分类）
    primary_attribution TEXT NOT NULL,     -- 一级：selection/execution/market/risk / ability/luck
    secondary_attribution TEXT NOT NULL,   -- 二级：因子失效/追高/止损不力/系统性风险/仓位过重 等
    controllability_score REAL,            -- 可控程度 0-10（越高=策略可优化空间越大）
    pnl_contribution REAL,                 -- 该归因因素对盈亏的贡献占比 %
    root_cause TEXT,                       -- 核心原因描述
    lesson TEXT,                           -- 经验/教训总结
    optimization_rule TEXT,                -- 对应优化规则
    scenario_tags TEXT,                    -- 场景标签 JSON: ["假突破","板块退潮"]
    ability_luck_verdict TEXT,             -- 能力运气裁决: ability/luck/mixed
    data_source TEXT DEFAULT 'westock',    -- 数据来源
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (position_id) REFERENCES position_history(id)
)
''')

# 7. 经验库表（四库合一，用library_type区分）
cursor.execute('''
CREATE TABLE IF NOT EXISTS experience_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_type TEXT NOT NULL CHECK(library_type IN (
        'loss_case',       -- 亏损案例库
        'profit_paradigm', -- 盈利范式库
        'failure_scene',   -- 失效场景库
        'optimization_log' -- 优化迭代日志库
    )),
    case_id TEXT NOT NULL,                 -- 案例ID: LOSS-001 / PROFIT-001 / SCENE-001 / OPT-001
    code TEXT,
    name TEXT,
    trade_link TEXT,                       -- 交易链路描述
    pnl_summary TEXT,                      -- 盈亏摘要
    attribution_detail TEXT,               -- 归因明细
    controllability_score REAL,
    core_facts TEXT,                       -- 核心事实依据
    lesson TEXT,                           -- 经验/教训
    optimization_rule TEXT,                -- 对应优化规则
    scenario_tags TEXT,                    -- 场景标签 JSON
    verification_status TEXT DEFAULT 'pending', -- pending/forward_testing/verified/rejected
    created_at TEXT DEFAULT (datetime('now','localtime'))
)
''')

# ========== 索引 ==========
cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_code ON trade_records(code)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_date ON trade_records(trade_date)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_pos_code ON position_history(code)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_pos_entry ON position_history(entry_date)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_pos_status ON position_history(status)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_snap_code_date ON signal_snapshots(code, date)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_factor_name_date ON factor_performance(factor_name, date)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_attr_pos ON trade_attribution(position_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_attr_code ON trade_attribution(code)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_attr_primary ON trade_attribution(primary_attribution)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_exp_type ON experience_library(library_type)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_exp_tags ON experience_library(scenario_tags)')

conn.commit()
conn.close()

print(f'[OK] Attribution DB v2 initialized: {DB_PATH}')
print('Tables: trade_records, position_history, attribution_monthly, signal_snapshots,')
print('        factor_performance, trade_attribution, experience_library')
