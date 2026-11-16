# -*- coding: utf-8 -*-
"""
第三轮迭代: 板块集中度控制 (单板块<=30%)
========================================
验证 pending_enhancements 第一项: 单板块<=30%。
方法: 对已采纳配置 (bp_ep_top15_hold0 / ml_top20_hold20) 分别跑
  基线(无上限) vs 30%板块上限, 记录每次调仓目标板块集中度分布,
  对比收益/回撤/夏普/大亏。
输出: 打印到 stdout (由调用方重定向捕获)。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

loader_global = None
conc_records = {}  # name -> list of (date, max_sector_share)


def sector_totals(weights, ind_of):
    st = {}
    for code, w in weights.items():
        st[ind_of[code]] = st.get(ind_of[code], 0) + w
    return st


def sector_cap(weights, loader, cap=0.30):
    """迭代软上限: 各板块<=cap 且总和=1; 病态全超限时释放权重留现金"""
    si = loader._si_indexed
    ind_of = {}
    members = {}
    for code in weights:
        ind = si.loc[code, "industry"] if code in si.index else "unknown"
        ind_of[code] = ind
        members.setdefault(ind, []).append(code)
    w = dict(weights)
    for _ in range(50):
        st = {ind: sum(w.get(c, 0) for c in mlist) for ind, mlist in members.items()}
        total = sum(st.values())
        if total <= 0:
            return {}
        over = [i for i, t in st.items() if t > cap]
        if not over and abs(total - 1) < 1e-9:
            break
        freed = 0.0
        for ind in over:
            scale = cap / st[ind]
            for c in members[ind]:
                w[c] *= scale
            freed += st[ind] - cap
        under = {i: t for i, t in st.items() if t <= cap}
        ut = sum(under.values())
        if ut > 0 and freed > 0:
            for ind in under:
                add = freed * (under[ind] / ut)
                for c in members[ind]:
                    w[c] = w[c] + w[c] * (add / under[ind]) if under[ind] > 0 else w[c]
        else:
            break
    return w


def make_bpep(top_n):
    def sig(loader, date_str):
        df = loader.get_fundamental_factors(date_str)
        if df is None or len(df) < top_n:
            return {}, {}
        df = df[df["close"] <= 30].copy()
        if len(df) < top_n:
            return {}, {}
        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
        top = df.nlargest(top_n, "score")
        fail = loader.get_failure_flags(date_str)
        vt, rc = fail.get("value_trap", set()), fail.get("rebound_chase", set())
        weight = 0.40 / top_n
        weights = {}
        for idx, row in top.iterrows():
            mult = 0.5 if (idx in vt or idx in rc) else 1.0
            weights[idx] = weight * mult
        return weights, {}
    return sig


def run_variant(name, sig_fn, freq, overrides, cap):
    bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds):
        weights, _ = sig_fn(loader_global, ds)
        if weights and cap is not None:
            st = sector_totals(weights, _ind_of(weights))
            conc_records.setdefault(name, []).append((ds, max(st.values()) if st else 0))
        if cap is not None:
            weights = sector_cap(weights, loader_global, cap)
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader_global, start_date=m.START_DATE, end_date=m.END_DATE,
                    rebalance_freq=freq, strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    line = (f"[{name}] cap={cap if cap else '无'} | 总收益={metrics.get('total_return_pct',0):+.1f}% "
            f"年化={metrics.get('annualized_return_pct',0):.1f}% 夏普={metrics.get('sharpe_ratio',0):.2f} "
            f"回撤={metrics.get('max_drawdown_pct',0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} 大亏(<=-5%)={n_big5}")
    print(line, flush=True)
    return {"name": name, "cap": cap,
            "total_return_pct": metrics.get('total_return_pct', 0),
            "annualized_return_pct": metrics.get('annualized_return_pct', 0),
            "sharpe": metrics.get('sharpe_ratio', 0),
            "max_drawdown_pct": metrics.get('max_drawdown_pct', 0),
            "win_rate": round(wr, 1), "n_trades": len(trades), "n_big5": n_big5}


def _ind_of(weights):
    si = loader_global._si_indexed
    return {c: (si.loc[c, "industry"] if c in si.index else "unknown") for c in weights}


def main():
    global loader_global
    m.logger.info("=== 第三轮: 板块集中度控制 ===")
    loader_global = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    runs = [
        # 名称, 信号, 频率, 覆盖, cap
        ("bpep15_cap30", make_bpep(15), "monthly", {"max_holding_bars": 0}, 0.30),
        ("ml20_cap30", lambda dd, ds: m.ml_signal(loader_global, ds), "monthly", {"max_holding_bars": 20}, 0.30),
    ]
    rows = []
    for name, sig_fn, freq, overrides, cap in runs:
        rows.append(run_variant(name, sig_fn, freq, overrides, cap))

    # 板块集中度分布统计 (基线配置的调仓目标)
    for nm, recs in conc_records.items():
        if recs:
            shares = [r[1] for r in recs]
            over30 = sum(1 for s in shares if s > 0.30)
            print(f"[板块集中度 {nm}] 调仓{len(shares)}次 | 平均最大板块占比={sum(shares)/len(shares)*100:.1f}% "
                  f"| 超30%次数={over30}({over30/len(shares)*100:.0f}%) | 最大={max(shares)*100:.1f}%")

    print("===ROUND3_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
