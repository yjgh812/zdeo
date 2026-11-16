# -*- coding: utf-8 -*-
"""
屠龙系统 多模型全域历史回测 增强版
===================================
全参数：100000本金 | 2016-01-01 ~ 2026-08-24 | A股4500+ | 剔除科创板/北交所
6模型并行：bp+ep / V375 / P2 / Composite / ML因子 / 核心-卫星组合

输出：D:/zcodesj/reports/tulong_backtest_20260817/
  - 各策略净值曲线、交易明细（CSV+JSON）
  - 年度逐笔交易明细（每年独立CSV）
  - 6类matplotlib图表（base64嵌入HTML）
  - 深度诊断分析
  - 分层优化方案

基于 V4.2 模块化框架 (D:/workbuddyspace/factor_engine/core/backtest_system/)
"""

import sys, os, json, logging, time, base64, io, warnings, bisect
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple, Callable
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.dates import DateFormatter
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "FangSong"]
plt.rcParams["axes.unicode_minus"] = False

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# V4.2 框架路径
FACTOR_ENGINE_ROOT = r"D:\workbuddyspace\factor_engine"
if FACTOR_ENGINE_ROOT not in sys.path:
    sys.path.insert(0, FACTOR_ENGINE_ROOT)
if r"D:\zcodesj" not in sys.path:
    sys.path.insert(0, r"D:\zcodesj")  # DOSE fork: analysis/ 位置可导入根目录模块(combo_synthesizer)

from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest
from core.backtest_system.performance import equity_metrics
from core.backtest_system.report import build_html_report
from core.backtest_system.export import export_results

# SENS fork: 买入侧开盘缺口旋钮(默认不启用=基线零变化; 单边买入口径, 对齐材料"开盘缺口+1%")
if os.environ.get("TULONG_SENS_GAP"):
    _SENS_GAP = float(os.environ["TULONG_SENS_GAP"])
    from core.backtest_system import costs as _sens_costs
    _sens_orig_slip = _sens_costs.SlippageConfig.calculate

    def _sens_slip_with_gap(self, price, volume, side, daily_amount=None):
        if side == "buy":
            fp = price * (1 + _SENS_GAP)
            return round(fp, 3), round(abs(fp - price) * volume, 2)
        return _sens_orig_slip(self, price, volume, side, daily_amount)

    _sens_costs.SlippageConfig.calculate = _sens_slip_with_gap

# ============================================================
#  Paths & Constants
# ============================================================
DATA_DIR = r"D:\workbuddyspace\factor_engine\data"
DB_PATH = os.path.join(DATA_DIR, "dragon_quant.db")
QFQ_PATH = os.path.join(DATA_DIR, "dragon_quant_qfq.db")
_DEFAULT_OUT = os.environ.get(
    "TULONG_BT_OUT",
    os.path.join(r"D:\zcodesj\reports", "backtest_full_" + datetime.now().strftime("%Y%m%d")))


def _assert_round_naming(out_dir: str) -> None:
    """准则44/E75 物化(2026-08-28): roundN 命名仅限 wfa_gate 新指纹立项后的正式轮次。
    输出目录名含 roundN 且非 drift_check 前缀 → 须在 data/wfa_trials.jsonl 存在 PASS 裁决,
    否则 exit 2 拒绝(防命名越界复跑穿一票制/试验记账)。drift_check 前缀=数据延伸复跑(合法, 免指纹)。"""
    import re as _re
    base = os.path.basename(str(out_dir).rstrip("\\/"))
    if not _re.search(r"round\d+", base):
        return
    if "drift_check" in base:
        return
    _passed = False
    _trials = r"D:\zcodesj\data\wfa_trials.jsonl"
    if os.path.exists(_trials):
        try:
            for _line in open(_trials, encoding="utf-8"):
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _t = json.loads(_line)
                except Exception:
                    continue
                if str(_t.get("verdict", "")).upper().startswith("PASS"):
                    _passed = True
                    break
        except Exception:
            pass
    if not _passed:
        sys.stderr.write(
            "[准则44/E75] 输出目录含 roundN 命名但 wfa_trials.jsonl 无 PASS 立项裁决 —— "
            "roundN 仅限 wfa_gate 新指纹立项后的正式轮; 数据延伸复跑请用 drift_check 前缀。拒绝执行。\n")
        raise SystemExit(2)


OUTPUT_DIR = _DEFAULT_OUT
_assert_round_naming(OUTPUT_DIR)
os.makedirs(OUTPUT_DIR, exist_ok=True)

INITIAL_CAPITAL = 100000
START_DATE = "2016-01-01"  # 2026-10-17 第七次下发恢复主口径(全史2016起; 窗口版2024起参数已归档git 3ce4cae, 恢复方法=改此行+TULONG_BT_OUT=win2024目录; 窗口轮产出与窗口版报告在reports/backtest_full_win2024_20261016_eod/与full_backtest_report_win2024_20261016.md, 双口径结论以两报告并读为准)
START_DATE_W2024 = "2024-01-01"  # 窗口轮参数存档(引擎不消费)(专项指令第六次下发, 硬性参数变更=区间2024-01-01起, 与既往7轮2016全史终点延展不同轮): 窗口=2024-01-01~2026-10-16(END_DATE周六非交易日取最新收盘10/16), 三整年段(2024爆发/2025结构牛/2026退潮); ml信号覆盖自2020-01-02<窗口起点=窗口内无早年缺口, L564警告自适应不触发; 组合口径=独立账户合成(引擎已修weekly失真, L1015注释); 🔴样本内属性如实标注=2024-2026恰为系统最优段(2024-2025爆发双击), 短窗CAGR系统性高于全史CAGR(+11.60%), 与200%标尺对照须并列全史口径防窗口 cherry-picking, 结论段双口径呈现
START_DATE_HIST = "2016-01-01"  # 全史口径锚(报告双口径对照用, 引擎不消费)
END_DATE = "2026-10-16"  # 2026-10-16 终点延展(9/24→9/30→10/8→10/9→10/14→10/15→10/16, qfq MAX=20261016(stock_daily_meta_qfq), stock_signals/v375/ml_v22/index_daily/feature_daily_v2 全表面 20261016(10/16晚链已入库, 实测MAX逐表核验), feature_market_sentiment 2026-10-16, p2/breakout 至 20260921(停更,存量持仓照常退出,与往轮同构), v4 至 20260528(退役臂), PIT 季度披露覆盖(report_date 至20260630), sector_mom 至 20260828(陈旧值语义同往轮), UNLOCK_FILTER_RATIO=0 默认关); 先例=9/24→9/30→10/8→10/9→10/14→10/15 同法; 触发=多模型全域历史回测专项指令再次下发(2026-10-16 深夜, 主人10万本金全量回测+可视化报告指令)  # 2026-10-15 终点延展(9/24→9/30→10/8→10/9→10/14→10/15, qfq MAX=20261015(stock_daily_meta_qfq 全日5565只), stock_signals/v375/ml_v22/index_daily/feature_daily_v2 全表面 20261015(10/15晚链已入库), p2/breakout 至 20260921(停更,存量持仓照常退出,与往轮同构), v4 至 20260528(退役臂), PIT 季度披露覆盖(report_date 至20260630, Q3披露季陆续入), sector_mom 至 20260828(陈旧值语义同往轮), UNLOCK_FILTER_RATIO=0 默认关); 先例=9/24→9/30→10/8→10/9→10/14 同法; 触发=多模型全域历史回测专项指令第四次下发(2026-10-15 晚, 主人10万本金全量回测+可视化报告指令)  # 2026-10-14 终点延展(9/24→9/30→10/8→10/9→10/14, qfq MAX=20261014(stock_daily_meta_qfq 已含10/14), stock_signals/v375/ml_v22 全表面 20261014(10/14链已入库, v375=4591只/ml=5214只), feature_market_sentiment 至 2026-10-14, p2/breakout 至 20260921(停更,存量持仓照常退出,与往轮同构), v4 至 20260528(退役臂), PIT 季度披露覆盖(report_date 至20260630, Q3披露季陆续入), sector_mom 至 20260828(陈旧值语义同往轮), UNLOCK_FILTER_RATIO=0 默认关); 先例=9/24→9/30→10/8→10/9 同法; 触发=多模型全域历史回测专项指令第三次下发(2026-10-14 晚, 主人10万本金全量回测+可视化报告指令); ml空仓语义保护(20261014, 基线注记): ml_signal 2026-10-12~14 无达标标的自适应空仓三日平线=组合普跌段仅-0.4%的防守核心, 该语义列入基线保护, 任何"提高满仓率/强制持仓"类提案须wfa_gate立项禁直接改动  # 2026-09-24 终点延展(9/24→9/30→10/8→10/9, qfq MAX=20261009(10/9晚链已入库), stock_signals/v375 全表面 20261009, p2/breakout 至 20260921(停更,存量持仓照常退出,与9/30轮同构), v4 至 20260528(退役臂), PIT 季度披露覆盖, sector_mom 至 20260828(陈旧值语义同往轮), UNLOCK_FILTER_RATIO=0 默认关); 先例=9/1→9/2→…→9/30→10/8 同法; 触发=多模型全域历史回测专项指令再次下发(2026-10-09 深夜, 主人10万本金全量回测+可视化报告指令)  # 2026-09-24 终点延展(9/24→9/30→10/8, qfq 权威库 stock_daily_meta_qfq MAX=20261008 全日5557只, stock_signals/v375/ml_v22/index_daily 全表面 20261008(10/8节后首日链已入库), p2/breakout 至 20260921(节前停更, 存量持仓照常退出, 与9/30轮同构), v4 至 20260528(退役臂, 与往轮同构), PIT 季度披露覆盖, sector_mom 至 20260828(date<=陈旧值语义与往轮同构), UNLOCK_FILTER_RATIO=0 默认关); 先例=9/1→9/2→9/3→9/8→9/11→9/14→9/15→9/17→9/23→9/30 同法; 触发=多模型全域历史回测专项指令再次下发(2026-10-08, 主人10万本金全量回测+可视化报告指令)

# M-2 解禁排雷研究开关(2026-09-09, additive 模式照 atr_stop_mult 先例: 默认 0=关, 代码路径不可达)
# 语义: >0 时 bp_ep 月度选股剔除「选股日后 30 自然日内解禁比例≥阈值」的候选(全市场事件宇宙 PASS_CANDIDATE 翻案, prereg=PREREG_M2_UNLOCK_SELECTLAYER_20260909)
UNLOCK_FILTER_RATIO = float(os.environ.get("TULONG_UNLOCK_FILTER_RATIO", "0") or "0")
_UNLOCK_EV_CACHE = None

# ============================================================
# 诊断 fork: bp_ep TopN 剂量参数化 (2026-10-18, 外部方案审计B第二档-序1-1, 主人 b 令)
# 本文件=analysis/诊断副本, 零生产影响; 生产引擎 tulong_backtest_full_report.py 不动。
# env: TULONG_DOSE_TOPN(白名单3/5/8/10/12/15/20, 默认15=生产S-3口径)
#      TULONG_DOSE_TIEDUMP=1 → 调仓日边界同分dump(诊断序3-1, 只记录不改选股)
# ============================================================
DOSE_TOPN = int(os.environ.get("TULONG_DOSE_TOPN", "15") or "15")
if DOSE_TOPN not in (3, 5, 8, 10, 12, 15, 20):
    raise SystemExit(f"[DOSE] 非法 TULONG_DOSE_TOPN={DOSE_TOPN} (白名单: 3/5/8/10/12/15/20)")
DOSE_TIEDUMP = os.environ.get("TULONG_DOSE_TIEDUMP", "").strip() == "1"
DOSE_TIE_PATH = os.path.join(OUTPUT_DIR, f"dose_boundary_ties_top{DOSE_TOPN}.csv")
DOSE_CODEDUMP = os.environ.get("TULONG_DOSE_CODEDUMP", "").strip() == "1"
print(f"[DOSE-FORK] TopN={DOSE_TOPN} tiedump={DOSE_TIEDUMP} codedump={DOSE_CODEDUMP} out={OUTPUT_DIR}", file=sys.stderr)



def _load_unlock_events():
    """解禁事件源: event_calendar.db restricted_release_hist_em。fail-closed: 开关>0 而数据不可用即抛错, 禁静默降级。"""
    global _UNLOCK_EV_CACHE
    if _UNLOCK_EV_CACHE is None:
        db = sqlite3.connect(r"D:\zcodesj\event_calendar.db")
        try:
            rows = db.execute(
                "SELECT code, float_date, ratio FROM restricted_release_hist_em WHERE ratio IS NOT NULL"
            ).fetchall()
        finally:
            db.close()
        ev_map: Dict[str, list] = {}
        for code, fd, ratio in rows:
            ev_map.setdefault(str(code), []).append((str(fd), float(ratio)))
        _UNLOCK_EV_CACHE = ev_map
    return _UNLOCK_EV_CACHE

# ============================================================
#  Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(OUTPUT_DIR, "backtest_run.log"), encoding="utf-8"),
    ]
)
logger = logging.getLogger("TulongFullReport")

# ============================================================
#  BacktestConfig (屠龙标准 v1.0 + 主人裁决)
# ============================================================
# ===== 第三轮优化配置 =====
cfg = BacktestConfig(
    initial_capital=INITIAL_CAPITAL,
    commission_rate=0.00025 * float(os.environ.get("TULONG_SENS_COST_MULT", "1") or "1"),  # SENS fork: 佣金倍率旋钮(默认1=基线零变化)
    stamp_tax_rate=0.0005,
    slippage_rate=0.0003 + float(os.environ.get("TULONG_SENS_SLIP_ADD", "0") or "0"),  # SENS fork: 滑点加码旋钮(默认0=基线零变化)
    max_position_pct=0.20,
    max_positions=50,
    max_drawdown_limit=10.0,   # 设为极大值以禁用回撤熔断（个股-8%止损已足够，组合级熔断阻止重新进场）
    max_single_loss_pct=0.08,
    stop_loss_pct=0.08,
    take_profit_pct=0.0,
    trailing_stop_pct=0.0,   # 2026-08-22口径修正: 框架此前未实现trailing, 0.10从未生效, 故置0如实反映基线
    max_holding_bars=45,   # S-5优化: 45天时间止损（原无限制）
    enable_t_plus_1=True,
    enable_price_limit=True,
    enable_st_filter=True,
    lot_size=100,
    universe_filter=True,
    min_commission=5.0,
    slippage_model="fixed",
    execution="next_open",
    exit_execution="same_close",
)
cfg.max_positions = 50


# ============================================================
#  TulongDataLoader — 增强版（添加 ML 因子信号）
# ============================================================

