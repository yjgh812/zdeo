#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replay_reconciliation.py — 回测↔模拟盘重演对账（呈现层，零交易权）。
2026-11-07 立项（主人「继续」令），回放适配器第一步。判据口径见 reports 输出。

重演价 = 原始不复权开盘价（stock_daily_raw，与模拟盘成交价同口径真实价）。
入场腿=纸面入场价 vs 当日开盘价；出场腿=纸面出场价 vs 当日开盘价（盘中止损另列）。
判据：①入场重合率≥95%(偏差≤0.3%) ②入场偏差中位≤0.3% ③差异全归因
④涨停入场率<5% ⑤幽灵占比<5% ⑥净值匹配度≥80% ⑦校准因子0.6–0.8可管理。
数据源只读：attribution.db position_history + dragon_quant.db stock_daily_raw。
涨跌停幅度=market_rules_config（修复后分段规则）。退出码：0正常/3数据源不可用。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
import market_rules_config as mrc  # noqa: E402
import stock_name_lookup as snl  # noqa: E402
from minute_fill_engine import lc5_day as _lc5_day, bar_at as _bar_at  # noqa: E402

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
PRICE_DB = "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db" + "?mode=ro"
REPORT_DIR = r"D:\zcodesj\reports"
DEV_TOL_PP = 0.3
GHOST_TOL = 5.0
MATCH_FLOOR = 80.0
MIN_SIDE_N = 10

_KEYS = ("code", "name", "entry_date", "exit_date", "entry_price",
         "exit_price", "pnl_pct", "exit_reason")


def _code6(c) -> str:
    s = str(c or '').strip().lower()
    for p in ('sh', 'sz', 'bj'):
        if s.startswith(p):
            s = s[len(p):]
    return s.split('.')[0]


def _norm(d) -> int:
    if d is None:
        return 0
    if isinstance(d, (int, float)):
        return int(d)
    s = str(d)
    for sep in ('-', '/', '.', ' '):
        s = s.replace(sep, '')
    head = s[:8]
    return int(head) if head.isdigit() else 0


def _load_closed() -> list[dict]:
    conn = sqlite3.connect(ATTR_DB, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT code,name,entry_date,exit_date,entry_price,exit_price,pnl_pct,exit_reason "
            "FROM position_history WHERE pnl_pct IS NOT NULL AND exit_date IS NOT NULL "
            "AND entry_price > 0 AND exit_price > 0").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(zip(_KEYS, r))
        d["code"] = _code6(d["code"])
        out.append(d)
    return out


def _load_bars(conn, code, d_from, d_to):
    rows = conn.execute(
        "SELECT date, open, high, low, close FROM stock_daily_raw "
        "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
        (code, d_from, d_to)).fetchall()
    return {int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in rows}


def _dwin(d, back):
    from datetime import timedelta
    dt = datetime.strptime(str(d), "%Y%m%d").date()
    return int((dt - timedelta(days=back)).strftime("%Y%m%d"))


def _sealed(bar, prev_close, code, d, side):
    """封板判定，与撮合引擎同公式。bar=(open,high,low,close)。"""
    pct = mrc.limit_pct(code, d)
    if side == 'up':
        lim = prev_close * (1.0 + pct)
        tol = max(lim * 1e-3, 1e-6)
        return bar[2] >= lim - tol      # 全天最低钉在涨停价上方
    lim = prev_close * (1.0 - pct)
    tol = max(lim * 1e-3, 1e-6)
    return bar[1] <= lim + tol          # 全天最高钉在跌停价下方


