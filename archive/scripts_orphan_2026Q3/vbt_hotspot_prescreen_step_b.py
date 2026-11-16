#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""hotspot_breakout 参数 OAT 预筛 · Step B：VectorBT A股规则化引擎回测（.venv_vbt）。

运行（工作目录 D:\zcodesj）：
  ./.venv_vbt/Scripts/python.exe vbt_hotspot_prescreen_step_b.py

设计：
  - 逐股单列跑（复用 vbt_ashare_adapter.run_ashare / manual_sim_ashare 原组件，adapter 零改动）
    ——停牌股 bar 序列不能进 union 日历，逐股保住 rolling/next-bar 语义
  - 退出流 = "持有≥N日后持续想卖"（ent.cumsum().shift(N)>0）：跌停拦卖次日自动重试，
    与生产卖出重试行为一致；entry 为单次信号（涨停拦买=作废，与生产当日有效挂单一致）
  - 持有期 N ∈ {5,10,20}：衡量信号 forward 质量（open[t+1]→open[t+1+N]，next-bar 可执行口径）
  - B1 引擎对账：BASE×N=10×信号最多3只，vbt trades 逐笔 vs manual_sim_ashare 手工模拟

统计自 vbt records 直接计算（entry/exit price×size 与 fees），不依赖 vbt return 字段口径。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from vbt_ashare_adapter import AshareRules, run_ashare, manual_sim_ashare  # noqa: E402

SIG_DB = r"D:\zcodesj\_cache\vbt_hotspot_prescreen_signals.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_JSON = Path(r"D:\zcodesj\reports\vbt_hotspot_prescreen_results.json")
HOLD_NS = [5, 10, 20]
LOAD_START, LOAD_END = "20210801", "20260827"
SIZE = 100  # 与 PoC/adapter 一致：等权100股，最低佣金对不同价位形成真实差异化摩擦


def load_quotes() -> dict[str, pd.DataFrame]:
    """信号涉及股票行情一次载入（懒不到场，全量 3785 只）。"""
    con = sqlite3.connect(f"file:{SIG_DB}?mode=ro", uri=True, timeout=60)
    codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM signals")]
    con.close()
    q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    ph = ",".join("?" * len(codes))
    df = pd.read_sql_query(
        f"SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        f"WHERE date>=? AND date<=? AND code IN ({ph}) ORDER BY code,date",
        q, params=(LOAD_START, LOAD_END, *codes))
    q.close()
    df["code"] = df["code"].map(lambda c: str(c).split(".")[0].zfill(6))
    out: dict[str, pd.DataFrame] = {}
    for code, g in df.groupby("code"):
        g = g.set_index("date")
        g["prev_close"] = g["close"].shift(1)
        out[code] = g
    return out


def build_streams(df: pd.DataFrame, sig_dates: list[str], n_hold: int):
    """entry 单次信号 + "距最近entry≥N"的持续 exit 流。

    ext[j] = (j - last_entry[j]) >= N（last_entry=entry 行号 ffill）：
    信号行号 i → 买入 i+1 → 首卖 i+N+1（持有恰 N bar）；跌停拦卖后次 bar 自动重试；
    卖出后空仓期间 ext 仍 True 但被状态机忽略，新 entry 重置计数。
    v1 教训：cumsum+shift 版在首笔卖出后持续 True，后续信号买入即卖（avg_hold~1bar）。"""
    ent = pd.Series(False, index=df.index)
    hit = [d for d in sig_dates if d in df.index]
    ent.loc[hit] = True
    pos = pd.Series(np.arange(len(df)), index=df.index)
    ent_pos = pd.Series(np.where(ent.to_numpy(), np.arange(len(df)), np.nan),
                        index=df.index).ffill()
    ext = (pos - ent_pos) >= n_hold
    ext = ext.fillna(False).astype(bool)
    return ent, ext


