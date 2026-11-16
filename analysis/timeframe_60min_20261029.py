# -*- coding: utf-8 -*-
"""timeframe_60min_20261029.py — 60分钟线聚合模块（从minute_5推导，可复用）

主人令「选股要用上日线和60分钟线」:
  日线 = 方向层(趋势/因子筛选)
  60分钟线 = 确认层(MA5/MA10交叉、量比、趋势方向)
  双周期共振 = 日线信号 + 60分钟确认 → 入场

数据源(双源, 2026-10-30 .lc5 攻克验证后接入):
  主源 DB: dragon_quant_meta.db stock_minute_5 (1.07亿行, 9411只, fresh到当日) — 历史回测
  备源 vipdoc: fzline .lc5 直读 (通达信收盘后自动写盘, ~191交易日×48根5min/日) — 当日即时/DB缺数据时
  单票序列整段取自同一源, 禁止跨源混算(vol单位口径可能差×100, 量比比值指标内部自洽不受影响)
⚠️ 除权跨窗风险(20261030审计): 分钟线均为不复权原始价, 若除权日落在 MA10 覆盖段(末端
  2.5个交易日)内, MA5/MA10 会被除权跳变污染出假金叉/死叉。防护: 窗口起点须早于末端
  MA周期(≥10根=2.5交易日)之前无除权事件; 或调用方传入分红数据做因子归一。
  20261030实证: 143只持仓窗口内3只除权票(600380/600685/601919), 除权点均在MA10窗口外,
  复权口径trend与原始口径全部一致, 本窗口零污染(qfq日线因子对拍)。
聚合规则: A股4小时交易 → 4根60min/日
  H1: 09:35-10:30 | H2: 10:35-11:30 | H3: 13:05-14:00 | H4: 14:05-15:00
输出: 每股60min OHLCV序列 + MA5/MA10 + 金叉/死叉 + 量比 + 趋势方向 (+ src来源标注)
"""
from __future__ import annotations
import sqlite3, sys, os, math
from collections import defaultdict
import numpy as np

# tdx_reader(.lc5 唯一读取 owner)位于项目根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tdx_reader import read_min5 as _lc5_read

META_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db"

# A股 60min 分段
SEGMENTS = [
    (935, 1030, "H1"),
    (1035, 1130, "H2"),
    (1305, 1400, "H3"),
    (1405, 1500, "H4"),
]


def get_60min_bars(con, code: str, date: int) -> list:
    """单票单日 60min OHLCV bars"""
    rows = con.execute(
        "select time, open, high, low, close, volume from stock_minute_5 "
        "where code=? and date=? and volume>0 order by time", (code, date)).fetchall()
    if len(rows) < 12:
        return []
    bars = []
    for t_lo, t_hi, label in SEGMENTS:
        seg = [r for r in rows if t_lo <= r[0] <= t_hi]
        if not seg:
            continue
        bars.append({
            "label": label, "open": seg[0][1],
            "high": max(r[2] for r in seg), "low": min(r[3] for r in seg),
            "close": seg[-1][4], "vol": sum(r[5] for r in seg),
        })
    return bars


def _series_metrics(all_closes: list, all_vols: list, daily_detail: dict) -> dict:
    """60min序列 → MA5/MA10/趋势/量比/动量（DB 与 vipdoc 两路线共用，口径唯一）"""
    n = len(all_closes)
    if n == 0:
        return {"status": "无数据", "bars_60min": 0}
    ma5 = np.mean(all_closes[-5:]) if n >= 5 else None
    ma10 = np.mean(all_closes[-10:]) if n >= 10 else None
    cur = all_closes[-1]
    # 量比: 最近4根60min均量 / 前20根60min均量
    vol_recent = np.mean(all_vols[-4:]) if n >= 4 else None
    vol_base = np.mean(all_vols[-20:-4]) if n >= 24 else None
    vol_ratio = vol_recent / vol_base if vol_base and vol_base > 0 else None
    # 趋势: MA5 vs MA10
    if ma5 and ma10:
        trend = "金叉" if ma5 > ma10 else "死叉"
    else:
        trend = "中性"
    # 动量: 最近2根60min涨跌
    momentum = (all_closes[-1] / all_closes[-2] - 1) if n >= 2 else 0
    return {
        "status": "OK",
        "bars_60min": n,
        "cur_close": cur,
        "ma5": round(ma5, 3) if ma5 else None,
        "ma10": round(ma10, 3) if ma10 else None,
        "trend": trend,
        "momentum_pct": round(momentum * 100, 2),
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "daily_detail": daily_detail,
    }


