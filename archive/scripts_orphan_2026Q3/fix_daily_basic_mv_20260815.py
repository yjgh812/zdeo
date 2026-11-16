#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_daily_basic_mv_20260815.py — 修复 daily_basic.total_mv/circ_mv 写入损坏
==========================================================================
背景（2026-08-15 排查结论，详见 reports/DATA_PIPELINE_FIX_20260815.md）:
  2026-05-29 起多源降级写入器（tools/backfill_daily_basic.py 家族）在 tushare 断供后
  降级到"仅价格"源（新浪/tdx_dayfile/meta_db），除 close 外其余字段全部写 0.0/NULL，
  造成 total_mv/circ_mv/total_share 等大面积损坏：
    - 20260529 / 20260602 / 20260603 三天全量损坏（且混入 ETF(159/51x)/可转债(16x)/
      板块指数(880) 等非个股代码；6/2、6/3 还各有一份横线日期'2026-06-02'/'2026-06-03'
      影子行，共 13394 行）
    - 20260601 整日缺失（hsj 有 8817 行）
    - 20260810 缺 1909 行（baostock 当日"网络接收错误"跳过，见 backfill 日志）
    - 6/4 起 ~19 只停牌股/新股因"股本取自最新一行"被上游 0 值污染 → mv 持续为 0
  baostock 2026-08-15 实测登录被拉黑(10001011 黑名单用户)，无法重拉 peTTM/turn，
  故按任务预案采用计算回填：
    口径 = total_mv(万元) = 不复权close(元, dragon_quant.db.stock_daily_hsj, TDX同源)
                          × total_share(万股, 损坏窗口前最后一个 total_share>0 已知值顺延)
          circ_mv(万元)  = close × float_share(万股, 同上)
  （与 backfill_daily_basic_20260808.py 起的库内既有口径一致；股本期内基本不变。
    新股无历史股本时用腾讯 qt.gtimg.cn 现值总股本/流通股本(vals[73]/vals[72], 股)换算万股。）
  turnover_rate/pe_ttm/pb/ps_ttm 在损坏日仍为 0 —— baostock 恢复后可另行回补，
  本次如实保留并在报告说明。

用法:
    python fix_daily_basic_mv_20260815.py            # dry-run 只打印计划
    python fix_daily_basic_mv_20260815.py --execute  # 实际写库（幂等，可重跑）

铁律: 写库仅限 daily_basic 表的 total_share/float_share/total_mv/circ_mv(+close兜底)
      及缺失日行插入; 改前自动备份受影响原始行到 D:/zcodesj/archive/。
