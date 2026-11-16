#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_sz_000xxx.py — 被上证系指数顶行的深市主板股 三库真值重建（修复工具 V1）
================================================================
背景（reports/META_QFQ_POLLUTION_ESCALATION_20260826.md 十九节根因反转）：
  tulong Phase1 曾把 sh/lday 的 336 个上证系指数文件当作深市股票写入，
  UNIQUE(code,date)+REPLACE 使同码深市股真实行情行被指数点位顶替。
  实锤受灾面：532,948 条 / 309 只（meta==sh 指数点位 ±3%）。

重建策略：
  A. 幽灵码（vipdoc/sz 无 sz{code}.day，此 code 纯属上证指数文件名，如 000300/000905）：
     raw/meta/qfq 三库该 code 全部行删除（真实世界不存在该“深市股票”）。
  B. 真股（sz{code}.day 存在）：以 vipdoc 原始日线全量重放 raw+meta（重算
     change_pct/change_amount/amplitude，与 tulong Phase1 口径一致），qfq 走
     fix_qfq_rebuild.rebuild_one（vipdoc+新浪因子全量重算，自带锚点守卫）。
  期间临时摘除 pollution_guard INSERT 触发器（防极端送转边缘误拦），完毕幂等装回。

用法:
  python rebuild_sz_000xxx.py --scan                 # 盘点：幽灵码/真股受灾分类清单
  python rebuild_sz_000xxx.py --run --batch 50       # 执行重建（分批提交，支持断点续跑自动跳过已完成）
  python rebuild_sz_000xxx.py --verify               # 终验：抽样比对 vipdoc
