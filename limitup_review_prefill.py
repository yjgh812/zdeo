#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘 rows.json 预填器 limitup_review_prefill v1 (2026-09-09)

【用途】为当晚复盘会话生成次日 limitup_review_cli append 所需的 rows.json 骨架：
  八字段（code/name/limit_count/first_hhmm/seal_money/turnover/industry/reason）
  = 引擎行(attributionPatterns, industry/limit_count/turnover/first_limit_time 100%填充)
    × 当日涨停池(zt_pool, reason/seal_money) 合并。
【语义边界（主人 2026-09-09 裁决）】本脚本只做**数据搬运预填**，主题词表与主线判定
  属复盘人工判断（财联社收盘复盘页验证）——产出文件为 _draft 复核稿，复盘会话复核
  reason/补 themes 后方可 `limitup_review_cli.py append` 入库。本脚本永不直接入库。
【用法】python limitup_review_prefill.py --date 20260909
  产出 data/review_rows_{date}_draft.json（已存在则覆盖，幂等）
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, r"D:\workbuddyspace")
from unified_stock_attribution import AkshareSource  # noqa: E402

ATTR_DB = ROOT / "attribution" / "attribution.db"
OUT = ROOT / "data"


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="复盘 rows.json 预填器（纯数据搬运,永不入库）")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    a = ap.parse_args()
    date = a.date.replace("-", "")
    # 1) 引擎行（industry/limit_count/turnover/first_limit_time 100%填充）
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    eng = {r[0]: r for r in con.execute(
        "SELECT code, name, industry, limit_count, turnover, first_limit_time, seal_status "
        "FROM limit_up_patterns WHERE trade_date=? AND pattern_type='engine_v2'", (int(date),))}
    con.close()
    # 2) 当日涨停池（reason/seal_money）
    pool = {}
    try:
        df = AkshareSource.zt_pool(date)
        for _, row in df.iterrows():
            pool[str(row.get("代码", "")).zfill(6)] = (
                str(row.get("涨停原因") or row.get("reason") or "").strip(),
                float(row.get("封板资金") or row.get("seal_money") or 0) or None)
    except Exception as e:
        print(f"[prefill][WARN] 涨停池拉取失败({type(e).__name__}: {e}) → reason/seal 用引擎值兜底")
    rows, miss = [], []
    for c, (name, ind, lc, to, flt, seal_st) in ((k, (v[1], v[2], v[3], v[4], v[5], v[6])) for k, v in eng.items()):
        reason, seal = pool.get(c, ("", None))
        if not reason:  # 池子未命中 → 从 seal_status 反解首封时间做 reason 占位, 如实标记待复核
            miss.append(c)
            reason = f"[待复盘补reason] {seal_st}"
        hhmm = f"{flt[:2]}:{flt[2:4]}" if flt and len(str(flt)) >= 4 else "00:00"
        rows.append({"code": c, "name": name, "limit_count": int(lc), "first_hhmm": hhmm,
                     "seal_money": seal or 0.0, "turnover": float(to or 0),
                     "industry": ind or "未分类", "reason": reason})
    rows.sort(key=lambda r: (-int(r["limit_count"]), r["first_hhmm"]))
    out = {
        "_说明": ("复盘会话复核稿(预填骨架, 2026-09-09 limitup_review_prefill 生成): "
                  "①reason 东财池无该列→全部[待复盘补reason]，补录源=同花顺涨停池"
                  "(MCP get_a_share_special_data_limit_up_pool 的 limit_up_reason)或财联社"
                  "复盘页 cls.cn/depth?id=1003，逐行复核；②按当日市况增删 themes 词表"
                  "(主线判定=人工，财联社验证)；③复核后运行 "
                  f"python limitup_review_cli.py append --date {date} --rows 本文件。"
                  "未复核直接入库=内置9/8词表自动分类, 与人工判断口径不符。"),
        "_池未命中reason待补": miss,
        "batch": f"review_{date}",
        "rows": rows,
    }
    OUT.mkdir(exist_ok=True)
    p = OUT / f"review_rows_{date}_draft.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[prefill] {date}: {len(rows)} 行(池未命中{len(miss)}只待补reason) → {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
