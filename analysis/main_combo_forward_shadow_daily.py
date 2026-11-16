# -*- coding: utf-8 -*-
"""主组合前向机械影子臂 daily runner（PREREG_MAIN_COMBO_FORWARD_SHADOW ACTIVE 2026-10-18）

架构 = 引擎整套机制复用（重算幂等，与 low_repair/dipflush 影子家族同构）：
  - 薄壳 import 屠龙回测引擎 tulong_backtest_full_report，进程内覆盖 START_DATE=锚/END_DATE=最新 qfq 日；
  - 进程内 monkey-patch BacktestEngineV4.liquidate_all：end_of_sample 假清仓 no-op
    （影子语义=持仓延续、净值连续），不改 factor_engine 任何生产文件；
  - 参数单一真源 = data/PREREG_MAIN_COMBO_FORWARD_SHADOW_DRAFT_20261018.json（缺字段 fail-closed）。

三件产物（原子覆盖）：
  data/main_combo_fwd_shadow_ledger.jsonl / main_combo_fwd_shadow_state_latest.json / main_combo_fwd_shadow_brief_latest.txt

边界：纯记账零交易权——不接 executor、不下单、不触 practice_190。
复现模式：--anchor 2024-01-01 --end 2026-10-16 --tag repro2024 --ledger-dir <dir>
"""
import argparse
import glob
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"D:/zcodesj"
QFQ_PATH = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"


def fail(msg):
    print(f"[fwd_shadow][FAIL-CLOSED] {msg}")
    sys.exit(2)


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", default="", help="prereg 路径；缺省按名字模式发现")
    ap.add_argument("--anchor", default="", help="复现模式：覆盖窗口起点 YYYY-MM-DD")
    ap.add_argument("--end", default="", help="复现模式：覆盖窗口终点 YYYY-MM-DD")
    ap.add_argument("--tag", default="", help="复现模式：产物文件名标签")
    ap.add_argument("--ledger-dir", default="", help="复现模式：产物目录（缺省=data/ 生产路径）")
    return ap.parse_args()