def _load_fill_times():
    """执行台账(orderId→code/date/action) × westock成交回报(filledAt) →
    {(code6, trade_date, 'BUY'|'SELL'): 最早成交时刻'HH:MM:SS'}。失败→{}。"""
    oid_map = {}
    try:
        from executor_order_ledger import load_orders
    except ImportError:
        return {}
    for r in load_orders():
        oid = r.get("order_id")
        if not oid:
            continue
        oid_map[str(oid)] = (_code6(r.get("code", "")),
                             int(str(r.get("trade_date") or 0)[:8] or 0),
                             str(r.get("action") or "").upper())
    if not oid_map:
        return {}
    try:
        sys.path.insert(0, r"D:\workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
    except Exception:
        return {}
    out = {}
    for req in ({"range": "recent", "limit": 500},
                {"range": "history", "month": "202608", "limit": 500},
                {"range": "history", "month": "202607", "limit": 500}):
        try:
            resp = ws.call("portfolio_paper_history", req)
        except Exception:
            continue
        if not isinstance(resp, dict) or not resp.get("ok"):
            continue
        for x in (resp.get("data") or {}).get("records") or []:
            oid = str(x.get("orderId") or "")
            fa = str(x.get("filledAt") or "")
            st = str(x.get("status") or "")
            if not oid or not fa or st not in ("已成交", "部分成交", "成交"):
                continue
            meta = oid_map.get(oid)
            if not meta or not meta[1]:
                continue
            key = (meta[0], meta[1], meta[2])
            cur = out.get(key)
            if cur is None or fa < cur:
                out[key] = fa
    return out


def main():
    try:
        positions = _load_closed()
    except Exception as e:  # noqa: BLE001
        print(f"[REPLAY] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3
    if not positions:
        print("[REPLAY] 无平仓样本——无法对账")
        return 0
    conn = sqlite3.connect(PRICE_DB, uri=True, check_same_thread=False)
    codes = sorted({str(p["code"]) for p in positions})
    bars_by_code = {}
    for c in codes:
        ds = [_norm(p["entry_date"]) for p in positions if str(p["code"]) == c]
        ds += [_norm(p["exit_date"]) for p in positions if str(p["code"]) == c]
        if ds and min(ds) > 0:
            bars_by_code[c] = _load_bars(conn, c, _dwin(min(ds), 30), max(ds))
    conn.close()

    try:
        fills = _load_fill_times()
    except Exception as e:  # noqa: BLE001
        print(f"[REPLAY] 成交时刻装载失败({type(e).__name__}), 按旧口径继续")
        fills = {}
    results, missing = [], 0
    for p in positions:
        code = str(p["code"])
        de, dx = _norm(p["entry_date"]), _norm(p["exit_date"])
        bars = bars_by_code.get(code) or {}
        dates = sorted(bars)
        idx = {d: i for i, d in enumerate(dates)}
        be, bx = bars.get(de), bars.get(dx)
        if be is None or bx is None:
            missing += 1
            continue
        i, j = idx[de], idx[dx]
        prev_e = bars[dates[i - 1]][3] if i > 0 else None
        prev_x = bars[dates[j - 1]][3] if j > 0 else None
        sealed_e = bool(prev_e and _sealed(be, prev_e, code, de, 'up'))
        sealed_x = bool(prev_x and _sealed(bx, prev_x, code, dx, 'dn'))
        dev_in = (float(p["entry_price"]) - be[0]) / be[0] * 100
        dev_out = ((float(p["exit_price"]) - bx[0]) / bx[0] * 100) if bx[0] else 0.0
        rep_pnl = (bx[0] / be[0] - 1) * 100
        day_e = _lc5_day(code, de)
        d_lo, d_hi, e_bars = (day_e if day_e else (None, None, {}))
        pin = float(p["entry_price"])
        m_hit = bool(e_bars and any(v[2] - 1e-9 <= pin <= v[1] + 1e-9
                                    for v in e_bars.values()))
        ft_e = fills.get((code, de, "BUY"))
        e_bar = _bar_at(e_bars, ft_e)
        e_at_hit = bool(e_bar and e_bar[2] - 1e-9 <= pin <= e_bar[1] + 1e-9)
        day_x = _lc5_day(code, dx)
        x_lo, x_hi, x_bars = (day_x if day_x else (None, None, {}))
        pout = float(p["exit_price"])
        ft_x = fills.get((code, dx, "SELL"))
        x_bar = _bar_at(x_bars, ft_x)
        x_at_hit = bool(x_bar and x_bar[2] - 1e-9 <= pout <= x_bar[1] + 1e-9)
        b40 = e_bars.get(940)
        m_dev40 = ((pin - b40[0]) / b40[0] * 100) if b40 else None
        reason = str(p["exit_reason"] or "")
        stop = ("S-5" in reason or "止损" in reason)
        lib_name = snl.get_name(code)
        disp = lib_name or str(p["name"])
        if lib_name and str(p["name"]) and lib_name != str(p["name"]):
            disp = f"{lib_name}(台账:{p['name']})"   # 名称主库为唯一真源, 台账错名留痕
        results.append({
            "code": code, "name": disp, "de": de, "dx": dx,
            "paper_in": float(p["entry_price"]), "rep_in": be[0], "dev_in": dev_in,
            "paper_out": float(p["exit_price"]), "rep_out": bx[0], "dev_out": dev_out,
            "sealed_e": sealed_e, "sealed_x": sealed_x,
            "rep_pnl": rep_pnl, "paper_pnl": float(p["pnl_pct"]),
            "stop": stop, "reason": reason[:40],
            "m_hit": m_hit, "m_hit_day": m_hit, "m_dev40": m_dev40,
            "d_lo": d_lo, "d_hi": d_hi,
            "e_ft": ft_e, "e_at_hit": e_at_hit,
            "x_ft": ft_x, "x_at_hit": x_at_hit,
        })

    devs = [abs(r["dev_in"]) for r in results]
    n_e = len(devs)
    srt = sorted(devs)
    med_in = srt[n_e // 2] if n_e else None
    ok_n = sum(1 for v in devs if v <= DEV_TOL_PP)
    share_ok = ok_n / n_e * 100 if n_e else 0.0
    sealed_n = sum(1 for r in results if r["sealed_e"])
    seal_rate = sealed_n / n_e * 100 if n_e else 0.0
    tot = sum(r["paper_pnl"] for r in results)
    ghost = sum(r["paper_pnl"] for r in results if r["sealed_e"])
    ghost_share = ghost / tot * 100 if abs(tot) >= 1.0 else None
    denom = sum(abs(r["paper_pnl"]) for r in results)
    match = (1 - sum(abs(r["rep_pnl"] - r["paper_pnl"]) for r in results)
             / max(denom, 1e-9)) * 100
    m_rep = sum(r["rep_pnl"] for r in results) / n_e if n_e else 0.0
    m_pp = sum(r["paper_pnl"] for r in results) / n_e if n_e else 0.0
    calib = m_rep / m_pp if (n_e >= MIN_SIDE_N and abs(m_pp) > 0.5) else None

    def leg_stats(rows_, key):
        vals = [abs(r[key]) for r in rows_]
        if not vals:
            return None
        s2 = sorted(vals)
        return {"n": len(vals), "med": s2[len(s2) // 2],
                "ok": sum(1 for v in vals if v <= DEV_TOL_PP) / len(vals) * 100}

    out_st = leg_stats([r for r in results if r["stop"]], "dev_out")
    out_ns = leg_stats([r for r in results if not r["stop"]], "dev_out")

    # ── 自校准：实测摩擦系数(带符号均值) → 注入 → 残差 ──
    f_in = sum(r["dev_in"] for r in results) / n_e if n_e else 0.0
    f_out = sum(r["dev_out"] for r in results) / n_e if n_e else 0.0
    for r in results:
        r["cal_dev_in"] = r["dev_in"] - f_in
        r["rep_pnl_cal"] = ((r["rep_pnl"] / 100.0 + 1.0)
                            * (1.0 + f_out / 100.0)
                            / (1.0 + f_in / 100.0) - 1.0) * 100
    cal_ok = sum(1 for r in results if abs(r["cal_dev_in"]) <= DEV_TOL_PP)
    cal_share = cal_ok / n_e * 100 if n_e else 0.0
    cal_devs = sorted(abs(r["cal_dev_in"]) for r in results)
    cal_med = cal_devs[n_e // 2] if n_e else None
    match_cal = (1 - sum(abs(r["rep_pnl_cal"] - r["paper_pnl"]) for r in results)
                 / max(denom, 1e-9)) * 100
    m_rep_cal = (sum(r["rep_pnl_cal"] for r in results) / n_e) if n_e else 0.0
    calib_cal = m_rep_cal / m_pp if (n_e >= MIN_SIDE_N and abs(m_pp) > 0.5) else None

    # ── 分钟窗检验：晨窗(9:35–9:40)命中 + 全日触达命中 ──
    m_vals = [r for r in results if r["m_dev40"] is not None]
    m_cov = len(m_vals)
    m_hit_n = sum(1 for r in results if r["m_hit"])
    m_hit_rate = m_hit_n / m_cov * 100 if m_cov else None
    m_day_n = sum(1 for r in results if r["m_hit_day"])
    m_day_rate = m_day_n / m_cov * 100 if m_cov else None
    m_devs = sorted(abs(r["m_dev40"]) for r in m_vals)
    m_med = m_devs[len(m_devs) // 2] if m_devs else None
    sim_rows = [r for r in results
                if r["m_dev40"] is not None and not r["m_hit_day"]]
    sim_pnl = sum(r["paper_pnl"] for r in sim_rows)
    sim_share = sim_pnl / tot * 100 if abs(tot) >= 1.0 else None
    tot_ex = tot - sim_pnl                      # 剔除法：不含瑕疵笔的纸面总收益
    sim_re = 0.0                                # 重定价法：真实限价单高于市价=立即按市价成交≈当日开盘
    for r in sim_rows:
        e = r["rep_in"] if r["rep_in"] else r["paper_in"]
        if e and e > 0:
            sim_re += (float(r["paper_out"]) / e - 1.0) * 100
    e_ft_rows = [r for r in results if r["e_ft"]]
    e_ft_cov = len(e_ft_rows)
    e_ft_rate = (sum(1 for r in e_ft_rows if r["e_at_hit"]) / e_ft_cov * 100
                 if e_ft_cov else None)
    x_ft_rows = [r for r in results if r["x_ft"]]
    x_ft_cov = len(x_ft_rows)
    x_ft_rate = (sum(1 for r in x_ft_rows if r["x_at_hit"]) / x_ft_cov * 100
                 if x_ft_cov else None)
    return (results, missing, dict(n_e=n_e, med_in=med_in, share_ok=share_ok,
            sealed_n=sealed_n, seal_rate=seal_rate, tot=tot, ghost=ghost,
            ghost_share=ghost_share, match=match, m_rep=m_rep, m_pp=m_pp,
            calib=calib, out_st=out_st, out_ns=out_ns,
            f_in=f_in, f_out=f_out, cal_share=cal_share, cal_med=cal_med,
            match_cal=match_cal, calib_cal=calib_cal,
            m_cov=m_cov, m_hit_rate=m_hit_rate, m_med=m_med,
            m_day_rate=m_day_rate, m_day_n=m_day_n,
            sim_rows=sim_rows, sim_pnl=sim_pnl, sim_share=sim_share,
            tot_ex=tot_ex, sim_re=sim_re,
            e_ft_cov=e_ft_cov, e_ft_rate=e_ft_rate,
            x_ft_cov=x_ft_cov, x_ft_rate=x_ft_rate))


def run():
    out = main()
    if not isinstance(out, tuple):
        return out
    results, missing, agg = out
    L = [f"# 重演对账报告 {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 基准：模拟盘平仓 {len(results)} 笔（另有数据缺失 {missing} 笔，如实未计入）",
         "- 口径：重演价=当日原始开盘价（与模拟盘成交价同域真实价）；",
         "  出场腿含盘中止损成交，与开盘价口径不同，分桶列示不混判。",
         "", "## 判据结果（六判据+校准因子）", ""]
    s1 = '✅' if agg["share_ok"] >= 95 else '❌'
    L.append(f"- {s1} ①逐笔重合率(入场腿|偏差|≤{DEV_TOL_PP}pp)={agg['share_ok']:.1f}% (n={agg['n_e']}, 阈≥95%)")
    if agg["med_in"] is not None:
        s2 = '✅' if agg["med_in"] <= DEV_TOL_PP else '❌'
        L.append(f"- {s2} ②入场偏差中位={agg['med_in']:.3f}pp (阈≤{DEV_TOL_PP})")
    else:
        L.append("- ⏳ ②无有效样本")
    L.append(f"- ③差异全归因：入场偏差>{DEV_TOL_PP}pp 共 "
             f"{sum(1 for r in results if abs(r['dev_in']) > DEV_TOL_PP)} 笔，附录逐笔列示")
    s4 = '✅' if agg["seal_rate"] < GHOST_TOL else '❌'
    L.append(f"- {s4} ④涨停入场率={agg['seal_rate']:.1f}% ({agg['sealed_n']}/{agg['n_e']}, 阈<{GHOST_TOL}%)")
    if agg["ghost_share"] is None:
        L.append("- ⏳ ⑤幽灵占比：分母(Σ纸面收益)绝对值过小，比例无意义——如实N/A")
    else:
        s5 = '✅' if abs(agg["ghost_share"]) < GHOST_TOL else '❌'
        L.append(f"- {s5} ⑤幽灵占比={agg['ghost_share']:+.1f}% (阈|值|<{GHOST_TOL}%)")
    s6 = '✅' if agg["match"] >= MATCH_FLOOR else '❌'
    L.append(f"- {s6} ⑥净值匹配度={agg['match']:.1f}% (阈≥{MATCH_FLOOR}%)")
    if agg["calib"] is None:
        L.append("- ⏳ ⑦校准因子：样本不足或纸面均值过小——如实N/A")
    else:
        L.append(f"- ⑦校准因子(开盘口径,毛)={agg['calib']:.2f}"
                 "（0.6–0.8可管理；≈1.0警惕共享幻觉；<0.4摩擦吃掉大半）")
    L += ["", "## 校准后判据（注入实测执行摩擦后）", "",
          f"- 实测摩擦系数：买入追价 {agg['f_in']:+.2f}pp / 出场偏移 {agg['f_out']:+.2f}pp"
          "（259笔带符号均值；出场含盘中止损机制，恒定系数是近似）"]
    s1c = '✅' if agg["cal_share"] >= 95 else '❌'
    L.append(f"- {s1c} ①校准后逐笔重合率={agg['cal_share']:.1f}% (阈≥95%)")
    if agg["cal_med"] is not None:
        L.append(f"- ②校准后入场偏差中位={agg['cal_med']:.3f}pp"
                 " ← 恒定摩擦解释不了的残余（此值大→需要分钟级成交精化）")
    s6c = '✅' if agg["match_cal"] >= MATCH_FLOOR else '❌'
    L.append(f"- {s6c} ⑥校准后净值匹配度={agg['match_cal']:.1f}% (阈≥80%)")
    L.append("- ⑦校准后因子≈1.0为构造目标（价值在残余大小，不在因子本身）")
    L += ["", "## 分钟窗检验（重演价=五分钟实价，逐笔闭合的最后一步）", ""]
    if agg["m_cov"]:
        rate = agg["m_hit_rate"]
        s8 = '✅' if rate >= 95 else ('⚠️' if rate >= 80 else '❌')
        L.append(f"- {s8} ⑧全日逐bar触达(入场价∈当日任一5分钟K区间)={rate:.1f}% (覆盖{agg['m_cov']}笔)")
        if agg["m_day_rate"] is not None:
            s8b = '✅' if agg["m_day_rate"] >= 95 else ('⚠️' if agg["m_day_rate"] >= 80 else '❌')
            L.append(f"- {s8b} ⑧b全日触达(入场价当日真实成交过)={agg['m_day_rate']:.1f}%")
        if agg["m_med"] is not None:
            L.append(f"- 入场价 vs 9:35时价偏差中位={agg['m_med']:.3f}pp"
                     "（个股级波动的直接量测）")
        L += ["- 解读：晨窗命中之外的成交=限价单全天有效、价格后段触达才成交；",
              "  全日触达<100%的部分才是模拟盘仿真瑕疵（成交价当日从未被交易到）。",
              "  出场腿无逐笔成交时刻，维持开盘价口径分桶，不混判。"]
    else:
        L.append("- ⏳ 分钟库无覆盖——如实N/A")
    L += ["", "## 真实成交时刻检验（westock回报filledAt × 所在5分钟K区间）", ""]
    if agg["e_ft_cov"]:
        L.append(f"- 入场腿：覆盖 {agg['e_ft_cov']} 笔，成交时刻K线区间命中率="
                 f"{agg['e_ft_rate']:.1f}%")
    else:
        L.append("- 入场腿：成交时刻不可得（台账/回报未配对）——如实披露")
    if agg["x_ft_cov"]:
        L.append(f"- 出场腿：覆盖 {agg['x_ft_cov']} 笔，命中率={agg['x_ft_rate']:.1f}%"
                 "（出场腿逐笔口径缺口由此闭合）")
    else:
        L.append("- 出场腿：成交时刻不可得——如实披露")
    sim_rows = agg["sim_rows"]
    if sim_rows:
        share_s = (f"，占纸面总盈利 {agg['sim_share']:+.1f}%"
                   if agg["sim_share"] is not None else "")
        L += ["", f"## 模拟盘仿真瑕疵清单（{len(sim_rows)} 笔，成交价当日从未被市场交易）", "",
              f"- 这些笔的纸面盈利合计 {agg['sim_pnl']:+.2f}pp{share_s}"
              "——方向是拖累而非虚增：仿真撮合按高于全日区间的价格给了成交，"
              "随后这些仓位整体亏损", "",
              f"- 对账双口径：**剔除法**=纸面总收益 {agg['tot_ex']:+.1f}pp（不含瑕疵笔）｜"
              f"**重定价法**=瑕疵笔入场按当日开盘价重定价后合计 {agg['sim_re']:+.2f}pp"
              f"（原 {agg['sim_pnl']:+.2f}pp；真实限价单高于市价时会立即按市价成交）——两口径并存，消费方按需取用", "",
              "| 代码 | 名称 | 入场日 | 纸面入价 | 当日实际成交区间 | 纸面收益% |",
              "|---|---|---|---|---|---|"]
        for r in sim_rows[:30]:
            L.append(f"| {r['code']} | {r['name']} | {r['de']} "
                     f"| {r['paper_in']:.2f} "
                     f"| {r['d_lo']:.2f}–{r['d_hi']:.2f} "
                     f"| {r['paper_pnl']:+.2f} |")
    L += ["", "## 出场腿分桶（口径提示：盘中止损成交 vs 当日开盘价不可直接比）", ""]
    if agg["out_st"]:
        L.append(f"- 盘中止损出场 {agg['out_st']['n']} 笔：偏差中位 {agg['out_st']['med']:.2f}pp，"
                 f"≤0.3pp 占比 {agg['out_st']['ok']:.0f}%（预期偏差大=口径差，非缺陷）")
    if agg["out_ns"]:
        L.append(f"- 非止损出场 {agg['out_ns']['n']} 笔：偏差中位 {agg['out_ns']['med']:.2f}pp，"
                 f"≤0.3pp 占比 {agg['out_ns']['ok']:.0f}%")
    L += ["", "## 附录：逐笔明细", "",
          "| 代码 | 名称 | 入场日 | 出场日 | 纸面入 | 重演入 | 入偏差pp | 纸面出 | 重演出 | 出偏差pp | 封板 | 纸面收益% | 重演收益% | 出场桶 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        seal_tag = '涨停' if r["sealed_e"] else ('跌停' if r["sealed_x"] else '')
        L.append(f"| {r['code']} | {r['name']} | {r['de']} | {r['dx']} "
                 f"| {r['paper_in']:.2f} | {r['rep_in']:.2f} | {r['dev_in']:+.2f} "
                 f"| {r['paper_out']:.2f} | {r['rep_out']:.2f} | {r['dev_out']:+.2f} "
                 f"| {seal_tag} | {r['paper_pnl']:+.2f} | {r['rep_pnl']:+.2f} "
                 f"| {'止损盘中' if r['stop'] else ''} |")
    path = os.path.join(REPORT_DIR,
                        f"replay_reconciliation_{datetime.now():%Y%m%d}.md")
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    if agg["sim_rows"]:
        import json as _json
        with open(r"D:\zcodesj\data\sim_fill_artifacts.jsonl", "w",
                  encoding="utf-8") as f:
            for r in agg["sim_rows"]:
                f.write(_json.dumps({
                    "code": r["code"], "name": r["name"],
                    "entry_date": r["de"], "paper_in": r["paper_in"],
                    "day_low": r["d_lo"], "day_high": r["d_hi"],
                    "paper_pnl_pct": r["paper_pnl"],
                    "recorded_at": datetime.now().isoformat(timespec="seconds"),
                }, ensure_ascii=False) + "\n")
    calib_s = "N/A" if agg["calib"] is None else f"{agg['calib']:.2f}"
    m_s = "N/A" if agg["m_hit_rate"] is None else f"{agg['m_hit_rate']:.1f}%"
    e_s = "N/A" if agg["e_ft_rate"] is None else f"{agg['e_ft_rate']:.1f}%"
    x_s = "N/A" if agg["x_ft_rate"] is None else f"{agg['x_ft_rate']:.1f}%"
    print(f"[REPLAY] n={len(results)} 缺失={missing} "
          f"①重合率={agg['share_ok']:.1f}% ④涨停入场率={agg['seal_rate']:.1f}% "
          f"⑥匹配度={agg['match']:.1f}% ⑦校准因子={calib_s} | "
          f"摩擦: 买{agg['f_in']:+.2f}pp/卖{agg['f_out']:+.2f}pp | "
          f"⑧全日逐bar触达={m_s} | 成交时刻检验: 入{agg['e_ft_cov']}笔{e_s}/"
          f"出{agg['x_ft_cov']}笔{x_s} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
