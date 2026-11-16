#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大板块智能复盘（2026-08-21 建，屠龙系统新增功能③）

工作日 22:45 生成（避开 22:00 八合一），全口径大板块复盘：
  1. 指数与量能 —— 腾讯行情（上证/深成/创业板/科创50/沪深300）
  2. 申万一级 31 行业 —— 当日涨跌幅 Top10/Bottom10 + 近5日动量（sw_industry.db）
  3. 东财行业/概念 —— 涨跌榜 + 主力资金流榜（sector_data.db 快照，滞后时标注）
  4. 涨停梯队与情绪 —— seal_snapshot 真封板/伪封板/炸板 + 封单 Top + qfq 当日涨跌家数
  5. 轮动链 —— 近3日申万领涨板块自动对比（sector_review_leaders.json 自维护）

集成点（重要）：
  同时输出 reports/daily_review_{date}.json，契约 = read_yesterday_summary 期望的
  {market_env, main_sectors, risk_warnings, space_board} —— 09:10 作战清单自动消费。

脚本定位 = 数据事实生成器；智能定性解读由 cron 中知微完成（数据→正负信号→结论）。

用法：
    python tulong_sector_review.py                 # 今日
    python tulong_sector_review.py --date 20260820 # 回溯
    python tulong_sector_review.py --json          # JSON 输出
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime

REPORT_DIR = r"D:\zcodesj\reports"
SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"
SECTOR_DB = r"D:\zcodesj\eastmoney_quant\sector\sector_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SEAL_DB = r"D:\zcodesj\attribution\attribution.db"  # 2026-08-24 双库统一:指向唯一真源
LEADER_HIST = os.path.join(REPORT_DIR, "sector_review_leaders.json")

INDEX_MAP = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
             ("sh000688", "科创50"), ("sh000300", "沪深300")]

WEEKDAYS_ZH = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}


def _today() -> str:
    # 2026-09-25 修复(9/24夜实证): 午夜后恢复性重跑曾锚到未完结的"明天", 误产
    # 20260925 半成品产物(真封板0)会被次日09:10误消费。分时段锚定:
    #   >=06点(含22:45 cron正常路径) = 日历日(维持原语义, 数据滞后由脚本自带标注兜底);
    #   <06点(凌晨恢复窗口)          = qfq 主库 MAX(date)=已完结交易日, 库不可用回退日历日。
    if datetime.now().hour >= 6:
        return datetime.now().strftime("%Y%m%d")
    try:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
        try:
            d = str(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
            if len(d) == 8 and d.isdigit():
                return d
        finally:
            con.close()
    except Exception:
        pass
    return datetime.now().strftime("%Y%m%d")


def fetch_tencent(codes: list[str]) -> dict[str, dict]:
    """腾讯批量行情（带市场前缀的代码）。"""
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
    out: dict[str, dict] = {}
    for seg in raw.strip().split(";"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        f = seg.split("=")[1].strip('"').split("~")
        if len(f) < 40:
            continue
        try:
            out[f[2]] = {"name": f[1], "price": float(f[3] or 0), "prev": float(f[4] or 0),
                         "pct": float(f[32] or 0), "amount_wan": float(f[37] or 0)}
        except (ValueError, IndexError):
            continue
    return out


def ensure_sw(date_str: str) -> str:
    """确保申万 L1 覆盖目标日；不足则子进程增量更新，失败返回实际最新日。"""
    con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True, timeout=20)
    d = con.execute("SELECT MAX(trade_date) FROM industry_kline_sw WHERE level='L1'").fetchone()[0]
    con.close()
    if d and d.replace("-", "") >= date_str:
        return d
    print(f"  申万 L1 最新 {d} < 目标 {date_str}，运行增量更新...")
    try:
        r = subprocess.run([sys.executable, r"D:/zcodesj/update_sw_index_daily.py"],
                           capture_output=True, text=True, timeout=420)
        if r.returncode != 0:
            print(f"  ⚠️ update_sw_index_daily 失败: {r.stderr[-300:]}")
    except Exception as e:
        print(f"  ⚠️ 更新异常: {e}")
    con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True, timeout=20)
    d2 = con.execute("SELECT MAX(trade_date) FROM industry_kline_sw WHERE level='L1'").fetchone()[0]
    con.close()
    return d2 or d


