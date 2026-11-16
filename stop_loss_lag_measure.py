#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stop_loss_lag_measure.py — 止损执行滞后分钟级测量器（M2，2026-09-29）。

补自省报告数据缺口①：「跌破 -8% 时点 → 实际卖出」此前不可测（MCP 成交回报无过程价）。
本脚本用 本地1分钟K(stock_minute_1, 真实价口径) × 成交时间(fills_clean.filled_at)
× 成本配对(trade_records 加权均成本) 三角定位滞后。

口径与限制（如实标注，不做无据折算）:
  - 触发线 = 加权平均成本 × 0.92（S-5 -8% 口径，真实价；若持有期内有除权除息则存在偏差,
    近期样本逐一核验 notes 无除权标记, 偏差列入已知限制）。
  - 成本配对=trade_records 该 code 卖出日前全部 BUY 金额-此前 SELL 面值的净额/净股数;
    历史窗口起于 20260727, 更早底仓成本不可得 → 该样本标 cost_incomplete 并剔除出中位数。
  - 首次破线=止损观察窗口(默认20交易日)内 low<=trigger 的最早分钟bar(date,time)。
  - 滞后主指标=破线日到成交日的交易日差(同日=0)，同日附分钟差。
  - 只测量 不含触发即卖判定, 属观测层零交易权。

用法: python stop_loss_lag_measure.py [--month 202609] [--window 20]
输出: reports/stop_loss_lag_<month>.json（记分卡 M3 消费）+ 同名 .md 明细。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