def load_prereg(path_hint):
    path = path_hint
    if not path:
        hits = sorted(glob.glob(os.path.join(ROOT, "data", "PREREG_MAIN_COMBO_FORWARD_SHADOW*.json")))
        if not hits:
            fail("prereg 文件未发现（data/PREREG_MAIN_COMBO_FORWARD_SHADOW*.json）")
        path = hits[-1]
    if not os.path.exists(path):
        fail(f"prereg 不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        prereg = json.load(f)
    if prereg.get("status") != "ACTIVE":
        fail(f"prereg status={prereg.get('status')} 非 ACTIVE，影子臂不得运行")
    p = prereg.get("params") or {}
    for k in ("activation_anchor", "weights", "capital_book", "arms", "ledger_only"):
        if k not in p:
            fail(f"prereg params 缺字段: {k}")
    if not p.get("ledger_only"):
        fail("prereg ledger_only 非 true，拒绝运行")
    mech = p.get("mechanics_verified") or {}
    for k in ("rebalance", "entry", "eos_suppression"):
        if k not in mech:
            fail(f"prereg mechanics_verified 缺字段: {k}")
    return path, prereg, p


def qfq_max_date():
    if not os.path.exists(QFQ_PATH):
        fail(f"qfq 库不存在: {QFQ_PATH}")
    conn = sqlite3.connect(QFQ_PATH)
    try:
        row = conn.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        fail("qfq stock_daily_meta_qfq MAX(date) 为空")
    s = str(row[0])
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def dump_batch_lists(eng, loader, end, out_path):
    """当日生产批次名单 dump（20261019「按推荐继续」激活 PREREG_VR120_FILTER_20261018 的输入件）。

    保真 by construction: 同 loader 同信号函数取真 Top15/20; 补位池=镜像选股管线取
    Top25/Top30, 其前 15/20 与真信号输出断言相等(不一致 fail-closed, 防镜像漂移)。
    纯增量写文件, 零行为改动(影子臂/组合/台账逻辑不受影响)。"""
    df = loader.get_fundamental_factors(end)
    bp_pool = []
    if df is not None and len(df) >= 20:
        df = df[df["close"] <= 30].copy()
        if len(df) >= 20:
            df["ep_rank"] = df["ep"].rank(pct=True)
            df["bp_rank"] = df["bp"].rank(pct=True)
            df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
            top = df.nlargest(25, "score")
            bp_pool = [(str(c).zfill(6), round(float(r["score"]), 6), round(float(r["close"]), 4))
                       for c, r in top.iterrows()]
    w_bp, _ = eng.bp_ep_signal(loader, end)
    bp_true = [str(c).zfill(6) for c in w_bp.keys()]
    if [c for c, _, _ in bp_pool[:15]] != bp_true:
        fail(f"bp_ep 镜像池前15与真信号不一致: mirror={[c for c, _, _ in bp_pool[:15]]} true={bp_true}")
    mldf = loader.get_ml_signals(end)
    ml_pool = []
    if mldf is not None and len(mldf) > 0:
        col = mldf.columns[0]
        cand = mldf[mldf[col].notna()]
        if len(cand) >= 20:
            top = cand.nlargest(30, col)
            ml_pool = [(str(c).zfill(6), round(float(r[col]), 6)) for c, r in top.iterrows()]
    w_ml, _ = eng.ml_signal(loader, end)
    ml_true = [str(c).zfill(6) for c in w_ml.keys()]
    if [c for c, _ in ml_pool[:20]] != ml_true:
        fail(f"ml 镜像池前20与真信号不一致: mirror={[c for c, _ in ml_pool[:20]]} true={ml_true}")
    snap = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "batch_date": end,
        "bp_ep_pool": [{"code": c, "score": s, "close": cl} for c, s, cl in bp_pool],
        "ml_pool": [{"code": c, "ml_prob": p_} for c, p_ in ml_pool],
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    print(f"[fwd_shadow] 批次名单dump: bp_ep={len(bp_pool)} ml={len(ml_pool)} @ {end} -> {out_path}")


def main():
    t0 = time.time()
    args = parse_args()
    prereg_path, prereg, p = load_prereg(args.prereg)
    repro = bool(args.anchor or args.end or args.tag or args.ledger_dir)

    anchor = args.anchor or p["activation_anchor"]
    end = args.end or qfq_max_date()
    if len(anchor) != 10 or len(end) != 10:
        fail(f"日期格式须 YYYY-MM-DD: anchor={anchor} end={end}")
    if end <= anchor:
        fail(f"窗口非法: end={end} <= anchor={anchor}")

    tag = args.tag or ""
    ledger_dir = args.ledger_dir or os.path.join(ROOT, "data")
    os.makedirs(ledger_dir, exist_ok=True)
    stem = f"main_combo_fwd_shadow_{tag}" if tag else "main_combo_fwd_shadow"
    ledger_path = os.path.join(ledger_dir, f"{stem}_ledger.jsonl")
    state_path = os.path.join(ledger_dir, f"{stem}_state_latest.json")
    brief_path = os.path.join(ledger_dir, f"{stem}_brief_latest.txt")
    engine_out = os.path.join(ledger_dir, f"{stem}_engine_out")
    os.makedirs(engine_out, exist_ok=True)

    # ---- env 必须在引擎 import 前就位 ----
    os.environ["TULONG_BT_OUT"] = engine_out
    os.environ["TULONG_BPEP_MAX_HOLDING"] = "0"   # 证伪 S-5: bp_ep 无时间退出
    os.environ["TULONG_ML_MAX_HOLDING"] = "20"    # PREREG_ML_H20_WFA 批准默认
    os.environ.setdefault("TULONG_BPEP_PORTRAIT_FILTER", "")  # 画像过滤旋钮默认关

    sys.path.insert(0, ROOT)
    t_imp = time.time()
    import tulong_backtest_full_report as eng  # noqa: E402
    print(f"[fwd_shadow] 引擎模块 import 完成 {time.time()-t_imp:.1f}s")

    # ---- 进程内覆盖窗口（引擎全局量在调用时读取） ----
    eng.START_DATE = anchor
    eng.END_DATE = end

    # ---- EOS 抑制补丁（影子语义：窗口末不假清仓，持仓延续） ----
    from core.backtest_system.engine import BacktestEngineV4  # noqa: E402
    _orig_liquidate = BacktestEngineV4.liquidate_all

    def _liquidate_eos_noop(self, prices, date, opens=None, prev_closes=None,
                            is_st_map=None, names=None, exit_reason="", **kw):
        if exit_reason == "end_of_sample":
            return
        return _orig_liquidate(self, prices, date, opens=opens, prev_closes=prev_closes,
                               is_st_map=is_st_map, names=names, exit_reason=exit_reason, **kw)

    BacktestEngineV4.liquidate_all = _liquidate_eos_noop

    # ---- 数据装载 + 两臂运行 ----
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("BacktestEngineV4").setLevel(logging.WARNING)

    t_loader = time.time()
    loader = eng.TulongDataLoader(eng.DB_PATH, eng.QFQ_PATH)
    print(f"[fwd_shadow] 数据装载完成 {time.time()-t_loader:.1f}s")

    # ---- 当日批次名单 dump (vr120 过滤影子输入件, 零行为改动) ----
    dump_batch_lists(eng, loader, end, os.path.join(ROOT, "data", "main_combo_fwd_shadow_batch_latest.json"))

    arms = {}
    for arm_name, sig_fn, hold in (
        ("bp+ep_core", eng.bp_ep_signal, 0),
        ("ml_signal", eng.ml_signal, 20),
    ):
        t_arm = time.time()
        res = eng.run_single_backtest(arm_name, sig_fn, loader, "monthly",
                                      {"max_holding_bars": hold})
        trades = res.get("trades_with_reason") or []
        if any(str(t.get("exit_reason")) == "end_of_sample" for t in trades):
            fail(f"{arm_name} 出现 end_of_sample 成交：EOS 抑制补丁失效，影子语义被破坏")
        eq_df = res.get("equity_curve")
        eq = eq_df[["date", "equity"]].copy()
        eq["date"] = eq["date"].astype(str).str[:10]
        arms[arm_name] = {"res": res, "trades": trades, "eq": eq}
        print(f"[fwd_shadow] {arm_name} 完成 {time.time()-t_arm:.1f}s "
              f"(closed={len(trades)}, 末日权益={float(eq['equity'].iloc[-1]):,.0f})")

    # ---- 组合 50/50（对齐 canonical PortfolioBacktest._combine_results：逐日再平衡收益链） ----
    bp_eq = arms["bp+ep_core"]["eq"].set_index("date")["equity"].astype(float)
    ml_eq = arms["ml_signal"]["eq"].set_index("date")["equity"].astype(float)
    common = bp_eq.index.intersection(ml_eq.index)
    if len(common) == 0:
        fail("两臂交易日无交集，组合无法合成")
    capital = float(p["capital_book"])
    bp_seq = [float(bp_eq.loc[d]) for d in common]
    ml_seq = [float(ml_eq.loc[d]) for d in common]
    combo_vals = []
    prev = capital
    for i in range(len(common)):
        wr = 0.0
        if 0 < i < len(bp_seq) and bp_seq[i - 1] > 0:
            wr += 0.5 * (bp_seq[i] - bp_seq[i - 1]) / bp_seq[i - 1]
        if 0 < i < len(ml_seq) and ml_seq[i - 1] > 0:
            wr += 0.5 * (ml_seq[i] - ml_seq[i - 1]) / ml_seq[i - 1]
        prev = prev * (1 + wr)
        combo_vals.append(prev)
    combo_eq = pd.Series(combo_vals, index=common)

    # ---- 在持仓位（引擎 Position 真身：trades 只记已平 lot，不得用交易流净额推导） ----
    open_positions = []
    for arm_name in ("bp+ep_core", "ml_signal"):
        positions = arms[arm_name]["res"].get("engine").portfolio.positions
        for code, pos in positions.items():
            if getattr(pos, "shares", 0) > 0:
                open_positions.append({
                    "arm": arm_name,
                    "code": str(getattr(pos, "stock_code", code)).zfill(6),
                    "name": str(getattr(pos, "stock_name", "") or ""),
                    "shares": int(pos.shares),
                    "avg_cost": float(getattr(pos, "avg_cost", 0.0)),
                    "open_date": str(getattr(pos, "open_date", "") or "")[:10],
                })

    # ---- 门进度 ----
    approved_at = (prereg.get("approval") or {}).get("approved_at", "2026-10-18")
    act = datetime.strptime(approved_at, "%Y-%m-%d")
    # 12 自然月门
    oos_end_str = f"{act.year + 1}-{act.month:02d}-{act.day:02d}"
    cal_days = max(0, (datetime.strptime(end, "%Y-%m-%d") - act).days)
    n_closed = sum(len(arms[a]["trades"]) for a in arms)
    n_closed_bp = len(arms["bp+ep_core"]["trades"])
    n_closed_ml = len(arms["ml_signal"]["trades"])

    # ---- 台账（原子覆盖） ----
    lines = []
    lines.append(json.dumps({
        "type": "meta", "prereg": os.path.basename(prereg_path), "prereg_status": "ACTIVE",
        "anchor": anchor, "end": end,
        "architecture": "engine-reuse-recompute-idempotent",
        "eos_suppressed": True, "weights": p["weights"], "capital_book": p["capital_book"],
    }, ensure_ascii=False))
    for arm_name in ("bp+ep_core", "ml_signal"):
        for t in arms[arm_name]["trades"]:
            lines.append(json.dumps({
                "type": "trade", "arm": arm_name,
                "entry_date": str(t.get("entry_date", ""))[:10],
                "exit_date": str(t.get("exit_date", ""))[:10],
                "code": str(t.get("symbol", "")).zfill(6),
                "name": str(t.get("symbol_name", "")),
                "entry_price": float(t.get("entry_price", 0.0)),
                "exit_price": float(t.get("exit_price", 0.0)),
                "shares": int(t.get("shares", 0)),
                "pnl": float(t.get("pnl", 0.0)),
                "pnl_pct": float(t.get("pnl_pct", 0.0)),
                "exit_reason": str(t.get("exit_reason", "")),
                "entry_reason": str(t.get("entry_reason", "")),
                "cost": float(t.get("cost", 0.0)),
                "holding_bars": t.get("holding_bars"),
            }, ensure_ascii=False))
    for d in common:
        lines.append(json.dumps({"type": "daily", "date": d,
                                 "bp_eq": round(float(bp_eq.loc[d]), 2),
                                 "ml_eq": round(float(ml_eq.loc[d]), 2),
                                 "combo_eq": round(float(combo_eq.loc[d]), 2)}, ensure_ascii=False))
    for op in open_positions:
        lines.append(json.dumps({"type": "open", **op}, ensure_ascii=False))
    tmp = ledger_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ledger_path)

    # ---- 快照 ----
    snap = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "anchor": anchor, "end": end, "repro_mode": repro,
        "prereg": os.path.basename(prereg_path),
        "bp_ep": {"final_eq": round(float(bp_eq.iloc[-1]), 2),
                  "ret_pct": round((float(bp_eq.iloc[-1]) / float(bp_eq.iloc[0]) - 1) * 100, 2),
                  "closed": n_closed_bp},
        "ml": {"final_eq": round(float(ml_eq.iloc[-1]), 2),
               "ret_pct": round((float(ml_eq.iloc[-1]) / float(ml_eq.iloc[0]) - 1) * 100, 2),
               "closed": n_closed_ml},
        "combo": {"final_eq": round(float(combo_eq.iloc[-1]), 2),
                  "ret_pct": round((float(combo_eq.iloc[-1]) / float(combo_eq.iloc[0]) - 1) * 100, 2)},
        "open_positions_n": len(open_positions),
        "gate": {"activation": approved_at, "oos_end": oos_end_str,
                 "calendar_days_elapsed": cal_days,
                 "closed_total": n_closed,
                 "pass": prereg["gates_locked"]["pass"],
                 "kill": prereg["gates_locked"]["kill"]},
        "ledger": ledger_path,
    }
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path)

    # ---- 简报 ----
    B = []
    B.append(f"主组合前向机械影子臂简报（PREREG_MAIN_COMBO_FORWARD_SHADOW ACTIVE，纯记账零交易权）")
    B.append(f"窗口 {anchor}~{end}｜生成 {snap['generated_at']}｜架构=引擎整套机制复用重算幂等｜EOS抑制=持仓延续")
    B.append(f"bp_ep 臂: 权益 {snap['bp_ep']['final_eq']:,.0f} ({snap['bp_ep']['ret_pct']:+.2f}%) 已平 {n_closed_bp} 笔")
    B.append(f"ml 臂   : 权益 {snap['ml']['final_eq']:,.0f} ({snap['ml']['ret_pct']:+.2f}%) 已平 {n_closed_ml} 笔")
    B.append(f"组合50/50: 权益 {snap['combo']['final_eq']:,.0f} ({snap['combo']['ret_pct']:+.2f}%)｜在持 {len(open_positions)} 只")
    if open_positions:
        B.append("在持明细:")
        for op in open_positions:
            B.append(f"  [{op['arm']}] {op['name']}({op['code']}) {op['shares']}股 @ 成本{op['avg_cost']:.3f} ({op['open_date']}入)")
    B.append(f"门进度: OOS 日历 {cal_days}/{365} 天(门终 {oos_end_str})｜closed {n_closed}/60")
    B.append(f"pass(原样): {prereg['gates_locked']['pass']}")
    B.append(f"kill(原样): {prereg['gates_locked']['kill']}")
    B.append("边界: 影子观测层零交易权不接闸门不接executor；月度哨兵引用本台账做对账")
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write("\n".join(B) + "\n")

    print(f"[fwd_shadow][OK] ledger={ledger_path}")
    print(f"[fwd_shadow][OK] state={state_path}")
    print(f"[fwd_shadow][OK] brief={brief_path}")
    print(f"[fwd_shadow][OK] 总耗时 {time.time()-t0:.1f}s｜组合终值 {snap['combo']['final_eq']:,.0f} ({snap['combo']['ret_pct']:+.2f}%)")


if __name__ == "__main__":
    main()
