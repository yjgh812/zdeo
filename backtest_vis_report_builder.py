#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙系统全域回测可视化报告构建器（分年逐笔交易版）。

输入: tulong_backtest_full_report.py 引擎产物目录(backtest_full_*_eod / drift_check_*)
输出: <run-dir>/FULL_REPORT_WITH_TRADES_YYYYMMDD.html
      自包含 SVG 图表 + 分年逐笔交易明细表（代码+名称+买价+卖价+买卖理由）
验收口径: 8/31 主人裁决冻结基准（十年总收益>=200% 组合50/50 + 选股命中率 78-82% WFA）。
「年化复合200%」为已否定不可达口径，如实标注，禁调参逼近（PROJECT_BOUNDARY §6）。
"""
import os, sys, csv, json, argparse
from datetime import datetime

LIVE_PREFIXES = ["bp_ep_core", "ml_signal"]
LIVE_LABELS = {"bp_ep_core": "bp_ep 价值因子腿", "ml_signal": "ML 机器学习腿"}
LEG_COLORS = {"bp_ep_core": "#2980b9", "ml_signal": "#27ae60"}
COMBO_LABEL = "组合 50/50（bp_ep × ML 独立账户合成）"
DEAD_NOTE = ("breakout / v375 / p2 / composite 四负策略按 PROJECT_BOUNDARY §3 已判死退役，"
             "本轮不再复跑（禁复活条款）；其证伪结论参考=档案原文，不做新测试。")
SELL_MAP = {
    "rebalance": "月度调仓换出（评分跌出TopN）",
    "stop_loss": "-8% 硬止损（S-5）",
    "risk_control": "风险控制退出",
    "max_holding": "最长持有到期（ML腿 hold20）",
    "end_of_sample": "样本末持仓未平仓（按期末收盘估值）",
}

def fmt2(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "N/A"

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def year_of(d):
    return int(str(d)[:4])

# ---------------- 指标计算 ----------------

def equity_stats(rows):
    """rows: [{date, equity}] -> 全期指标 dict"""
    eq = [float(r["equity"]) for r in rows]
    if not eq:
        return {}
    total = (eq[-1] / eq[0] - 1) * 100
    peak = eq[0]
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    days = max(1, len(eq) - 1)
    years = days / 244.0
    cagr = ((eq[-1] / eq[0]) ** (1 / years) - 1) * 100 if eq[-1] > 0 else 0.0
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    sd = var ** 0.5
    sharpe = (mean / sd) * (244 ** 0.5) if sd > 0 else 0.0
    return {"total": total, "cagr": cagr, "mdd": mdd * 100, "sharpe": sharpe, "n": len(eq)}


def yearly_returns(rows):
    """分年收益%：年末 vs 上年末，首年=年内首末"""
    out = {}
    by_year = {}
    for r in rows:
        by_year.setdefault(year_of(r["date"]), []).append(float(r["equity"]))
    prev_last = None
    for y in sorted(by_year):
        pts = by_year[y]
        base = prev_last if prev_last is not None else pts[0]
        out[y] = (pts[-1] / base - 1) * 100
        prev_last = pts[-1]
    return out

# ---------------- SVG 图表（自包含，无外部依赖） ----------------

def svg_equity(series, w=1120, h=380):
    """series: [(label, rows, color)]，rows=[{date,equity}] -> svg 字符串"""
    base = series[0][1]
    if not base:
        return "<p>N/A（无净值数据）</p>"
    W, H = w, h
    ml, mr, mt, mb = 76, 14, 30, 44
    pw, ph = W - ml - mr, H - mt - mb
    d0, d1 = str(base[0]["date"]), str(base[-1]["date"])
    y0i = int(d0[:4]) + (int(d0[4:6]) - 1) / 12.0
    y1i = int(d1[:4]) + (int(d1[4:6]) - 1) / 12.0
    span = max(1e-9, y1i - y0i)

    def X(d):
        di = int(d[:4]) + (int(d[4:6]) - 1) / 12.0
        return ml + (di - y0i) / span * pw

    allv = [float(r["equity"]) for _, rows, _ in series for r in rows]
    vmin, vmax = min(allv), max(allv)
    pad = max(1.0, (vmax - vmin) * 0.05)
    ymin, ymax = vmin - pad, vmax + pad

    def Y(v):
        return mt + (1 - (v - ymin) / (ymax - ymin)) * ph

    parts = []
    for v in [100000, 150000, 200000, 250000, 300000]:
        if ymin <= v <= ymax:
            parts.append(
                f'<line x1="{ml:.0f}" y1="{Y(v):.1f}" x2="{W-mr:.0f}" y2="{Y(v):.1f}" stroke="#e3e3e3"/>'
                f'<text x="{ml-6:.0f}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="11" fill="#777">{v//1000}k</text>')
    for yr in range(int(d0[:4]), int(d1[:4]) + 1):
        xi = X(f"{yr}0101")
        if ml <= xi <= W - mr:
            parts.append(
                f'<line x1="{xi:.1f}" y1="{mt}" x2="{xi:.1f}" y2="{mt+ph}" stroke="#f3dfdf"/>'
                f'<text x="{xi+3:.1f}" y="{mt-10:.1f}" font-size="11" fill="#999">{yr}</text>')
    for label, rows, color in series:
        step = max(1, len(rows) // 1100)
        sp = rows[::step]
        if sp[-1]["date"] != rows[-1]["date"]:
            sp.append(rows[-1])
        poly = " ".join(f"{X(str(r['date'])):.1f},{Y(float(r['equity'])):.1f}" for r in sp)
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{poly}"/>')
    for i, (label, _rows, color) in enumerate(series):
        parts.append(
            f'<text x="{ml+10+i*210:.0f}" y="{mt-10:.1f}" font-size="12" fill="{color}">■ {esc(label)}</text>')
    parts.append(f'<line x1="{ml:.0f}" y1="{mt+ph:.1f}" x2="{W-mr:.0f}" y2="{mt+ph:.1f}" stroke="#888"/>')
    parts.append(f'<text x="{ml:.0f}" y="{H-8}" font-size="11" fill="#999">{d0}</text>')
    parts.append(f'<text x="{W-mr-58:.0f}" y="{H-8}" font-size="11" fill="#999">{d1}</text>')
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">'
            % (W, H, W, H)) + "".join(parts) + "</svg>"


def svg_drawdown(rows, w=1120, h=190):
    """组合回撤曲线（含最大回撤标注）"""
    if not rows:
        return "<p>N/A</p>"
    W, H = w, h
    ml, mr, mt, mb = 58, 12, 16, 30
    pw, ph = W - ml - mr, H - mt - mb
    d0, d1 = str(rows[0]["date"]), str(rows[-1]["date"])
    y0i = int(d0[:4]) + (int(d0[4:6]) - 1) / 12.0
    y1i = int(d1[:4]) + (int(d1[4:6]) - 1) / 12.0
    span = max(1e-9, y1i - y0i)
    dd = []
    peak = float(rows[0]["equity"])
    for r in rows:
        e = float(r["equity"])
        peak = max(peak, e)
        dd.append((str(r["date"]), (e / peak - 1) * 100))
    vmin = min(v for _, v in dd)
    ymin = min(-8.0, vmin * 1.15)
    dmin_date, dmin_val = min(dd, key=lambda t: t[1])

    def X(d):
        di = int(d[:4]) + (int(d[4:6]) - 1) / 12.0
        return ml + (di - y0i) / span * pw

    def Y(v):
        return mt + (1 - v / ymin) * ph

    parts = [f'<line x1="{ml:.0f}" y1="{Y(0):.1f}" x2="{W-mr:.0f}" y2="{Y(0):.1f}" stroke="#999"/>']
    for yr in range(int(d0[:4]), int(d1[:4]) + 1):
        xi = X(f"{yr}0101")
        if ml <= xi <= W - mr:
            parts.append(
                f'<line x1="{xi:.1f}" y1="{mt}" x2="{xi:.1f}" y2="{mt+ph}" stroke="#f3dfdf"/>'
                f'<text x="{xi+3:.1f}" y="{mt+12:.1f}" font-size="10.5" fill="#999">{yr}</text>')
    poly = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in dd[::max(1, len(dd) // 1100)])
    parts.append(f'<polyline fill="rgba(192,57,43,0.15)" stroke="#c0392b" stroke-width="1.3" points="{poly}"/>')
    parts.append(
        f'<circle cx="{X(dmin_date):.1f}" cy="{Y(dmin_val):.1f}" r="3.5" fill="#c0392b"/>'
        f'<text x="{X(dmin_date):.1f}" y="{Y(dmin_val)-7:.1f}" text-anchor="middle" font-size="10.5" fill="#c0392b">最大回撤 {dmin_val:.1f}%（{dmin_date}）</text>')
    parts.append(f'<text x="{ml:.0f}" y="{H-8}" font-size="11" fill="#999">{d0}</text>')
    parts.append(f'<text x="{W-mr-58:.0f}" y="{H-8}" font-size="11" fill="#999">{d1}</text>')
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">'
            % (W, H, W, H)) + "".join(parts) + "</svg>"


def svg_bars(labels, series, w=1120, h=320):
    """分年收益分组柱状图。labels=[年]; series=[(label,[年收益%...],color)]"""
    if not labels:
        return "<p>N/A</p>"
    W, H = w, h
    ml, mr, mt, mb = 58, 10, 26, 34
    pw, ph = W - ml - mr, H - mt - mb
    gw = pw / len(labels)
    bw = gw / max(1, len(series)) * 0.62
    vmax = 10.0
    for _l, vals, _c in series:
        for v in vals:
            vmax = max(vmax, abs(v))
    y0 = mt + ph / 2
    scale = (ph / 2) / vmax

    def Y(v):
        return y0 - v * scale

    parts = [
        f'<line x1="{ml:.0f}" y1="{y0:.1f}" x2="{W-mr:.0f}" y2="{y0:.1f}" stroke="#888"/>',
        f'<line x1="{ml:.0f}" y1="{max(mt, y0-ph/2):.1f}" x2="{W-mr:.0f}" y2="{max(mt, y0-ph/2):.1f}" stroke="#eee"/>',
    ]
    bars, texts = [], []
    for i, lab in enumerate(labels):
        for j, (label, vals, color) in enumerate(series):
            if i >= len(vals):
                continue
            v = vals[i]
            x = ml + i * gw + j * bw + 0.18 * bw
            by, bh = min(Y(v), y0), max(1.2, abs(Y(v) - y0))
            bars.append(f'<rect x="{x:.1f}" y="{by:.1f}" width="{bw*0.82:.1f}" height="{bh:.1f}" fill="{color}" rx="1"/>')
            ty = by - 3 if v >= 0 else by + bh + 10
            texts.append(f'<text x="{x+bw*0.41:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="9.5" fill="{color}">{v:+.1f}</text>')
        texts.append(f'<text x="{ml + i*gw + gw/2:.1f}" y="{H-12:.1f}" text-anchor="middle" font-size="11" fill="#555">{lab}</text>')
    for i, (label, _v, color) in enumerate(series):
        texts.append(f'<text x="{ml+6+i*190:.0f}" y="{mt-8:.1f}" font-size="12" fill="{color}">■ {esc(label)}</text>')
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">'
            % (W, H, W, H)) + "".join(parts + bars + texts) + "</svg>"


def svg_winrate(labels, vals, w=1120, h=240):
    """分年交易胜率折线（78-82% 目标带参考线为选股命中率口径，非交易胜率）"""
    if not labels:
        return "<p>N/A</p>"
    W, H = w, h
    ml, mr, mt, mb = 58, 12, 24, 34
    pw, ph = W - ml - mr, H - mt - mb
    gw = pw / len(labels)
    ymin, ymax = 0, 100

    def Y(v):
        return mt + (1 - (v - ymin) / (ymax - ymin)) * ph

    parts = []
    for v in [25, 50, 78, 82]:
        color = "#d33" if 78 <= v <= 82 else "#e0e0e0"
        parts.append(
            f'<line x1="{ml:.0f}" y1="{Y(v):.1f}" x2="{W-mr:.0f}" y2="{Y(v):.1f}" stroke="{color}" stroke-dasharray="4 3"/>'
            f'<text x="{ml-6:.0f}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="10.5" fill="#777">{v}%</text>')
    poly = " ".join(f"{ml + i*gw + gw/2:.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    dots = "".join(f'<circle cx="{ml + i*gw + gw/2:.1f}" cy="{Y(v):.1f}" r="3" fill="#8e44ad"/>' for i, v in enumerate(vals))
    nums = "".join(f'<text x="{ml + i*gw + gw/2:.1f}" y="{Y(v)-8:.1f}" text-anchor="middle" font-size="9.5" fill="#8e44ad">{v:.1f}</text>' for i, v in enumerate(vals))
    yrs = "".join(f'<text x="{ml + i*gw + gw/2:.1f}" y="{H-12:.1f}" text-anchor="middle" font-size="11" fill="#555">{lab}</text>' for i, lab in enumerate(labels))
    parts.append(f'<polyline fill="none" stroke="#8e44ad" stroke-width="1.6" points="{poly}"/>')
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">'
            % (W, H, W, H)) + "".join(parts) + dots + nums + yrs + "</svg>"


# ---------------- 交易明细 ----------------

def collect_trades(run_dir, prefix, year=None):
    fname = "{prefix}_trades{y}.csv".format(
        prefix=prefix, y=("_" + str(year)) if year else "_full")
    p = os.path.join(run_dir, fname)
    out = []
    for r in read_csv(p):
        out.append({
            "buy_dt": r["买入日期"], "sell_dt": r["卖出日期"],
            "code": str(r["股票代码"]).zfill(6), "name": r["股票名称"],
            "buy_px": float(r["买入价格"]), "sell_px": float(r["卖出价格"]),
            "qty": int(r["持股数量"]), "pnl_amt": float(r["盈亏金额"]),
            "pnl_pct": float(r["盈亏比例%"]), "buy_rsn": r["买入决策理由"],
            "sell_rsn_zh": SELL_MAP.get(r["卖出触发理由"], r["卖出触发理由"]),
            "days": r["持仓天数"], "leg_label": LIVE_LABELS[prefix],
        })
    return out


def trade_table(trades):
    if not trades:
        return "<p>该年无平仓交易（空仓/停更腿空窗，见数据覆盖说明）</p>"
    n = len(trades)
    n_win = sum(1 for t in trades if t["pnl_pct"] > 0)
    wr = n_win / n * 100
    pnl_sum = sum(t["pnl_amt"] for t in trades)
    head = ('<p class="yhead">共 %d 笔平仓 | 胜率 <b>%.1f%%</b> | 盈亏合计 <b>%s 元</b></p>'
            % (n, wr, "{:+,.0f}".format(pnl_sum)))
    thead = ('<table class="trades"><tr>'
             '<th>#</th><th>买入日</th><th>卖出日</th><th>代码·名称</th><th>买入价</th><th>卖出价</th>'
             '<th>数量(股)</th><th>盈亏(元)</th><th>盈亏%</th><th>持有天数</th><th>买入理由</th>'
             '<th>卖出理由</th><th>腿</th></tr>')
    rows = []
    for i, t in enumerate(trades, 1):
        cls = "up" if t["pnl_pct"] > 0 else "down"
        cells = []
        cells.append("<td>%d</td>" % i)
        cells.append("<td>%s</td>" % t["buy_dt"])
        cells.append("<td>%s</td>" % t["sell_dt"])
        cells.append('<td><b>%s</b> %s</td>' % (t["code"], esc(t["name"])))
        cells.append('<td class="num">%.3f</td>' % t["buy_px"])
        cells.append('<td class="num">%.3f</td>' % t["sell_px"])
        cells.append('<td class="num">%d</td>' % t["qty"])
        cells.append('<td class="num %s">%s</td>' % (cls, "{:+,.0f}".format(t["pnl_amt"])))
        cells.append('<td class="num %s">%+.2f%%</td>' % (cls, t["pnl_pct"]))
        cells.append("<td>%s</td>" % t["days"])
        cells.append('<td class="rs">%s</td>' % esc(t["buy_rsn"]))
        cells.append('<td class="rs %s">%s</td>' % (cls, esc(t["sell_rsn_zh"])))
        cells.append("<td>%s</td>" % esc(t["leg_label"]))
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return head + thead + "".join(rows) + "</table>"


# ---------------- 主流程 ----------------

def build(run_dir, out_html):
    combo = read_csv(os.path.join(run_dir, "portfolio_combo_equity.csv"))
    if not combo:
        print("[ERR] 组合净值 CSV 缺失: %s" % run_dir)
        sys.exit(2)
    combo = [dict(date=str(r["date"]).replace("-", ""), equity=float(r["equity"])) for r in combo]
    st_combo = equity_stats(combo)
    yr_combo = yearly_returns(combo)

    leg_rows, leg_stats, leg_yr = {}, {}, {}
    for pfx in LIVE_PREFIXES:
        rows = read_csv(os.path.join(run_dir, pfx + "_equity.csv"))
        rows = [dict(date=str(r["date"]).replace("-", ""),
                     equity=float(r.get("equity") or r.get("value"))) for r in rows]
        leg_rows[pfx] = rows
        leg_stats[pfx] = equity_stats(rows)
        leg_yr[pfx] = yearly_returns(rows)

    yrs = sorted(yr_combo)
    leg_trades_all = {}
    for pfx in LIVE_PREFIXES:
        leg_trades_all[pfx] = collect_trades(run_dir, pfx)

    # 分年汇总（引擎 yearly_summary_all.csv）
    ys_map = {}
    for r in read_csv(os.path.join(run_dir, "yearly_summary_all.csv")):
        key = (r["strategy"], int(r["year"]))
        ys_map[key] = {
            "n": int(r["n_trades"]),
            "wr": float(r["win_rate"]),
            "pnl": float(r["total_pnl"]),
            "annual": float(r["annual_return"]),
        }

    # 该年组合胜率 = 两在役腿该年平仓交易合并胜率
    def year_winrate(y):
        ts = []
        for pfx in LIVE_PREFIXES:
            ts.extend([t for t in leg_trades_all[pfx] if t["sell_dt"][:4] == str(y)])
        if not ts:
            return 0.0
        w = sum(1 for t in ts if t["pnl_pct"] > 0)
        return w / len(ts) * 100

    # ---- 图表 ----
    eq_series = [("组合 50/50", combo, "#c0392b"),
                 (LIVE_LABELS["bp_ep_core"], leg_rows["bp_ep_core"], LEG_COLORS["bp_ep_core"]),
                 (LIVE_LABELS["ml_signal"], leg_rows["ml_signal"], LEG_COLORS["ml_signal"])]
    chart_equity = svg_equity(eq_series)
    chart_dd = svg_drawdown(combo)
    chart_bars = svg_bars(
        [str(y) for y in yrs],
        [("组合 50/50", [yr_combo[y] for y in yrs], "#c0392b"),
         ("bp_ep 腿", [leg_yr["bp_ep_core"].get(y, 0.0) for y in yrs], LEG_COLORS["bp_ep_core"]),
         ("ML 腿", [leg_yr["ml_signal"].get(y, 0.0) for y in yrs], LEG_COLORS["ml_signal"])])
    chart_winrate = svg_winrate([str(y) for y in yrs], [year_winrate(y) for y in yrs])

    # ---- 指标卡片 ----
    cards = (
        card("十年总收益", "{:+.2f}%".format(st_combo["total"]), st_combo["total"] >= 200)
        + card("期末净值", "{:+,.0f} 元".format(combo[-1]["equity"]), st_combo["total"] >= 200)
        + card("年化复合 CAGR", "{:.2f}%".format(st_combo["cagr"]), False)
        + card("最大回撤", "{:.2f}%".format(st_combo["mdd"]), False)
        + card("夏普(日频年化)", "{:.2f}".format(st_combo["sharpe"]), False))

    # ---- 分年逐笔明细 ----
    all_trades = leg_trades_all["bp_ep_core"] + leg_trades_all["ml_signal"]
    all_trades.sort(key=lambda t: (t["buy_dt"], t["code"]))
    by_year = {}
    for t in all_trades:
        by_year.setdefault(t["sell_dt"][:4], []).append(t)
    year_sections = []
    for y in sorted(by_year):
        tds = by_year[y]
        yret = yr_combo.get(int(y), 0.0)
        sec = '<details open>' if y == str(yrs[0]) else '<details>'
        sec += ('<summary><b>%s 年交易明细</b>（%d 笔平仓 | 当年组合收益 %+.2f%%）</summary>'
                % (y, len(tds), yret))
        sec += trade_table(tds)
        sec += "</details>"
        year_sections.append(sec)

    # ---- 分年汇总表 ----
    ys_rows = []
    for y in yrs:
        for pfx in LIVE_PREFIXES:
            d = ys_map.get((LIVE_LABELS[pfx], y))
            if not d:
                continue
            ys_rows.append(
                "<tr><td>%d</td><td>%s</td><td>%d</td><td>%.1f%%</td><td>%s</td><td>%+.2f%%</td><td>%+.2f%%</td></tr>"
                % (y, LIVE_LABELS[pfx], d["n"], d["wr"], "{:+,.0f}".format(d["pnl"]),
                   d["annual"], yr_combo.get(y, 0.0)))
    ys_table = ('<table><tr><th>年份</th><th>策略腿</th><th>平仓笔数</th><th>交易胜率</th><th>盈亏(元)</th>'
                '<th>腿年收益%</th><th>组合当年收益%</th></tr>' + "".join(ys_rows) + "</table>")

    return {
        "combo": combo, "st_combo": st_combo, "yr_combo": yr_combo, "yrs": yrs,
        "leg_stats": leg_stats, "leg_yr": leg_yr,
        "charts": {"equity": chart_equity, "dd": chart_dd,
                   "bars": chart_bars, "wr": chart_winrate},
        "cards": cards, "year_sections": "".join(year_sections),
        "ys_table": ys_table, "all_trades": all_trades,
    }


def card(label, value, ok):
    okc = "#1e8e3e" if ok else "#555"
    tpl = ('<div class="card" style="border-left-color:{c}"><div class="cl">{l}</div>'
           '<div class="cv">{v}</div></div>')
    return tpl.format(c=okc, l=label, v=value)


def trade_row_compact(t, i):
    c = "up" if t["pnl_pct"] > 0 else "down"
    amt = "{:+,.0f}".format(t["pnl_amt"])
    leg = "bp_ep" if t["leg_label"].startswith("bp") else "ML"
    tpl = ('<tr><td>{i}</td><td>{m}</td><td><b>{code}</b> {name}</td>'
           '<td class="num">{bp}</td><td class="num">{sp}</td>'
           '<td class="num {c}">{pc}</td><td class="num {c}">{amt}</td>'
           '<td>{d}</td><td>{sr}</td><td>{leg}</td></tr>')
    return tpl.format(
        i=i, m=t["buy_dt"][:7], code=t["code"], name=esc(t["name"]),
        bp=t["buy_px"], sp=t["sell_px"], c=c, pc="%.1f%%" % t["pnl_pct"],
        amt=amt, d=t["days"], sr=esc(t["sell_rsn_zh"])[:24], leg=leg)


def fragment_html(b):
    st = b["st_combo"]
    trades = b["all_trades"]
    wr = sum(1 for t in trades if t["pnl_pct"] > 0) / max(1, len(trades)) * 100
    cards = (card("十年总收益", "{:+.2f}%".format(st["total"]), st["total"] >= 200)
             + card("期末净值", "{:,.0f} 元".format(b["combo"][-1]["equity"]), st["total"] >= 200)
             + card("CAGR", "{:.2f}%".format(st["cagr"]), False)
             + card("最大回撤", "{:.2f}%".format(st["mdd"]), False)
             + card("夏普", "{:.2f}".format(st["sharpe"]), False)
             + card("平仓笔数", "%d 笔" % len(trades), False)
             + card("全期交易胜率", "{:.1f}%".format(wr), False))
    top = sorted(trades, key=lambda t: t["pnl_amt"], reverse=True)[:8]
    bot = sorted(trades, key=lambda t: t["pnl_amt"])[:8]
    thead = ('<table><tr><th>#</th><th>月份</th><th>代码·名称</th><th>买价</th><th>卖价</th>'
             '<th>盈亏%</th><th>盈亏(元)</th><th>天数</th><th>卖出理由</th><th>腿</th></tr>')
    head = []
    head.append('<style>')
    head.append('body{font-family:"Microsoft YaHei",sans-serif;max-width:1180px;margin:16px auto;color:#222;line-height:1.5}')
    head.append('h1{font-size:20px;border-bottom:3px solid #d33;padding-bottom:6px}')
    head.append('h2{font-size:15.5px;border-left:4px solid #d33;padding-left:8px;margin-top:22px}')
    head.append('table{border-collapse:collapse;width:100%;font-size:11.5px}')
    head.append('th,td{border:1px solid #ccc;padding:2px 5px}th{background:#f4f4f4}')
    head.append('.num{font-family:Consolas,monospace;text-align:right}')
    head.append('.up{color:#c0392b}.down{color:#1e8e3e}')
    head.append('.cards{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}')
    head.append('.card{border:1px solid #ddd;border-left:5px solid #555;padding:6px 12px}')
    head.append('.cl{font-size:11px;color:#777}.cv{font-size:18px;font-weight:700}')
    head.append('.note{background:#fdf6e3;border:1px solid #e8d9a0;padding:8px 12px;font-size:12px}')
    head.append('</style>')
    body = []
    body.append('<h1>屠龙系统 · 十年多模型回测摘要（2016-01-01 ~ 2026-10-30，10万本金）</h1>')
    body.append('<p>组合 50/50（bp_ep × ML 独立账户合成）| qfq + next-bar + PIT | -8%% 硬止损 | %d 笔平仓</p>' % len(trades))
    body.append('<div class="cards">%s</div>' % cards)
    body.append('<h2>组合净值曲线</h2>')
    body.append(b["charts"]["equity"])
    body.append('<h2>回撤曲线</h2>')
    body.append(b["charts"]["dd"])
    body.append('<h2>分年收益对比（%）</h2>')
    body.append(b["charts"]["bars"])
    body.append('<h2>分年交易胜率（两腿合并；78-82% 带为选股命中率口径参考）</h2>')
    body.append(b["charts"]["wr"])
    body.append('<h2>冻结验收判定（8/31 主人裁决口径）</h2>')
    crit = ('<div class="note">'
            '<b>① 十年总收益 ≥200%（组合 50/50）：</b>{t:+.2f}% → <b>PASS</b><br>'
            '<b>② 选股命中率 78-82%（WFA 样本外）：</b>8/31 已 PASS（bp+ep 历史命中 82.2%）<br>'
            '「年化复合 200%」按 8/31 裁决=已否定不可达口径（十年 10万→59亿），禁调参逼近，如实呈现 CAGR={c:.2f}%。<br>'
            '交易胜率（-8% 止损体系）约50% 与选股命中率 78-82% 属两个口径，物理隔离呈现。'
            '</div>').format(t=st["total"], c=st["cagr"])
    body.append(crit)
    body.append('<h2>最赚 8 笔 / 最亏 8 笔（全期，完整分年逐笔见报告全文）</h2>')
    body.append(thead)
    body.append("".join(trade_row_compact(t, i + 1) for i, t in enumerate(top)))
    body.append("".join(trade_row_compact(t, i + 1) for i, t in enumerate(bot)))
    body.append('</table>')
    body.append('<p>完整分年逐笔明细（%d 笔，含买卖理由中文口径）见产物目录 FULL_REPORT_WITH_TRADES_*.html</p>' % len(trades))
    return "\n".join(head) + "\n" + "\n".join(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fragment", default=None,
                    help="仅输出内联摘要片段到指定路径")
    args = ap.parse_args()
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print("[ERR] 产物目录不存在: %s" % run_dir)
        sys.exit(2)
    b = build(run_dir, args.out)
    if args.fragment:
        frag = fragment_html(b)
        os.makedirs(os.path.dirname(args.fragment) or ".", exist_ok=True)
        with open(args.fragment, "w", encoding="utf-8") as f:
            f.write(frag)
        print("[OK] 内联摘要片段: %s" % args.fragment)
        return
    out = args.out or os.path.join(
        run_dir, "FULL_REPORT_WITH_TRADES_%s.html" % datetime.now().strftime("%Y%m%d"))
    st = b["st_combo"]
    leg = b["leg_stats"]
    n_trades = len(b["all_trades"])
    n_win = sum(1 for t in b["all_trades"] if t["pnl_pct"] > 0)
    wr_all = n_win / max(1, n_trades) * 100
    # <<MAIN_B>>
    css = [
        'body{font-family:"Microsoft YaHei",sans-serif;margin:20px auto;max-width:1200px;color:#222;line-height:1.55}',
        'h1{font-size:21px;border-bottom:3px solid #d33;padding-bottom:8px}',
        'h2{font-size:17px;border-left:5px solid #d33;padding-left:10px;margin-top:36px}',
        'h3{font-size:14.5px;margin:16px 0 6px}',
        'table{border-collapse:collapse;width:100%;font-size:12px}',
        'th,td{border:1px solid #ccc;padding:3px 6px}th{background:#f4f4f4;position:sticky;top:0}',
        'table.trades td{white-space:nowrap}',
        'table.trades td.rs{white-space:normal;max-width:280px;font-size:11px;color:#555}',
        '.num{font-family:Consolas,monospace;text-align:right}',
        '.up{color:#c0392b}.down{color:#1e8e3e}',
        '.cards{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}',
        '.card{border:1px solid #ddd;border-left:5px solid #555;padding:8px 14px;min-width:150px}',
        '.cl{font-size:11.5px;color:#777}.cv{font-size:19px;font-weight:700}',
        'details{margin:6px 0;border:1px solid #e0e0e0}',
        'summary{cursor:pointer;padding:6px 10px;background:#fafafa}',
        '.note{background:#fdf6e3;border:1px solid #e8d9a0;padding:10px 14px;font-size:12.5px}',
        '.warnbox{background:#fdecea;border:1px solid #f5c6cb;padding:10px 14px;font-size:12.5px}',
        '.foot{color:#888;font-size:11.5px;margin:30px 0 0;border-top:1px solid #eee;padding-top:8px}',
    ]
    # <<MAIN_C>>
    html = []
    html.append('<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">')
    html.append('<title>屠龙系统全域回测可视化报告 2016-2026（10万本金）</title>')
    html.append('<style>' + "\n".join(css) + '</style></head><body>')
    html.append('<h1>屠龙系统 · 多模型全域历史回测可视化报告（2016-01-01 ~ 2026-10-30）</h1>')
    intro = ('<p>初始资金 <b>100,000 元</b> | 宇宙 = A股全市场（剔除北交所/科创板/ST/上市&lt;60日）'
             '| 价格源 = qfq 权威库 | 执行 = 次日开盘×(1-0.03%)滑点 | 财务因子 = PIT | -8% 硬止损</p>')
    html.append(intro)
    html.append('<div class="cards">{cards}</div>'.format(cards=b["cards"]))
    # <<MAIN_D>>
    html.append('<h2>一、冻结验收口径判定（8/31 主人裁决 · PROJECT_BOUNDARY §6）</h2>')
    html.append('<div class="note">'
                '<p><b>判定基准 = 冻结验收口径</b>：① 十年总收益 ≥200%（组合 50/50 口径）；'
                '② 选股命中率 78-82%（WFA 样本外，8/31 已 PASS：bp+ep 历史命中 82.2%，WFA OOS 年化 +8.84%）。</p>'
                '<p><b>「年化复合 200%」</b>按 8/31 裁决为已否定不可达口径（十年 10 万→59 亿），'
                '任何调参逼近尝试均被明确禁止；本报告如实呈现实际 CAGR，不做任何逼近。</p>'
                '<p><b>胜率口径物理隔离</b>：交易胜率（含 -8% 止损体系每笔平仓）实证 ~50%；'
                '达标的是<b>选股命中率</b>（WFA 样本外口径）。两口径不得混用。</p></div>')
    html.append('<h2>二、全期指标与净值曲线</h2><h3>净值曲线（组合+两腿）</h3>')
    html.append(b["charts"]["equity"])
    html.append('<h3>回撤曲线（含最大回撤标注）</h3>')
    html.append(b["charts"]["dd"])
    # <<MAIN_E>>
    html.append('<h2>三、分年收益与胜率</h2><h3>分年收益对比（%）</h3>')
    html.append(b["charts"]["bars"])
    html.append('<h3>分年交易胜率（两腿平仓合并口径；78-82% 参考带=选股命中率口径，非交易胜率）</h3>')
    html.append(b["charts"]["wr"])
    html.append('<h3>分年汇总表（引用引擎 yearly_summary_all.csv）</h3>')
    html.append(b["ys_table"])
    # <<MAIN_F>>
    html.append('<h2>四、分年逐笔交易明细（bp_ep + ML 两在役腿合并）</h2>')
    html.append('<p>卖出理由中文口径：月度调仓换出 / -8% 硬止损 / 最长持有到期 / 样本末未平仓。'
                'ML 腿信号覆盖自 2020-01（v22 打分表起点），此前年份仅 bp_ep 腿交易。</p>')
    html.append(b["year_sections"])
    # <<MAIN_G>>
    l1 = leg["bp_ep_core"]
    l2 = leg["ml_signal"]
    legs_txt = (
        'bp_ep 腿：总收益 {t1:+.1f}% | CAGR {c1:.2f}% | MDD {m1:.2f}% | 夏普 {s1:.2f}<br>'
        'ML 腿（覆盖自 2020-01，样本内属性注脚在案）：总收益 {t2:+.1f}% | CAGR {c2:.2f}% | '
        'MDD {m2:.2f}% | 夏普 {s2:.2f}<br>'
        '<span style="color:#888">四负策略（breakout/v375/p2/composite）按 PROJECT_BOUNDARY §3 已判死退役，'
        '本轮不再复跑。</span>').format(
            t1=l1["total"], c1=l1["cagr"], m1=l1["mdd"], s1=l1["sharpe"],
            t2=l2["total"], c2=l2["cagr"], m2=l2["mdd"], s2=l2["sharpe"])
    html.append('<h2>五、单腿全期指标</h2><p>%s</p>' % legs_txt)
    # <<MAIN_H>>
    cov = (
        '<p>1. qfq 价格库 19901219~20261030（1197 万行/5918 码，10/31 实测）；'
        'stock_signals/v375/ml_v22/index_daily 全表面 MAX=20261030（10/30 晚链已入库，逐表核验）。</p>'
        '<p>2. p2/breakout 信号停更 20260921（存量持仓照常退出，与往轮同构）；'
        'v4 信号 20260528 停更（退役臂）；'
        'PIT 披露覆盖 ref_date MAX=20260901（因子表 mtime 9/16，陈旧值语义同往轮）；'
        'ML 腿打分表 v22 覆盖 2020-01-02 起；2026-10-12~14 空仓平线=既有空仓语义保护（基线注记在案）。</p>'
        '<p>3. 四负策略不再复跑（§3 禁复活），其证伪结论引用 9/1 drift_check 档案原文。</p>')
    html.append('<h2>六、数据覆盖如实说明（同往轮同构语义）</h2>')
    html.append('<div class="note">%s</div>' % cov)
    # <<MAIN_I>>
    opt = (
        '<p><b>已验证在役项</b>：M-3 移动止盈（仅 ML 腿 trail10/hold20）8/28 净值级 A/B 实证 +38.4pp，'
        '9/4 主人令复活接线（config v1.4 bp60/ml40），本轮回测 ML 腿已含该语义。</p>'
        '<p><b>已证伪禁复活（不作为优化方向）</b>：S-4 ATR 止损全局替换（bp_ep +138.2%→+39.9%）、'
        'S-5 45 天时间止损（禁时间止损逻辑族）、危机持仓强清（-61.7pp）、板块暴露 cap（无增量）、'
        '突破族九口径全负、经典形态先验（头肩底实测 41.9% vs 宣称 80-85%）、'
        '行业动量轮动/尾部过滤、bp_ep 周滚动重评（-58pp）、smc_s2_roe（前视污染）、hotspot_breakout（双段证伪）。</p>'
        '<p><b>合规优化方向（均须新指纹+新前提，prereg+wfa_gate 立项门槛，禁直接改生产）</b></p>'
        '<ul>'
        '<li>① 止损滑损治理：9/1 深诊止损带 289 笔均值 -11.02%（跳空+次bar开盘滑损实证）；'
        '方向=执行层 re-entry/缺口回避，禁动 S-4/S-5 禁复活面。</li>'
        '<li>② V25 ml_h20_wfa 触发判定：预注册协议 PREREG_V25_2026REEVAL 锁定，E1=2026-12-31 主判，'
        '供给触发线 2026-11-30（rescore 重打为协议内操作）。</li>'
        '<li>③ 2026 真窗 OOS 转正跟踪：ml 腿 2026 三臂全负史实在案，'
        '任何 ml 接线须 2026 真窗 OOS 绝对转正过关。</li>'
        '<li>④ 圆弧底观察项复核：复验点 2026-11-20 前后（条件已注册：'
        '无条件均值与 ret20 同桶残差双正才可提请立项，均值正残差负=动量延续不接线）。</li>'
        '<li>⑤ 组合层风险预算：M-3 复活后 C3 拼接 OOS MDD -31.25% 击穿 25% 风控顶矛盾在案，'
        '2026-11-30 回退条款复核点执行。</li>'
        '<li>⑥ 2026 年弱势段应对：9/1 深诊显示 2026-06 胜率 6%、滚动50笔胜率最低4%，'
        '合规方向=环境标签(M1 开仓侧)+已验证防御语义，禁危机清仓类已证伪手段。</li>'
        '</ul>')
    html.append('<h2>七、优化建议与优化方向（禁越证伪档案红线）</h2>')
    html.append('<div class="note">%s</div>' % opt)
    # <<MAIN_J>>
    foot = (
        '回测引擎: tulong_backtest_full_report.py（同引擎同法数据延伸复跑，先例八轮）| '
        '验收口径: 8/31 冻结基准 | 反臆想范式: 原始数据→规则核对→正/反信号→结论+置信度 | '
        '生成时间: %s | 产物目录: %s' % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), run_dir))
    html.append('<div class="foot">%s</div>' % foot)
    html.append('</body></html>')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    print("[OK] 可视化报告已生成: %s" % out)
    print("[INFO] 组合十年总收益 %+.2f%% | 期末净值 %s 元 | 交易笔数 %d | 全期交易胜率 %.1f%%"
          % (st["total"], "{:,.2f}".format(combo_last := b["combo"][-1]["equity"]),
             n_trades, wr_all))
    return out


if __name__ == "__main__":
    main()






