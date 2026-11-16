# -*- coding: utf-8 -*-
"""universe_bs_zone_locator.py — 全宇宙候选买卖区域定位器 v1（侦察级，零交易权）
================================================================================
2026-09-29（SOP四十九-V）。结合 选股系统(universe_startup_scan) × 画像系统(portrait_feed)
× 买卖区域事件研究(bs_zone_event_study)，对扫描命中股给出 买入区域/卖出参考 的价格带与证据标注。

铁律与边界（不可豁免）：
- 侦察级：本工具产物仅供主人研判与PD裁决，不进 trade_executor / pre_trade_check 任何闸门；
  任何真实买入仍须走 trade_executor 唯一入口 + R闸全跑。
- 措辞：本产物为「侦察观察组」，非名单（名单制已废除，宪法§10）。
- 价格口径：区域计算用 qfq（回测唯一价格源）；呈报现价用 vipdoc 不复权实际价（宪法呈报铁律），
  两口径错位>3%时逐股标注 ⚠。
- 禁追检查：当日涨≥6% 标「当日禁追」；chg5≥15% 标「追高禁区」（阈值9/30主人终裁=6%，原3%废止）。
- S-5：-8%硬止损铁律不变；本工具给出的 -8% 参考线仅为侦察锚，实际止损=成本-8%。

用法：
    python universe_bs_zone_locator.py                # 默认最新 universe_startup_scan_*.json
    python universe_bs_zone_locator.py --scan reports/universe_startup_scan_20260929.json
输出（幂等覆盖）：
    data/universe_bs_zones_<today>.json
    reports/universe_bs_zones_<today>.md
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from datetime import date

from tdx_reader import read_day

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
PORTRAIT_FEED = r"D:/zcodesj/data/portrait_feed.json"
TODAY = date.today().strftime("%Y%m%d")
STUDY_JSON = r"D:/zcodesj/data/bs_zone_event_study_20260929.json"


def latest_scan_path() -> str:
    hits = sorted(glob.glob(r"D:/zcodesj/reports/universe_startup_scan_*.json"))
    if not hits:
        raise SystemExit("[BSZONE-LOC][FAIL] 未找到 universe_startup_scan_*.json，先跑 universe_startup_scan.py")
    return hits[-1]


def load_qfq(code: str, bars: int = 300):
    con = sqlite3.connect(QFQ_DB, uri=True)
    rows = con.execute(
        "SELECT date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date DESC LIMIT ?", (code, bars)).fetchall()
    con.close()
    return list(reversed(rows))


def _sma(xs, win):
    if len(xs) < win:
        return None
    return sum(xs[-win:]) / win


def analyze_hit(hit: dict, portrait: dict) -> dict:
    code = hit["code"]
    name = hit.get("name", "")
    if "ST" in name.upper():
        return {**hit, "skip": "ST"}
    rows = load_qfq(code)
    if len(rows) < 260:
        return {**hit, "skip": "qfq不足260根"}
    dates = [r[0] for r in rows]
    closes = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    c = closes[-1]
    prev_c = closes[-2]
    ma5, ma10 = _sma(closes, 5), _sma(closes, 10)
    ma20, ma60 = _sma(closes, 20), _sma(closes, 60)
    ma20_prev5 = closes[-25] if len(closes) >= 25 else None
    ma20_5ago = _sma(closes[:-5], 20) if len(closes) >= 30 else None
    ma20_rising = bool(ma20_5ago is not None and ma20 is not None and ma20 > ma20_5ago)
    chg5 = c / closes[-6] - 1.0 if len(closes) >= 6 else None
    ret20 = c / closes[-21] - 1.0 if len(closes) >= 21 else None
    win250 = closes[-250:]
    anchor = min(win250)
    off250 = c / anchor - 1.0
    res60 = max(closes[-60:])
    vma5, vma120 = _sma(vols, 5), _sma(vols, 120)
    vr120 = (vma5 / vma120) if (vma5 and vma120) else None
    vol_tag = ("缩量" if (vma5 and vols[-1] < vma5) else
               ("放量" if (vma5 and vols[-1] >= 1.5 * vma5) else "常量"))

    # 买入区域（qfq口径）
    z1_lo, z1_hi = anchor * 1.05, anchor * 1.10          # Z1 确认窗带
    b2_lo, b2_hi = (ma20, ma10) if (ma20_rising and ma10 and ma20 and ma10 > ma20) else (None, None)
    in_z1 = z1_lo <= c <= z1_hi
    in_b2 = bool(b2_lo is not None and b2_lo <= c <= b2_hi)
    # 卖出参考（qfq口径）
    stop_ref = c * 0.92
    bad_line = ma20 * 0.99 if ma20 else None             # trend_checker 走坏判定起点
    blowoff = bool(vr120 and vr120 >= 1.5 and ret20 is not None and ret20 >= 0.20)

    # 不复权呈报价
    raw = read_day("", code)
    raw_close = raw[-1][4] if raw else None
    raw_date = raw[-1][0] if raw else None
    raw_prev = raw[-2][4] if len(raw) >= 2 else None
    raw_chg = (raw_close / raw_prev - 1.0) if (raw_close and raw_prev) else None
    caliber_warn = bool(raw_close and abs(raw_close - c) / c > 0.03)
    # 挂单参考(提案⑤, 2026-10-01 批准; 同日八轮N实验修正语义):
    # ⚠逆向选择警示: 98折可成交组 t20e +5.09%/win51% vs 未成交组 +7.82%/win62%,
    #   组合级限价策略 -6.58% vs 开盘追价 +15.85% —— 挂折价限价会选中走弱的票,
    #   本列仅为触及价位参考, ❌不构成挂单建议; 引擎验证的有效入场=次日开盘(回踩企稳确认后)。
    limit_base = raw_close if raw_close else c
    limit_ref = round(limit_base * 0.98, 2)

    pf = portrait.get("scores_short", {}).get(code, {})
    pct = pf.get("pct")
    prob = pf.get("prob")

    # 先行因子 lift 标注（up_prob_recon_20260924 §八口径：F1涨停基因lift1.94/F5创60日新高lift2.43；
    # 诚实面：三因全中 fwd5 中位-1.45%，捕获率≠正期望，仅大涨概率提示非买入信号）
    limit_hits = 0
    for j in range(max(1, len(closes) - 20), len(closes)):
        ratio = 1.2 if code.startswith(("300", "301")) else 1.1
        if closes[j] >= round(closes[j - 1] * ratio, 2):
            limit_hits += 1
            break
    new_high_60 = bool(len(closes) >= 61 and c >= max(closes[-61:-1]))
    lift_notes = []
    if limit_hits:
        lift_notes.append("涨停基因(lift1.94)")
    if new_high_60:
        lift_notes.append("创60日新高(lift2.43)")
    lift_note = "+".join(lift_notes) if lift_notes else "-"

    # 禁追检查
    no_chase_today = bool(raw_chg is not None and raw_chg >= 0.06) or (raw_chg is None and False)  # 9/30主人终裁:追涨线3%->6%
    chase_zone = bool(chg5 is not None and chg5 >= 0.15)

    if in_z1 and in_b2:
        state = f"确认窗∩回踩带({vol_tag})"
    elif in_z1:
        state = "⭐确认窗内"
    elif in_b2:
        state = f"回踩带内({vol_tag})" + ("✓Z2s" if vol_tag == "缩量" else "")
    elif b2_lo is not None and c < b2_lo:
        state = "回踩未企稳(带下方,触发=收复MA10)"
    elif c > z1_hi:
        state = "上方等回踩"
    else:
        state = "确认窗下方(启动偏早段)"

    if chase_zone or blowoff:
        grp = "C"
    elif in_z1 or in_b2:
        grp = "A"
    elif state.startswith(("回踩未企稳", "上方等回踩")):
        grp = "B"
    else:
        grp = "D"
    if no_chase_today:
        grp = grp + "+禁追" if grp in ("A", "B", "D") else grp

    return {
        "code": code, "name": name, "ind": hit.get("ind", ""),
        "px_qfq": round(c, 2), "qfq_date": dates[-1],
        "px_raw": raw_close, "raw_date": raw_date,
        "raw_chg_pct": round(raw_chg * 100, 2) if raw_chg is not None else None,
        "caliber_warn": caliber_warn,
        "portrait_pct": pct, "portrait_prob": prob,
        "pos250": hit.get("pos250"), "off_start": hit.get("off_start"), "days": hit.get("days"),
        "chg5_pct": round(chg5 * 100, 2) if chg5 is not None else None,
        "ret20_pct": round(ret20 * 100, 2) if ret20 is not None else None,
        "vr120": round(vr120, 2) if vr120 else None,
        "buy_zone_z1": [round(z1_lo, 2), round(z1_hi, 2)],
        "buy_zone_ma": [round(b2_lo, 2), round(b2_hi, 2)] if b2_lo is not None else None,
        "in_z1": in_z1, "in_ma_zone": in_b2, "state": state,
        "stop_ref": round(stop_ref, 2),
        "res60": round(res60, 2), "res_dist_pct": round((res60 / c - 1) * 100, 1),
        "bad_line": round(bad_line, 2) if bad_line else None,
        "blowoff_warn": blowoff, "no_chase_today": no_chase_today, "chase_zone": chase_zone,
        "lift_note": lift_note, "limit_ref": limit_ref,
        "grp": grp, "portrait_fresh": portrait.get("freshness", {}),
    }


def main() -> int:
    scan_path = latest_scan_path()
    scan = json.load(open(scan_path, encoding="utf-8"))
    portrait = json.load(open(PORTRAIT_FEED, encoding="utf-8")) if os.path.isfile(PORTRAIT_FEED) else {}
    study = json.load(open(STUDY_JSON, encoding="utf-8")) if os.path.isfile(STUDY_JSON) else {}
    zs = study.get("zones", {})
    z1, z2, z4 = zs.get("Z1_confirm_window", {}), zs.get("Z2_pullback_ma10", {}), zs.get("Z4_chase_negative", {})
    sell_z = study.get("sell_zones", {})
    s1 = sell_z.get("S1_fail_retest", {}).get("all", {})
    s2 = sell_z.get("S2_breakout_vol", {}).get("all", {})
    sp = study.get("strict_population_check", {}).get("Z2_shrink_vol", {})
    sp_b, sp_s = sp.get("broad", {}).get("t20e", {}), sp.get("strict", {}).get("t20e", {})
    idx_rows = read_day("sh", "000001")
    cur_regime = "N/A(上证数据缺失)"
    if len(idx_rows) >= 21:
        ma20_idx = sum(r[4] for r in idx_rows[-20:]) / 20
        cur_regime = ("BULL(上证收于MA20上方)" if idx_rows[-1][4] >= ma20_idx else "BEAR(上证收于MA20下方)") + f"@{idx_rows[-1][0]}"
    z2_regime = z2.get("regime", {})
    z2_bull = z2_regime.get("BULL", {}).get("t20e", {})
    z2_bear = z2_regime.get("BEAR", {}).get("t20e", {})

    results, skipped = [], []
    for hit in scan.get("hits", []):
        r = analyze_hit(hit, portrait)
        (skipped if r.get("skip") else results).append(r)
    results.sort(key=lambda r: (r["grp"][0], -(r.get("portrait_pct") or 0)))

    out = {"version": "universe_bs_zone_locator_v1", "recon_only": True,
           "scan_file": scan_path, "scan_date": os.path.basename(scan_path)[24:32],
           "asof_today": TODAY, "qfq_state": scan.get("state"),
           "portrait_snapshot": portrait.get("snapshot_date"),
           "study_summary": {k: {"t20e": zs[k]["all"]["t20e"], "fwd5": zs[k]["all"]["fwd5"],
                                 "regime": zs[k].get("regime")} for k in zs},
           "current_regime": cur_regime,
           "sell_zones": {k: sell_z[k]["all"] for k in sell_z},
           "n_hits": len(scan.get("hits", [])), "n_located": len(results), "n_skipped": len(skipped),
           "rows": results}
    json_path = rf"D:/zcodesj/data/universe_bs_zones_{TODAY}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    def zone_txt(r):
        z1t = f"确认窗带[{r['buy_zone_z1'][0]}~{r['buy_zone_z1'][1]}]"
        mat = f"回踩带[{r['buy_zone_ma'][0]}~{r['buy_zone_ma'][1]}]" if r["buy_zone_ma"] else "回踩带N/A(MA20未上行)"
        return f"{z1t}；{mat}｜状态:{r['state']}"

    def sell_txt(r):
        return (f"止损参考{r['stop_ref']}(=现价-8%)；压力{r['res60']}(距{r['res_dist_pct']}%)；"
                f"走坏线{r['bad_line']}(MA20-1%×2日)"
                + ("；⚠爆量末段警戒" if r["blowoff_warn"] else ""))

    t2 = z2.get("all", {}).get("t20e", {})
    f4 = z4.get("all", {}).get("fwd5", {})
    lines = [
        f"# 全宇宙候选买卖区域定位（侦察级·零交易权） {TODAY}",
        "",
        f"> 声明：本报告为侦察观察组产物，**非名单**，不进任何闸门；买入仍须 trade_executor 唯一入口+R闸全跑。",
        f"> 数据源：{os.path.basename(scan_path)}（扫描{scan.get('scanned')}只/命中{out['n_hits']}）× portrait_feed@{out['portrait_snapshot']}"
        f" × qfq库(计算口径) × vipdoc不复权(呈报口径)。",
        "",
        "## 一、原始数据（证据底座，先于结论）",
        "",
        f"- 买卖区域事件研究(bs_zone_event_study_20260929, qfq库, 2023-01~2026-09, 25,216事件, 入场=次开, 止损=收盘-8%持20日)：",
        f"  - Z2 回踩MA10企稳带 n=11,849：t20e {t2.get('mean')}%/胜率{t2.get('win')}%/中位{t2.get('med')}%（四带唯一中位为正）",
        f"  - Z1 确认窗带 n=2,343：fwd20 +1.381%/胜率43.2%（与9/19迭代报告 fwd20+1.54% 复验吻合）",
        f"  - Z4 追高负对照 n=774：fwd5 {f4.get('mean')}%/胜率{f4.get('win')}%（与9/18禁追铁律互证）",
        f"- 严格口径验证(v1.3，泛强池∧扫描器细筛：前60日跌≥25%∧启动前缩量)：**Z2s缩量回踩 strict n={sp_s.get('n')} t20e {sp_s.get('mean')}%/胜率{sp_s.get('win')}%/中位{sp_s.get('med')}%**（泛口径 {sp_b.get('mean')}%/胜率{sp_b.get('win')}%）——**扫描器质量细筛为质变关键，选股×缩量回踩合取为本宇宙最强买点组合**；Z1严格口径n=80小样本观察级。",
        f"- 分年如实：2023/2026年四带 t20e 全负，2024强正——**区域有效性依赖市况**；市况分层(上证MA20口径)：Z2回踩带 BULL段 t20e {z2_bull.get('mean')}%(胜率{z2_bull.get('win')}%,n={z2_bull.get('n')}) vs BEAR段 {z2_bear.get('mean')}%(胜率{z2_bear.get('win')}%,n={z2_bear.get('n')})；**当前市况={cur_regime}**。",
        f"- 卖出压力带实证(v1.1，两个直觉假设均被削弱)：S1冲过前高回落 n={s1.get('fwd5', {}).get('n')} fwd5 {s1.get('fwd5', {}).get('mean')}%(胜率{s1.get('fwd5', {}).get('win')}%,t20e中位{s1.get('t20e', {}).get('med')}%)——**前高冲高回落≠卖出依据**(fwd为正)；S2放量创60日新高 n={s2.get('fwd5', {}).get('n')} fwd5 {s2.get('fwd5', {}).get('mean')}% fwd20 {s2.get('fwd20', {}).get('mean')}%——**创新高后买入偏负=追高族再证**。定谳：卖出依据=S-5止损/trend_checker走坏/爆量末段状态（C3），非价格压力带。",
        "- 先行因子lift标注（up_prob_recon §八）：涨停基因lift1.94/创60日新高lift2.43（大涨概率提示）；诚实面=三因全中fwd5中位-1.45%胜率44.8%——**捕获率≠正期望**，lift列仅提示、非买入信号。",
        "- 深贴-8%回补（自有持仓卖出侧）：prereg_sold_rebuy_deep8 已过三门（355,640笔/+2.826%/胜率60%），pilot已实战，不在本报告买入侧。",
        "- 卖出侧证据：爆量末段(vr>1.5∧ret20≥20%)→后续回撤中位+3pp（C3相位量能）；走坏=收盘<MA20且空头排列且破位≥1%×连续2日（trend_checker）。",
        "",
        "## 二、规则核对",
        "",
        "- 排除面：科创板/北交所（扫描器内剔）、ST（本器跳过）、价≤30元（扫描器内剔）；次新<60交易日=G0闸。",
        "- 禁追：当日涨≥6%→当日禁追；chg5≥15%→追高禁区（组别C）。",
        "- 98折触及参考（提案⑤, 2026-10-01 批准后同日八轮N实验修正）：⚠逆向选择实锤——98折可成交组前向 t20e +5.09%/win51% 显著差于未成交组 +7.82%/win62%，组合级限价策略 -6.58% vs 开盘追价 +15.85%。**挂折价限价会系统性选中走弱的票，本列仅供盘口触及参考，不构成挂单建议；验证有效的入场=次日开盘（回踩企稳确认后），与「回踩带内限价」铁律的区别=后者是带内企稳确认后介入，非盘中挂折价单等成交。**",
        "- 涉及买卖动作全部保留主人裁决权；卖多少问主人；S-5触发即执行不等待。",
        "",
        "## 三、定位明细（按组别排序；价格带为qfq口径，现价为不复权口径）",
        "",
    ]
    for g, gname in (("A", "A组·买入区域活跃（现处确认窗/回踩带）"), ("B", "B组·回踩观察（未在带内：等回踩或回踩未企稳）"),
                     ("C", "C组·禁区/警戒（追高禁区或爆量末段）"), ("D", "D组·其余观察（确认窗下方偏早段）")):
        rows = [r for r in results if r["grp"].startswith(g)]
        if not rows:
            continue
        lines += [f"### {gname}（{len(rows)}只）", "",
                  "| 代码 | 名称 | 行业 | 现价(不复权) | 日% | qfq价 | 买入区域 | 卖出参考 | 98折触及参考⚠ | 画像pct | 先行因子 | 组别 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            px = f"{r['px_raw']}@{r['raw_date']}" if r["px_raw"] else f"qfq{r['px_qfq']}"
            warn = " ⚠口径错位" if r["caliber_warn"] else ""
            pct = r["portrait_pct"] if r["portrait_pct"] is not None else "-"
            lines.append(f"| {r['code']} | {r['name']} | {r['ind']} | {px}{warn} | {r['raw_chg_pct']} | {r['px_qfq']} | "
                         f"{zone_txt(r)} | {sell_txt(r)} | {r['limit_ref']} | {pct} | {r['lift_note']} | {r['grp']} |")
        lines.append("")
    lines += [
        "## 四、正向/反向信号与结论",
        "",
        f"- 正向：定位完成 {out['n_located']}/{out['n_hits']} 只（跳过{out['n_skipped']}）；A组{len([r for r in results if r['grp'].startswith('A')])}只区域活跃；Z2回踩带为本宇宙内实证最优买点带（BULL段胜率55.1%/中位+1.41%兑现质量最好）。",
        "- 反向：①2023/2026年四带全负=市况依赖，BEAR段下Z2均值虽正但中位-0.93%（右偏彩票化，多数事件小亏靠少数大赢家拉均值）；②扫描命中多为低位启动初期（off_start<5%偏早带，9/19口径确认不足）；③画像分仅观察级（v1特征面已判死falsified#49），A/B期中B+3.61pp非结论；④Z1确认窗带BULL段t20e-0.92%反弱于BEAR段+3.98%（反直觉，如实呈现不过度解释）。",
        f"- 结论【预判，存在不确定性】：本宇宙「精准买点」实证排序 = 回踩MA10企稳带 > 确认窗带(+5~10%) > 深回踩MA20；追高带跨市况负期望成立；卖出依据=状态（S-5/走坏/爆量）而非价格压力带。置信度：中（证据≥3条独立数据：9/19迭代、桶C相对维度+2.656pp、本次25,216+19.7万事件研究；分年不稳与市况依赖拉低置信度）。",
        "- 下一步边界：区域带是否作为 pre_trade_check 参考层/侦察简报接线，走 PD-023 裁决；本报告不触发任何交易。",
        "",
        "## 五、人话版（终裁呈报附通俗段，主人9/23铁律）",
        "",
        "- 这份报告回答两个问题：**在哪里买划算、在哪里卖有依据**。",
        "- 买：我们选出的低位启动股，历史数据说「涨起来之后回踩到10日均线附近能站稳」的位置买入，20天后平均赚3.7%、一半以上不亏——这是四个候选买点里唯一「多数情况能赚」的；但前提是大盘在20日线上方（牛市段），弱市里它变成「少数暴涨拉平均」，多数小亏。",
        "- 追高（5天涨15%以上再买）不管牛熊都亏，继续禁。",
        "- 卖：我们本来猜「冲到前高回落就该卖」「创新高说明强势该拿着」——数据都说不成立。真正的卖出发令枪还是那三样：亏到-8%走人、趋势走坏、放量滞涨警报。价格位置本身不构成卖出理由。",
        "- 本报告只是侦察地图，不是买入指令；买不买、买多少，全部等主人裁决（PD-023）。",
    ]
    md_path = rf"D:/zcodesj/reports/universe_bs_zones_{TODAY}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[BSZONE-LOC] hits={out['n_hits']} located={len(results)} skipped={len(skipped)} -> {json_path}")
    for g in "ABCD":
        n = len([r for r in results if r["grp"].startswith(g)])
        print(f"  {g}组: {n}")
    return 0


def run_positions() -> int:
    """--positions 持仓盯防模式（侦察级）：28只在仓股的卖出参考/警戒状态。
    止损线=成本×0.92（S-5铁律真锚）；走坏线=MA20×0.99（实判归trend_checker连续2日+空头排列）；
    爆量警戒=C3口径；分级仅侦察提示，卖出决策=主人+pre_sell防线。零交易权。"""
    import glob as _g
    cands = sorted(_g.glob(r"D:/zcodesj/data/positions_recon_*.json"))
    pf_path = cands[-1] if cands else r"D:/zcodesj/data/positions_latest.json"
    pf = json.load(open(pf_path, encoding="utf-8"))
    stale = "recon" not in os.path.basename(pf_path)
    rows = []
    for p in pf.get("positions", []):
        if int(p.get("quantity", 0) or 0) <= 0 and p.get("quantity") is not None:
            continue
        code, name = p["code"], p["name"]
        cost, cur = float(p.get("cost") or p.get("costPrice") or 0), float(p.get("current") or p.get("currentPrice") or 0)
        pr = float(p.get("profit_rate", p.get("profitRate", 0)) or 0)
        rows_q = load_qfq(code)
        if len(rows_q) < 60:
            rows.append({**p, "grp": "SKIP", "why": "qfq不足"})
            continue
        closes = [r[4] for r in rows_q]
        vols = [r[5] for r in rows_q]
        c = closes[-1]
        ma10, ma20, ma60 = _sma(closes, 10), _sma(closes, 20), _sma(closes, 60)
        ma20_5ago = _sma(closes[:-5], 20) if len(closes) >= 30 else None
        ma20_up = bool(ma20_5ago and ma20 > ma20_5ago)
        ret20 = c / closes[-21] - 1.0 if len(closes) >= 21 else 0
        vma5, vma120 = _sma(vols, 5), _sma(vols, 120)
        vr120 = (vma5 / vma120) if (vma5 and vma120) else None
        res60 = max(closes[-60:])
        stop = cost * 0.92
        dist_stop = (stop / cur - 1) * 100 if cur else None   # 现价再跌x%触S-5
        blowoff = bool(vr120 and vr120 >= 1.5 and ret20 >= 0.20)
        bearish_approx = bool(ma20 and ma10 and ma60 and c < ma20 * 0.99 and ma10 < ma20)
        zone_state = "-"
        if ma20_up and ma10 and ma20:
            if ma20 <= c <= ma10:
                zone_state = "回踩带内"
            elif c < ma20:
                zone_state = "带下方(破MA20)"
            else:
                zone_state = "带上方"
        if pr <= -6 or bearish_approx:
            grp = "RISK"
        elif pr <= -5 or blowoff or (dist_stop is not None and dist_stop >= -3):
            grp = "WARN"
        else:
            grp = "NORMAL"
        big_gain = pr >= 60
        rows.append({"code": code, "name": name, "cost": cost, "cur": cur,
                     "profit_rate": pr, "today_pct": p.get("today_pct"),
                     "stop": round(stop, 3), "dist_stop_pct": round(dist_stop, 2) if dist_stop is not None else None,
                     "bad_line": round(ma20 * 0.99, 2) if ma20 else None,
                     "res60_qfq": round(res60, 2), "res_dist_pct": round((res60 / c - 1) * 100, 1),
                     "vr120": round(vr120, 2) if vr120 else None, "ret20_pct": round(ret20 * 100, 1),
                     "blowoff": blowoff, "bearish_approx": bearish_approx, "zone_state": zone_state,
                     "big_gain": big_gain, "grp": grp})
    order = {"RISK": 0, "WARN": 1, "NORMAL": 2, "SKIP": 3}
    rows.sort(key=lambda r: order.get(r["grp"], 9))
    out = {"version": "positions_bs_watch_v1", "recon_only": True, "positions_file": pf_path,
           "stale_local_snapshot": stale, "asof": TODAY, "n": len(rows),
           "counts": {g: len([r for r in rows if r["grp"] == g]) for g in ("RISK", "WARN", "NORMAL")},
           "rows": rows}
    json_path = rf"D:/zcodesj/data/positions_bs_watch_{TODAY}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    lines = [
        f"# 持仓买卖区域盯防（侦察级·零交易权） {TODAY}",
        "",
        f"> 声明：侦察提示非指令；持仓真值=westock MCP实时（本表源={os.path.basename(pf_path)}"
        + ("，⚠本地陈旧快照" if stale else "") + "）；卖出须有理由（趋势走坏或S-5触发），卖多少问主人，S-5触发即执行不等待。",
        f"> 分级统计：🔴RISK {out['counts']['RISK']}只 / 🟠WARN {out['counts']['WARN']}只 / ⚪NORMAL {out['counts']['NORMAL']}只（共{out['n']}只在仓）。",
        "",
        "## 证据与口径",
        "",
        "- S-5止损线=成本×0.92（铁律真锚，非现价-8%近似）；走坏线=MA20×0.99（**实判归trend_checker**：收盘<MA20∧空头排列∧破位≥1%×连续2日，本表bearish列为近似预警）；爆量警戒=vr120≥1.5∧ret20≥20%（C3相位量能：末段爆量→后续回撤+3pp）。",
        "- 压力带研究结论（v1.1）：前高冲高回落≠卖出信号——本表压力距离仅作兑现参考，不构成卖出依据。",
        "",
    ]
    for g, gname, note in (("RISK", "🔴RISK·风险带（距S-5近或近似走坏预警）", "逐只核对trend_checker实判与S-5线"),
                           ("WARN", "🟠WARN·警戒带（爆量末段/距止损3pp内）", "关注量能与兑现节奏，处置=主人裁决"),
                           ("NORMAL", "⚪NORMAL·正常带", "")):
        rs = [r for r in rows if r["grp"] == g]
        if not rs:
            continue
        lines += [f"### {gname}（{len(rs)}只）{note}", "",
                  "| 代码 | 名称 | 成本 | 现价 | 浮盈% | 距S-5线 | 止损线 | 走坏线 | 回踩带状态 | 爆量警戒 | 压力(60日,距%) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rs:
            lines.append(f"| {r['code']} | {r['name']} | {r['cost']} | {r['cur']} | {r['profit_rate']} | "
                         f"{r['dist_stop_pct']}% | {r['stop']} | {r['bad_line']} | {r['zone_state']} | "
                         f"{'⚠是' if r['blowoff'] else '-'} | {r['res60_qfq']}({r['res_dist_pct']}%) |")
        lines.append("")
    lines += [
        "## 人话版",
        "",
        f"- 这张表只回答一件事：手里的票，**跌到哪必须走（止损线）、走到哪算走坏（MA20线，两日确认才算）、涨到哪要打起精神（爆量/压力）**。",
        "- 🔴只=离止损线近或形状已坏（待trend_checker实判），🟠只=有警报但没到红线，其余正常。",
        "- 本表不给任何卖出指令；卖不卖、卖多少，全部主人裁决。",
    ]
    md_path = rf"D:/zcodesj/reports/positions_bs_watch_{TODAY}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[BSZONE-POS] n={out['n']} RISK={out['counts']['RISK']} WARN={out['counts']['WARN']} NORMAL={out['counts']['NORMAL']} -> {json_path}")
    return 0


if __name__ == "__main__":
    if "--positions" in sys.argv:
        sys.exit(run_positions())
    sys.exit(main())
