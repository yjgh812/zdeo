"""
bp+ep 月度调仓 Picker（回测+选股双模式）
=========================================
唯一A级策略（年化22%/Sharpe1.10/9年8正/DSR=1.0），调研发现未接入生产，本脚本上线。

⚠️ 2026-09-15 二次勘误（数据实证，推翻 2026-08-12 勘误）：
  - 实际入库: bp = 账面市值比 B/M(=1/PB)，ep = 盈利收益率 E/P(=ROE×B/M)
  - 铁证: 百亚 bp=0.184=1/实时PB5.43；贵阳银行 bp=3.21；百亚 ep=ROE12.44%×B/M0.184
  - 策略本质 = 高B/M + 高E/P = 深度价值（经典价值因子），非质量成长
  - 20年A/B回测: 价值域链乘×46.3 vs 成长域×10.5 vs 市场×21.0 (2006-2026,248期月度)

策略逻辑（来自 bp_ep_amp_monthly_v2.py 回测验证）：
  - ep = E/P（高盈利收益率）, bp = B/M（高账面市值比）→ 深度价值因子
  - 等权 rank 合成（不是ICIR加权）
  - 月度调仓（每月首个交易日）
  - Top15 持仓

用法：
  python bp_ep_monthly_picker.py                    # 选股模式（输出当日Top15）
  python bp_ep_monthly_picker.py --backtest          # 回测模式（2018-2026验证）
  python bp_ep_monthly_picker.py --backtest --start 20240101 --end 20260810

输出：
  选股模式 → D:\\workbuddyspace\\v375_paper_trading\\picks\\bp_ep_YYYYMMDD.json
  回测模式 → D:\\zcodesj\\multifactor\\reports\\

数据源: dragon_quant.db.factor_daily (ep/bp列, 1.3M行, 至20260811)
"""
from __future__ import annotations
import sys, json, sqlite3, argparse, time
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

# 路径
MULTIFACTOR_DIR = Path(r"D:\zcodesj\multifactor")
sys.path.insert(0, str(MULTIFACTOR_DIR))
PICKS_DIR = Path(r"D:\workbuddyspace\v375_paper_trading\picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"

import config, factor_loader, backtester
from config import BacktestConfig


# ============================================================
# 1. 加载 bp+ep 因子（等权rank合成）
# ============================================================
def load_bp_ep_scores(start="20180101", end="20260810"):
    """从 factor_pit.db 加载 PIT 干净 bp/ep，等权rank合成 (2026-08-24 完整PIT切换,
    修复安全过滤在报告季中清空候选的问题, 回退到上一份已公告报告)"""
    ep_df = factor_loader.load_pit_factors("ep", start=start, end=end)
    bp_df = factor_loader.load_pit_factors("bp", start=start, end=end)

    if ep_df.empty or bp_df.empty:
        return pd.DataFrame(columns=["code", "date", "composite_score"])

    # 合并 ep + bp
    merged = ep_df.merge(bp_df, on=["code", "date"],
                         suffixes=("_ep", "_bp"))
    merged = merged.rename(columns={"factor_value_ep": "ep",
                                     "factor_value_bp": "bp"})
    merged = merged[(merged["ep"] > 0) & (merged["bp"] > 0)]  # 剔除负估值

    # 等权 rank
    merged["ep_rank"] = merged.groupby("date")["ep"].rank(pct=True)
    merged["bp_rank"] = merged.groupby("date")["bp"].rank(pct=True)
    merged["composite_score"] = (merged["ep_rank"] + merged["bp_rank"]) / 2

    return merged[["code", "date", "composite_score"]].sort_values(
        ["date", "composite_score"], ascending=[True, False]
    )


# ============================================================
# 2. 选股模式
# ============================================================
def pick_today(n=15):
    """输出当日 bp+ep Top15 (2026-08-24 裁决: 第二轮迭代 S-3 验证 TopN 20→15 收益+134.2%→+144.3%)"""
    # 2026-08-24: 选股日取 qfq 价格真源最近日(与PIT完整读取对齐, 避免因子表比qfq新导致无价格)
    latest = factor_loader.get_recent_qfq_date() or factor_loader.get_recent_trade_date(0)
    print(f"[bp+ep月度] 选股日: {latest}")

    scores = load_bp_ep_scores(start=latest, end=latest)
    if scores.empty:
        # 回退到最近有数据的日期
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        r = conn.execute(
            "SELECT MAX(trade_date) FROM factor_daily WHERE ep IS NOT NULL"
        ).fetchone()
        conn.close()
        if r[0]:
            latest = str(r[0])
            scores = load_bp_ep_scores(start=latest, end=latest)

    if scores.empty:
        print("[FAIL] 无 bp+ep 数据")
        return []

    # 股票池过滤
    from stock_filter import apply_filters
    meta = factor_loader.get_pool_filter_data(latest)
    if not meta.empty:
        filtered = apply_filters(meta, reference_date=latest)
        scores = scores[scores["code"].isin(set(filtered["code"]))]
        scores = scores.merge(
            filtered[["code", "name", "industry", "close"]].drop_duplicates("code"),
            on="code", how="left"
        )

    top = scores.head(n).reset_index(drop=True)
    top["rank"] = range(1, len(top) + 1)

    print(f"\n[bp+ep月度] Top{n} ({latest}):")
    cols = [c for c in ["rank", "code", "name", "industry", "close", "composite_score"] if c in top.columns]
    print(top[cols].to_string(index=False))

    # 保存到 picks 目录（paper_bridge 格式）
    picks_json = []
    for _, row in top.iterrows():
        picks_json.append({
            "code": row["code"],
            "name": row.get("name", ""),
            "combined_score": round(row["composite_score"], 4),
            "max5_prob": 0,  # bp+ep 不用模型概率
            "strategy": "bp_ep_monthly",
            "reason": "bp+ep月度价值策略(A级/年化22%)",
        })

    out = PICKS_DIR / f"bp_ep_{latest}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"date": latest, "strategy": "bp_ep_monthly",
                   "picks": picks_json}, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 保存: {out}")

    # 生成月度调仓 order_book（对比上月 → 买卖指令）
    generate_rebalance_orderbook(picks_json, latest)

    return picks_json


