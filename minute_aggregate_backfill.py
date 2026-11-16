# -*- coding: utf-8 -*-
"""
minute_aggregate_backfill.py — vipdoc 本地 1 分钟聚合补库(30m/60m)
==================================================================
背景(2026-10-29): tulong_phase1_minute.db(mootdx 网络采集线)停更于 2026-07-20。
pytdx/mootdx 协议层在本机已系统性失效(20 台服务器 TCP 通但取数全空, 而通达信官方
客户端正常取数 = 老 pytdx 协议被服务器弃用), 网络线不可复活。
主人令: 补 60m/30m 至 2026-10-29。vipdoc .lc1 已含 2026-01-07 起 1 分钟原始
(主人手动更新通达信即保鲜), 本脚本 = 本地聚合正源补库, 不依赖失效网络线。

口径:
  - 数据源: vipdoc .lc1 (1 分钟, 2026-01-07 起) / .lc5 (5 分钟原始)
  - 聚合频 30m/60m: 交易分钟序号分桶(上午 idx 1..120, 下午 121..240, 午休不占桶),
    60m=4 桶/日(10:30/11:30/14:00/15:00), 30m=8 桶/日, 桶末整点标记 = mootdx 旧口径
  - 原始频 1m/5m: vipdoc 对应周期文件原样入库(不聚合)
  - datetime 统一 'YYYY-MM-DD HH:MM:SS'(对齐旧库 mootdx 格式)
  - vol/amount 单位: lc1 聚合 vs 旧库同日对照倍数 1.0, 已对齐无需换算
写入(幂等):
  - 窗口替换: DELETE freq IN batch AND date >= since AND code IN batch, 再插入
  - since 缺省 = WINDOW_START(20260107, 全量重建); --since YYYYMMDD = 增量模式(日常推荐)
  - 保留 60m 2025-08-25~2026-01-06 mootdx 旧史
验收:
  - 内置抽样: 每频末桶 close/volume 合计对照 stock_daily_meta 不复权日线 (±0.5% 容差)

用法:
  python minute_aggregate_backfill.py                       # universe 全量 30m+60m
  python minute_aggregate_backfill.py --freqs 1m 5m 30m 60m # 四频全量
  python minute_aggregate_backfill.py --since 20261103      # 增量模式: 只补 11/3 之后(日常更新推荐)
  python minute_aggregate_backfill.py --since 20261103 --freqs 1m 5m 30m 60m  # 四频增量
  python minute_aggregate_backfill.py --dry-run             # 只打印计划不写库
  python minute_aggregate_backfill.py --codes 600519,000001 # 指定股票先验证
"""
import argparse
import json
import sqlite3
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.append(r"D:\zcodesj\chanlun_engine")

TARGET_DB = Path(r"D:\workbuddyspace\factor_engine\data\tulong_phase1_minute.db")
DAILY_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")
LOG_PATH = Path(r"D:\zcodesj\reports\minute_aggregate_backfill.log")

