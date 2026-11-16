"""
T1 封板回测公共模块（2026-08-12 知微抽取+修复）
====================================================
消除 t1_tp_backtest/t1_tp_layers/t1_close_buy/dip_backtest 的 95% 重复代码。

🔧 同时修复两个方法论 bug：
  Bug 1 (line 136 原): ret 分母 close0 → open1（实际买入价是 T+1 开盘）
  Bug 2 (line 176 原): ny 年化基准改为全样本跨度（统一可比，避免稀疏子集天文级放大）

参考：
  - 记忆 [[t1-target-evidence]]（含 bug 勘误记录）
  - Explore agent 报告 agent_c1538de2

作者: 知微
日期: 2026-08-12
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import List, Optional, Dict

import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# tdx_reader 位于 D:\zcodesj 根（本模块运行目录可能在 t1_research 下，需补 path）
_ZCODESJ_ROOT = Path(r'D:\zcodesj')
if str(_ZCODESJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZCODESJ_ROOT))


# ============================================================
# 1. 常量
# ============================================================
TDX = Path(r'D:\anzhuang\tdx\vipdoc')
START = '20180101'
END = '20260807'
INIT_CAP = 100_000.0

# A 股白名单（沪主板/深主板/创业板；排除逆回购/基金/B股/债券/科创/北交）
STOCK_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301')

# TDX .day 文件格式（32 字节/条）
DAY_DTYPE = np.dtype([('date', '<u4'), ('open', '<u4'), ('high', '<u4'), ('low', '<u4'),
                      ('close', '<u4'), ('amount', '<f4'), ('volume', '<u4'), ('unused', '<u4')])
# TDX .lc5/.lc1 分钟线格式
MIN_DTYPE = np.dtype([('date', '<u2'), ('time', '<u2'), ('open', '<f4'), ('high', '<f4'),
                      ('low', '<f4'), ('close', '<f4'), ('amount', '<f4'), ('volume', '<u4'),
                      ('unused', '<u4')])


# ============================================================
# 2. 数据读取
# ============================================================
def read_day(path: Path) -> pd.DataFrame:
    """读 TDX .day 文件 → DataFrame (index=日期)

    2026-08-31 迁移: 底层字节解析统一走 tdx_reader.read_day（全量验证过、纯 stdlib）。
    """
    from tdx_reader import read_day as _read_day

    code = path.stem[-6:]
    market = path.parent.parent.name
    rows = _read_day(market, code)
    if not rows:
        return pd.DataFrame()
    idx = pd.to_datetime([str(r[0]) for r in rows], format="%Y%m%d")
    return pd.DataFrame({
        "open": [r[1] for r in rows], "high": [r[2] for r in rows],
        "low": [r[3] for r in rows], "close": [r[4] for r in rows],
        "amount": np.array([r[5] for r in rows], dtype=np.float32),
        "volume": [float(r[6]) for r in rows],
    }, index=idx)


def read_min5(path: Path) -> pd.DataFrame:
    """读 TDX .lc5 5分钟线 → DataFrame (index=datetime, 列 open/high/low/close/volume)

    2026-08-31 新增（收敛 limitup_profile_v2/v3 内联实现）：
    底层委托 tdx_reader.read_min5（打包日期解码、volume=股，全量验证过）。
    契约与原 limitup_profile_v2.read_min5 一致（volume 为 float64、无 amount 列）。
    """
    from tdx_reader import read_min5 as _read_min5

    code = path.stem[-6:]
    market = path.parent.parent.name
    rows = _read_min5(market, code)
    if not rows:
        return pd.DataFrame()
    idx = pd.to_datetime([r["dt"] for r in rows], format="%Y-%m-%d %H:%M")
    return pd.DataFrame({
        "open": np.array([r["o"] for r in rows], dtype=np.float32),
        "high": np.array([r["h"] for r in rows], dtype=np.float32),
        "low": np.array([r["l"] for r in rows], dtype=np.float32),
        "close": np.array([r["c"] for r in rows], dtype=np.float32),
        "volume": [float(r["vol"]) for r in rows],
    }, index=idx)


def limit_ratio(code: str) -> float:
    """涨停比例：创业板/科创创业 20%，其余 10%"""
    return 0.20 if code.startswith(('300', '301')) else 0.10


# ============================================================
# 3. 事件采集
# ============================================================
def collect_events(code: str, market: str,
                   start: str = START,
                   kind: str = 'sealed') -> List[Dict]:
    """
    收集 T 日封板/炸板事件 + T+1 行情
    -------------------------------------------------------
    kind: 'sealed' 只封板 | 'broken' 只炸板 | 'both' 都收
    返回: list of dict {code, date, kind, is_new, one_word, close0, open1, high1, low1, close1}
    """
    day_path = TDX / market / 'lday' / f'{market}{code}.day'
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = read_day(day_path)
    except Exception:
        return []
    if len(day) < 60:  # 次新判定需要前 60 日
        return []
    # 留 start 前 60 日做 is_new 判定，但事件只从 start 起
    day = day[day.index >= pd.Timestamp('20171001')]
    if len(day) == 0:
        return []

    ratio = limit_ratio(code)
    lims = (day['close'].shift(1) * (1 + ratio)).round(2)
    touched = (day['high'] >= lims - 1e-9) & day['close'].notna()
    sealed = ((day['close'] - lims).abs() <= 0.001) & day['close'].notna()
    broken = touched & ~sealed

    start_ts = pd.Timestamp(start)
    events = []
    for i in range(len(day)):
        if day.index[i] < start_ts:
            continue
        lim = lims.iloc[i]
        if pd.isna(lim) or i + 1 >= len(day):
            continue

        is_sealed = bool(sealed.iloc[i])
        is_broken = bool(broken.iloc[i])
        if kind == 'sealed' and not is_sealed:
            continue
        if kind == 'broken' and not is_broken:
            continue
        if not is_sealed and not is_broken:
            continue

        c, o = day['close'].iloc[i], day['open'].iloc[i]
        nxt = day.iloc[i + 1]
        o1, h1, l1, c1 = nxt['open'], nxt['high'], nxt['low'], nxt['close']
        if o1 <= 0 or c1 <= 0:
            continue

        events.append({
            'code': code,
            'date': day.index[i].strftime('%Y%m%d'),
            'kind': 'sealed' if is_sealed else 'broken',
            'is_new': int(i < 60),
            'one_word': int(o >= lim - 1e-9),
            'close0': float(c), 'open1': float(o1),
            'high1': float(h1), 'low1': float(l1), 'close1': float(c1),
        })
    return events


def scan_all_codes(start: str = START, kind: str = 'sealed',
                   max_workers: int = 16) -> pd.DataFrame:
    """扫描所有 A 股，返回事件 DataFrame"""
    codes = []
    for market in ('sh', 'sz'):
        lday = TDX / market / 'lday'
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f'{market}*.day')]
    print(f'[info] 扫描 {len(codes)} 只 kind={kind} start={start}', flush=True)

    events = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(collect_events, c, m, start, kind) for c, m in codes]
        for f in as_completed(futs):
            events.extend(f.result())
    ev = pd.DataFrame(events)
    print(f'[info] 原始事件 {len(ev)}', flush=True)
    return ev


# ============================================================
# 4. 合规过滤
# ============================================================
def apply_filters(ev: pd.DataFrame,
                  min_price: float = 2.0,
                  exclude_new: bool = True,
                  exclude_one_word: bool = True,
                  kind: Optional[str] = 'sealed') -> pd.DataFrame:
    """
    合规过滤
    -------------------------------------------------------
    min_price: 最低股价
    exclude_new: 剔次新（上市<60 交易日）
    exclude_one_word: 剔一字板（开盘即封板，买不进）
    kind: None=不过滤 | 'sealed'=只封板 | 'broken'=只炸板
    """
    if ev.empty:
        return ev
    ev = ev.copy()
    if exclude_new:
        ev = ev[ev['is_new'] == 0]
    if exclude_one_word:
        ev = ev[ev['one_word'] == 0]
    if kind is not None:
        ev = ev[ev['kind'] == kind]
    ev = ev[ev['close0'] >= min_price]
    return ev


# ============================================================
# 5. 条件单收益计算（🔧 修复 Bug 1）
# ============================================================
def compute_tp_returns(ev: pd.DataFrame,
                       tp: float = 0.03,
                       cost: float = 0.003,
                       use_open1_denominator: bool = True,
                       stop_loss: Optional[float] = None) -> pd.DataFrame:
    """
    计算条件单止盈收益
    -------------------------------------------------------
    tp: 止盈阈值（0.03 = +3%）
    cost: 单边交易成本（佣金+印花税+滑点，默认 0.3%）
    use_open1_denominator:
      True  = 用 open1 做分母（🔧 正确，实际买入价）
      False = 用 close0 做分母（旧 bug，系统性高估）
    stop_loss:
      None  = 无止损（T+1 收盘卖）
      float = 盘中止损阈值（如 0.03 = -3%），若 low1 <= open1*(1-stop) 按止损价卖出
              止损优先于止盈（保守假设：同日双触时按止损）

    卖出优先级：止损 > 止盈 > 收盘
    """
    d = ev.copy()
    # 止盈判定
    tp_hit = d['high1'] >= d['open1'] * (1 + tp)
    d['hit'] = tp_hit
    d['sell'] = np.where(tp_hit, d['open1'] * (1 + tp), d['close1'])

    # 盘中止损（可选，优先于止盈）
    if stop_loss is not None and stop_loss > 0:
        sl_hit = d['low1'] <= d['open1'] * (1 - stop_loss)
        d['sell'] = np.where(sl_hit, d['open1'] * (1 - stop_loss), d['sell'])
        d['stop_hit'] = sl_hit
        d['final_status'] = np.where(sl_hit, 'stop_loss',
                                      np.where(tp_hit, 'take_profit', 'close_sell'))
    else:
        d['stop_hit'] = False
        d['final_status'] = np.where(tp_hit, 'take_profit', 'close_sell')

    # 🔧 Bug 1 修复：分母用 open1（实际买入价）
    denom = d['open1'] if use_open1_denominator else d['close0']
    d['ret'] = d['sell'] / denom - 1
    d = d[np.isfinite(d['ret']) & (d['ret'].abs() <= 0.5)].copy()
    d['trade_ret'] = d['ret'] - cost
    return d


# ============================================================
# 6. 完整回测（🔧 修复 Bug 2）
# ============================================================
def run_backtest_full(d: pd.DataFrame,
                      pos: float = 0.2,
                      max_per_day: int = 5,
                      init_cap: float = INIT_CAP,
                      ny_override: Optional[float] = None,
                      verbose: bool = True) -> Dict:
    """
    完整回测：按交易日复利
    -------------------------------------------------------
    pos: 单笔仓位占比（0.2 = 20%）
    max_per_day: 同日最多持仓数
    ny_override:
      None  = 用子集信号天数/242（旧 bug，稀疏子集放大）
      float = 传入固定年数（如 8.6），统一可比（🔧 正确）
    """
    if len(d) == 0:
        return {'n': 0, 'win': 0, 'ann': 0, 'cum': 0, 'mdd': 0,
                'monthly': 0, 'avg_ret': 0, 'n_days': 0, 'per_year': {}}

    d = d.copy()
    # 同日取前 max_per_day 只
    d['rank_in_day'] = d.groupby('date').cumcount()
    d = d[d['rank_in_day'] < max_per_day].copy()

    days = sorted(d['date'].unique())
    eq = init_cap
    peak = eq
    eq_curve = []
    wins = []
    n = 0
    per_year = {}

    for day in days:
        day_tr = d[d['date'] == day]
        n_day = len(day_tr)
        per = eq * pos / n_day
        day_pnl = 0.0
        for r in day_tr['trade_ret']:
            day_pnl += per * r
            wins.append(1 if r > 0 else 0)
            n += 1
        eq += day_pnl
        peak = max(peak, eq)
        eq_curve.append(eq)
        py = per_year.setdefault(day[:4], {'n': 0, 'pnl': 0.0})
        py['n'] += n_day
        py['pnl'] += day_pnl

    eq_arr = np.array(eq_curve)
    mdd = float((eq_arr / np.maximum.accumulate(eq_arr) - 1).min() * 100) if len(eq_arr) > 0 else 0
    cum = (eq / init_cap - 1) * 100

    # 🔧 Bug 2 修复：ny 年化基准
    if ny_override is not None:
        ny = ny_override
    else:
        ny = len(set(d['date'])) / 242

    ann = ((eq / init_cap) ** (1 / ny) - 1) * 100 if ny > 0 else 0
    monthly = ((1 + ann / 100) ** (1 / 12) - 1) * 100 if ann > -100 else 0

    result = {
        'n': n,
        'win': round(float(np.mean(wins) * 100), 1) if wins else 0,
        'ann': round(float(ann), 1),
        'cum': round(float(cum), 1),
        'mdd': round(mdd, 1),
        'monthly': round(float(monthly), 1),
        'avg_ret': round(float(d['trade_ret'].mean() * 100), 3),
        'n_days': len(days),
        'per_year': {y: {'n': v['n'], 'pnl': round(float(v['pnl']), 0)}
                     for y, v in sorted(per_year.items())}
    }
    if verbose:
        print(f"  n={result['n']} win={result['win']}% ann={result['ann']}% "
              f"monthly={result['monthly']}% mdd={result['mdd']}% "
              f"avg={result['avg_ret']}% days={result['n_days']}")
    return result


# ============================================================
# 7. 工具：计算全样本跨度（用于 ny_override）
# ============================================================
def compute_full_span_years(ev: pd.DataFrame, trading_days_per_year: int = 242) -> float:
    """计算事件覆盖的完整年数（用于 ny_override）"""
    if ev.empty:
        return 0.0
    unique_days = ev['date'].nunique()
    return unique_days / trading_days_per_year


def date_range_years(start: str = START, end: str = END,
                     trading_days_per_year: int = 242) -> float:
    """从日期范围估算年数（近似）"""
    from datetime import datetime
    s = datetime.strptime(start, '%Y%m%d')
    e = datetime.strptime(end, '%Y%m%d')
    calendar_days = (e - s).days
    return calendar_days / 365.25
