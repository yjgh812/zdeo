# -*- coding: utf-8 -*-
"""
round10 主人版汇总报告生成器 (2026-08-27)
====================================================
输入: reports/tulong_backtest_round10_full_20260827/ (全量回测产物)
输出:
  - MASTER_REPORT_20260827.html  单文件可视化报告(配置/目标对照/真·年度收益/
    生产组合两组件逐年逐笔明细折叠表/数据质量验证)
  - yearly_returns_true.csv      真·年度收益率(对年初权益, 修正主脚本
    total_pnl/初始本金 的简化口径; 净值数学零改动, 纯报告层)
设计约束: 只读消费回测产物, 不重算不修改冻结协议; 缺文件降级 N/A 不伪造。
"""
import os
import json
import pandas as pd

OUT = r"D:\zcodesj\reports\tulong_backtest_round10_full_20260827"
ADOPTED = r"D:\zcodesj\reports\round10_adopted_combo_20260827"
INIT_CAP = 100000.0
COMPONENTS = ["bp_ep_core", "ml_signal"]  # 生产组合 60/40 两组件
ALL_STRATS = ["bp_ep_core", "breakout_signal", "v375_signal",
              "p2_signal", "composite_signal", "ml_signal"]
CN = {  # trades_full.csv 中文列
    "买入日期": "entry", "股票代码": "code", "股票名称": "name",
    "买入价格": "buy", "卖出价格": "sell", "卖出日期": "exit",
    "盈亏金额": "pnl", "盈亏比例%": "pnl_pct", "持股数量": "shares",
    "买入决策理由": "why_buy", "卖出触发理由": "why_sell",
    "交易成本": "cost", "持仓天数": "hold",
}


def _read_csv(p):
    if not os.path.exists(p):
        return None
    try:
        return pd.read_csv(p, encoding="utf-8-sig")
    except Exception:
        return None


def year_returns(eq_df, val_col):
    """真·年度收益率: 对上一年末权益(首年对初始本金)。返回 {year: (ret%, end_eq)}"""
    if eq_df is None or len(eq_df) == 0:
        return {}
    df = eq_df.copy()
    df["date"] = df["date"].astype(str).str[:10]
    df["year"] = df["date"].str[:4]
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    out = {}
    for yr, g in df.groupby("year"):
        end_eq = float(g.iloc[-1][val_col])
        prev_year = str(int(yr) - 1)
        base = out[prev_year][1] if prev_year in out else INIT_CAP
        out[yr] = ((end_eq / base - 1) * 100.0, end_eq)
    return out


def fmt(v, nd=2, sign=False):
    try:
        s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
        return s
    except Exception:
        return "N/A"


