#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_dossier.py — 个股全息档案工具(多周期趋势 + 产业链概念)
=============================================================
定位(2026-10-03 主人令"每一只股票的涨跌走势/趋势/产业链概念了如指掌"):
  任何个股分析与汇报前, 一条命令拉出该股的多周期趋势结构与产业链概念背景。
  档案只呈现【算术事实 + 系统既有信号引用】, 不输出买卖建议(反臆想铁律;
  交易判断仍归闸门层/主人)。

数据源(全部只读):
  1. qfq库  D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db
            表 stock_daily_meta_qfq(code,date,open,high,low,close,volume,amount,change_pct)
            前复权; 最新bar收盘=不复权实价(呈报口径一致)
  2. fund库 D:/workbuddyspace/databases/fundamental_data.db
            industry_map_ths / industry_map / concept_map / daily_basic
            / feature_industry_momentum(个股→行业动量,按ts_code直连)
  3. data/stock_chain_cache.json         产业链归属在线快照缓存(westock MCP)
  4. data/swing_phase_holdings_latest.json 持仓波段相位(系统既有产物, 描述性零交易权)
  5. data/industry_volume_state_latest.json 行业量能状态(31申万一级)
  6. data/holdings_codes_latest.json     持仓清单快照(真值仍以westock MCP实时为准)

用法:
  python stock_dossier.py --code 003006              # 单只(可多个逗号分隔)
  python stock_dossier.py --holdings                 # 全持仓 → analysis/持仓档案/

口径说明:
  - 月线36月位置: 近36个月末收盘, (今-min)/(max-min); 底区<33%/中位/顶区>66%
    (三等分近似口径; 10/2研究原始分界以研究报告为准, 此处仅定位参考)
  - 量能: vol20/vol60; <0.8缩量 0.8-1.2常量 1.2-2放量 >2爆量
  - 波动率异常/乖离等仅报数值, 阈值性结论由系统信号层给