def trade_stats(pf, n_signals: int) -> dict:
    tr = pf.trades.records_arr
    closed = tr["status"] == 1
    d: dict = {"n_signals": n_signals, "n_trades": int(closed.sum()),
               "n_open": int((~closed).sum())}
    if not closed.any():
        d.update(win_rate_pct=None, avg_ret_pct=None, med_ret_pct=None,
                 avg_fee_drag_pct=None, avg_hold_bars=None)
        return d
    ev = tr["entry_price"][closed].astype(float) * tr["size"][closed].astype(float)
    xv = tr["exit_price"][closed].astype(float) * tr["size"][closed].astype(float)
    ef = tr["entry_fees"][closed].astype(float)
    xf = tr["exit_fees"][closed].astype(float)
    ret = (xv - xf - ev - ef) / (ev + ef)
    d["win_rate_pct"] = round(float((ret > 0).mean() * 100), 2)
    d["avg_ret_pct"] = round(float(ret.mean() * 100), 4)
    d["med_ret_pct"] = round(float(np.median(ret) * 100), 4)
    d["avg_fee_drag_pct"] = round(float(((ef + xf) / (ev + ef)).mean() * 100), 4)
    d["avg_hold_bars"] = round(float((tr["exit_idx"][closed].astype(int)
                                      - tr["entry_idx"][closed].astype(int)).mean()), 2)
    return d