# 聚合窗口起点 = .lc1 可用起点(2026-01-07), 同时是窗口删除下界(保留 mootdx 更早旧史)
WINDOW_START = 20260107
# 聚合频: 1分钟原始按 N 分钟分桶; 原始频: vipdoc 对应周期文件原样入库(不聚合)
AGG_FREQS = {"30m": 30, "60m": 60}
RAW_FREQS = {"1m": "read_min1", "5m": "read_min5"}
ALL_FREQS = list(AGG_FREQS) + list(RAW_FREQS)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _minute_of(hhmm: int):
    """HHMM(分钟末标记) -> 日内分钟数; 非交易分钟返回 None。"""
    t = (hhmm // 100) * 60 + hhmm % 100
    if 571 <= t <= 690:      # 09:31~11:30
        return t
    if 781 <= t <= 900:      # 13:01~15:00
        return t
    return None


def _trading_idx(hhmm: int):
    """日内分钟数 -> 交易分钟序号 1..240; 非法返回 None。"""
    t = _minute_of(hhmm)
    if t is None:
        return None
    return t - 570 if t <= 690 else 120 + t - 780


def _mark_hhmm(idx: int) -> int:
    """交易分钟序号 -> 桶末 HHMM 标记 (idx=60 -> 1030, 120 -> 1130, 180 -> 1400, 240 -> 1500)。"""
    t = idx + 570 if idx <= 120 else idx + 660
    return (t // 60) * 100 + t % 60


def _dt_str(date: int, hhmm: int) -> str:
    """date=20261029, hhmm=1500 -> '2026-10-29 15:00:00' (对齐旧库 mootdx datetime 格式)。"""
    return f"{date // 10000:04d}-{date // 100 % 100:02d}-{date % 100:02d} {hhmm // 100:02d}:{hhmm % 100:02d}:00"


def _raw_rows_to_kline(rows, freq: str, code: str, since: int):
    """vipdoc 原始分钟行(1m/.lc1 或 5m/.lc5) -> stock_minute 行列表(原样转换, 不聚合)。"""
    out = []
    for r in rows:
        if r["date"] < since:
            continue
        hhmm = r["time"]
        out.append({
            "code": code,
            "datetime": _dt_str(r["date"], hhmm),
            "date": r["date"],
            "time_sec": (hhmm // 100) * 3600 + (hhmm % 100) * 60,
            "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"],
            "volume": r["vol"], "amount": r["amount"],
            "freq": freq,
        })
    return out


def aggregate_1m(rows, freq: str):
    """1 分钟原始行 -> N 分钟 K 线列表。rows: tdx_reader.read_min1 输出(dict)。"""
    n = AGG_FREQS[freq]
    buckets = {}
    for r in rows:
        hhmm = r["time"]
        idx = _trading_idx(hhmm)
        if idx is None:
            continue
        b = (idx + n - 1) // n  # 桶号 = ceil(idx/n)
        key = (r["date"], b)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = {
                "date": r["date"], "bucket": b, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"], "volume": r["vol"], "amount": r["amount"],
            }
        else:
            cur["high"] = max(cur["high"], r["h"])
            cur["low"] = min(cur["low"], r["l"])
            cur["close"] = r["c"]
            cur["volume"] += r["vol"]
            cur["amount"] += r["amount"]
    out = []
    for (date, b), cur in sorted(buckets.items()):
        end_idx = b * n
        if end_idx > 240:  # 理论不可达, 防御
            continue
        hhmm = _mark_hhmm(end_idx)
        dt = _dt_str(date, hhmm)
        out.append({
            "code": None,  # 由调用方填
            "datetime": dt,
            "date": date,
            "time_sec": (hhmm // 100) * 3600 + (hhmm % 100) * 60,
            "open": cur["open"], "high": cur["high"], "low": cur["low"], "close": cur["close"],
            "volume": cur["volume"], "amount": cur["amount"],
            "freq": freq,
        })
    return out


def process_one(args):
    """子进程: 读 vipdoc 原始分钟 -> 聚合频(30m/60m 由 lc1 聚) / 原始频(1m/5m 原样)。
    返回 (code, {freq: rows}, err)。"""
    code, freqs, since = args
    try:
        from tdx_reader import read_min1, read_min5
        rows_by_src = {}
        if any(f in freqs for f in ("1m", "30m", "60m")):
            rows_by_src["lc1"] = read_min1(code=code)
        if "5m" in freqs:
            rows_by_src["lc5"] = read_min5(code=code)
        src = next(iter(rows_by_src.values()), None)
        if not src:
            return (code, {}, "vipdoc_empty")
        if all(len(v) == 0 for v in rows_by_src.values()):
            return (code, {}, "vipdoc_empty")
        out = {}
        for f in freqs:
            if f in AGG_FREQS:
                rows = [r for r in rows_by_src["lc1"] if r["date"] >= since]
                out[f] = aggregate_1m(rows, f)
                for r in out[f]:
                    r["code"] = code
            else:
                out[f] = _raw_rows_to_kline(rows_by_src["lc5" if f == "5m" else "lc1"], f, code, since)
        if not any(out.values()):
            # 2026-11-03: 增量模式下"源文件在但窗口内无新数据"(停牌股) = 正常跳过而非失败
            return (code, {}, None)
        return (code, out, None)
    except Exception:
        return (code, {}, traceback.format_exc(limit=2))


def load_universe(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='universe'").fetchone()
    if row is None:
        raise SystemExit("meta.universe 不存在, 请先运行 tulong_phase1_minute_pipeline.py 初始化")
    uni = json.loads(row[0])
    return sorted({str(u["code"]).zfill(6) for u in uni})


def write_db(conn, all_rows, freqs, since):
    """窗口替换写入: 只删本次 batch code 的 since 起之后旧行, 保留更早旧史。"""
    codes = sorted({r["code"] for r in all_rows})
    before = conn.execute("SELECT COUNT(*) FROM stock_minute").fetchone()[0]
    ph_codes = ",".join(["?"] * len(codes))
    ph_freqs = ",".join(["?"] * len(freqs))
    conn.execute(
        f"DELETE FROM stock_minute WHERE freq IN ({ph_freqs}) AND date >= ? AND code IN ({ph_codes})",
        list(freqs) + [since] + codes,
    )
    conn.commit()
    # 2026-11-03加固(11/3晚第八崩实锤): 原实现列表推导复制56万dict/批(~1GB内存尖峰,
    # 63GB空闲内存下MemoryError反复发作) → 生成器流式消费, 零复制零尖峰, 语义不变。
    def _iter_rows():
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        for r in all_rows:
            r["fetched_at"] = ts
            yield r

    conn.executemany(
        """INSERT OR REPLACE INTO stock_minute
           (code, datetime, date, time_sec, open, high, low, close, volume, amount, freq, fetched_at)
           VALUES (:code, :datetime, :date, :time_sec, :open, :high, :low, :close,
                   :volume, :amount, :freq, :fetched_at)""",
        _iter_rows(),
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM stock_minute").fetchone()[0]
    return before, after


def validate_sample(conn, codes, freqs, tail_days=3, tol=0.005):
    """抽样验收: 末桶 close / volume 合计 对照 stock_daily_meta 不复权日线。"""
    dconn = sqlite3.connect(f"file:{DAILY_DB}?mode=ro", uri=True)
    checks, fails = 0, []
    sample = codes[-5:] if len(codes) >= 5 else codes
    for code in sample:
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM stock_minute WHERE code=? AND freq=? ORDER BY date DESC LIMIT ?",
            (code, freqs[0], tail_days))]
        for d in days:
            drow = dconn.execute(
                "SELECT close, volume FROM stock_daily_meta WHERE code=? AND date=?", (code, d)
            ).fetchone()
            if drow is None:
                continue
            # 每个 freq 单独对照日线(30m/60m 是同源两份切片, 联合 SUM 会翻倍)
            for f in freqs:
                agg = conn.execute(
                    """SELECT MAX(CASE WHEN datetime LIKE '%15:00:00' THEN close END),
                              SUM(volume) FROM stock_minute
                       WHERE code=? AND freq=? AND date=?""",
                    (code, f, d)).fetchone()
                last_close, vol_sum = agg
                if last_close is None or vol_sum is None:
                    fails.append((code, d, f"{f} no_last_bucket"))
                    continue
                checks += 1
                if abs(last_close - drow[0]) / drow[0] > tol:
                    fails.append((code, d, f"{f} close {last_close} vs daily {drow[0]}"))
                if drow[1] and abs(vol_sum - drow[1]) / drow[1] > tol:
                    fails.append((code, d, f"{f} vol {vol_sum} vs daily {drow[1]}"))
    dconn.close()
    return checks, fails


def main():
    parser = argparse.ArgumentParser(description="vipdoc 分钟补库: 聚合 30m/60m, 原样 1m/5m")
    parser.add_argument("--freqs", nargs="+", default=["30m", "60m"], choices=ALL_FREQS)
    parser.add_argument("--codes", default="", help="逗号分隔股票代码, 省略=universe 全量")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=50, help="每批写库股票数(控内存)")
    parser.add_argument("--since", type=int, default=0,
                        help="增量模式起始日 YYYYMMDD: 只处理/替换该日之后数据; 缺省=全量重建(20260107)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log(f"=== minute_aggregate_backfill 启动 freqs={args.freqs} window_start={WINDOW_START} ===")
    if not TARGET_DB.exists():
        raise SystemExit(f"目标库不存在: {TARGET_DB}")

    # 2026-11-03加固(11/3凌晨连崩六次实证): 库已于11/3转WAL(诊断恢复时设置), 但本脚本
    # 连接无busy超时——与只读探针/恢复连接并发窗口会直接SQLITE_BUSY硬崩。timeout=120
    # 让锁冲突等待而非崩溃; WAL下读写可并存, 该超时是并发安全网而非串行化。
    conn = sqlite3.connect(str(TARGET_DB), timeout=120)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or load_universe(conn)
    log(f"股票数: {len(codes)} | {TARGET_DB}")

    for f in args.freqs:
        r = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT code) FROM stock_minute WHERE freq=?",
            (f,)).fetchone()
        log(f"更新前 {f}: 日期 {r[0]}~{r[1]}, 覆盖 {r[2]} 只")

    if args.dry_run:
        log("[DRY-RUN] 计划: 窗口替换写入上述 freq, 不执行。退出。")
        return

    t0 = time.time()
    failed, inserted_total = [], 0

    # 2026-11-03增量模式(主人令「修复优化迭代」延续, 九连崩根因=全窗口重写50GB级IO churn):
    # --since 显式日期 → 只处理/替换该日之后的数据(日常更新50MB事务, 分钟级完成);
    # 缺省=WINDOW_START 保持全量重建语义不变。停牌股窗口内无新数据=正常跳过不再计失败。
    since = args.since if args.since else WINDOW_START
    log(f"窗口起点(since) = {since}" + ("  [增量模式]" if args.since else "  [全量重建模式]"))
    if args.since:
        r_min = conn.execute(
            "SELECT MIN(d) FROM (SELECT MAX(date) AS d FROM stock_minute WHERE freq IN (%s) GROUP BY freq)"
            % ",".join(["?"] * len(args.freqs)),
            args.freqs).fetchone()[0]
        if r_min and r_min >= args.since:
            log(f"提示: {args.freqs} 库内最新日 {r_min} >= since {args.since}, 本轮将仅刷新重写该日数据")

    def flush(buf):
        nonlocal inserted_total
        rows = []
        for _code, _out in buf:
            for f in args.freqs:
                rows.extend(_out.get(f, []))
        if rows:
            b0, a0 = write_db(conn, rows, args.freqs, since)
            inserted_total += a0 - b0
            log(f"  批次写库: {len(buf)} 只 {len(rows)} 行 (库 {b0} -> {a0})")

    batch_buf, tasks = [], [(c, args.freqs, since) for c in codes]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for i, fut in enumerate(as_completed(ex.submit(process_one, t) for t in tasks), 1):
            code, out, err = fut.result()
            if err:
                failed.append((code, err))
                if "Traceback" not in err:  # 结构化原因才刷屏, 堆栈只进日志
                    log(f"FAIL {code}: {err}")
            else:
                batch_buf.append((code, out))
                if len(batch_buf) >= args.batch_size:
                    flush(batch_buf)
                    batch_buf = []
            if i % 100 == 0 or i == len(tasks):
                log(f"进度 {i}/{len(tasks)}")

    if batch_buf:
        flush(batch_buf)
    if failed:
        log(f"失败 {len(failed)} 只: {failed[:10]}{'...' if len(failed) > 10 else ''}")
    if inserted_total == 0 and not failed:
        raise SystemExit("无任何输出且零写入, 中止")
    log(f"累计净增 {inserted_total} 行")

    checks, fails = validate_sample(conn, codes, args.freqs)
    log(f"抽样验收: {checks} 组, 不符 {len(fails)} 组 {fails[:5] if fails else ''}")

    for f in args.freqs:
        r = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT code), COUNT(*) FROM stock_minute WHERE freq=?",
            (f,)).fetchone()
        log(f"更新后 {f}: 日期 {r[0]}~{r[1]}, 覆盖 {r[2]} 只, {r[3]} 行")

    conn.close()
    log(f"=== 完成 耗时 {time.time() - t0:.0f}s, 失败 {len(failed)} 只, 验收不符 {len(fails)} 组 ===")
    if failed or fails:
        sys.exit(2)


if __name__ == "__main__":
    main()
