# -*- coding: utf-8 -*-
"""
daily_chain_update.py — 屠龙数据链路统一更新脚本（一键跑全链）
================================================================
用法:
    python daily_chain_update.py                     # 自动检测通达信最新交易日
    python daily_chain_update.py --date 20260812     # 指定目标交易日
    python daily_chain_update.py --phase qfq         # 只跑指定环节(all|qfq|daily_basic|aux)
    python daily_chain_update.py --dry-run           # 试运行（只打印计划）

环节:
    qfq         通达信本地日线 + 新浪 qfq.js 复权因子 -> dragon_quant_qfq.db
                (腾讯 fqkline 接口 501 已废, 此路径为唯一正源, 2026-08-11 实测)
    daily_basic 基本面日频 (baostock peTTM/pbMRQ/psTTM/turn) -> fundamental_data.db
    aux         辅助表: feature_market_sentiment / northbound_daily /
                feature_industry_momentum / industry_kline_ths
    main        主链路 tulong_daily_update.py Phase1-9 (stock_daily_raw/因子/信号/北向)
    feature     feature_daily_v2 特征表增量构建 (build_feature_v2.py)
                依赖 stock_daily_raw 已含目标日; 计算滚动特征需前150天历史
    v43         v43信号增量刷新 (LightGBM推理 feature_daily_v2 -> stock_signals_v43)
                依赖 feature_daily_v2 已含目标日特征; 缺失则安全跳过
    smc         S2×ROE>10% SSL流动性扫荡信号扫描 (smartmoneyconcepts严格定义+PIT ROE>10%)
                依赖 qfq 日线已到 target; 产出 reports/smc_s2_roe_signal_{date}.csv 供白名单加载
                实验源 smc_s2_roe (G12降半仓), 2026-08-23 阶段1落地

依赖: 通达信 vipdoc 已更新到目标日; 脚本位置 D:/zcodesj
作者: 知微 | 日期: 2026-08-11 (由 8/8、8/10、8/11 三次手动回补沉淀; 8/23 加 smc 环节)
"""
import argparse
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

# 2026-09-10: schtasks/cmd 环境下 stdout 为管道时编码回落 cp936,
# 本脚本的 ⛔/✗/⚠️/✅ 打印会 UnicodeEncodeError 崩链(9/8、9/9 两晚 23:30 信号管线实锤)。
# 源头加固: 任何入口、任何环境统一 UTF-8 + errors=replace 兜底。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─────────────────────────── 路径常量 ───────────────────────────
META_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")
MAIN_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
QFQ_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
FUND_DB = Path(r"D:\workbuddyspace\databases\fundamental_data.db")
SWDB = Path(r"D:\workbuddyspace\databases\sw_industry.db")
LOG_DIR = Path(r"D:\workbuddyspace\logs")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    # 2026-09-19 双写落盘: automation/schtasks 会话的 stdout 不留痕, 9/18 夜 22:00 原链
    # 失败原因无从查起(排查盲区)。任何入口跑链都留持久日志; 编码错不阻断主流程。
    try:
        with open(LOG_DIR / f"daily_chain_{time.strftime('%Y%m%d')}.log", "a", encoding="utf-8") as _lf:
            _lf.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# 2026-10-14 根治令: 链耗时台账(环节级+aux子件级), 链尾写 data/chain_timing_latest.json,
# health_check 预算告警消费——新挂件成本立即可见, 防 aux 类慢性膨胀无人察觉。
_AUX_TIMING: dict = {}
_PHASE_TIMING: dict = {}


# ─────────────────────────── 单实例锁 (2026-09-19; v2双要素 2026-10-08) ───────────────────────────
# 背景: 9/18 夜 22:00 链挂死后, 会话连环重启(23:26 --date / 23:37 无参 / 00:09 信号生产),
# 三实例互相持 sqlite 写锁嵌套僵死十五小时(daily_basic 卡 2100/4138)。本锁在启动口
# 拒绝第二实例, 防嵌套; 陈旧锁(pid 已死)自动覆盖, OpenProcess 异常 fail-open 不阻断。
# v2(2026-10-08): 锁内容升级"pid|创建时刻epoch"。10/8夜实锤v1漏洞: 孤儿锁pid被Windows
# 复用给无关进程(node), 活性检查误判「在跑」, 22:00合法链被假活误拒。创建时刻比对=复用免疫。
LOCK_FILE = Path(r"D:\zcodesj\data\_daily_chain_update.lock")
STALE_LOCK_SEC = 90 * 60  # 挂死判定阈值: >最长单环节(daily_basic~40min), 环节touch保活防误杀


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False  # 检测失败按死进程处理(fail-open 覆盖锁继续)


