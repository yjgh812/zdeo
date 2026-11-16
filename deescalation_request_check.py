#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deescalation_request_check.py — 存量减仓提请档·第二层个股级窄提请（PD-012② 2026-10-02 主人「继续」批准落地）

设计稿=reports/deescalation_proposal_design_20260920.md 方案B（窄提请档），逐条实现：
  触发(AND缺一不可):
    1) 结构风险事实(任一): 单票占比>20% ∨ 同簇占比>15%且簇内止损带≥2只 ∨ 止损带(-8%,-5%]拥挤≥3只
    2) 红档双口径一致: market_state_probe.state==LOW_VOL ∧ regime_gate_shadow(env≥80 ∨ LOW_VOL连续≥3日)
  对象(全部满足): 非前5赢家(浮盈排序) ∧ (空头排列 ∨ 浮亏≤-5%) ∧ 浮亏>-8%(非S-5域,S-5铁律不重复提请)
  流程: 提请入 pending_decisions_ledger(id=PD-DEC-<date>-<code>, SLA=2交易日, 走PendingAging升级,
        不即时toast); 同标的冷却5交易日(≈7日历日保守近似)/同簇冷却; 每次强制附9/18反例数据行。
  边界: 纯regime触发永不提请(9/18反例); 红档单独永不触发; S-5/趋势走坏/主人指令三元裁决不动。
数据源: westock实时持仓+portfolio(唯一真值), stock_sector_sw1(簇), qfq 20根(空头排列),
        data/market_state_probe.json(20:00) + data/regime_gate_shadow.jsonl(21:10)——故调度21:30。
用法: python deescalation_request_check.py [--dry]
退出码: 0=正常(含未触发/非交易日/冷却跳过) / 3=真值源失败fail-closed
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "pending_decisions_ledger.jsonl")
COOL = os.path.join(ROOT, "data", "deescalation_state.jsonl")
PROBE = os.path.join(ROOT, "data", "market_state_probe.json")
SHADOW = os.path.join(ROOT, "data", "regime_gate_shadow.jsonl")
COUNTEREXAMPLE = ("反例防线(9/18): LOW_VOL连续确认的次日即修复日(up_pct74.1/组合显著反弹,百亚+2541元级),"
                  "滞后regime信号驱动的减仓会卖在修复日早晨——请两面读后再裁")
SINGLE_CAP, CLUSTER_CAP, CLUSTER_STOPBAND, STOPBAND_CROWD = 0.20, 0.15, 2, 3
COOL_CAL_DAYS = 7  # 5交易日≈7日历日保守近似


def _read_jsonl(p):
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def _today():
    return time.strftime("%Y%m%d")


def _red_flag():
    try:
        probe = json.load(open(PROBE, encoding="utf-8"))
        red1 = str(probe.get("state") or "") == "LOW_VOL"
    except Exception as e:
        return False, f"market_state_probe 读取失败({e})"
    rows = sorted(_read_jsonl(SHADOW), key=lambda r: str(r.get("date")), reverse=True)[:10]
    if not rows:
        return False, "regime_gate_shadow 空"
    latest = rows[0]
    red2_env = float(latest.get("env") or 0) >= 80
    streak = 0
    for r in rows:
        if str(r.get("state")) == "LOW_VOL":
            streak += 1
        else:
            break
    red2 = red2_env or streak >= 3
    return (red1 and red2), (f"probe={probe.get('state')}/{probe.get('asof')} "
                             f"shadow_env={latest.get('env')}/LOW_VOL连续{streak}日")


def _westock():
    if r"D:\workbuddyspace" not in sys.path:
        sys.path.insert(0, r"D:\workbuddyspace")
    from westock_client import WeStockClient
    ws = WeStockClient()
    pos = ((ws.positions().get("data") or {}).get("positions")) or []
    pf = ws.portfolio() or {}
    inner = ((pf.get("data") or {}).get("portfolio")) or pf.get("data") or pf
    total = float(inner.get("totalAssets") or inner.get("total_assets") or 0)
    out = []
    for p in pos:
        c6 = "".join(ch for ch in str(p.get("code", "")) if ch.isdigit()).zfill(6)[-6:]
        q = float(p.get("quantity") or p.get("qty") or 0)
        px = float(p.get("currentPrice") or p.get("current_price") or 0)
        cost = float(p.get("costPrice") or p.get("cost_price") or 0)
        if c6 and q > 0 and px > 0 and cost > 0:
            out.append({"code": c6, "name": str(p.get("name") or ""),
                        "qty": q, "px": px, "cost": cost,
                        "value": px * q, "pnl_pct": px / cost - 1})
    return out, total


def _cluster_of(codes):
    con = sqlite3.connect(r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro",
                          uri=True, timeout=10)
    out = {}
    for c in codes:
        pref = ("sh" if c.startswith(("600", "601", "603", "605", "688", "689"))
                else ("bj" if c.startswith(("8", "920", "430", "4")) else "sz"))
        r = con.execute("SELECT sector_name FROM stock_sector_sw1 WHERE code=?", (pref + c,)).fetchone()
        out[c] = r[0] if r and r[0] else ""
    con.close()
    return out


