#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙系统·行业量能影子件首评器（H-NEW6 转正决策支持，2026-09-18）

功能：读影子件逐日台账（industry_volume_state_daily.jsonl），对每个 C1 持续放量事件
计算申万一级指数 fwd5/fwd20（next-bar），对照同期全行业基准，并输出 C1 第二日持续率。
用途：约 20 交易日影子期结束（2026-10 中旬）跑一次，供主人裁决影子件转正与否。
边界：只读计算，零生产依赖；影子期样本预计极少（每日 0-2 个事件），输出如实标注样本量，
      本器是"机制验证"工具而非统计显著性工具——判定权在主人。
用法：python industry_vol_shadow_eval.py [--asof YYYYMMDD]
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SW_DB = Path(r"D:\workbuddyspace\databases\sw_industry.db")
LEDGER = Path(r"D:\zcodesj\data\industry_volume_state_daily.jsonl")


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} [shadow_eval] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="评估截止日 YYYYMMDD, 缺省=台账最新日")
    a = ap.parse_args()
    if not LEDGER.exists():
        log(f"ERROR 台账缺失: {LEDGER}")
        return 2
    events = []  # (date, industry)
    days = 0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if a.asof and d.get("date", "99999999") > a.asof:
            continue
        days += 1
        for ind in d.get("c1_industries", []):
            events.append((d["date"], ind))
    log(f"台账影子日 {days} 天, C1 事件 {len(events)} 个")

    con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True)
    sw = con.execute("SELECT ts_code,name,trade_date,close FROM industry_kline_sw WHERE level='L1'").fetchall()
    con.close()
    series = {}
    for ts, name, td, close in sw:
        series.setdefault(name, []).append((str(td).replace("-", ""), float(close)))
    for name in series:
        series[name].sort()

    def fwd(name, d, n):
        arr = series.get(name, [])
        dates = [x[0] for x in arr]
        if d not in dates:
            return None
        i = dates.index(d)
        if i + 1 >= len(arr) or i + 1 + n >= len(arr):
            return None  # 未到期
        entry = arr[i + 1][1]
        return arr[i + 1 + n][1] / entry - 1.0

    rows, due5, due20 = [], 0, 0
    base5, base20 = [], []
    # 全行业基准(同窗口)
    all_rets5, all_rets20 = {}, {}
    for name, arr in series.items():
        for k in range(1, len(arr) - 21):
            e = arr[k][1]
            if e <= 0:
                continue
            all_rets5.setdefault(arr[k][0], []).append(arr[k + 6][1] / e - 1.0)
            all_rets20.setdefault(arr[k][0], []).append(arr[k + 21][1] / e - 1.0)
    for d, ind in events:
        f5, f20 = fwd(ind, d, 5), fwd(ind, d, 20)
        b5 = (sum(all_rets5.get(d, [])) / len(all_rets5[d])) if all_rets5.get(d) else None
        b20 = (sum(all_rets20.get(d, [])) / len(all_rets20[d])) if all_rets20.get(d) else None
        if f5 is not None:
            due5 += 1
        if f20 is not None:
            due20 += 1
        rows.append({"date": d, "industry": ind,
                     "fwd5": round(f5, 4) if f5 is not None else "未到期",
                     "fwd20": round(f20, 4) if f20 is not None else "未到期",
                     "基准fwd5": round(b5, 4) if b5 else None,
                     "基准fwd20": round(b20, 4) if b20 else None})
    # C1 第二日持续率
    ev_set = set(events)
    cont_total = cont_keep = 0
    for d, ind in events:
        nxt = None
        later = [x for x in events if x[0] > d and x[1] == ind]
        if later:
            cont_total += 1
            cont_keep += 1  # 台账中有后续同行业 C1 即记持续(粗口径)
    print(json.dumps({
        "asof": a.asof or "latest", "影子日数": days, "C1事件数": len(events),
        "fwd5到期": due5, "fwd20到期": due20,
        "第二日持续率": (f"{cont_keep}/{cont_total}" if cont_total else "无连续事件"),
        "基准口径": "同日全行业均值",
        "事件明细": rows,
        "声明": "影子期样本极少, 本器为机制验证非统计显著性工具; 判定(转正/退役)权在主人",
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