"""
import argparse
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

FUND_DB = Path(r"D:\workbuddyspace\databases\fundamental_data.db")
MAIN_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
ARCHIVE = Path(r"D:\zcodesj\archive")
STAMP = "20260815"

CORRUPT_DAYS = ["20260529", "20260602", "20260603"]      # 紧凑日期全损日
SHADOW_DAYS = ["2026-06-02", "2026-06-03"]               # 横线日期影子行
MISSING_DAYS = ["20260601", "20260810"]                  # 整日/大额缺行日
LAST_GOOD = "20260528"                                   # 最后一个健康交易日(tushare时代)
RESIDUAL_SINCE = "20260604"                              # 残留零值起点
COLS = ["ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f",
        "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
        "total_share", "float_share", "free_share", "total_mv", "circ_mv"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def canonical(ts: str) -> bool:
    """个股代码判定：6位纯数字且前缀属于 A 股个股段（排除 ETF/债/板块指数/北交）。"""
    code = ts.split(".")[0]
    if "." not in ts or len(code) != 6 or not code.isdigit():
        return False
    return code[:2] in ("60", "00", "30") or code[:3] in ("688", "689")


def fetch_tencent_shares(ts_codes):
    """腾讯 qt.gtimg.cn: vals[73]=总股本(股), vals[72]=流通股本(股) → 万股。"""
    sym = [("sh" if t.endswith(".SH") else "sz") + t.split(".")[0] for t in ts_codes]
    out = {}
    for i in range(0, len(sym), 60):
        url = "http://qt.gtimg.cn/q=" + ",".join(sym[i:i + 60])
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read().decode("gbk", errors="ignore")
        except Exception as e:
            log(f"  [WARN] 腾讯批次失败({i}): {e}")
            continue
        for line in data.strip().split(";"):
            line = line.strip()
            if "~" not in line:
                continue
            key = line.split("=", 1)[0].replace("v_", "").strip()
            vals = line.split("=", 1)[1].strip('"').split("~")
            if len(vals) < 80:
                continue
            ts = key[2:] + (".SH" if key.startswith("sh") else ".SZ")
            try:
                tot, flt = float(vals[73]) / 1e4, float(vals[72]) / 1e4   # 股→万股
                if tot > 0:
                    out[ts] = (tot, flt)
            except (ValueError, IndexError):
                continue
    return out


def dump_rows(fund, fname, where_sql, params=()):
    """把将受影响的原始行导出为可回放的 INSERT 语句（备份）。"""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(ARCHIVE / fname, "w", encoding="utf-8") as f:
        f.write("-- backup before fix_daily_basic_mv_20260815\n"
                "-- replay: 粘贴执行即可恢复原始行(INSERT OR REPLACE)\n")
        for row in fund.execute(f"SELECT {','.join(COLS)} FROM daily_basic WHERE {where_sql}",
                                params):
            vals = ",".join("NULL" if v is None else
                            (f"'{v}'" if isinstance(v, str) else repr(v)) for v in row)
            f.write(f"INSERT OR REPLACE INTO daily_basic VALUES ({vals});\n")
            n += 1
    log(f"  备份 {n} 行 → archive/{fname}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="实际写库（默认 dry-run）")
    args = ap.parse_args()
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log(f"=== fix_daily_basic_mv mode={mode} ===")

    fund = sqlite3.connect(str(FUND_DB), timeout=120)
    fund.execute("PRAGMA busy_timeout=120000")
    hsj = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)

    # ── 0. 基准数据 ──────────────────────────────────────────────
    # 0.1 标准股票池: 邻近健康日并集（5/28 ∪ 6/4，去非个股）
    canon = set()
    for d in (LAST_GOOD, "20260604"):
        canon |= {r[0] for r in fund.execute(
            "SELECT ts_code FROM daily_basic WHERE trade_date=?", (d,))}
    canon = {t for t in canon if canonical(t)}
    log(f"标准股票池(5/28 ∪ 6/4, 去非个股): {len(canon)} 只")

    # 0.2 待修股票全集（用于动态识别缺股本的新股）
    need_ts = set()
    for d in CORRUPT_DAYS:
        need_ts |= {r[0] for r in fund.execute(
            "SELECT ts_code FROM daily_basic WHERE trade_date=?", (d,))} & canon
    need_ts |= {r[0] for r in fund.execute(
        "SELECT DISTINCT ts_code FROM daily_basic WHERE trade_date>=? "
        "AND (total_mv IS NULL OR total_mv=0)", (RESIDUAL_SINCE,))}

    # 0.3 股本基准: 每股 ≤LAST_GOOD 最后一行 total_share>0
    shares = {}
    for ts, tot, flt in fund.execute(
            "SELECT ts_code, total_share, float_share FROM daily_basic "
            "WHERE trade_date<=? AND total_share>0 "
            "AND (ts_code, trade_date) IN (SELECT ts_code, MAX(trade_date) FROM daily_basic "
            "WHERE trade_date<=? AND total_share>0 GROUP BY ts_code)",
            (LAST_GOOD, LAST_GOOD)):
        shares[ts] = (tot, flt or tot)

    # 0.4 无历史股本者(窗口内新股/停牌污染股) → 腾讯现值股本
    miss = sorted(t for t in need_ts if t not in shares)
    log(f"股本基准(≤{LAST_GOOD} total_share>0): {len(shares)} 只; 待腾讯补股本: {miss}")
    if miss:
        got = fetch_tencent_shares(miss)
        for t in miss:
            if t in got:
                shares[t] = got[t]
        still = [t for t in miss if shares.get(t, (0,))[0] <= 0]
        log(f"腾讯取得 {len([t for t in miss if t in got])}/{len(miss)}; 无股本(mv置NULL/保留): {still}")

    # 0.5 hsj 价格
    px_map = {}
    for d in CORRUPT_DAYS + MISSING_DAYS:
        px_map[d] = {r[0]: r[1] for r in hsj.execute(
            "SELECT code, close FROM stock_daily_hsj WHERE date=? AND close>0", (int(d),))}

    plan = {"mode": mode}

    # ── 1. 删除横线日期影子行 ───────────────────────────────────
    n = fund.execute("SELECT COUNT(*) FROM daily_basic WHERE trade_date IN (?,?)",
                     SHADOW_DAYS).fetchone()[0]
    plan["shadow_deleted"] = n
    log(f"1) 删除影子日期行 {SHADOW_DAYS}: {n} 行")
    if args.execute and n:
        dump_rows(fund, f"daily_basic_shadow_{STAMP}.sql",
                  "trade_date IN ('2026-06-02','2026-06-03')")
        fund.execute("DELETE FROM daily_basic WHERE trade_date IN (?,?)", SHADOW_DAYS)

    # ── 2. 三个全损日: 删非个股行 + close×股本 重算 4 字段 ────────
    junk, upd, close_fix, nulled, noprice = [], [], 0, 0, 0
    for d in CORRUPT_DAYS:
        rows = fund.execute(
            "SELECT ts_code, close FROM daily_basic WHERE trade_date=?", (d,)).fetchall()
        j = [(t, d) for t, _ in rows if t not in canon]
        junk += j
        log(f"2) {d}: 现有 {len(rows)} 行, 非个股待删 {len(j)} 行")
        for ts, close in rows:
            if ts not in canon:
                continue
            if not close or close <= 0:
                px = px_map[d].get(ts.split(".")[0])
                if px:
                    close, close_fix = px, close_fix + 1
                else:
                    noprice += 1
                    continue
            tot, flt = shares.get(ts, (0.0, 0.0))
            if tot > 0:
                upd.append((tot, flt, close * tot, close * flt, ts, d))
            else:
                nulled += 1
    plan.update(junk_deleted=len(junk), mv_updated=len(upd),
                close_fixed=close_fix, mv_nulled=nulled, skipped_no_price=noprice)
    log(f"2) 三天合计: 删非个股 {len(junk)}, 重算mv {len(upd)}, close兜底 {close_fix}, "
        f"无股本保留0 {nulled}, 无价格跳过 {noprice}")
    if args.execute:
        dump_rows(fund, f"daily_basic_corrupt3d_{STAMP}.sql",
                  "trade_date IN ('20260529','20260602','20260603')")  # 删/改前全量备份
        if junk:
            fund.executemany("DELETE FROM daily_basic WHERE ts_code=? AND trade_date=?", junk)
        if upd:
            fund.executemany(
                "UPDATE daily_basic SET total_share=?, float_share=?, total_mv=?, circ_mv=? "
                "WHERE ts_code=? AND trade_date=?", upd)

    # ── 3. 补缺失日 6/1、8/10 ───────────────────────────────────
    inserts = []
    for d in MISSING_DAYS:
        if d == "20260601":
            pool = canon
        else:  # 20260810: 用 8/7 ∪ 8/11 现有池
            pool = set()
            for dd in ("20260807", "20260811"):
                pool |= {r[0] for r in fund.execute(
                    "SELECT ts_code FROM daily_basic WHERE trade_date=?", (dd,))}
            pool = {t for t in pool if canonical(t)}
        have = {r[0] for r in fund.execute(
            "SELECT ts_code FROM daily_basic WHERE trade_date=?", (d,))}
        px = px_map[d]
        n_ins = 0
        for ts in sorted(pool - have):
            c = px.get(ts.split(".")[0])
            if not c:
                continue
            tot, flt = shares.get(ts, (0.0, 0.0))
            mv = (c * tot if tot > 0 else 0.0)
            cmv = (c * flt if tot > 0 else 0.0)
            inserts.append((ts, d, c, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                            tot, flt, 0.0, mv, cmv))
            n_ins += 1
        log(f"3) 补 {d}: 目标池 {len(pool)}, 已有 {len(have)}, 插入 {n_ins} 行")
    plan["rows_inserted"] = len(inserts)
    if args.execute and inserts:
        with open(ARCHIVE / f"daily_basic_insert_undo_{STAMP}.sql", "w", encoding="utf-8") as f:
            for r in inserts:
                f.write(f"DELETE FROM daily_basic WHERE ts_code='{r[0]}' AND trade_date='{r[1]}';\n")
        fund.executemany(
            "INSERT OR REPLACE INTO daily_basic VALUES (" + ",".join(["?"] * 18) + ")", inserts)

    # ── 4. 残留零值股(≥6/4 total_mv=0/NULL) ──────────────────────
    resid = fund.execute(
        "SELECT ts_code, trade_date, close FROM daily_basic "
        "WHERE trade_date>=? AND (total_mv IS NULL OR total_mv=0)", (RESIDUAL_SINCE,)).fetchall()
    upd2 = []
    for ts, d, close in resid:
        tot, flt = shares.get(ts, (0.0, 0.0))
        if tot > 0 and close and close > 0:
            upd2.append((tot, flt, close * tot, close * flt, ts, d))
    plan["residual_rows"] = len(resid)
    plan["residual_updated"] = len(upd2)
    log(f"4) 残留零值行 {len(resid)}, 本次可修 {len(upd2)}")
    if args.execute and upd2:
        dump_rows(fund, f"daily_basic_residual_{STAMP}.sql",
                  "trade_date>='20260604' AND (total_mv IS NULL OR total_mv=0)")
        fund.executemany(
            "UPDATE daily_basic SET total_share=?, float_share=?, total_mv=?, circ_mv=? "
            "WHERE ts_code=? AND trade_date=?", upd2)

    # ── 5. 收尾 ─────────────────────────────────────────────────
    if args.execute:
        fund.commit()
    bad = fund.execute(
        "SELECT COUNT(*) FROM daily_basic WHERE trade_date BETWEEN '20260529' AND '20260814' "
        "AND length(trade_date)=8 AND (total_mv IS NULL OR total_mv=0)").fetchone()[0]
    log(f"验证: 窗口内剩余 total_mv=0/NULL = {bad} 行")
    for d in ["20260529", "20260601", "20260602", "20260603", "20260604", "20260810", "20260814"]:
        row = fund.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_mv>0),0) FROM daily_basic WHERE trade_date=?",
            (d,)).fetchone()
        log(f"  {d}: rows={row[0]}, mv>0={row[1]}")
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    (ARCHIVE / f"fix_daily_basic_mv_{STAMP}_stats.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"统计 → archive/fix_daily_basic_mv_{STAMP}_stats.json | mode={mode}")
    fund.close()
    hsj.close()


if __name__ == "__main__":
    main()