def get_60min_series(con, code: str, dates: list) -> dict:
    """跨日60min序列(DB路线) → MA5/MA10/趋势/量比"""
    dates = sorted(dates)  # 20261030防御: 调用方传倒序会倒放序列出假交叉(鲁阳节能8.41vs日线7.74实锤), 升序化根治
    all_closes = []
    all_vols = []
    daily_detail = {}
    for d in dates:
        bars = get_60min_bars(con, code, d)
        if not bars:
            continue
        daily_detail[d] = bars
        for b in bars:
            all_closes.append(b["close"])
            all_vols.append(b["vol"])
    return _series_metrics(all_closes, all_vols, daily_detail)


# ---------- vipdoc .lc5 直读备源 ----------
def get_60min_bars_vipdoc(code: str, date: int, rows_cache: dict = None) -> list:
    """单票单日 60min bars（vipdoc .lc5 直读）。聚合规则与 DB 版一致（SEGMENTS，vol>0 剔零成交平移bar）。"""
    rows = (rows_cache or {}).get(code)
    if rows is None:
        rows = _lc5_read("", code)   # read_min5(market, code): 首参为 market, 留空由 _normalize 推断
        if rows_cache is not None:
            rows_cache[code] = rows
    if not rows:
        return []
    day = [r for r in rows if r["date"] == date and r["vol"] > 0]
    if len(day) < 12:
        return []
    bars = []
    for t_lo, t_hi, label in SEGMENTS:
        seg = [r for r in day if t_lo <= r["time"] <= t_hi]
        if not seg:
            continue
        bars.append({
            "label": label, "open": seg[0]["o"],
            "high": max(r["h"] for r in seg), "low": min(r["l"] for r in seg),
            "close": seg[-1]["c"], "vol": sum(r["vol"] for r in seg),
        })
    return bars


def get_60min_series_vipdoc(code: str, dates: list, cache: dict = None) -> dict:
    """跨日60min序列(vipdoc .lc5 直读) → 输出结构与 get_60min_series 完全同构。"""
    dates = sorted(dates)  # 同上防御
    rows_cache = cache if cache is not None else {}
    all_closes, all_vols, daily_detail = [], [], {}
    for d in dates:
        bars = get_60min_bars_vipdoc(code, d, rows_cache)
        if not bars:
            continue
        daily_detail[d] = bars
        for b in bars:
            all_closes.append(b["close"])
            all_vols.append(b["vol"])
    return _series_metrics(all_closes, all_vols, daily_detail)


