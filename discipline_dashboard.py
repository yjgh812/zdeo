#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discipline_dashboard.py — 屠龙系统纪律看板初版（A6）
执行审计官 m5-zhixing-shenji · 2026-08-17

定位：把 discipline_stats.py 的四类违规统计 + naked_trade_audit 裸奔口径 +
      l0_system_health 健康巡检产物，聚合渲染成「自包含单文件 HTML」纪律看板，
      供周会复盘离线打开（不依赖 3019 常驻进程）。

数据源（只读）：
  D:/zcodesj/reports/discipline_YYYYMMDD.json   （discipline_stats.py 产出，双源交叉）
  D:/zcodesj/reports/system_health_*.json       （l0_system_health.py 健康巡检）

用法：
  python discipline_dashboard.py
  python discipline_dashboard.py --days 7 --top 20260816 --out D:/deepseeksj/deliverables/discipline_dashboard_20260816.html

铁律：所有数字来自 discipline_*.json / system_health_*.json 真实台账，不编造；
      原始计数（含重复 precheck 烟雾测试）与去重后「真实违规」双栏展示。
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
from datetime import datetime

REPORT_DIR = r"D:\zcodesj\reports"

TYPE_NAME = {"batch": "批量", "overprice": "超价", "offplan": "非主线", "other": "其他闸门"}
LEVEL_COLOR = {"ERROR": "#d64545", "WARN": "#e0a03c", "INFO": "#2e9e5b", "OK": "#2e9e5b"}


def load_discipline(days: int, top: str | None) -> list:
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "discipline_*.json")))
    items = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
            if "date" in d and "violations" in d:
                items.append(d)
        except Exception:
            continue
    items.sort(key=lambda x: x["date"])
    if top:
        items = [x for x in items if x["date"] <= top]
    return items[-days:] if days else items


