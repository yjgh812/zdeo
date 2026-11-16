# -*- coding: utf-8 -*-
"""
round10 采纳组合逐年明细补全 (2026-08-28)
====================================================
闭合 cleanbase README 标记的 round10 工程项「开仓理由回填」:
对采纳组合两腿(bp_ep_top15 / ml_hold20)的 trades.json 重放信号函数生成
entry_reason(确定性, 与 full_report 的 _inject_entry_reasons 同构 bisect 匹配),
补 symbol_name(stock_info), 代码补零 6 位, 导出逐年 CSV。
只读消费回测产物; 不重算回测。
"""
import sys, os, json, bisect, gc, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)

OUT = r"D:\zcodesj\reports\round10_adopted_combo_20260827"
CN = {"entry_date": "买入日期", "exit_date": "卖出日期", "symbol": "股票代码",
      "symbol_name": "股票名称", "entry_price": "买入价格", "exit_price": "卖出价格",
      "shares": "持股数量", "pnl": "盈亏金额", "pnl_pct": "盈亏比例%",
      "entry_reason": "买入决策理由", "exit_reason": "卖出触发理由",
      "cost": "交易成本", "holding_bars": "持仓天数"}


def bp_ep_topn(start: int, end: int):
    """与 round10_adopted_combo 同源(协议冻结)。"""
    def sig(ld, date_str):
        df = ld.get_fundamental_factors(date_str)
        if df is None or len(df) < end:
            return {}, {}
        df = df[df["close"] <= 30].copy()
        if len(df) < end:
            return {}, {}
        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
        top = df.nlargest(end, "score").iloc[start:]
        n = end - start
        weight = 0.40 / n
        weights, reasons = {}, {}
        for idx, row in top.iterrows():
            weights[idx] = weight
            reasons[idx] = f"bp+ep月度rank{start + 1}-{end} PITv2 (score={row['score']:.3f})"
        return weights, reasons
    return sig


def replay_reasons(sig_fn, trading_dates, loader):
    """全窗口重放信号, 构建 code -> [(sig_date, reason)] 升序表(键统一补零6位)。"""
    reasons_map = {}
    for d in trading_dates:
        _, r = sig_fn(loader, d)
        if not r:
            continue
        for code, reason in r.items():
            reasons_map.setdefault(str(code).zfill(6), []).append((d, reason))
    return reasons_map


def backfill(trades, reasons_map, loader):
    out = []
    for t in trades:
        entry = str(t.get("entry_date", ""))[:10]
        code = str(t.get("symbol", "")).zfill(6)
        reason = ""
        lst = reasons_map.get(code)
        if lst:
            idx = bisect.bisect_right([x[0] for x in lst], entry) - 1
            if idx >= 0:
                reason = lst[idx][1]
        name = ""
        if code in loader._si_indexed.index:
            _row = loader._si_indexed.loc[code]
            name = str(_row.get("name", "")) if hasattr(_row, "get") else str(_row["name"])
        out.append({**t, "symbol": code, "symbol_name": name, "entry_reason": reason})
    return out


def write_yearly(trades, prefix):
    df = pd.DataFrame(trades)
    df = df.rename(columns={k: v for k, v in CN.items() if k in df.columns})
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    df.to_csv(os.path.join(OUT, f"{prefix}_trades_full.csv"), index=False, encoding="utf-8-sig")
    df["_y"] = df["买入日期"].astype(str).str[:4]
    for yr, g in df.groupby("_y"):
        g.drop(columns="_y").to_csv(os.path.join(OUT, f"{prefix}_trades_{yr}.csv"),
                                    index=False, encoding="utf-8-sig")
    n_reason = int(df["买入决策理由"].notna().sum())
    print(f"[{prefix}] 逐年CSV已写: {len(df)} 笔 / 有理由 {n_reason} / 年份 {sorted(df['_y'].unique())}")


def leg_bp():
    os.environ["TULONG_LOADER_LIGHT"] = "bp"
    loader = _ns["TulongDataLoader"](_ns["DB_PATH"], _ns["QFQ_PATH"])
    trades = json.load(open(os.path.join(OUT, "bp_ep_top15_pitv2_trades.json"), encoding="utf-8"))
    rm = replay_reasons(bp_ep_topn(0, 15), loader._trading_dates, loader)
    return backfill(trades, rm, loader)


def leg_ml():
    os.environ["TULONG_LOADER_LIGHT"] = "ml"
    loader = _ns["TulongDataLoader"](_ns["DB_PATH"], _ns["QFQ_PATH"])
    trades = json.load(open(os.path.join(OUT, "ml_hold20_pitv2_trades.json"), encoding="utf-8"))
    rm = replay_reasons(_ns["ml_signal"], loader._trading_dates, loader)
    return backfill(trades, rm, loader)


if __name__ == "__main__":
    print("[detail] 腿1: bp_ep_top15 开仓理由回填...")
    write_yearly(leg_bp(), "bp_ep_top15_pitv2")
    gc.collect()
    print("[detail] 腿2: ml_hold20 开仓理由回填...")
    write_yearly(leg_ml(), "ml_hold20_pitv2")
    print("[detail] 采纳组合逐年明细补全完成")