def batch_60min_status_auto(codes: list, recent_dates: list, meta_db: str = META_DB) -> dict:
    """单一入口: 逐票优先 DB minute_5，该票无数据自动落 vipdoc .lc5 直读（不跨源混算）。
    返回 {code: status_dict}，status_dict 附 "src": "db"|"vipdoc"|"无数据"。
    """
    out = {}
    con = None
    try:
        con = sqlite3.connect(f"file:{meta_db}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"[timeframe_60min] DB 只读连接失败，全量走 vipdoc: {e}")
        con = None
    lc5_cache = {}
    for code in codes:
        st = get_60min_series(con, code, recent_dates) if con is not None else None
        if st and st.get("status") == "OK":
            st["src"] = "db"
            out[code] = st
        else:
            st2 = get_60min_series_vipdoc(code, recent_dates, cache=lc5_cache)
            st2["src"] = "vipdoc" if st2.get("status") == "OK" else "无数据"
            out[code] = st2
    if con is not None:
        con.close()
    return out


def batch_60min_status(con, codes: list, recent_dates: list) -> dict:
    """批量获取多票60min状态 → {code: status_dict}"""
    out = {}
    for code in codes:
        out[code] = get_60min_series(con, code, recent_dates)
    return out


def apply_60min_filter(picks: list, status_60: dict, require_golden: bool = True) -> tuple:
    """
    60分钟确认层过滤器(20261030 修复 docstring-实现矛盾: 原实现死叉被宽松模式放行、
    中性两模式均滤, 与文档「排除死叉(允许中性)」及实证结论「死叉过滤有效」相悖):
      require_golden=True  → 只保留金叉票(中性/死叉均过滤; 无数据恒放行不误杀)
      require_golden=False → 排除死叉票(中性/无数据放行)
    require_golden=True 模式行为与修复前完全一致(实证用例零影响)。
    返回 (通过列表, 被过滤列表)
    """
    passed, filtered = [], []
    for item in picks:
        code = item[0] if isinstance(item, tuple) else item
        st = status_60.get(code, {})
        trend = st.get("trend", "无数据")
        if trend == "无数据":
            passed.append(item)          # 无60min数据不过滤
        elif trend == "金叉":
            passed.append(item)
        elif trend == "死叉":
            filtered.append((item, trend))   # 两模式均过滤死叉(实证: 死叉过滤有效)
        else:  # 中性
            if require_golden:
                filtered.append((item, trend))
            else:
                passed.append(item)
    return passed, filtered


# ---------- 独立运行: 示例 + 双源对拍自检 ----------
def verify_vipdoc_vs_db(codes: list, recent_dates: list) -> int:
    """DB vs vipdoc 双源对拍: 价格字段(cur_close/ma5/ma10/momentum_pct)必须一致;
    vol/量比只报告比值(单位口径可能差×100, 属预期)。mism>0 返回 1。"""
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    mism = 0
    for code in codes:
        a = get_60min_series(con, code, recent_dates)
        b = get_60min_series_vipdoc(code, recent_dates)
        if a.get("status") != "OK" or b.get("status") != "OK":
            print(f"[对拍] {code}: DB={a.get('status')} vipdoc={b.get('status')} 跳过(缺源)")
            continue
        for k in ("cur_close", "ma5", "ma10", "momentum_pct"):
            va, vb = a.get(k), b.get(k)
            if va is None and vb is None:
                continue
            if va is None or vb is None or abs(float(va) - float(vb)) > 0.01:
                mism += 1
                print(f"[对拍] {code} {k} 不一致: db={va} vipdoc={vb}")
        vol_ratio = None
        try:
            da = a["daily_detail"]; db_ = b["daily_detail"]
            va = sum(x["vol"] for d in da for x in da[d])
            vb = sum(x["vol"] for d in db_ for x in db_[d])
            if va > 0 and vb > 0:
                vol_ratio = vb / va
        except Exception:
            pass
        print(f"[对拍] {code}: cur_close db={a['cur_close']} vipdoc={b['cur_close']} "
              f"trend db={a['trend']} vipdoc={b['trend']} "
              f"vol(vip/db比)={vol_ratio and round(vol_ratio, 2)}")
    con.close()
    print(f"[对拍] 结论: {'双源一致' if mism == 0 else f'{mism} 笔不一致, FAIL'}")
    return 1 if mism else 0


if __name__ == "__main__":
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    # 示例: 3只 revival 持仓的 60min 状态
    demo_codes = ["000001", "601939", "000559"]
    recent = [20261023, 20261027, 20261028, 20261029]
    for code in demo_codes:
        r = get_60min_series(con, code, recent)
        name = {"000001": "平安银行", "601939": "建设银行", "000559": "万向钱潮"}.get(code, code)
        print(f"\n{name}({code}):")
        for k, v in r.items():
            if k != "daily_detail":
                print(f"  {k}: {v}")
    con.close()
    print("\n=== 双源对拍自检(DB vs vipdoc .lc5) ===")
    sys.exit(verify_vipdoc_vs_db(demo_codes, recent))