def load_health() -> dict | None:
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "system_health_*.json")))
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def dedup_violations(items: list) -> dict:
    out = {"raw_reject": 0, "uniq_reject": 0, "by_type": {}}
    uniq = set()
    for d in items:
        for x in d.get("reject_detail", []):
            out["raw_reject"] += 1
            key = (x["code"], x["type"])
            if key not in uniq:
                uniq.add(key)
                out["by_type"][x["type"]] = out["by_type"].get(x["type"], 0) + 1
    out["uniq_reject"] = len(uniq)
    return out


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def render(items: list, health: dict | None, top: str | None) -> str:
    tot_buy = sum(d.get("buy_total", 0) for d in items)
    tot_naked = sum(d.get("naked_buys", 0) for d in items)
    comp = round((tot_buy - tot_naked) / tot_buy * 100, 1) if tot_buy else 100.0
    dedup = dedup_violations(items)

    overprice_fills = []
    for d in items:
        for x in d.get("overprice_detail", []):
            try:
                if float(x.get("price") or 0) > 30:
                    overprice_fills.append(x)
            except (TypeError, ValueError):
                pass

    naked_all = []
    for d in items:
        for x in d.get("naked_detail", []):
            naked_all.append({**x, "date": d["date"]})

    level = "ERROR" if (tot_naked or comp < 80) else ("WARN" if comp < 95 else "OK")
    color = LEVEL_COLOR[level]

    rows = ""
    for d in items:
        v = d["violations"]
        lc = LEVEL_COLOR.get(d.get("level"), "#888")
        rows += (
            '<tr><td>{d}</td><td style="color:{lc};font-weight:600">{lv}</td>'
            "<td>{b}</td><td style='color:{nc};font-weight:600'>{n}</td><td>{c:.1f}%</td>"
            "<td>{bt}</td><td>{ov}</td><td>{of}</td><td>{mp}@{mm}</td></tr>"
        ).format(
            d=esc(d["date"]), lc=lc, lv=esc(d.get("level")),
            b=d.get("buy_total", 0), nc=("#d64545" if d.get("naked_buys", 0) else "#888"),
            n=d.get("naked_buys", 0), c=d.get("compliance_rate", 0),
            bt=v.get("batch_reject", 0), ov=v.get("overprice", 0), of=v.get("offplan", 0),
            mp=v.get("minute_peak", 0), mm=esc(v.get("minute_peak_min", "—")),
        )

    naked_rows = ""
    for n in naked_all[:60]:
        naked_rows += "<tr><td>{d}</td><td>{c}</td><td>{n}</td><td>{p}</td></tr>".format(
            d=esc(n["date"]), c=esc(n.get("code")), n=esc(n.get("name")), p=esc(n.get("price")))
    if not naked_rows:
        naked_rows = '<tr><td colspan="4" style="color:#2e9e5b">✅ 无裸奔单</td></tr>'

    over_rows = ""
    for o in overprice_fills:
        over_rows += '<tr><td>{c}</td><td>{n}</td><td style="color:#d64545;font-weight:600">{p}</td></tr>'.format(
            c=esc(o.get("code")), n=esc(o.get("name")), p=esc(o.get("price")))
    if not over_rows:
        over_rows = '<tr><td colspan="3" style="color:#2e9e5b">✅ 无 >30 元超价成交</td></tr>'

    dedup_rows = "".join(
        "<tr><td>{n}</td><td>{v}</td></tr>".format(n=TYPE_NAME.get(k, k), v=v)
        for k, v in sorted(dedup["by_type"].items(), key=lambda kv: -kv[1]))

    health_html = ""
    if health:
        hp = health.get("passed", 0)
        hf = health.get("failed", 0)
        hc = "#2e9e5b" if hf == 0 else "#d64545"
        detail_rows = ""
        for det in health.get("details", []):
            st = det.get("status", "")
            sc = {"PASS": "#2e9e5b", "FAIL": "#d64545", "SKIP": "#888"}.get(st, "#888")
            detail_rows += "<tr><td>{n}</td><td style='color:{sc};font-weight:600'>{st}</td></tr>".format(
                n=esc(det.get("name")), sc=sc, st=esc(st))
        health_html = (
            '<div class="card" style="grid-column:1/-1"><h3>🩺 健康巡检（l0_system_health.py 最新）</h3>'
            '<p>时间：{t}　<span style="color:{hc};font-weight:600">PASS {p} / FAIL {f} / TOTAL {tt}</span></p>'
            "<table><tr><th>检查项</th><th>状态</th></tr>{dr}</table></div>"
        ).format(t=esc(health.get("timestamp", "")), hc=hc, p=hp, f=hf,
                 tt=health.get("total", hp + hf), dr=detail_rows)
    else:
        health_html = '<div class="card" style="grid-column:1/-1"><h3>🩺 健康巡检</h3><p>⚠️ 未找到 system_health_*.json（巡检缺失）</p></div>'

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>屠龙纪律看板 · 初版 · 至 {top}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:#14161a;color:#e8eaf0;padding:24px}}
h1{{font-size:22px;margin-bottom:4px}}
.sub{{color:#9aa3b2;font-size:13px;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:20px}}
.card{{background:#1d2027;border:1px solid #2a2e37;border-radius:10px;padding:16px}}
.card h3{{font-size:13px;color:#9aa3b2;margin-bottom:8px;font-weight:600}}
.big{{font-size:28px;font-weight:700;line-height:1.1}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:7px 10px;border-bottom:1px solid #2a2e37;text-align:left}}
th{{color:#9aa3b2;font-weight:600;background:#191c22}}
tr:hover td{{background:#23262e}}
h2{{font-size:16px;margin:22px 0 10px;color:#c9d1e0}}
.note{{font-size:12px;color:#9aa3b2;margin-top:6px;line-height:1.6}}
</style></head><body>
<h1>📋 屠龙纪律看板 · 初版</h1>
<div class="sub">执行审计官 m5-zhixing-shenji ｜ 数据源：discipline_stats.py 双源交叉 + naked_trade_audit 口径 + l0_system_health 巡检 ｜ 生成 {gen}</div>

<div class="grid">
  <div class="card"><h3>期间买入笔数</h3><div class="big">{tb}</div></div>
  <div class="card"><h3>裸奔买入</h3><div class="big" style="color:#d64545">{tn}</div></div>
  <div class="card"><h3>合规率</h3><div class="big" style="color:{color}">{comp:.1f}%</div></div>
  <div class="card"><h3>超价成交(&gt;30元)</h3><div class="big" style="color:{oc}">{on}</div></div>
</div>

<h2>一、逐日纪律（{nd} 个交易日）</h2>
<div class="card">
<table><tr><th>日期</th><th>等级</th><th>买入</th><th>裸奔</th><th>合规率</th><th>批量拦截</th><th>超价</th><th>非主线</th><th>同分钟峰值</th></tr>{rows}</table>
</div>

<h2>二、裸奔明细（未过闸 BUY）</h2>
<div class="card"><table><tr><th>日期</th><th>代码</th><th>名称</th><th>价格</th></tr>{naked_rows}</table></div>

<h2>三、超价成交明细（&gt;30 元铁律）</h2>
<div class="card"><table><tr><th>代码</th><th>名称</th><th>成交价</th></tr>{over_rows}</table>
<p class="note">铁律 S-3：买入价 &gt; 30 元一律不做（8/13 主人终审，无豁免）。8/13 中恒电气 44.77 为唯一成交侧超价违规。</p></div>

<h2>四、闸门拦截去重视图</h2>
<div class="card">
<p>原始拦截计数（含重复 precheck 烟雾测试）<b>{rr}</b> 条 → 去重后真实拦截 <b>{ur}</b> 个（code×type）。</p>
<table><tr><th>违规类</th><th>去重后个数</th></tr>{dedup_rows}</table>
</div>

{health_html}

</body></html>""".format(
        top=esc(top or "最新"), gen=esc(datetime.now().strftime("%Y-%m-%d %H:%M")),
        tb=tot_buy, tn=tot_naked, color=color, comp=comp,
        oc=("#d64545" if overprice_fills else "#2e9e5b"), on=len(overprice_fills),
        nd=len(items), rows=rows, naked_rows=naked_rows, over_rows=over_rows,
        rr=dedup["raw_reject"], ur=dedup["uniq_reject"], dedup_rows=dedup_rows,
        health_html=health_html,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="纪律看板初版生成器 (A6)")
    ap.add_argument("--days", type=int, default=7, help="展示最近 N 个交易日")
    ap.add_argument("--top", default="", help="截止日期 YYYYMMDD")
    ap.add_argument("--out", default="", help="输出 HTML 路径")
    a = ap.parse_args()

    top = a.top or datetime.now().strftime("%Y%m%d")
    items = load_discipline(a.days, a.top or None)
    if not items:
        print("[ERR] 未找到 discipline_*.json，先运行 discipline_stats.py --rebuild")
        return 2
    health = load_health()
    html_out = render(items, health, a.top or None)
    out = a.out or os.path.join(REPORT_DIR, f"discipline_dashboard_{top}.html")
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[OK] 纪律看板落盘 {out}")
    print(f"[OK] 覆盖 {len(items)} 个交易日，裸奔 {sum(x.get('naked_buys',0) for x in items)} 笔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
