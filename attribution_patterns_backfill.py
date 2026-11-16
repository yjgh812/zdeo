#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
attribution_patterns_backfill.py — 涨停范式库批量回灌（2026-08-15 知微·归因v2.0配套）

【用途】
  用引擎v2.0批量归因历史涨停池，自动写 limit_up_patterns(pattern_type='engine_v2')
  + engine_attribution_log，替代 8/12-8/13 的手工硬编码入库脚本。
  幂等：同日同码 engine_v2 已存在则跳过。经验库不写（避免批量污染手工精选案例）。

【用法】
  python attribution_patterns_backfill.py --date 20260813                # 单日全量
  python attribution_patterns_backfill.py --dates 20260812,20260813,20260814
  python attribution_patterns_backfill.py --date 20260813 --limit 12     # 试点
  python attribution_patterns_backfill.py --dates ... --event big_drop   # 跌停池回灌
"""
import argparse
import json
import os
import socket
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 2026-10-22 主人「按推荐继续」批准: 进程级 socket 默认超时 30s。10/20、10/21 连续
# 两晚个股挂死(688004 limit_up / 10/19 big_drop, 均 20+ 分钟零推进被迫停进程)根因
# = akshare 内部 requests 无 timeout, 挂死 socket 永不抛异常 → _retry 永不触发;
# 本进程内所有未显式指定超时的网络调用(akshare/新闻/cninfo)落到该默认值, 挂死变
# TimeoutError → 逐票超时计数不中断(引擎自身 urllib 显式 6/8s 与 sqlite timeout
# =30s 不受影响)。30s 取建议区间(15-30s)上限, 配合 _retry(n=2) 自动兜一次。
socket.setdefaulttimeout(30)

sys.path.insert(0, r"D:\workbuddyspace")
from unified_stock_attribution import (
    StockAttributionEngine, AkshareSource, save_to_db, ATTR_DB)


def already_saved(code6, date_str, event="limit_up"):
    con = sqlite3.connect(str(ATTR_DB))
    if event == "limit_up":
        n = con.execute(
            "SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=? AND code=? "
            "AND pattern_type='engine_v2'", (date_str, code6)).fetchone()[0]
    else:
        n = con.execute(
            "SELECT COUNT(*) FROM limit_down_risks WHERE trade_date=? AND code=? "
            "AND drop_type='engine_big_drop'", (date_str, code6)).fetchone()[0]
    con.close()
    return bool(n)


def prefixed(code6):
    return ("sh" if code6.startswith(("6",)) else
            "bj" if code6.startswith(("4", "8", "92")) else "sz") + code6


def pool_codes(date_str, event="limit_up"):
    if event == "limit_up":
        df = AkshareSource.zt_pool(date_str)
        col_code, col_name, col_lb = "代码", "名称", "连板数"
    else:
        import akshare as ak
        df = AkshareSource._retry(ak.stock_zt_pool_dtgc_em, date=date_str)
        col_code, col_name, col_lb = "代码", "名称", "连续跌停"
    if df is None or not len(df):
        return []
    out = []
    for _, r in df.iterrows():
        # 2026-08-24 P2修复: 旧写法 col_lb(列名常量)恒真, NaN 时 int() 崩; 判单元格值
        _lb = r[col_lb]
        out.append((str(r[col_code]).zfill(6), str(r[col_name]),
                    int(_lb) if _lb == _lb and str(_lb).strip() not in ('', 'nan') else 0))
    # 连板/连续跌停多的先跑（样本价值高）
    return sorted(out, key=lambda x: -x[2])


QFQ_DB_RO = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"


def limit_sanity_filter(stocks, date_str, event, pct_map=None):
    """入库前涨跌停真实性校验（2026-10-22 主人「现在干」批准）。

    池源对历史日期曾返回错误日期池子（10/19 dtgc 两查 22 vs 46 只实证，「缺失」
    27 只抽 4 全为当日涨停 +10.00~+10.01%）。本过滤按板别涨跌幅限制下限+1.0pp
    容差校验 qfq change_pct，方向不符的票剔除不入引擎：
      big_drop 要求 change_pct <= -(limit-1.0)；limit_up 要求 >= +(limit-1.0)。
    板别：68/30→20%；4/8/92(北交)→30%；主板名含 ST→5%（<2026-07-06）/
    10%（≥2026-07-06，沪深交易所修订交易规则当日施行）；其余→10%。
    qfq 无行情行/无 change_pct 的票放行并计 unverified（防 qfq 缺行误杀真票）。
    pct_map 仅供函数级测试注入，生产路径自动从 qfq 只读加载（单次全日批量）。
    """
    date_int = int(date_str)
    if pct_map is None:
        con = sqlite3.connect(QFQ_DB_RO, uri=True)
        try:
            pct_map = dict(con.execute(
                "SELECT code, change_pct FROM stock_daily_meta_qfq WHERE date=?",
                (date_int,)).fetchall())
        finally:
            con.close()

    # 沪深主板ST/*ST涨跌幅限制变更日（沪深交易所修订交易规则，2026-07-06施行）：
    # 此前5%，此后10%与主板普通股一致；创/科板ST一直20%、北交所30%不受影响。
    ST_LIMIT_RULE_CHANGE = 20260706

    def _limit(code6, name, date_str):
        if code6.startswith(("4", "8", "92")):
            return 30.0
        if code6.startswith(("68", "30")):
            return 20.0
        if "ST" in (name or "").upper():
            return 5.0 if int(date_str) < ST_LIMIT_RULE_CHANGE else 10.0
        return 10.0

    kept, rejected, unverified = [], [], 0
    for code6, name, lb in stocks:
        pct = pct_map.get(code6)
        if pct is None or pct != pct:      # 缺行/NULL/NaN → 放行防误杀
            unverified += 1
            kept.append((code6, name, lb))
            continue
        limit = _limit(code6, name, date_str)
        if event == "big_drop":
            ok = pct <= -(limit - 1.0)
        else:
            ok = pct >= (limit - 1.0)
        if ok:
            kept.append((code6, name, lb))
        else:
            rejected.append((code6, name, float(pct), limit))
    return kept, rejected, unverified


def main():
    ap = argparse.ArgumentParser(description="涨停范式库批量回灌(引擎v2)")
    ap.add_argument("--date", type=str)
    ap.add_argument("--dates", type=str, help="逗号分隔多日")
    ap.add_argument("--event", default="limit_up", choices=["limit_up", "big_drop"])
    ap.add_argument("--limit", type=int, default=0, help="每日最多处理N只(0=不限)")
    args = ap.parse_args()

    dates = []
    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    elif args.date:
        dates = [args.date]
    if not dates:
        ap.print_help()
        return 2

    stats = {"done": 0, "skipped": 0, "failed": 0, "timed_out": 0, "rejected": 0, "unverified": 0}
    t0 = time.time()
    failed_dates = []
    for dt in dates:
        try:
            stocks = pool_codes(dt, args.event)
            if args.limit:
                stocks = stocks[:args.limit]
            print(f"[{dt}] {args.event}池 {len(stocks)} 只（连板优先）")
            # 2026-10-22 真实性校验(主人「现在干」批准): 池源历史日期污染防线,
            # 方向不符票剔除不入引擎(见 limit_sanity_filter docstring)。
            stocks, rejected, unverified = limit_sanity_filter(stocks, dt, args.event)
            for c6, n, pct, lim in rejected:
                print(f"  REJECT {c6} {n}: change_pct={pct:+.2f}% 非当日"
                      f"{'跌' if args.event == 'big_drop' else '涨'}停(板别阈值{lim:.0f}%±1pp)")
            print(f"[{dt}] 真实性校验: 拒绝{len(rejected)} 未验{unverified} 剩余{len(stocks)} 只")

            # 2026-10-20 优化(「修复,优化,迭代」轮): 逐票引擎归因为网络型负载
            # (实测limit_up≈5.8s/只, big_drop≈50s/只), 并行化近线性收益。
            # 每票独立引擎实例=线程隔离; run()内无DB写, save_to_db 统一主线程
            # 串行写(规避SQLite锁竞争); WORKERS env 可调, 默认4(源限频保守)。
            workers = max(1, int(os.environ.get("PATTERNS_BACKFILL_WORKERS", "4")))

            def _attr_one(item):
                code6, name, lb = item
                tag = f"{code6} {name}" + (f" {lb}板" if lb else "")
                try:
                    if already_saved(code6, dt, args.event):
                        return ("skip", tag, None)
                    code = prefixed(code6)
                    eng = StockAttributionEngine(code, args.event, dt)
                    result = eng.run(use_cache=False)
                    result["_zt_row"] = eng.data.get("zt_row")
                    result["_sector_name"] = eng.data.get("sector_name")
                    return ("ok", tag, result)
                except Exception as e:
                    es = f"{type(e).__name__} {str(e)[:80]}"
                    es_low = str(e).lower()
                    if (isinstance(e, TimeoutError) or "Timeout" in type(e).__name__
                            or "timeout" in es_low or "timed out" in es_low):
                        return ("timeout", tag, es)
                    return ("err", tag, es)

            done = skip = failed = timed_out = 0
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(_attr_one, it) for it in stocks]
                for f in as_completed(futs):
                    st, tag, payload = f.result()
                    if st == "skip":
                        skip += 1
                        print(f"  SKIP {tag}")
                    elif st == "timeout":
                        timed_out += 1
                        print(f"  TIMEOUT {tag}: {payload}")
                    elif st == "err":
                        failed += 1
                        print(f"  ERR {tag}: {payload}")
                    else:
                        save_to_db(payload, seats=None, write_experience=False)
                        done += 1
                        cf = payload.get("counterfactual") or {}
                        print(f"  OK {tag} → {payload.get('main_attribution')}/"
                              f"{payload.get('sub_attribution')} AR={cf.get('ar')} "
                              f"置信{payload.get('confidence')}")
            stats["done"] += done
            stats["skipped"] += skip
            stats["failed"] += failed
            stats["timed_out"] += timed_out
            stats["rejected"] += len(rejected)
            stats["unverified"] += unverified
        except Exception as e:
            # 2026-08-24 修复: 单日池拉取失败不再崩掉整批（后续 --dates 日期继续处理）
            print(f'[warn] {dt} 回灌失败(继续后续日期): {type(e).__name__}: {e}', file=sys.stderr)
            failed_dates.append(dt)
    print("=" * 50)
    print(f"回灌完成: 成功{stats['done']} 跳过{stats['skipped']} 失败{stats['failed']} "
          f"超时{stats['timed_out']} 拒{stats['rejected']}(未验{stats['unverified']}) "
          f"耗时{time.time()-t0:.0f}s → {datetime.now().isoformat(timespec='seconds')}")
    if failed_dates:
        print(f"[warn] 失败日期(可用 --dates 重跑): {failed_dates}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
