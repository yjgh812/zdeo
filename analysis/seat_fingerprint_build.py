# -*- coding: utf-8 -*-
"""seat_fingerprint_build.py — PD-032 席位指纹库层1构建器(描述层零交易权)
主人2026-10-07裁决批准(PD-032席位指纹库层1, 识别件纯描述)。

数据源: D:\\workbuddyspace\\databases\\fundamental_data.db lhb_seat_detail
        (东财席位明细全史 2010-01-04~2026-09-30, 2393524行, lhb_update.py唯一写入方)
产出(idempotent全量重建):
  1. attribution.db lhb_seat_library     — 席位聚合档案(设计消费层,原表9/8后停更,本次全史重建,同schema)
  2. attribution.db lhb_seat_fingerprint — 层1指纹表(原schema+2026增量字段+活跃天数+个股数)
  3. attribution.db lhb_seat_cooccur     — 锚席位共现表(top锚×top伙伴, 同股同日任一侧)
边界: 纯描述识别件, 零交易权, 不接闸门不接cron; 消费=跟踪研判参考, 禁止直接构成买卖依据。
用法: python analysis/seat_fingerprint_build.py
"""
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SRC = r"D:\workbuddyspace\databases\fundamental_data.db"
DST = r"D:\zcodesj\attribution\attribution.db"
REPORT = Path(r"D:\zcodesj\reports\seat_fingerprint_L1_20261007.md")
CUTOFF_2026 = "2026-01-01"
ANCHOR_TOP = 30          # 锚席位=2026年营业部口径上榜次数top30
PARTNER_TOP = 20         # 每锚保留共现伙伴top20
HM_MIN_APPEARANCES = 50  # 游资候选线(2026年上榜次数, 全史口径倍增)