"""
import argparse
import json
import os
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"D:/workbuddyspace")

from build_qfq_meta_tdx import read_tdx_day  # noqa: E402


def parse_day_file(filepath, min_date=0):
    """通达信 .day 解析（与 tulong_daily_update.parse_day_file 口径一致：<IIIIIfII）。"""
    import struct as _struct
    recs = []
    with open(filepath, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            date, o, h, l, c, amount, volume, _ = _struct.unpack("<IIIIIfII", b)
            if c <= 0 or o <= 0:
                continue
            recs.append({"date": date, "open": o / 100.0, "high": h / 100.0,
                         "low": l / 100.0, "close": c / 100.0,
                         "volume": int(volume), "amount": float(amount)})
    return [r for r in recs if r["date"] >= min_date]

MAIN_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
META_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db"
QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SZ_LDAY = Path(r"D:/anzhuang/tdx/vipdoc/sz/lday")
SH_LDAY = Path(r"D:/anzhuang/tdx/vipdoc/sh/lday")

DONE_LOG = ROOT / "data" / "rebuild_sz_done.json"


def classify():
    """扫描：幽灵码 / 需重建真股清单。"""
    m = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    codes = sorted({r[0] for r in m.execute(
        "SELECT DISTINCT code FROM stock_daily_meta").fetchall()
        if str(r[0]).startswith(("000", "001"))})
    ghosts, stocks = [], []
    for code in codes:
        if os.path.exists(SZ_LDAY / f"sz{code}.day"):
            # 是深市真股；进一步判定是否曾遭 sh 指数文件撞码（sh 存在同号文件才可能）
            if os.path.exists(SH_LDAY / f"sh{code}.day"):
                stocks.append(code)          # 撞码候选=需重建
            # else: 未被撞码，保持现状
        else:
            if any(os.path.exists(SH_LDAY / f"sh{code}.day") for _ in [0]):
                ghosts.append(code)          # 只有 sh 同名文件=幽灵码（纯指数）
            else:
                pass                          # 与 sh 无关的其他杂码，不动
    m.close()
    return ghosts, stocks


def drop_guards(con_main, con_meta):
    dropped = []
    for con, tbl in ((con_main, "stock_daily_raw"), (con_meta, "stock_daily_meta")):
        for tg in ("trg_pguard_%s_ins" % tbl, "trg_pguard_%s_upd" % tbl):
            try:
                con.execute(f"DROP TRIGGER IF EXISTS {tg}")
                dropped.append(f"{tbl}:{tg}")
            except Exception as e:
                print("drop fail", tg, e)
    con.commit(); con_meta.commit()
    return dropped


def restore_guards(con_main, con_meta):
    import db_pollution_guard as g
    g.MAIN_DB = MAIN_DB; g.META_DB = META_DB
    g.install()


def replay_one(con_main, con_meta, code: str) -> tuple[int, int]:
    """vipdoc 真值全量重放单只 deep 主板股到 raw+meta。返回 (raw_rows, meta_rows)。"""
    path = SZ_LDAY / f"sz{code}.day"
    recs = parse_day_file(str(path), min_date=0)
    if not recs:
        return 0, 0
    recs.sort(key=lambda r: r["date"])
    raw_rows, meta_rows = [], []
    prev = None
    for r in recs:
        d, o, h, l, c, v, a = (r["date"], r["open"], r["high"], r["low"],
                               r["close"], r["volume"], r["amount"])
        if prev and prev > 0 and c > 0:
            cp = round((c - prev) / prev, 6)
            ca = round(c - prev, 4)
            amp = round((h - l) / prev, 6)
        else:
            cp = ca = amp = 0
        raw_rows.append((code, d, o, h, l, c, v, a))
        meta_rows.append((code, d, o, h, l, c, v, a, cp, ca, amp,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        prev = c if c > 0 else prev
    con_main.executemany(
        "INSERT OR REPLACE INTO stock_daily_raw (code,date,open,high,low,close,vol,amt) "
        "VALUES (?,?,?,?,?,?,?,?)", raw_rows)
    con_meta.executemany(
        "INSERT OR REPLACE INTO stock_daily_meta "
        "(code,date,open,high,low,close,volume,amount,change_pct,change_amount,amplitude,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", meta_rows)
    return len(raw_rows), len(meta_rows)


def run(batch_size: int):
    ghosts, stocks = classify()
    done = set()
    if DONE_LOG.exists():
        done = set(json.loads(DONE_LOG.read_text(encoding="utf-8")))
    todo_stocks = [c for c in stocks if c not in done]
    print(f"[scan] 幽灵码={len(ghosts)} | 需重建真股={len(stocks)} | 待处理={len(todo_stocks)}")

    import fix_qfq_rebuild as fq

    con = sqlite3.connect(MAIN_DB, timeout=120)
    mcon = sqlite3.connect(META_DB, timeout=120)
    qcon = sqlite3.connect(QFQ_DB, timeout=120)
    for c_ in (con, mcon, qcon):
        c_.execute("PRAGMA journal_mode=WAL")
        c_.execute("PRAGMA busy_timeout=120000")

    # ── A. 幽灵码清除 ──
    bak_path = ROOT / "data" / f"rebuild_ghost_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    gbak = {}
    for code in ghosts:
        for tag, conn, tbl in (("raw", con, "stock_daily_raw"),
                               ("meta", mcon, "stock_daily_meta"),
                               ("qfq", qcon, "stock_daily_meta_qfq")):
            rows = conn.execute(f"SELECT * FROM {tbl} WHERE code=?", (code,)).fetchall()
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            gbak[f"{code}:{tag}"] = {"cols": cols, "rows": rows}
            conn.execute(f"DELETE FROM {tbl} WHERE code=?", (code,))
        print(f"  👻 {code}: 幽灵码清除")
    con.commit(); mcon.commit(); qcon.commit()
    bak_path.write_text(json.dumps(gbak, ensure_ascii=False), encoding="utf-8")
    print(f"📦 幽灵备份 → {bak_path}")

    # ── B. 真股重建（分批）──
    dropped = drop_guards(con, mcon)
    print(f"护栏临时摘除: {len(dropped)} 个")
    t0 = time.time()
    n_done_this_run = 0
    for i in range(0, len(todo_stocks), batch_size):
        batch = todo_stocks[i:i + batch_size]
        t_b = time.time()
        stats = []
        for code in batch:
            con.execute("DELETE FROM stock_daily_raw WHERE code=?", (code,))
            mcon.execute("DELETE FROM stock_daily_meta WHERE code=?", (code,))
            qcon.execute("DELETE FROM stock_daily_meta_qfq WHERE code=?", (code,))
            nr, nm = replay_one(con, mcon, code)
            st, info = fq.rebuild_one(qcon, code, verbose=False)
            stats.append((code, nr, nm, st))
            done.add(code)
        con.commit(); mcon.commit(); qcon.commit()
        n_done_this_run += len(batch)
        DONE_LOG.write_text(json.dumps(sorted(done)), encoding="utf-8")
        el = time.time() - t0
        print(f"  batch {i//batch_size+1}/{(len(todo_stocks)+batch_size-1)//batch_size}: "
              f"{len(batch)} 只 done={n_done_this_run} elapsed={el:.0f}s "
              f"last={batch[-1]} failed={[s for s,_,_,st in stats if st!='ok']}", flush=True)
    restore_guards(con, mcon)
    print("护栏已装回")
    con.close(); mcon.close(); qcon.close()
    print(f"\n🏁 重建完成: 本轮处理 {n_done_this_run} 只，总耗时 {time.time()-t0:.0f}s")
    return 0


def verify(sample: int = 12):
    m = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    codes = [r[0] for r in m.execute(
        "SELECT DISTINCT code FROM stock_daily_meta WHERE code LIKE '000%']".replace("]","")).fetchall()][:sample]
    mism = ok = skipped = 0
    for code in codes:
        vd = read_tdx_day(code)
        if vd is None:
            skipped += 1
            continue
        vmp = {d.strftime("%Y%m%d"): r["close"] for d, r in vd.iterrows()}
        rows = m.execute(
            "SELECT date,close FROM stock_daily_meta WHERE code=? ORDER BY date DESC LIMIT 50",
            (code,)).fetchall()
        for date, cl in rows:
            ds = str(date).replace("-", "")
            if ds in vmp:
                if abs(cl / vmp[ds] - 1) > 0.01:
                    mism += 1
                else:
                    ok += 1
    print(f"终验抽样: 一致={ok} 不一致={mism} skip(vipdoc无)={skipped}")
    m.close()
    return 0 if mism == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--sample", type=int, default=12)
    a = ap.parse_args()
    if a.scan:
        ghosts, stocks = classify()
        print(f"幽灵码 {len(ghosts)}: {ghosts[:20]}{'...' if len(ghosts)>20 else ''}")
        print(f"需重建真股 {len(stocks)} 只（近端样例 {stocks[:10]}）")
        return 0
    if a.run:
        return run(a.batch)
    if a.verify:
        return verify(a.sample)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
