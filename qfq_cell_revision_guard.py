#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""qfq_cell_revision_guard.py — qfq 库历史单元格修订审计守卫
（自省第三期 S8/M5，漂移 v3 移交项；进化周期 7 落地，2026-09-20）

背景: 9/13-9/14 夜间链曾改写历史 qfq 单元格(600064@2021-07-26 下修≥2.8% 等双向修订),
现行守卫(vwap自洁/close完整性)对"修订后值内部自洽"的历史行变更零覆盖 → 回测锚漂移
0.4~1% 噪音无人知。本守卫补位: **前向防护**(基线建立后的历史区修订即抓)。

机制:
  基线 = data/qfq_cell_baseline.json, 按"(date 前6位=月, 六列特征校验和)"存月级指纹
         (特征 = 行数 + SUM(open,high,low,close,volume), SQL 一次 GROUP BY 秒级)
  检测区 = 距 qfq_max > GUARD_RECENT_DAYS 自然日的完整月(近端=数据链合法回填窗口不检;
          当月不完整不检)
  滚动封账 = 新完整月自动入基线(写入即封账, 下轮起受检)
  触发 = 受检月特征与基线不一致 → FAIL 点名月份; 行级旧值审计走 db_backup 每日备份
         diff(备份=行级真源), 本守卫职责=检测+证据保全(触发月 dump 当前行样本)

不追溯声明: 基线建立前的修订(如 9/13-14 事件)无法由本守卫检出, 如实标注。

用法:
  python qfq_cell_revision_guard.py --init   # 建基线(或月缺失时补)
  python qfq_cell_revision_guard.py          # 检测
  python qfq_cell_revision_guard.py --db <path>  # 指定库(测试用)
接线: health_check_daily 新探针(run_check import 模式, 同 qfq_close_guard)。
退出码: 0=OK/BASELINE_CREATED, 2=FAIL(检出修订), 3=库不可达
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
DEFAULT_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
BASELINE = os.path.join(ROOT, "data", "qfq_cell_baseline.json")
GUARD_RECENT_DAYS = 7      # 近端回填豁免窗(自然日)
SAMPLE_ROWS = 5            # FAIL 时 dump 样本行数


def month_fingerprints(con) -> dict:
    """月 → 指纹哈希(行数+六列 SUM 规范化)。SQL 端聚合, 全表一次扫。"""
    fps = {}
    for m, n, so, sh, sl, sc, sv in con.execute(
            "SELECT substr(date,1,6), COUNT(*), "
            "IFNULL(SUM(open),0), IFNULL(SUM(high),0), IFNULL(SUM(low),0), "
            "IFNULL(SUM(close),0), IFNULL(SUM(volume),0) "
            "FROM stock_daily_meta_qfq GROUP BY substr(date,1,6) ORDER BY 1"):
        raw = "|".join(f"{x:.6f}" if isinstance(x, float) else str(x) for x in (n, so, sh, sl, sc, sv))
        fps[m] = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return fps


def load_baseline() -> dict:
    if os.path.exists(BASELINE):
        try:
            return json.load(open(BASELINE, encoding="utf-8"))
        except Exception:
            pass
    return {"created": None, "months": {}}


def save_baseline(bl: dict):
    tmp = BASELINE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bl, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BASELINE)


def guardable_months(fps: dict, qfq_max: str) -> set:
    """完整月 = 月末+GUARD_RECENT_DAYS < qfq_max(近似: 月下一月首-1 与 qfq_max 差>7自然日)。"""
    out = set()
    ym_sorted = sorted(fps)
    for i, m in enumerate(ym_sorted):
        nxt = ym_sorted[i + 1] if i + 1 < len(ym_sorted) else None
        if nxt is None:
            continue  # 最新月=当月不完整不检
        # 月 m 的最后数据日必 < 月 m+1 首日; 以"m+1 月存在"证 m 完整
        # 距今豁免: m+1 月首日 + 7 仍 <= qfq_max 才检(近端回填窗)
        guard_from = (nxt[:4] + nxt[4:6] + "01")
        if guard_from <= _shift_days(qfq_max, -GUARD_RECENT_DAYS):
            out.add(m)
    return out