FP_DDL = """
CREATE TABLE IF NOT EXISTS lhb_seat_fingerprint (
    seat_name TEXT PRIMARY KEY,
    style_tag TEXT NOT NULL,
    appearances INTEGER NOT NULL,
    appearances_2026 INTEGER NOT NULL,
    active_days INTEGER NOT NULL,
    n_stocks INTEGER NOT NULL,
    total_buy REAL NOT NULL, total_sell REAL NOT NULL, total_net REAL NOT NULL,
    total_net_2026 REAL NOT NULL,
    first_seen TEXT, last_seen TEXT,
    hm_candidate INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""
CO_DDL = """
CREATE TABLE IF NOT EXISTS lhb_seat_cooccur (
    anchor TEXT NOT NULL,
    partner TEXT NOT NULL,
    co_count INTEGER NOT NULL,
    co_net REAL NOT NULL,
    first_seen TEXT, last_seen TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (anchor, partner)
);
"""


def classify(name: str) -> str:
    if name == "机构专用":
        return "机构"
    if "股通专用" in name:
        return "北向通道"
    return "营业部"


def main():
    t0 = time.time()
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    src.execute("PRAGMA busy_timeout=60000")
    dst = sqlite3.connect(DST, timeout=60)
    dst.execute("PRAGMA busy_timeout=60000")
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # ---- 1) 全史席位聚合(SQL内聚, 2.39M行秒级) ----
    rows = src.execute("""
        SELECT operate_dept_name,
               COUNT(DISTINCT trade_date||'|'||ts_code)        AS appearances,
               COUNT(DISTINCT trade_date)                      AS active_days,
               COUNT(DISTINCT ts_code)                          AS n_stocks,
               SUM(COALESCE(buy,0))                             AS tb,
               SUM(COALESCE(sell,0))                            AS ts_,
               SUM(COALESCE(net,0))                             AS tn,
               MIN(trade_date) AS fs, MAX(trade_date) AS ls,
               SUM(CASE WHEN trade_date>=? THEN COALESCE(net,0) ELSE 0 END) AS tn26,
               SUM(CASE WHEN trade_date>=? THEN 1 ELSE 0 END)   AS app26
        FROM lhb_seat_detail
        GROUP BY operate_dept_name
    """, (CUTOFF_2026, CUTOFF_2026)).fetchall()
    print(f"[AGG] 全史席位 {len(rows)} 个")

    # ---- 2) 重建 lhb_seat_library(设计消费层, 同schema同语义) ----
    dst.execute("DELETE FROM lhb_seat_library")
    dst.executemany(
        "INSERT INTO lhb_seat_library (seat_name,style_tag,appearances,total_buy,total_sell,"
        "total_net,first_seen,last_seen,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(n, classify(n), app, tb, tsel, tn, fs, ls, now)
         for (n, app, ad, ns, tb, tsel, tn, fs, ls, tn26, app26) in rows])
    print(f"[L0] lhb_seat_library 重建 {len(rows)} 行(全史口径)")

    # ---- 3) 层1指纹表(含2026增量) ----
    dst.execute("DROP TABLE IF EXISTS lhb_seat_fingerprint")
    dst.execute(FP_DDL)
    fp_rows = []
    for (n, app, ad, ns, tb, tsel, tn, fs, ls, tn26, app26) in rows:
        tag = classify(n)
        hm = 1 if (tag == "营业部" and app26 >= HM_MIN_APPEARANCES) else 0
        fp_rows.append((n, tag, app, app26, ad, ns, tb, tsel, tn, tn26, fs, ls, hm, now))
    dst.executemany(
        "INSERT INTO lhb_seat_fingerprint VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fp_rows)
    hm_n = sum(1 for r in fp_rows if r[12])
    print(f"[L1] lhb_seat_fingerprint {len(fp_rows)} 行, 游资候选(2026≥{HM_MIN_APPEARANCES}次) {hm_n} 个")

    # ---- 4) 锚席位共现(top锚×top伙伴, 同股同日; 2026窗口内存计算, 源库只读零索引变更) ----
    anchors = [r[0] for r in src.execute("""
        SELECT operate_dept_name FROM lhb_seat_detail
        WHERE trade_date>=? AND operate_dept_name NOT LIKE '%股通专用' AND operate_dept_name<>'机构专用'
        GROUP BY operate_dept_name
        ORDER BY COUNT(DISTINCT trade_date||'|'||ts_code) DESC LIMIT ?
    """, (CUTOFF_2026, ANCHOR_TOP)).fetchall()]
    anchor_set = set(anchors)
    co_pairs = {}   # (anchor, partner) -> [count, anchor_net_sum, first, last]
    cur_key, group = None, []
    for td, code, seat, net in src.execute("""
        SELECT trade_date, ts_code, operate_dept_name, net FROM lhb_seat_detail
        WHERE trade_date>=? ORDER BY trade_date, ts_code
    """, (CUTOFF_2026,)):
        key = (td, code)
        if key != cur_key:
            if group:
                seats = {s for s, _ in group}
                for a in seats & anchor_set:
                    a_net = sum(n for s, n in group if s == a)
                    for p in seats:
                        if p == a:
                            continue
                        k = (a, p)
                        rec = co_pairs.setdefault(k, [0, 0.0, td, td])
                        rec[0] += 1
                        rec[1] += a_net
                        rec[3] = td
            cur_key, group = key, []
        group.append((seat, net or 0.0))
    if group:
        seats = {s for s, _ in group}
        for a in seats & anchor_set:
            a_net = sum(n for s, n in group if s == a)
            for p in seats:
                if p == a:
                    continue
                k = (a, p)
                rec = co_pairs.setdefault(k, [0, 0.0, cur_key[0], cur_key[0]])
                rec[0] += 1
                rec[1] += a_net
                rec[3] = cur_key[0]

    dst.execute("DROP TABLE IF EXISTS lhb_seat_cooccur")
    dst.execute(CO_DDL)
    co_rows = []
    for a in anchors:
        top = sorted([(p, v) for (aa, p), v in co_pairs.items() if aa == a],
                     key=lambda kv: -kv[1][0])[:PARTNER_TOP]
        for p, (cc, net_sum, fs, ls) in top:
            co_rows.append((a, p, cc, net_sum, fs, ls, now))
    dst.executemany(
        "INSERT OR REPLACE INTO lhb_seat_cooccur VALUES (?,?,?,?,?,?,?)", co_rows)
    print(f"[CO] 锚{len(anchors)}个 × 共现伙伴 {len(co_rows)} 行(2026窗口内存计算)")

    dst.commit()

    # ---- 5) 锚席位读数(报告用) ----
    wanping = [r for r in fp_rows if "宛平南路" in r[0]]
    top26 = sorted([r for r in fp_rows if r[1] == "营业部"], key=lambda r: -r[3])[:15]
    inst26 = [r for r in fp_rows if r[1] == "机构"]
    north26 = [r for r in fp_rows if r[1] == "北向通道"]

    lines = [
        "# 席位指纹库层1 (PD-032, 描述层零交易权)",
        "",
        f"> 构建时间 {now} | 源=fundamental_data.db lhb_seat_detail 全史(2010-01-04~2026-09-30, 2,393,524行)",
        f"> 产出: lhb_seat_library(全史重建) + lhb_seat_fingerprint(层1指纹) + lhb_seat_cooccur(锚共现) @ attribution.db",
        f"> 边界: 纯描述识别件零交易权; 消费=跟踪研判参考, 禁止直接构成买卖依据(PD-032 boundary)",
        "",
        "## 2026年锚席位 top15(营业部口径, 按2026上榜次数)",
        "",
        "| 席位 | 2026上榜 | 全史上榜 | 活跃日 | 个股数 | 2026净买(万) | 游资候选 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in top26:
        lines.append(f"| {r[0]} | {r[3]} | {r[2]} | {r[4]} | {r[5]} | {r[9]/1e4:.0f} | {'✓' if r[12] else ''} |")
    lines += [
        "",
        "## 宛平南路读数(战备单宣称179次实证复核)",
        "",
    ]
    for r in wanping:
        lines.append(f"- {r[0]}: 2026年上榜 **{r[3]} 次**(全史 {r[2]}), 2026净买 {r[9]/1e4:.0f} 万, 活跃日 {r[4]}")
    if not wanping:
        lines.append("- 2026年无宛平南路命中(如实)")
    lines += [
        "",
        f"## 家族概览: 席位总数 {len(fp_rows)} | 机构 {len(inst26)} | 北向通道 {len(north26)} | 游资候选 {hm_n}",
        "",
        "## 2026游资候选(top15 by 2026上榜, 全量见指纹表 hm_candidate=1)",
        "",
        "| 席位 | 2026上榜 | 2026净买(万) | 全史净买(亿) |",
        "|---|---|---|---|",
    ]
    hm_top = sorted([r for r in fp_rows if r[12]], key=lambda r: -r[3])[:15]
    for r in hm_top:
        lines.append(f"| {r[0]} | {r[3]} | {r[9]/1e4:.0f} | {r[8]/1e8:.1f} |")
    lines += ["", f"构建耗时 {time.time()-t0:.0f}s | 幂等可重跑 | PD-032层1完"]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[REPORT] {REPORT}")

    # ---- 6) 自验 ----
    n_lib = dst.execute("SELECT COUNT(*) FROM lhb_seat_library").fetchone()[0]
    n_fp = dst.execute("SELECT COUNT(*) FROM lhb_seat_fingerprint").fetchone()[0]
    n_co = dst.execute("SELECT COUNT(*) FROM lhb_seat_cooccur").fetchone()[0]
    assert n_lib == len(rows) and n_fp == len(fp_rows) and n_co == len(co_rows), "行数自验失败"
    src.close()
    dst.close()
    print(f"[SELF-CHECK] library={n_lib} fingerprint={n_fp} cooccur={n_co} 全部一致 OK")


if __name__ == "__main__":
    main()