def sw_l1(date_str: str) -> tuple[list[dict], list[dict], str]:
    """申万 L1：全部行业末条(带 pct/5日动量)、当日有数据行业、最新日期。"""
    con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True, timeout=30)
    rows = con.execute(
        "SELECT ts_code, name, trade_date, close FROM industry_kline_sw "
        "WHERE level='L1' AND trade_date <= ? ORDER BY ts_code, trade_date",
        (f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",)).fetchall()
    con.close()
    by: dict[str, list] = {}
    names: dict[str, str] = {}
    for ts, name, d, close in rows:
        by.setdefault(ts, []).append((d, close))
        names[ts] = name
    latest = ""
    for ts, seq in by.items():
        if seq and seq[-1][0] > latest:
            latest = seq[-1][0]
    items = []
    for ts, seq in by.items():
        if len(seq) < 2:
            continue
        seq.sort(key=lambda x: x[0])
        pct = (seq[-1][1] / seq[-2][1] - 1) * 100 if seq[-2][1] else None
        mom5 = None
        if len(seq) >= 6 and seq[-6][1]:
            mom5 = (seq[-1][1] / seq[-6][1] - 1) * 100
        items.append({"code": ts, "name": names.get(ts, ts), "date": seq[-1][0],
                      "close": seq[-1][1], "pct": pct, "mom5": mom5})
    items = [it for it in items if it["pct"] is not None]
    items.sort(key=lambda x: -(x["pct"] or 0))
    today = [it for it in items if it["date"] == latest]
    return items, today, latest


def em_boards() -> tuple[list[dict], list[dict], str]:
    """东财板块快照：行业+概念当日榜（含主力净流入与领涨股）。返回 (行业, 概念, 数据日期)。
    数据日期取 meta.sector_updated（整体更新日，通常为 T-1）。"""
    ind, concepts = [], []
    date_s = ""
    try:
        conn = sqlite3.connect(f"file:{SECTOR_DB}?mode=ro", uri=True, timeout=30)
        meta = conn.execute(
            "SELECT value FROM meta WHERE key='sector_updated'").fetchone()
        rows = conn.execute(
            "SELECT sector_name, sector_type, change_pct, main_net_inflow, main_net_pct, "
            "lead_stock_name FROM sector_basic").fetchall()
        conn.close()
        if meta and meta[0]:
            date_s = str(meta[0])
    except Exception:
        return ind, concepts, date_s
    for name, btype, pct, inflow, inflow_pct, lead in rows:
        if not name:
            continue
        # 2026-09-21 根治: 东财领涨股名含全角/连续空格(如"万  科Ａ")致 verify_stock_names
        # 吃字判❌(9/18与9/21两犯) → 归一化去空格; 全角字符(Ａ)保留与库中名称一致
        lead_norm = (lead or "").replace("\u3000", "").replace("  ", " ").strip()
        rec = {"name": name, "pct": pct, "inflow": inflow, "inflow_pct": inflow_pct,
               "lead": lead_norm}
        if btype == "industry":
            ind.append(rec)
        elif btype == "concept":
            concepts.append(rec)
    return ind, concepts, date_s


def sw_realtime() -> list[dict]:
    """申万二级行业当日实时行情（akshare index_realtime_sw，124 个 L2，无 L1）。
    当日口径补充（官方日线历史常滞后 T+1）。返回 [{code,name,pct,price,amount}]。"""
    try:
        import akshare as ak
        df = ak.index_realtime_sw()
    except Exception:
        return []
    out = []
    for _, r in df.iterrows():
        prev, last = r.get("昨收盘"), r.get("最新价")
        if prev and last:
            out.append({"code": str(r.get("指数代码", "")), "name": str(r.get("指数名称", "")),
                        "pct": (float(last) / float(prev) - 1) * 100,
                        "price": float(last), "amount": float(r.get("成交额") or 0)})
    out.sort(key=lambda x: -x["pct"])
    return out


def seal_stats(date_str: str) -> dict:
    """seal_snapshot 当日封板结构（14:57 cron 产物）。"""
    out = {"n_sealed": 0, "n_fake": 0, "n_broken": 0, "top5": []}
    try:
        con = sqlite3.connect(f"file:{SEAL_DB}?mode=ro", uri=True, timeout=20)
        row = con.execute(
            "SELECT COALESCE(SUM(sealed),0), COALESCE(SUM(fake),0), COALESCE(SUM(broken),0) "
            "FROM seal_snapshot WHERE date=?", (date_str,)).fetchone()
        top = con.execute(
            "SELECT code, name, sealed_amount FROM seal_snapshot "
            "WHERE date=? AND sealed=1 ORDER BY sealed_amount DESC LIMIT 5", (date_str,)).fetchall()
        con.close()
    except Exception:
        return out
    if row:
        out["n_sealed"], out["n_fake"], out["n_broken"] = (int(row[0] or 0), int(row[1] or 0),
                                                           int(row[2] or 0))
    out["top5"] = [{"code": r[0], "name": r[1], "amount": r[2] or 0} for r in top]
    return out


def limit_stats(date_str: str) -> dict:
    """limit_up_patterns / limit_down_risks 归因库口径（22:00 链日更，剔 ST/科创北交）。

    2026-09-20 起 sentiment_cycle 台账按主人 PD-03 裁决只读封存（falsified #63），
    本口径即涨停生态的官方替代数据面；尾盘板口径 = first_limit_time >= 13:00:00
    （库内 afternoon_board 冻结定义，backfill_limitup_features_20260818.py）。
    """
    out = {"date": None, "n_limit": 0, "max_board": 0, "n_ge2": 0,
           "n_afternoon": 0, "break_events": 0, "n_down": 0}
    try:
        con = sqlite3.connect(f"file:{SEAL_DB}?mode=ro", uri=True, timeout=20)
        d = con.execute("SELECT MAX(trade_date) FROM limit_up_patterns").fetchone()[0]
        rows = con.execute(
            "SELECT COUNT(*), COALESCE(MAX(limit_count),0), "
            "COALESCE(SUM(CASE WHEN limit_count>=2 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN first_limit_time>='130000' THEN 1 ELSE 0 END),0), "
            "seal_status FROM limit_up_patterns WHERE trade_date=? GROUP BY seal_status",
            (date_str,)).fetchall()
        out["date"] = str(d) if d else None
        for n, mb, ge2, pm, status in rows:
            out["n_limit"] += int(n or 0)
            out["max_board"] = max(out["max_board"], int(mb or 0))
            out["n_ge2"] += int(ge2 or 0)
            out["n_afternoon"] += int(pm or 0)
            m = re.search(r"(\d+)炸", status or "")
            if m:
                out["break_events"] += int(m.group(1)) * int(n or 0)
        out["n_down"] = int(con.execute(
            "SELECT COUNT(*) FROM limit_down_risks WHERE trade_date=?",
            (date_str,)).fetchone()[0])
        con.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def qfq_breadth(date_str: str) -> dict:
    """qfq 库当日涨跌家数 + 涨跌中位数实测（change_pct 正负分布，仅做广度）。"""
    out = {"date": None, "up": 0, "down": 0, "flat": 0, "n": 0, "median": None}
    try:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=30)
        d = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        row = con.execute(
            "SELECT COALESCE(SUM(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN change_pct<0 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN change_pct=0 THEN 1 ELSE 0 END),0), COUNT(*) "
            "FROM stock_daily_meta_qfq WHERE date=?", (d,)).fetchone()
        pcts = [r[0] for r in con.execute(
            "SELECT change_pct FROM stock_daily_meta_qfq WHERE date=?", (d,))
            if r[0] is not None]
        con.close()
    except Exception:
        return out
    if row:
        out.update({"date": str(d), "up": int(row[0]), "down": int(row[1]),
                    "flat": int(row[2]), "n": int(row[3])})
        if pcts:
            sp = sorted(pcts)
            m = len(sp) // 2
            out["median"] = sp[m] if len(sp) % 2 else (sp[m - 1] + sp[m]) / 2
    return out


def leader_history(date_str: str, today_leaders: list[str]) -> list[dict]:
    """轮动链：读近3日领涨榜（自维护 json），再写入今日。
    原子写（临时文件 + os.replace），失败打印而非静默，避免 0 字节空文件。"""
    import tempfile
    hist = {}
    if os.path.exists(LEADER_HIST):
        try:
            with open(LEADER_HIST, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = {}
    # 2026-08-24 修复: 申万当日更新失败致空榜时, 防止空榜覆盖已有轮动历史
    if today_leaders:
        hist[date_str] = today_leaders
    else:
        print('[warn] 当日领涨榜为空, 跳过 leader_history 写入(保护既有历史)', file=sys.stderr)
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LEADER_HIST), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LEADER_HIST)
    except Exception as e:
        print(f"  ⚠️ 轮动链历史写入失败: {e}")
    days = sorted(hist.keys())
    return [{"date": d, "leaders": hist[d]} for d in days[-4:]]