def b1_verify(quotes, sig_by_code: dict[str, list[str]], n_hold: int = 10,
              n_stocks: int = 3) -> list[str]:
    """B1：BASE×N=10×信号最多3只，vbt vs manual_sim_ashare 逐笔对账。"""
    top = sorted(sig_by_code.items(), key=lambda kv: -len(kv[1]))[:n_stocks]
    fails: list[str] = []
    for code, dates in top:
        df = quotes[code]
        ent, ext = build_streams(df, dates, n_hold)
        pf, *_ = run_ashare(df, ent, ext, size=SIZE, code=code)
        man_eq, man_fee, man_ep, man_xp = manual_sim_ashare(
            df, ent, ext, size=SIZE, code=code)
        tr = pf.trades.records_arr
        vbt_eq = float(pf.value().iat[-1, 0])
        if len(tr) != len(man_ep):
            fails.append(f"{code}: 笔数 vbt={len(tr)} man={len(man_ep)}")
            continue
        if len(tr):
            dev_ep = float(np.max(np.abs(tr["entry_price"].astype(float) - np.array(man_ep))))
            if dev_ep >= 1e-9:
                fails.append(f"{code}: entry_price 偏差 {dev_ep:.2e}")
            closed = tr["status"] == 1
            if closed.any():
                dev_xp = float(np.max(np.abs(
                    tr["exit_price"][closed].astype(float) - np.array(man_xp))))
                if dev_xp >= 1e-9:
                    fails.append(f"{code}: exit_price 偏差 {dev_xp:.2e}")
            vbt_fee = float(tr["entry_fees"].astype(float).sum()
                            + tr["exit_fees"].astype(float).sum())
            if abs(vbt_fee - man_fee) >= 1e-6:
                fails.append(f"{code}: 费用 vbt={vbt_fee:.6f} man={man_fee:.6f}")
        if abs(vbt_eq - man_eq) / man_eq >= 1e-9:
            fails.append(f"{code}: 权益 vbt={vbt_eq:.2f} man={man_eq:.2f}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="仅跑指定 param_id（逗号分隔，如 P90,P91）")
    ap.add_argument("--out-suffix", default="", help="输出 JSON 文件名后缀")
    a = ap.parse_args()
    t0 = time.time()
    con = sqlite3.connect(f"file:{SIG_DB}?mode=ro", uri=True, timeout=60)
    params = con.execute("SELECT param_id, axis, is_base FROM params ORDER BY param_id").fetchall()
    if a.only:
        keep = {x.strip() for x in a.only.split(",") if x.strip()}
        params = [p for p in params if p[0] in keep]
        if not params:
            print(f"[ERROR] --only {a.only} 未命中任何 param_id")
            return 2
    sig_rows = con.execute("SELECT param_id, code, signal_date FROM signals").fetchall()
    window = con.execute("SELECT v FROM meta WHERE k='window'").fetchone()[0]
    con.close()
    sig_map: dict[str, dict[str, list[str]]] = {}
    keep_ids = {p[0] for p in params}
    for pid, code, d in sig_rows:
        if pid not in keep_ids:
            continue
        sig_map.setdefault(pid, {}).setdefault(code, []).append(d)
    print(f"[info] 参数组合 {len(params)}，信号组 {len(sig_map)}")

    quotes = load_quotes()
    print(f"[info] 行情载入 {len(quotes)} 只，{time.time()-t0:.0f}s")

    # B1 引擎对账（先于统计；失败 exit 2）。--only 子集模式跳过：引擎同 run_ashare，
    # 已在上轮 BASE 全量对账（本脚本不带 --only 时执行）。
    if not a.only:
        base_pid = next(pid for pid, ax, b in params if b == 1)
        fails = b1_verify(quotes, sig_map[base_pid])
        if fails:
            print(f"[FAIL] B1 引擎对账 {len(fails)} 处：")
            for x in fails:
                print("  ", x)
            return 2
        print("[PASS] B1 引擎对账：BASE×N=10×3只 逐笔价格/费用/权益全一致（vbt==手工模拟）")

    rules = AshareRules()
    results = []
    for pid, axis, is_base in params:
        codes_sig = sig_map.get(pid, {})
        for n_hold in HOLD_NS:
            agg = {"n_signals": 0, "n_trades": 0, "n_open": 0,
                   "rets": [], "fees_drag": [], "holds": [], "bb": 0, "bs": 0}
            for code, dates in codes_sig.items():
                df = quotes.get(code)
                if df is None or len(df) < 30:
                    continue
                ent, ext = build_streams(df, dates, n_hold)
                pf, ents_f, exts_f, masks = run_ashare(df, ent, ext, size=SIZE, code=code)
                ent_shift = ent.shift(1).fillna(False)
                ext_shift = ext.shift(1).fillna(False)
                agg["bb"] += int((ent_shift & (masks["suspended"] | masks["limit_up_open"])).sum())
                agg["bs"] += int((ext_shift & (masks["suspended"] | masks["limit_dn_open"])).sum())
                tr = pf.trades.records_arr
                closed = tr["status"] == 1
                agg["n_signals"] += len(dates)
                agg["n_trades"] += int(closed.sum())
                agg["n_open"] += int((~closed).sum())
                if closed.any():
                    ev = tr["entry_price"][closed].astype(float) * tr["size"][closed].astype(float)
                    xv = tr["exit_price"][closed].astype(float) * tr["size"][closed].astype(float)
                    ef = tr["entry_fees"][closed].astype(float)
                    xf = tr["exit_fees"][closed].astype(float)
                    agg["rets"].extend((((xv - xf - ev - ef) / (ev + ef)) * 100).tolist())
                    agg["fees_drag"].extend((((ef + xf) / (ev + ef)) * 100).tolist())
                    agg["holds"].extend((tr["exit_idx"][closed].astype(int)
                                         - tr["entry_idx"][closed].astype(int)).tolist())
            r = {"param_id": pid, "axis": axis, "is_base": bool(is_base), "N": n_hold,
                 "n_signals": agg["n_signals"], "n_trades": agg["n_trades"], "n_open": agg["n_open"],
                 "blocked_buy": agg["bb"], "blocked_sell": agg["bs"]}
            if agg["rets"]:
                r["win_rate_pct"] = round(float(np.mean([x > 0 for x in agg["rets"]])) * 100, 2)
                r["avg_ret_pct"] = round(float(np.mean(agg["rets"])), 4)
                r["med_ret_pct"] = round(float(np.median(agg["rets"])), 4)
                r["avg_fee_drag_pct"] = round(float(np.mean(agg["fees_drag"])), 4)
                r["avg_hold_bars"] = round(float(np.mean(agg["holds"])), 2)
            else:
                r.update(win_rate_pct=None, avg_ret_pct=None, med_ret_pct=None,
                         avg_fee_drag_pct=None, avg_hold_bars=None)
            results.append(r)
        print(f"  {pid} [{axis}] done，{time.time()-t0:.0f}s")

    out_path = OUT_JSON.with_name(OUT_JSON.stem + a.out_suffix + OUT_JSON.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"window": window, "size": SIZE, "hold_ns": HOLD_NS,
                 "equivalent_N": f"{len(params)}组合×{len(HOLD_NS)}持有期={len(params)*len(HOLD_NS)}次评估（B3红线）",
                 "b1": "PASS" if not a.only else "SKIP(上轮BASE已对账)",
                 "a1": "PASS（StepA 30抽样）",
                 "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "engine": "vbt_ashare_adapter.run_ashare（T+1/涨跌停/停牌/最低佣金5元/卖印税/100股）"},
        "results": results,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] {out_path} | {len(results)} 行 | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