def _shift_days(ymd: str, days: int) -> str:
    d = datetime.strptime(ymd, "%Y%m%d")
    from datetime import timedelta
    return (d + timedelta(days=days)).strftime("%Y%m%d")


def run_check(db: str = DEFAULT_DB, init: bool = False):
    """返回 {status: OK|FAIL|UNAVAILABLE|BASELINE_CREATED, changed: [...], detail}"""
    if not os.path.exists(db) or os.path.getsize(db) == 0:
        return {"status": "UNAVAILABLE", "changed": [], "detail": f"库不可用: {db}"}
    try:
        con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
        qfq_max = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        if not qfq_max:
            con.close()
            return {"status": "UNAVAILABLE", "changed": [], "detail": "表无数据"}
        fps = month_fingerprints(con)
        con.close()
    except Exception as e:
        return {"status": "UNAVAILABLE", "changed": [], "detail": f"库读取失败: {e}"}

    bl = load_baseline()
    guardable = guardable_months(fps, qfq_max)

    if init or not bl["months"]:
        new_m = {m: fps[m] for m in guardable}
        n_new = len(new_m) - len(set(new_m) & set(bl["months"]))
        bl["months"].update(new_m)
        if bl["created"] is None:
            bl["created"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        bl["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        bl["note"] = "月级特征校验和(行数+六列SUM); 检测区=完整月且距qfq_max>7自然日; 不追溯基线前修订"
        save_baseline(bl)
        return {"status": "BASELINE_CREATED", "changed": [], "n_baseline": len(bl["months"]),
                "detail": f"基线建立/补账: 新封账{n_new}月, 共{len(bl['months'])}月 (qfq_max={qfq_max})"}

    changed = []
    for m in sorted(guardable):
        if m in bl["months"] and fps[m] != bl["months"][m]:
            changed.append(m)
    if changed:
        # 证据保全: dump 首个变化月的样本行
        ev = os.path.join(ROOT, "data", f"qfq_revision_evidence_{datetime.now().strftime('%Y%m%d')}.json")
        try:
            con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
            sample = [{"code": r[0], "date": r[1], "close": r[2]}
                      for r in con.execute(
                          f"SELECT code,date,close FROM stock_daily_meta_qfq "
                          f"WHERE substr(date,1,6)='{changed[0]}' ORDER BY code,date LIMIT {SAMPLE_ROWS}")]
            con.close()
            json.dump({"detected_at": datetime.now().isoformat(), "changed_months": changed,
                       "sample_rows_first_month": sample,
                       "audit_guide": "行级旧值审计: db_backup.py 每日备份 diff 该月"},
                     open(ev, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
        return {"status": "FAIL", "changed": changed,
                "detail": f"检出历史月特征变更: {len(changed)}月({changed[0]}~{changed[-1]}) — 禁直接消费该区间, 先备份diff审计; 全名单与样本见证据文件 {os.path.basename(ev)}"}

    # 滚动封账: 新完整月入基线(不改变检测结果语义: 新月此前未受检)
    new_m = {m: fps[m] for m in guardable if m not in bl["months"]}
    if new_m:
        bl["months"].update(new_m)
        bl["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_baseline(bl)
    return {"status": "OK", "changed": [],
            "detail": f"受检{len(guardable)}月零变更 (基线{len(bl['months'])}月, qfq_max={qfq_max}, 新封账{len(new_m)}月)"}


def main():
    ap = argparse.ArgumentParser(description="qfq 历史单元格修订审计守卫")
    ap.add_argument("--init", action="store_true", help="建基线/补账")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()
    r = run_check(a.db, init=a.init)
    tag = "QFQ-CELL-GUARD"
    print(f"[{tag}] status={r['status']} {r['detail']}")
    if r["status"] == "FAIL":
        print(f"[{tag}] 变更月: {r['changed']}")
        return 2
    if r["status"] == "UNAVAILABLE":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