def fmt_sw_table(items: list[dict]) -> str:
    if not items:
        return "- 无数据"
    rows = ["| 代码 | 行业 | 收盘 | 当日% | 近5日% |", "|---|---|---|---|---|"]
    for it in items[:10]:
        mom = f"{it['mom5']:+.1f}" if it["mom5"] is not None else "--"
        rows.append(f"| {it['code']} | {it['name']} | {it['close']:.2f} | {it['pct']:+.2f} | {mom} |")
    return "\n".join(rows)


def fmt_em_table(rows: list[dict], top: int = 10, by_inflow: bool = False,
                 reverse: bool = False) -> str:
    if not rows:
        return "- 无数据"
    if by_inflow:
        rows = sorted([r for r in rows if r["inflow"] is not None],
                      key=lambda r: r["inflow"], reverse=not reverse)[:top]
    else:
        rows = sorted([r for r in rows if r["pct"] is not None],
                      key=lambda r: r["pct"], reverse=not reverse)[:top]
    lines = ["| 板块 | 涨跌% | 主力净流入(亿) | 领涨股 |", "|---|---|---|---|"]
    for r in rows:
        infl = r["inflow"] / 1e8 if r["inflow"] is not None else None
        infl_s = f"{infl:+.2f}" if infl is not None else "--"
        pct_s = f"{r['pct']:+.2f}" if r["pct"] is not None else "--"
        lines.append(f"| {r['name']} | {pct_s} | {infl_s} | {r['lead']} |")
    return "\n".join(lines)