def _bear_alignment(c6):
    """简版空头排列: MA5<MA10<MA20 且 close<MA20 (qfq 21根; 取数失败=False 由贴止损条件兜底)"""
    try:
        con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True, timeout=10)  # 2026-10-13 切wb唯一真源(旧zc副本停更10/09已废, 409ef2c域); 按code逐只查零口径变化
        rows = con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code LIKE ? ORDER BY date DESC LIMIT 21",
            (f"%{c6}",)).fetchall()
        con.close()
        closes = [float(r[0]) for r in rows if r[0]]
        if len(closes) < 20:
            return False
        ma = lambda k: sum(closes[:k]) / k
        return closes[0] < ma(20) and ma(5) < ma(10) < ma(20)
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="演练: 只打印不落台账")
    a = ap.parse_args()
    today = _today()
    from trade_day_guard import is_trading_day
    if not is_trading_day(today):
        print(f"[GUARD] {today} 非交易日 → 跳过")
        return 0
    red, why = _red_flag()
    print(f"[RED] 红档双口径={red} ({why})")
    if not red:
        print("[OK] 非红档 → 不提请(纯regime/未双确认永不触发)")
        return 0
    try:
        pos, total = _westock()
    except Exception as e:
        print(f"[FAIL-CLOSED] westock 真值源失败({type(e).__name__}: {e}) → 本轮不提请")
        return 3
    if not pos or total <= 0:
        print("[OK] 持仓/总资产不可得 → 不提请")
        return 0
    clusters = _cluster_of([p["code"] for p in pos])
    stopband = [p for p in pos if -0.08 < p["pnl_pct"] <= -0.05]
    winners5 = {p["code"] for p in sorted(pos, key=lambda x: x["value"] * x["pnl_pct"], reverse=True)[:5]}
    fact_single = max(p["value"] for p in pos) / total > SINGLE_CAP
    cl_val, cl_stop = {}, {}
    for p in pos:
        cl = clusters.get(p["code"]) or "未知"
        cl_val[cl] = cl_val.get(cl, 0) + p["value"]
        if p in stopband:
            cl_stop[cl] = cl_stop.get(cl, 0) + 1
    fact_cluster = any(v / total > CLUSTER_CAP and cl_stop.get(k, 0) >= CLUSTER_STOPBAND
                       for k, v in cl_val.items())
    fact_crowd = len(stopband) >= STOPBAND_CROWD
    fact = fact_single or fact_cluster or fact_crowd
    print(f"[FACT] 单票>20%={fact_single} 同簇>15%∧止损带≥2={fact_cluster} 止损带≥3只={fact_crowd} → 触发={fact}")
    if not fact:
        print("[OK] 无结构风险事实 → 不提请(红档单独永不触发)")
        return 0
    state_rows = _read_jsonl(COOL)
    # 画像免死(10/2优化迭代): 画像前50%=模型看好→不提请减仓(防误杀好票回踩; 镇海603637案)
    _pf = {}
    try:
        _pfj = json.load(open(os.path.join(ROOT, "data", "portrait_feed.json"), encoding="utf-8"))
        for k, v in (_pfj.get("scores_short") or {}).items():
            if isinstance(v, dict) and "pct" in v:
                _pf[str(k).zfill(6)[-6:]] = float(v["pct"])
    except Exception:
        pass
    now = time.strftime("%F %T")
    new_pd, new_state = [], []
    for p in sorted(pos, key=lambda x: x["pnl_pct"]):
        cl = clusters.get(p["code"]) or "未知"
        if p["code"] in winners5:
            continue
        # 画像免死: 模型前50%=看好, 不提请(提请本身仅呈报非自动卖, 本条=减少噪声)
        _pf_pct = _pf.get(p["code"])
        if _pf_pct is not None and _pf_pct >= 50:
            print(f"[PF-SKIP] {p['code']} {p['name']} 画像前{_pf_pct:.0f}%看好 → 免提请")
            continue
        if not (p["pnl_pct"] <= -0.05 or _bear_alignment(p["code"])):
            continue
        if p["pnl_pct"] <= -0.08:
            continue  # S-5 域, 铁律链已覆盖
        if any(s.get("code") == p["code"] and s.get("date", "0") >= "20260101"
               and (time.mktime(time.strptime(today, "%Y%m%d")) -
                    time.mktime(time.strptime(s["date"], "%Y%m%d"))) / 86400 < COOL_CAL_DAYS
               for s in state_rows):
            print(f"[COOL] {p['code']} {p['name']} 同标的冷却中 → 跳过")
            continue
        if any(s.get("cluster") == cl and cl != "未知" and s.get("date", "0") >= "20260101"
               and (time.mktime(time.strptime(today, "%Y%m%d")) -
                    time.mktime(time.strptime(s["date"], "%Y%m%d"))) / 86400 < COOL_CAL_DAYS
               for s in state_rows):
            print(f"[COOL] {p['code']} {p['name']} 同簇[{cl}]冷却中 → 跳过")
            continue
        reason = (f"存量减仓提请档(PD-012第二层·方案B窄提请): 浮亏{p['pnl_pct'] * 100:.1f}%"
                  f"{'/空头排列' if _bear_alignment(p['code']) else ''}, "
                  f"占总量{p['value'] / total * 100:.1f}%, 簇[{cl}]; "
                  f"触发事实: 单票>20%={fact_single}/同簇拥挤={fact_cluster}/止损带≥3只={fact_crowd}"
                  f"(止损带{len(stopband)}只); {COUNTEREXAMPLE}")
        new_pd.append({"id": f"PD-DEC-{today}-{p['code']}", "title": f"{reason}",
                       "category": "owner_gate", "raised_date": today,
                       "sla_trading_days": 2, "status": "open"})
        new_state.append({"date": today, "code": p["code"], "name": p["name"],
                          "cluster": cl, "ts": now})
        print(f"[提请] {p['code']} {p['name']} 浮亏{p['pnl_pct'] * 100:.1f}% 簇[{cl}]")
    if a.dry:
        print(f"[DRY] 拟提请 {len(new_pd)} 笔(不落台账)")
        return 0
    if new_pd:
        with open(LEDGER, "a", encoding="utf-8") as f:
            for r in new_pd:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(COOL, "a", encoding="utf-8") as f:
            for r in new_state:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[DONE] 提请 {len(new_pd)} 笔 → {LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