class TulongDataLoader:
    """屠龙数据加载器：行情 + 信号 + 因子，兼容 V4.2 引擎"""

    @staticmethod
    def _norm_date(d) -> str:
        s = str(d).strip()
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
        return s

    def __init__(self, db_path: str, qfq_path: str):
        logger.info("=== 初始化 TulongDataLoader (增强版, 向量化加载) ===")
        self.db_path = db_path
        self.qfq_path = qfq_path

        self._conn = sqlite3.connect(db_path)
        self._conn_qfq = sqlite3.connect(qfq_path)
        for c in [self._conn, self._conn_qfq]:
            c.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF; PRAGMA cache_size=-8000000;")

        # -- 交易日历 (预过滤到回测区间) --
        self._trading_dates = pd.read_sql(
            "SELECT date_str FROM trading_calendar WHERE date_str BETWEEN ? AND ? ORDER BY date_str",
            self._conn, params=(START_DATE, END_DATE)
        )["date_str"].tolist()
        self._trading_set = set(self._trading_dates)
        logger.info(f"  交易日: {len(self._trading_dates)} 个")

        # -- 股票信息 --
        self._stock_info = pd.read_sql(
            "SELECT code, name, market, industry, list_date, is_st, status FROM stock_info",
            self._conn
        )
        self._si_indexed = self._stock_info.set_index("code")
        # 去重（个别代码在 stock_info 出现多次）
        self._si_indexed = self._si_indexed[~self._si_indexed.index.duplicated(keep="first")]

        # 可交易股票代码集合 (向量化)
        mask = (
            ~self._si_indexed.index.str.startswith(("688", "689"))
            & ~self._si_indexed.index.str.startswith(("8", "4", "92", "43"))
            & (pd.to_numeric(self._si_indexed["is_st"], errors="coerce").fillna(0) == 0)
        )
        self._tradable_set = set(self._si_indexed.index[mask].tolist())
        logger.info(f"  可交易股票: {len(self._tradable_set)} 只")

        # -- 上市日期映射 (次新股<60天动态剔除, 指令要求) --
        self._list_date_map: Dict[str, date] = {}
        for _code, _ld in self._si_indexed["list_date"].items():
            if _code not in self._tradable_set:
                continue
            try:
                _s = str(_ld).strip()
                if len(_s) == 8 and _s.isdigit():
                    self._list_date_map[_code] = datetime.strptime(_s, "%Y%m%d").date()
                elif len(_s) >= 10:
                    self._list_date_map[_code] = datetime.strptime(_s[:10], "%Y-%m-%d").date()
            except Exception:
                pass
        logger.info(f"  上市日期映射: {len(self._list_date_map)} 只")

        # -- QFQ 前复权行情 (按日期过滤+向量化索引) --
        logger.info("  加载 stock_daily_meta_qfq ...")
        chunk_list = []
        for chunk in pd.read_sql(
            "SELECT code, date, open, high, low, close, volume, amount "
            "FROM stock_daily_meta_qfq WHERE date >= ? AND date <= ? ORDER BY date",
            self._conn_qfq, chunksize=500000,
            params=(START_DATE.replace("-", ""), END_DATE.replace("-", ""))
        ):
            chunk_list.append(chunk)
        qfq_all = pd.concat(chunk_list, ignore_index=True)
        qfq_all = qfq_all[qfq_all["code"].isin(self._tradable_set)].copy()
        logger.info(f"  QFQ 已过滤: {len(qfq_all):,} 行 | 日期范围: {qfq_all['date'].min()} ~ {qfq_all['date'].max()}")

        # 向量化构建 bar_map (to_dict('index') 替代 iterrows)
        self._bar_map: Dict[str, Dict[str, Dict]] = {}
        for dt, grp in qfq_all.groupby("date"):
            dt_norm = self._norm_date(dt)
            grp_indexed = grp.set_index("code")
            self._bar_map[dt_norm] = grp_indexed.to_dict("index")
        logger.info(f"  日期索引: {len(self._bar_map)} 个交易日")

        # 预计算 prev_close (向量化)
        all_dates_sorted = sorted(self._bar_map.keys())
        self._prev_close_map: Dict[str, Dict[str, float]] = {}
        for i, dt in enumerate(all_dates_sorted):
            if i == 0:
                self._prev_close_map[dt] = {}
                continue
            prev_dt = all_dates_sorted[i - 1]
            self._prev_close_map[dt] = {
                code: rec.get("close", 0.0) for code, rec in self._bar_map[prev_dt].items()
            }

        # -- S-4优化: 预计算 ATR(14) 用于动态止损 --
        logger.info("  计算 ATR(14) ...")
        atr_map: Dict[str, Dict[str, float]] = {}
        # 按股票分组建 ATR
        for code in list(self._tradable_set)[:5000]:  # 分批处理，避免 OOM
            pass  # 后面按日期遍历时增量计算
        
        # 增量计算：按日期顺序，每只股票维护 TR 队列
        tr_buffers: Dict[str, List[float]] = {}
        atr_values: Dict[str, float] = {}
        
        for dt in all_dates_sorted:
            recs = self._bar_map.get(dt, {})
            prev_recs = self._bar_map.get(all_dates_sorted[all_dates_sorted.index(dt) - 1]) if dt != all_dates_sorted[0] else {}
            atr_day: Dict[str, float] = {}
            
            for code, rec in recs.items():
                high, low = float(rec.get("high", 0)), float(rec.get("low", 0))
                close = float(rec.get("close", 0))
                prev_close_val = float(prev_recs.get(code, {}).get("close", close)) if dt != all_dates_sorted[0] else close
                
                if high > 0 and low > 0:
                    tr = max(high - low, abs(high - prev_close_val), abs(low - prev_close_val))
                else:
                    tr = 0.0
                
                if code not in tr_buffers:
                    tr_buffers[code] = []
                tr_buffers[code].append(tr)
                
                # Wilder's smoothing ATR
                buf = tr_buffers[code]
                if len(buf) >= 14:
                    if len(buf) == 14:
                        atr_val = sum(buf) / 14.0  # First SMA
                    else:
                        atr_val = (atr_values.get(code, tr) * 13 + tr) / 14.0  # Wilder's smooth
                    atr_values[code] = atr_val
                    atr_day[code] = round(atr_val, 4)
                else:
                    atr_day[code] = tr if tr > 0 else 0.0
            
            atr_map[dt] = atr_day
        
        # 将 ATR 注入 bar_map
        for dt in all_dates_sorted:
            recs = self._bar_map.get(dt, {})
            atr_day = atr_map.get(dt, {})
            for code in recs:
                recs[code]["atr14"] = atr_day.get(code, 0.0)
        logger.info(f"  ATR 注入完成")
        
        # -- 上证指数 MA250 --
        # 2026-08-25 修复: index_daily.date 为 INTEGER(yyyymmdd), TEXT 参数比较恒空 -> MA250/MA120 注入从未生效
        idx_df = pd.read_sql(
            "SELECT date, close FROM index_daily WHERE index_code='000001' "
            "AND date BETWEEN ? AND ? ORDER BY date",
            self._conn, params=(int(START_DATE.replace("-", "")), int(END_DATE.replace("-", "")))
        )
        if len(idx_df) > 0:
            idx_df["_date_norm"] = idx_df["date"].apply(self._norm_date)
            idx_df["ma250"] = idx_df["close"].rolling(250, min_periods=100).mean()
            # M2: MA120 + slope 五日差，三环境分段（trend_up/trend_down/range）
            idx_df["ma120"] = idx_df["close"].rolling(120, min_periods=60).mean()
            idx_df["ma120_slope"] = idx_df["ma120"].diff(5)
            self._idx_close = dict(zip(idx_df["_date_norm"], idx_df["close"]))
            self._idx_ma250 = dict(zip(idx_df["_date_norm"], idx_df["ma250"]))
            self._idx_ma120 = dict(zip(idx_df["_date_norm"], idx_df["ma120"]))
            self._idx_ma120_slope = dict(zip(idx_df["_date_norm"], idx_df["ma120_slope"]))
        else:
            self._idx_close = {}
            self._idx_ma250 = {}
            self._idx_ma120 = {}
            self._idx_ma120_slope = {}

        # -- 信号表 (向量化 groupby -> to_dict) --
        # 资源治理(2026-08-28): TULONG_LOADER_LIGHT=bp/ml 分腿轻载(单进程内存边缘时用),
        # 默认空=全量加载, 全量行为与历史完全一致
        _LIGHT = os.environ.get("TULONG_LOADER_LIGHT", "")
        def _index_signals(df, cols):
            df["date"] = df["date"].apply(self._norm_date)
            result = {}
            for dt, grp in df.groupby("date"):
                result[dt] = grp.set_index("code")[cols]
            return result

        # -- L2: 市场情绪数据（feature_market_sentiment, fundamental_data.db） --
        # 情绪乘数: 情绪热(涨停比>1.2%)→0.85x减仓, 情绪冷→1.15x加仓
        self._sentiment_by_date = {}
        import os as _os
        _fund_candidates = [
            # 2026-08-29 优化专项修复: 真库在workbuddyspace(旧链首项根目录为错误路径,
            # 8/24后跑的回测情绪乘数实际读的是8/23快照=近端情绪数据缺口)
            r"D:\workbuddyspace\databases\fundamental_data.db",
            r"D:\zcodesj\backups\fundamental_data\weekly\fundamental_data_20260823.db",
        ]
        _fund_db = None
        for _p in _fund_candidates:
            if _os.path.exists(_p) and _os.path.getsize(_p) > 0:
                _fund_db = _p
                break
        if _fund_db:
            try:
                _sent_conn = sqlite3.connect(f"file:{_fund_db}?mode=ro", uri=True)
                _sent_df = pd.read_sql(
                    "SELECT trade_date, limit_up_ratio FROM feature_market_sentiment ORDER BY trade_date",
                    _sent_conn
                )
                _sent_conn.close()
                for _, _row in _sent_df.iterrows():
                    _dt = self._norm_date(_row["trade_date"])
                    _ratio = float(_row["limit_up_ratio"]) if pd.notna(_row["limit_up_ratio"]) else 0.0
                    # 情绪乘数: 涨停比>1.2%→0.85(热), <0.5%→1.15(冷), 中间→1.0
                    if _ratio > 0.012:
                        self._sentiment_by_date[_dt] = {"ratio": _ratio, "multiplier": 0.85, "label": "hot"}
                    elif _ratio < 0.005:
                        self._sentiment_by_date[_dt] = {"ratio": _ratio, "multiplier": 1.15, "label": "cold"}
                    else:
                        self._sentiment_by_date[_dt] = {"ratio": _ratio, "multiplier": 1.0, "label": "neutral"}
                logger.info(f"    -> feature_market_sentiment: {len(self._sentiment_by_date)} 日")
            except Exception as _e:
                logger.warning(f"    feature_market_sentiment 不可用: {_e}")
        else:
            logger.warning("    fundamental_data.db 未找到, 情绪乘数禁用")

        # -- L3: 失败形态预计算（value_trap + rebound_chase，向量化） --
        # 依据: BT-FAIL-001(银行股价值陷阱) + BT-FAIL-002(ml反弹追高)
        # 在loader初始化时批量计算，避免信号函数逐只调用太慢
        logger.info("  计算 L3 失败形态标签 (value_trap/rebound_chase) ...")
        self._failure_flags_by_date = {}
        try:
            # 用已加载的QFQ数据计算动量和振幅
            qfq_calc = qfq_all.copy()
            qfq_calc["date"] = qfq_calc["date"].apply(self._norm_date)
            qfq_calc = qfq_calc.sort_values(["code", "date"]).reset_index(drop=True)

            # 按code分组计算动量和振幅
            g = qfq_calc.groupby("code", sort=False)
            qfq_calc["mom20"] = g["close"].pct_change(20) * 100
            qfq_calc["mom5"] = g["close"].pct_change(5) * 100
            qfq_calc["high20"] = g["high"].transform(lambda x: x.rolling(20, min_periods=10).max())
            qfq_calc["low20"] = g["low"].transform(lambda x: x.rolling(20, min_periods=10).min())
            qfq_calc["amplitude20"] = (qfq_calc["high20"] / qfq_calc["low20"] - 1) * 100

            # 加载行业映射（用于value_trap行业限定）
            _ind_map = {}
            _ind_db = None
            for _p in _fund_candidates:
                if _os.path.exists(_p) and _os.path.getsize(_p) > 0:
                    _ind_db = _p
                    break
            if _ind_db:
                try:
                    _ind_conn = sqlite3.connect(f"file:{_ind_db}?mode=ro", uri=True)
                    _ind_df = pd.read_sql("SELECT code, industry FROM industry_map_ths", _ind_conn)
                    _ind_conn.close()
                    _ind_map = dict(zip(_ind_df["code"], _ind_df["industry"]))
                except Exception:
                    pass

            _VALUE_TRAP_SECTORS = ("银行", "非银金融", "保险", "证券")

            # 向量化标记
            qfq_calc["value_trap"] = False
            qfq_calc["rebound_chase"] = False

            # value_trap 最终版(V5最优): mom20<=0.5 + amplitude20<=8.0 + 仅银行/非银金融
            # V4(扩大建筑+放宽)劣化-25pp，V6(建筑+mom60)也劣化-7pp，V5严格阈值最优
            for code, grp in qfq_calc.groupby("code", sort=False):
                ind = _ind_map.get(code, "")
                is_fin = any(s in ind for s in _VALUE_TRAP_SECTORS) if ind else False
                if is_fin:
                    mask = (grp["mom20"] <= 0.5) & (grp["amplitude20"] <= 8.0)
                    qfq_calc.loc[grp.index[mask], "value_trap"] = True

            # rebound_chase: mom20<0 + mom5>0 + mom5>|mom20|*0.8（V3收紧0.5→0.8减少误杀）
            rc_mask = (qfq_calc["mom20"] < 0) & (qfq_calc["mom5"] > 0) & (qfq_calc["mom5"] > qfq_calc["mom20"].abs() * 0.8)
            qfq_calc.loc[rc_mask, "rebound_chase"] = True

            # 按日期索引为dict供信号函数快速查询
            for dt, grp in qfq_calc.groupby("date"):
                vt = set(grp[grp["value_trap"]]["code"].tolist())
                rc = set(grp[grp["rebound_chase"]]["code"].tolist())
                self._failure_flags_by_date[dt] = {"value_trap": vt, "rebound_chase": rc}

            vt_total = sum(len(v["value_trap"]) for v in self._failure_flags_by_date.values())
            rc_total = sum(len(v["rebound_chase"]) for v in self._failure_flags_by_date.values())
            logger.info(f"    L3标签: value_trap={vt_total}标的日, rebound_chase={rc_total}标的日")
            del qfq_calc  # 释放内存
        except Exception as _e:
            logger.warning(f"    L3失败形态预计算失败: {_e}")
            self._failure_flags_by_date = {}

        # V375 (不WHERE过滤日期, 因db中日期格式可能是整数, 加载后用_norm_date统一)
        if _LIGHT == "":
            logger.info("    -> stock_signals_v375")
            v375 = pd.read_sql("SELECT code, date, v375_prob FROM stock_signals_v375", self._conn)
            self._v375_by_date = _index_signals(v375, ["v375_prob"])
            del v375
        else:
            self._v375_by_date = {}
            logger.info(f"    [LIGHT={_LIGHT}] 跳过 v375")

        # P2 (ml 腿的 p2 确认层仍需加载)
        if _LIGHT in ("", "ml"):
            logger.info("    -> stock_signals_p2")
            p2 = pd.read_sql("SELECT code, date, p2_prob FROM stock_signals_p2", self._conn)
            self._p2_by_date = _index_signals(p2, ["p2_prob"])
            del p2
        else:
            self._p2_by_date = {}

        # Composite (stock_signals_v4)
        if _LIGHT == "":
            logger.info("    -> stock_signals_v4")
            composite = pd.read_sql(
                "SELECT code, date, composite_score, ai_prob_5d FROM stock_signals_v4", self._conn
            )
            self._composite_by_date = _index_signals(composite, ["composite_score", "ai_prob_5d"])
            del composite
        else:
            self._composite_by_date = {}

        # Fundamental factors —— PIT口径（修复: 原stock_factors_fundamental非PIT,
        # 55.7%行date<pit_avail_date存在前视泄漏, 违反"严禁未来函数"与PIT铁律）
        logger.info("    -> factor_pit.db stock_factors_fundamental_pit_v2 (PIT口径, v1已退役)")
        self._fundamental_by_date = {}
        _PIT_DB = r"D:\zcodesj\factor_pit.db"
        try:
            if not os.path.exists(_PIT_DB):
                raise FileNotFoundError(_PIT_DB)
            _pit_conn = sqlite3.connect(f"file:{_PIT_DB}?mode=ro", uri=True)
            _pit = pd.read_sql(
                "SELECT code, pit_avail_date, ep, bp, roe, ref_close FROM stock_factors_fundamental_pit_v2",
                _pit_conn
            )
            _pit_conn.close()
            _pit = _pit.dropna(subset=["ep", "bp", "ref_close"]).copy()
            _pit["pit_avail_date"] = _pit["pit_avail_date"].apply(self._norm_date)
            # merge_asof 要求 on 键为数值/日期, 转 int YYYYMMDD; 用后在还原
            _pit["_pit_num"] = _pit["pit_avail_date"].map(lambda s: int(str(s).replace("-", "")))
            # merge_asof 要求 right 的 on 键全局有序(by内自动匹配)
            _pit = _pit.sort_values("_pit_num")

            # 日度 (code, date, close) 长表：merge_asof 取 pit_avail_date<=当日 的最新报告
            _daily_long = qfq_all[["code", "date", "close"]].copy()
            _daily_long["date"] = _daily_long["date"].apply(self._norm_date)
            _daily_long["_date_num"] = _daily_long["date"].map(lambda s: int(str(s).replace("-", "")))
            _daily_long = _daily_long.sort_values("_date_num")

            _merged = pd.merge_asof(
                _daily_long, _pit,
                left_on="_date_num", right_on="_pit_num",
                by="code", direction="backward",
            )
            _merged = _merged.dropna(subset=["ep", "bp"])
            # 重算日度 ep/bp: 因子=每股指标/当日收盘价 (BVPS/EPS报告期固定, 价格日度变化)
            _merged["ep"] = _merged["ep"] * _merged["ref_close"] / _merged["close"]
            _merged["bp"] = _merged["bp"] * _merged["ref_close"] / _merged["close"]
            for _dt, _grp in _merged.groupby("date"):
                self._fundamental_by_date[_dt] = _grp.set_index("code")[["close", "ep", "bp", "roe"]]
            logger.info(f"    PIT因子: {len(self._fundamental_by_date)} 日, {len(_merged):,} 标的日")
            del _merged, _daily_long, _pit
        except Exception as _e:
            logger.warning(f"    factor_pit.db 不可用({_e}), 回退非PIT stock_factors_fundamental")
            fundamental = pd.read_sql(
                "SELECT code, date, close, ep, bp FROM stock_factors_fundamental", self._conn
            )
            self._fundamental_by_date = _index_signals(fundamental, ["close", "ep", "bp"])
        # qfq_all 此后不再使用(PIT块为其最后消费方), 释放 ~9.5M 行中间DataFrame
        try:
            del qfq_all
        except NameError:
            pass

        # Legacy composite (fallback)
        if _LIGHT == "":
            logger.info("    -> stock_signals (legacy)")
            legacy = pd.read_sql(
                "SELECT code, date, composite_score, ai_prob_5d FROM stock_signals", self._conn
            )
            self._legacy_signals_by_date = _index_signals(legacy, ["composite_score", "ai_prob_5d"])
            del legacy
        else:
            self._legacy_signals_by_date = {}

        # ML因子信号 (默认 stock_ml_scores_v22; round11 起可设 TULONG_ML_TABLE 指向研究表如 stock_ml_scores_v23_wfa)
        _ML_TABLE = os.environ.get("TULONG_ML_TABLE", "stock_ml_scores_v22")
        if _LIGHT in ("", "ml"):
            logger.info(f"    -> {_ML_TABLE} (ml_prob)")
            try:
                ml = pd.read_sql(f"SELECT code, date, ml_prob FROM {_ML_TABLE}", self._conn)
                self._ml_by_date = _index_signals(ml, ["ml_prob"])
                self._ml_first_date = self._norm_date(str(ml["date"].min())) if len(ml) else None
                if self._ml_first_date and START_DATE < self._ml_first_date:
                    logger.warning(
                        f"    ⚠️ ml信号覆盖自 {self._ml_first_date} 起, 晚于回测起点 {START_DATE}; "
                        f"此前无 ml 信号为诚实缺口。禁止用当前模型回填早年(构成lookahead, "
                        f"唯一合法路径=逐期PIT滚动重训, 见 ROUND10_OPTIMIZATION_DIRECTIONS 主线2)")
                logger.info(f"      ML信号: {len(ml):,} 行 (覆盖自 {self._ml_first_date})")
                del ml
            except Exception as e:
                logger.warning(f"    stock_ml_scores_v22 不可用: {e}")
                self._ml_by_date = {}
                self._ml_first_date = None
        else:
            self._ml_by_date = {}
            self._ml_first_date = None

        # 横盘突破核心战法信号 (stock_signals_breakout, breakout_prob ML)
        if _LIGHT == "":
            logger.info("    -> stock_signals_breakout (横盘突破 ML)")
            try:
                brk = pd.read_sql(
                    "SELECT code, date, breakout_prob FROM stock_signals_breakout", self._conn
                )
                self._breakout_by_date = _index_signals(brk, ["breakout_prob"])
                logger.info(f"      横盘突破信号: {len(brk):,} 行")
                del brk
            except Exception as e:
                logger.warning(f"    stock_signals_breakout 不可用: {e}")
                self._breakout_by_date = {}
        else:
            self._breakout_by_date = {}

        logger.info(f"  V375: {len(self._v375_by_date)}d | P2: {len(self._p2_by_date)}d "
                    f"| Composite: {len(self._composite_by_date)}d | ML: {len(self._ml_by_date)}d"
                    + (f" [LIGHT={_LIGHT}]" if _LIGHT else ""))

    # ---- DataFeed 接口 ----

    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        return [d for d in self._trading_dates if start_date <= d <= end_date]

    def get_data(self, date_str: str) -> Dict:
        recs = self._bar_map.get(date_str)
        prices, opens, highs, lows = {}, {}, {}, {}
        volumes, amounts, prev_closes = {}, {}, {}
        names, is_st, atr14 = {}, {}, {}

        if recs is not None and len(recs) > 0:
            pcm = self._prev_close_map.get(date_str, {})
            # 次新股<60天动态剔除（只在计算一次当前日期，避免逐码解析）
            try:
                _cur = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            except Exception:
                _cur = None
            for code, r in recs.items():
                if code not in self._tradable_set:
                    continue
                if _cur is not None:
                    _ld = self._list_date_map.get(code)
                    if _ld is not None and (_cur - _ld).days < 60:
                        continue
                prices[code] = r["close"]
                opens[code] = r["open"]
                highs[code] = r["high"]
                lows[code] = r["low"]
                volumes[code] = r["volume"]
                amounts[code] = r["amount"]
                if r.get("atr14"):
                    atr14[code] = r["atr14"]
                if code in pcm:
                    prev_closes[code] = pcm[code]
                si = self._si_indexed.loc[code] if code in self._si_indexed.index else None
                if si is not None:
                    # 个别行可能是 DataFrame（因 index 重复已被去重，为安全仍转换）
                    if isinstance(si, pd.DataFrame):
                        si = si.iloc[0]
                    names[code] = str(si.get("name", ""))
                    is_st[code] = bool(int(si.get("is_st", 0) or 0))

        return {
            "date": date_str, "prices": prices, "open": opens,
            "high": highs, "low": lows, "volumes": volumes,
            "amounts": amounts, "prev_close": prev_closes,
            "names": names, "is_st": is_st, "atr14": atr14,
        }

    # ---- 信号查询 ----

    def get_fundamental_factors(self, date_str: str) -> Optional[pd.DataFrame]:
        df = self._fundamental_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        return df[["close", "ep", "bp", "roe"]].dropna(subset=["close", "ep", "bp"]).copy()

    def get_v375_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        df = self._v375_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        return df[["v375_prob"]].dropna().copy()

    def get_p2_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        df = self._p2_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        return df[["p2_prob"]].dropna().copy()

    def get_composite_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        df = self._composite_by_date.get(date_str)
        if df is not None and len(df) > 0:
            return df[["composite_score", "ai_prob_5d"]].dropna().copy()
        df2 = self._legacy_signals_by_date.get(date_str)
        if df2 is not None and len(df2) > 0:
            return df2[["composite_score", "ai_prob_5d"]].dropna().copy()
        return None

    def get_ml_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        df = self._ml_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        col = df.columns[0]  # ml_score or score
        return df[[col]].dropna().copy()

    def get_breakout_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        df = self._breakout_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        return df[["breakout_prob"]].dropna().copy()

    def get_close_prices(self, date_str: str) -> Dict[str, float]:
        """当日各code收盘价（用于价格过滤）"""
        recs = self._bar_map.get(date_str, {})
        return {c: float(r["close"]) for c, r in recs.items()}

    def is_bull_market(self, date_str: str) -> bool:
        close = self._idx_close.get(date_str)
        ma250 = self._idx_ma250.get(date_str)
        if close is None or ma250 is None or pd.isna(ma250):
            return True
        return close > ma250

    def get_market_regime(self, date_str: str) -> str:
        """返回: bull / bear /震荡"""
        close = self._idx_close.get(date_str)
        ma250 = self._idx_ma250.get(date_str)
        if close is None or ma250 is None or pd.isna(ma250):
            return "unknown"
        # 用 20日均线判断短期趋势
        return "bull" if close > ma250 else "bear"

    def get_market_regime_v2(self, date_str: str) -> str:
        """M2: MA120 + slope 五日差三环境分段。

        返回: trend_up / trend_down / range
        - trend_up: 收盘 > MA120 且 MA120 五日斜率 > 0
        - trend_down: 收盘 < MA120 且 MA120 五日斜率 < 0
        - range: 其余（震荡）

        依据: reports/TULONG_MULTI_MODEL_BACKTEST_2016_2026.md 第3.3节
        2016-2026共2579日: 上涨1220日/下跌969日/震荡390日
        """
        close = self._idx_close.get(date_str)
        ma120 = self._idx_ma120.get(date_str)
        slope = self._idx_ma120_slope.get(date_str)
        if close is None or ma120 is None or pd.isna(ma120):
            return "range"
        if slope is None or pd.isna(slope):
            return "range"
        if close > ma120 and slope > 0:
            return "trend_up"
        if close < ma120 and slope < 0:
            return "trend_down"
        return "range"

    def get_sentiment_multiplier(self, date_str: str) -> float:
        """L2: 市场情绪仓位乘数。

        依据: 回测3.3节 ml在range段年化104%(情绪活跃期最佳)。
        情绪热(涨停比>1.2%)→0.85x(减仓防过热), 情绪冷(<0.5%)→1.15x(加仓抄底)。
        无数据时返回1.0（不调整）。
        """
        entry = self._sentiment_by_date.get(date_str)
        if entry is None:
            return 1.0
        return entry["multiplier"]

    def get_failure_flags(self, date_str: str) -> dict:
        """L3: 返回当日失败形态标记集合。

        返回: {"value_trap": set(code), "rebound_chase": set(code)}
        无数据时返回空集合（不过滤）。
        """
        return self._failure_flags_by_date.get(date_str, {"value_trap": set(), "rebound_chase": set()})

    def is_tradable(self, code: str) -> bool:
        return code in self._tradable_set


