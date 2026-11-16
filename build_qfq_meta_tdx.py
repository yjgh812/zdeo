#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_qfq_meta_tdx.py  —  复权派生表构建 (P0-② 通达信本地版)
==================================================================
腾讯 web.ifzq.gtimg.cn/fqkline 接口已不可用(501), 改用:
  - 日线数据: 通达信本地 vipdoc/{sh,sz}/lday/*.day (不复权)
  - 复权因子: 新浪财经 qfq.js (https://finance.sina.com.cn/realstock/company/{sh/sz}{code}/qfq.js)

口径/约束与串行版保持一致:
  - 写锁解耦: 结果写入 dragon_quant_qfq.db
  - 日期统一 YYYYMMDD, change_pct 百分比口径
  - 脏数据闸门 |chg|>21 置 NULL
  - 腾讯无 qfq 的票回退复制原始 stock_daily_meta 行
  - ADDITIVE 断点续跑 (meta_min 精确判定)
"""
import argparse
import json
import os
import re
import sqlite3
import struct
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"   # 只读源
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"    # 唯一真源(2026-10-13 定谳翻转: 10/12原裁zc但链直写wb从未切换致分叉409ef2c, 实证wb=活体主库, 巡检/备份/熔断闸/影子均已对齐wb; 本脚本周六--full全量刷新直写wb, 低峰时段跑)
QFQ_TABLE = "stock_daily_meta_qfq"
TDX_DIR = r"D:\anzhuang\tdx"
WINDOW_START = "20160101"

SINA_QFQ_URL = "https://finance.sina.com.cn/realstock/company/{prefix}{code}/qfq.js"
SLEEP_PER_REQ = 0.15          # 新浪 qfq.js 请求间隔(秒)
WORKERS = 8                   # 并发 worker(本地 IO + 新浪请求)
MAX_RETRY = 3
TIMEOUT = 20
TICKFLOW_BATCH_TIMEOUT = 90   # 2026-08-29 修复: 免费源无响应时整链曾空挂20min; 单批超时90s后回退vipdoc

# TickFlow 数据源 (2026-08-23 接入: 底层日K源 vipdoc -> tickflow, 复权因子仍新浪 free 档可用)
TICKFLOW_CHUNK = 150          # 每批标的数(控制 free-api 频率)
TICKFLOW_WINDOW_LEAD = 20     # 区间起点回退自然日数(覆盖长假, 保证含前一交易日算 change_pct)
# 连续N批超时中止剩余预拉取(2026-10-09主人令"按推荐继续"落地): 10/8晚13批全超时+10/9晚29批
# ~50分钟纯等待实证夜间免费源高概率全批超时, 而vipdoc回退7.5分钟完成5559只零失败反而更快;
# 提前中止只减少等待, 缺省票仍按既有次序走vipdoc/备源回退, 数据口径零变化。
TICKFLOW_CONSEC_TIMEOUT_ABORT = 5

_local = threading.local()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _dash(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


# ----------------------------------------------------------------------------
# 数据库准备
# ----------------------------------------------------------------------------
def ensure_table(qfq_conn: sqlite3.Connection):
    qfq_conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {QFQ_TABLE} (
            code      TEXT NOT NULL,
            date      TEXT NOT NULL,
            open      REAL, high REAL, low REAL, close REAL,
            volume    REAL, amount REAL, change_pct REAL,
            PRIMARY KEY (code, date)
        )
    """)
    qfq_conn.commit()


def get_universe(meta_conn: sqlite3.Connection):
    # 白名单: 仅真A股(60沪主/00深主/30创/68科/92北交) — 2026-08-16库级清理后防回填复现
    # (此前全代码入库导致ETF/转债/指数5738只混入, 转债价格错位制造污染日)
    # 2026-11-08 二次补缝: 00* 号段与上交所/中证指数码(000xxx)撞码 —— 无深市同码股票时行情源按
    # 指数供给, 99 个 market='其他' 指数码(沪深300/上证转债/380系列等)已混入 qfq(部分增量仍写到
    # 20261029)。判别器=深市 lday 文件存在性: 00* 码必须有 vipdoc sz/lday/sz{code}.day,
    # sh000xxx 指数序列无 sz 文件(000001 平安银行有 sz 文件正常保留)。qfq 存量行按北交所段先例
    # 留库自滤, 不删行。
    rows = meta_conn.execute(
        "SELECT DISTINCT code FROM stock_daily_meta "
        "WHERE code GLOB '60*' OR code GLOB '00*' OR code GLOB '30*' "
        "   OR code GLOB '68*' OR code GLOB '92*' "
        "ORDER BY code"
    ).fetchall()
    _codes = [r[0] for r in rows]
    _kept = []
    for _c in _codes:
        if _c.startswith('00') and not os.path.exists(os.path.join(
                r'D:\anzhuang\tdx\vipdoc', 'sz', 'lday', 'sz' + _c + '.day')):
            continue  # sh 指数序列(无深市同码股票的 000xxx 码)
        _kept.append(_c)
    return _kept


def get_coverage(qfq_conn: sqlite3.Connection, meta_conn: sqlite3.Connection, code: str):
    r = qfq_conn.execute(
        f"SELECT MIN(date), MAX(date), COUNT(*) FROM {QFQ_TABLE} WHERE code=?", (code,)
    ).fetchone()
    m = meta_conn.execute(
        "SELECT MIN(date) FROM stock_daily_meta WHERE code=?", (code,)
    ).fetchone()
    def _s(v):
        return str(v).replace("-", "") if v is not None else None
    return (_s(r[0]), _s(r[1]), (r[2] or 0), _s(m[0] if m else None))


class QfqGuardBlocked(Exception):
    """写前 vipdoc 对账拦截（2026-08-26 O2, E35 防护）：payload 最新日 qfq 收盘
    与 vipdoc 同日收盘偏离>5% → 判定量级/因子错误，拒绝写库防止污染扩散。"""


QFQ_WRITE_GUARD_DEVIATION = 0.05


def _vipdoc_close_on(code: str, date: str):
    """vipdoc 指定日期收盘价；无该日数据（停牌/未同步）返回 None（不误判）。
    与 fix_qfq_rebuild.scan_polluted 同款同日对比逻辑。"""
    df = read_tdx_day(code)
    if df is None or df.empty:
        return None
    try:
        return float(df.loc[pd.Timestamp(date)].get("close"))
    except (KeyError, TypeError, ValueError):
        return None


def _write_rows(qfq_conn: sqlite3.Connection, code: str, rows):
    # 写前守卫（2026-08-26 O2, qfq污染22只收口）: 最新行 qfq 收盘 vs vipdoc 同日收盘
    # 偏离>5% → 拒绝整批写入（fail-closed）。因子锚定最新交易日≈1.0，故最新行 qfq≈原始价，
    # tickflow 类量级错误（0.29x~8x）必被拦；vipdoc 同日无数据（停牌/未同步）则放行不误判。
    if rows:
        _last = rows[-1]
        _date, _close = str(_last[0]), _last[4]
        _vclose = _vipdoc_close_on(code, _date)
        if (_vclose and _vclose > 0 and _close
                and abs(float(_close) / _vclose - 1) > QFQ_WRITE_GUARD_DEVIATION):
            raise QfqGuardBlocked(
                f"{code} {_date} qfq_close={_close} vs vipdoc_close={_vclose} "
                f"偏离 {float(_close) / _vclose:.2f}x > {QFQ_WRITE_GUARD_DEVIATION:.0%} → 拒绝写入")
    sql = f"""INSERT OR REPLACE INTO {QFQ_TABLE}
              (code,date,open,high,low,close,volume,amount,change_pct)
              VALUES (?,?,?,?,?,?,?,?,?)"""
    payload = [(code, *r) for r in rows]
    for attempt in range(1, 6):
        try:
            qfq_conn.executemany(sql, payload)
            qfq_conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 5:
                time.sleep(2 * attempt)
                continue
            raise
    raise sqlite3.OperationalError(f"write failed after 5 retries for {code}")


# ----------------------------------------------------------------------------
# 通达信本地日线读取
# ----------------------------------------------------------------------------
def read_tdx_day(symbol: str):
    """读取通达信本地 .day 日线, 返回 DataFrame[dateindex, open,high,low,close,amount,volume]

    2026-08-31 迁移: 底层解析统一走 tdx_reader.read_day（全量验证过、纯 stdlib），
    此处保留 DataFrame 契约薄壳，调用方（worker/对账/fix_*）契约不变。
    """
    from tdx_reader import read_day as _read_day

    # 2026-09-10 D3: 市场映射对齐 tdx_reader._normalize(6/9/5→sh, 4/8→bj, 其余 sz)——
    # 原 "6→sh else sz" 把 92 码北交所股按 sz 读→None→_fallback_raw 伪复权(原始价冒充
    # qfq), 写前守卫对这些码从不生效
    # 20261011批1: 92 码归 bj 先于 9→sh 判定(D3 实际只修了 4/8, 920 新码段仍被 9→sh 吞——
    # sh/lday 无 sh920* → 恒走 _fallback_raw, 与 tdx_reader._normalize 同步修复保持两处同律)
    if symbol.startswith("92"):
        market = "bj"
    elif symbol.startswith(("6", "9", "5")):
        market = "sh"
    elif symbol.startswith(("4", "8")):
        market = "bj"
    else:
        market = "sz"
    rows = _read_day(market, symbol)
    if not rows:
        return None
    df = pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "amount", "volume"]
    )
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index()
    return df


# ----------------------------------------------------------------------------
# 新浪前复权因子
# ----------------------------------------------------------------------------
def fetch_sina_qfq(symbol: str):
    """从新浪财经 qfq.js 获取前复权因子, 返回 DataFrame[dateindex, factor]
    2026-08-24 提速: 北交所码(4/8/920 前缀)新浪无 qfq.js(恒 404), 每次失败 + 3 次重试 +
    tickflow 预拉取全浪费约 2-3 分钟。短路直接返回 None → compute_qfq 走原始价(factor=1.0),
    北交所新上市段基本无除权, 原始价即正确复权价; 且北交所已剔除交易域。
    2026-08-25 修复: 689 段 CDR(689009 九号公司)新浪 qfq.js 同样无数据, 但走"空 data
    静默 None"路径 → worker 误判 "failed", 整链每天恒 rc=1(狼来了)。纳入同一短路:
    该股已入库 1414 行即 factor=1.0 原始价口径(qfq==raw 逐点一致, CDR 无除权),
    且科创板/CDR 已剔除交易域。"""
    if symbol.startswith(("4", "8", "920", "689")):
        return None
    # 2026-08-24 P1修复: SLEEP_PER_REQ 此前定义从未生效(8并发无间隔, 限流概率放大)。
    # ThreadPool 下 per-worker sleep 非全局节流, 但降低单线程连发频率, 配合重试更稳。
    time.sleep(SLEEP_PER_REQ)
    prefix = "sh" if symbol.startswith("6") else "sz"
    url = SINA_QFQ_URL.format(prefix=prefix, code=symbol)
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                text = resp.read().decode("gbk", errors="ignore")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end <= start:
                return None
            obj = json.loads(text[start:end])
            data = obj.get("data", [])
            if not data:
                # 20261011批5尾: HTTP200+空data=真无因子段(689009类), 与网络失败分流——
                # 混同曾迫使689硬编码短路, 任何新无因子股复现整链每晚rc=1狼来了
                return "EMPTY"
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["d"])
            df["factor"] = df["f"].astype(float)
            df = df[["date", "factor"]].sort_values("date").set_index("date")
            return df
        except Exception as e:
            last_err = e
            time.sleep(0.5 * attempt)
    log(f"[warn] {symbol} qfq factor failed: {last_err}")
    return None


# ----------------------------------------------------------------------------
# 因子事件表缓存 (2026-10-13 立项, 主人令"现在干"; 回测价格源合同变更=三层安全网)
# 原理: 前复权增量口径下, 无新除权事件则事件表不变→复用缓存=产出逐位一致;
# 事件名单=pro.dividend(ex_date=target) 一口拉(实测10/12=16只); 三层安全网=
# ①事件名单源(tushare) ②写前锚点校验(最新日qfq≈vipdoc, 漏大事件必炸) ③TTL强制
# 刷新(兜名单漏报的小额分红, 3天内有界自愈)。QFQ_FACTOR_CACHE=1 启用, 默认关。
# ----------------------------------------------------------------------------
FACTOR_CACHE_DIR = Path(r"D:\zcodesj\data\qfq_factor_cache")
FACTOR_CACHE_HINT: Optional[set] = None   # 当日除权票集合; None=缓存禁用(全量取新)
FACTOR_CACHE_TTL_DAYS = 3


def set_factor_event_hint(event_codes):
    """run_qfq 注入当日除权票集合(或 None 禁用缓存)。线程池启动前调用=线程安全。"""
    global FACTOR_CACHE_HINT
    FACTOR_CACHE_HINT = event_codes


def _factor_cache_path(code: str) -> Path:
    return FACTOR_CACHE_DIR / f"{code}.json"


def _load_factor_cache(code: str):
    """读缓存事件表(超TTL/损坏→None=回源)。格式与 fetch_sina_qfq 返回严格同构。"""
    try:
        p = _factor_cache_path(code)
        if not p.is_file():
            return None
        import json as _json
        obj = _json.loads(p.read_text(encoding="utf-8"))
        fetched = datetime.strptime(obj["fetched_date"], "%Y%m%d")
        if (datetime.now() - fetched).days >= FACTOR_CACHE_TTL_DAYS:
            return None
        df = pd.DataFrame({"factor": [float(f) for f in obj["factors"]]},
                          index=pd.to_datetime(obj["dates"], format="%Y-%m-%d"))
        df.index.name = "date"
        return df.sort_index()
    except Exception:
        return None


def _save_factor_cache(code: str, factor_df) -> None:
    """sina 成功拉取后落缓存(原子写); 失败仅 WARN 不阻断(下次重取)。"""
    try:
        import json as _json
        FACTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        obj = {"fetched_date": datetime.now().strftime("%Y%m%d"),
               "dates": [d.strftime("%Y-%m-%d") for d in factor_df.index],
               "factors": [float(f) for f in factor_df["factor"]]}
        tmp = _factor_cache_path(code).with_suffix(".tmp")
        tmp.write_text(_json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_factor_cache_path(code))
    except Exception as e:
        logger.warning("因子缓存写失败 %s: %s", code, e)


# ----------------------------------------------------------------------------
# TickFlow 日K 数据源 (free-api: 历史日K免注册; 复权因子 free 档无权限 -> 默认新浪)
# ----------------------------------------------------------------------------
def _tickflow_client():
    """TickFlow SDK 客户端: 配置了 TICKFLOW_API_KEY 走付费端点, 否则 free(免注册历史日K)。"""
    from tickflow import TickFlow
    key = (os.environ.get("TICKFLOW_API_KEY") or "").strip()
    return TickFlow(api_key=key) if key else TickFlow.free()


def _tickflow_ms(compact_date: str) -> int:
    """YYYYMMDD -> 东八区当日 00:00 毫秒时间戳(与 tickflow K线时间戳对齐)。"""
    dt = datetime.strptime(compact_date, "%Y%m%d").replace(tzinfo=timezone(timedelta(hours=8)))
    return int(dt.timestamp() * 1000)


def _to_tickflow_symbol(code: str) -> str:
    """6位纯码 -> tickflow symbol (60/68沪, 00/30深, 92北交)。"""
    if code.startswith(("60", "68")):
        return f"{code}.SH"
    if code.startswith(("92",)):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _ts_to_dates(ts_list) -> list[str]:
    """毫秒时间戳列表 -> YYYYMMDD 列表(东八区)。"""
    return [datetime.fromtimestamp(t / 1000, tz=timezone(timedelta(hours=8))).strftime("%Y%m%d")
            for t in ts_list]


def _tickflow_batch_with_timeout(tf, syms, start_ms, end_ms, timeout_s: float):
    """tickflow 批量调用加超时上界(守护线程)。

    2026-08-29 修复: 原裸调 tf.klines.batch() 无超时, 免费源无响应时整条 22:00 链
    无限空挂(实证 20+ min)。超时抛 TimeoutError -> 调用方 log 后 continue,
    该批标的缺省 -> worker 层既有回退 vipdoc, 不阻塞整链。
    """
    box: dict = {}

    def _run():
        try:
            box["raw"] = tf.klines.batch(syms, period="1d", adjust="none",
                                         start_time=start_ms, end_time=end_ms,
                                         count=10000, as_dataframe=True, show_progress=False)
        except BaseException as e:  # noqa: BLE001 兜底带回主线程
            box["err"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"tickflow batch 超时 {timeout_s}s ({len(syms)}只)")
    if "err" in box:
        raise box["err"]
    return box["raw"]


def fetch_tickflow_raw_map(codes: list[str], sd: str, ed: str) -> dict[str, pd.DataFrame]:
    """批量拉 tickflow 不复权日K, 返回 {code: DataFrame[dateidx, open,high,low,close,amount,volume]}。

    区间起点回退 TICKFLOW_WINDOW_LEAD 自然日, 保证含 sd 前一交易日(算 change_pct);
    调用方随后过滤到 [sd, ed]。拉取失败的标的直接缺省(由 worker 回退 vipdoc)。
    """
    tf = _tickflow_client()
    start_ms = _tickflow_ms((datetime.strptime(sd, "%Y%m%d") - timedelta(days=TICKFLOW_WINDOW_LEAD)).strftime("%Y%m%d"))
    end_ms = _tickflow_ms(ed) + 24 * 3600 * 1000  # ed 日收盘后
    out: dict[str, pd.DataFrame] = {}
    consec_timeout = 0  # 连续超时批计数(成功批清零, 达 TICKFLOW_CONSEC_TIMEOUT_ABORT 中止)
    for i in range(0, len(codes), TICKFLOW_CHUNK):
        syms = [_to_tickflow_symbol(c) for c in codes[i:i + TICKFLOW_CHUNK]]
        try:
            raw = _tickflow_batch_with_timeout(tf, syms, start_ms, end_ms, TICKFLOW_BATCH_TIMEOUT)
        except Exception as e:
            consec_timeout += 1
            log(f"[warn] tickflow 批量拉取失败/超时({len(syms)}只): {type(e).__name__}: {e}")
            if consec_timeout >= TICKFLOW_CONSEC_TIMEOUT_ABORT:
                remain = len(codes) - i - len(syms)
                log(f"[warn] tickflow 连续{TICKFLOW_CONSEC_TIMEOUT_ABORT}批超时, 判定免费源当前不可用, "
                    f"中止剩余预拉取(剩{remain}只), 已命中{len(out)}只; 缺省票按既有次序走 vipdoc/备源回退")
                break
            continue
        consec_timeout = 0
        if not isinstance(raw, dict):
            log(f"[warn] tickflow 返回异常类型: {type(raw).__name__}")
            continue
        for sym, sub in raw.items():
            if sub is None or len(sub) == 0:
                continue
            code = sym.split(".")[0]
            df = pd.DataFrame({
                "date": pd.to_datetime(_ts_to_dates(sub["timestamp"].to_list()), format="%Y%m%d"),
                "open": sub["open"].to_list(),
                "high": sub["high"].to_list(),
                "low": sub["low"].to_list(),
                "close": sub["close"].to_list(),
                "amount": sub["amount"].to_list(),
                "volume": sub["volume"].to_list(),
            })
            # tickflow volume 单位为"手"(100股), 与 qfq库/meta 的"股"不一致(实测 600519: 33472手 vs 3347231股)
            # -> 统一乘 100 转股, 保持 qfq库/qlib_bin 口径; amount 单位为元, 与 meta/tickflow 一致, 无需换算
            df["volume"] = df["volume"] * 100
            # 过滤停牌/非交易平盘行(volume=0, OHLC 恒等): 对齐 vipdoc 仅真实交易日语义, 避免平盘假数据污染 qfq 库
            df = df[df["volume"] > 0]
            out[code] = df.set_index("date").sort_index()
    return out


# ----------------------------------------------------------------------------
# 备用日线源(第3层): tickflow 缺省 且 vipdoc 陈旧 时补位不复权原始价
# ----------------------------------------------------------------------------
# 2026-10-10 接线(主人批准"继续"); 前置验收 = analysis/backup_daily_probe.py
# (2026-10-08 三通道×三样本 5日 OHLCV 与 vipdoc 逐位一致)。
# 口径契约: 备源只取【不复权原始价】, 复权仍走新浪 qfq 因子链 —— 绕开
# baostock 涨跌幅复权口径陷阱, 与 qfq 库(通达信×新浪因子)天然兼容。
# 量字段换算: 腾讯/tushare vol 手→股 ×100; tushare amount 千元→元 ×1000;
# baostock 元原样。停牌/平盘行(volume=0)过滤, 对齐 tickflow 语义。
# 通道次序 = 轻→重(无token HTTP → 官方库 → token库), 通道级超时上界,
# 首个非空命中通道生效; 单轮补位标的数上界防限频失控。
# ⚠️ 腾讯 fqkline 2026-08 曾 501 不可用(见本文件头 docstring), 2026-10-08
#    实测恢复 —— 备源是降级保险层, 任何通道都不得视为稳定依赖。
# 2026-10-09 第4通道 sohu(搜狐族, 备源第9轮研究 §八, 主人令继续): hisHq
#    免token HTTP, 三样本 OHLC 与 vipdoc 逐位一致(reports/free_source_probe_
#    20261009.md); 追加尾部不改既有命中次序(最小改动); 量纲 手×100=股;
#    北交由 universe 过滤自然排除。
BACKUP_SOURCE_ORDER = ("tencent", "baostock", "tushare", "sohu")

# ----------------------------------------------------------------------------
# 前复权因子多源链(20261011批5尾, 主人令"换几个数据源"): sina 慢区(0006xx-0008xx
# 请求病态缓慢, 两夜实测同位停滞)反制——按股整串单源安全(因子为每股自身归一, 跨股
# 水位不可比, 混源才有伪跳变风险), 链内单股只会用到一个源。新浪 8s 硬超时(复用
# _run_with_timeout) + 累计慢/败 run 级降级, 直接杀掉慢区爬行。口径=除法因子且
# 最新≈1.0(与 sina 同锚), compute_qfq 锚点守卫继续兜底。BJ/CDR 段维持原语义。
FACTOR_SOURCE_ORDER = ("sina", "baostock", "tushare")  # tushare adj_factor限频1次/分=末位后备
FACTOR_SINA_TIMEOUT_S = 8.0
FACTOR_DEMOTE_AFTER = 8      # 连续慢/败次数阈值→run 级降级该源
_factor_slow_streak: dict = {}
_factor_demoted: set = set()


# 因子回取窗口下限 = 写入域起点回退7天(取2016-01-01前最后一根bar作ffill锚)。
# 2026-10-21 观察项①收口(接 _qfq_continuity_ok 检验域修复同日): 原 3700 天窗
# (≈2016-10 起)对 2016-01..2016-10 之间有除权的票因子缺台阶, 闸在写入域内见到
# 原始悬崖即假拒 → 断供覆盖缺口; 窗口对齐 WINDOW_START 后该段事件天然在序列内。
FACTOR_FETCH_START = (datetime.strptime(WINDOW_START, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")


def _tushare_pro():
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token and Path(BACKUP_TUSHARE_TOKEN_FILE).is_file():
        token = Path(BACKUP_TUSHARE_TOKEN_FILE).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN missing (env→config/tushare_token.txt)")
    return ts.pro_api(token)


def fetch_tushare_qfq_factor(code: str):
    """tushare adj_factor → 前复权除法因子(最新=1.0)。DataFrame[date,factor] / "EMPTY" / None。"""
    pro = _tushare_pro()
    full = _to_backup_symbol(code, "tushare")
    if full is None:
        return None
    df = pro.adj_factor(ts_code=full,
                        start_date=FACTOR_FETCH_START,
                        end_date=datetime.now().strftime("%Y%m%d"))
    if df is None or df.empty:
        return "EMPTY"
    df = df.sort_values("trade_date")
    adj = pd.to_numeric(df["adj_factor"], errors="coerce")
    if adj.dropna().empty:
        return "EMPTY"
    latest = float(adj.dropna().iloc[-1])
    out = pd.DataFrame({"date": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
                        "factor": latest / adj})
    out = out.dropna().set_index("date").sort_index()
    return out if not out.empty else "EMPTY"


def fetch_baostock_qfq_factor(code: str):
    """baostock 同源自洽比值 raw(adjustflag=3)/qfq(2) → 除法因子(最新=1.0)。"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login fail: {lg.error_msg}")
    full = _to_backup_symbol(code, "baostock")
    if full is None:
        return None
    sd = FACTOR_FETCH_START
    ed = datetime.now().strftime("%Y%m%d")

    def _q(flag):
        rs = bs.query_history_k_data_plus(
            full, "date,close", start_date=_dash(sd), end_date=_dash(ed),
            frequency="d", adjustflag=flag)
        rows = {}
        while rs.error_code == "0" and rs.next():
            d, c = rs.get_row_data()
            try:
                rows[d.replace("-", "")] = float(c)
            except ValueError:
                continue
        return rows

    raw, qfq = _q("3"), _q("2")
    if not qfq:
        return "EMPTY"
    common = sorted(set(raw) & set(qfq))
    if len(common) < 2:
        return "EMPTY"
    fac = {d: raw[d] / qfq[d] for d in common if qfq[d]}
    last_d = common[-1]
    norm = fac[last_d] if fac.get(last_d) else max(fac.values())
    out = pd.DataFrame(sorted((d, fac[d] / norm) for d in fac),
                       columns=["date", "factor"])
    out["date"] = pd.to_datetime(out["date"], format="%Y%m%d")
    return out.set_index("date").sort_index()


def _qfq_continuity_ok(code: str, fac) -> bool:
    """回退源连续性闸: qfq收盘日收益超板限=错基准特征(漏调送转的悬崖)。True=通过。"""
    try:
        raw = read_tdx_day(code)
        if raw is None or raw.empty or len(raw) < 3:
            return True                   # 无法核验→不因闸加阻(诚实留痕由调用方日志)
        df = raw.reset_index()[["date", "close"]].merge(
            fac.reset_index(), on="date", how="left")
        # 2026-10-21 探针修正(analysis/probe_baostock_factor_shadow_20261021.py): 检验域
        # 对齐写入域。因子回退源按3700天窗供给(≈2016起), 原实现却对vipdoc全史(1991/2001
        # 起)检验——2016前因子=bfill常数, 真实历史除权跳变(茅台2006一拆十单日-56%)被误判
        # 为错基准, 2016前上市老票被系统性假拒(8样本7拒, 唯一通过的300750=2018上市, 全
        # 吻合); 而compute_qfq仅写≥WINDOW_START, 被拒段永不入库=纯假阳性, 新浪断供夜
        # 回退链对老票实际不可用。
        # 同日二次修正(d026e15后窗口对齐复验): 前轮"残余2拒=真悬崖"归因有误——000001
        # 2016-06-16(10派1.53转2)/000333 2016-05-06(10派12转5), hithink事件流证实均为
        # 真实除权, 但同属3700天窗起点(≈2016-10)截断伪影; FACTOR_FETCH_START 对齐
        # WINDOW_START 后 baostock 因子在事件日正常台阶(对sina偏差0.086%/0.025%),
        # 8/8 过闸。闸保护语义本体(写入域内漏调送转悬崖→拒收→failed→次夜重试)经
        # m7 合成情形②复算保留不变。
        df = df[df["date"] >= pd.Timestamp(WINDOW_START)]
        df["factor"] = df["factor"].ffill().bfill().fillna(1.0)
        df["qfq"] = df["close"] / df["factor"]
        r = df["qfq"].pct_change().abs().dropna()
        if r.empty:
            return True
        lim = 0.22 if code.startswith("30") else 0.15
        return bool(r.max() <= lim)
    except Exception:
        return True                       # 闸自身异常不阻断(对齐守卫哲学)


def fetch_factor(code: str):
    """因子多源链。返回 DataFrame(date,factor) / "EMPTY"(全源皆无) / None(全源失败)。"""
    if code.startswith(("4", "8", "920", "689")):
        return None                       # BJ/CDR 段维持原语义(factor=1.0), 不入链
    saw_empty = False
    for src in FACTOR_SOURCE_ORDER:
        if src in _factor_demoted:
            continue
        try:
            if src == "sina":
                # 因子缓存(2026-10-13): 非当日除权票且缓存新鲜→复用事件表(无新事件
                # 事件表不变=产出逐位一致); 当日除权票/缓存缺失/超TTL→正常回源并落缓存。
                if FACTOR_CACHE_HINT is not None and code not in FACTOR_CACHE_HINT:
                    cached = _load_factor_cache(code)
                    if cached is not None:
                        return cached
                try:
                    r = _run_with_timeout(lambda c=code: fetch_sina_qfq(c), FACTOR_SINA_TIMEOUT_S)
                except Exception as e:
                    log(f"[factor:sina] {code} 异常: {type(e).__name__}: {str(e)[:80]}")
                    r = None
                if r is None:
                    # 2026-10-13: 16线程突发下新浪随机节流(实测每轮1-3只随机票拉取失败,
                    # 单票隔离重试全成=纯瞬态)——单次退避重试, 成功则不计降级连败
                    # (非源劣化, 是突发节流)。
                    time.sleep(1.5)
                    log(f"[factor:sina] {code} 首拉失败, 退避1.5s重试一次")
                    try:
                        r = _run_with_timeout(lambda c=code: fetch_sina_qfq(c), FACTOR_SINA_TIMEOUT_S)
                    except Exception as e:
                        log(f"[factor:sina] {code} 重试仍失败: {type(e).__name__}: {str(e)[:80]}")
                        r = None
                if r is not None and not isinstance(r, str) and len(r) > 0:
                    _save_factor_cache(code, r)
            elif src == "tushare":
                r = fetch_tushare_qfq_factor(code)
            else:
                r = fetch_baostock_qfq_factor(code)
        except Exception as e:
            log(f"[factor:{src}] {code} 异常: {type(e).__name__}: {str(e)[:80]}")
            r = None
        if isinstance(r, str) and r == "EMPTY":
            saw_empty = True
            continue                      # 本源真无因子→换源(他源可能有)
        if r is not None and src != "sina" and not _qfq_continuity_ok(code, r):
            # 20261011深夜验证实证: 三源对000626分歧~996%(送转刷新不同步族)——回退源
            # 盲收会引入错基准。连续性闸: 回退源qfq日收益超板限(主板15%/创业板22%)
            # =错基准特征(如漏调10转10的-50%悬崖)→拒收该源, 股票failed下次重试。
            log(f"[factor:{src}] {code} 连续性闸拒收(qfq日收益超板限, 疑因子错基准)")
            r = None
        if r is None:
            _factor_slow_streak[src] = _factor_slow_streak.get(src, 0) + 1
            if _factor_slow_streak[src] >= FACTOR_DEMOTE_AFTER:
                _factor_demoted.add(src)
                log(f"[factor] {src} 连续慢/败{FACTOR_DEMOTE_AFTER}→run级降级")
            continue
        _factor_slow_streak[src] = 0
        return r
    return "EMPTY" if saw_empty else None
BACKUP_TIMEOUT_S = 120        # 单通道整通道超时上界(守护线程, 同 tickflow 模式)
BACKUP_MAX_CODES = 300        # 单轮补位标的数上界(超出截断+WARN, 防限频失控)
BACKUP_TUSHARE_TOKEN_FILE = r"D:\zcodesj\config\tushare_token.txt"


def vipdoc_latest_date(code: str = "600519") -> "str | None":
    """vipdoc 指定代表票最新交易日(YYYYMMDD); 读不到返回 None(不作数, 不误判)。

    用于 qfq 阶段断供判定: vipdoc 最新日 < target 即陈旧/缺失。
    """
    try:
        df = read_tdx_day(code)
        if df is None or df.empty:
            return None
        return df.index.max().strftime("%Y%m%d")
    except Exception:
        return None


def _to_backup_symbol(code: str, style: str) -> "str | None":
    """6位码 -> 备源代码格式。仅映射沪/深(60/68/9/5→sh, 其余→sz);
    北交(92/4/8)备源通道不覆盖, 返回 None 由调用方自然缺省(照旧回退 vipdoc/meta)。"""
    if not code.startswith(("60", "68", "00", "30")):
        return None
    mkt = "sh" if code.startswith(("60", "68")) else "sz"
    if style == "tencent":
        return f"{mkt}{code}"
    if style == "baostock":
        return f"{mkt}.{code}"
    if style == "sohu":
        return code                             # 搜狐 cn_{code} 裸6位码
    return f"{code}.{mkt.upper()}"          # tushare 600519.SH


def _backup_fetch_tencent(codes: list, sd: str, ed: str) -> dict:
    import requests
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://gu.qq.com/",
    })
    out: dict = {}
    for code in codes:
        sym = _to_backup_symbol(code, "tencent")
        if sym is None:
            continue
        r = sess.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                     params={"param": f"{sym},day,{_dash(sd)},{_dash(ed)},320,"},
                     timeout=15)
        rows = ((r.json().get("data") or {}).get(sym) or {}).get("day") or []
        if not rows:
            continue
        recs = []
        for it in rows:
            try:
                v = float(it[5]) * 100.0        # 手 → 股
            except (ValueError, IndexError, TypeError):
                continue
            if v <= 0:
                continue                        # 停牌/平盘行过滤, 对齐 tickflow
            recs.append((str(it[0]).replace("-", ""), float(it[1]), float(it[3]),
                         float(it[4]), float(it[2]), v))
        if recs:
            df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
            out[code] = df.set_index("date").sort_index()
        time.sleep(0.12)                        # 轻间隔防限频
    return out


def _backup_fetch_baostock(codes: list, sd: str, ed: str) -> dict:
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login fail: {lg.error_msg}")
    try:
        out: dict = {}
        for code in codes:
            full = _to_backup_symbol(code, "baostock")
            if full is None:
                continue
            rs = bs.query_history_k_data_plus(
                full, "date,open,high,low,close,volume,amount",
                start_date=_dash(sd), end_date=_dash(ed), frequency="d", adjustflag="3")
            if rs.error_code != "0":
                continue
            recs = []
            while rs.error_code == "0" and rs.next():
                d, o, h, l, c, v, a = rs.get_row_data()
                try:
                    vf = float(v)
                except ValueError:
                    continue
                if vf <= 0:
                    continue
                recs.append((d.replace("-", ""), float(o), float(h), float(l), float(c), vf))
            if recs:
                df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
                df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
                out[code] = df.set_index("date").sort_index()
        return out
    finally:
        bs.logout()


def _backup_fetch_tushare(codes: list, sd: str, ed: str) -> dict:
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token and Path(BACKUP_TUSHARE_TOKEN_FILE).is_file():
        token = Path(BACKUP_TUSHARE_TOKEN_FILE).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN missing (env→config/tushare_token.txt)")
    pro = ts.pro_api(token)
    out: dict = {}
    for code in codes:
        full = _to_backup_symbol(code, "tushare")
        if full is None:
            continue
        df = pro.daily(ts_code=full, start_date=sd, end_date=ed)
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date")
        recs = []
        for r in df.itertuples():
            v = float(r.vol) * 100.0            # 手 → 股
            if v <= 0:
                continue
            recs.append((str(r.trade_date), float(r.open), float(r.high),
                         float(r.low), float(r.close), v, float(r.amount) * 1000.0))
        if recs:
            d2 = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume", "amount"])
            d2["date"] = pd.to_datetime(d2["date"], format="%Y%m%d")
            out[code] = d2.set_index("date").sort_index()
        time.sleep(0.12)
    return out


def _backup_fetch_sohu(codes: list, sd: str, ed: str) -> dict:
    """搜狐族 hisHq 日K(不复权原始价)。字段序: 0日期/1开/2收/5低/6高/7量(手)。
    量纲 手×100=股(qfq库口径); 停牌 volume<=0 过滤; 免token需Referer。"""
    import requests
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://q.stock.sohu.com/",
    })
    out: dict = {}
    for code in codes:
        if _to_backup_symbol(code, "sohu") is None:
            continue
        r = sess.get("https://q.stock.sohu.com/hisHq",
                     params={"code": f"cn_{code}", "start": sd, "end": ed,
                             "stat": 1, "order": "D", "period": "d"},
                     timeout=15)
        hq = ((r.json() or [{}])[0].get("hq")) or []
        recs = []
        for it in hq:
            try:
                v = float(it[7]) * 100.0            # 手 → 股
            except (ValueError, IndexError, TypeError):
                continue
            if v <= 0:
                continue                            # 停牌/平盘行过滤, 对齐 tickflow
            recs.append((str(it[0]).replace("-", ""), float(it[1]), float(it[6]),
                         float(it[5]), float(it[2]), v))
        if recs:
            df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
            out[code] = df.set_index("date").sort_index()
        time.sleep(0.12)                        # 轻间隔防限频
    return out


_BACKUP_FETCHERS = {
    "tencent": _backup_fetch_tencent,
    "baostock": _backup_fetch_baostock,
    "tushare": _backup_fetch_tushare,
    "sohu": _backup_fetch_sohu,
}


def fetch_backup_raw_map(codes: list, sd: str, ed: str) -> dict:
    """备源补位入口: 返回与 fetch_tickflow_raw_map 同构 {code: DataFrame}。

    逐通道尝试(BACKUP_SOURCE_ORDER), 首个非空命中通道生效; 全通道失败/超时
    返回 {}(调用方降级回 vipdoc/meta 现状, 不阻塞)。第三方库延迟导入,
    通道未启用时零导入开销。
    """
    universe = [c for c in codes if c.startswith(("60", "68", "00", "30"))]
    if len(codes) > BACKUP_MAX_CODES:
        log(f"[warn] 备源补位标的 {len(codes)} 只超上界, 截断至 {BACKUP_MAX_CODES} 只(防限频)")
        universe = universe[:BACKUP_MAX_CODES]
    if not universe:
        return {}
    for ch in BACKUP_SOURCE_ORDER:
        fn = _BACKUP_FETCHERS.get(ch)
        if fn is None:                          # 配置错误降级跳过, 不炸整链
            log(f"[warn] 备源通道 {ch} 未注册, 跳过")
            continue
        t0 = time.time()
        try:
            out = _run_with_timeout(lambda fn=fn: fn(universe, sd, ed), BACKUP_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 通道级降级
            log(f"[warn] 备源通道 {ch} 失败/超时: {type(e).__name__}: {str(e)[:120]}")
            continue
        got = sum(1 for v in (out or {}).values() if not v.empty)
        log(f"[qfq] 备源通道 {ch} 补位命中 {got}/{len(universe)} ({time.time() - t0:.1f}s)")
        if out:
            return out
    return {}


def _run_with_timeout(fn, timeout_s: float):
    """守护线程超时包装(同 _tickflow_batch_with_timeout 模式)。"""
    box: dict = {}

    def _run():
        try:
            box["r"] = fn()
        except BaseException as e:  # noqa: BLE001 兜底带回主线程
            box["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"备源通道超时 {timeout_s}s")
    if "e" in box:
        raise box["e"]
    return box["r"]


# ----------------------------------------------------------------------------
# 复权计算
# ----------------------------------------------------------------------------
def compute_qfq(symbol: str, raw_df: pd.DataFrame, factor_df: pd.DataFrame = None):
    """前复权计算; 返回 DataFrame(已过滤 WINDOW_START 之后)"""
    if raw_df is None or raw_df.empty:
        return None
    df = raw_df.reset_index()
    if factor_df is not None and not factor_df.empty:
        df = df.merge(factor_df.reset_index(), on="date", how="left")
        df["factor"] = df["factor"].ffill().fillna(1.0)
        # [A7 qfq 整改] 前复权因子锚点校验：最新交易日因子必须≈1.0（前复权以最新价为锚）。
        # 新浪 qfq.js 因子随除权事件重基准化；增量(ADDITIVE) run 间因子快照可能漂移，
        # 例 sz002739 8/11 除权后新浪锚点滞后，ffill 把 2017 因子 1.506 错带到 8/11-8/13，
        # 导致 close 被错缩(-32%/-41% 伪跳变)。若最新因子偏离 1.0，判定因子不可信，
        # 放弃复权(按原始价, change_pct 自动自洽)，避免写入错误复权价。
        last_factor = float(df["factor"].iloc[-1]) if len(df) else 1.0
        if abs(last_factor - 1.0) > 0.01:
            df["factor"] = 1.0
    else:
        df["factor"] = 1.0

    for col in ["open", "high", "low", "close"]:
        df[col] = df[col] / df["factor"]

    df = df.set_index("date").sort_index()
    df = df[df.index >= pd.Timestamp(WINDOW_START)]
    if df.empty:
        return None

    df["change_pct"] = df["close"].pct_change() * 100.0
    # 2026-08-24 P2修复: 阈值 21→30.5 分板——北交涨停±30%、创业板±20%、主板±10%,
    # 旧阈值 21 误杀北交涨停(30%>21→NULL, 下游因子丢数据)。30.5 仅 null 真无涨跌
    # 限制新股首日(>30%)与除权跳变; 合法涨停保留。
    df.loc[df["change_pct"].abs() > 30.5, "change_pct"] = None
    return df


# ----------------------------------------------------------------------------
# 回退: 复制原始 stock_daily_meta 行
# ----------------------------------------------------------------------------
def get_meta_conn():
    if not hasattr(_local, "conn"):
        c = sqlite3.connect(META_DB, timeout=30)
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return _local.conn


def _fallback_raw(code: str, sd: str | None = None, ed: str | None = None):
    """回退: 复制原始 stock_daily_meta 行。默认仅 [sd, ed] 窗口(ADDITIVE 补缺日), 避免把全史以原始价覆盖 qfq 库。"""
    meta_conn = get_meta_conn()
    if sd and ed:
        rows = meta_conn.execute(
            "SELECT date,open,high,low,close,volume,amount,change_pct "
            "FROM stock_daily_meta WHERE code=? AND date>=? AND date<=?", (code, sd, ed)
        ).fetchall()
    else:
        rows = meta_conn.execute(
            "SELECT date,open,high,low,close,volume,amount,change_pct "
            "FROM stock_daily_meta WHERE code=?", (code,)
        ).fetchall()
    out = []
    for (d, o, h, l, c, v, amt, chg) in rows:
        # 20261011批1: meta库 change_pct 为分数口径(亲测 600519=0.005741), qfq 库铁律=
        # 百分数 → 回退拷贝必须 ×100。原样直拷即 920 段 2942 行 100 倍单位污染的根因;
        # 且下方 30.5 异常守卫按百分数设计, 分数口径下(|chg|<0.5)从不触发=第三症状。
        if chg is not None:
            chg = float(chg) * 100.0
        if chg is not None and abs(chg) > 30.5:  # 2026-08-24 P2: 21→30.5 分板(同 compute_qfq)
            chg = None
        out.append((str(d).replace("-", ""), o, h, l, c, v, amt, chg))
    return out


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------
def worker(task, source: str = "vipdoc", raw_map: dict | None = None):
    code, sd, ed = task
    try:
        if source == "tickflow" and raw_map is not None:
            raw_df = raw_map.get(code)
            if raw_df is None or raw_df.empty:
                raw_df = read_tdx_day(code)   # tickflow 无该股(如新股未收录) -> 回退 vipdoc
        else:
            raw_df = read_tdx_day(code)
        if raw_df is None or raw_df.empty:
            # 原始源缺该股 -> 仅回退 [sd,ed] 窗口的 meta 原始行
            rows = _fallback_raw(code, sd, ed)
            return (code, "fallback", rows) if rows else (code, "skip", None)

        factor_df = fetch_factor(code)
        # 2026-08-24 P1修复(数据真相铁律): 区分"新浪无qfq.js段(原始价即正确复权价)"
        # 与"新浪拉取失败(网络/限流)"。前者 factor_df is None 且 code∈无因子段 → 走原始价(ok);
        # 后者 factor_df is None 且 code∉无因子段 → 禁止走 factor=1.0 固化(会永久污染回测正源,
        # 且 qmax 推进后增量窗口不再回填), 返回 "failed" 让本次不写不推进, 下次重试。
        # 2026-08-25: 无因子段 = 北交(4/8/920) + 科创CDR(689), 见 fetch_sina_qfq docstring。
        # 20261011批5尾: "EMPTY"哨兵=HTTP200空data(真无因子段, fetch侧分流)——isinstance判
        # 而非==防DataFrame按元素比较歧义; 此类走factor=1.0原始价(ok)+响亮留痕, 不再
        # 混入failed(否则任何新无因子股=整链每晚rc=1狼来了, 689硬编码即此类创可贴)。
        _factor_empty = isinstance(factor_df, str)
        if _factor_empty:
            log(f"[warn] {code} 新浪qfq空data段→factor=1.0原始价(真无因子, 非网络失败)")
            factor_df = None
        _no_sina_qfq = code.startswith(("4", "8", "920", "689")) or _factor_empty
        if factor_df is None and not _no_sina_qfq:
            return (code, "failed", "sina qfq 拉取失败(网络/限流)——不固化原始价, 下次重试")
        qfq_df = compute_qfq(code, raw_df, factor_df)
        if qfq_df is None or qfq_df.empty:
            rows = _fallback_raw(code, sd, ed)
            return (code, "fallback", rows) if rows else (code, "skip", None)

        # 2026-08-26 防护（qfq 污染教训: tickflow 源 8/11/8/24 对 22 只返回错误原始价直写污染, 见
        # reports/QFQ_CORRUPTION_DIAGNOSIS_20260825.md）:
        # 前复权铁律 = 最新日 qfq ≈ vipdoc 同日原始价（最新因子=1.0）。
        # 写库前用 vipdoc 对账, 偏离>5% → fail-closed 不写（保留现状, 下次重试）, 防 tickflow 污染直写。
        try:
            _vd = read_tdx_day(code)
            if _vd is not None:
                _vmp = {d.strftime("%Y%m%d"): r["close"] for d, r in _vd.iterrows()}
                _te = qfq_df.index[qfq_df.index <= pd.Timestamp(ed)]
                if len(_te):
                    _ed = _te[-1].strftime("%Y%m%d")
                    _qc = float(qfq_df.loc[_te[-1], "close"])
                    if _ed in _vmp and abs(_qc / _vmp[_ed] - 1) > 0.05:
                        return (code, "failed",
                                f"最新日锚点校验失败: qfq[{_ed}]={_qc:.3f} vs vipdoc={_vmp[_ed]:.3f}"
                                f"（偏离 {abs(_qc / _vmp[_ed] - 1):.1%}>5%，疑似 tickflow 源错误）→ 不写库")
        except Exception:
            pass   # vipdoc 读不到不拦截, 保持原行为

        # 过滤到任务区间 [sd, ed]
        qfq_df = qfq_df[(qfq_df.index >= pd.Timestamp(sd)) & (qfq_df.index <= pd.Timestamp(ed))]
        if qfq_df.empty:
            # 窗口内无交易日(周末/非交易区间): 已是最新, 写入空 -> skip, 禁止覆盖历史
            return (code, "skip", None)

        rows = []
        for d, row in qfq_df.iterrows():
            rows.append((
                d.strftime("%Y%m%d"),
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row["amount"], row["change_pct"]
            ))
        return (code, "ok", rows)
    except Exception as e:
        return (code, "error", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, default=0, help="从 universe 的第 N 只开始处理")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只 (烟测)")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不发请求")
    ap.add_argument("--workers", type=int, default=WORKERS, help="并发 worker 数")
    ap.add_argument("--source", choices=["tickflow", "vipdoc"], default="tickflow",
                    help="日K底层数据源: tickflow(默认, free-api 免注册) / vipdoc(通达信本地兜底)")
    ap.add_argument("--full", action="store_true",
                    help="全量基准重建: 全宇宙强制 sd=WINDOW_START(20261011批1; 新浪因子全史重基准"
                         "后历史行唯一重写入口, 亦可作非交易日定期刷新; 量大请配 --source vipdoc)")
    args = ap.parse_args()
    nw = args.workers
    source = args.source

    meta_conn = sqlite3.connect(META_DB, timeout=30)
    meta_conn.execute("PRAGMA busy_timeout=30000")

    qfq_conn = sqlite3.connect(QFQ_DB, timeout=60)
    qfq_conn.execute("PRAGMA journal_mode=WAL")
    qfq_conn.execute("PRAGMA busy_timeout=60000")
    qfq_conn.execute("PRAGMA synchronous=NORMAL")
    ensure_table(qfq_conn)
    log(f"qfq 写入库: {QFQ_DB}")
    log(f"并发 worker 数 = {nw}")

    universe = get_universe(meta_conn)
    log(f"universe size = {len(universe)}")

    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    start_thr = (datetime.strptime(WINDOW_START, "%Y%m%d") + timedelta(days=60)).strftime("%Y%m%d")

    tasks = []
    skipped = 0
    if args.offset:
        universe = universe[args.offset:]
    if args.limit:
        universe = universe[: args.limit]

    for code in universe:
        if args.full:
            # 20261011批1: 增量"已到目标即永久skip"+"历史行永不按新基准重写"的结构缺陷
            # (除权重基准化后新旧行跨基准混杂, 主库实测主创板117行不自洽)由此入口闭环。
            tasks.append((code, WINDOW_START, today))
            continue
        qmin, qmax, qcnt, mmin = get_coverage(qfq_conn, meta_conn, code)
        reached_start = (qmin is not None and qmin <= start_thr)
        near_ipo = (qmin is not None and mmin is not None and
                    qmin >= (datetime.strptime(mmin, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d"))
        if qmax and qmax >= today and (reached_start or near_ipo):
            skipped += 1
            continue
        if qmax:
            sd = (datetime.strptime(qmax, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        else:
            sd = WINDOW_START
        tasks.append((code, sd, today))

    log(f"待处理任务 = {len(tasks)} (skip={skipped})")
    meta_conn.close()

    if args.dry_run:
        for code, sd, ed in tasks[:20]:
            log(f"[dry] {code}: {sd} -> {ed}")
        log(f"[dry] 共 {len(tasks)} 只待处理")
        qfq_conn.close()
        return

    # TickFlow 源: 预拉取任务区间不复权日K(统一窗口 = 最小时点起), 失败标的缺省由 worker 回退 vipdoc
    raw_map: dict | None = None
    if source == "tickflow" and tasks:
        all_sd = min(t[1] for t in tasks)
        all_ed = max(t[2] for t in tasks)
        log(f"tickflow 预拉取 {len(tasks)} 只 (窗口 {all_sd}~{all_ed}) ...")
        raw_map = fetch_tickflow_raw_map([t[0] for t in tasks], all_sd, all_ed)
        got = sum(1 for c in raw_map if not raw_map[c].empty)
        log(f"tickflow 预拉取完成: 命中 {got}/{len(tasks)} 只, 缺省 {len(tasks) - got} 只(将回退 vipdoc)")

    done = 0
    fallback_cnt = 0
    skip_cnt = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=nw) as ex:
        futures = {ex.submit(worker, t, source, raw_map): t for t in tasks}
        for fut in as_completed(futures):
            code, status, payload = fut.result()
            try:
                if status == "failed":
                    failed += 1
                elif status == "skip":
                    skip_cnt += 1
                elif status == "fallback":
                    _write_rows(qfq_conn, code, payload)
                    fallback_cnt += 1
                    done += 1
                elif status == "ok":
                    _write_rows(qfq_conn, code, payload)
                    done += 1
                else:
                    failed += 1
                    log(f"[error] {code}: {payload}")
            except Exception as e:
                failed += 1
                log(f"[write-error] {code}: {e}")
            if (done + failed) % 50 == 0:
                log(f"progress: done={done} skipped={skip_cnt} fallback={fallback_cnt} failed={failed} last={code}")

    qfq_conn.close()
    log(f"FINAL done={done} skipped={skip_cnt} fallback={fallback_cnt} failed={failed}")


if __name__ == "__main__":
    main()
