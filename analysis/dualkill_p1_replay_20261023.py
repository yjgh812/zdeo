#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dualkill_p1_replay_20261023.py — 2026双杀验证协议 P1 判别树 L0a-L2 影子回放 harness
====================================================================================
依据: analysis/dualkill_verification_protocol_20261019.md (主人批准 P1 影子回放)
      L0a 信号日收盘进/持20日/无闸门无费用(理论alpha)
      L0b T+1开盘进/持20日/无闸门无费用(剥离执行缺口)
      L1  = L0b + 闸门(模板标准过滤链)
      L2  = L1 + -8%硬止损(引擎, 含生产费率; 月度22日周期>20日帽=持仓名义固定20日)
      L3  边界: bp_ep回测腿无"趋势走坏卖出"组件(Week-0账本三退出可证), 不伪造,
              L4-L2 差额归因调仓/组合结构
      L4  = 10/18 fork 在档账本(只读引用, 不重跑)

层语义:
  raw  = TULONG_LADDER_SIGNAL=raw (裸Top15, 跳过close<=30过滤) → L0a/L0b
  gate = 模板标准过滤链(universe+价格≤30+失败形态等)               → L1
引擎层(L2)以子进程跑模板fork: 源码补丁 START_DATE/END_DATE/策略裁剪(bp_ep单腿),
  TULONG_BT_OUT 重定向产物目录, TULONG_BPEP_STOP=0.08 TULONG_BPEP_MAX_HOLDING=20。

用法:
  python analysis/dualkill_p1_replay_20261023.py analytic   # L0a/L0b/L1 + IC期限结构
  python analysis/dualkill_p1_replay_20261023.py build-l2   # 生成L2引擎fork(源码补丁)
  python analysis/dualkill_p1_replay_20261023.py report     # 汇总判定表(L4引用在档账本)

只读审计件: 零交易权, 零生产链改动; 引擎fork运行时只写 OUTPUT_DIR 产物。
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, "bt_full_report_ladder_20261019.py")
OUT_DIR = os.path.join(ROOT, "reports", "dualkill_p1_replay_20261023")
L4_TRADES = os.path.join(ROOT, "reports", "backtest_full_20261019_ladder_debug",
                         "bp_ep_core_trades.csv")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
WINDOW_START = "2025-01-01"          # 协议: 重放当前模拟盘区间
REBALANCE_BARS = 22                  # 生产月度=每22交易日
HOLD_BARS = 20                       # 判别树固定持有
TOP_N = 15
ST_RULE_CHANGE = (2026, 7, 6)        # 主板ST 5%→10% (与本流修复同步的口径注记, 不参与计算)


def qfq_conn():
    return sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)


def load_calendar_and_prices(d0, d1):
    """返回 trading_days 排序表 + {code: {date_str: (open, close)}}。仅窗口。"""
    con = qfq_conn()
    try:
        rows = con.execute(
            "SELECT date, code, open, close FROM stock_daily_meta_qfq "
            "WHERE date>=? AND date<=? AND open>0 AND close>0",
            (int(d0.replace("-", "")), int(d1.replace("-", "")))).fetchall()
    finally:
        con.close()
    days = sorted({r[0] for r in rows})
    px = {}
    for dt, code, o, c in rows:
        px.setdefault(str(code).zfill(6), {})[str(dt)] = (float(o), float(c))
    return [str(d) for d in days], px


def month_starts(trading_days):
    """月度调仓日: 每 REBALANCE_BARS 个交易日一个(自窗口首日起), 与生产月频同构。"""
    return [trading_days[i] for i in range(0, len(trading_days), REBALANCE_BARS)
            if i + HOLD_BARS < len(trading_days)]


