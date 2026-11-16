# -*- coding: utf-8 -*-
"""反思文档开放项观察研究（只读，零交易权）：
A. 生产滑点 vs 引擎 0.03% 校准预研（executor 台账 FILLED 订单 vs qfq 基准价；样本量如实声明）
B. 组合极端情景表（canonical 组合净值曲线历史压力窗纯描述）
输出 = reports/observation_studies_20261018.md（脚本直出，禁人工转抄）
"""
import json
import sqlite3

L = []


def w(s=""):
    L.append(s)


QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
EXEC = r"D:/zcodesj/data/executor_orders.jsonl"
A1 = r"D:/zcodesj/data/a1_slippage_ledger.jsonl"
COMBO = r"D:/zcodesj/reports/backtest_full_20261016_eod/portfolio_combo_equity.csv"
BP = r"D:/zcodesj/reports/backtest_full_20261016_eod/bp_ep_core_equity.csv"
ML = r"D:/zcodesj/reports/backtest_full_20261016_eod/ml_signal_equity.csv"
OUT = r"D:/zcodesj/reports/observation_studies_20261018.md"

conn = sqlite3.connect(QFQ)


def day_prices(code6, yyyymmdd, need=("open", "close")):
    r = conn.execute(
        f"SELECT {','.join(need)} FROM stock_daily_meta_qfq WHERE code=? AND date=?",
        (code6, int(yyyymmdd)),
    ).fetchone()
    return r


# ================= Part A =================
rows = [json.loads(l) for l in open(EXEC, encoding="utf-8")]
filled = [r for r in rows if r.get("status") == "FILLED"]
from collections import Counter

status_cnt = Counter(str(r.get("status")) for r in rows)
a1_rows = [json.loads(l) for l in open(A1, encoding="utf-8")]

buy_stats, sell_stats, miss = [], [], 0
for r in filled:
    code6 = str(r.get("code", ""))[-6:]
    td = str(r.get("trade_date", ""))
    px = float(r.get("price", 0) or 0)
    act = r.get("action")
    if act == "BUY":
        pr = day_prices(code6, td, ("open",))
        if not pr or not pr[0]:
            miss += 1
            continue
        slip = (px - float(pr[0])) / float(pr[0])  # 正=比开盘贵（劣于引擎假设）
        buy_stats.append(slip)
    elif act == "SELL":
        pr = day_prices(code6, td, ("close",))
        if not pr or not pr[0]:
            miss += 1
            continue
        slip = (float(pr[0]) - px) / float(pr[0])  # 正=卖得比收盘低（劣于引擎假设）
        sell_stats.append(slip)


def dstats(xs):
    if not xs:
        return None
    xs2 = sorted(xs)
    n = len(xs2)

    def pct(p):
        i = min(n - 1, max(0, int(p * (n - 1))))
        return xs2[i]

    return {"n": n, "mean": sum(xs2) / n, "med": pct(0.5), "p25": pct(0.25), "p75": pct(0.75),
            "worst": max(xs2, key=abs), "pos_share": sum(1 for x in xs2 if x > 0) / n}


b = dstats(buy_stats)
s = dstats(sell_stats)
ENGINE_SLIP = 0.0003

w("# 反思文档开放项观察研究（A 滑点校准预研 + B 极端情景表）")
w()
w("> 生成：脚本 analysis/observation_studies_20261018.py 直出（禁人工转抄）｜只读零交易权｜"
  "性质=观察预研，样本量与口径限制如实声明，不构成任何调参或接线依据")
w()
w("## A. 生产滑点 vs 引擎 0.03% 假设——校准预研")
w()
w(f"样本：executor_orders.jsonl 全量 {len(rows)} 笔提交（2026-08-26~2026-10-16，单一 regime 窗口），"
  f"状态分布 {dict(status_cnt)}；取 FILLED {len(filled)} 笔（BUY {b['n'] if b else 0}/SELL {s['n'] if s else 0}），"
  f"qfq 基准价缺失剔除 {miss} 笔。")
w()
w("口径与代理假设（如实声明）：")
w("- 成交价代理=委托价（限价单 FILLED 通常按委托价成交；a1_slippage_ledger 全部 "
  f"{len(a1_rows)} 笔近期成交 slippage_pp=0 与该代理一致，但 n=5 仅为旁证）；")
w("- BUY 基准=当日开盘价（引擎 next-open 口径）；slip=(成交代理−开盘)/开盘，正=劣于引擎假设；")
w("- SELL 基准=当日收盘价（引擎止损 same-close 口径）；slip=(收盘−成交代理)/收盘，正=劣于引擎假设；")
w("- 引擎滑点假设=0.03%（固定），另佣金 0.025%（min 5 元）+印花税 0.05%（卖出）单列。")
w()
w("| 侧 | n | 均值 | 中位 | p25 | p75 | 最劣 | 劣于引擎占比 |")
w("|---|---|---|---|---|---|---|---|")
for name, d in (("BUY vs 开盘", b), ("SELL vs 收盘", s)):
    if d:
        w(f"| {name} | {d['n']} | {d['mean']*100:+.3f}% | {d['med']*100:+.3f}% | "
          f"{d['p25']*100:+.3f}% | {d['p75']*100:+.3f}% | {d['worst']*100:+.3f}% | {d['pos_share']*100:.0f}% |")
    else:
        w(f"| {name} | 0 | N/A | N/A | N/A | N/A | N/A | N/A |")