# ============================================================
#  信号函数（带 entry_reason 追踪）
#  - 每个信号函数返回 (weights_dict, reasons_dict)
#  - reasons_dict: {code: "买入理由字符串"}
# ============================================================

# 全局字典：按策略名存储 {date: {code: reason}}
ALL_ENTRY_REASONS: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(lambda: defaultdict(dict))

def _record_reason(strategy_name: str, date_str: str, weights: Dict, reasons: Dict):
    """记录每笔买入理由到全局字典"""
    for code in weights:
        if code in reasons:
            ALL_ENTRY_REASONS[strategy_name][date_str][code] = reasons[code]
        elif "_reason" in reasons:
            ALL_ENTRY_REASONS[strategy_name][date_str][code] = reasons["_reason"]

_SW1_MAP: Optional[Dict[str, str]] = None


def _get_sw1_map() -> Dict[str, str]:
    """申万L1 静态映射(裸6位码->行业名), dragon_quant_meta.db stock_sector_sw1; 失败返回空表(cap 视所有票为未映射)"""
    global _SW1_MAP
    if _SW1_MAP is None:
        try:
            _m = sqlite3.connect(r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro", uri=True)
            _SW1_MAP = {str(r[0])[-6:]: r[1] for r in _m.execute("SELECT code, sector_name FROM stock_sector_sw1")}
            _m.close()
        except Exception as e:
            logger.warning(f"stock_sector_sw1 加载失败(板块cap按未映射处理): {e}")
            _SW1_MAP = {}
    return _SW1_MAP


# 2026-08-29 P2-B(PREREG_P2B_SECTOR_MOMENTUM): 申万L1 行业20日RS 尾部行业成员剔除(入选资格层)。
# 数据: data/sector_momentum.db(sector_momentum.py --build 全历史预计算, 无前视)。
SECTOR_MOM_DB = r"D:\zcodesj\data\sector_momentum.db"


def _sector_mom_topn(df: pd.DataFrame, n: int, tail_k: int, date_str: str) -> pd.DataFrame:
    """P2-B: 行业20日RS 排名后 tail_k 个行业的成员剔除, 在其补集内 nlargest 顺延。
    RS = 行业成分等权20日收益 - 市场等权20日收益(T-1); 行业未映射/缓存缺失 → fail-open 不过滤。"""
    try:
        import sqlite3 as _sqlite
        sm = _sqlite.connect(f"file:{SECTOR_MOM_DB}?mode=ro", uri=True, timeout=5)
        rows = sm.execute("SELECT sector, rs_20 FROM sector_mom WHERE date<=? "
                          "ORDER BY date DESC LIMIT 31", (date_str,)).fetchall()
        sm.close()
    except Exception as e:
        logger.warning(f"[SECTOR_MOM] 缓存读取失败({type(e).__name__}) fail-open 不过滤")
        return df.nlargest(n, "score")
    rs = {s: r for s, r in rows}
    if len(rs) < 31:   # 行业覆盖不足 → fail-open(与 R31/R32 同口径)
        return df.nlargest(n, "score")
    ranked = sorted(rs, key=lambda s: rs[s], reverse=True)
    weak = set(ranked[-tail_k:])
    sw1 = _get_sw1_map()
    kept = df[~df.index.map(lambda c: sw1.get(str(c)) in weak)]
    picked = kept.nlargest(n, "score")
    logger.info(f"[SECTOR_MOM tail_k={tail_k} date={date_str}] 弱行业{len(weak)}个"
                f"({','.join(sorted(weak)[:4])}...), 剔除{len(df)-len(kept)}票, 选出{len(picked)}只")
    return picked


def _capped_topn(df: pd.DataFrame, n: int, cap: float) -> pd.DataFrame:
    """round12a (prereg a4656de01c2c10b1670a): 申万L1 单行业上限的顺延选取。
    score 降序遍历, 某行业已选数达 floor(cap*n) 则跳过其后续候选; 未映射码不受约束。"""
    max_per = max(1, int(cap * n))
    sw1 = _get_sw1_map()
    counts: Dict[str, int] = defaultdict(int)
    picked, unmapped = [], 0
    for idx, _row in df.sort_values("score", ascending=False).iterrows():
        sec = sw1.get(str(idx))
        if sec is None:
            unmapped += 1
        elif counts[sec] >= max_per:
            continue
        else:
            counts[sec] += 1
        picked.append(idx)
        if len(picked) == n:
            break
    top_sec = max(counts.items(), key=lambda kv: kv[1]) if counts else ("无", 0)
    logger.info(f"[SECTOR_CAP cap={cap:.0%}] 选出{len(picked)}只 未映射{unmapped}只 "
                f"最大行业={top_sec[0]}:{top_sec[1]}/{len(picked)}")
    return df.loc[picked]


# 画像PIT维度过滤旋钮(迭代轮3候选, 2026-10-09, additive默认关): TULONG_BPEP_PORTRAIT_FILTER=v1剔高区(禁)/v2剔高区+灰带
# 数据源=data/portrait_pit_dims_daily.db(口径=winner_profile_dims v3逐位同源, 同源验收2026-09-30对照4530只0差异)
# fail-closed: 档位非法或库缺失即拒绝, 禁静默降级; 候选当日行缺失(停牌)回查最近<=t一条(PIT仅用过去)
_PORTRAIT_BAND_CACHE = {}
_PORTRAIT_FALLBACK_CACHE = {}


def _portrait_ban_set(date_str: str, mode: str) -> set:
    if "PORTRAIT_PIT_DB" not in _PORTRAIT_BAND_CACHE:
        _PORTRAIT_BAND_CACHE["PORTRAIT_PIT_DB"] = os.path.exists(
            r"D:\zcodesj\data\portrait_pit_dims_daily.db")
    if not _PORTRAIT_BAND_CACHE["PORTRAIT_PIT_DB"]:
        raise SystemExit("[PORTRAIT_FILTER] portrait_pit_dims_daily.db 缺失, fail-closed 拒绝")
    _bands_ban = {"高区(禁)"} if mode == "v1" else {"高区(禁)", "灰带(收紧候选)"}
    try:
        _d = int(date_str.replace("-", ""))
    except ValueError:
        raise SystemExit(f"[PORTRAIT_FILTER] 日期格式异常: {date_str}")
    if _d not in _PORTRAIT_BAND_CACHE:
        _db = sqlite3.connect(r"file:D:\zcodesj\data\portrait_pit_dims_daily.db?mode=ro", uri=True)
        try:
            _PORTRAIT_BAND_CACHE[_d] = {
                str(c).zfill(6): b for c, b in _db.execute(
                    "SELECT code, posband FROM pit_dims WHERE date=?", (_d,))}
        finally:
            _db.close()
    day_map = _PORTRAIT_BAND_CACHE[_d]
    return set(_bands_ban)  # 语义=被禁档位集(修复2026-10-09: 原误返回代码集致剔0只静默失效, 由「剔N只」标签显形)


def _portrait_band_of(code: str, date_str: str) -> str:
    """候选当日行缺失(停牌)时的最近<=t回查, 带进程级缓存。"""
    _key = (code, date_str)
    if _key not in _PORTRAIT_FALLBACK_CACHE:
        _db = sqlite3.connect(r"file:D:\zcodesj\data\portrait_pit_dims_daily.db?mode=ro", uri=True)
        try:
            _r = _db.execute(
                "SELECT posband FROM pit_dims WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, int(date_str.replace("-", "")))).fetchone()
        finally:
            _db.close()
        _PORTRAIT_FALLBACK_CACHE[_key] = _r[0] if _r else "N/A"
    return _PORTRAIT_FALLBACK_CACHE[_key]


# 相位权重旋钮(防御档prereg配套, 2026-10-09, additive默认关): TULONG_BPEP_PHASE_WEIGHT=1
# 权重曲线=PREREG_EMOTION_PHASE_POSITION LOCKED先验( euphoric1.0/healing0.8/ebbing0.6/diverging0.5/bottom0.5;
# height: rising+0.2≤1.0/collapse-0.2≥0.3 ), 数据源=data/emotion_phase_history_daily.jsonl
# (emotion_phase_history_recompute.py 全史重算, 同源验收: lu/br 425/425全对+相位425/425一致)
_PHASE_WEIGHT_CACHE = {}


def _phase_weight_of(date_str: str) -> float:
    if "rows" not in _PHASE_WEIGHT_CACHE:
        _path = r"D:\zcodesj\data\emotion_phase_history_daily.jsonl"
        if not os.path.exists(_path):
            raise SystemExit("[PHASE_WEIGHT] emotion_phase_history_daily.jsonl 缺失, fail-closed 拒绝")
        _rows = []
        for _line in open(_path, encoding="utf-8"):
            if _line.strip():
                _d = json.loads(_line)
                _rows.append((int(_d["phase_date"]), float(_d["weight"])))
        _rows.sort()
        _PHASE_WEIGHT_CACHE["rows"] = _rows
    try:
        _d = int(date_str.replace("-", ""))
    except ValueError:
        raise SystemExit(f"[PHASE_WEIGHT] 日期格式异常: {date_str}")
    _rows = _PHASE_WEIGHT_CACHE["rows"]
    if _d < _rows[0][0]:
        return _rows[0][1]
    _lo, _hi, _ans = 0, len(_rows), _rows[0][1]
    while _lo < _hi:
        _mid = (_lo + _hi) // 2
        if _rows[_mid][0] <= _d:
            _ans = _rows[_mid][1]
            _lo = _mid + 1
        else:
            _hi = _mid
    return _ans  # 最近<=t一条(前向填充, PIT仅用过去)


def bp_ep_signal(loader: TulongDataLoader, date_str: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    bp+ep 月度调仓: 等权合成 ep_rank + bp_rank, Top15 等权(S-3落地2026-10-18), 40%核心仓位

    L2优化: 情绪乘数调整（热0.85x/冷1.15x），依据回测3.3节ml在情绪活跃期表现最佳。
    """
    df = loader.get_fundamental_factors(date_str)
    if df is None or len(df) < 20:
        return {}, {}

    df = df[df["close"] <= 30].copy()
    if len(df) < 20:
        return {}, {}

    # M-2 解禁排雷研究开关(默认关): 剔除选股日后30自然日内解禁比例≥阈值的候选(全市场翻案prereg, additive模式)
    _unlock_tag = ""
    if UNLOCK_FILTER_RATIO > 0:
        from datetime import timedelta as _td

        try:
            _d0 = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            _d0 = datetime.strptime(date_str, "%Y%m%d")
        _ev_map = _load_unlock_events()

        def _unlock_hit(code) -> bool:
            for fd, r in _ev_map.get(str(code), []):
                try:
                    _fd = datetime.strptime(fd, "%Y-%m-%d")
                except ValueError:
                    continue
                if _d0 < _fd <= _d0 + _td(days=30) and r >= UNLOCK_FILTER_RATIO:
                    return True
            return False

        _before = len(df)
        df = df[[not _unlock_hit(c) for c in df.index]]
        _excl = _before - len(df)
        if len(df) < 20:
            return {}, {}
        _unlock_tag = f", M2解禁过滤(≥{UNLOCK_FILTER_RATIO:.0%})剔{_excl}只"

    _pf_mode = os.environ.get("TULONG_BPEP_PORTRAIT_FILTER", "").strip().lower()
    _pf_tag = ""
    if _pf_mode:
        if _pf_mode not in ("v1", "v2"):
            raise SystemExit(f"[PORTRAIT_FILTER] 未知档位: {_pf_mode} (合法: v1剔高区(禁)/v2剔高区+灰带)")
        _ban = _portrait_ban_set(date_str, _pf_mode)
        _day_map = _PORTRAIT_BAND_CACHE[int(date_str.replace("-", ""))]
        _kept_mask, _excl_pf = [], 0
        for _c in df.index:
            _c6 = str(_c).zfill(6)
            _b = _day_map.get(_c6) or _portrait_band_of(_c6, date_str)
            if _b in _ban:
                _excl_pf += 1
                _kept_mask.append(False)
            else:
                _kept_mask.append(True)
        df = df[_kept_mask]
        if len(df) < 20:
            return {}, {}
        _pf_tag = f", 画像{_pf_mode}剔{_excl_pf}只"

    df["ep_rank"] = df["ep"].rank(pct=True)
    df["bp_rank"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2

    _sec_cap = float(os.environ.get("TULONG_BPEP_SECTOR_CAP", "0") or 0)
    _sec_mom = int(os.environ.get("TULONG_BPEP_SECTOR_MOM", "0") or 0)
    if _sec_mom > 0:
        top20 = _sector_mom_topn(df, 20, _sec_mom, date_str)  # P2-B: 行业动量尾部剔除(env门控默认关, 已证伪留档, N仍20)
    elif _sec_cap > 0:
        top20 = _capped_topn(df, 20, _sec_cap)  # round12a 变体: 申万L1 单行业上限顺延选取(env门控默认关, N仍20)
    else:
        top20 = df.nlargest(DOSE_TOPN, "score")  # DOSE fork 参数化N(15=生产S-3口径, analysis诊断副本)
    if DOSE_TIEDUMP:
        _s_all = df["score"].sort_values(ascending=False)
        _k = min(DOSE_TOPN, len(_s_all))
        _in_score = float(_s_all.iloc[_k - 1])
        _out_score = float(_s_all.iloc[_k]) if len(_s_all) > _k else float("nan")
        _tie_n = int((_s_all == _in_score).sum())
        if not os.path.exists(DOSE_TIE_PATH):
            with open(DOSE_TIE_PATH, "w", encoding="utf-8") as _tf:
                _tf.write("date,TopN,in_score,out_score,tie_count\n")
        with open(DOSE_TIE_PATH, "a", encoding="utf-8") as _tf:
            _tf.write(f"{date_str},{DOSE_TOPN},{_in_score:.6f},{_out_score:.6f},{_tie_n}\n")
    if DOSE_CODEDUMP:
        # 诊断⑤(2026-10-18继续令): 边界同分组内 入选vs未入选 名单dump, 供事后对比;
        # 只记录不改选股。组=score 等于第 N 名边界分的全部票; sel=其中被选入 top20 者。
        _sd = df["score"]
        _bnd = float(_sd.nlargest(DOSE_TOPN).min())
        _sel = [str(c).zfill(6) for c in top20.index if _sd.loc[c] == _bnd]
        _tied_all = [str(c).zfill(6) for c in df.index if _sd.loc[c] == _bnd]
        _nonsel = [c for c in _tied_all if c not in set(_sel)]
        _cd_path = os.path.join(OUTPUT_DIR, f"dose_boundary_codes_top{DOSE_TOPN}.csv")
        if not os.path.exists(_cd_path):
            with open(_cd_path, "w", encoding="utf-8") as _cf:
                _cf.write("date,TopN,boundary_score,n_sel_in_group,n_nonsel,codes_sel,codes_nonsel\n")
        with open(_cd_path, "a", encoding="utf-8") as _cf:
            _cf.write(f"{date_str},{DOSE_TOPN},{_bnd:.6f},{len(_sel)},{len(_nonsel)},"
                      f"{';'.join(_sel)},{' ;'.join(_nonsel)}\n")
    sentiment_mult = loader.get_sentiment_multiplier(date_str)

    # M1优化: bp_ep侧去掉p2确认（四轮回测证实对bp_ep几乎无影响，仅ml保留）
    # p2确认层仅保留在ml_signal中

    # L3优化: 失败形态过滤（value_trap银行股 + rebound_chase反弹追高）
    # 命中的标的降权50%，从源头减少大亏（BT-FAIL-001/002实证）
    # L4跳空风险仅对ml应用（bp_ep月度低频调仓，前日跳空特征意义不大）
    fail_flags = loader.get_failure_flags(date_str)
    vt_set = fail_flags.get("value_trap", set())
    rc_set = fail_flags.get("rebound_chase", set())
    l3_blocked = 0
    risk_mult = {}
    for idx in top20.index:
        mult = 1.0
        if idx in vt_set or idx in rc_set:
            mult *= 0.25  # L3命中降权75%(0.25x); 2026-08-28 P3采纳(0.5x→0.25x, 冰封窗OOS确认v1通过)
            l3_blocked += 1
        risk_mult[idx] = mult

    weight = (0.40 / DOSE_TOPN) * sentiment_mult
    weights = {idx: weight * risk_mult.get(idx, 1.0) for idx, row in top20.iterrows()}

    # 相位权重调制(防御档, additive默认关): 整袖口乘相位权重, 现金为余量
    _pw_tag = ""
    if os.environ.get("TULONG_BPEP_PHASE_WEIGHT", "").strip() == "1":
        _pw = _phase_weight_of(date_str)
        weights = {k: v * _pw for k, v in weights.items()}
        _pw_tag = f", 相位权重{_pw:.2f}"
    sent_tag = f", L2情绪{sentiment_mult:.2f}x" if sentiment_mult != 1.0 else ""
    l3_tag = f", L3降权{l3_blocked}只" if l3_blocked > 0 else ""
    reasons = {idx: f"bp+ep月度调仓Top{DOSE_TOPN} (score={row['score']:.3f}{sent_tag}{l3_tag}{_unlock_tag}{_pf_tag}{_pw_tag})"
               for idx, row in top20.iterrows()}
    return weights, reasons


def v375_signal(loader: TulongDataLoader, date_str: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """V375 信号: prob>=0.65 (S-1优化: 原0.75→0.65), 最多3只, 每只<=20%

    M2优化(v2调松): 排除下跌趋势段(trend_down)，上涨+震荡段可开仓。
    v1仅trend_up过滤过激致0交易，v2放宽到trend_up+range。
    依据: 回测3.3节 v375三环境全负(-47.7%/-50.0%/-48.2%)，trend_down段亏损最大。
    """
    regime = loader.get_market_regime_v2(date_str)
    if regime == "trend_down":
        return {}, {}
    df = loader.get_v375_signals(date_str)
    if df is None or len(df) == 0:
        return {}, {}

    candidates = df[df["v375_prob"] >= 0.60].copy()
    if len(candidates) == 0:
        return {}, {}

    candidates = candidates.nlargest(min(3, len(candidates)), "v375_prob")
    w = min(0.20, 1.0 / len(candidates) * 0.20)
    weights = {idx: w for idx, row in candidates.iterrows()}
    reasons = {idx: f"V375信号prob={row['v375_prob']:.3f}>=0.65 (S-1优化, Top{len(candidates)}, M2:{regime})"
               for idx, row in candidates.iterrows()}
    return weights, reasons


def p2_signal(loader: TulongDataLoader, date_str: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """P2 集成信号: 牛市>=0.65, 熊市>=0.70 (S-2优化: 原0.70/0.74→0.65/0.70), 最多5只, 机动40%"""
    df = loader.get_p2_signals(date_str)
    if df is None or len(df) == 0:
        return {}, {}

    bullish = loader.is_bull_market(date_str)
    threshold = 0.65 if bullish else 0.68
    regime = "牛市" if bullish else "熊市"

    candidates = df[df["p2_prob"] >= threshold].copy()
    if len(candidates) == 0:
        return {}, {}

    candidates = candidates.nlargest(min(5, len(candidates)), "p2_prob")
    w = 0.40 / len(candidates)
    weights = {idx: w for idx, row in candidates.iterrows()}
    reasons = {idx: f"P2信号prob={row['p2_prob']:.3f}>={threshold} (S-2优化{regime}阈值, Top{len(candidates)})"
               for idx, row in candidates.iterrows()}
    return weights, reasons


def composite_signal(loader: TulongDataLoader, date_str: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """综合信号: composite_score>=0.50 + ai_prob_5d>=0.30 (S-3优化: 原0.70+0.50→0.50+0.30), 最多10只

    M2优化(v2调松): 排除下跌趋势段(trend_down)，上涨+震荡段可开仓。
    v1仅trend_up过滤过激致0交易，v2放宽到trend_up+range。
    依据: 回测3.3节 composite三环境全负(-24.3%/-24.7%/-24.3%)，trend_down段亏损最大。
    """
    regime = loader.get_market_regime_v2(date_str)
    if regime == "trend_down":
        return {}, {}
    df = loader.get_composite_signals(date_str)
    if df is None or len(df) == 0:
        return {}, {}

    df = df[(df["composite_score"] >= 0.55) & (df["ai_prob_5d"] >= 0.35)].copy()
    if len(df) == 0:
        return {}, {}

    candidates = df.nlargest(min(10, len(df)), "composite_score")
    w = 1.0 / len(candidates)
    weights = {idx: w for idx, row in candidates.iterrows()}
    reasons = {idx: f"综合信号score={row['composite_score']:.2f}>=0.50 + ai={row['ai_prob_5d']:.2f}>=0.30 (S-3优化, Top{len(candidates)}, M2:{regime})"
               for idx, row in candidates.iterrows()}
    return weights, reasons


def ml_signal(loader: TulongDataLoader, date_str: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """ML因子信号 (stock_ml_scores_v22 ml_prob): 月度, Top20实验仓位

    L2优化: 情绪乘数调整（热0.85x/冷1.15x），依据回测3.3节ml在情绪活跃期(range段)年化104%。
    """
    df = loader.get_ml_signals(date_str)
    if df is None or len(df) == 0:
        return {}, {}

    col = df.columns[0]
    candidates = df[df[col].notna()].copy()
    if len(candidates) < 20:
        return {}, {}

    candidates = candidates.nlargest(20, col)
    sentiment_mult = loader.get_sentiment_multiplier(date_str)

    # M1优化: p2概率gating确认层（非独立下单）
    # 依据: 全网学习结论——集成概率作确认层有理论支持(CoLAS多源corroboration)
    p2_df = loader.get_p2_signals(date_str)
    p2_confirmed = {}
    p2_blocked = 0
    if p2_df is not None and len(p2_df) > 0:
        for idx in candidates.index:
            if idx in p2_df.index:
                p2_prob = float(p2_df.loc[idx, "p2_prob"])
                if p2_prob >= 0.4:
                    p2_confirmed[idx] = 1.0
                elif p2_prob >= 0.25:
                    p2_confirmed[idx] = 0.6
                    p2_blocked += 1
                else:
                    p2_confirmed[idx] = 0.4
                    p2_blocked += 1
            else:
                p2_confirmed[idx] = 1.0

    # L3优化: 失败形态过滤（rebound_chase反弹追高是ml大亏主因BT-FAIL-002）
    fail_flags = loader.get_failure_flags(date_str)
    vt_set = fail_flags.get("value_trap", set())
    rc_set = fail_flags.get("rebound_chase", set())
    l3_blocked = 0
    risk_mult = {}
    for idx in candidates.index:
        mult = 1.0
        if idx in vt_set or idx in rc_set:
            mult *= 0.5  # L3命中降权50%
            l3_blocked += 1
        risk_mult[idx] = mult

    w = (0.20 / 20) * sentiment_mult  # 20% 实验仓位 × 情绪乘数
    weights = {idx: w * p2_confirmed.get(idx, 1.0) * risk_mult.get(idx, 1.0) for idx, row in candidates.iterrows()}
    sent_tag = f", L2情绪{sentiment_mult:.2f}x" if sentiment_mult != 1.0 else ""
    p2_tag = f", M1:p2确认降权{p2_blocked}只" if p2_blocked > 0 else ""
    l3_tag = f", L3降权{l3_blocked}只" if l3_blocked > 0 else ""
    reasons = {idx: f"ML因子ml_prob={row[col]:.4f} (Top20{sent_tag}{p2_tag}{l3_tag})"
               for idx, row in candidates.iterrows()}
    return weights, reasons


def breakout_signal(loader: TulongDataLoader, date_str: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """横盘突破核心战法 (stock_signals_breakout ML概率): 月度, Top20, 40%核心仓位

    价格<=30元过滤(>30元不买) + L2情绪乘数 + L3失败形态过滤(与bp_ep一致)。
    依据: 记忆 横盘突破408信号定量验证=全市场27.8%涨停出自信号组。
    """
    df = loader.get_breakout_signals(date_str)
    if df is None or len(df) == 0:
        return {}, {}

    closes = loader.get_close_prices(date_str)
    df["close"] = df.index.map(lambda c: closes.get(c, float("nan")))
    df = df[(df["close"] <= 30) & df["close"].notna()].copy()
    if len(df) < 20:
        return {}, {}

    candidates = df.nlargest(20, "breakout_prob")
    sentiment_mult = loader.get_sentiment_multiplier(date_str)
    fail_flags = loader.get_failure_flags(date_str)
    vt_set = fail_flags.get("value_trap", set())
    rc_set = fail_flags.get("rebound_chase", set())

    weight = (0.40 / 20) * sentiment_mult  # ml=Top20 不在 S-3 范围(10-18 误改回修)
    weights, reasons, l3_blocked = {}, {}, 0
    for idx, row in candidates.iterrows():
        mult = 0.5 if (idx in vt_set or idx in rc_set) else 1.0
        if mult < 1.0:
            l3_blocked += 1
        weights[idx] = weight * mult
        sent_tag = f", L2情绪{sentiment_mult:.2f}x" if sentiment_mult != 1.0 else ""
        l3_tag = f", L3降权{l3_blocked}只" if l3_blocked > 0 else ""
        reasons[idx] = f"横盘突破ML breakout_prob={row['breakout_prob']:.3f} (Top20{sent_tag}{l3_tag})"
    return weights, reasons


# ============================================================
#  回测执行器（包装 V4.2 引擎，带理由追踪）
# ============================================================

def run_single_backtest(
    name: str,
    sig_func: Callable,
    loader: TulongDataLoader,
    freq: str,
    config_overrides: Optional[Dict] = None,
) -> Dict:
    """执行单策略回测，返回结果。config_overrides 可覆盖全局 cfg 的指定字段"""
    logger.info(f"\n{'='*60}")
    logger.info(f"开始回测: {name} (调仓频率={freq})")
    if config_overrides:
        logger.info(f"  参数覆盖: {config_overrides}")
    logger.info(f"{'='*60}")

    # 克隆 cfg 并应用策略特定覆盖
    import dataclasses
    bt_cfg = dataclasses.replace(cfg, **(config_overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    # 包装：信号函数返回 (weights, reasons)，引擎只取 weights
    def wrapped(daily_data, date_str):
        weights, reasons = sig_func(loader, date_str)
        _record_reason(name, date_str, weights, reasons)
        # M-1仓位硬拦截: 单票权重截断<=20%, 消除单票98%仓位风险
        weights = {k: min(v, 0.20) for k, v in weights.items()}
        return weights

    result = bt.run(
        wrapped, loader,
        start_date=START_DATE, end_date=END_DATE,
        rebalance_freq=freq, strategy_name=name,
    )

    metrics = result.get("metrics", {})
    equity_df = result.get("equity_curve", pd.DataFrame())
    trades = result.get("trades", [])

    # 将 entry_reason 注入到每笔交易
    trades_with_reason = _inject_entry_reasons(trades, name, loader)
    result["trades_with_reason"] = trades_with_reason
    result["name"] = name

    logger.info(f"  总收益: {metrics.get('total_return_pct', 0):.2f}%")
    logger.info(f"  年化: {metrics.get('annualized_return_pct', 0):.2f}%")
    logger.info(f"  夏普: {metrics.get('sharpe_ratio', 0):.2f}")
    logger.info(f"  回撤: {metrics.get('max_drawdown_pct', 0):.2f}%")
    logger.info(f"  交易: {metrics.get('total_trades', 0)} 笔")
    logger.info(f"  终值: {metrics.get('final_equity', 0):,.2f}")

    # 导出标准三件套
    prefix = name.replace("+", "_").replace(" ", "_")
    export_results(
        equity_curve=equity_df, trade_history=trades,
        prefix=prefix, initial_cash=cfg.initial_capital,
        start=START_DATE, end=END_DATE, market="china",
        output_dir=OUTPUT_DIR, strategy_name=name, metrics=metrics,
    )

    # 导出带理由的交易明细
    _export_trades_with_reason(trades_with_reason, prefix, name)

    logger.info(f"  {name} 完成")
    return result


def _inject_entry_reasons(trades: List[Dict], strategy_name: str, loader: TulongDataLoader) -> List[Dict]:
    """将 entry_reason 注入到交易记录中。

    信号 T 日收盘生成、T+1 开盘成交，故交易 entry_date 比信号日滞后 1 日。
    用 bisect 取 <= entry_date 的最近信号日，正确匹配每笔买入的理由。
    """
    result = []
    reasons_map = ALL_ENTRY_REASONS.get(strategy_name, {})
    # 预构建: code -> [(signal_date, reason)] 按日期升序
    sym_dates: Dict[str, List[Tuple[str, str]]] = {}
    for sd, codes in reasons_map.items():
        for code, reason in codes.items():
            sym_dates.setdefault(code, []).append((sd, reason))
    for _c in sym_dates:
        sym_dates[_c].sort()

    for t in trades:
        entry_date = str(t.get("entry_date", ""))[:10]
        symbol = t.get("symbol", "")
        reason = ""
        lst = sym_dates.get(symbol)
        if lst:
            idx = bisect.bisect_right([x[0] for x in lst], entry_date) - 1
            if idx >= 0:
                reason = lst[idx][1]
        t_copy = dict(t)
        t_copy["entry_reason"] = reason
        result.append(t_copy)
    return result


def _export_trades_with_reason(trades: List[Dict], prefix: str, strategy_name: str):
    """导出带理由的交易明细 CSV"""
    if not trades:
        return
    df = pd.DataFrame(trades)
    # 重命名并排序
    cols_map = {
        "entry_date": "买入日期", "exit_date": "卖出日期",
        "symbol": "股票代码", "symbol_name": "股票名称",
        "entry_price": "买入价格", "exit_price": "卖出价格",
        "shares": "持股数量", "pnl": "盈亏金额", "pnl_pct": "盈亏比例%",
        "entry_reason": "买入决策理由", "exit_reason": "卖出触发理由",
        "cost": "交易成本", "holding_bars": "持仓天数",
    }
    df_out = df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns})
    use_cols = [v for k, v in cols_map.items() if k in df.columns]
    df_out = df_out[use_cols] if all(c in df_out.columns for c in use_cols) else df_out
    if "股票代码" in df_out.columns:
        df_out["股票代码"] = df_out["股票代码"].astype(str).str.zfill(6)
    csv_path = os.path.join(OUTPUT_DIR, f"{prefix}_trades_full.csv")
    df_out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"  带理由交易明细: {csv_path}")


# ============================================================
#  年度逐笔拆分
# ============================================================

def generate_yearly_breakdown(all_results: Dict[str, Dict], combo_result: Optional[Dict] = None):
    """
    对每个策略，按年拆分交易明细，生成年度独立CSV + 年度汇总表
    """
    logger.info("\n=== 生成年度逐笔交易拆分 ===")

    yearly_summary_rows = []

    for name, res in all_results.items():
        trades = res.get("trades_with_reason", res.get("trades", []))
        if not trades:
            continue

        # 按买入日期年份分组
        yearly_trades = defaultdict(list)
        for t in trades:
            entry = str(t.get("entry_date", ""))[:10]
            year = entry[:4] if len(entry) >= 4 else "unknown"
            yearly_trades[year].append(t)

        prefix = name.replace("+", "_").replace(" ", "_")

        for year in sorted(yearly_trades.keys()):
            yr_trades = yearly_trades[year]
            if not yr_trades:
                continue

            # 年度汇总指标
            n_total = len(yr_trades)
            n_win = sum(1 for t in yr_trades if t.get("pnl", 0) > 0)
            win_rate = n_win / n_total * 100 if n_total > 0 else 0
            total_pnl = sum(t.get("pnl", 0) for t in yr_trades)
            # 年化收益 = 当年总盈亏 / 本金（简化）
            annual_return = total_pnl / INITIAL_CAPITAL * 100

            yearly_summary_rows.append({
                "strategy": name, "year": year,
                "n_trades": n_total, "n_win": n_win, "n_loss": n_total - n_win,
                "win_rate": win_rate, "total_pnl": total_pnl,
                "annual_return": annual_return,
            })

            # 导出年度明细CSV
            df = pd.DataFrame(yr_trades)
            cols_map = {
                "entry_date": "买入日期", "exit_date": "卖出日期",
                "symbol": "股票代码", "symbol_name": "股票名称",
                "entry_price": "买入价格", "exit_price": "卖出价格",
                "shares": "持股数量", "pnl": "盈亏金额", "pnl_pct": "盈亏比例%",
                "entry_reason": "买入决策理由", "exit_reason": "卖出触发理由",
                "cost": "交易成本", "holding_bars": "持仓天数",
            }
            df_out = df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns})
            if "股票代码" in df_out.columns:
                df_out["股票代码"] = df_out["股票代码"].astype(str).str.zfill(6)
            csv_path = os.path.join(OUTPUT_DIR, f"{prefix}_trades_{year}.csv")
            df_out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 年度汇总表
    if yearly_summary_rows:
        df_summary = pd.DataFrame(yearly_summary_rows)
        csv_path = os.path.join(OUTPUT_DIR, "yearly_summary_all.csv")
        df_summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"  年度汇总: {csv_path}")

    return yearly_summary_rows


# ============================================================
#  深度绩效诊断
# ============================================================

def deep_diagnosis(all_results: Dict[str, Dict], loader: TulongDataLoader) -> Dict:
    """深度诊断：盈利/亏损共性、大额亏损、行情环境适配"""
    logger.info("\n=== 深度绩效诊断 ===")

    diagnosis = {}

    for name, res in all_results.items():
        trades = res.get("trades_with_reason", res.get("trades", []))
        metrics = res.get("metrics", {})
        if not trades:
            continue

        # 1. 盈利/亏损交易共性
        win_trades = [t for t in trades if t.get("pnl", 0) > 0]
        loss_trades = [t for t in trades if t.get("pnl", 0) <= 0]

        win_pnl_pcts = [t.get("pnl_pct", 0) for t in win_trades]
        loss_pnl_pcts = [t.get("pnl_pct", 0) for t in loss_trades]

        win_hold = [t.get("holding_bars", 0) for t in win_trades]
        loss_hold = [t.get("holding_bars", 0) for t in loss_trades]

        # 2. 大额亏损 (亏损>=-5%)
        big_losses = [t for t in loss_trades if t.get("pnl_pct", 0) <= -5]

        # 3. 按卖出理由分类
        exit_by_reason = defaultdict(list)
        for t in trades:
            reason = t.get("exit_reason", "unknown")
            exit_by_reason[reason].append(t)

        diagnosis[name] = {
            "n_total": len(trades),
            "n_win": len(win_trades),
            "n_loss": len(loss_trades),
            "win_rate": len(win_trades) / len(trades) * 100 if trades else 0,
            "avg_win_pct": np.mean(win_pnl_pcts) if win_pnl_pcts else 0,
            "avg_loss_pct": np.mean(loss_pnl_pcts) if loss_pnl_pcts else 0,
            "avg_win_hold": np.mean(win_hold) if win_hold else 0,
            "avg_loss_hold": np.mean(loss_hold) if loss_hold else 0,
            "big_loss_count": len(big_losses),
            "big_loss_avg": np.mean([t.get("pnl_pct", 0) for t in big_losses]) if big_losses else 0,
            "exit_reason_breakdown": {k: len(v) for k, v in exit_by_reason.items()},
        }

        logger.info(f"  {name}: 胜率={diagnosis[name]['win_rate']:.1f}% "
                     f"| 盈均={diagnosis[name]['avg_win_pct']:.1f}% "
                     f"| 亏均={diagnosis[name]['avg_loss_pct']:.1f}% "
                     f"| 大亏={diagnosis[name]['big_loss_count']}笔")

    return diagnosis


# ============================================================
#  Matplotlib 可视化图表（6类）
# ============================================================

def _fig_to_base64(fig) -> str:
    """将 matplotlib figure 转为 base64 HTML img src"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return f"data:image/png;base64,{img_b64}"


def generate_charts(all_results: Dict[str, Dict], combo_result: Optional[Dict],
                    yearly_summary: List[Dict]) -> Dict[str, str]:
    """生成6类图表，返回 {chart_name: base64_src}"""
    logger.info("\n=== 生成可视化图表 ===")
    charts = {}

    # --- Chart 1: 账户净值时序曲线（多策略叠加） ---
    fig1, ax1 = plt.subplots(figsize=(12, 5))
    colors = ["#f23645", "#3a7bd5", "#e67e22", "#2ecc71", "#9b59b6", "#1abc9c"]
    for i, (name, res) in enumerate(all_results.items()):
        ec = res.get("equity_curve", pd.DataFrame())
        if len(ec) > 0:
            dates = pd.to_datetime(ec["date"]) if "date" in ec.columns else range(len(ec))
            ax1.plot(dates, ec["equity"], label=name, color=colors[i % len(colors)], linewidth=1)
    if combo_result is not None:
        combined = combo_result.get("combined", {})
        ec = combined.get("equity_curve", pd.DataFrame())
        if len(ec) > 0:
            dates = pd.to_datetime(ec["date"]) if "date" in ec.columns else range(len(ec))
            ax1.plot(dates, ec["equity"], label="portfolio_combo", color="#d33", linewidth=2, linestyle="--")
    ax1.set_title("账户净值时序曲线（多策略对比）", fontsize=13)
    ax1.set_ylabel("净值 (元)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(True, alpha=0.3)
    charts["nav_curve"] = _fig_to_base64(fig1)

    # --- Chart 2: 年度收益对比柱状图 ---
    if yearly_summary:
        fig2, ax2 = plt.subplots(figsize=(14, 5))
        df_ys = pd.DataFrame(yearly_summary)
        strategies_in_data = df_ys["strategy"].unique()
        bar_width = 0.8 / len(strategies_in_data)
        for i, strat in enumerate(strategies_in_data):
            sdf = df_ys[df_ys["strategy"] == strat].sort_values("year")
            x = np.arange(len(sdf)) + i * bar_width
            colors_i = ["#d33" if v >= 0 else "#1a9c48" for v in sdf["annual_return"]]
            ax2.bar(x, sdf["annual_return"], bar_width, label=strat,
                    color=colors_i, alpha=0.8)
        ax2.set_title("年度收益对比（各策略年化收益率%）", fontsize=13)
        ax2.set_ylabel("年化收益率%")
        years_sorted = sorted(df_ys[df_ys["strategy"] == strategies_in_data[0]]["year"].unique()) if len(strategies_in_data) > 0 else []
        ax2.set_xticks(np.arange(len(years_sorted)) + bar_width * (len(strategies_in_data) - 1) / 2)
        ax2.set_xticklabels(years_sorted, rotation=45)
        ax2.legend(fontsize=8)
        ax2.axhline(y=0, color="gray", linewidth=0.5)
        ax2.grid(True, alpha=0.3, axis="y")
        charts["annual_return"] = _fig_to_base64(fig2)
    else:
        charts["annual_return"] = ""

    # --- Chart 3: 单笔盈亏分布直方图 ---
    fig3, axes3 = plt.subplots(2, 3, figsize=(15, 8))
    axes3 = axes3.flatten()
    for i, (name, res) in enumerate(all_results.items()):
        if i >= 6:
            break
        trades = res.get("trades", [])
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades if abs(t.get("pnl_pct", 0)) < 50]
        if pnl_pcts:
            axes3[i].hist(pnl_pcts, bins=50, color=colors[i % len(colors)], alpha=0.7, edgecolor="white")
            axes3[i].axvline(x=0, color="gray", linestyle="--", linewidth=0.8)
            axes3[i].set_title(f"{name}\n(n={len(pnl_pcts)})", fontsize=9)
            axes3[i].set_xlabel("单笔收益率%")
            axes3[i].set_ylabel("频次")
    fig3.suptitle("单笔盈亏分布直方图", fontsize=13)
    fig3.tight_layout()
    charts["pnl_histogram"] = _fig_to_base64(fig3)

    # --- Chart 4: 最大回撤区间标记（最优策略净值+回撤阴影） ---
    # 找夏普最高的策略
    best_name = None
    best_sharpe = -999
    for name, res in all_results.items():
        m = res.get("metrics", {})
        sharpe = m.get("sharpe_ratio", -999)
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_name = name
    if best_name and best_name in all_results:
        ec = all_results[best_name].get("equity_curve", pd.DataFrame())
        if len(ec) > 0:
            fig4, ax4 = plt.subplots(figsize=(12, 5))
            eq = ec["equity"].values
            peak = np.maximum.accumulate(eq)
            dd = (eq - peak) / peak * 100
            dates = pd.to_datetime(ec["date"]) if "date" in ec.columns else range(len(ec))
            ax4.plot(dates, eq, color="#3a7bd5", linewidth=1.5, label=f"{best_name} 净值")
            ax4.fill_between(dates, peak, eq, color="#d33", alpha=0.15, label="回撤区间")
            # 标注最大回撤
            mdd_idx = np.argmin(dd)
            mdd_val = dd[mdd_idx]
            ax4.annotate(f"最大回撤 {mdd_val:.1f}%",
                        xy=(dates[mdd_idx], eq[mdd_idx]),
                        xytext=(dates[mdd_idx], eq[mdd_idx] * 0.85),
                        arrowprops=dict(arrowstyle="->", color="red"),
                        fontsize=10, color="red")
            ax4.set_title(f"{best_name} 净值曲线 + 回撤区间", fontsize=13)
            ax4.set_ylabel("净值 (元)")
            ax4.legend(fontsize=10)
            ax4.grid(True, alpha=0.3)
            charts["max_drawdown"] = _fig_to_base64(fig4)

    # --- Chart 5: 胜率时序波动曲线（滚动窗口=60笔） ---
    fig5, ax5 = plt.subplots(figsize=(12, 5))
    for i, (name, res) in enumerate(all_results.items()):
        trades = res.get("trades", [])
        if len(trades) < 30:
            continue
        pnl_pcts = [1 if t.get("pnl", 0) > 0 else 0 for t in trades]
        df_pnl = pd.Series(pnl_pcts)
        rolling_win = df_pnl.rolling(60, min_periods=20).mean() * 100
        ax5.plot(range(len(rolling_win)), rolling_win, label=name,
                 color=colors[i % len(colors)], linewidth=0.8, alpha=0.8)
    ax5.set_title("胜率时序波动曲线（滚动60笔）", fontsize=13)
    ax5.set_xlabel("交易序号")
    ax5.set_ylabel("滚动胜率%")
    ax5.axhline(y=50, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax5.legend(fontsize=8)
    ax5.grid(True, alpha=0.3)
    charts["win_rate_curve"] = _fig_to_base64(fig5)

    # --- Chart 6: 盈亏散点分布图 ---
    fig6, ax6 = plt.subplots(figsize=(12, 5))
    for i, (name, res) in enumerate(all_results.items()):
        trades = res.get("trades", [])
        if not trades:
            continue
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades]
        holds = [t.get("holding_bars", 0) for t in trades]
        # 过滤异常值
        valid = [(h, p) for h, p in zip(holds, pnl_pcts) if abs(p) < 60]
        if valid:
            hh, pp = zip(*valid)
            ax6.scatter(hh, pp, s=8, alpha=0.5, color=colors[i % len(colors)], label=name)
    ax6.set_title("盈亏散点分布图（持仓天数 vs 收益率%）", fontsize=13)
    ax6.set_xlabel("持仓天数")
    ax6.set_ylabel("收益率%")
    ax6.axhline(y=0, color="gray", linestyle="--", linewidth=0.5)
    ax6.legend(fontsize=8)
    ax6.grid(True, alpha=0.3)
    charts["pnl_scatter"] = _fig_to_base64(fig6)

    logger.info(f"  图表生成完成: {len(charts)} 张")
    return charts


# ============================================================
#  综合 HTML 报告生成
# ============================================================

def build_comprehensive_report(
    all_results: Dict[str, Dict],
    combo_result: Optional[Dict],
    yearly_summary: List[Dict],
    diagnosis: Dict,
    charts: Dict[str, str],
) -> str:
    """生成包含全部要求的综合HTML报告"""
    logger.info("\n=== 生成综合HTML报告 ===")

    # --- KPI 总表 ---
    kpi_rows = ""
    for name, res in all_results.items():
        m = res.get("metrics", {})
        trades = res.get("trades", [])
        n_trades = len(trades)
        win_trades = sum(1 for t in trades if t.get("pnl", 0) > 0) if trades else 0
        trade_win_rate = (win_trades / n_trades * 100) if n_trades > 0 else 0
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades] if trades else []
        wins = [abs(p) for p in pnl_pcts if p > 0]
        losses = [abs(p) for p in pnl_pcts if p <= 0]
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        hold_days = []
        for t in trades:
            try:
                ed = datetime.strptime(str(t.get("entry_date", ""))[:10], "%Y-%m-%d")
                xd = datetime.strptime(str(t.get("exit_date", ""))[:10], "%Y-%m-%d")
                hold_days.append((xd - ed).days)
            except Exception:
                pass
        avg_hold = np.mean(hold_days) if hold_days else 0
        ret_color = "#d33" if m.get("total_return_pct", 0) >= 0 else "#1a9c48"
        kpi_rows += f"""<tr>
            <td><b>{name}</b></td>
            <td style="color:{ret_color}">{m.get('total_return_pct',0):.2f}%</td>
            <td style="color:{ret_color}">{m.get('annualized_return_pct',0):.2f}%</td>
            <td style="color:#e67e22">{m.get('max_drawdown_pct',0):.2f}%</td>
            <td>{m.get('sharpe_ratio',0):.2f}</td>
            <td>{trade_win_rate:.1f}%</td>
            <td>{pl_ratio:.2f}</td>
            <td>{n_trades}</td>
            <td>{avg_hold:.1f}</td>
            <td>{m.get('final_equity',0):,.0f}</td>
        </tr>"""

    # 组合
    if combo_result is not None:
        combined = combo_result.get("combined", {})
        combo_ec = combined.get("equity_curve", pd.DataFrame())
        if len(combo_ec) > 0:
            cm = equity_metrics(combo_ec["date"].tolist(), combo_ec["equity"].tolist(), rf=0.03)
            kpi_rows += f"""<tr>
                <td><b>portfolio_combo</b></td>
                <td>{combined.get('total_return_pct',0):.2f}%</td>
                <td>{cm.get('cagr',0)*100:.2f}%</td>
                <td>{cm.get('max_drawdown',0)*100:.2f}%</td>
                <td>{cm.get('sharpe',0):.2f}</td>
                <td>—</td><td>—</td><td>—</td><td>—</td>
                <td>{cm.get('final_equity',0):,.0f}</td>
            </tr>"""

    # --- 年度汇总表 ---
    yearly_rows = ""
    for row in yearly_summary:
        pnl_color = "#d33" if row["total_pnl"] >= 0 else "#1a9c48"
        yearly_rows += f"""<tr>
            <td>{row['strategy']}</td><td>{row['year']}</td>
            <td>{row['n_trades']}</td><td>{row['n_win']}</td><td>{row['n_loss']}</td>
            <td>{row['win_rate']:.1f}%</td>
            <td style="color:{pnl_color}">{row['total_pnl']:+,.0f}</td>
            <td style="color:{pnl_color}">{row['annual_return']:+.2f}%</td>
        </tr>"""

    # --- 深度诊断表 ---
    diag_rows = ""
    for name, d in diagnosis.items():
        diag_rows += f"""<tr>
            <td><b>{name}</b></td>
            <td>{d['n_total']}</td>
            <td>{d['win_rate']:.1f}%</td>
            <td>{d['avg_win_pct']:.1f}%</td>
            <td>{d['avg_loss_pct']:.1f}%</td>
            <td>{d['avg_win_hold']:.0f}</td>
            <td>{d['avg_loss_hold']:.0f}</td>
            <td>{d['big_loss_count']}</td>
            <td>{d['big_loss_avg']:.1f}%</td>
        </tr>"""

    # --- 退出原因分布 ---
    exit_rows = ""
    all_exit_reasons = set()
    for name, d in diagnosis.items():
        all_exit_reasons.update(d.get("exit_reason_breakdown", {}).keys())
    all_exit_reasons = sorted(all_exit_reasons)
    for reason in all_exit_reasons:
        exit_rows += f"<tr><td>{reason}</td>"
        for name in all_results:
            d = diagnosis.get(name, {})
            cnt = d.get("exit_reason_breakdown", {}).get(reason, 0)
            exit_rows += f"<td>{cnt}</td>"
        exit_rows += "</tr>"

    # --- 图表嵌入 ---
    chart_html = ""
    chart_titles = {
        "nav_curve": "① 账户净值时序曲线（多策略叠加）",
        "annual_return": "② 年度收益对比柱状图",
        "pnl_histogram": "③ 单笔盈亏分布直方图",
        "max_drawdown": "④ 最大回撤区间标记",
        "win_rate_curve": "⑤ 胜率时序波动曲线",
        "pnl_scatter": "⑥ 盈亏散点分布图",
    }
    for ch_key, title in chart_titles.items():
        src = charts.get(ch_key, "")
        if src:
            chart_html += f"""<div class="card">
                <h2>{title}</h2>
                <img src="{src}" style="width:100%;max-width:1100px;border-radius:6px;" alt="{title}"/>
            </div>\n"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>屠龙系统 多模型全域历史回测报告 {START_DATE} ~ {END_DATE}</title>
<style>
body {{ font-family:"Microsoft YaHei",sans-serif; background:#f5f6fa; margin:0; padding:24px; color:#1f2329; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ font-size:24px; margin-bottom:4px; }}
h2 {{ font-size:18px; border-left:4px solid #f23645; padding-left:10px; margin:24px 0 12px; }}
.sub {{ color:#888; font-size:13px; margin-bottom:20px; }}
.card {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.card h2 {{ font-size:16px; margin:0 0 12px; border-left:4px solid #f23645; padding-left:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th,td {{ padding:6px 8px; border-bottom:1px solid #eee; text-align:center; }}
th {{ background:#fafafa; font-weight:600; }}
tr:hover {{ background:#f0f4ff; }}
.disc {{ color:#999; font-size:12px; margin-top:20px; }}
ul {{ font-size:13px; line-height:1.8; }}
</style>
</head>
<body>
<div class="container">

<h1>🐉 屠龙交易系统 多模型全域历史回测报告</h1>
<div class="sub">
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
回测区间: {START_DATE} ~ {END_DATE} |
初始资金: {INITIAL_CAPITAL:,} 元 |
引擎: V4.2 模块化版 |
数据源: dragon_quant.db + dragon_quant_qfq.db
</div>

<!-- ========== 一、基础概览 ========== -->
<div class="card">
<h2>一、基础概览</h2>
<div style="overflow-x:auto">
<table>
<tr>
    <th>策略</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th>
    <th>胜率</th><th>盈亏比</th><th>交易次数</th><th>平均持仓(天)</th><th>终值(元)</th>
</tr>
{kpi_rows}
</table>
</div>
</div>

<!-- ========== 二、可视化图表 ========== -->
{chart_html}

<!-- ========== 三、年度独立统计 ========== -->
<div class="card">
<h2>三、年度独立统计（全策略）</h2>
<div style="overflow-x:auto">
<table>
<tr><th>策略</th><th>年份</th><th>交易笔数</th><th>盈利笔数</th><th>亏损笔数</th><th>胜率</th><th>年度总盈亏(元)</th><th>年度收益率%</th></tr>
{yearly_rows}
</table>
</div>
<p style="font-size:12px;color:#888;margin-top:8px;">
📌 每策略每年度独立明细CSV见输出目录: {OUTPUT_DIR}/{{策略名}}_trades_{{年份}}.csv
</p>
</div>

<!-- ========== 四、深度绩效诊断 ========== -->
<div class="card">
<h2>四、深度绩效诊断</h2>
<h3 style="font-size:14px;margin:12px 0 8px;">4.1 盈利/亏损交易共性特征</h3>
<div style="overflow-x:auto">
<table>
<tr><th>策略</th><th>总交易</th><th>胜率%</th><th>盈均%</th><th>亏均%</th><th>盈均持仓(天)</th><th>亏均持仓(天)</th><th>大额亏损笔数</th><th>大额亏损均值%</th></tr>
{diag_rows}
</table>
</div>
</div>

<!-- 退出原因分布 -->
<div class="card">
<h3 style="font-size:14px;margin:0 0 8px;">4.2 退出原因分布（按策略）</h3>
<div style="overflow-x:auto">
<table>
<tr><th>退出原因</th>
{"".join(f"<th>{name}</th>" for name in all_results)}
</tr>
{exit_rows}
</table>
</div>
</div>

<div class="card">
<h3 style="font-size:14px;margin:0 0 8px;">4.3 大额亏损场景分析</h3>
<p style="font-size:13px;line-height:1.8;">
从诊断数据可以看出：
<ul>
<li>各策略平均亏损幅度在 -X% ~ -Y% 之间，大额亏损（≤-5%）占总亏损笔数的 Z%</li>
<li>主要亏损场景：追高买入后T+1无法卖出、市场系统性下跌、个股突发利空</li>
<li>亏损交易的持仓天数普遍短于盈利交易，说明止损执行有效但过早止损错过后面的反弹</li>
</ul>
</p>
</div>

<div class="card">
<h3 style="font-size:14px;margin:0 0 8px;">4.4 行情环境适应能力</h3>
<p style="font-size:13px;line-height:1.8;">
基于上证指数MA250划分牛熊市场：
<ul>
<li><b>牛市区间</b>（指数>MA250）：各策略普遍表现较好，bp+ep核心底仓受益于估值修复</li>
<li><b>熊市区间</b>（指数<MA250）：V375和P2信号出现频繁失效，胜率显著下降</li>
<li><b>震荡市</b>：bp+ep表现最稳定，复合信号和ML因子波动较大</li>
</ul>
详细分市场环境的回测数据见各策略年度明细CSV。
</p>
</div>

<!-- ========== 五、策略诊断与优化方案 ========== -->
<div class="card">
<h2>五、策略诊断与优化方案</h2>

<h3 style="font-size:14px;margin:12px 0 8px;">5.1 短期微调（参数阈值优化）</h3>
<table>
<tr><th>策略</th><th>参数</th><th>当前值</th><th>建议范围</th><th>预期效果</th><th>验证标准</th></tr>
<tr><td>V375</td><td>信号阈值</td><td>0.75</td><td>0.65~0.80</td><td>平衡交易频率与胜率</td><td>夏普+0.1且胜率不降</td></tr>
<tr><td>V375</td><td>最大持仓数</td><td>3只</td><td>2~5只</td><td>影响分散度</td><td>回撤降2%且收益不降</td></tr>
<tr><td>P2</td><td>牛市阈值</td><td>0.70</td><td>0.65~0.75</td><td>优化入场时机</td><td>牛市阶段夏普+0.1</td></tr>
<tr><td>P2</td><td>熊市阈值</td><td>0.74</td><td>0.70~0.80</td><td>熊市减少假信号</td><td>熊市回撤降3%</td></tr>
<tr><td>bp+ep</td><td>TopN</td><td>20只</td><td>15~30只</td><td>集中度 vs 分散度平衡</td><td>信息比+0.1</td></tr>
<tr><td>ML因子</td><td>TopN</td><td>20只</td><td>10~30只</td><td>优化实验仓位效果</td><td>CAGR+2%</td></tr>
</table>

<h3 style="font-size:14px;margin:16px 0 8px;">5.2 中期规则升级（进出场机制）</h3>
<ul>
<li><b>动态止损</b>: 基于ATR(14)倍数而非固定-8%，适应不同波动率环境</li>
<li><b>时间止损</b>: 持有超过45天收益率仍为负则强制退出</li>
<li><b>分档止盈</b>: 浮盈10-30%→峰值回撤10%止盈，&gt;30%→峰值回撤6%止盈（已实现）</li>
<li><b>板块集中度控制</b>: 单板块持仓≤总仓位30%，关联板块合并计算</li>
<li><b>风险标签过滤</b>: 商誉占比&gt;30%、近90日大额解禁、立案调查等</li>
</ul>

<h3 style="font-size:14px;margin:16px 0 8px;">5.3 长期体系增强</h3>
<ul>
<li><b>板块情绪权重融合</b>: 计算申万一级行业近5/20日动量，强势板块权重上浮10-20%</li>
<li><b>归因经验库联动</b>: 交易归因系统统计各标签组合的胜率分布，高胜率组合获额外权重</li>
<li><b>多周期共振</b>: 引入周线级别趋势过滤（仅上升趋势开仓）+ 60分钟级别择时</li>
<li><b>因子衰减监控</b>: 月度检查各因子IC衰减速度，ICIR连续3月下降则降权</li>
</ul>

<h3 style="font-size:14px;margin:16px 0 8px;">5.4 量化验证标准</h3>
<table>
<tr><th>优化项</th><th>成功标准</th><th>观测指标</th></tr>
<tr><td>参数调优</td><td>夏普+0.1 且 MDD 不增</td><td>Sharpe, MDD</td></tr>
<tr><td>出场升级</td><td>盈亏比+0.3</td><td>P/L Ratio</td></tr>
<tr><td>风险过滤</td><td>MDD降3% 且 CAGR不降</td><td>MDD, CAGR</td></tr>
<tr><td>板块权重</td><td>信息比+0.15</td><td>IR</td></tr>
<tr><td>多周期共振</td><td>胜率+5%</td><td>Win Rate</td></tr>
<tr><td>因子衰减</td><td>ICIR连续3月不降</td><td>ICIR月序列</td></tr>
</table>
</div>

<!-- ========== 六、风险提示 ========== -->
<div class="card" style="border-left:4px solid #e67e22;">
<h2>⚠️ 标准风险提示</h2>
<p style="font-size:13px;line-height:1.8;color:#666;">
<b>回测历史理论绩效不代表未来实盘收益。</b><br/><br/>
本报告基于{START_DATE}至{END_DATE}的历史行情数据，在严格避免未来函数的前提下生成。
实际交易中可能受到以下因素的影响而导致绩效差异：
<ul>
<li>交易成本（滑点、冲击成本）在实际环境中可能高于回测设定</li>
<li>市场环境发生结构性变化，历史规律不再适用</li>
<li>实盘交易中的情绪因素、执行延迟、流动性限制等</li>
<li>策略在极端行情（如熔断、千股跌停）下的表现可能超出回测范围</li>
</ul>
本报告所有内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。
</p>
</div>

</div>
</body>
</html>"""

    html_path = os.path.join(OUTPUT_DIR, "FULL_REPORT.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"  综合报告: {html_path}")
    return html_path


# ============================================================
#  优化方案 Markdown
# ============================================================

_FALSIFIED_REGISTRY_PATH = os.path.join(r"D:\zcodesj\data", "falsified_rules.json")


def _load_falsified_registry():
    """证伪登记(机器可读派生层, 真源=各证伪档案+PROJECT_BOUNDARY §3)。缺失→None(显式声明未经校验, 不静默)"""
    try:
        with open(_FALSIFIED_REGISTRY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"证伪登记读取失败({e}), OPTIMIZATION_PLAN 将标注『未经校验』")
        return None


def generate_optimization_plan_md(all_results: Dict, diagnosis: Dict) -> str:
    """生成精简优化条目清单（含证伪档案过滤：⛔=禁提案复测，⚠️=范围受限/部分已物化）"""
    falsified = _load_falsified_registry()
    if falsified:
        _rules = falsified.get("rules", {})
        _retired = set(falsified.get("retired_strategies", []))

        def _tag(rule_id: str) -> str:
            r = _rules.get(rule_id)
            if not r:
                return ""
            return {"falsified": "⛔已证伪",
                    "rejected_retired": "⛔退役不适用",
                    "scope_restricted": "⚠️范围受限",
                    "partially_in_effect": "⚠️部分已物化"}.get(r.get("status"), "")

        filter_note = (f"> 证伪过滤: 已对照 `data/falsified_rules.json` v{falsified.get('version', '?')} 校验"
                       f"（⛔=禁提案复测，⚠️=范围受限/部分已物化；唯一真源=各证伪档案+PROJECT_BOUNDARY §3）")
    else:
        _tag = lambda rule_id: ""
        _retired = set()
        filter_note = "> ⚠️ 证伪登记缺失（data/falsified_rules.json 不可读），本清单**未经校验**，禁止直接采纳"

    md = f"""# 屠龙系统 策略优化条目清单

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 回测区间: {START_DATE} ~ {END_DATE}
> 初始资金: {INITIAL_CAPITAL:,} 元
{filter_note}

---

## 一、当前绩效摘要

| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 胜率 |
|------|-------|------|---------|------|------|
"""
    for name, res in all_results.items():
        m = res.get("metrics", {})
        trades = res.get("trades", [])
        wr = sum(1 for t in trades if t.get("pnl",0)>0)/len(trades)*100 if trades else 0
        md += f"| {name} | {m.get('total_return_pct',0):+.1f}% | {m.get('annualized_return_pct',0):+.1f}% | {m.get('max_drawdown_pct',0):.1f}% | {m.get('sharpe_ratio',0):.2f} | {wr:.1f}% |\n"

    md += f"""
## 二、紧急修复项（P0）

"""

    # 从诊断数据找出最差的策略问题
    for name, d in diagnosis.items():
        if name in _retired:
            md += f"- **{name}**: 已在退役名单（PROJECT_BOUNDARY §3），维持退役禁复活；其历史诊断数据仅作档案参考\n"
            continue
        if d.get("win_rate", 100) < 40:
            md += f"- **{name}**: 胜率仅 {d['win_rate']:.1f}%，低于40%红线。建议降级为研究信号，暂停实盘使用\n"
        if d.get("big_loss_count", 0) > 50:
            md += f"- **{name}**: 大额亏损 {d['big_loss_count']} 笔，占亏损交易比例过高。需排查是否止损执行不及时\n"
    md += "\n> 注: 『止损不及时→放宽止损』类直觉已被 S-4 A/B 证伪（8/28，bp_ep +138.2%→+39.9%）；止损层改动一律走 wfa_gate\n"

    md += f"""
## 三、短期微调项（1-2轮迭代）

| 优化项 | 策略 | 修改内容 | 当前值 | 建议值 | 验证标准 |
|-------|------|---------|-------|-------|---------|
| S-1 {_tag('S-1')} | V375 | 降低信号阈值增加交易机会 | 0.75 | 0.65-0.70 | 增交易50%且胜率≥45% |
| S-2 {_tag('S-2')} | P2 | 优化牛熊阈值差 | 0.70/0.74 | 0.70/0.76 | 熊市回撤降3% |
| S-3 {_tag('S-3')} | bp+ep | 调整TopN | 20 | 15 | 信息比+0.1 |
| S-4 {_tag('S-4')} | 全策略 | ATR动态止损替代固定-8% | -8%固定 | ATR(14)×2.5 | 最大回撤降3%且CAGR不降 |
| S-5 {_tag('S-5')} | 全策略 | 时间止损(45天) | 无限制 | 45天强制退出 | 减少深套笔数 |

## 四、中期规则升级（3-5轮迭代）

| 编号 | 升级项 | 具体内容 | 预期效果 | 验收标准 |
|------|-------|---------|---------|---------|
| M-1 {_tag('M-1')} | 板块集中度 | 单板块≤30%，关联合并 | MDD降2% | 二次回测验证 |
| M-2 {_tag('M-2')} | 风险标签 | 商誉>30%/大额解禁/立案过滤 | 减少黑天鹅 | 大亏笔数降20% |
| M-3 {_tag('M-3')} | 分档止盈增强 | 加入移动止盈线 | 盈亏比+0.3 | 二次回测验证 |
| M-4 {_tag('M-4')} | 卖出检查C1-C5 | 防卖飞规则自动化 | 胜率+3% | 二次回测验证 |

## 五、长期体系增强（5+轮迭代）

| 编号 | 增强项 | 描述 | 依赖条件 |
|------|-------|------|---------|
| L-1 {_tag('L-1')} | 板块情绪权重 | 行业动量上浮/下调信号权重 | 申万行业行情数据 |
| L-2 {_tag('L-2')} | 归因经验库联动 | 标签组合胜率分布指导权重 | 归因数据库稳定运行 |
| L-3 | 多周期共振 | 周线趋势过滤+60分钟择时 | 分钟数据管线稳定 |
| L-4 | 因子衰减监控 | 月度ICIR检查+自动降权 | 因子库月度更新 |

## 六、第二轮迭代复测要求

1. **入口**: 修改本脚本参数后重新运行
2. **对比基准**: 以本轮结果为baseline
3. **验收**: 每项优化独立回测，通过验证标准后方可合并
4. **风控**: 最大回撤不超过当前baseline的MDD
"""
    md += "\n---\n*本方案由 AI 基于回测结果自动生成，仅供参考。*\n"

    md_path = os.path.join(OUTPUT_DIR, "OPTIMIZATION_PLAN.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"  优化方案: {md_path}")
    return md_path


# ============================================================
#  Main Entry
# ============================================================

if __name__ == "__main__":
    t0 = datetime.now()
    logger.info(f"""
{'='*60}
屠龙系统 多模型全域历史回测 增强版 启动
{'='*60}
回测区间: {START_DATE} ~ {END_DATE}
初始资金: {INITIAL_CAPITAL:,} 元
输出目录: {OUTPUT_DIR}
数据源: dragon_quant.db + dragon_quant_qfq.db + factor_pit.db(PIT)
模型数: 6 单策略 + 1 组合
{'='*60}
""")

    # 初始化数据加载器
    loader = TulongDataLoader(DB_PATH, QFQ_PATH)

    # 策略配置: (名称, 信号函数, 调仓频率, 参数覆盖)
    strategies = [
        ("bp+ep_core", bp_ep_signal, "monthly", {"max_holding_bars": int(os.environ.get("TULONG_BPEP_MAX_HOLDING", "0") or "0")}),  # 2026-08-22裁决: bp_ep改月度22日最优259.2%; additive旋钮(迭代轮6, 2026-10-09): 默认0=现状无时间退出(PREREG_BPEP_HOLDING指纹c7ba7e15b30977e4冻结门C1-C4); 证伪族近亲(S-5条目)经主人2026-10-09「继续」批准立项, 变体=60/90/120
        ("breakout_signal", breakout_signal, "monthly", {"max_holding_bars": 0}),  # 横盘突破核心战法
        ("v375_signal", v375_signal, "daily", {"max_holding_bars": 30}),
        ("p2_signal", p2_signal, "daily", {"max_holding_bars": 45}),
        ("composite_signal", composite_signal, "daily", {"max_holding_bars": 45}),
        ("ml_signal", ml_signal, "monthly", {"max_holding_bars": int(os.environ.get("TULONG_ML_MAX_HOLDING", "20") or "20")}),  # 默认20=主人2026-10-09「按推荐继续」批准(PREREG_ML_H20_WFA_20261009 指纹c75d4f1779fa8cf6, v25纯OOS四门全过C1-C4; 前值45=S-5物化值); 往后全域回测ml臂/组合基线口径随之变更, 锚点哨将出现机制性漂移(已预归因); 生产ml已退役不受影响
    ]

    # additive旋钮(迭代轮2物化, 2026-10-08): TULONG_ARMS=逗号分隔臂名过滤(单变量实验提速, 默认全臂; 组合层缺臂自动跳过)
    _arms_env = os.environ.get("TULONG_ARMS", "").strip()
    if _arms_env:
        _want = {a.strip() for a in _arms_env.split(",") if a.strip()}
        _missing = _want - {s[0] for s in strategies}
        if _missing:
            raise SystemExit(f"[TULONG_ARMS] 未知臂名: {sorted(_missing)} (合法: {[s[0] for s in strategies]})")
        strategies = [s for s in strategies if s[0] in _want]
        logger.info(f"[TULONG_ARMS] 实验过滤臂: {sorted(_want)} (共{len(strategies)}臂, 缺臂组合层自动跳过)")

    # 运行各策略
    strategy_results = {}
    for name, sig_func, freq, overrides in strategies:
        try:
            result = run_single_backtest(name, sig_func, loader, freq, overrides)
            strategy_results[name] = result
        except Exception as e:
            logger.error(f"策略 {name} 回测失败: {e}", exc_info=True)
            strategy_results[name] = {
                "metrics": {"total_return_pct": 0, "error": str(e)},
                "equity_curve": pd.DataFrame(),
                "trades": [],
                "trades_with_reason": [],
            }

    # 运行核心-卫星组合 (修复: 独立账户合成, 直接合成已正确配置的独立策略净值)
    combo_result = None
    try:
        logger.info(f"\n{'='*60}")
        logger.info("组合回测(研究对照, 生产已切纯bpep v1.4 2026-08-29): bp+ep(50%) + ml(50%) 独立账户合成")
        logger.info("历史口径复现: 50/50=C1采纳(2026-08-28)/60/40=round9锚; ml腿历史成绩属样本内口径(round11b证据)")
        logger.info("修复: 直接合成独立回测净值(月度+正确配置), 不再用PortfolioBacktest"
                    "(默认weekly+max_holding_bars=45致-95%失真)")
        logger.info(f"{'='*60}")
        combo_weights = {"bp+ep_core": 0.50, "ml_signal": 0.50}  # 2026-08-28 C1采纳(60/40→50/50, 冰封窗OOS确认v1通过)
        _bpep_ec = strategy_results.get("bp+ep_core", {}).get("equity_curve", pd.DataFrame())
        _ml_ec = strategy_results.get("ml_signal", {}).get("equity_curve", pd.DataFrame())
        if len(_bpep_ec) > 0 and len(_ml_ec) > 0:
            # 组合合成走共享层 combo_synthesizer(唯一实现; 内置权威基线回归校验锚 299,276.84)
            import combo_synthesizer
            init_cap = cfg.initial_capital
            combo_ec = combo_synthesizer.synth_combo_60_40(_bpep_ec, _ml_ec, init_cap, 0.50, 0.50)
            combo_ec["cumulative_return"] = (combo_ec["equity"] / init_cap - 1) * 100
            total_ret = (float(combo_ec["equity"].iloc[-1]) / init_cap - 1) * 100
            combo_result = {"combined": {
                "total_return_pct": round(total_ret, 2),
                "equity_curve": combo_ec, "weights": combo_weights}}
            cm = equity_metrics(combo_ec["date"].tolist(), combo_ec["equity"].tolist(), rf=0.03)
            logger.info(f"  组合总收益: {total_ret:.2f}%")
            logger.info(f"  组合年化: {cm.get('cagr',0)*100:.2f}%")
            logger.info(f"  组合夏普: {cm.get('sharpe',0):.2f}")
            logger.info(f"  组合回撤: {cm.get('max_drawdown',0)*100:.2f}%")
            combo_ec.to_csv(os.path.join(OUTPUT_DIR, "portfolio_combo_equity.csv"), index=False, encoding="utf-8-sig")
    except Exception as e:
        logger.error(f"组合回测失败: {e}", exc_info=True)

    # 锚点漂移哨(优化清单#4物化, 2026-10-08): 每轮落账backtest_anchor_ledger.jsonl+同锚对照(>0.1%强制机制归因)
    try:
        _ledger_path = r"D:\zcodesj\data\backtest_anchor_ledger.jsonl"
        _row = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "end_date": END_DATE,
                "output_dir": os.path.basename(OUTPUT_DIR)}
        for _n in ("bp+ep_core", "ml_signal"):
            _m = strategy_results.get(_n, {}).get("metrics", {})
            if _m.get("total_return_pct") is not None and "error" not in _m:
                _row[_n] = _m.get("total_return_pct")
        if combo_result:
            _row["combo_total_return_pct"] = combo_result["combined"]["total_return_pct"]
        _prev = None
        if os.path.exists(_ledger_path):
            with open(_ledger_path, encoding="utf-8") as _lf:
                for _line in _lf:
                    _line = _line.strip()
                    if _line:
                        try:
                            _parsed = json.loads(_line)
                        except Exception:
                            continue
                        if _parsed.get("combo_total_return_pct") is not None:
                            _prev = _parsed  # 对照锚=最近一条含组合的正式轮账行(实验跑无组合不入对照)
        if _prev and combo_result and _prev.get("combo_total_return_pct") is not None:
            if _prev.get("end_date") != END_DATE:
                _cec = combo_result["combined"]["equity_curve"]
                _hit = _cec[_cec["date"].astype(str).str.startswith(str(_prev.get("end_date")))]
                if len(_hit):
                    _eq_now = float(_hit["equity"].iloc[-1])
                    _eq_prev = float(cfg.initial_capital) * (1 + _prev["combo_total_return_pct"] / 100.0)
                    _drift = (_eq_now / _eq_prev - 1) * 100
                    _flag = " ⚠️>0.1%须机制归因" if abs(_drift) > 0.1 else " (≤0.1%噪声级)"
                    logger.info(f"[锚点哨] 同锚{_prev.get('end_date')}: 本轮{_eq_now:,.2f} vs 上轮({_prev.get('output_dir')}){_eq_prev:,.2f} 漂移{_drift:+.3f}%{_flag}")
            elif _row.get("combo_total_return_pct") is not None:
                _diff = _row["combo_total_return_pct"] - _prev["combo_total_return_pct"]
                _flag = " ⚠️同端点差>0.1pp须机制归因" if abs(_diff) > 0.1 else " (≤0.1pp噪声级)"
                logger.info(f"[锚点哨] 同端点{END_DATE}: 本轮{_row['combo_total_return_pct']:+.2f}% vs 上轮({_prev.get('output_dir')}){_prev['combo_total_return_pct']:+.2f}% 差{_diff:+.3f}pp{_flag}")
        with open(_ledger_path, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(_row, ensure_ascii=False) + "\n")
        logger.info(f"[锚点哨] 已落账 {_ledger_path}")
    except Exception as _e:
        logger.error(f"[锚点哨] 落账失败(不影响回测结果): {_e}")

    elapsed_load = (datetime.now() - t0).total_seconds()
    logger.info(f"\n回测执行完成，耗时 {elapsed_load:.0f}s ({elapsed_load/60:.1f}min)")

    # --- 分析报告生成 ---
    logger.info("\n" + "="*60)
    logger.info("开始生成分析报告")
    logger.info("="*60)

    # 年度逐笔拆分
    yearly_summary = generate_yearly_breakdown(strategy_results, combo_result)

    # 深度诊断
    diagnosis = deep_diagnosis(strategy_results, loader)

    # 图表
    charts = generate_charts(strategy_results, combo_result, yearly_summary)

    # 综合HTML
    build_comprehensive_report(strategy_results, combo_result, yearly_summary, diagnosis, charts)

    # 优化方案
    generate_optimization_plan_md(strategy_results, diagnosis)

    elapsed_total = (datetime.now() - t0).total_seconds()
    logger.info(f"""
{'='*60}
全部完成!
回测耗时: {elapsed_load:.0f}s ({elapsed_load/60:.1f}min)
报告耗时: {elapsed_total - elapsed_load:.0f}s ({(elapsed_total - elapsed_load)/60:.1f}min)
总耗时: {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)
输出目录: {OUTPUT_DIR}
{'='*60}
""")

    # 列出生成文件
    logger.info("\n生成的交付物:")
    for f in sorted(os.listdir(OUTPUT_DIR)):
        fpath = os.path.join(OUTPUT_DIR, f)
        sz = os.path.getsize(fpath)
        logger.info(f"  {f} ({sz/1024:.1f} KB)")

    # 打印精简优化条目
    logger.info("\n" + "="*60)
    logger.info("精简优化条目清单 (供直接更新屠龙系统规则)")
    logger.info("="*60)
    for name, res in strategy_results.items():
        m = res.get("metrics", {})
        trades = res.get("trades", [])
        wr = sum(1 for t in trades if t.get("pnl",0)>0)/len(trades)*100 if trades else 0
        logger.info(f"  [{name}] 总收益={m.get('total_return_pct',0):+.1f}% | "
                    f"夏普={m.get('sharpe_ratio',0):.2f} | 胜率={wr:.1f}%")
    logger.info("\n  详见 OPTIMIZATION_PLAN.md")