"""
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\zcodesj")
sys.path.insert(0, str(ROOT))
from stock_name_lookup import get_name  # noqa: E402 名称唯一合法接口(铁律)

QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FUND_DB = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
CHAIN_CACHE = ROOT / "data" / "stock_chain_cache.json"
GRAPH_CACHE = ROOT / "data" / "stock_chain_graph_cache.json"
SWING_PHASE = ROOT / "data" / "swing_phase_holdings_latest.json"
IND_VOL = ROOT / "data" / "industry_volume_state_latest.json"
HOLDINGS = ROOT / "data" / "holdings_codes_latest.json"
WATCHLIST = ROOT / "data" / "watchlist_codes.json"
OUT_DIR = ROOT / "analysis" / "持仓档案"


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def load_kline(code, conn=None):
    """qfq日线, 升序. 返回 [(date, close, volume), ...]; conn可复用(pool批量)"""
    own = conn is None
    c = conn or sqlite3.connect(QFQ_DB, uri=True)
    rows = c.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
        (code,),
    ).fetchall()
    if own:
        c.close()
    return rows


def ma(series, n, idx):
    if idx + 1 < n:
        return None
    seg = series[idx - n + 1 : idx + 1]
    if any(v is None for v in seg):
        return None
    return sum(seg) / n


def monthly_closes(rows, months=36):
    """每月最后交易日收盘"""
    out = {}
    for d, c, _v in rows:
        out[d[:6]] = c  # dict 后写覆盖 → 自动保留每月最后一天
    vals = [out[k] for k in sorted(out)][-months:]
    return vals


def vol_label(r):
    if r is None:
        return "N/A"
    if r < 0.8:
        return "缩量"
    if r < 1.2:
        return "常量"
    if r < 2.0:
        return "放量"
    return "爆量"


def compute_trend(rows):
    closes = [r[1] for r in rows]
    vols = [r[2] for r in rows]
    n = len(rows)
    if n < 30:
        return None
    last = closes[-1]
    mas = {k: ma(closes, k, n - 1) for k in (5, 10, 20, 60, 120, 250)}
    # 排列判定(算术事实)
    if all(mas.get(k) for k in (5, 10, 20, 60)):
        if mas[5] > mas[10] > mas[20] > mas[60]:
            ma_state = "多头排列(MA5>10>20>60)"
        elif mas[5] < mas[10] < mas[20] < mas[60]:
            ma_state = "空头排列(MA5<10<20<60)"
        else:
            ma_state = "均线纠缠"
    else:
        ma_state = "上市未满/数据不足"
    above = "站上" if last > (mas[20] or math.inf) else "跌破"
    # MA20方向(5日前对比)
    ma20_dir = "N/A"
    if mas[20] and n > 25:
        prev = ma(closes, 20, n - 6)
        if prev:
            d = (mas[20] / prev - 1) * 100
            ma20_dir = f"上行(+{d:.1f}%)" if d > 0.1 else (f"下行({d:.1f}%)" if d < -0.1 else "走平")
    # 年内位置(252交易日≈一年)
    seg = closes[-252:] if n >= 252 else closes
    hi, lo = max(seg), min(seg)
    pos252 = (last - lo) / (hi - lo) * 100 if hi > lo else float("nan")
    off_high = (last / hi - 1) * 100
    # 月线36月位置(口径对齐 reports/monthly_top_z2s_20261002.md:
    #   顶区=pos≥85; 底区=距36月高≥35% 或 pos≤30; 其余中位)
    m = monthly_closes(rows, 36)
    m_hi, m_lo = max(m), min(m)
    pos36 = (last - m_lo) / (m_hi - m_lo) * 100 if m_hi > m_lo else float("nan")
    off36h = (last / m_hi - 1) * 100
    if pos36 >= 85:
        zone36 = f"月顶区(pos={pos36:.0f}%)"
    elif off36h <= -35 or pos36 <= 30:
        zone36 = f"月底区(距36月高{off36h:.0f}%/pos={pos36:.0f}%)"
    else:
        zone36 = f"月中位(pos={pos36:.0f}%)"
    # 量能
    v20 = sum(vols[-20:]) / min(20, len(vols))
    v60 = sum(vols[-60:]) / min(60, len(vols)) if n >= 60 else None
    vr = v20 / v60 if v60 else None
    # 涨跌幅
    ret20 = (last / closes[-21] - 1) * 100 if n > 21 else float("nan")
    ret60 = (last / closes[-61] - 1) * 100 if n > 61 else float("nan")
    new60 = "60日新高" if last >= max(closes[-60:]) else ""
    # 乖离
    bias20 = (last / mas[20] - 1) * 100 if mas[20] else float("nan")
    return {
        "last": last, "date": rows[-1][0], "n": n,
        "ma_state": ma_state, "above20": above, "ma20_dir": ma20_dir,
        "mas": mas, "pos252": pos252, "off_high": off_high,
        "pos36": pos36, "zone36": zone36,
        "vr": vr, "vr_label": vol_label(vr), "ret20": ret20, "ret60": ret60,
        "new60": new60, "bias20": bias20,
    }


def get_fund_info(code):
    c = sqlite3.connect(FUND_DB, uri=True)
    ts = f"{code}.{'SZ' if code.startswith(('0', '3')) else 'SH'}"
    out = {}
    r = c.execute(
        "SELECT industry FROM industry_map_ths WHERE code=?", (code,)
    ).fetchone()
    out["ths_industry"] = r[0] if r else None
    l1 = [x[0] for x in c.execute(
        "SELECT industry_name FROM industry_map WHERE ts_code=? AND level='L1'", (ts,)
    ).fetchall()]
    out["sw_l1"] = l1  # 本库存在多行脏数据, 全部呈现由交叉验证暴露
    r = c.execute(
        "SELECT trade_date, pe_ttm, pb, turnover_rate, total_mv FROM daily_basic "
        "WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts,)
    ).fetchone()
    out["basic"] = r
    r = c.execute(
        "SELECT industry, industry_ret20 FROM feature_industry_momentum "
        "WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts,)
    ).fetchone()
    out["momentum_industry"] = r
    r = c.execute(
        "SELECT concept FROM concept_map WHERE code=? LIMIT 12", (code,)
    ).fetchall()
    out["concepts"] = [x[0] for x in r]
    c.close()
    return out


def fmt(x, suffix=""):
    if x is None:
        return "N/A"
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return "N/A"
    return f"{x:.2f}{suffix}"


def build_dossier(code, holding=None):
    name = get_name(code) or (holding or {}).get("name") or "未知名称"
    h = holding or {}
    rows = load_kline(code)
    t = compute_trend(rows) if rows else None
    fund = get_fund_info(code)
    chain = _load_json(CHAIN_CACHE).get("stocks", {}).get(f"sz{code}" if code.startswith(("0", "3")) else f"sh{code}")
    lines = [f"### {code} {name}"]
    if h:
        if h.get("cost"):
            lines.append(
                f"[持仓 {h['qty']}股 成本{h['cost']} 浮盈{h['pnl_pct']:+.1f}%] "
                f"S-5止损参考线 {h['cost'] * 0.92:.2f}(-8%铁律, 距现价{h['price'] / (h['cost'] * 0.92) * 100 - 100:+.1f}%)"
            )
        else:
            lines.append(f"[持仓 {h['qty']}股 成本N/A(零成本记录) 浮盈{h.get('pnl_pct', 0):+.1f}%] S-5参考线 N/A")
    if t:
        mas = t["mas"]
        ma_str = " ".join(
            f"MA{k}={fmt(v)}" for k, v in mas.items() if v
        )
        lines.append(
            f"- **趋势结构**({t['date']}, {t['n']}根): {t['ma_state']}, "
            f"价格{t['above20']}MA20(MA20{t['ma20_dir']}) | {ma_str}"
        )
        lines.append(
            f"- **位置** | 年内位置 {t['pos252']:.0f}% (距年高{t['off_high']:+.1f}%) | "
            f"月线36月: {t['zone36']}, {t['pos36']:.0f}% | 乖离MA20 {t['bias20']:+.1f}% {t['new60']}"
        )
        lines.append(
            f"- **量能与动量** | 量能{t['vr_label']}(vol20/vol60={fmt(t['vr'], 'x')}) | "
            f"近20日{t['ret20']:+.1f}% 近60日{t['ret60']:+.1f}%"
        )
    else:
        lines.append("- **趋势结构**: qfq无K线数据, 缺少原始数据无法得出确定性判断")
    # 波段相位(系统既有产物, 描述性)
    sp = _load_json(SWING_PHASE)
    rec = next((r for r in sp.get("records", []) if r.get("code") == code), None)
    if rec:
        lines.append(
            f"- **波段相位(系统,描述性零交易权)** | {rec.get('direction')}段{rec.get('seg_days')}天"
            f"{rec.get('seg_chg', 0):+.1f}% vr={rec.get('vr_now')} → {rec.get('phase')}"
        )
    # 产业链概念
    parts = []
    if fund.get("momentum_industry"):
        parts.append(f"行业动量库: {fund['momentum_industry'][0]}(ret20={fmt(fund['momentum_industry'][1], '%')})")
    if fund.get("ths_industry"):
        parts.append(f"THS行业: {fund['ths_industry']}")
    if fund.get("sw_l1"):
        parts.append(f"SW L1: {'/'.join(fund['sw_l1'])}")
    lines.append("- **产业链/行业** | " + ("; ".join(parts) if parts else "本地映射库无记录"))
    if chain:
        iv = _load_json(IND_VOL).get("industries", [])
        ind_name = (fund.get("momentum_industry") or (None,))[0]
        ivr = next((x for x in iv if x.get("industry") == ind_name), None)
        iv_str = f" | 行业量能volr20={fmt(ivr['volr20'], 'x')}(hot5={ivr['hot5']})" if ivr else ""
        lines.append(
            f"- **产业链节点(在线快照{chain.get('fetched_at', '')})** | 节点: {'、'.join(chain['nodes'][:8]) or '(上游无数据,如实记录)'} | "
            f"主题: {'、'.join(chain['themes'][:8])}{iv_str}"
        )
        # 产业链图谱(上→中→下游结构, 本股位置)
        g = _load_json(GRAPH_CACHE)
        gthemes = g.get("themes", {})
        stock_nodes = g.get("stock_nodes", {}).get(code, {})
        for thm in [x for x in chain["themes"] if x in gthemes][:2]:
            th = gthemes[thm]
            pos_str = ""
            if thm in stock_nodes and stock_nodes[thm]:
                pos_str = f" 〖本股位置: {'; '.join(stock_nodes[thm])}〗"
            lines.append(
                f"- **图谱·{thm}**(上→中→下) | 上游:{'、'.join(th['上游'][:4])} → 中游:{'、'.join(th['中游'][:4])} "
                f"→ 下游:{'、'.join(th['下游'][:4])}{pos_str}"
            )
    else:
        lines.append("- **产业链节点**: 缓存无记录(本地概念/行业层已在上方, 节点层按需抓取), 不做主观推演")
    if fund.get("concepts"):
        lines.append("- **概念标签(本地库)** | " + "、".join(fund["concepts"]))
    b = fund.get("basic")
    if b:
        lines.append(
            f"- **估值({b[0]})** | PE(TTM)={fmt(b[1])} PB={fmt(b[2])} 换手={fmt(b[3], '%')} 总市值={fmt(b[4] / 10000, '亿')}"
        )
    lines.append("")
    summary = {
        "code": code, "name": name,
        "pnl": f"{h['pnl_pct']:+.1f}%" if h else "",
        "s5dist": (f"{h['price'] / (h['cost'] * 0.92) * 100 - 100:+.1f}%" if h and h.get("cost") else ""),
        "ma_short": (t["ma_state"].split("(")[0] if t else "N/A"),
        "pos252": (f"{t['pos252']:.0f}%" if t else "N/A"),
        "zone_short": (t["zone36"].split("(")[0] if t else "N/A"),
        "vr": (t["vr_label"] if t else "N/A"),
        "ret20": (f"{t['ret20']:+.1f}%" if t else "N/A"),
        "phase": (rec.get("phase", "")[:14] if rec else ""),
        "themes": ("、".join(chain["themes"][:3]) if chain else ""),
    }
    return "\n".join(lines), summary


def render_summary_table(summaries, ind_col=False):
    """速览表: 一股一行, 晨会可扫"""
    has_rs = any("rs_rank" in s for s in summaries)
    head = ["代码", "名称"] + (["行业/类别"] if ind_col else []) + \
        ["浮盈", "趋势", "年内pos", "月线区", "量能", "ret20", "波段相位", "S-5距"] + \
        (["RS池内"] if has_rs else [])
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in summaries:
        cells = [s["code"], s["name"]] + \
            ([s.get("industry", "")] if ind_col else []) + \
            [s.get("pnl", ""), s["ma_short"], s["pos252"], s["zone_short"],
             s["vr"], s["ret20"], s.get("phase", ""), s.get("s5dist", "")] + \
            ([s.get("rs_rank", "")] if has_rs else [])
        rows.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(rows)


def pool_mode(out_dir):
    """主线池(universe_zone_scan 最新CSV, 主线标=是)趋势速览 — 选股参考层"""
    scans = sorted((ROOT / "data").glob("universe_zone_scan_*.csv"))
    if not scans:
        print("无 universe_zone_scan_* 产物, --pool 不可用")
        sys.exit(2)
    csv_path = scans[-1]
    with open(csv_path, encoding="utf-8-sig") as f:
        zrows = list(csv.DictReader(f))
    mcol = next(c for c in zrows[0] if "主线" in c)
    pool = [r for r in zrows if r.get(mcol) == "是"]
    conn = sqlite3.connect(QFQ_DB, uri=True)
    summaries = []
    for r in pool:
        t = compute_trend(load_kline(r["code"], conn))
        if not t:
            continue
        summaries.append({
            "code": r["code"], "name": r.get("name", ""),
            "industry": f'{r.get("industry", "")}/{r.get("cat", "")[:6]}',
            "pnl": "", "s5dist": "",
            "ma_short": t["ma_state"].split("(")[0],
            "pos252": f"{t['pos252']:.0f}%", "pos252_val": t["pos252"],
            "zone_short": t["zone36"].split("(")[0],
            "vr": t["vr_label"], "ret20": f"{t['ret20']:+.1f}%",
            "phase": "", "themes": "",
        })
    conn.close()
    order = {"月顶区": 0, "月中位": 1, "月底区": 2}
    summaries.sort(key=lambda s: (order.get(s["zone_short"], 9), -s.get("pos252_val", 0)))
    stat = Counter(s["zone_short"] for s in summaries)
    stat_str = " / ".join(f"{k}{v}只" for k, v in sorted(stat.items(), key=lambda x: order.get(x[0], 9)))
    header = (
        f"# 主线池趋势速览\n\n> 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"来源: {csv_path.name} 主线标=是 (池{len(pool)}只, 有效{len(summaries)}只)\n"
        f"> 月区口径=monthly_top_z2s_20261002(顶区pos≥85/底区=距36月高≥35%或pos≤30) | 仅事实呈现零建议\n\n"
        f"**月区统计**: {stat_str}\n\n"
    )
    out = out_dir / f"主线池_趋势速览_{datetime.now().strftime('%Y%m%d')}.md"
    out.write_text(header + render_summary_table(summaries, ind_col=True) + "\n", encoding="utf-8")
    print(f"written: {out} ({len(summaries)}只)")


def cluster_mode(out_dir):
    """持仓主题/行业簇分析 — 集中度观察层(事实呈现零建议; 簇上限25%硬闸=R30b)
    民船4只/AI链多只等同主题持仓一眼可见, 归因层簇风险参照。"""
    hmeta = _load_json(HOLDINGS)
    holds = {p["code"][2:]: p for p in hmeta.get("positions", [])}
    chain = _load_json(CHAIN_CACHE).get("stocks", {})
    theme_map, ind_map = {}, {}
    conn = sqlite3.connect(FUND_DB, uri=True)
    for code, h in holds.items():
        pref = f"sz{code}" if code.startswith(("0", "3")) else f"sh{code}"
        label = f'{h["name"]}({h["pnl_pct"]:+.1f}%)'
        for t in dict.fromkeys((chain.get(pref) or {}).get("themes", [])):
            theme_map.setdefault(t, []).append(label)
        ts = f"{code}.{'SZ' if code.startswith(('0', '3')) else 'SH'}"
        r = conn.execute(
            "SELECT industry FROM feature_industry_momentum WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
            (ts,),
        ).fetchone()
        if r:
            ind_map.setdefault(r[0], []).append(label)
    conn.close()
    lines = [
        "# 持仓主题/行业簇分析",
        "",
        f"> 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 来源: 链缓存主题+行业动量库 | "
        "集中度观察层(事实呈现零建议; 单一申万L1簇上限25%硬闸=R30b买入端)",
    ]
    lines.append("\n## 主题簇(≥2只, 产业链图谱主题)")
    for t, lst in sorted(theme_map.items(), key=lambda x: -len(x[1])):
        if len(lst) >= 2:
            lines.append(f"- **{t}**({len(lst)}只): " + "、".join(lst))
    lines.append("\n## 行业簇(≥2只, 行业动量库)")
    for t, lst in sorted(ind_map.items(), key=lambda x: -len(x[1])):
        if len(lst) >= 2:
            lines.append(f"- **{t}**({len(lst)}只): " + "、".join(lst))
    out = out_dir / f"持仓_主题簇_{datetime.now().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {out}")


def main():
    args = sys.argv[1:]
    holdings_map = {}
    hmeta = _load_json(HOLDINGS)
    for p in hmeta.get("positions", []):
        holdings_map[p["code"][2:]] = p
    # 持仓成本/现价唯一真值=westock实时(快照合同=三要素code/name/qty不含价格盈亏;
    # 10/8新仓缺cost致build_dossier KeyError档案断产5晚, 2026-10-13修复)
    try:
        sys.path.insert(0, r"D:\workbuddyspace")
        from westock_client import WeStockClient
        _resp = WeStockClient().call("portfolio_paper_positions", {})
        if not _resp or not _resp.get("ok"):
            raise RuntimeError(str(_resp)[:160])
        for _p in (_resp.get("data") or {}).get("positions") or []:
            _h = holdings_map.get(str(_p.get("code", ""))[2:])
            if _h is not None:
                _h["cost"] = float(_p.get("costPrice") or 0)
                _h["price"] = float(_p.get("currentPrice") or 0)
                _h["pnl_pct"] = float(_p.get("profitRate") or 0)
    except Exception as _e:
        print(f"[dossier][FAIL] westock成本合并失败, fail-loud退出(宁缺勿伪造): {type(_e).__name__}: {_e}")
        sys.exit(2)
    # 跟踪池追加(data/watchlist_codes.json, 非持仓票不加持仓字段, 名称走name_lookup)
    wl = _load_json(WATCHLIST)
    watch_codes = [w for w in wl if w not in holdings_map]
    codes = []
    out_path = None
    if "--pool" in args:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        pool_mode(OUT_DIR)
        return
    if "--clusters" in args:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cluster_mode(OUT_DIR)
        return
    if "--holdings" in args:
        codes = list(holdings_map.keys()) + watch_codes
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"持仓_全息档案_{datetime.now().strftime('%Y%m%d')}.md"
    else:
        raw = args[args.index("--code") + 1] if "--code" in args else ""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
    if not codes:
        print("用法: python stock_dossier.py --code 003006 | --holdings | --pool")
        sys.exit(2)
    blocks, summaries = [], []
    for c in codes:
        blk, summ = build_dossier(c, holdings_map.get(c))
        blocks.append(blk)
        summaries.append(summ)
    body = "\n".join(blocks)
    header = (
        f"# 个股全息档案\n\n> 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"工具: stock_dossier.py | 口径: qfq复权计算/最新bar=实价\n"
        f"> 产业链节点=westock MCP快照缓存 | 波段相位=系统既有产物(描述性零交易权) | 本档案仅事实呈现, 零交易建议\n\n"
    )
    if out_path:
        # X1描述性标注(PD-030⑨降级: 仅池内相对分位呈现, 零选股权重; 定义见analysis/x1_rs_rank_shadow_20261003.py)
        try:
            import re as _re
            from bisect import bisect_left as _bl
            nums = []
            for s in summaries:
                mnum = _re.match(r"([+-]?[\d.]+)", str(s.get("ret20", "")))
                nums.append(float(mnum.group(1)) if mnum else None)
            vals = sorted(v for v in nums if v is not None)
            n = len(vals)
            for s, v in zip(summaries, nums):
                if v is None or n == 0:
                    s["rs_rank"] = ""
                    continue
                lo_cnt = _bl(vals, v)
                s["rs_rank"] = f"P{lo_cnt / n * 100:.0f}"
        except Exception:
            pass
        out_path.write_text(header + render_summary_table(summaries) + "\n\n---\n\n" + body, encoding="utf-8")
        print(f"written: {out_path} ({len(codes)}只)")
    else:
        print(header + render_summary_table(summaries) + "\n\n---\n\n" + body)


if __name__ == "__main__":
    main()