def dash(d):
    """紧凑交易日键(20250103)→信号函数日期串(2025-01-03)。loader只认横杠格式。"""
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def run_analytic():
    """L0a/L0b/L1 解析层 + IC 期限结构。"""
    os.environ.setdefault("TULONG_BT_QUIET", "1")
    sys.path.insert(0, HERE)
    sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
    import importlib.util
    spec = importlib.util.spec_from_file_location("ladder_tpl", TEMPLATE)
    tpl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tpl)

    loader = tpl.TulongDataLoader(tpl.DB_PATH, tpl.QFQ_PATH)
    trading_days, px = load_calendar_and_prices(WINDOW_START, "2026-12-31")
    rbs = month_starts(trading_days)
    print(f"[analytic] 窗口 {WINDOW_START}~ 交易日 {len(trading_days)} 调仓批 {len(rbs)}")

    layers = {
        "L0a": {"raw": True, "entry": "close"},
        "L0b": {"raw": True, "entry": "open_next"},
        "L1": {"raw": False, "entry": "open_next"},
    }
    day_idx = {d: i for i, d in enumerate(trading_days)}
    results = {k: [] for k in layers}
    skipped = {k: 0 for k in layers}

    for rb in rbs:
        i = day_idx[rb]
        if i + HOLD_BARS >= len(trading_days):
            continue
        exit_day = trading_days[i + HOLD_BARS]
        for name, cfg in layers.items():
            os.environ["TULONG_LADDER_SIGNAL"] = "raw" if cfg["raw"] else ""
            try:
                scores, names = tpl.bp_ep_signal(loader, dash(rb))
            except Exception as e:
                print(f"[warn] {name} {rb} 信号失败: {type(e).__name__}: {e}")
                continue
            top = sorted(scores.items(), key=lambda x: -x[1])[:TOP_N]
            rets = []
            for code, _sc in top:
                path = px.get(str(code).zfill(6))
                if not path:
                    skipped[name] += 1
                    continue
                if cfg["entry"] == "close":
                    ep = path.get(rb, (None, None))[1]
                else:
                    nxt = trading_days[i + 1] if i + 1 < len(trading_days) else None
                    ep = path.get(nxt, (None, None))[0] if nxt else None
                xp = path.get(exit_day, (None, None))[1]
                if not ep or not xp:
                    skipped[name] += 1
                    continue
                rets.append(xp / ep - 1.0)
            if rets:
                results[name].append({"entry": rb, "exit": exit_day,
                                      "n": len(rets),
                                      "ret_eqw": sum(rets) / len(rets),
                                      "rets": rets})
            os.environ.pop("TULONG_LADDER_SIGNAL", None)

    # IC 期限结构: 逐日gate Top15 对未来1/5/20日超额收益 Rank IC
    import statistics
    ic = {1: [], 5: [], 20: []}
    sample_days = trading_days[::3]  # 每3日采样降载
    for d in sample_days:
        i = day_idx.get(d)
        if i is None or i + 20 >= len(trading_days):
            continue
        os.environ["TULONG_LADDER_SIGNAL"] = ""
        try:
            scores, _n = tpl.bp_ep_signal(loader, dash(d))
        except Exception:
            continue
        finally:
            os.environ.pop("TULONG_LADDER_SIGNAL", None)
        top = sorted(scores.items(), key=lambda x: -x[1])[:TOP_N]
        if len(top) < TOP_N:
            continue
        # 市场等权基准: 当日全有价股票的前视等权均值
        for h in (1, 5, 20):
            ed = trading_days[i + h]
            xs, mk = [], []
            for code, _s in top:
                p = px.get(str(code).zfill(6))
                e0 = p.get(d, (None, None))[1] if p else None
                e1 = p.get(ed, (None, None))[1] if p else None
                if e0 and e1:
                    xs.append(e1 / e0 - 1.0)
            # 市场基准抽样: px 中当日与ed均有价的随机500只(确定性抽样)
            codes_all = sorted(px.keys())
            step = max(1, len(codes_all) // 500)
            for c in codes_all[::step]:
                p = px[c]
                a = p.get(d, (None, None))[1]
                b = p.get(ed, (None, None))[1]
                if a and b:
                    mk.append(b / a - 1.0)
            if len(xs) == TOP_N and len(mk) > 50:
                ex = [x - (sum(mk) / len(mk)) for x in xs]
                ic[h].append(_rank_ic(ex))
    out = {"window": [WINDOW_START, trading_days[-1] if trading_days else None],
           "layers": {}, "ic_term_structure": {}, "skipped": skipped,
           "l3_boundary": "bp_ep回测腿无趋势走坏卖出组件(Week-0账本三退出可证), "
                          "L4-L2差额归因调仓/组合结构, 不伪造组件"}
    for name, cohorts in results.items():
        all_rets = [r for c in cohorts for r in c["rets"]]
        wins = [r for r in all_rets if r > 0]
        losses = [r for r in all_rets if r <= 0]
        srt = sorted(all_rets)
        n = len(all_rets)

        def _pct(arr, q):
            return arr[int(q * (len(arr) - 1))] if arr else None
        pl = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None
        tail = sorted(all_rets, reverse=True)[:max(1, int(0.05 * n))]
        out["layers"][name] = {
            "cohorts": len(cohorts), "n_trades": n,
            "win_rate": len(wins) / n if n else None,
            "pl_ratio": pl,
            "win_p50": _pct(sorted(wins), 0.5) if wins else None,
            "win_p90": _pct(sorted(wins), 0.9) if wins else None,
            "win_p99": _pct(sorted(wins), 0.99) if wins else None,
            "loss_p50": _pct(sorted(losses), 0.5) if losses else None,
            "loss_p10": _pct(sorted(losses), 0.1) if losses else None,
            "top5pct_contrib": (sum(tail) / sum(all_rets)) if all_rets and sum(all_rets) else None,
            "total_return_eqw_cohort": sum(c["ret_eqw"] for c in cohorts),
        }
    for h, series in ic.items():
        if series:
            m = sum(series) / len(series)
            sd = statistics.pstdev(series) if len(series) > 1 else 0
            out["ic_term_structure"][f"IC{h}"] = {
                "mean": m, "n_days": len(series),
                "t_like": m / sd * (len(series) ** 0.5) if sd else None}
    os.makedirs(OUT_DIR, exist_ok=True)
    jp = os.path.join(OUT_DIR, "p1_analytic_result.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[analytic] 写出 {jp}")
    for name in layers:
        L = out["layers"][name]
        print(f"  {name}: n={L['n_trades']} 胜率={L['win_rate'] if L['win_rate'] is None else round(L['win_rate'],4)}"
              f" 盈亏比={L['pl_ratio'] if L['pl_ratio'] is None else round(L['pl_ratio'],3)}"
              f" 批次={L['cohorts']}")
    print("  IC期限结构:", {k: (round(v['mean'], 4), v['n_days']) for k, v in out["ic_term_structure"].items()})


def _rank_ic(xs):
    """Spearman秩相关: Top15成员的横截面超额收益 vs 分数秩(分数序=给定序)。"""
    n = len(xs)
    ranks = list(range(n, 0, -1))  # 分数从高到低给定, 秩 n..1
    def _rk(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    a, b = ranks, _rk(xs)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = (sum((x - ma) ** 2 for x in a) * sum((x - mb) ** 2 for x in b)) ** 0.5
    return cov / va if va else 0.0


def build_l2_fork():
    """生成 L2 引擎 fork: 源码补丁跑窗+策略裁剪, 产物目录重定向。"""
    src = open(TEMPLATE, encoding="utf-8").read()
    src, n1 = re.subn(r'(?m)^START_DATE = "[^"]*"',
                      f'START_DATE = "{WINDOW_START}"', src, count=1)
    src, n2 = re.subn(r'(?m)^END_DATE = "[^"]*"',
                      'END_DATE = "2026-10-22"', src, count=1)
    # 策略裁剪: strategies 列表收尾后仅保留 bp_ep 腿
    m = re.search(r"(?m)^    strategies = \[\n(.*?)^\    \]\n", src, re.S)
    n3 = 0
    if m:
        src = src[:m.end()] + ('    strategies = [s for s in strategies '
                               'if s[0] == "bp+ep_core"]  # P1判别树: 仅bp_ep腿\n') + src[m.end():]
        n3 = 1
    fork = os.path.join(HERE, "_p1_fork_L2_20261023.py")
    with open(fork, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[build-l2] fork={fork} 补丁: START×{n1} END×{n2} 裁剪×{n3}")
    print(f"  运行: TULONG_BT_OUT={OUT_DIR}{os.sep}L2 TULONG_BPEP_STOP=0.08 "
          f"TULONG_BPEP_MAX_HOLDING=20 python {fork}")
    return fork


def report():
    """汇总判定表: 解析层JSON + L2产物 + L4在档账本引用。"""
    jp = os.path.join(OUT_DIR, "p1_analytic_result.json")
    out = {"note": "L0b正常→执行/风控层; L0b已差→信号/regime层(协议判定规则)",
           "l4_reference": L4_TRADES if os.path.exists(L4_TRADES) else "MISSING",
           "analytic": json.load(open(jp, encoding="utf-8")) if os.path.exists(jp) else None}
    os.makedirs(OUT_DIR, exist_ok=True)
    mp = os.path.join(OUT_DIR, "p1_verdict_draft.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# P1 判别树影子回放 判定草案(待L2引擎层回填)\n\n")
        f.write(f"- L4在档账本: {out['l4_reference']}\n")
        a = out["analytic"]
        if a:
            f.write(f"- 窗口: {a['window']}\n")
            for k, v in a["layers"].items():
                f.write(f"- {k}: n={v['n_trades']} 胜率={v['win_rate']} "
                        f"盈亏比={v['pl_ratio']} 右尾Top5%贡献={v['top5pct_contrib']}\n")
            f.write(f"- IC期限结构: {a['ic_term_structure']}\n")
            f.write(f"- L3边界: {a['l3_boundary']}\n")
    print(f"[report] {mp}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "analytic":
        run_analytic()
    elif cmd == "build-l2":
        build_l2_fork()
    elif cmd == "report":
        report()
    else:
        print(__doc__)
        sys.exit(2)