w()
if b and s:
    verdict_parts = []
    if b["mean"] <= ENGINE_SLIP:
        verdict_parts.append(f"买入侧均值 {b['mean']*100:+.3f}% ≤ 引擎假设 0.03%")
    else:
        verdict_parts.append(f"买入侧均值 {b['mean']*100:+.3f}% > 引擎假设 0.03%")
    if s["mean"] <= ENGINE_SLIP:
        verdict_parts.append(f"卖出侧均值 {s['mean']*100:+.3f}% ≤ 引擎假设 0.03%")
    else:
        verdict_parts.append(f"卖出侧均值 {s['mean']*100:+.3f}% > 引擎假设 0.03%")
    w(f"**预研读数（非结论）**：{'；'.join(verdict_parts)}。样本仅 2 个月单一 regime（含 2026 秋退潮段），"
      "n<50，不可作调参依据；正式校准=月度对账哨（automation-03d6d9dc）持续积累生产账本后按月更新本研究的同口径统计。")
w()
w("🔴解读边界（防误读，比读数本身更重要）：")
w(f"1. 买入侧 +{b['mean']*100:.2f}% **不是**「引擎滑点参数应为 2%」的证据——本表度量的是生产混合流程"
  "（人工+闸门单，非机械策略单）的成交时点成本：贴线限价单在 09:35-10:00 成交，基准=当日开盘，"
  "读数混合了「买入时点选择偏强盘 morning drift」与「限价相对开盘的主动定价」，两者都不是滑点参数；")
w("2. 与引擎 0.03% 可比的量，须是**同总体**（机械策略单：月度调仓 Top20 大中盘、开盘一次成交）的同口径实测——"
  "这正是前向机械影子臂投运后才能产生的东西（影子臂与生产共用的 0.03% 假设届时由影子 vs 真实开盘价直接检验）；")
w("3. 本表的正确用途=TCA 观察基线（生产混合流程执行质量），归月度对账哨「执行差异」分类引用；"
  "卖出侧 52% 劣于收盘基准=止损早于收盘兑现（跳空限价保成交设计）与崩盘日盘中价路径混合，同样非滑点参数。")
w()
w("附加观察（生产↔回测执行差异的另一面）：FILLED 48 / 提交 231 ≈ 21%——大量订单状态 OPEN/CAncelled/None"
  "（OPEN 可能含陈旧未撤单的台账卫生问题，open_orders.jsonl 才是活订单口径）；引擎假设信号总能成交（除涨停拒单），"
  "生产的未成交=信号→持仓转化漏损的观测口径，归月度对账哨「执行差异」分类持续跟踪，本轮不展开。")
w()

# ================= Part B =================
w("## B. 组合极端情景表（canonical 组合净值历史压力窗，纯描述）")
w()
w("> 数据源=10/16 全史引擎 portfolio_combo_equity.csv（2016-01-04~2026-10-16，50/50 逐日再平衡口径）；"
  "本表为既有回测产物的描述性切片，非新回测、非任何改造方案。")
w()


def load_eq(path):
    import pandas as pd
    df = pd.read_csv(path, encoding="utf-8-sig")
    val = "value" if "value" in df.columns else "equity"
    df["date"] = df["date"].astype(str)
    return df.set_index("date")[val].astype(float)


import pandas as pd  # noqa: E402

eq_combo = load_eq(COMBO)
eq_bp = load_eq(BP)
eq_ml = load_eq(ML)

WINDOWS = [
    ("2018 贸易战熊市全年", "2018-01-01", "2018-12-31"),
    ("2020-02~04 新冠冲击", "2020-02-01", "2020-04-30"),
    ("2021-02~03 核心资产崩", "2021-02-01", "2021-03-31"),
    ("2022 熊市全年", "2022-01-01", "2022-12-31"),
    ("2024-09~10 政策爆发（上行压力）", "2024-09-15", "2024-10-31"),
    ("2025-06~2026-10 当前 regime 段", "2025-06-01", "2026-10-16"),
]

w("| 情景窗 | 组合窗收益 | 组合窗内 MDD | bp_ep 窗收益 | ml 窗收益 |")
w("|---|---|---|---|---|")
for name, a, bnd in WINDOWS:
    cells = [name]
    for eq in (eq_combo, eq_bp, eq_ml):
        seg = eq[(eq.index >= a) & (eq.index <= bnd)]
        if len(seg) < 2:
            cells.append("N/A")
            continue
        ret = seg.iloc[-1] / seg.iloc[0] - 1
        mdd = (seg / seg.cummax() - 1).min()
        if eq is eq_combo:
            cells.append(f"{ret*100:+.2f}%｜{mdd*100:+.2f}%")
        else:
            cells.append(f"{ret*100:+.2f}%")
    w("| " + " | ".join(cells) + " |")
w()
w("读数注记（描述性）：组合 MDD 全史极值 -11.53%（2020-05-22）；压力窗呈现=文档 §八「情景分析」缺口的观察级闭合，"
  "风险预算改造（风险平价/BL 等）属组合结构变更，仍须主人立项，本表不构成任何配置建议。")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("\n".join(L))
print(f"\n[OK] {OUT}")
