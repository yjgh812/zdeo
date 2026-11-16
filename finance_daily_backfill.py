#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finance_daily_backfill.py — finance_daily 表报告期回填（幂等）（2026-09-02）

背景：finance_daily 的写入器为 2026-05-22 一次性历史回填（created_at 单批次），
此后失传，导致 20260630(H1) 分区缺失 → factor_pit v2 的 droe/roa（compute_droe_roa
依赖 net_profit/total_equity/total_assets 三列）H1 全 NULL。本脚本为防断链保留件。

数据源：通达信 gpcw*.dat（vipdoc/cw，pit_h1_refresh 链已就绪的同一文件）。
字段索引经数据对账锁定（000001/000002/600519 三股 × Q1 真实值 1e-4 相对误差全过）：
  net_profit=floats[95]  total_equity=floats[270]  total_assets=floats[39]
  （95/231 与 39/72 在合并/母公司报表重复，取消费方历史对账一致的 95/270/39）
另附 parser FINANCE_FIELD_MAP 全部每股/比率字段（消费方用到 basic_eps/bps/
total_shares/circulating_shares）。

用法:
  python finance_daily_backfill.py                    # 回填 cw 目录最新报告期
  python finance_daily_backfill.py --report-date 20260630
幂等: INSERT OR REPLACE（code+report_date UNIQUE），created_at 标记本次批次。
自检: --selftest 用 20260331 文件对账 finance_daily 既有真实值（不写库）。
"""
from __future__ import annotations

import argparse
import struct
import sys
from datetime import datetime
from pathlib import Path

CW_DIR = Path(r"D:\anzhuang\tdx\vipdoc\cw")
DB_PATH = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")

# 绝对额索引（对账锁定，勿改）
IDX_NET_PROFIT = 95
IDX_TOTAL_EQUITY = 270
IDX_TOTAL_ASSETS = 39
# parser FINANCE_FIELD_MAP 的同名映射（gpcw col_num → 列名，col_num-1 为 0 基索引）
EXTRA_MAP = {
    1: "basic_eps", 4: "bps", 6: "roe", 159: "current_ratio",
    162: "interest_coverage", 183: "revenue_growth", 184: "profit_growth",
    197: "roe_financial", 198: "roa", 199: "net_margin",
    200: "total_asset_return", 202: "gross_margin", 210: "debt_ratio",
    238: "total_shares", 239: "circulating_shares", 242: "shareholder_count",
}


def parse_gpcw_raw(filepath: Path) -> tuple[int, list[tuple[str, list[float]]]]:
    """复刻 tdx_finance_full_parser.parse_gpcw_file 的 struct 布局, 返回(报告期, [(code, floats)])"""
    header_fmt = "<hIHLLL"
    hs = struct.calcsize(header_fmt)
    item_fmt = "<6scL"
    isz = struct.calcsize(item_fmt)
    data = filepath.read_bytes()
    if len(data) < hs:
        raise ValueError(f"{filepath.name} 过小({len(data)}B), 非有效gpcw文件")
    h = struct.unpack(header_fmt, data[:hs])
    rd, max_count, report_size = h[1], h[2], h[4]
    field_count = report_size // 4
    if report_size <= 0 or max_count <= 0:
        raise ValueError(f"{filepath.name} header异常")
    out = []
    for idx in range(max_count):
        pos = hs + idx * isz
        if pos + isz > len(data):
            break
        item = struct.unpack(item_fmt, data[pos:pos + isz])
        code = item[0].decode("utf-8", errors="ignore").strip("\x00").strip()
        foa = item[2]
        if not code or foa + report_size > len(data):
            continue
        floats = struct.unpack(f"<{field_count}f", data[foa:foa + report_size])
        out.append((code, floats))
    return rd, out


def clean(v: float) -> float | None:
    return round(v, 6) if (v == v and abs(v) < 1e15) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-date", type=int, default=0, help="YYYYMMDD, 缺省取cw最新gpcw")
    ap.add_argument("--selftest", action="store_true", help="20260331对账既有值, 不写库")
    args = ap.parse_args()

    if args.report_date:
        fp = CW_DIR / f"gpcw{args.report_date}.dat"
        if not fp.exists():
            print(f"❌ 文件不存在: {fp}")
            return 2
        files = [fp]
    else:
        files = sorted(CW_DIR.glob("gpcw*.dat"), key=lambda p: p.stat().st_size,
                       reverse=True)
        files = [f for f in files if f.stat().st_size > 1_000_000][:1]
        if not files:
            print("❌ cw 目录无有效 gpcw 文件(>1MB)")
            return 2
    fp = files[0]
    rd, rows = parse_gpcw_raw(fp)
    print(f"解析 {fp.name}: report_date={rd}, 记录 {len(rows)} 条")

    built = []
    for code, fl in rows:
        rec = {"code": code, "report_date": rd,
               "net_profit": clean(fl[IDX_NET_PROFIT]),
               "total_equity": clean(fl[IDX_TOTAL_EQUITY]),
               "total_assets": clean(fl[IDX_TOTAL_ASSETS])}
        for col_num, name in EXTRA_MAP.items():
            if col_num <= len(fl):
                rec[name] = clean(fl[col_num - 1])
        built.append(rec)

    if args.selftest:
        st = sqlite3_selftest(rd, built)
        return st

    import sqlite3
    con = sqlite3.connect(DB_PATH)
    cols = [c[1] for c in con.execute("PRAGMA table_info(finance_daily)").fetchall()]
    batch_tag = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for rec in built:
        rec["created_at"] = batch_tag
        use = {k: v for k, v in rec.items() if k in cols}
        ph = ",".join("?" for _ in use)
        sql = (f"INSERT OR REPLACE INTO finance_daily ({','.join(use)}) "
               f"VALUES ({ph})")
        con.execute(sql, tuple(use.values()))
        n += 1
    con.commit()
    mx = con.execute("SELECT MAX(report_date) FROM finance_daily").fetchone()[0]
    con.close()
    print(f"✅ 回填 {n} 行 (report_date={rd}, 批次 {batch_tag}), 表MAX={mx}")
    return 0


def sqlite3_selftest(rd: int, built: list[dict]) -> int:
    import sqlite3
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    ok = 0
    for rec in built:
        if rec["code"] not in ("000001", "000002", "600519"):
            continue
        row = con.execute(
            "SELECT net_profit, total_equity, total_assets FROM finance_daily "
            "WHERE code=? AND report_date=?", (rec["code"], rd)).fetchone()
        if not row or not all(row):
            continue
        good = all(
            row[i] and rec[k] and abs(rec[k] - row[i]) / abs(row[i]) < 1e-4
            for i, k in enumerate(("net_profit", "total_equity", "total_assets")))
        print(f"  自检 {rec['code']}: {'✅' if good else '❌'} "
              f"(库 {row} vs 解析 {rec['net_profit']:.0f}/{rec['total_equity']:.0f}/{rec['total_assets']:.0f})")
        ok += good
    con.close()
    print(f"对账通过 {ok}/3")
    return 0 if ok == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