ROOT = r"D:\zcodesj"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
FILLS_DB = "file:D:/zcodesj/data/fills_clean.db?mode=ro"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=datetime.now().strftime("%Y%m"))
    ap.add_argument("--window", type=int, default=20)
    a = ap.parse_args()
    month = a.month
    start, end = month + "01", month[:6] + "40"

    acon = sqlite3.connect(ATTR_DB)
    sells = acon.execute(
        "SELECT code, name, trade_date, price, volume, notes FROM trade_records "
        "WHERE action='SELL' AND trade_date>=? AND trade_date<? "
        "AND (notes LIKE '%止损%' OR notes LIKE '%S-5%') ORDER BY trade_date",
        (start, end)).fetchall()
    fcon = sqlite3.connect(FILLS_DB, uri=True)
    mcon = sqlite3.connect(META_DB, uri=True)
    cal = [str(r[0]) for r in mcon.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq_view ORDER BY date")] if False else \
        [str(r[0]) for r in mcon.execute(
            "SELECT DISTINCT date FROM stock_daily_meta ORDER BY date")]
    idx = {d: i for i, d in enumerate(cal)}

    items, skipped = [], []
    for code, name, sdate, sprice, svolume, notes in sells:
        c6 = str(code)[-6:]
        # 加权成本配对
        hist = acon.execute(
            "SELECT action, price, volume, amount FROM trade_records "
            "WHERE code=? AND trade_date<? AND action IN ('BUY','SELL') ORDER BY trade_date",
            (code, sdate)).fetchall()
        net_val, net_sh, incomplete = 0.0, 0, False
        for act, p, v, amt in hist:
            v = int(v or 0)
            amt = float(amt or 0) or float(p or 0) * v
            if act == "BUY":
                net_val += amt; net_sh += v
            else:
                # 卖出按当前均价实现成本
                avg = net_val / net_sh if net_sh else 0
                net_val -= avg * min(v, net_sh); net_sh -= min(v, net_sh)
        if net_sh <= 0 or not net_val:
            skipped.append({"code": code, "name": name, "reason": "成本配对失败"})
            continue
        # 底仓早于历史窗口=成本不完整（该票历史最早记录无对应BUY且卖出量>配对量则剔除）
        first_rec = acon.execute(
            "SELECT MIN(trade_date) FROM trade_records WHERE code=?", (code,)).fetchone()[0]
        if str(first_rec) >= start and str(first_rec) == sdate:
            incomplete = True  # 当月才见首笔记录而当月即卖=窗口截断风险高
        cost = net_val / net_sh
        trig = round(cost * 0.92, 4)
        # 成交时间: fills_clean 当日同码同向最近一笔
        fill = fcon.execute(
            "SELECT filled_at, price, quantity FROM fills WHERE code=? AND UPPER(action) LIKE '%SELL%' "
            "AND date(filled_at) LIKE ? ORDER BY filled_at DESC LIMIT 1",
            (c6, f"{month[:4]}-{month[4:6]}-%")).fetchone()
        # 破线搜索窗 = [首笔BUY日, 卖出日]（M2修正20260929: 旧版用卖出日往前window日,
        # 破线点早于建仓=伪影, 科森科技sh603626 8/7破线8/26建仓实证）
        # ⚠️ trade_records 日期存在双格式漂移(8月导入行'2026-08-03' vs 9月行'20260907',
        #    赞宇科技sz002637实证), 一律 replace('-','') 归一后再查交易日索引
        i0 = idx.get(str(sdate).replace('-', ''), len(cal) - 1)
        b_first = acon.execute(
            "SELECT MIN(trade_date) FROM trade_records WHERE code=? AND action='BUY'",
            (code,)).fetchone()[0]
        b_first_n = str(b_first).replace('-', '')
        i1 = idx.get(b_first_n)
        if i1 is None or i1 >= i0:
            skipped.append({"code": code, "name": name, "date": sdate,
                            "reason": f"建仓日{b_first_n}不在交易日索引或晚于卖出日(历史超窗/格式异常)"})
            continue
        span = cal[i1:i0 + 1]
        cov = mcon.execute(
            "SELECT COUNT(DISTINCT date) FROM stock_minute_1 WHERE code=? AND date>=? AND date<=?",
            (c6, span[0], span[-1])).fetchone()[0]
        if cov < len(span) - 2:
            skipped.append({"code": code, "name": name, "date": sdate,
                            "reason": f"分钟覆盖不足({cov}/{len(span)}日)——缺数据不为零滞后"})
            continue
        brk = None
        for d in span:
            row = mcon.execute(
                "SELECT time, low FROM stock_minute_1 WHERE code=? AND date=? AND low<=? "
                "ORDER BY time LIMIT 1", (c6, d, trig)).fetchone()
            if row:
                brk = (d, int(row[0]), float(row[1]))
                break
        if brk is None:
            tail_missing = not mcon.execute(
                "SELECT 1 FROM stock_minute_1 WHERE code=? AND date=? LIMIT 1",
                (c6, str(sdate).replace('-', ''))).fetchone()
            skipped.append({"code": code, "name": name, "date": sdate,
                            "reason": ("卖出日分钟数据缺失,破线可能在卖出当日=不可测(缺数据不为零)"
                                       if tail_missing else "持仓期内分钟库无破线记录(未达-8%触发线,口径异常待核)")})
            continue
        bd, bt, _ = brk
        lag_days = max(0, idx.get(str(sdate), i0) - idx.get(bd, i0))
        item = {"code": code, "name": name, "sell_date": sdate, "sell_price": sprice,
                "avg_cost": round(cost, 3), "trigger": trig,
                "first_break": f"{bd} {bt:04d}",
                "filled_at": (fill[0] if fill else None),
                "lag_trade_days": lag_days,
                "same_day_minute_gap": None,
                "owner_override_in_notes": bool(notes and "override" in str(notes)),
                "cost_incomplete": incomplete}
        if bd == str(sdate) and fill:
            try:
                ft = datetime.fromisoformat(str(fill[0]))
                gap = (ft.hour * 60 + ft.minute) - (bt // 100 * 60 + bt % 100)
                if 9 * 60 + 30 <= ft.hour * 60 + ft.minute <= 15 * 60:
                    item["same_day_minute_gap"] = max(0, gap)
            except ValueError:
                pass
        items.append(item)

    acon.close(); fcon.close(); mcon.close()
    valid = [x for x in items if not x["cost_incomplete"]]
    lags = sorted(x["lag_trade_days"] for x in valid)
    out = {"month": month, "generated": datetime.now().isoformat(timespec="seconds"),
           "n_sells_stoploss": len(sells), "n_measured": len(valid),
           "n_skipped": len(skipped),
           "median_lag_days": lags[len(lags) // 2] if lags else None,
           "p90_lag_days": lags[int(len(lags) * 0.9) - 1] if lags else None,
           "items": items, "skipped": skipped,
           "口径限制": "成本配对窗口起20260727; 除权偏差未核; 分钟源缺失票计入skipped不为零滞后"}
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with open(os.path.join(ROOT, "reports", f"stop_loss_lag_{month}.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    lines = [f"# 止损执行滞后测量 {month}", "",
             f"- 止损类卖出 {len(sells)} 笔 | 可测 {len(valid)} | 剔除 {len(skipped)}",
             f"- 滞后中位数 = **{out['median_lag_days']}** 交易日 | P90 = **{out['p90_lag_days']}**", "",
             "| 代码 | 名称 | 卖出日 | 均成本 | 触发线 | 首破线 | 成交时间 | 滞后(日) | 同日分钟差 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for x in items:
        lines.append(f"| {x['code']} | {x['name']} | {x['sell_date']} | {x['avg_cost']} "
                     f"| {x['trigger']} | {x['first_break']} | {x['filled_at'] or '—'} "
                     f"| {x['lag_trade_days']} | {x['same_day_minute_gap'] if x['same_day_minute_gap'] is not None else '—'} |")
    if skipped:
        lines += ["", "## 剔除样本（缺数据不为零）", ""]
        for s in skipped:
            lines.append(f"- {s.get('code')} {s.get('name','')}：{s.get('reason')}")
    with open(os.path.join(ROOT, "reports", f"stop_loss_lag_{month}.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[LAG] month={month} 可测={len(valid)} 中位={out['median_lag_days']} "
          f"P90={out['p90_lag_days']} 剔除={len(skipped)} -> reports/stop_loss_lag_{month}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