def build_daily_review_json(idx: list[dict], l2_rt: list[dict], ind: list[dict],
                            concept: list[dict], seal: dict, breadth: dict,
                            em_date: str, lst: dict) -> dict:
    def _pct(txt: str) -> str:
        return txt

    idx_s = "；".join(f"{m['name']}{m['pct']:+.2f}%" for m in idx if m.get("pct") is not None) or "指数缺失"
    market_env = (f"指数：{idx_s}。涨跌家数（qfq 口径 {breadth.get('date', '')}）："
                  f"涨 {breadth.get('up', 0)} / 跌 {breadth.get('down', 0)}。"
                  f"封板：真封 {seal.get('n_sealed', 0)} / 伪封 {seal.get('n_fake', 0)} / "
                  f"炸板 {seal.get('n_broken', 0)}。"
                  f"涨停归因库口径（剔ST）：涨停 {lst.get('n_limit', 0)} / 最高板 {lst.get('max_board', 0)} / "
                  f"尾盘板 {lst.get('n_afternoon', 0)}。")
    main_sectors = [f"{it['name']}({it['pct']:+.2f}%)" for it in l2_rt[:3]] if l2_rt else []
    main_sectors += [f"{r['name']}({r['pct']:+.2f}%)" for r in
                     sorted(concept, key=lambda r: r["pct"] if r["pct"] is not None else -999,
                            reverse=True)[:2] if r["pct"] is not None]
    risk_warnings = [f"申万领跌：{it['name']}({it['pct']:+.2f}%)" for it in l2_rt[-3:]] if l2_rt else []
    if ind:
        worst = sorted([r for r in ind if r["inflow"] is not None], key=lambda r: r["inflow"])[:2]
        risk_warnings += [f"行业流出：{r['name']}({r['inflow']/1e8:+.2f}亿)" for r in worst]
    if seal.get("n_fake"):
        risk_warnings.append(f"伪封板 {seal['n_fake']} 只（封单不足/情绪转弱信号）")
    if em_date:
        market_env += f"（东财板块口径 {em_date}，可能滞后）"
    return {"market_env": market_env, "main_sectors": main_sectors,
            "risk_warnings": risk_warnings, "space_board": None}


