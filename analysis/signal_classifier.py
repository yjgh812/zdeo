# -*- coding: utf-8 -*-
"""信号归类器 v0(主人裁判框架·判据一 归类预期兑现率) — 只读证据归集+启发式归类, 零交易权。
四驱动框架(主人定义): 业绩驱动 / 政策驱动 / 资金推动 / 估值修复 (+阶段+量价形态)。
v0 证据源(本地可得):
  业绩驱动 = event_calendar.db earnings_forecast 30日内预告(预增/扭亏/大幅上升族)
  资金推动 = data/em_flow_snapshot.db flow_snapshot em_5d>0 且 ths_5d>0(双口径同向净流入)
  政策驱动 = v0 无本地数据面 → 不自动归类(占位, 待晨会人工补录)
  估值修复 = v0 无可靠本地估值分位 → 不自动归类(占位)
量价形态 = 信号自身(20日新高突破+放量倍数+涨幅), 建批时随条目自带。
优先级: 业绩预告(最强事件证据) > 资金双正; 双命中 → 复合标签"业绩+资金共振"。
**v0 启发式归类仅支撑判据一的初步统计; 兑现判定标准与最终归类以主人方法论为准(判定前定义)。**
用法:
  classify_seats(entries, asof) -> {code: {...}}            (供管线 builder 调用)
  python analysis/signal_classifier.py --stamp <batch.json> (--asof YYYYMMDD)  (给手写批次补标)
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\zcodesj")

BASE = r"D:\zcodesj"
CAL_DB = "file:D:/zcodesj/event_calendar.db?mode=ro"
FLOW_DB = "file:D:/zcodesj/data/em_flow_snapshot.db?mode=ro"
POSITIVE_TYPES = ("预增", "扭亏", "大幅上升", "略增", "续盈")


def _q(query, db, params=()):
    try:
        con = sqlite3.connect(db, uri=True)
        rows = con.execute(query, params).fetchall()
        con.close()
        return rows
    except Exception:
        return []


def classify_one(code, asof, close=None, vol_ratio=None):
    """对单票回 harvest 证据 + v0 归类。asof=信号日 YYYYMMDD int。"""
    ev = {}
    # 业绩预告(30日内)
    d30 = (datetime.strptime(str(asof), "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    rows = _q("SELECT announce_date, forecast_type, growth_min, growth_max FROM earnings_forecast "
              "WHERE code LIKE ? AND announce_date>=? AND announce_date<=? ORDER BY announce_date DESC LIMIT 3",
              CAL_DB, (f"%{code}", d30, str(asof)))
    forecasts = [r for r in rows if any(k in (r[1] or "") for k in POSITIVE_TYPES)]
    if forecasts:
        ev["业绩预告"] = [{"date": r[0], "type": r[1], "growth": [r[2], r[3]]} for r in forecasts]
    # 资金双口径5日
    flow = _q("SELECT em_5d, ths_5d, trade_date FROM flow_snapshot WHERE code LIKE ? "
              "ORDER BY trade_date DESC LIMIT 1", FLOW_DB, (f"%{code}",))
    if flow and flow[0][0] is not None and flow[0][1] is not None:
        em5, ths5, fdate = float(flow[0][0]), float(flow[0][1]), flow[0][2]
        ev["资金5日"] = {"em_5d": round(em5, 0), "ths_5d": round(ths5, 0), "asof": fdate}
        dual_positive = em5 > 0 and ths5 > 0
    else:
        dual_positive = False
    # 归类(优先级: 业绩 > 资金; 双命中复合)
    if forecasts and dual_positive:
        cls = "业绩+资金共振"
    elif forecasts:
        cls = "业绩驱动"
    elif dual_positive:
        cls = "资金推动"
    else:
        cls = "待归类"
    return {"code": code, "driver_class_v0": cls, "evidence": ev,
            "price_volume": {"pattern": "20日新高突破+放量", "vol_ratio": vol_ratio,
                             "chg1d": None},
            "classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "method": "v0_heuristic(兑现判定标准待主人方法论定义)"}


def classify_seats(entries, asof):
    out = {}
    for e in entries:
        code = str(e["code"])
        c = classify_one(code, asof, close=e.get("prev_close"), vol_ratio=None)
        c["close"] = e.get("prev_close")
        e["class_v0"] = c["driver_class_v0"]
        out[code] = c
    return out


def stamp_file(path, asof=None):
    entries = json.load(open(path, encoding="utf-8"))
    if not entries:
        print("空批次, 跳过")
        return
    asof_i = asof or _infer_asof(entries)
    cls = classify_seats(entries, asof_i)
    rec = {"signal_date": asof_i, "classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "classifications": cls}
    out = os.path.join(BASE, "data", "breakout_classifications.jsonl")
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for e in entries:
        e["class_v0"] = cls[e["code"]]["driver_class_v0"]
    json.dump(entries, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已标 {path}: " + ", ".join(f"{c}={cls[c]['driver_class_v0']}" for c in cls))


def _infer_asof(entries):
    """手写批次的信号日=快照基准日(qfq最新日, 与扫描器/管线 asof 同源)。"""
    con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    m = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    con.close()
    return int(m)


if __name__ == "__main__":
    if "--stamp" in sys.argv:
        path = sys.argv[sys.argv.index("--stamp") + 1]
        asof = None
        if "--asof" in sys.argv:
            asof = int(sys.argv[sys.argv.index("--asof") + 1])
        stamp_file(path, asof)
    else:
        print("用法: --stamp <batch.json> [--asof YYYYMMDD]")