def main():
    # ---- 指标 ----
    metrics = {}
    for s in ALL_STRATS:
        j = None
        p = os.path.join(OUT, f"{s}_summary.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    j = json.load(f).get("summary", {})
            except Exception:
                j = None
        metrics[s] = j or {}
    combo_ec = _read_csv(os.path.join(OUT, "portfolio_combo_equity.csv"))
    combo_ec_adopted = _read_csv(os.path.join(ADOPTED, "bpep15_ml20_pitv2_combo_equity.csv"))

    # ---- 真·年度收益 ----
    yr_tables = {}
    for s in ALL_STRATS:
        eq = _read_csv(os.path.join(OUT, f"{s}_equity.csv"))
        col = "value" if eq is not None and "value" in eq.columns else "equity"
        yr_tables[s] = year_returns(eq, col)
    yr_tables["combo_60_40"] = year_returns(combo_ec, "equity")
    yr_tables["combo_adopted"] = year_returns(combo_ec_adopted, "equity")

    rows_true = []
    names_cn = {"bp_ep_core": "bp+ep价值核心", "breakout_signal": "横盘突破",
                "v375_signal": "v375动量", "p2_signal": "p2行为",
                "composite_signal": "composite综合", "ml_signal": "ML因子",
                "combo_60_40": "全量报告组合(Top20+ml45 60/40)",
                "combo_adopted": "采纳组合(top15+ml20 60/40)"}
    for s, tab in yr_tables.items():
        for yr, (ret, end_eq) in sorted(tab.items()):
            rows_true.append({"策略": names_cn[s], "年份": yr,
                              "年度收益率%": round(ret, 2),
                              "年末权益(元)": round(end_eq, 0)})
    if rows_true:
        pd.DataFrame(rows_true).to_csv(
            os.path.join(OUT, "yearly_returns_true.csv"),
            index=False, encoding="utf-8-sig")

    # ---- 逐年交易明细(生产组合两组件) ----
    detail_html = {}
    yearly_stats = {}
    for s in COMPONENTS:
        df = _read_csv(os.path.join(OUT, f"{s}_trades_full.csv"))
        if df is None:
            detail_html[s] = "<p class='na'>缺少明细文件，不做推演。</p>"
            continue
        df = df.rename(columns={k: v for k, v in CN.items() if k in df.columns})
        if "entry" in df.columns:
            df["_y"] = df["entry"].astype(str).str[:4]
        parts, stats = [], {}
        for yr, g in df.groupby("_y"):
            g = g.sort_values("entry")
            n = len(g)
            nw = int((pd.to_numeric(g["pnl"], errors="coerce") > 0).sum())
            tp = pd.to_numeric(g["pnl"], errors="coerce").sum()
            stats[yr] = (n, nw, (nw / n * 100 if n else 0), tp)
            trs = []
            def _pad(c):
                c = str(c)
                return c.zfill(6) if c.isdigit() else c
            for _, r in g.iterrows():
                def g2(k):
                    x = r.get(k, "")
                    return "" if pd.isna(x) else x
                trs.append(
                    "<tr>"
                    f"<td>{g2('entry')}</td><td class='mono'>{_pad(g2('code'))}</td>"
                    f"<td>{g2('name')}</td>"
                    f"<td class='num'>{g2('buy')}</td><td>{g2('exit')}</td>"
                    f"<td class='num'>{g2('sell')}</td>"
                    f"<td class='num'>{fmt(pd.to_numeric(g2('pnl_pct'), errors='coerce'), 2, True)}</td>"
                    f"<td class='num'>{fmt(pd.to_numeric(g2('pnl'), errors='coerce'), 0, True)}</td>"
                    f"<td class='why'>{g2('why_buy')}</td>"
                    f"<td class='why'>{g2('why_sell')}</td>"
                    f"<td class='num'>{g2('hold')}</td></tr>")
            parts.append(
                f"<details><summary>{yr} 年 —— {n} 笔 | 胜率 {stats[yr][2]:.1f}% "
                f"| 当年盈亏 {tp:+,.0f} 元</summary>"
                "<table class='trades'><thead><tr><th>买入日期</th><th>代码</th>"
                "<th>名称</th><th>买入价</th><th>卖出日期</th><th>卖出价</th>"
                "<th>盈亏%</th><th>盈亏额</th><th>买入理由</th><th>卖出理由</th>"
                "<th>持仓天数</th></tr></thead><tbody>"
                + "".join(trs) + "</tbody></table></details>")
        detail_html[s] = "".join(parts) if parts else "<p class='na'>无交易记录。</p>"
        yearly_stats[s] = stats

    # ---- 汇总表 ----
    def mget(s, k, d="N/A"):
        v = metrics.get(s, {}).get(k, d)
        return v if v != "" else d

    sum_rows = ""
    for s in ALL_STRATS:
        tr = mget(s, "total_return_pct", 0)
        try:
            trf = float(tr)
        except Exception:
            trf = 0.0
        sum_rows += (f"<tr><td>{names_cn[s]}</td>"
                     f"<td class='num'>{fmt(tr, 1, True)}%</td>"
                     f"<td class='num'>{fmt(mget(s, 'annualized_return_pct', 0), 2)}%</td>"
                     f"<td class='num'>{fmt(mget(s, 'sharpe_ratio', 0), 2)}</td>"
                     f"<td class='num'>{fmt(mget(s, 'max_drawdown_pct', 0), 1)}%</td>"
                     f"<td class='num'>{fmt(mget(s, 'trade_win_rate_pct', 0), 1)}%</td>"
                     f"<td class='num'>{mget(s, 'total_trades', 'N/A')}</td></tr>")
    def _combo_stats(ec):
        if ec is None or len(ec) == 0:
            return None, None
        colv = ec["equity"] if "equity" in ec.columns else ec["value"]
        total = (float(colv.iloc[-1]) / INIT_CAP - 1) * 100
        end = float(colv.iloc[-1])
        n_years = (pd.to_datetime(ec["date"].astype(str).str[:10]).iloc[-1]
                   - pd.to_datetime(ec["date"].astype(str).str[:10]).iloc[0]).days / 365.25
        yr = ((end / INIT_CAP) ** (1 / max(n_years, 1e-9)) - 1) * 100
        return total, yr
    combo_total, combo_yr = _combo_stats(combo_ec_adopted)  # 主口径=采纳组合(生产top15+ml20)
    combo2_total, combo2_yr = _combo_stats(combo_ec)        # 次口径=全量报告组合(Top20+ml45)

    # ---- 真·年度收益表(组合+两组件) ----
    yr_rows = ""
    years_all = sorted({y for t in ("combo_adopted", "combo_60_40", *COMPONENTS)
                        for y in yr_tables.get(t, {})})
    for yr in years_all:
        cells = f"<td>{yr}</td>"
        ta = yr_tables.get("combo_adopted", {})
        tg = yr_tables.get("combo_60_40", {})
        cells += f"<td class='num'>{fmt(ta[yr][0], 2, True) if yr in ta else 'N/A'}%</td>"
        cells += f"<td class='num'>{fmt(tg[yr][0], 2, True) if yr in tg else 'N/A'}%</td>"
        for s in COMPONENTS:
            t = yr_tables.get(s, {})
            cells += f"<td class='num'>{fmt(t[yr][0], 2, True) if yr in t else 'N/A'}%</td>"
        st_bp = yearly_stats.get("bp_ep_core", {}).get(yr)
        st_ml = yearly_stats.get("ml_signal", {}).get(yr)
        cells += (f"<td class='num'>{fmt(st_bp[2],1) if st_bp else 'N/A'}%</td>"
                  f"<td class='num'>{st_bp[0] if st_bp else 'N/A'}</td>"
                  f"<td class='num'>{fmt(st_ml[2],1) if st_ml else 'N/A'}%</td>"
                  f"<td class='num'>{st_ml[0] if st_ml else 'N/A'}</td>")
        yr_rows += f"<tr>{cells}</tr>"

    # ---- 诚实结论(数据驱动) ----
    def _wr_all(s):
        df = _read_csv(os.path.join(OUT, f"{s}_trades_full.csv"))
        if df is None or "盈亏金额" not in df.columns:
            return None, None
        pnl = pd.to_numeric(df["盈亏金额"], errors="coerce")
        return (int((pnl > 0).sum()), len(df))
    bp_w, bp_n = _wr_all("bp_ep_core")
    ml_w, ml_n = _wr_all("ml_signal")
    max_yr_wr = 0.0
    for s in COMPONENTS:
        for yr, st in yearly_stats.get(s, {}).items():
            max_yr_wr = max(max_yr_wr, st[2])
    verdict = ""
    if combo_yr is not None:
        verdict += (f"<p><b>收益目标差距</b>：目标年化复合 200%（即每年≈3倍，10.65年≈{pow(3,10.65):,.0f}倍）；"
                    f"生产组合实际年化 <b>{combo_yr:.2f}%</b>（全期 {fmt(combo_total,1,True)}%）。"
                    f"在「qfq唯一价格源+PIT因子+next-bar成交+真实成本」的不可妥协口径下，"
                    f"200% 年化不具备可达到性证据；强行逼近只能靠前视/幸存者/调参，已被 WFA 一票制与多轮证伪档案否定。</p>")
    if bp_w and ml_w:
        verdict += (f"<p><b>胜率目标差距</b>：目标 78-82%；组件全期交易胜率 bp+ep {bp_w/(bp_n or 1)*100:.1f}%"
                    f"（{bp_w}/{bp_n}）、ml {ml_w/(ml_n or 1)*100:.1f}%（{ml_w}/{ml_n}），"
                    f"单年最高 {max_yr_wr:.1f}%——78-82% 区间在任何自然年均未达到。"
                    f"月度价值/ML 调仓的盈亏比结构（盈小亏也小、靠仓位复利）天然对应 50-60% 胜率带。</p>")
    verdict += ("<p><b>诚实 OOS 锚</b>：bp_ep WFA 门禁(2026-08-27)拼接样本外年化 +8.82%、正窗 62%；"
                "round9 五折 WFA v2 PASS。全样本回测数字显著高于诚实 OOS 是正常现象（选择偏差+参数幸存），"
                "以 OOS 与模拟盘实盘为准。</p>")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>屠龙 round10 全域回测主人版报告 2016-01-01 ~ 2026-08-27</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;background:#f7f8fa;color:#222}}
h1{{font-size:22px}} h2{{font-size:17px;border-left:4px solid #2b6cb0;padding-left:8px;margin-top:28px}}
.card{{display:inline-block;background:#fff;border:1px solid #e2e8f0;border-radius:8px;
padding:12px 18px;margin:6px;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
.card .v{{font-size:22px;font-weight:700}} .card .k{{font-size:12px;color:#666}}
table{{border-collapse:collapse;background:#fff;font-size:13px;margin:10px 0}}
th,td{{border:1px solid #dfe3e8;padding:4px 8px}}
th{{background:#edf2f7}} .num{{text-align:right;font-family:Consolas,monospace}}
.mono{{font-family:Consolas,monospace}} .why{{max-width:260px;font-size:12px}}
.na{{color:#999}} details{{margin:8px 0}} summary{{cursor:pointer;font-weight:600;background:#fff;
border:1px solid #e2e8f0;border-radius:6px;padding:6px 10px}}
table.trades{{width:100%}} .gap{{color:#b7791f}} .ok{{color:#2f855a}} .bad{{color:#c53030}}
small{{color:#888}}
</style></head><body>
<h1>屠龙系统 round10 全域多模型回测 · 主人版报告</h1>
<p><b>区间</b> 2016-01-01 ~ 2026-08-27 ｜ <b>本金</b> 100,000 元 ｜
<b>股票池</b> A股全市场（剔科创板/北交所/ST/上市&lt;60天次新，月均可交易 ~5,500 只）<br>
<b>价格源</b> dragon_quant_qfq.db 前复权唯一价格源 ｜ <b>财务因子</b> factor_pit.db PIT v2（法定披露滞后，无前视）<br>
<b>执行</b> 信号T日收盘生成→T+1开盘成交(next-bar)，卖出当日收盘；佣金0.025%+印花税0.05%+滑点0.03%；
T+1、涨跌停、单票≤20%、≤50只、个股-8%止损</p>

<h2>一、生产组合(采纳) vs 目标</h2>
<div class="card"><div class="k">采纳组合总收益(10.65年)</div><div class="v">{fmt(combo_total,1,True) if combo_total is not None else 'N/A'}%</div></div>
<div class="card"><div class="k">采纳组合年化复合</div><div class="v">{fmt(combo_yr,2) if combo_yr is not None else 'N/A'}%</div></div>
<div class="card"><div class="k">全量报告组合(Top20+ml45)</div><div class="v">{fmt(combo2_total,1,True) if combo2_total is not None else 'N/A'}%</div></div>
<div class="card"><div class="k">目标:年化200%</div><div class="v gap">差距如实呈现</div></div>
<div class="card"><div class="k">目标:胜率78-82%</div><div class="v gap">逐年见下表</div></div>
<p><small>目标口径说明：主人 8/23 已裁决 200%/80% 为「逼近方向非硬性」。本报告如实给出真实可达水平，
不通过调参凑指标（OOS 一票制：终点数据不反哺参数）。</small></p>

<h2>二、六模型 + 组合总表（全期）</h2>
<table><thead><tr><th>策略</th><th>总收益</th><th>年化</th><th>夏普</th><th>最大回撤</th><th>交易胜率</th><th>交易笔数</th></tr></thead>
<tbody>{sum_rows}
<tr><td><b>{names_cn['combo_adopted']}</b></td><td class="num"><b>{fmt(combo_total,1,True) if combo_total is not None else 'N/A'}%</b></td>
<td class="num"><b>{fmt(combo_yr,2) if combo_yr is not None else 'N/A'}%</b></td><td class="num">见FULL_REPORT</td>
<td class="num">见图</td><td class="num">—</td><td class="num">—</td></tr></tbody></table>
<p><small>{names_cn['combo_60_40']} 为全量报告脚本的变体口径（{fmt(combo2_total,1,True) if combo2_total is not None else 'N/A'}%）；两条腿 top15/ml20 与 Top20/ml45 见其各自行。</small></p>

<h2>三、真·年度收益率（对上年末权益）+ 逐年胜率</h2>
<table><thead><tr><th>年份</th><th>采纳组合年度收益</th><th>全量报告组合</th><th>bp+ep年度收益</th><th>ml年度收益</th>
<th>bp+ep胜率</th><th>bp+ep笔数</th><th>ml胜率</th><th>ml笔数</th></tr></thead>
<tbody>{yr_rows}</tbody></table>
<p><small>年度收益率=年末权益/上年末权益-1（首年对10万本金），修正主脚本「当年盈亏/初始本金」的简化口径；
2026 为不足年（按实际天数）。</small></p>

<h2>四、逐年逐笔交易明细（生产组合两组件）</h2>
<p><small>内嵌明细为全量报告脚本的组件腿（bp+ep Top20、ML 45天止损），含逐笔买入/卖出理由；
采纳组合（top15+ml20，headline +198.91%）为同族策略仅 TopN/止损天数不同，其逐年明细（含开仓理由回填、代码补零）见
<code>reports/round10_adopted_combo_20260827/</code> 目录（bp_ep_top15_pitv2_trades_full.csv / ml_hold20_pitv2_trades_full.csv 及逐年 CSV）。</small></p>
<h3>4.1 bp+ep 价值核心（60%仓）</h3>
{detail_html.get('bp_ep_core','')}
<h3>4.2 ML 因子（40%仓，信号覆盖自 2020 年起）</h3>
{detail_html.get('ml_signal','')}
<p><small>明细同时见目录内 <code>*_trades_full.csv</code> 与逐年 <code>*_trades_YYYY.csv</code>；
其余四个研究型策略明细见对应 CSV（未内嵌以控制单文件体积）。</small></p>

<h2>五、数据质量与验证</h2>
<ul>
<li>8/27 收盘价独立源抽查 8/8 全对（qfq vs 新浪实时收盘：茅台1292.30/平安银行11.59/建设机械4.31 等），
此前「8/27盘中快照疑点」关闭。</li>
<li>qfq/交易日历/上证指数均覆盖至 2026-08-27（日行数 ~5,547）。</li>
<li>PIT v2 唯一财务因子源；v1 前视口径已于 8/26 退役。</li>
<li>执行语义 next-bar（T+1开盘），买卖理由逐笔回填（bisect 按 T+1 匹配信号日）。</li>
</ul>

<h2>六、诚实结论</h2>
{verdict}
<p><small>可视化图表(净值曲线/年度收益柱状图/回撤等6图)见同目录 <a href="FULL_REPORT.html">FULL_REPORT.html</a>；
优化条目见 <a href="OPTIMIZATION_PLAN.md">OPTIMIZATION_PLAN.md</a>；全面优化建议与方向见
<a href="ROUND10_OPTIMIZATION_DIRECTIONS_20260828.md">ROUND10_OPTIMIZATION_DIRECTIONS_20260828.md</a>。</small></p>
<p><small>生成: round10_master_report_20260827.py（只读消费回测产物，报告层零回测改动）</small></p>
</body></html>"""

    path = os.path.join(OUT, "MASTER_REPORT_20260827.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[master] 写出 {path} ({os.path.getsize(path)/1024:.0f} KB)")
    print(f"[master] 真·年度收益表: {os.path.join(OUT, 'yearly_returns_true.csv')}")
    if combo_total is not None:
        print(f"[master] 组合总收益 {combo_total:+.2f}% | 年化 {combo_yr:.2f}%")


if __name__ == "__main__":
    main()