def render_md(date_str: str, idx: list[dict], today_rank: list[dict], rank_src: str,
              l2_rt: list[dict], sw_date: str, ind: list[dict], concept: list[dict],
              em_date: str, seal: dict, breadth: dict, rotation: list[dict],
              lst: dict) -> str:
    d = datetime.strptime(date_str, "%Y%m%d")
    L = []
    L.append(f"# 📈 大板块智能复盘 · {d.strftime('%Y-%m-%d')}（{WEEKDAYS_ZH[d.weekday()]}）")
    L.append("")
    L.append(f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 本报告由脚本产出数据事实，"
             "定性解读由知微在 cron 中补充")
    L.append("")

    L.append("## 一、指数与量能")
    L.append("")
    L.append("| 指数 | 现价 | 涨跌% | 成交额(亿) |")
    L.append("|---|---|---|---|")
    for m in idx:
        amt = m.get("amount_wan")
        amt_s = f"{amt/1e4:.0f}" if amt else "--"
        L.append(f"| {m['name']} | {m['price']:.2f} | {m['pct']:+.2f} | {amt_s} |")
    if breadth.get("date"):
        med_s = (f"，涨跌中位数 {breadth['median']:+.2f}%（qfq 实测）"
                 if breadth.get("median") is not None else "")
        L.append(f"- 全市场涨跌家数（qfq 口径 {breadth['date']}）：涨 {breadth['up']} / 跌 {breadth['down']} / "
                 f"平 {breadth['flat']}（共 {breadth['n']}）{med_s}")
    L.append("")

    L.append(f"## 二、申万一级行业（当日榜 {len(today_rank)} 个，来源：{rank_src}；历史库最新 {sw_date}）")
    L.append("")
    L.append("### 领涨 Top10")
    L.append("")
    L.append(fmt_sw_table(today_rank))
    L.append("")
    L.append("### 领跌 Top10")
    L.append("")
    L.append(fmt_sw_table(list(reversed(today_rank[-10:]))))
    L.append("")

    if l2_rt:
        L.append(f"### 申万二级当日实时榜（akshare，{len(l2_rt)} 个 L2，今日口径）")
        L.append("")
        l2_lines = ["| 二级行业 | 涨跌% |", "|---|---|"]
        for it in l2_rt[:10]:
            l2_lines.append(f"| {it['name']} | {it['pct']:+.2f} |")
        l2_lines.append("")
        l2_lines.append("**领跌：** " + "、".join("%s%+.2f%%" % (it["name"], it["pct"]) for it in l2_rt[-5:]))
        L.append("\n".join(l2_lines))
        L.append("")
    L.append("")

    em_note = f"（东财快照 {em_date}，可能滞后，知微可用 MCP 复核当日）" if em_date else ""
    L.append(f"## 三、东财行业+概念板块{em_note}")
    L.append("")
    L.append("### 行业主力净流入 Top10")
    L.append("")
    L.append(fmt_em_table(ind, 10, by_inflow=True))
    L.append("")
    L.append("### 行业主力净流出 Top10")
    L.append("")
    L.append(fmt_em_table(ind, 10, by_inflow=True, reverse=True))
    L.append("")
    L.append("### 概念涨幅 Top10")
    L.append("")
    L.append(fmt_em_table(concept, 10))
    L.append("")

    L.append("## 四、涨停梯队与情绪（三口径固定并列，2026-09-21 起）")
    L.append("")
    seal_total = seal.get("n_sealed", 0) + seal.get("n_fake", 0) + seal.get("n_broken", 0)
    if seal_total:
        real_rate = seal["n_sealed"] / seal_total * 100 if seal_total else 0
        L.append(f"- **口径A｜seal_snapshot 14:57 快照（宽，含ST/科创）**：涨停 {seal_total} 只 = "
                 f"真封 {seal['n_sealed']}（{real_rate:.0f}%）/ 伪封 {seal['n_fake']} / 炸板 {seal['n_broken']}")
    else:
        L.append("- 口径A｜seal_snapshot：当日无数据（14:57 cron 未跑或非交易日）")
    if lst.get("n_limit") is not None and not lst.get("error"):
        L.append(f"- **口径B｜limit_up_patterns 归因库（剔ST/科创北交，22:00 链日更，库最新 {lst.get('date')}）**："
                 f"涨停 {lst['n_limit']} 只、二板以上 {lst['n_ge2']}、最高板 {lst['max_board']}、"
                 f"尾盘板（首封≥13:00 库内冻结口径）{lst['n_afternoon']} 只、炸板事件 {lst['break_events']} 次、"
                 f"跌停入库 {lst['n_down']} 只")
    else:
        L.append(f"- 口径B｜limit_up_patterns 归因库：无数据（{lst.get('error', '当日未写入')}）")
    L.append("- **口径C｜westock changedist / 同花顺涨停池（MCP 实时）**：由知微定性段复核后并列补充")
    if seal.get("top5"):
        L.append("- 封单额 Top5：")
        for t in seal["top5"]:
            L.append(f"  - {t['name']}（{t['code']}）{t['amount']/1e8:.2f} 亿")
    L.append("")
    L.append("> 💡 真封板率 = 真封/(真封+伪封+炸板)。伪封板 >3 只或真封板率 <50% 提示情绪转弱（seal_snapshot 14:57 口径）")
    L.append(">")
    L.append("> 📌 **固定口径注记（禁混用）**：A 为宽口径（含 ST/科创，总数≈当日触板全景）；B 剔 ST/科创北交、")
    L.append("> 跌停库不含 ST 样本；C 为 MCP 实时口径（westock 含 ST、同花顺池剔 ST）。三口径数字天然有差，")
    L.append("> 任何结论须注明口径；北向资金自 2024/8 起仅披露成交总额（无净买入口径），引用必须标注。")
    L.append("> sentiment_cycle 台账 2026-09-20 起按主人 PD-03 裁决只读封存（falsified #63），口径B 即替代面。")
    L.append("")

    L.append("## 五、板块轮动链（近4个复盘日申万领涨 Top3）")
    L.append("")
    if rotation:
        for r in rotation:
            L.append(f"- **{r['date']}**：{'、'.join(r['leaders'][:3])}")
    else:
        L.append("- 无历史轮动记录")
    L.append("")

    L.append("## 六、今日数据事实（供知微定性解读）")
    L.append("")
    facts = []
    if l2_rt:
        facts.append("今日领涨（二级实时）：" + "、".join("%s%+.2f%%" % (it["name"], it["pct"]) for it in l2_rt[:3]))
        facts.append("今日领跌（二级实时）：" + "、".join("%s%+.2f%%" % (it["name"], it["pct"]) for it in l2_rt[-3:]))
    else:
        facts.append("领涨行业（L1 历史）：" + "、".join("%s%+.2f%%" % (it["name"], it["pct"]) for it in today_rank[:3]))
        facts.append("领跌行业（L1 历史）：" + "、".join("%s%+.2f%%" % (it["name"], it["pct"]) for it in today_rank[-3:]))
    if ind:
        best = sorted([r for r in ind if r["inflow"] is not None], key=lambda r: -r["inflow"])[:3]
        worst = sorted([r for r in ind if r["inflow"] is not None], key=lambda r: r["inflow"])[:3]
        facts.append("行业流入前三：" + "、".join("%s%+.2f亿" % (r["name"], r["inflow"] / 1e8) for r in best))
        facts.append("行业流出前三：" + "、".join("%s%+.2f亿" % (r["name"], r["inflow"] / 1e8) for r in worst))
    for f in facts:
        L.append(f"- {f}")
    L.append("")
    L.append("## ⚠️ 风险提示")
    L.append("- 本报告为数据复盘，非交易指令；数据滞后处已标注，请以 MCP 实时复核为准。")
    L.append("- 涨跌家数来自 qfq 库前复权口径，仅用于广度参考。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="大板块智能复盘数据生成器")
    ap.add_argument("--date", default=_today())
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    date_str = a.date

    # 1 指数
    idx_q = fetch_tencent([c for c, _ in INDEX_MAP])
    idx = []
    for code, name in INDEX_MAP:
        q = idx_q.get(code[-6:]) or {}
        if q:
            idx.append({"name": name, "price": q["price"], "pct": q["pct"],
                        "amount_wan": q.get("amount_wan")})
    # 2 申万 L1（当日实时优先，历史库补动量）
    sw_date = ensure_sw(date_str)
    sw_all, sw_today_hist, _ = sw_l1(date_str)
    mom_map = {it["name"]: it["mom5"] for it in sw_all}
    rt = sw_realtime()
    # L1 当日榜=历史库（官方日线 T+1 滞后）；L2 当日实时榜补充今日口径
    today_rank = [{"code": it["code"], "name": it["name"], "pct": it["pct"],
                   "close": it["close"], "mom5": mom_map.get(it["name"]), "src": "history"}
                  for it in sw_today_hist]
    rank_src = f"历史库（最新 {sw_date}，官方日线T+1滞后）"
    l2_rt = rt  # 124 个二级实时
    # 3 东财板块
    ind, concept, em_date = em_boards()
    # 4 情绪（三口径并列：seal_snapshot 宽口径 + limit_up_patterns 归因库；MCP 口径由知微定性段补）
    seal = seal_stats(date_str)
    lst = limit_stats(date_str)
    breadth = qfq_breadth(date_str)
    # 5 轮动链
    rotation = leader_history(date_str, [it["name"] for it in today_rank[:3]])

    if a.json:
        print(json.dumps({
            "date": date_str,
            "index": idx,
            "sw_date": sw_date, "sw_rank_src": rank_src, "sw_today_count": len(today_rank),
            "sw_top3": [it["name"] for it in today_rank[:3]],
            "sw_bottom3": [it["name"] for it in today_rank[-3:]],
            "l2_realtime_count": len(l2_rt),
            "l2_realtime_top3": [it["name"] for it in l2_rt[:3]] if l2_rt else [],
            "em_date": em_date,
            "seal": seal, "limit_stats": lst, "breadth": breadth,
            "rotation": rotation,
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    md = render_md(date_str, idx, today_rank, rank_src, l2_rt, sw_date, ind, concept, em_date,
                   seal, breadth, rotation, lst)
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, f"sector_review_{date_str}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)

    # 集成点：daily_review JSON（作战清单 read_yesterday_summary 优先消费）
    dj = build_daily_review_json(idx, l2_rt, ind, concept, seal, breadth, em_date, lst)
    # 2026-09-16 增补(规则库§4.2/H-NEW6): 行业量能C1持续放量状态注入作战清单消费面
    # (证据: C1行业fwd20+1.96%/胜率56.5% n=14168; 治"接棒识别滞后"; 只读影子件台账, 不改任何闸门语义)
    try:
        volp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                            "industry_volume_state_latest.json")
        if os.path.exists(volp):
            with open(volp, encoding="utf-8") as vf:
                vs = json.load(vf)
            dj["industry_volume_c1"] = {
                "date": vs.get("date"),
                "c1_count": vs.get("c1_count"),
                "c1_industries": vs.get("c1_industries", []),
                "volr_top3": [{"industry": x.get("industry"), "volr20": x.get("volr20"),
                               "hot5": x.get("hot5")} for x in vs.get("industries", [])[:3]],
                "note": "C1=持续放量(申万一级 volr20>=1.3且近5日4日站上均量; C1行业fwd20+1.96%/56.5%),"
                        "供接棒识别参考; 闸门语义(DD_GATE等)不变",
            }
    except Exception as e:
        dj["industry_volume_c1"] = {"error": f"{type(e).__name__}: {e}"}
    with open(os.path.join(REPORT_DIR, f"daily_review_{date_str}.json"), "w", encoding="utf-8") as f:
        json.dump(dj, f, ensure_ascii=False, indent=2)

    print(f"✅ 板块复盘已生成：{out}")
    print(f"   申万当日榜 {len(today_rank)} 个（{rank_src}，历史库 {sw_date}）｜ "
          f"东财板块 {len(ind)}+{len(concept)}（{em_date}）｜ "
          f"真封板 {seal['n_sealed']} ｜ daily_review json 已写（作战清单集成点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())