def _pid_create_time(pid: int) -> float | None:
    """进程创建时刻(epoch秒, GetProcessTimes)。取不到(权限/已退出/API异常)返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            c, e = wintypes.FILETIME(), wintypes.FILETIME()
            k, u = wintypes.FILETIME(), wintypes.FILETIME()
            if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                       ctypes.byref(k), ctypes.byref(u)):
                return None
            # FILETIME(1601-01-01起, 100ns) → epoch秒
            return (c.dwHighDateTime * 0x100000000 + c.dwLowDateTime
                    - 116444736000000000) / 10_000_000.0
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _own_lock_content() -> str:
    """本进程的锁内容(v2)="pid|创建时刻epoch"; 创建时刻取不到时降级v1裸pid。
    acquire 写入与 release 比对共用此单一真源, 防两处格式漂移。"""
    ct = _pid_create_time(os.getpid())
    return f"{os.getpid()}|{ct:.6f}" if ct is not None else str(os.getpid())


def _pid_reused(old_pid: int, old_ct) -> bool:
    """v2 复用识别: 锁记创建时刻 vs 该pid现进程实际创建时刻, 不符=pid已被无关进程复用
    (10/8夜实锤: 孤儿锁pid被node复用, 22:00合法链被假活误拒)。旧格式/取不到时刻
    fail-open 按 v1 语义不判复用。"""
    if old_ct is None:
        return False
    cur = _pid_create_time(old_pid)
    if cur is None:
        return False
    if abs(cur - old_ct) > 1.0:
        log(f"单实例锁: pid={old_pid} 活着但创建时刻不符(锁记{old_ct:.0f}≠现进程{cur:.0f}, "
            f"pid已被无关进程复用) 假活识别按陈旧锁处理")
        return True
    return False


def acquire_single_instance_lock():
    """占用单实例锁; 真持有者确认后三态: 活实例且锁龄<阈值→拒绝(exit 3); 活实例但锁龄>=阈值
    →疑似挂死强杀接管(9/18夜模式: 挂死实例会堵死一切兜底重启, 必须可接管); 死pid→陈旧锁覆盖。
    v2(2026-10-08): 先过创建时刻双要素——pid活但锁记创建时刻≠现进程创建时刻=pid复用假活,
    直接按陈旧锁覆盖(否则合法链被拒最长90分钟)。旧格式裸pid/创建时刻取不到按v1语义降级。
    锁龄阈值 90min > 最长单环节(daily_basic~40min), 健康长链不会被误杀(环节循环 touch 保活)。"""
    if LOCK_FILE.exists():
        try:
            raw = LOCK_FILE.read_text().strip()
        except OSError:
            raw = ""
        # v2格式"pid|创建时刻epoch"; v1裸pid(9/19-10/8旧锁)降级兼容
        old_pid, old_ct = 0, None
        try:
            if "|" in raw:
                _p, _, _c = raw.partition("|")
                old_pid, old_ct = int(_p or 0), float(_c) if _c else None
            else:
                old_pid = int(raw or 0)
        except ValueError:
            old_pid = 0
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
        except OSError:
            age = 0
        if old_pid and _pid_alive(old_pid) and not _pid_reused(old_pid, old_ct):
            if age >= STALE_LOCK_SEC:
                # 杀前验证目标是python进程(pid复用防护: kill后pid被无关进程复用会误杀;
                # tasklist中文系统输出GBK, 必须显式gbk解码否则readerthread崩stdout=None误判)
                _tl = subprocess.run(["tasklist", "/FI", f"PID eq {old_pid}", "/FO", "CSV"],
                                     capture_output=True, text=True,
                                     encoding="gbk", errors="replace")
                _is_py = "python" in (_tl.stdout or "").lower()
                if _is_py:
                    log(f"单实例锁: PID={old_pid} 活着但锁龄{age/60:.0f}分钟>={STALE_LOCK_SEC//60}分钟"
                        f"(环节心跳停摆) 疑似挂死python链, 强杀接管(9/18夜嵌套僵死模式)")
                    subprocess.run(["taskkill", "/F", "/PID", str(old_pid)], capture_output=True)
                    time.sleep(2)
                else:
                    log(f"单实例锁: PID={old_pid} 锁龄{age/60:.0f}分钟但非python进程(pid复用), "
                        f"只覆盖锁不强杀")
            else:
                log(f"⛔ 单实例锁: 已有实例 PID={old_pid} 在跑(锁龄{age/60:.0f}分钟<阈值), 本次拒绝启动")
                sys.exit(3)
        else:
            log(f"单实例锁: 陈旧锁(pid={old_pid} 已死或pid复用假活, 锁龄{age/60:.0f}分钟)覆盖")
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(_own_lock_content())


def touch_single_instance_lock():
    """环节心跳: 每环节开始时刷新锁 mtime, 挂死实例(无环节进展)的锁龄会持续增长被接管。"""
    try:
        os.utime(LOCK_FILE, None)
    except OSError:
        pass


def release_single_instance_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == _own_lock_content():
            LOCK_FILE.unlink()
    except OSError:
        pass


@contextmanager
def ro(db: Path) -> sqlite3.Connection:
    """只读连接（context manager，退出时自动 close() 防止句柄泄漏给子进程）"""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


# ─────────────────────────── 日期检测 ───────────────────────────
_A_PREFIXES = ("600", "601", "603", "605", "688", "689",
               "000", "001", "002", "003", "300", "301", "8", "4", "920")


def detect_tdx_latest() -> str:
    """扫描通达信 sh/sz lday 真实 A 股文件，取众数最新日期（YYYYMMDD）
    注意：lday 目录含指数/退市股/长期停牌股（如 sh000004 停在 20260529），
    必须按 A 股代码段过滤后再取众数，否则被老数据污染（2026-08-11 实测踩坑）。
    2026-08-31 迁移: 底层字节解析统一走 tdx_reader.latest_day_date。
    """
    from tdx_reader import latest_day_date

    d = latest_day_date(markets=("sh", "sz"), prefixes=_A_PREFIXES, max_samples=3000)
    if not d:
        sys.exit("错误: 无法从通达信读取任何日期")
    return str(d)


def detect_main_latest() -> str:
    with ro(MAIN_DB) as c:
        return c.execute("SELECT MAX(date) FROM stock_daily_raw").fetchone()[0]


# ─────────────────────────── 环节: qfq ───────────────────────────
QFQ_SOURCE = "tickflow"   # 日K底层数据源: tickflow(free-api, 默认) / vipdoc(通达信本地兜底)


def _vipdoc_fresh(m, target: str) -> bool:
    """vipdoc 代表票最新交易日 >= target 即新鲜(与缺省分发同一 freshness 闸)。
    None(读不到)按陈旧处理——不误判新鲜, 保断供路径走 tickflow/备源。"""
    vd = m.vipdoc_latest_date()
    return vd is not None and vd >= target


def _clean_stray_qfq_copy():
    """禁用旧路径空壳 qfq 副本守卫（2026-10-19 全量测试轮修复）: data\\dragon_quant_qfq.db
    系 10-13 翻转前旧真源路径, 白名单巡检禁入; 全量测试轮实抓 0 字节空壳（07:13 现形,
    创建者未定位）。仅删 size==0 空壳（无数据可失）, 非空文件绝不碰（零数据风险）;
    每夜 qfq 环节自愈; 白名单巡检（check_db_freshness）仍为独立检测面。

    2026-11-21 全量测试轮扩展: 根目录 dragon_quant_qfq.db 同类 0 字节空壳（11-20 02:15
    现形, 创建者未定位, 同 10-19 旧路径案）, 白名单巡检同样禁入; 按同一先例扩展自愈覆盖,
    仅删 size==0 空壳, 非空绝不碰。"""
    _strays = [
        Path(__file__).parent / "data" / "dragon_quant_qfq.db",
        Path(__file__).parent / "dragon_quant_qfq.db",  # 根目录空壳(2026-11-21 扩展)
    ]
    for _stray in _strays:
        try:
            if _stray.exists() and _stray.stat().st_size == 0:
                _stray.unlink()
                log(f"[qfq] 已清禁用旧路径空壳副本(0字节): {_stray}")
        except OSError as e:
            log(f"[qfq] 空壳副本清理失败(不阻塞链, 留白名单巡检报警): {e}")


def run_qfq(target: str):
    """日K底层源(tickflow, 兜底 vipdoc) + 新浪 qfq.js 因子, 增量写 dragon_quant_qfq.db"""
    _clean_stray_qfq_copy()
    sys.path.insert(0, str(Path(__file__).parent))
    import build_qfq_meta_tdx as m   # 复用已验证的 worker/因子/写库函数

    qfq_conn = sqlite3.connect(str(QFQ_DB), timeout=60)
    qfq_conn.execute("PRAGMA journal_mode=WAL")
    qfq_conn.execute("PRAGMA busy_timeout=60000")
    qfq_conn.execute("PRAGMA synchronous=NORMAL")
    m.ensure_table(qfq_conn)

    meta_conn = sqlite3.connect(str(META_DB), timeout=30)
    meta_conn.execute("PRAGMA busy_timeout=30000")
    universe = m.get_universe(meta_conn)
    log(f"[qfq] universe={len(universe)} target={target} source={QFQ_SOURCE}")

    start_thr = (datetime.strptime(m.WINDOW_START, "%Y%m%d") + timedelta(days=60)).strftime("%Y%m%d")
    tasks, skipped = [], 0
    for code in universe:
        qmin, qmax, qcnt, mmin = m.get_coverage(qfq_conn, meta_conn, code)
        reached_start = (qmin is not None and qmin <= start_thr)
        near_ipo = (qmin is not None and mmin is not None and
                    qmin >= (datetime.strptime(mmin, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d"))
        if qmax and qmax >= target and (reached_start or near_ipo):
            skipped += 1
            continue
        sd = (datetime.strptime(qmax, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d") if qmax else m.WINDOW_START
        tasks.append((code, sd, target))
    meta_conn.close()
    log(f"[qfq] 待处理={len(tasks)} skip={skipped}")
    if not tasks:
        qfq_conn.close()
        return 0

    # 2026-10-19 构建器窗口预检(主人「按推荐继续」②qfq预算项; 证据=10/19静态日复现
    # 待处理=1278只全被worker skip[窗口内无交易日] + 周日链qfq=1906.4s爆炸归因):
    # vipdoc 在 [sd, target] 窗口内无任何K线的任务(长期停牌/未上市段)永不可产行,
    # 却夜夜进入 tickflow 预拉取/备源网络路径——vipdoc陈旧+免费源退化夜对其整批
    # 90s超时/逐只备源=预算爆炸机制。构建期丢弃, 复牌/新K出现自然回归任务列表
    # (自愈, 无永久丢数窗口: 真缺口=窗口有K线=保留)。worker/写库/写前守卫零改动。
    _t0 = time.time()
    _kept = []
    for (_code, _sd, _ed) in tasks:
        _vd = m.read_tdx_day(_code)
        if _vd is None or _vd.empty or _vd.index[-1].strftime("%Y%m%d") < _sd:
            continue  # vipdoc 窗口内无K线 = 无可产行, 丢弃
        _kept.append((_code, _sd, _ed))
    log(f"[qfq] 窗口预检: {len(tasks)}→{len(_kept)} (无可产行任务丢弃 {len(tasks) - len(_kept)} 只, "
        f"{time.time() - _t0:.0f}s)")
    tasks = _kept
    if not tasks:
        qfq_conn.close()
        return 0

    # tickflow 源: 预拉取任务区间不复权日K(统一窗口), 缺省标的由 worker 回退 vipdoc
    raw_map: dict | None = None
    if QFQ_SOURCE == "tickflow":
        # 2026-10-12 提速(主人令"更新快一些,保证数据完整,不偷工减料"): vipdoc 已新鲜
        # (>=target)时跳过 tickflow 预拉取, 全量直接走 vipdoc worker。两源同为不复权
        # 原始价, 复权同走新浪因子链, 写库/写前守卫零改动=产出等价; 省掉 8-50 分钟
        # 网络等待(10/8/10/9 两晚超时实证)。与下方缺省分发同一 freshness 闸, 零新假设;
        # vipdoc 陈旧/缺失才走 tickflow 预拉取+备源, 断供路径不变。
        if _vipdoc_fresh(m, target):
            log(f"[qfq] vipdoc已新鲜(>={target}), 跳过tickflow预拉取, 全量走vipdoc回退(省网络等待)")
        else:
            all_sd = min(t[1] for t in tasks)
            raw_map = m.fetch_tickflow_raw_map([t[0] for t in tasks], all_sd, target)
            got = sum(1 for c in raw_map if not raw_map[c].empty)
            log(f"[qfq] tickflow 预拉取命中 {got}/{len(tasks)} (缺省回退 vipdoc)")

            # 备源第3层(2026-10-10 接线, 验收=analysis/backup_daily_probe.py):
            # 仅当 tickflow 有缺省 且 vipdoc 陈旧/缺失(断供场景) 时, 对缺省标的补位
            # 不复权原始价(复权仍走新浪因子链)。正常路径(tickflow全命中或vipdoc新鲜)
            # 零备源网络调用零开销; worker/写库/写前守卫零改动。
            if raw_map is not None:
                miss = [t[0] for t in tasks if t[0] not in raw_map or raw_map[t[0]].empty]
                if miss:
                    vd_latest = m.vipdoc_latest_date()
                    if vd_latest is None or vd_latest < target:
                        log(f"[qfq] tickflow缺省{len(miss)}只 且 vipdoc陈旧(最新={vd_latest} < {target}) → 启用备源补位")
                        backup_map = m.fetch_backup_raw_map(miss, all_sd, target)
                        hit = sum(1 for c in backup_map if not backup_map[c].empty)
                        raw_map.update(backup_map)
                        log(f"[qfq] 备源补位并入 {hit}/{len(miss)} 只(来源可追溯)")
                    else:
                        log(f"[qfq] tickflow缺省{len(miss)}只, vipdoc新鲜({vd_latest}) → 按现状回退 vipdoc, 备源不启用")

    done = fallback = skip = failed = blocked = 0
    # 因子事件缓存(2026-10-13 立项, 主人令"现在干"; 构建期默认关闸对拍):
    # pro.dividend(ex_date=target) 一口拉当日除权名单(实测10/12=16只), 非事件票
    # 复用本地缓存事件表(无新除权→事件表不变=产出逐位一致); 名单获取失败→
    # hint=None 全量取新=旧行为。三层安全网=名单源+写前锚点校验+3天TTL。
    # QFQ_FACTOR_CACHE=0 可停用; 10/13 凌晨双轮全量对拍 PASS 后默认置 "1"。
    if os.environ.get("QFQ_FACTOR_CACHE", "1") != "0":
        try:
            import tushare as _ts_fc
            _tok_fc = os.environ.get("TUSHARE_TOKEN", "").strip()
            if not _tok_fc and Path(r"D:\zcodesj\config\tushare_token.txt").is_file():
                _tok_fc = Path(r"D:\zcodesj\config\tushare_token.txt").read_text(encoding="utf-8").strip()
            _pro_fc = _ts_fc.pro_api(_tok_fc)
            _ddf = _pro_fc.dividend(ex_date=target, fields="ts_code,div_proc,ex_date")
            _ev = {r.ts_code.split(".")[0] for r in _ddf.itertuples() if r.div_proc == "实施"}
            m.set_factor_event_hint(_ev)
            log(f"[qfq] 因子缓存启用: 当日除权票{len(_ev)}只(名单源pro.dividend), 其余复用缓存事件表")
        except Exception as e:
            m.set_factor_event_hint(None)
            log(f"[qfq] 因子缓存事件名单获取失败({type(e).__name__}: {str(e)[:100]}), 本次全量取新")
    else:
        m.set_factor_event_hint(None)
    # 2026-10-13 提速(实测: vipdoc读6ms vs 新浪因子423ms/只=98%耗时; 新浪对16并发
    # 零失败, 50只1.6s)。worker 数从8提至16(env可调): 因子HTTP为主, 本地读/写库
    # (WAL+busy_timeout)在16线无争用。默认保守16(~31req/s), 允许 QFQ_WORKERS=24。
    _qf_w = max(1, int(os.environ.get("QFQ_WORKERS", "16")))
    log(f"[qfq] 工作线程={_qf_w}")
    with m.ThreadPoolExecutor(max_workers=_qf_w) as ex:
        futs = {ex.submit(m.worker, t, QFQ_SOURCE, raw_map): t for t in tasks}
        for fut in m.as_completed(futs):
            code, status, payload = fut.result()
            try:
                # 2026-08-24 P1修复: worker 异常实际返回 status="error"，旧代码只判 "failed"
                # → 错误股落入 else 分支把异常字符串当数据行传 _write_rows（靠二次异常兜底
                # 计 failed，异常详情全部静默丢失）。现两者都计入 failed 并打印详情。
                if status in ("failed", "error"):
                    failed += 1
                    if status == "error":
                        log(f"[qfq] {code} worker异常: {str(payload)[:160]}")
                elif status == "skip":
                    skip += 1
                elif status == "fallback":
                    m._write_rows(qfq_conn, code, payload)
                    fallback += 1
                    done += 1
                else:
                    m._write_rows(qfq_conn, code, payload)
                    done += 1
            except m.QfqGuardBlocked as e:
                # 2026-08-26 O2 写前 vipdoc 对账 fail-closed: 量级/因子错误拒绝入库(E35收口)
                blocked += 1
                log(f"[qfq] ⛔ 写前守卫拦截 {code}: {str(e)[:200]}")
            except Exception as e:
                # 20261011批4: 写库失败细节此前完全吞掉不落日志(对比 build_qfq main 有 [write-error])
                failed += 1
                log(f"[qfq] ⚠️ {code} 写入异常: {type(e).__name__}: {str(e)[:120]}")
            if (done + failed + blocked) % 500 == 0:
                log(f"[qfq] done={done} fallback={fallback} skip={skip} failed={failed} blocked={blocked}")
    qfq_conn.close()
    log(f"[qfq] 完成 done={done} fallback={fallback} skip={skip} failed={failed} blocked={blocked}")
    return failed


# ─────────────────────────── 环节: daily_basic ───────────────────────────
def daily_basic_close_guard(close: float, qfq_close) -> bool:
    """2026-09-16 新增: daily_basic 写入前 close 与 qfq 同日比价守卫。
    事件背景: 20260521-28 六日 87 只深市 000/001 段股票行被写入指数点位值(521 行,
    000001.SZ close=4077.28 恰为上证指数当日收盘, 双义码解析缺陷, 已外科修复)。
    语义: qfq 有同日价且 |close/qfq-1|>15% → 拒写(返回 False); qfq 缺失(独立跑
    daily_basic 相位时 qfq 可能未更新当日)→ 放行, 保守不误杀。"""
    if not qfq_close or qfq_close <= 0:
        return True
    return abs(close / qfq_close - 1.0) <= 0.15


def tencent_daily_basic_rows(items, target: str):
    """2026-10-14 根治令: 腾讯批量行情兜底路径(tushare 限频/失败时的二线, 替代
    baostock 逐股 13-18min → ~40s)。baostock 保留为第三线不变。

    依据: analysis/ab_tencent_daily_basic_full_20261014.py 全市场 A/B 实测(4596 只,
    20261013 盘后快照 vs tushare 快路径库行): close/量比/总股本/总市值 100% 吻合,
    pe_ttm 中位相对差 0.005%(>1% 仅 0.25%, 两位舍入级)。已知边界: pb/dv_ttm 存在
    披露时点差(p95 3.4%/2.1%, 主因三季报披露节奏与微净资产股数值不稳), 次日 tushare
    快路径 INSERT OR REPLACE 自愈; ps/ps_ttm/dv_ratio/turnover_rate_f/free_share
    腾讯无对应口径 → 0(未采集语义, 同 baostock 夜)。字段位次以探针实测为准, 禁止凭
    记忆调整: idx3=收盘 idx38=换手(%) idx39=PE(TTM) idx44/45=流通/总市值(亿元)
    idx46=PB idx49=量比 idx64=股息率(TTM) idx72/73=流通/总股本(股)。

    返回 (rows, covered): rows=18 列元组(列序与 INSERT OR REPLACE 一致);
    covered=已覆盖 ts_code 集合; close<=0(停牌/无行情)不写不记 → 落 baostock。
    close 对 qfq 守卫由调用方施加(本函数不持有 guard_reject 台账)。
    """
    import urllib.request

    def _fnum(v):
        try:
            f = float(v)
            return f if f == f else 0.0
        except (TypeError, ValueError):
            return 0.0

    rows, covered = [], set()
    for i in range(0, len(items), 60):
        chunk = items[i:i + 60]
        syms = [("sh" if ts.endswith(".SH") else "sz") + code
                for code, ts, _t, _f in chunk]
        req = urllib.request.Request("http://qt.gtimg.cn/q=" + ",".join(syms),
                                     headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read().decode("gbk", errors="ignore")
        except Exception:
            time.sleep(0.5)
            continue  # 单批失败 fail-soft: 该批标的落 baostock, 不拖垮整层
        by_sym = {}
        for line in data.strip().split(";"):
            line = line.strip()
            if "~" in line:
                k, _, val = line.partition("=")
                by_sym[k.replace("v_", "").strip()] = val.strip('"').split("~")
        for (code, ts_code, tot_sh, flt_sh), sym in zip(chunk, syms):
            v = by_sym.get(sym)
            if not v or len(v) < 74:
                continue
            close = _fnum(v[3])
            if close <= 0:
                continue
            tot_tx = _fnum(v[73]) / 1e4
            flt_tx = _fnum(v[72]) / 1e4
            rows.append((ts_code, target, close,
                         _fnum(v[38]), 0.0, _fnum(v[49]),
                         0.0, _fnum(v[39]), _fnum(v[46]), 0.0, 0.0,
                         0.0, _fnum(v[64]),
                         tot_tx or tot_sh, flt_tx or flt_sh, 0.0,
                         _fnum(v[45]) * 1e4 or close * (tot_tx or tot_sh),
                         _fnum(v[44]) * 1e4 or close * (flt_tx or flt_sh)))
            covered.add(ts_code)
        time.sleep(0.12)
    return rows, covered


def run_daily_basic(target: str):
    """daily_basic 日更三级路径: tushare按日批量主路径(1次调用) → 腾讯批量兜底
    (2026-10-14 根治令) → baostock逐票补齐/整体回退(2026-10-12 提速, 槽位口径与
    backfill_daily_basic_20260808/10/11 一致)"""
    import baostock as bs
    CKPT = LOG_DIR / f"backfill_ckpt_daily_basic_{target}.txt"
    SKIP = LOG_DIR / f"backfill_skip_daily_basic_{target}.txt"
    bsdate = f"{target[:4]}-{target[4:6]}-{target[6:]}"

    # 2026-09-16 守卫数据: 预载 qfq 同日 close(all 相位下 qfq 先于 daily_basic 必有
    # 当日; 独立相位可能缺当日 → 守卫自动放行, 不误杀)
    with ro(QFQ_DB) as q:
        qfq_close_map = {r[0]: r[1] for r in q.execute(
            "SELECT code, close FROM stock_daily_meta_qfq WHERE date=?", (target,)).fetchall()}
    guard_reject = []

    with ro(META_DB) as m:
        codes = [r[0] for r in m.execute(
            "SELECT DISTINCT code FROM stock_daily_meta WHERE date=?", (target,)).fetchall()]
    codes = [c for c in codes if not c.startswith(("688", "8", "9", "4"))]
    with ro(FUND_DB) as f:
        shares = {}
        for ts, tot, flt in f.execute(
                "SELECT ts_code, total_share, float_share FROM daily_basic "
                "WHERE (ts_code, trade_date) IN (SELECT ts_code, MAX(trade_date) FROM daily_basic GROUP BY ts_code)"):
            if tot and tot > 0:
                shares[ts] = (tot, flt or 0.0)
    failover = sorted(c for c in codes
                      if shares.get(f"{c}.SH" if c.startswith("6") else f"{c}.SZ", (0,))[0] <= 0)
    if failover:
        log(f"[daily_basic] {len(failover)} 只无历史股本，尝试腾讯接口补足...")
        try:
            import urllib.request
            syms = []
            for c in failover:
                syms.append(("sh" if c.startswith("6") else "sz") + c)
            got = 0
            for i in range(0, len(syms), 60):
                url = "http://qt.gtimg.cn/q=" + ",".join(syms[i:i+60])
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        data = r.read().decode("gbk", errors="ignore")
                except Exception:
                    continue
                for line in data.strip().split(";"):
                    line = line.strip()
                    if "~" not in line:
                        continue
                    key = line.split("=", 1)[0].replace("v_", "").strip()
                    vals = line.split("=", 1)[1].strip('"').split("~")
                    if len(vals) < 80:
                        continue
                    ts_key = key[2:] + (".SH" if key.startswith("sh") else ".SZ")
                    try:
                        tot_sh_gu, flt_sh_gu = float(vals[73]), float(vals[72])
                        if tot_sh_gu > 0:
                            shares[ts_key] = (tot_sh_gu / 1e4, flt_sh_gu / 1e4)  # 股→万股
                            got += 1
                    except (ValueError, IndexError):
                        continue
            log(f"[daily_basic] 腾讯补股本: 成功 {got}/{len(failover)}")
        except Exception as e:
            log(f"[daily_basic] 腾讯接口异常: {e}，无股本股票保持 0")
    uni = [(c, f"{c}.SH" if c.startswith("6") else f"{c}.SZ", *shares.get(f"{c}.SH" if c.startswith("6") else f"{c}.SZ", (0.0, 0.0)))
           for c in sorted(codes)]
    log(f"[daily_basic] 股票池 {len(uni)} 只 target={target}")

    done_set = set(CKPT.read_text(encoding="utf-8").split()) if CKPT.exists() else set()
    todo = [x for x in uni if x[1] not in done_set]
    log(f"[daily_basic] 待回补 {len(todo)}（已完成 {len(done_set)}）")
    if not todo:
        return 0

    n = 0
    # ── 提速主路径(2026-10-12, 主人令"更新快些,保证数据完整,不偷工减料"): tushare
    # daily_basic 按日一口拉全市场(1次调用替代4596次baostock逐票, 18分钟~小时级→秒级;
    # 本token限频1次/小时, 代码库内此调用独占无竞争)。完整性三层保底: ①仅写todo域内票
    # +同close守卫+同槽位语义(pe_ttm列存TTM/ps_ttm列存TTM, pe列恒0——10/13 A/B实数据
    # 勘误: baostock实际写入位次就是pe_ttm列, pe列全史恒0; 修正早前"pe列存TTM"的错读,
    # 本实现按位次镜像本就正确) ②tushare缺口票全部落入下方
    # baostock逐票补齐(重试/重连/SKIP/CKPT机制原样, "当日数据未生成不锁死"语义保留)
    # ③tushare调用失败整体回退baostock全量路径=旧行为。产出同表同列同单位。
    remaining = todo
    try:
        # 启用闸: 字段级A/B对账已PASS(10/13晚 40样本: 34行全列逐位一致+6行仅baostock
        # 缺pe_ttm写0而tushare有真值=严格更优+零反向错误; 零配额仲裁6/6四位精确
        # baostock peTTM≡tushare pe_ttm)——默认启用, DAILY_BASIC_TUSHARE=0 可停用。
        if os.environ.get("DAILY_BASIC_TUSHARE", "1") == "0":
            raise RuntimeError("主路径已停用(DAILY_BASIC_TUSHARE=0)")
        socket.setdefaulttimeout(30)
        _token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not _token and Path(r"D:\zcodesj\config\tushare_token.txt").is_file():
            _token = Path(r"D:\zcodesj\config\tushare_token.txt").read_text(encoding="utf-8").strip()
        if not _token:
            raise RuntimeError("TUSHARE_TOKEN 缺失(env→config/tushare_token.txt)")

        def _num(v):
            try:
                f = float(v)
                return f if f == f else 0.0  # NaN/None/空串归零
            except (TypeError, ValueError):
                return 0.0

        import tushare as _ts
        pro = _ts.pro_api(_token)
        tdf = pro.daily_basic(
            trade_date=target,
            fields="ts_code,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,"
                   "ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv")
        tmap = {r.ts_code: r for r in tdf.itertuples()}
        _rows = []
        covered = set()
        for code, ts_code, tot_sh, flt_sh in todo:
            r = tmap.get(ts_code)
            close = _num(r.close) if r is not None else 0.0
            if close <= 0:
                continue  # 当日行未生成/缺口 → baostock 补齐(不记 CKPT, 重跑自愈, 语义同旧)
            if not daily_basic_close_guard(close, qfq_close_map.get(code)):
                guard_reject.append(f"{ts_code}@{target} close={close} qfq={qfq_close_map.get(code)}")
                continue
            # 2026-10-13 死列填活(主人令; 普查实证 pe/ps/dv/turnover_rate_f/volume_ratio/
            # free_share 列全史恒0且零消费方读取, 填真值=零行为变更): tushare 一次调用
            # 同源同批全字段。baostock 补齐路径无这些字段维持0(混布语义: 0=未采集, 静态
            # PE/PS 不会有真0)。历史行不回填(配额5次/天), 前向生效。
            _rows.append((ts_code, target, close,
                          _num(r.turnover_rate), _num(r.turnover_rate_f), _num(r.volume_ratio),
                          _num(r.pe), _num(r.pe_ttm), _num(r.pb), _num(r.ps), _num(r.ps_ttm),
                          _num(r.dv_ratio), _num(r.dv_ttm),
                          _num(r.total_share) or tot_sh, _num(r.float_share) or flt_sh,
                          _num(r.free_share),
                          _num(r.total_mv) or close * (_num(r.total_share) or tot_sh),
                          _num(r.circ_mv) or close * (_num(r.float_share) or flt_sh)))
            covered.add(ts_code)
        if _rows:
            _con = sqlite3.connect(str(FUND_DB), timeout=60)
            try:
                _con.execute("PRAGMA journal_mode=WAL")
                _con.execute("PRAGMA busy_timeout=60000")
                _con.executemany(
                    "INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _rows)
                _con.commit()
            finally:
                _con.close()
            n += len(_rows)
            with CKPT.open("a", encoding="utf-8") as f:
                f.write("\n".join(sorted(covered)) + "\n")
        remaining = [x for x in todo if x[1] not in covered]
        log(f"[daily_basic] tushare按日批量: 写{n}/{len(todo)}, 缺口{len(remaining)}只转baostock补齐")
    except Exception as e:
        log(f"[daily_basic] tushare批量不可用({type(e).__name__}: {str(e)[:120]}), 回退baostock全量路径")
        remaining = todo

    # ── 2026-10-14 根治令二线: 腾讯批量行情兜底(77 请求 ~40s 替代 baostock 逐股
    # 13-18min)。字段口径/A-B实测边界见 tencent_daily_basic_rows docstring。
    # DAILY_BASIC_TENCENT=0 可停用(停用后直接落 baostock=旧行为)。
    if remaining and os.environ.get("DAILY_BASIC_TENCENT", "1") != "0":
        try:
            t_rows, t_covered = tencent_daily_basic_rows(remaining, target)
            kept, kept_codes = [], set()
            for row in t_rows:
                code6 = row[0][:6]
                if not daily_basic_close_guard(row[2], qfq_close_map.get(code6)):
                    guard_reject.append(f"{row[0]}@{target} close={row[2]} qfq={qfq_close_map.get(code6)}")
                    continue
                kept.append(row)
                kept_codes.add(row[0])
            if kept:
                _con = sqlite3.connect(str(FUND_DB), timeout=60)
                try:
                    _con.execute("PRAGMA journal_mode=WAL")
                    _con.execute("PRAGMA busy_timeout=60000")
                    _con.executemany(
                        "INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        kept)
                    _con.commit()
                finally:
                    _con.close()
                n += len(kept)
                with CKPT.open("a", encoding="utf-8") as f:
                    f.write("\n".join(sorted(kept_codes)) + "\n")
            remaining = [x for x in remaining if x[1] not in kept_codes]
            log(f"[daily_basic] 腾讯批量兜底: 写{len(kept)}/{len(todo)}, 剩余{len(remaining)}只转baostock")
        except Exception as e:
            log(f"[daily_basic] 腾讯兜底异常({type(e).__name__}: {str(e)[:120]}), 剩余转baostock")

    # 2026-08-24 修复: baostock 为单例共享 socket, query_history_k_data_plus 无内置超时
    # (实测单票可阻塞挂死 ~10 分钟阻塞全链)。baostock socket 不带显式超时、继承进程默认,
    # 登录前设 30s 超时 → 服务端停响应时 recv 抛 socket.timeout, 被下方重试循环捕获走 skip,
    # 不再无限挂起。finally 中恢复默认, 避免影响同进程后续环节(akshare/requests 等)。
    # 2026-10-23 修复(20:47 实弹整链静默死亡): ①原缺口0仍无条件 bs.login()——当晚账号被
    # baostock 服务端拉黑(10001011 黑名单用户, 本会话实弹复现), ②登录失败走 sys.exit(msg)
    # 直杀整链, msg 进 stderr 不落持久日志(9/19 已知调度会话盲区) → aux/eastmoney/
    # qlib_bin/hist/smc 全部未跑, 33分钟后接力实例才续上。改: 无缺口直接返回(不登录);
    # 登录失败降级置空 remaining(查询循环空转, finally 照常 logout/恢复 socket), 缺口
    # 留待下轮幂等补齐, 链尾 verify() fail-closed 兜底当日发现。
    if not remaining:
        return 0
    socket.setdefaulttimeout(30)
    try:
        lg = bs.login()
        if lg.error_code != "0":
            log(f"[daily_basic] baostock 登录失败({lg.error_code}: {lg.error_msg})，"
                f"剩余{len(remaining)}只本轮跳过留待下轮补齐，不阻断链")
            remaining = []

        con = sqlite3.connect(str(FUND_DB), timeout=60)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=60000")
        # 2026-09-07 修复: baostock 长会话被服务端踢(10054)后, 内层 query 重试无法自愈
        # (单例共享 socket, 实测 93 次异常零恢复空转 ~3.5h)。连续 3 只股票级失败
        # (3 次 attempt 全败)判定会话死亡 → logout+login 重开新会话续跑。
        # 重连失败的股票仍记 SKIP(无消费方, 重跑幂等自动补), 不会丢失。
        consec_fail = 0
        MAX_CONSEC_FAIL = 3
        try:
            for i, (code, ts, tot_sh, flt_sh) in enumerate(remaining, 1):
                bscode = ("sh." if code.startswith("6") else "sz.") + code
                rs = None
                for attempt in range(3):
                    try:
                        rs = bs.query_history_k_data_plus(
                            bscode, "date,close,peTTM,pbMRQ,psTTM,turn",
                            start_date=bsdate, end_date=bsdate, frequency="d", adjustflag="3")
                    except Exception:
                        rs = None
                    if rs is not None and rs.error_code == "0":
                        break
                    time.sleep(3 * (attempt + 1))
                if rs is None or rs.error_code != "0":
                    consec_fail += 1
                    with SKIP.open("a", encoding="utf-8") as f:
                        f.write(ts + "\n")
                    if consec_fail >= MAX_CONSEC_FAIL:
                        log(f"[daily_basic] 连续 {consec_fail} 只查询失败，判定会话断连，重开 baostock 会话...")
                        try:
                            bs.logout()
                        except Exception:
                            pass
                        lg = bs.login()
                        if lg.error_code != "0":
                            log(f"[daily_basic] baostock 重连失败: {lg.error_msg}，后续继续尝试")
                        consec_fail = 0
                    continue
                consec_fail = 0
                rows = []
                while rs.next():
                    d, close, pe, pb, ps, turn = rs.get_row_data()
                    if not close:
                        continue
                    close = float(close)
                    if not daily_basic_close_guard(close, qfq_close_map.get(code)):
                        guard_reject.append(f"{ts}@{d} close={close} qfq={qfq_close_map.get(code)}")
                        continue
                    rows.append((ts, d.replace("-", ""), close,
                                 float(turn) if turn else 0.0, 0.0, 0.0, 0.0,
                                 float(pe) if pe else 0.0, float(pb) if pb else 0.0,
                                 0.0, float(ps) if ps else 0.0, 0.0, 0.0,
                                 tot_sh, flt_sh, 0.0, close * tot_sh, close * flt_sh))
                if rows:
                    con.executemany(
                        "INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        rows)
                    n += len(rows)
                    # 2026-09-07 修复: query success 但空行(当日数据尚未生成)不记 CKPT,
                    # 留待下次重跑自动补(实测 baostock 晚间分批生成当日数据, 拉早了
                    # 若记 CKPT 会锁死成永久缺口, 当晚实弹 33 只创业板缺失即此因)。
                    with CKPT.open("a", encoding="utf-8") as f:
                        f.write(ts + "\n")
                if i % 100 == 0:
                    con.commit()
                    log(f"[daily_basic] {i}/{len(remaining)} 已写 {n} 行")
            con.commit()
        finally:
            bs.logout()
            con.close()
    finally:
        socket.setdefaulttimeout(None)
    if guard_reject:
        alert = LOG_DIR / "daily_basic_guard_alerts.log"
        with alert.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} " + " | ".join(guard_reject) + "\n")
        log(f"[daily_basic] ⚠️ close 守卫拒写 {len(guard_reject)} 行(详见 {alert.name})，相关股票未记 CKPT 待重跑")
    log(f"[daily_basic] 完成：共写入 {n} 行")
    return 0


# ─────────────────────────── 环节: 辅助表 ───────────────────────────
def run_sentiment(target: str):
    """feature_market_sentiment：本地全量重算缺失日（口径同 backfill_other_tables）
    2026-08-15: 涨停比例判定接入 price_limits.board_limit_pct 统一前缀口径
    (仍为涨幅近似口径 ±0.1, 无 ST 名称列不做 ST 修正, 与 Tushare 对账口径差异保持)"""
    sys.path.insert(0, str(Path(__file__).parent))
    from price_limits import board_limit_pct

    def is_a_stock(code):
        return code[:2] in ("60", "00", "30", "68") or code[0] in ("8", "9") or code.startswith("43")

    with ro(FUND_DB) as f:
        have = {r[0].replace("-", "") for r in f.execute(
            "SELECT trade_date FROM feature_market_sentiment").fetchall()}
    with ro(MAIN_DB) as m:
        dates = [str(r[0]) for r in m.execute(
            "SELECT DISTINCT date FROM stock_daily_raw WHERE date<=? ORDER BY date", (target,)).fetchall()]
    todo = [d for d in dates if d not in have]
    # 2026-09-05: 远古无前收日过滤(1990-91开市初期, MAX(date)<d 恒空 → 循环防呆跳过但不从
    # todo 剔除, 恒"待算21天/完成0天"每晚刷屏——9/4晚实证)。提前剔除并如实注明, 待算数收敛。
    _ancient = [d for d in todo if d < "20000101"]
    if _ancient:
        todo = [d for d in todo if d >= "20000101"]
        log(f"[sentiment] 远古无前收日 {len(_ancient)} 个(<2000) 永久跳过: {(_ancient[0])}~{_ancient[-1]}")
    log(f"[sentiment] 待算 {len(todo)} 天 (已覆盖 {len(have)})")
    if not todo:
        return 0

    con = sqlite3.connect(str(FUND_DB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    n = 0
    skipped_empty: list[str] = []
    # A股口径 SQL 谓词(与 is_a_stock 同口径): stock_daily_raw 混有转债/ETF/指数行,
    # 2026-09-05 修复: prev_date 曾取 raw 表全量 MAX(date)<d, 命中非A股脏日(如 20181231
    # 仅2行指数) → prev 全空 → 真交易日(20040823/20041115/20120104/20190102)被永久跳过。
    _ASTOCK_SQL = ("(substr(code,1,2) IN ('60','00','30','68') "
                   "OR substr(code,1,1) IN ('8','9') OR substr(code,1,2)='43')")
    with ro(MAIN_DB) as m:
        for d in todo:
            rows = m.execute("SELECT code, high, low, close FROM stock_daily_raw WHERE date=?",
                             (d,)).fetchall()
            prev_date = m.execute(
                f"SELECT MAX(date) FROM stock_daily_raw WHERE date<? AND {_ASTOCK_SQL}",
                (d,)).fetchone()[0]
            if not prev_date:
                skipped_empty.append(d)
                continue
            prev = dict(m.execute(
                f"SELECT code, close FROM stock_daily_raw WHERE date=? AND {_ASTOCK_SQL}",
                (prev_date,)).fetchall())
            tot = lu = ld = 0
            amp = 0.0
            for code, h, l, c in rows:
                if not is_a_stock(code):
                    continue
                pc = prev.get(code)
                if not pc or pc <= 0 or c <= 0:
                    continue
                tot += 1
                chg = (c - pc) / pc * 100
                lp = board_limit_pct(code) * 100
                if chg >= lp - 0.1:
                    lu += 1
                elif chg <= -lp + 0.1:
                    ld += 1
                amp += (h - l) / c
            if tot == 0:
                skipped_empty.append(d)
                continue
            lu_r, ld_r = lu / tot * 100, ld / tot * 100
            con.execute("INSERT OR REPLACE INTO feature_market_sentiment VALUES (?,?,?,?,?,?,?,?)",
                        (f"{d[:4]}-{d[4:6]}-{d[6:]}", lu, ld, tot, amp / tot, lu_r, ld_r, lu_r - ld_r))
            n += 1
    con.commit()
    con.close()
    log(f"[sentiment] 完成 {n} 天")
    if skipped_empty:
        log(f"[sentiment] 无A股行/无前收跳过 {len(skipped_empty)} 日(脏日如实留存): {skipped_empty[:5]}")
    return 0


def run_northbound(target: str):
    """northbound_daily 补 hs300_close/hs300_chg（net 字段按披露规则留 NULL）
    2026-08-24: ① 接入 aux 恢复文档意图; ② 清历史同日重复行污染(表无 trade_date 唯一约束,
    旧多轮 INSERT OR REPLACE 产生 34238 行/5240 distinct 日); ③ 补 000300 指数收盘与涨幅到 target。
    真实 north_net_buy 自 2024-08-19 起交易所停止实时披露, 近端恒为 NULL 属正常。"""
    # 历史去重: 同 trade_date 仅保留 rowid 最小一行(各副本字段同源一致, 任取可)
    with sqlite3.connect(str(FUND_DB), timeout=60) as con:
        con.execute("PRAGMA busy_timeout=60000")
        dup = con.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT trade_date) FROM northbound_daily").fetchone()[0]
        if dup:
            con.execute(
                "DELETE FROM northbound_daily WHERE rowid NOT IN "
                "(SELECT MIN(rowid) FROM northbound_daily GROUP BY trade_date)")
            con.commit()
            log(f"[northbound] 清理历史重复行 {dup} 行")
    with ro(META_DB) as m:
        rows = m.execute(
            "SELECT date, close FROM index_daily_meta WHERE code='000300' AND date<=? ORDER BY date",
            (target,)).fetchall()
    with ro(FUND_DB) as f:
        have = {r[0] for r in f.execute("SELECT trade_date FROM northbound_daily").fetchall()}
    # 2026-08-24: index_daily_meta.date 为 INTEGER, northbound_daily.trade_date 为 TEXT,
    # 直接比较恒不相等 → 每轮全量重插(接线前为死代码未暴露)。统一 str(d) 做成员判定。
    todo = [(str(d), c) for d, c in rows if str(d) not in have]
    log(f"[northbound] 待补 {len(todo)} 天")
    if not todo:
        return 0
    with ro(META_DB) as m:
        p = m.execute("SELECT close FROM index_daily_meta WHERE date<? ORDER BY date DESC LIMIT 1",
                      (int(todo[0][0]),)).fetchone()
    prev = p[0] if p else None
    con = sqlite3.connect(str(FUND_DB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    n = 0
    for d, close in todo:
        chg = (close - prev) / prev * 100 if prev else None
        con.execute("INSERT OR REPLACE INTO northbound_daily VALUES (?,?,?,?,?,?,?,?)",
                    (d, None, None, None, None, None, close, chg))
        prev = close
        n += 1
    con.commit()
    con.close()
    log(f"[northbound] 完成 {n} 天")
    return 0


def run_momentum(target: str):
    """feature_industry_momentum：ret20 行业等权 + 横截面分位（口径同 backfill_industry_momentum）"""
    import pandas as pd
    with ro(MAIN_DB) as m:
        ind_map = dict(m.execute(
            "SELECT code, industry FROM stock_info WHERE industry IS NOT NULL AND industry!=''").fetchall())
    log(f"[momentum] 行业映射 {len(ind_map)} 只")
    lookback = (datetime.strptime(target, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
    with ro(META_DB) as mt:
        px = pd.read_sql(
            "SELECT code, date, close FROM stock_daily_meta WHERE date>=? AND date<=?",
            mt, params=(lookback, target))
    px = px[px["code"].isin(ind_map.keys())]
    panel = px.pivot_table(index="date", columns="code", values="close").sort_index()
    panel.index = panel.index.astype(str)   # 修复bug: date列为整数，统一转字符串避免loc索引KeyError
    # 🔴债务登记(2026-10-14): pandas FutureWarning(fill_method默认pad将移除)如实知晓但**不修**——
    # 改 fill_method=None 会让停牌日从"前值填充"变"NaN传播", 20日动量口径对停牌股发生数值变化,
    # 属口径变更须A/B对拍+消费方(industry_momentum表读者)确认后另立任务, 禁止顺手改口径。
    ret20 = panel.pct_change(20) * 100

    with ro(FUND_DB) as f:
        have = {r[0] for r in f.execute(
            "SELECT trade_date FROM feature_industry_momentum").fetchall()}
    dates = [d for d in panel.index if d not in have and d <= target]
    log(f"[momentum] 面板 {panel.shape[0]} 天×{panel.shape[1]} 只, 待算 {len(dates)} 天")
    if not dates:
        return 0

    def _suffix(code):
        # 6/9开头沪市, 8/4开头北交所, 其余深市（与历史 ts_code 后缀口径一致）
        return (code + ".SH") if code[:1] in ("6", "9") else ((code + ".BJ") if code[:1] in ("8", "4") else (code + ".SZ"))

    con = sqlite3.connect(str(FUND_DB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    total = 0
    for d in dates:
        row = ret20.loc[d].dropna()
        if row.empty:
            continue
        df = row.rename("ret20").reset_index()
        if list(df.columns) != ["code", "ret20"]:
            df.columns = ["code", "ret20"]
        df["industry"] = df["code"].map(ind_map)
        df = df.dropna(subset=["industry"])
        # 口径: industry_ret20=行业个股ret20等权均值, industry_momentum=该行业ret20的横截面分位
        # (横截面=当日全部行业), 广播到每个成员股（与 feature_industry_momentum 历史口径一致）
        g = df.groupby("industry")["ret20"].mean()
        ranks = g.rank(pct=True)
        df["industry_ret20"] = df["industry"].map(g)
        df["industry_momentum"] = df["industry"].map(ranks)
        recs = [(_suffix(r["code"]), d, r["industry"], float(r["industry_ret20"]), float(r["industry_momentum"]))
                for _, r in df.iterrows()]
        con.executemany("INSERT OR REPLACE INTO feature_industry_momentum VALUES (?,?,?,?,?)", recs)
        total += len(recs)
        con.commit()
    con.close()
    log(f"[momentum] 完成 {len(dates)} 天 × 个股，共 {total} 行")
    return 0


def run_industry_ths(target: str):
    """industry_kline_ths：akshare 同花顺行业K线增量（唯一正源，东财源已废）"""
    import akshare as ak
    with ro(SWDB) as s:
        names = [r[0] for r in s.execute("SELECT name FROM industry_names").fetchall()]
    with ro(SWDB) as s:
        have = {r[0] for r in s.execute(
            "SELECT MAX(trade_date) FROM industry_kline_ths GROUP BY industry_name").fetchall()}
    start = min(have) if have else (datetime.strptime(target, "%Y%m%d") - timedelta(days=5)).strftime("%Y-%m-%d")
    log(f"[ths] 行业 {len(names)} 个, 区间 {start}~{target}")
    con = sqlite3.connect(str(SWDB), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    total = 0
    n_miss = 0
    missed_names: list[str] = []
    for i, name in enumerate(names, 1):
        df = None
        for attempt in range(3):
            try:
                df = ak.stock_board_industry_index_ths(
                    symbol=name, start_date=start,
                    end_date=f"{target[:4]}-{target[4:6]}-{target[6:]}")
                break
            except Exception as e:
                # 20261011批4: 重试失败此前完全静默(连异常信息都不记), 单行业缺口不可见
                log(f"[ths] {name} 第{attempt+1}次失败: {str(e)[:100]}")
                time.sleep(1 + attempt)
        if df is None or df.empty:
            n_miss += 1
            missed_names.append(name)
            log(f"[ths] ⚠️ {name} 无数据(重试后), 跳过")
            continue
        recs = [(name, str(r["日期"])[:10], r["开盘价"], r["最高价"], r["最低价"],
                 r["收盘价"], r["成交量"], r["成交额"]) for _, r in df.iterrows()]
        con.executemany(
            "INSERT OR REPLACE INTO industry_kline_ths"
            "(industry_name, trade_date, open, high, low, close, vol, amount) VALUES (?,?,?,?,?,?,?,?)",
            recs)
        total += len(recs)
        if i % 15 == 0:
            con.commit()
            log(f"[ths] {i}/{len(names)} 已写 {total} 行")
    con.commit()
    con.close()
    # 20261011批4: 缺行业必须可见(verify 只查全表 MAX(trade_date), 查不出单行业洞)
    log(f"[ths] 完成 {total} 行, 缺数据行业 {n_miss}/{len(names)}: {missed_names[:10]}")
    return 0


# ─────────────────────────── 环节: 主链路 ───────────────────────────
def run_main(target: str):
    """tulong_daily_update.py Phase1-9（TDX日线同步→因子→信号→基本面→市场统计→北向）"""
    log("[main] 调用 tulong_daily_update.py ...")
    r = subprocess.run([sys.executable, r"D:\workbuddyspace\tulong_daily_update.py",
                        "--date", target], cwd=r"D:\workbuddyspace")
    log(f"[main] 退出码 {r.returncode}")
    return r.returncode


# ─────────────────────────── 环节: feature 特征构建 ───────────────────────────
def run_feature(target: str):
    """feature_daily_v2 增量构建：调用 build_feature_v2.py 计算目标日技术特征。
    依赖：stock_daily_raw 已含目标日行情（由 main 环节写入）。
    背景：feature_daily_v2 原由 dragon_v4_bridge redis-bus 驱动，该链路已断。
          2026-08-12 发现 feature_daily_v2 无每日构建器导致 v43 时滞。
          脚本：D:/workbuddyspace/factor_engine/Scripts/build_feature_v2.py"""
    target_dash = f"{target[:4]}-{target[4:6]}-{target[6:]}"
    log(f"[feature] 增量构建 feature_daily_v2 for {target_dash} ...")
    r = subprocess.run(
        [sys.executable, r"D:\workbuddyspace\factor_engine\Scripts\build_feature_v2.py",
         "--start-date", target_dash, "--end-date", target_dash],
        cwd=r"D:\workbuddyspace", capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if r.stdout:
        for line in r.stdout.splitlines()[-12:]:
            log(f"[feature] {line}")
    if r.returncode != 0:
        log(f"[feature] 非零退出 {r.returncode}（可能行情未就绪，已跳过）: {r.stderr[-300:]}")
    return r.returncode


# ─────────────────────────── 环节: v43 信号刷新 ───────────────────────────
def run_v43(target: str):
    """v43 信号增量刷新：用 LightGBM 模型对 feature_daily_v2 的 target 日推理
    依赖：feature_daily_v2 已含 target 日特征。若特征未就绪则安全跳过。
    背景：stock_signals_v43 曾被误 purge（2026-07-30），现以 repair_v43_pipeline.py 重建，
          并接入本链路每日跟随特征刷新。脚本：D:/zcodesj/repair_v43_pipeline.py"""
    log("[v43] 增量刷新 stock_signals_v43 ...")
    r = subprocess.run(
        [sys.executable, r"D:\zcodesj\repair_v43_pipeline.py", "--date", target],
        cwd=r"D:\zcodesj", capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if r.stdout:
        for line in r.stdout.splitlines()[-8:]:
            log(f"[v43] {line}")
    if r.returncode != 0:
        log(f"[v43] 非零退出 {r.returncode}（可能特征未就绪，已跳过）: {r.stderr[-200:]}")
    return r.returncode


def run_v375(target: str):
    """V375 信号刷新：重训模型（2026-08-13，AUC 0.722）全量+增量特征→预测→写生产表
    背景：旧模型 TRAIN≤2023 致 OOS 衰减，2026-08-13 重训纳入至 2025-12，
          阈值 0.75（实测最优：OOS CAGR 25%/胜率 62.3%）。
    脚本：D:/zcodesj/update_v375_signals_r2.py（自备增量逻辑：复用历史数据集+增量特征）
    注意：脚本全量重写 stock_signals_v375（约5分钟），旧表有备份 stock_signals_v375_backup_20260813"""
    log("[v375] 刷新 stock_signals_v375（重训模型）...")
    r = subprocess.run(
        [sys.executable, r"D:\zcodesj\update_v375_signals_r2.py"],
        cwd=r"D:\zcodesj", capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800)
    if r.stdout:
        for line in r.stdout.splitlines()[-10:]:
            log(f"[v375] {line}")
    if r.returncode != 0:
        log(f"[v375] 非零退出 {r.returncode}: {r.stderr[-300:]}")
    return r.returncode


# ─────────────────────────── 环节: eastmoney 数据刷新 ───────────────────────────
def run_eastmoney(target: str):
    """东财量化数据增量更新：调用本地直跑脚本 update_eastmoney_quant_local.py
    绕过 MCP 的 30s 超时限制，直接同步东财股票 + 板块数据到本地 SQLite"""
    runner = r"D:\zcodesj\update_eastmoney_quant_local.py"
    log("[eastmoney] 增量更新东财股票+板块数据 ...")
    r = subprocess.run([sys.executable, runner, target],
                       cwd=r"D:\zcodesj", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    for line in r.stdout.splitlines()[-6:]:
        log(f"[eastmoney] {line}")
    if r.returncode != 0:
        log(f"[eastmoney] 非零退出 {r.returncode}: {r.stderr[-150:]}")
    return r.returncode


# ─────────────────────────── 环节: qlib_bin ───────────────────────────
def run_qlib_bin(target: str):
    """qlib_bin 日频增量(qfq 基准, 系统正源 2026-08-22根治): 把 stock_daily_meta_qfq 新交易日写入 qlib_bin
    依赖: meta 环节已同步到 target(stock_daily_meta)。幂等, 内置自检 FAIL 即退出码 2。
    2026-08-22: 替代 8/19/8/21 两个 ad-hoc 脚本, 并入日常链, 杜绝静默过期。"""
    log("[qlib_bin] 增量更新 qlib_bin ...")
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "qlib_bin_update_daily.py")],
                       cwd=str(Path(__file__).parent), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.stdout:
        for line in r.stdout.splitlines()[-8:]:
            log(f"[qlib_bin] {line}")
    if r.returncode != 0:
        log(f"[qlib_bin] 非零退出 {r.returncode}: {r.stderr[-200:]}")
    return r.returncode


# ─────────────────────────── 环节: hist (HIST 日频推理) ───────────────────────────
def run_hist(target: str):
    """HIST 5-seed ensemble 日推(不重训): Alpha360 特征 + 已固化 seed 权重 → 重写 hist_pred_seed*.parquet
    依赖: qlib_bin 已到 target(见 qlib_bin 环节)。重训由 hist_model_build.py 一次性完成。
    2026-08-22: 日常化, 替代原先"重负载 5-seed 训练 workflow 未日常化"导致的 HIST 停 8/13。"""
    log("[hist] HIST 日频推理 ...")
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "hist_daily_update.py")],
                       cwd=str(Path(__file__).parent), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if r.stdout:
        for line in r.stdout.splitlines()[-10:]:
            log(f"[hist] {line}")
    if r.returncode != 0:
        log(f"[hist] 非零退出 {r.returncode}: {r.stderr[-200:]}")
    return r.returncode


# ─────────────────────────── 环节: smc (S2×ROE>10% SSL扫荡信号) ───────────────────────────
def run_smc(target: str):
    """S2×ROE>10% SSL 流动性扫荡每日信号扫描（2026-08-29 前视污染退役=监控源，主人裁决A）。
    依赖: qfq 日线已到 target（smc_signal_daily.py 读 stock_daily_meta_qfq）。
    2026-08-23: 阶段1落地, smartmoneyconcepts 严格定义 + PIT ROE>10% + displacement 确认。
    2026-08-29: smartmoneyconcepts==0.0.27 swing_highs_lows 含 shift(-8) 未来函数——
    本环节降级为观察源: 照跑产 reports/smc_s2_roe_signal_{target}.csv 仅供监控复盘,
    白名单不再加载(tulong_buy_whitelist 断接线), admit 闸门拒绝(smc_s2_roe 入 RETIRED_SOURCES)。
    产物数字不可作选股/回测依据。污染档案: reports/SMC_LOOKAHEAD_BIAS_DISCOVERY_20260829.md"""
    log("[smc] S2×ROE>10% 扫荡信号扫描（退役监控源, 不入白名单） ...")
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "smc_signal_daily.py"),
         "--date", target, "--strategy"],
        cwd=str(Path(__file__).parent), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.stdout:
        for line in r.stdout.splitlines()[-8:]:
            log(f"[smc] {line}")
    if r.returncode != 0:
        log(f"[smc] 非零退出 {r.returncode}: {r.stderr[-200:]}")
    return r.returncode


# ─────────────────────────── 验证 ───────────────────────────
def verify(target: str) -> list[str]:
    """验证目标日各表新鲜度。2026-09-10 fail-closed: 返回「核心表落后」清单,
    调用方计入 failures 使链 exit 1(当日发现不隔夜——9/2 chan 断更当晚靠人工发现的
    同族教训); 感知/参考层表仅 WARN(health 01:00 STRICT 已覆盖, 不阻塞 22:00 链)。
    核心表定义=直接喂信号生产/白名单/闸门的表。"""
    log("=" * 60)
    log(f"验证目标日 {target}:")
    _CORE = {
        "qfq stock_daily_meta_qfq", "daily_basic", "stock_daily_raw", "feature_daily_v2",
        "stock_ml_scores_v22", "longhubang_daily", "lhb_seat_detail",
        "chanlun_signals_nolookahead",
    }
    checks = [
        ("qfq stock_daily_meta_qfq", QFQ_DB, "date"),
        ("daily_basic", FUND_DB, "trade_date"),
        ("feature_market_sentiment", FUND_DB, "trade_date"),
        ("feature_industry_momentum", FUND_DB, "trade_date"),
        ("northbound_daily", FUND_DB, "trade_date"),
        ("industry_kline_ths", SWDB, "trade_date"),
        ("stock_daily_raw", MAIN_DB, "date"),
        ("feature_daily_v2", MAIN_DB, "date"),
        ("stock_factors", MAIN_DB, "date"),
        ("stock_signals", MAIN_DB, "date"),
        ("stock_signals_v43", MAIN_DB, "date"),
        ("stock_signals_v375", MAIN_DB, "date"),
        # 2026-09-02 补查: aux 环节 chan 步骤失败曾静默(验证块无此表, 9/2 当晚靠人工发现)
        ("chanlun_signals_nolookahead", MAIN_DB, "date"),
        # 2026-09-05 补查: 分钟线/聪明钱当日接入 aux 环节(9/4晚 minute 停9/3 曾无人报警)
        ("stock_minute_1", META_DB, "date"),
        ("stock_minute_5", META_DB, "date"),
        ("smart_money_factors", META_DB, "date"),
        # 2026-09-05 补查: v22 ml 分数入链日更(8/14 停更曾致 10/1 调仓陈旧分 P0, 91eb5c2)
        ("stock_ml_scores_v22", MAIN_DB, "date"),
        # 2026-09-08 补查: 龙虎榜家族日更接入 aux(lhb_update.py 四表=榜单三表+席位明细
        # lhb_seat_detail; 此前三表 9/2 起断更 4 日无人报警=链内无环节; 席位净买额缺口闭合)
        ("longhubang_daily", FUND_DB, "trade_date"),
        ("lhb_seat_detail", FUND_DB, "trade_date"),
    ]
    core_behind: list[str] = []
    for name, db, col in checks:
        try:
            with ro(db) as c:
                _tbl = name.split(' ', 1)[1] if ' ' in name else name
                if not (_tbl.isidentifier() and str(col).isidentifier()):
                    raise ValueError(f"非法标识符: {name}/{col}")
                mx = c.execute(f"SELECT MAX({col}) FROM {_tbl}").fetchone()[0]
        except Exception as e:
            mx = f"ERR {e}"
        # 2026-08-24 P2修复: "ERR..." 字符串比较恒 >= 数字target("E">"2") → 误判OK
        mx_s = str(mx).replace("-", "")
        ok = mx_s.isdigit() and mx_s >= target
        if not ok and name in _CORE:
            core_behind.append(name)
        print(f"  [{'OK' if ok else '落后'}] {name}: {mx}")
    # qlib_bin: 日历末位(day.txt) 应 >= target
    try:
        qd = [l.strip() for l in
              open(r"D:\workbuddyspace\qlib_data\qlib_bin\calendars\day.txt", encoding="utf-8")
              if l.strip()]
        qmx = qd[-1] if qd else ""
    except Exception as e:
        qmx = "00000000"
    qok = qmx.replace("-", "") >= target
    if not qok:
        core_behind.append("qlib_bin day.txt")
    print(f"  [{'OK' if qok else '落后'}] qlib_bin day.txt: {qmx}")
    # hist: hist_pred_seed1.parquet 最新预测日应 >= target (20261011 工件 pkl→parquet)
    try:
        import pandas as _pd
        _hp = _pd.read_parquet(r"D:\zcodesj\qlib_exp\hist_data\hist_pred_seed1.parquet")
        hmx = str(_hp.index.get_level_values(0).max())[:10]
    except Exception as e:
        hmx = "00000000"
    hok = str(hmx).replace("-", "") >= target
    if not hok:
        core_behind.append("hist hist_pred_seed1.parquet")
    print(f"  [{'OK' if hok else '落后'}] hist hist_pred_seed1.parquet: {hmx}")
    if core_behind:
        log(f"⚠️ 核心表落后(fail-closed 2026-09-10): {', '.join(core_behind)}")
    log("=" * 60)
    return core_behind


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="屠龙数据链路统一更新")
    ap.add_argument("--date", help="目标交易日 YYYYMMDD（默认自动检测通达信最新）")
    ap.add_argument("--phase", default="all",
                    choices=["all", "main", "feature", "v43", "v375", "qfq", "daily_basic", "aux", "eastmoney", "qlib_bin", "hist", "smc"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target = args.date or str(max(int(detect_tdx_latest()), int(detect_main_latest())))
    # 2026-08-24 修复: 原默认锚定通达信vipdoc(可能滞后, 8/17), 致qfq构建从不尝试从tickflow追平主库(8/21)
    # 改取 vipdoc 与主库较新者, qfq阶段可经tickflow补齐新交易日
    LOG_DIR.mkdir(exist_ok=True)
    log(f"目标交易日: {target} | 通达信最新: {detect_tdx_latest()} | 主库最新: {detect_main_latest()}")

    if args.dry_run:
        log(f"[dry-run] 计划执行: phase={args.phase} 各环节将增量更新至 {target}")
        return

    # 2026-09-02 链序修复: v375 移到 qfq 之后。update_v375_signals_r2 的特征构建读
    # stock_daily_meta_qfq(QFQ_DB), 原序 v375 先于 qfq 执行 → v375 恒只产出 qfq 已有
    # 日期(滞后一天, 9/2 实证: 链内产出止于9/1被验证块报落后, 链后重跑才补齐9/2)。
    # v43 读 feature_daily_v2(MAIN_DB, main Phase1 已更新), 无 qfq 依赖, 维持原位。
    phases = {"all": ["main", "feature", "v43", "qfq", "v375", "daily_basic", "aux", "eastmoney", "qlib_bin", "hist", "smc"],
              "main": ["main"], "feature": ["feature"], "v43": ["v43"], "v375": ["v375"],
              "qfq": ["qfq"], "daily_basic": ["daily_basic"], "aux": ["aux"],
              "eastmoney": ["eastmoney"], "qlib_bin": ["qlib_bin"], "hist": ["hist"], "smc": ["smc"]}[args.phase]

    # 软失败如实上报(移植自 TSP daily_pipeline 编排原则):
    #   环节失败/异常 → 记入 failures, 继续跑后续独立环节; 终态如实 failed, exit 1。
    # ── 2026-10-14 根治令: aux 注册表化(替代 30+ 子件串行 try/except 平铺) ──
    #   背景: aux 自 9/1 86s 膨胀至 10/12 1111s, 每挂新观测件成本无人可见。
    #   结构: group="db"=写共享库/有先后依赖(pivot/sw_index/minute/ml_scores/lhb/
    #   attr族/patterns→cluster/chan/concept), 保序串行; group="shadow"=各写自己
    #   台账的独立观测件, 4 路并行池(db 组全部完成后才启动, 依赖靠相位序保证)。
    #   每件 timeout 上界 + 耗时入 chain_timing_latest.json(health 消费); 新挂件必须
    #   走注册表, 禁止回平铺。各件日志标签/timeout 原值/失败不阻断语义保真迁移;
    #   aux 整体重试 2 次(main 层)不变。
    # 2026-10-20 看门狗(「继续。修复、优化、迭代」批准, ths挂死35分钟实锤登记):
    # 前四内联进程内步骤(akshare/网络直调, 非 _run_script 子进程无 timeout 兜底)
    # 挂看门狗线程——超时弃子转后台(daemon, 链退出自然回收), rc=124 已入 timing,
    # 链继续不无限期挂死。弃子线程晚到写=同表 UPSERT 幂等且下次链在 24h 后, 风险可控。
    _INPROC_WATCHDOG = {"sentiment": 300, "northbound": 300, "momentum": 300, "ths": 300}

    def _timed(name, fn):
        t0 = time.time()
        rc = 0
        _wd = _INPROC_WATCHDOG.get(name)
        if _wd:
            # 2026-10-20 看门狗: 前四内联进程内步骤(akshare/网络直调, 非 _run_script
            # 子进程无 timeout 兜底)挂看门狗线程——超时弃子转后台(daemon, 链退出回收),
            # rc=124 与子进程超时同惯例; 弃子线程晚到写=同表 UPSERT 幂等且下次链在
            # 24h 后, 风险可控(ths 挂死 35 分钟实锤 20261020 凌晨登记)。
            _box = {}

            def _runner():
                try:
                    _box["rc"] = fn(target) or 0
                except Exception as _e:  # 线程内异常回传主判(保持原 rc=1 语义)
                    _box["err"] = _e

            _th = threading.Thread(target=_runner, daemon=True, name=f"wd-{name}")
            _th.start()
            _th.join(_wd)
            if _th.is_alive():
                rc = 124
                log(f"[{name}] ⌛ WATCHDOG 超时({_wd}s) 判网络挂起, 弃子继续"
                    f"(线程转后台, 链退出回收; 晚到写=同表幂等) rc=124 已入 timing")
            elif "err" in _box:
                log(f"[{name}] 跳过: {_box['err']}")
                rc = 1
            else:
                rc = _box.get("rc") or 0
        else:
            try:
                rc = fn(target) or 0
            except subprocess.TimeoutExpired:
                # 2026-10-18 a1修复: 超时原记 rc=0 冒充成功(timing json 无从区分, 违反
                # 生产路径结构化错误返回)。改记 124(Unix timeout 惯例); shadow 组 rc 不参与
                # _RC_FUNCS 重试判定, db 组四环节耗时远低于超时上界, 零误触发面。
                rc = 124
                log(f"[{name}] 超时跳过(不影响其他环节) rc=124 已入 timing")
            except Exception as e:
                log(f"[{name}] 跳过: {e}")
                rc = rc if rc else 1
        _AUX_TIMING[name] = {"rc": rc, "seconds": round(time.time() - t0, 1)}
        return rc

    def _run_script(name, argv, timeout, cwd=r"D:\zcodesj", ok_rc=(0,), tail=None, last=True):
        # 2026-10-23 加固: 子进程超时/启动失败原本异常穿透到 phase 层 except Exception,
        # aux shadow 线程池 map 会随之中断丢弃剩余子件; 改按 rc 语义返回(124=超时惯例),
        # 单件故障不放大为整环节丢弃。与 _timed 看门狗同语义。
        try:
            r = subprocess.run([sys.executable] + list(argv), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            log(f"[{name}] ⌛ 超时({timeout}s)被杀, rc=124")
            return 124
        except OSError as _oe:
            log(f"[{name}] 子进程启动失败 OSError: {_oe}")
            return 1
        if r.returncode not in ok_rc:
            log(f"[{name}] 非零退出 {r.returncode}: {(r.stderr or r.stdout or '')[-150:]}")
            return r.returncode
        lines = (r.stdout or "").splitlines()
        if tail is not None:
            out = " | ".join(l for l in lines if tail(l))[-300:]
            log(f"[{name}] {out or 'OK'}")
        elif last:
            log(f"[{name}] " + (lines or ["OK"])[-1][:120])
        else:
            log(f"[{name}] OK")
        return 0

    def run_aux(t):
        # qfq收益透视缓存刷新(配对规避/情绪合成/行业轮动等5个消费者共读; 自愈式: 落后qfq主库才重建, 幂等约13s)
        # 2026-08-27 接入: 此前仅factor_crowding_monitor写该缓存且无cron跑, 缓存停在8/14致消费者长期用旧数据。
        def _s_pivot(_t):
            sys.path.insert(0, str(Path(__file__).parent))
            from factor_crowding_monitor import load_return_pivot
            piv = load_return_pivot()
            log(f"[pivot] qfq收益透视缓存 OK 末列={piv.columns.max()} 股票数={len(piv)}")
            return 0

        # 申万官方行业指数增量(akshare, L1 31只约40s, 幂等; L2 为历史档案不逐日拉)
        def _s_sw_index(_t):
            return _run_script("sw_index", [str(Path(__file__).parent / "update_sw_index_daily.py")],
                               600, tail=lambda l: l.startswith("["))

        # 分钟线(1m/5m)+聪明钱因子增量(2026-09-05 接入: 此前不在链内, 滞后只能靠人工发现——
        # 9/4晚实证 minute_1/5 停9/3 而验证块与健康检查均无此表报警; 标准入口=update-dragon-
        # minute-smart-money skill, 链只做接线不复制语义。5分钟由1分钟聚合, 只依赖 vipdoc .lc1。
        # 两脚本均幂等(写前删目标日旧数据), 失败不阻断其他步骤)
        def _s_minute(_t):
            _fs = Path(r"D:\workbuddyspace\factor_engine\scripts")
            rc = 0
            for _script, _label in (("update_minute_from_tdx.py", "minute"),
                                    ("update_smart_money.py", "smart_money")):
                rc = _run_script(_label, [str(_fs / _script)], 1800,
                                 cwd=str(_fs.parent.parent),
                                 tail=lambda l: "INFO" in l or l.startswith("完成")) or rc
            return rc

        # v22 ml 分数增量打分(2026-09-05 深夜接入, 与 v43 同构=LightGBM 推理 feature_daily_v2;
        # 背景: 表 8/14 起停更(打分脚本被 8/18 审计清理归档), production_combo_monthly 按
        # MAX(date) 静默读, 10/1 调仓(ml权重40)将用陈旧分=P0。方案A修复(91eb5c2)后升级为
        # 链内日更=持续保鲜。原口径延续(同模型同88特征同SQL); 幂等窗口UPSERT;
        # 失败不阻断其他环节; 停更时调仓侧 staleness 守卫(>21d exit 2)兜底)
        def _s_ml_scores(_t):
            return _run_script("ml_scores", [r"D:\zcodesj\ml_scores_v22_incremental.py"], 900,
                               tail=lambda l: l.startswith("[OK]") or l.startswith("[WARN]"))

        # 龙虎榜家族日更(2026-09-08 接入: lhb_update.py 四表=longhubang_daily/dragon_tiger_new/
        # feature_longhubang/lhb_seat_detail, 席位净买额缺口闭合=融合总图龙虎榜机构模型数据缺口①;
        # 源=东财 datacenter 免费直连; 幂等窗口UPSERT, 增量从表内 MAX 当日重拉防东财半日出榜固化;
        # 盘后数据 22:00 链时点东财已就绪; 失败不阻断其他环节)
        def _s_lhb(_t):
            return _run_script("lhb", [r"D:\zcodesj\lhb_update.py"], 900,
                               tail=lambda l: l.startswith("[OK]") or l.startswith("[WARN]"))

        # 归因闭环同步(2026-09-08 O-N1接入, 第八轮自省B3收口: position_history此前唯一
        # 写入方=一次性导入脚本, 8/27后断链→归因反馈报告胜率分布停留在7-8月旧账,
        # "归因经验库反向赋能"数据根基失效。closed_loop_sync=闭环唯一owner(全史FIFO配对
        # trade_records→增量写CLOSED行, 幂等, legacy行与外键不动), 成功后顺序重归因。
        # 失败不阻断其他环节)
        def _s_attr_loop(_t):
            rc = _run_script("attr_loop", [r"D:\zcodesj\attribution\closed_loop_sync.py", "--execute"],
                             300, tail=lambda l: l.startswith("[OK]") or l.startswith("[closed_loop_sync]"))
            if rc:
                return rc
            return _run_script("attr_batch", [r"D:\zcodesj\attribution\batch_attribution.py"],
                               300, tail=lambda l: "总笔数" in l or "[OK]" in l)

        # 归因引擎T+3/T+5收益回填(2026-09-08 O-N5接入: 脚本docstring自述"daily_chain 22:30"
        # 但从未实际接线, 9/8 health报83行窗口到期未填=断链实锤。幂等None=等窗口, 失败不阻断)
        def _s_attr_fwd(_t):
            r = subprocess.run(
                [sys.executable, str(Path(r"D:\zcodesj\attribution_outcome_backfill.py")), "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600, cwd=r"D:\zcodesj")
            if r.returncode:
                log(f"[attr_fwd] 非零退出 {r.returncode}: {(r.stderr or r.stdout or '')[-150:]}")
                return r.returncode
            import json as _json
            try:
                last = [l for l in (r.stdout or "").splitlines() if l.startswith("{")]
                info = _json.loads(last[-1]) if last else {}
                log(f"[attr_fwd] updated={info.get('total', '?')} waiting_data={info.get('waiting_data', '?')}")
            except Exception:
                log("[attr_fwd] OK")
            return 0

        # 归因反馈闭环 sync+update(2026-10-06 三尾巴②接入: verification_trades 的
        # sync闭合与 actual_ret_t1/t3/t5+result 回填 owner=feed_feedback_tables.py,
        # 原 WestockReconcile_1640 schtasks 丢失后断链, 9/29-30 行 result=None 即实锤。
        # 幂等(WHERE result IS NULL), 失败不阻断; 消费方=strategy_decay_monitor)
        def _s_feedback(_t):
            fb_out, fb_ok = [], True
            for _fb_cmd in ("sync", "update"):
                r = subprocess.run(
                    [sys.executable, str(Path(r"D:\zcodesj\feed_feedback_tables.py")), _fb_cmd],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=600, cwd=r"D:\zcodesj")
                fb_out.append(f"{_fb_cmd}=rc{r.returncode}")
                if r.returncode:
                    fb_ok = False
                    log(f"[feedback] {_fb_cmd} 非零退出 {r.returncode}: {(r.stderr or r.stdout or '')[-150:]}")
            log(("[feedback] " if fb_ok else "[feedback][部分失败] ") + " ".join(fb_out))
            return 0 if fb_ok else 1

        # 涨停范式库引擎口径日更(2026-09-09 接入, 供给闭环A; 幂等=同日同码 engine_v2 跳过;
        # 白名单主线标注/首板共振探测器/复盘 CLI 均消费此表; 失败不阻断其他环节;
        # 防复发=health check_limitup_patterns。
        # 2026-09-10 B-M3: patterns 前置到 cluster 之前(原顺序 cluster 先跑时当日引擎行
        # 尚未回灌 → 22:00 cluster 产物恒为 T-1 截面; 顺序对调后 22:00 产物即为当日)
        def _s_patterns(_t):
            # 2026-10-21 主人「继续」批准: 900→1800。10/20、10/21 连续两晚撞 900 上限
            # (新日期全量回灌 46-50 只, 晚间窗口实测聚合 ~19.6s/只, 为日间 5.8s/只基线
            # 的 ~3.4 倍——akshare 源晚间限频/争用, 08bb8c0 四线程并行化后仍踩线);
            # 1800 给一倍余量, 免除每晚链外手工补收。幂等已入库 SKIP, 超时仍按设计降级。
            rc = _run_script("patterns", [r"D:\zcodesj\attribution_patterns_backfill.py", "--date", str(t)],
                             1800, tail=lambda l: ("入库" in l or "跳过" in l or "OK" in l))
            r2 = subprocess.run(
                [sys.executable, str(Path(r"D:\zcodesj\limitup_review_cli.py")), "check"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60, cwd=r"D:\zcodesj")
            for l in (r2.stdout or "").splitlines():
                if "check:" in l or "WARN" in l:
                    log(f"[patterns] {l.strip()}")
            return rc

        # 首板集群共振探测(2026-09-09 接入, 主人红棉复盘裁决; 归因表缺当日数据=fail-open
        # WARN 不阻断; 幂等覆盖; B-M3: 已移至 patterns 之后)
        def _s_cluster(_t):
            return _run_script("cluster", [r"D:\zcodesj\firstboard_cluster_probe.py"], 120,
                               tail=lambda l: l.startswith("[cluster]"))

        # 缠论二买增量(纯pandas, 幂等; 自动补 表内MAX+1 → 最新交易日)
        # qfq 副本同步已退役(2026-10-12 单库合并, 翻转定谳 wb=唯一真源, 详见SOP一百三十一/一百三十二)
        def _s_chan(_t):
            con = sqlite3.connect(str(MAIN_DB), timeout=30)
            mx = con.execute("SELECT COALESCE(MAX(date),0) FROM chanlun_signals_nolookahead").fetchone()[0]
            con.close()
            if mx >= int(t):
                log(f"[chan] 已最新({mx}), 跳过")
                return 0
            return _run_script("chan", [r"D:\workbuddyspace\build_chanlun_incremental.py",
                                        "--start-date", str(mx + 1)], 1800,
                               tail=lambda l: "INFO" in l)

        # TSP 扩展数据概念/行业常态化入库(已幂等, 无新增数据也秒过)
        def _s_concept(_t):
            sys.path.insert(0, str(Path(__file__).parent))
            import import_tsp_ext_data as etd
            etd.import_concept()
            etd.import_industry()
            return 0

        # ── shadow 组(各写自己台账的独立观测件, 并行池; 全部纯观测零交易权) ──
        # 相位仓位影子记账(2026-10-06 主人批准重测激活, 与已证伪族 SENTIMENT-CYCLE-DAILY-ALPHA
        # 同族重测条件已备案, PREREG指纹0fb887e3b115fda4; 重启扩大须主人裁决引用证伪档案)
        def _s_phase_pos(_t):
            return _run_script("phase_pos", [r"D:\zcodesj\emotion_position_shadow.py", "--since", "20261008"], 300)

        # 候选辛统一仓位模型影子记账(2026-10-07 批准, PREREG_POSITION_FRAMEWORK_SHADOW 7e85cef0d5d45f9f;
        # 相位层复用已批准重测的phase影子权重, 新增赢面层f_win三臂对照; 60交易日三门判读)
        def _s_pos_fw(_t):
            return _run_script("pos_fw", [r"D:\zcodesj\position_framework_shadow.py", "--since", "20261008"], 300)

        # 候选甲SC_SPRING_DEEP_LOUD影子日更(20261007「按推荐继续」批准, PREREG_SC_SPRING_DEEP_LOUD_20261007):
        # 全史重算幂等, 参数单一真源=prereg params字段fail-closed
        def _s_sc_spring(_t):
            return _run_script("sc_spring", [r"D:\zcodesj\analysis\sc_spring_shadow_daily.py"], 300)

        # C-Z1资金结构三态影子日更(20261008「按推荐继续」批准, PREREG_CZ1_FUND_STRUCTURE_STATE_SHADOW_20261008):
        # 全史重算幂等(滚动252日分位无前视口径)
        def _s_cz1(_t):
            return _run_script("cz1", [r"D:\zcodesj\analysis\cz1_structure_state_shadow_daily.py"], 300)

        # 低位枯竭修复族影子日更(20261017「按推荐继续」批准激活, PREREG_LOW_DRYUP_REPAIR_FAMILY_20261017):
        # 主判据臂T1低位缩量破跌停(臂级对照选优), 全史重算幂等, 参数单一真源=prereg params字段fail-closed
        def _s_low_repair(_t):
            return _run_script("low_repair", [r"D:\zcodesj\analysis\low_repair_family_shadow_daily.py"], 300)

        # 主组合前向机械影子臂日更(20261018「继续」令批准激活, PREREG_MAIN_COMBO_FORWARD_SHADOW):
        # 引擎整套机制复用重算幂等(bp_ep月度Top15+ml h20 50/50纯记账, S-3落地2026-10-18), EOS抑制=持仓延续, 600s(引擎装载重)
        def _s_fwd_shadow(_t):
            return _run_script("fwd_shadow", [r"D:\zcodesj\analysis\main_combo_forward_shadow_daily.py"], 600)

        # vr120 前置过滤影子日更(20261019「按推荐继续」批准激活, PREREG_VR120_FILTER_20261018 指纹ac59abfc027d3b20):
        # 消费 fwd_shadow 批次名单 dump(同loader同信号函数保真), ml臂主判据/bp_ep附臂严容差; 零交易权零接线
        def _s_vr120f(_t):
            return _run_script("vr120f", [r"D:\zcodesj\analysis\vr120_filter_shadow_daily.py"], 300,
                               tail=lambda l: l.startswith("[vr120f]") and
                               ("ok]" in l or "skip]" in l or "fail-closed" in l))

        # 深踩广度日篮子影子日更(20261011「继续」批准, PREREG_DIPFLUSH_BREADTH_BASKET_20261011)
        def _s_dipflush(_t):
            return _run_script("dipflush", [r"D:\zcodesj\analysis\dipflush_breadth_shadow_daily.py"], 300)

        # [chain_pool] 接力池扫描器(20261010「干,继续」令, 呈现层零交易权):
        # 确认池=当日涨停×板数×封板时段→次日连板率参考; 依据=entry_portrait_db_20261010 §十/§十一
        def _s_chain_pool(_t):
            return _run_script("chain_pool", [r"D:\zcodesj\analysis\chain_relay_screener.py"], 600)

        # 组合优势域影子日更(20261011立项令, PREREG_COMBODOMAIN_SHADOW_20261011;
        # 库尾≠运行日自动skip)
        def _s_combo(_t):
            return _run_script("combo", [r"D:\zcodesj\analysis\combo_domain_shadow_daily.py"], 300)

        # 财联社重点电报归档日更(20261011 批准B方向, PREREG_CLS_NEWS_SHADOW_20261011;
        # roll接口单窗30条=部分覆盖采样, 全量靠 ClsNewsArchive_Hourly schtasks)
        def _s_cls_news(_t):
            return _run_script("cls_news", [r"D:\zcodesj\analysis\cls_news_archive_daily.py"], 300)

        # [chain_shadow] 影子组合日更(20261010「继续」令): 哨vs重放对账+终版D影子逐日记账;
        # last_date幂等
        def _s_chain_shadow(_t):
            return _run_script("chain_shadow", [r"D:\zcodesj\analysis\chain_shadow_daily.py"], 900)

        # [ptwt]画像软权重影子已于2026-10-09迁出本链: 行业量能状态由IndustryVolState_2252
        # schtasks(22:52)在本链之后生成, 22:00槽位会永远拿到qfq=当日+C1=前日的锚错配对
        # (一致性闸每日SKIP=实验死); 正确挂点=automation PtwtShadow_0935。新件挂链前先核对
        # 数据对齐窗口, 防同类锚错配。
        # F2B主升3日新高确认窗影子日更(20261009「升级」令, PREREG_F2B_RALLY_CONFIRM_SHADOW_20261009)
        def _s_f2b_rally(_t):
            return _run_script("f2b_rally", [r"D:\zcodesj\analysis\r46_f2b_rally_shadow_daily.py"], 600)

        # G1市值分组成交额占比分位影子日更(20261009「升级」令, PREREG_G1_CROWDING_PCTL_SHADOW_20261009;
        # 双库流式归并全史重算幂等约1-2分钟)
        def _s_g1_crowding(_t):
            return _run_script("g1_crowding", [r"D:\zcodesj\analysis\r46_g1_crowding_shadow_daily.py"], 600)

        # 风格复盘人读件日更(20261009「继续」令固化, 滚动30交易日幂等件);
        # 🔴giveback出场侧治理已判死(证伪#793)仅事实呈现禁作止盈规则依据
        def _s_style_review(_t):
            return _run_script("style_review", [r"D:\zcodesj\analysis\r46_style_review_20261009.py"], 120)

        # 主线五角色+联合日卡观测层日更(2026-10-06 批准接日更, 候选戊落地);
        # akshare东财涨停池(近期窗口有效); 非交易日EMPTY自动跳过
        def _s_theme_obs(_t):
            # --date 须用链 target 而非 datetime.now(): 凌晨/跨日补跑时 now=次日,
            # joint_card 索要未交易日行必FAIL(10/20 02:22 实证索 20261020);
            # 22:00 常规窗二者恒等, 本改动对常规窗零行为变化
            return _run_script("theme_obs", [r"D:\zcodesj\theme_daily_refresh.py", "--date", _t], 600)

        # PD-034恐慌超跌出击影子台账(20261006批准激活, PREREG_PANIC_OVERSHOOT_20261006;
        # 观测层零交易权不接闸门)
        def _s_panic_obs(_t):
            return _run_script("panic_obs", [r"D:\zcodesj\panic_overshoot_shadow.py"], 600)

        # 组合结构六指标只读探针(2026-09-08 O-N3接入; 只读告警不拦截,
        # 集中度硬cap已被 round12a/12b 证伪关闭不复活; rc 0/1 均属正常)
        def _s_structure(_t):
            return _run_script("structure", [r"D:\zcodesj\portfolio_structure_probe.py"], 120,
                               ok_rc=(0, 1),
                               tail=lambda l: l.startswith("[") or "WARN" in l)

        # 低位首板蓄势感知层日更(2026-09-09 接入, 红棉断链收口; daily=scan+update 一条龙;
        # breakout 升级=leader_watchlist 登记幂等; 防复发探针=health check_scout_track)
        def _s_scout(_t):
            return _run_script("scout", [r"D:\zcodesj\breakout_scout.py", "daily"], 600,
                               tail=lambda l: l.startswith("[breakout_scout]"))

        # 主线龙头观察池日更(2026-09-09 接入, 供给闭环D: 补行情+回踩企稳 setup 检查+cooldown
        # 状态机, 幂等可重跑; setup_confirmed=提请研究非买信号)
        def _s_leader(_t):
            r = subprocess.run(
                [sys.executable, str(Path(r"D:\zcodesj\leader_watchlist.py")), "update"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=300, cwd=r"D:\zcodesj")
            if r.returncode:
                log(f"[leader] 非零退出 {r.returncode}: {(r.stderr or r.stdout or '')[-150:]}")
                return r.returncode
            tail = " | ".join(l for l in (r.stdout or "").splitlines()
                              if l.startswith("[leader_watchlist]") and "完成" in l)[-300:]
            _star = sum(1 for l in (r.stdout or "").splitlines() if "setup_confirmed" in l and "★" in l)
            log(f"[leader] {tail or 'OK'}" + (f" | 新setup确认{_star}只" if _star else ""))
            return 0

        # #802 score加权持仓分层影子日更(20261016「继续」令立项): bp_ep月度Top15生产批次(S-3落地2026-10-18)
        # (picker --no-sync 只读)按滚动IS三分位切点带权1.5/1.0/0.5 vs 等权base, 日频qfq链式
        # 超额+三门读数; 零交易权零接线, 判定权=影子末主人裁决。
        # prereg=PREREG_SCORE_WEIGHT_TIER_20261016.json 指纹03021090acf58ea3b37950dc7bd2654236327e53a5bafc68a9ed0845e518a653
        def _s_swt(_t):
            return _run_script("swt", [r"D:\zcodesj\score_weight_tier_shadow.py"], 300,
                               tail=lambda l: l.startswith("[") and ("ok]" in l or "skip]" in l or "fail-closed" in l))

        def _s_trend_alert(_t):
            # 大盘趋势预警分级+复盘首节两问直出(20261015主人令「复盘报告应该提醒」/
            # 「每次复盘要判断什么趋势,应该什么仓位」): 零交易权观测件, 只写
            # data/trend_alert_latest.txt+ledger; 数据源=vipdoc本地+相位/档位台账+westock仓位。
            # 分级规则与回撤窗口(120日)口径=脚本docstring单一真源(SOP一百四十二⑦)。
            return _run_script("trend_alert", [r"D:\zcodesj\analysis\trend_alert_daily.py"], 300,
                               tail=lambda l: l.startswith("【大盘趋势预警"))

        def _s_sop_index(_t):
            # SOP_INDEX.md 行号级索引再生(20261015自省§六短期6): 只读SOP→写索引,
            # 零交易权; 索引为生成物禁手改。SOP 追加台账行后由夜链自动同步,
            # 消除"索引滞后SOP本体"(10/12快照滞后10/14两例)。
            r = subprocess.run(
                [sys.executable, str(Path(r"D:\zcodesj\analysis\gen_sop_index.py"))],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, cwd=r"D:\zcodesj")
            if r.returncode:
                log(f"[sop_index] 非零退出 {r.returncode}: {(r.stderr or r.stdout or '')[-150:]}")
                return r.returncode
            log("[sop_index] SOP_INDEX.md 已再生(生成器直出)")
            return 0

        def _s_a1(_t):
            # A1事件驱动限价卖单滑点测量(PD-044③, 20261016「继续」令): 零交易权;
            # 扫带A1_EVENT_SELL注记卖单配westock回报算滑点UPSERT台账, n≥10自动出核对报告。
            rc = _run_script("a1_slippage", [r"D:\zcodesj\a1_slippage_ledger.py", "--scan"], 120,
                             tail=lambda l: l.startswith("[A1-LEDGER]"))
            if rc == 0:
                _run_script("a1_report", [r"D:\zcodesj\a1_slippage_ledger.py", "--report"], 60,
                            tail=lambda l: l.startswith("[A1-REPORT]"))
                # 11/07 增补: 执行质量判据探针(连续3日滑点超标→toast报警), 模拟盘结构性零滑点属正常
                _run_script("a1_alarm", [r"D:\zcodesj\a1_slippage_ledger.py", "--alarm"], 60,
                            tail=lambda l: l.startswith("[A1-ALARM]"))
            return rc

        def _s_shadow_sla(_t):
            # 影子判读SLA日历(PD-044①, 20261016「继续」令): 零交易权; registry唯一真源
            # (新增影子激活须同步登记), 到期≤3交易日toast; 消灭影子无限期在轨缺口。
            return _run_script("shadow_sla", [r"D:\zcodesj\analysis\shadow_sla_check.py"], 60,
                               tail=lambda l: l.startswith("[SHADOW-SLA]"))

        # 注册表: (计时名, 组, fn)。db 组保序=既有依赖序; shadow 组相互独立可并行。
        # ── 新件接入规范(SOP一百三十七, 拒绝回平铺) ──
        #   1. 用 _run_script("标签", [脚本路径, 参数...], timeout秒, tail=日志过滤器 或 默认取末行)
        #      包一个闭包; 有副脚本/特殊日志(如 leader/patterns/attr_loop)在闭包内自行编排,
        #      subprocess 超时/通用异常由 _timed 统一兜底, 闭包内只管业务。
        #   2. group 选择: 写共享 SQLite 库或有先后依赖 → "db"(串行保序); 只写自己台账
        #      jsonl/json 的独立观测件 → "shadow"(并行池)。拿不准 → "db"。
        #   3. 硬性要求: 零交易权声明 + prereg 指纹注记 + timeout 必填(不给默认无限挂)。
        #   4. 数据对齐窗口先核对(反例=[ptwt]: 锚错配对=实验死, 见下方迁出注记)。
        registry = [
            ("sentiment", "db", run_sentiment), ("northbound", "db", run_northbound),
            ("momentum", "db", run_momentum), ("ths", "db", run_industry_ths),
            ("pivot", "db", _s_pivot), ("sw_index", "db", _s_sw_index),
            ("minute", "db", _s_minute), ("ml_scores", "db", _s_ml_scores),
            ("lhb", "db", _s_lhb), ("attr_loop", "db", _s_attr_loop),
            ("attr_fwd", "db", _s_attr_fwd), ("feedback", "db", _s_feedback),
            ("patterns", "db", _s_patterns), ("cluster", "db", _s_cluster),
            ("chan", "db", _s_chan), ("concept_industry", "db", _s_concept),
            ("phase_pos", "shadow", _s_phase_pos), ("pos_fw", "shadow", _s_pos_fw),
            ("sc_spring", "shadow", _s_sc_spring), ("cz1", "shadow", _s_cz1),
            ("low_repair", "shadow", _s_low_repair),
            ("fwd_shadow", "shadow", _s_fwd_shadow),
            ("vr120_filter", "shadow", _s_vr120f),
            ("dipflush", "shadow", _s_dipflush), ("chain_pool", "shadow", _s_chain_pool),
            ("combo", "shadow", _s_combo), ("cls_news", "shadow", _s_cls_news),
            ("chain_shadow", "shadow", _s_chain_shadow), ("f2b_rally", "shadow", _s_f2b_rally),
            ("g1_crowding", "shadow", _s_g1_crowding), ("style_review", "shadow", _s_style_review),
            ("theme_obs", "shadow", _s_theme_obs), ("panic_obs", "shadow", _s_panic_obs),
            ("structure", "shadow", _s_structure), ("scout", "shadow", _s_scout),
            ("leader", "shadow", _s_leader),
            ("swt_shadow", "shadow", _s_swt),
            ("trend_alert", "shadow", _s_trend_alert),
            ("sop_index", "shadow", _s_sop_index),
            ("a1_slippage", "shadow", _s_a1),
            ("shadow_sla", "shadow", _s_shadow_sla),
        ]
        _AUX_TIMING.clear()
        # rc 语义保真(与旧版一致): 仅前4内联函数(sentiment/northbound/momentum/ths)的
        # 返回码参与环节失败判定(可触发 aux 整体重试); 其余子件失败仅记日志不阻断
        # (各有 staleness 守卫/health 探针兜底), 防单件故障放大为整 aux 重试×2。
        _RC_FUNCS = {"sentiment", "northbound", "momentum", "ths"}
        bad = []
        for _name, _group, _fn in registry:
            if _group != "db":
                continue
            _rc = _timed(_name, _fn)
            if _rc and _name in _RC_FUNCS:
                bad.append(_rc)
        from concurrent.futures import ThreadPoolExecutor
        _shadow = [(_name, _fn) for _name, _group, _fn in registry if _group == "shadow"]
        with ThreadPoolExecutor(max_workers=4) as _ex:
            list(_ex.map(lambda x: _timed(x[0], x[1]), _shadow))
        # 中段落盘(链挂死时 aux 数据不丢): 同样按 target 合并, 不覆盖既有 phases 键
        try:
            import json as _json_am
            _tpath = Path(r"D:\zcodesj\data") / "chain_timing_latest.json"
            _prev = {}
            if _tpath.exists():
                try:
                    _prev = _json_am.loads(_tpath.read_text(encoding="utf-8"))
                    if not isinstance(_prev, dict):
                        _prev = {}
                except Exception:
                    _prev = {}
            if _prev.get("target") != t:
                _prev = {"target": t}
            _aux_prev = dict(_prev.get("aux_steps") or {})
            _aux_prev.update(dict(_AUX_TIMING))
            _prev["target"] = t
            _prev["aux_steps"] = _aux_prev
            _prev.setdefault("phases", {})
            _tpath.write_text(
                _json_am.dumps(_prev, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as _e:
            log(f"[timing] aux耗时台账写入失败(不阻断): {_e}")
        return bad[0] if bad else 0


    dispatch = {
        "main": run_main, "feature": run_feature, "v43": run_v43, "v375": run_v375,
        "qfq": run_qfq, "daily_basic": run_daily_basic, "aux": run_aux,
        "eastmoney": run_eastmoney, "qlib_bin": run_qlib_bin, "hist": run_hist, "smc": run_smc,
    }
    failures: list[tuple[str, str]] = []
    acquire_single_instance_lock()  # 2026-09-19: 单实例锁(嵌套实例拒启, 陈旧锁自动覆盖)
    # 2026-09-05: aux 失败延迟重试一次(9/4晚实证: 并行链持 fundamental_data.db 写锁28分钟,
    # aux 两次在 60s busy_timeout 超时处整环节丢弃, 需人工二次补跑; 各步骤均幂等, 重试安全。
    # 仅 aux 重试——长环节(qfq/daily_basic)失败重试代价高且撞锁概率低)
    _AUX_RETRIES, _AUX_RETRY_DELAY = 2, 90
    # 2026-10-23 加固(今晚两实例静默死亡排查盲区): SystemExit/KeyboardInterrupt 等
    # BaseException 不进 phase 层 except Exception, 调度会话 stderr 不留痕 → 死因无从查起
    # (20:47 1号实例/21:19 2号实例均静默, 2号至今无法归因)。落持久尸检日志后原样上抛,
    # 退出码语义不变。
    try:
        for ph in phases:
            touch_single_instance_lock()  # 环节心跳: 刷新锁龄, 挂死(无环节进展)才会被接管
            attempts = _AUX_RETRIES if ph == "aux" else 1
            for attempt in range(1, attempts + 1):
                if attempt > 1:
                    failures[:] = [f for f in failures if f[0] != ph]
                    log(f"[retry] {ph} {_AUX_RETRY_DELAY}s 后重试(第{attempt}次/共{attempts})")
                    time.sleep(_AUX_RETRY_DELAY)
                t0 = time.time()
                log(f"──── 环节 {ph} 开始 ────")
                ok = False
                try:
                    rc = dispatch[ph](target) or 0
                    if rc:
                        failures.append((ph, f"返回码 {rc}"))
                        log(f"──── 环节 {ph} ✗失败 rc={rc} ({time.time() - t0:.0f}s) ────")
                    else:
                        ok = True
                        log(f"──── 环节 {ph} 完成 {time.time() - t0:.0f}s ────")
                except Exception as e:
                    failures.append((ph, f"异常 {type(e).__name__}: {e}"))
                    log(f"──── 环节 {ph} ✗异常 {type(e).__name__}: {e} ({time.time() - t0:.0f}s) ────")
                _PHASE_TIMING[ph] = round(time.time() - t0, 1)
                if ok:
                    break
    except BaseException as _fatal:
        log(f"⛔ 链主循环 BaseException({type(_fatal).__name__}): {_fatal}\n"
            f"{traceback.format_exc()}")
        raise

    # 2026-10-14 根治令: 链耗时台账落盘(环节+aux子件), health 预算告警数据源。
    # 2026-10-14 勘误(3:30 EastmoneyDataRefresh 单环节实弹暴露): 原"整体覆盖"写入会被
    # 单环节 schtasks(--phase eastmoney 等)清掉 22:00 链的完整台账。改按 target 合并:
    #   target 变了→整档换新; 同 target→本次跑过的环节键覆盖旧值, 未跑的键保留;
    #   aux 空跑(非 aux 相位)时保留旧 aux_steps。
    try:
        import json as _json_t
        _tpath = Path(r"D:\zcodesj\data") / "chain_timing_latest.json"
        _prev = {}
        if _tpath.exists():
            try:
                _prev = _json_t.loads(_tpath.read_text(encoding="utf-8"))
                if not isinstance(_prev, dict):
                    _prev = {}
            except Exception:
                _prev = {}
        if _prev.get("target") != target:
            _prev = {"target": target}
        _ph = dict(_prev.get("phases") or {})
        _ph.update(dict(_PHASE_TIMING))
        _aux = dict(_prev.get("aux_steps") or {})
        if _AUX_TIMING:
            _aux.update(dict(_AUX_TIMING))
        _tpath.write_text(
            _json_t.dumps({"target": target, "phases": _ph, "aux_steps": _aux},
                          ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as _e:
        log(f"[timing] 链耗时台账写入失败(不阻断): {_e}")

    _core_behind = verify(target)
    if _core_behind:
        # 2026-09-10 fail-closed: 核心表落后计入 failures → 链 exit 1(当日发现不隔夜),
        # 08:00 兜底/cron 告警链自然接力; 感知层表落后不在此列(health 01:00 STRICT 覆盖)
        failures.append(("verify", "核心表落后: " + ", ".join(_core_behind)))
    # 数据库备份自愈守卫（2026-08-23 备份强制化第二层）:
    #   数据链是 16:00/22:00 必跑入口, 此处检查当日备份产物, 缺失自动补跑 db_backup.py(幂等)。
    # 2026-09-02 修复: schtasks 22:30 的当日备份落在链运行窗口中途(链 22:00-23:00),
    #   ensure_backup 只验产物日期不验内容 → 误报 OK 但快照缺链后写入(daily_basic/龙虎榜等,
    #   9/2 实证)。改为链尾先强制重跑 db_backup(auto=当日小库组, 幂等覆盖), 再 ensure 复核;
    #   大库(dragon_quant族)本无每日组(周日/月初), 不受影响。
    try:
        _bk = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "db_backup.py")],
            cwd=str(Path(__file__).parent), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if _bk.returncode != 0:
            log(f"[backup_guard] db_backup 链尾重跑非零退出 {_bk.returncode}: {_bk.stderr[-200:]}")
    except Exception as _e:
        log(f"[backup_guard] db_backup 链尾重跑异常: {_e}")
    try:
        import ensure_backup as _eb
        _bk_status, _bk_detail = _eb.ensure()
        log(f"[backup_guard] {_bk_status}: {_bk_detail}")
    except Exception as _e:
        log(f"[backup_guard] 守卫异常: {_e}")

    if failures:
        log(f"⚠️ 管道部分失败: {len(failures)}/{len(phases)} 环节异常, 本次运行不算成功:")
        for ph, msg in failures:
            log(f"   ✗ {ph}: {msg}")
        release_single_instance_lock()
        sys.exit(1)
    log("全部完成 ✅")
    release_single_instance_lock()


if __name__ == "__main__":
    main()
