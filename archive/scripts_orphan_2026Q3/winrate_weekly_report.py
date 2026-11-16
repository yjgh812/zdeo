# -*- coding: utf-8 -*-
"""胜率周报四件套(P3, WINRATE_UPGRADE_PLAN_20260829 §P3)·周五复盘消费件

一键输出一周(默认最近5交易日)生产口径四件套:
  1) 生产买入胜率与超额(D+1/D+3, 剔除 [experiment] 标注, 对市场等权)
  2) 执行溢价(实盘买价 vs 当日 OHLC 均值, P1-1 纪律的验收指标)
  3) 名单健康度(调 bp_ep_list_health 逻辑摘要) 与漂移(调 bp_ep_drift 产物)
  4) 改价建议(reprice_advice 最新产物统计)
用法: python winrate_weekly_report.py [--days 5]
产物: reports/WINRATE_WEEKLY_YYYYMMDD.md(直接可贴周五复盘)
"""
from __future__ import annotations
import argparse
import csv
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

BASE = r"D:\zcodesj"
ATTR = os.path.join(BASE, "attribution", "attribution.db")
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT_DIR = os.path.join(BASE, "reports")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5, help="回看交易日数(默认5)")
    a = ap.parse_args()

    qconn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True, timeout=10)
    qconn.row_factory = sqlite3.Row
    qcur = qconn.cursor()
    qcur.execute("SELECT code, date, open, high, low, close FROM stock_daily_meta_qfq WHERE date >= '20260801'")
    px, cals = defaultdict(dict), set()
    for r in qcur.fetchall():
        px[r["code"]][r["date"]] = (r["open"], r["high"], r["low"], r["close"])
        cals.add(r["date"])
    cal = sorted(cals)
    idx = {d: i for i, d in enumerate(cal)}
    window = cal[-a.days:]
    since = window[0]

    conn = sqlite3.connect(ATTR)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""SELECT trade_date, code, name, price, notes FROM trade_records
                   WHERE action='BUY' AND trade_date >= ? ORDER BY trade_date, id""", (since,))
    buys = [dict(r) for r in cur.fetchall()]

    def fwd(code, bd, bp_, n):
        qc = code[2:] if code[:2] in ("sz", "sh", "bj") else code
        d = px.get(qc, {})
        if bd not in idx:
            return None
        j = idx[bd] + n
        if j >= len(cal):
            return None
        if cal[j] not in d:
            return None
        return (d[cal[j]][3] / bp_ - 1) * 100

    def mkt(bd, n):
        if bd not in idx:
            return None
        j = idx[bd] + n
        if j >= len(cal):
            return None
        rets = [(px[c][cal[j]][3] / px[c][bd][3] - 1) * 100 for c in px
                if bd in px[c] and cal[j] in px[c]]
        return sum(rets) / len(rets) if rets else None

    prod_all, exp_cnt = [], 0
    prem = []
    # 2026-08-29 P0-A: 名单内/外分口径(PROVEN∪EXPERIMENTAL)——量化名单外清零的预期基线
    from _common import PROVEN_EDGE_SOURCES, EXPERIMENTAL_SOURCES
    _PROD = PROVEN_EDGE_SOURCES | EXPERIMENTAL_SOURCES
    # 2026-08-29 P0-A: 名单内/外分口径——最贴合 P0-A 语义=该 code 是否曾入窗口内白名单文件
    # (subsystems 是补录标注非下单 source, 不能用它判; 白名单=生产允许买入集合)
    _wl_codes = set()
    for _f in sorted(glob.glob(os.path.join(REPORT_DIR, "buy_whitelist_2026*.csv")))[-max(a.days, 6):]:
        try:
            with open(_f, encoding="utf-8-sig") as _cf:
                for _r in csv.DictReader(_cf):
                    _c6 = str(_r.get("code", ""))[-6:]
                    if _c6.isdigit():
                        _wl_codes.add(_c6)
        except Exception:
            continue
    def _norm6(c):
        return "".join(ch for ch in c if ch.isdigit())[-6:]
    _wl_src = {}
    for b in buys:
        if "[experiment" in (b.get("notes") or ""):
            continue
        _wl_src[b["code"]] = _norm6(b["code"]) in _wl_codes
    for b in buys:
        note = b.get("notes") or ""
        if "[experiment" in note:
            exp_cnt += 1
            continue
        qc = b["code"][2:] if b["code"][:2] in ("sz", "sh", "bj") else b["code"]
        d = px.get(qc, {})
        if b["trade_date"] in d:
            o, h, l, c = d[b["trade_date"]]
            vp = (o + h + l + c) / 4
            if vp > 0:
                prem.append((b["price"] / vp - 1) * 100)
        for n in (1, 3):
            f = fwd(b["code"], b["trade_date"], b["price"], n)
            if f is not None:
                m = mkt(b["trade_date"], n)
                prod_all.append((n, f, f - m if m is not None else None))

    lines = [f"# 胜率周报(生产口径) · {window[0]}~{window[-1]}",
             "", f"生成: {datetime.now().isoformat(timespec='seconds')} | 脚本: winrate_weekly_report.py", ""]
    lines.append("## 1) 生产买入胜率与超额(剔 experiment)")
    for n in (1, 3):
        vals = [(f, e) for nn, f, e in prod_all if nn == n]
        if vals:
            fs = [v[0] for v in vals]
            es = [v[1] for v in vals if v[1] is not None]
            win = sum(1 for f in fs if f > 0)
            lines.append(f"- D+{n}: n={len(fs)} 胜率{win/len(fs)*100:.0f}% 均值{sum(fs)/len(fs):+.2f}% "
                         f"超额{sum(es)/len(es):+.2f}pp (实验单另计 {exp_cnt} 笔)")
    # 名单内 vs 名单外(D+3, P0-A 基线; 2026-08-29)
    _wl3, _ol3 = [], []
    for b in buys:
        if "[experiment" in (b.get("notes") or ""):
            continue
        f3 = fwd(b["code"], b["trade_date"], b["price"], 3)
        if f3 is None:
            continue
        (_wl3 if _wl_src.get(b["code"], False) else _ol3).append(f3)
    if _wl3:
        w_win = sum(1 for v in _wl3 if v > 0)
        lines.append(f"- 名单内(PROVEN∪EXP): n={len(_wl3)} 胜率{w_win/len(_wl3)*100:.0f}% "
                     f"D+3均值{sum(_wl3)/len(_wl3):+.2f}%")
    if _ol3:
        o_win = sum(1 for v in _ol3 if v > 0)
        lines.append(f"- 名单外(目标清零): n={len(_ol3)} 胜率{o_win/len(_ol3)*100:.0f}% "
                     f"D+3均值{sum(_ol3)/len(_ol3):+.2f}%")
    lines.append("")
    lines.append("## 2) 执行溢价(P1-1 验收指标, 目标≤+0.2%)")
    if prem:
        prem.sort()
        lines.append(f"- n={len(prem)} 均值{sum(prem)/len(prem):+.2f}% 中位{prem[len(prem)//2]:+.2f}% "
                     f"买贵占比{sum(1 for v in prem if v > 0)/len(prem)*100:.0f}% 最贵{max(prem):+.2f}%")
    lines.append("")
    lines.append("## 3) 名单健康度与漂移(P2-D/P2-A)")
    hh = sorted(glob.glob(os.path.join(REPORT_DIR, "bp_ep_list_health_*.json")))
    if hh:
        h = json.load(open(hh[-1], encoding="utf-8"))
        lines.append(f"- 健康度: {h.get('health')} (近{len(h.get('recent_lists', []))}期平均超额 {h.get('avg_excess_pp', 'NA')}pp)")
        # P2-D 试点标记(2026-08-29): RED=开关期(若 TULONG_HEALTH_TIER=1 激活), 对比开关/全额期生产超额
        _tier_on = os.environ.get("TULONG_HEALTH_TIER") == "1"
        lines.append(f"- P2-D 试点: 健康度={'RED→开关期' if h.get('health') == 'RED' else '非RED→全额期'}"
                     f"{' (TULONG_HEALTH_TIER=1 已激活)' if _tier_on else ' (开关未激活, 默认全额期)'}")
        if h.get("health") == "RED" and _tier_on:
            lines.append("  ⚠️ 本周=开关期(全名单tier减半已生效), 试点窗口开启——"
                         "后续周报需记录开关期 vs 全额期生产D+3超额对比(四周后裁决接线)")
    dd = sorted(glob.glob(os.path.join(REPORT_DIR, "bp_ep_drift_*.json")))
    if dd:
        d_ = json.load(open(dd[-1], encoding="utf-8"))
        dr = d_.get("drift", {})
        sa = d_.get("sector_alarm", {})
        lines.append(f"- 漂移: 跌破MA20 {dr.get('below_ma20_cnt')}/{d_.get('members_n')} "
                     f"(drift {dr.get('drift_score_pct')}%) | 行业告警: "
                     f"{'触发 ' + str(sa.get('top_industry')) + ' ' + str(sa.get('share')) + '%' if sa.get('triggered') else '无'}")
    lines.append("")
    lines.append("## 4) 改价建议统计(P1-2 v1)")
    rr = sorted(glob.glob(os.path.join(REPORT_DIR, "reprice_advice_*.json")))
    if rr:
        r_ = json.load(open(rr[-1], encoding="utf-8"))
        vc = defaultdict(int)
        for adv in r_.get("advice", []):
            vc[adv.get("verdict")] += 1
        lines.append(f"- {os.path.basename(rr[-1])}: 未决买单{r_.get('pending_buys')} "
                     + " ".join(f"{k}={v}" for k, v in sorted(vc.items())))
    else:
        lines.append("- 本周无 reprice_advice 产物(周六生成, 周一起有)")
    lines.append("")
    lines.append("## 指标对照(计划§4)")
    lines.append("- 生产D+3超额目标: 9月≥-1.5pp / 10月≥0 | 执行溢价≤+0.2% | 名单外生产买入=0")
    out = os.path.join(REPORT_DIR, f"WINRATE_WEEKLY_{window[-1]}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n产物: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