# ============================================================
# 2.5 月度调仓 order_book 生成（对比上月持仓）
# ============================================================
def generate_rebalance_orderbook(current_picks, current_date):
    """
    对比上月bp+ep持仓，生成调仓order_book
    输出到 paper_bridge 的 order_book 路径
    """
    current_codes = set(p["code"] for p in current_picks)
    current_names = {p["code"]: p.get("name", "") for p in current_picks}

    # 查找上月的 picks JSON
    import glob
    pattern = str(PICKS_DIR / "bp_ep_*.json")
    all_files = sorted(glob.glob(pattern))

    last_month_codes = set()
    last_month_names = {}
    last_month_date = None

    for fpath in reversed(all_files):
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        fdate = str(data.get("date", ""))
        # 找最近一个不同月份的文件
        if fdate[:6] != str(current_date)[:6]:
            last_month_date = fdate
            for p in data.get("picks", []):
                last_month_codes.add(p["code"])
                last_month_names[p["code"]] = p.get("name", "")
            break

    if not last_month_date:
        # 首次运行：全部为新建仓
        print(f"\n[调仓] 首次运行（无上月持仓），全部{len(current_codes)}只为新建仓")
        to_sell = set()
        to_buy = current_codes
        hold = set()
    else:
        to_sell = last_month_codes - current_codes  # 上月有本月无 → 卖出
        to_buy = current_codes - last_month_codes   # 本月有上月无 → 买入
        hold = current_codes & last_month_codes      # 两月都有 → 持有
        print(f"\n[调仓] 上月({last_month_date})→本月({current_date})")
        print(f"  持有: {len(hold)}只 | 卖出: {len(to_sell)}只 | 买入: {len(to_buy)}只")

    # 生成 order_book CSV（paper_bridge 格式）
    lines = ["action,code,name,price,shares,reason"]

    for code in sorted(to_sell):
        name = last_month_names.get(code, "")
        lines.append(f"sell,{code},{name},0,0,bp+ep月度调仓:本月Top15外")

    for code in sorted(to_buy):
        name = current_names.get(code, "")
        lines.append(f"buy,{code},{name},0,0,bp+ep月度调仓:本月Top15入选")

    csv_content = "\n".join(lines)

    # 输出到 paper_bridge 的 order_book 路径
    order_book_dir = Path(r"D:\reasonix\quant_backtest\pools")
    order_book_dir.mkdir(parents=True, exist_ok=True)
    out_path = order_book_dir / f"order_book_bp_ep_{current_date}.csv"
    out_path.write_text(csv_content, encoding="utf-8")

    print(f"  [OK] order_book: {out_path}")
    print(f"  sell={len(to_sell)} buy={len(to_buy)} hold={len(hold)}")

    if to_sell:
        print(f"\n  卖出明细:")
        for code in sorted(to_sell):
            print(f"    {code} {last_month_names.get(code,'')}")
    if to_buy:
        print(f"\n  买入明细:")
        for code in sorted(to_buy):
            print(f"    {code} {current_names.get(code,'')}")

    return {"sell": list(to_sell), "buy": list(to_buy), "hold": list(hold)}


# ============================================================
# 3. 回测模式
# ============================================================
def backtest(start="20180101", end="20260810"):
    """bp+ep月度回测验证"""
    print(f"[bp+ep月度] 回测 {start}~{end}")

    scores = load_bp_ep_scores(start, end)
    print(f"[bp+ep] 加载: {len(scores)}行, {scores['date'].nunique()}日")

    # 月度调仓
    def monthly(self, all_dates):
        dt = pd.to_datetime([str(d) for d in all_dates], format="%Y%m%d", errors="coerce")
        df = pd.DataFrame({"date": all_dates, "dt": dt})
        df["ym"] = df["dt"].dt.to_period("M").astype(str)
        return set(df.groupby("ym")["date"].first().tolist())
    backtester.Backtester._get_rebalance_dates = monthly

    cfg = BacktestConfig(start_date=start, end_date=end, top_n=20)
    result = backtester.run_backtest(scores, cfg=cfg, save=True)
    return result.get("performance", {})


# ============================================================
# 4. 主入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="bp+ep月度Picker")
    parser.add_argument("--backtest", action="store_true", help="回测模式")
    parser.add_argument("--start", default="20180101")
    parser.add_argument("--end", default="20260810")
    args = parser.parse_args()

    if args.backtest:
        perf = backtest(args.start, args.end)
        print(f"\n{'='*60}")
        print(f"bp+ep月度 回测结果 ({args.start}~{args.end})")
        print(f"{'='*60}")
        for k in ["ann_return", "sharpe", "max_drawdown", "excess_ann",
                   "info_ratio", "avg_turnover", "total_cost", "final_nav"]:
            print(f"  {k}: {perf.get(k, 0)}")

        print(f"\n=== 对比（完整周期）===")
        print(f"  ICIR 5因子+5日+周度:  年化+6.91% Sharpe0.580")
        print(f"  bp+ep月度(本脚本):    年化{perf.get('ann_return',0):.2%} Sharpe{perf.get('sharpe',0):.3f}")
        print(f"  bp+ep月度(记忆基准):  年化+22% Sharpe1.10（9年8正）")
    else:
        pick_today()
