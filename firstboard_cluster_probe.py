#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首板集群共振探测器 firstboard_cluster_probe v1 (2026-09-09)

起因（主人红棉复盘裁决）：9/8 糖链五股集体首板（红棉/中粮科技/保龄宝/中粮糖业/华康），
9/9 盘前系统零感知——盘感卡片判"涨停高度分散"、主线雷达零候选（其 CATALYST_THEME_MAP
为人工关键词表，新主题未登记即盲区）、白名单行无主线归属。本模块提供**数据驱动的
首板共振发现**：不依赖人工主题表，直接从涨停归因库按行业聚类，同行业≥N 只涨停且
首板≥M 只即判共振，盘前高亮"昨天发生了什么值得搞明白"。

信号定义（机械可测）：
  共振簇 = 同 industry 当日涨停 >= CLUSTER_MIN 只 且 其中 pattern_type 含"首板" >= FIRSTBOARD_MIN 只
  连续共振 = 前一交易日同 industry 涨停数亦 >= CLUSTER_MIN（加速）；
  新共振   = 前一交易日 < CLUSTER_MIN（首日启动，最高关注）

数据源 = attribution.db limit_up_patterns（涨停归因唯一真源，人工复盘行业口径；
  9/8 实锚：feature_industry_momentum 对改名股错标行业——000523 标"银行"——禁用）。
诚实边界：表内无当日数据 → fail-open 写 data_date 标注并 exit 0（感知层不阻断链）；
  行业口径为复盘人工归类，粒度粗于主题语义（如"食品"簇≠糖链全部），成员归因看 core_driver。

用法：
  python firstboard_cluster_probe.py            # 缺省=库内最新交易日
  python firstboard_cluster_probe.py --date 20260908

接线：daily_chain run_aux [cluster]（22:00 盘后）→ 09:10 链读
  reports/firstboard_cluster_latest.json 高亮汇报。
铁律边界（宪法 §10）：纯感知零交易权，共振提示不构成买入名单。
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ATTR_DB = ROOT / "attribution" / "attribution.db"
OUT_DIR = ROOT / "reports"

# 参数（fingerprint 入报告；变更须留痕）
CLUSTER_MIN = 3      # 同行业当日涨停数下限
FIRSTBOARD_MIN = 2   # 其中首板数下限（主线启动的第一信号=广度非高度）
THEME_MIN = 3        # 主题标签(core_driver "主线"段)跨行业共现下限（9/9 预演补层：
                     # 同行业口径抓不住主题级扩散——农化横跨基础化工/农林牧渔/食品饮料）


def _out(m):
    print(m, flush=True)


def load_day(con, date):
    rows = con.execute(
        "SELECT code, name, industry, limit_count, pattern_type, first_limit_time, "
        "core_driver, primary_attribution FROM limit_up_patterns WHERE trade_date=?",
        (date,)).fetchall()
    rows = [dict(zip(("code", "name", "industry", "limit_count", "pattern_type",
                      "first_limit_time", "core_driver", "primary_attribution"), r)) for r in rows]
    # 2026-09-10 B4 修复: 同日同码双行(engine_v2 行+复盘行, 9/9 有 48 对并存)按行计数
    # 曾致成员翻倍——14 簇中 11 簇是 2 只巧合被翻倍触发的假簇(9/10 盘前清单已渲染
    # 重复成员「000930中粮科技 000930中粮科技」)。按 code 去重: 复盘行优先(核心字段
    # pattern_type/core_driver 更全), engine_v2 行兜底。
    best = {}
    for r in rows:
        c = r["code"]
        if c not in best or (best[c].get("pattern_type") or "") == "engine_v2":
            best[c] = r
    return list(best.values())


def build_clusters(day_rows, prev_counts):
    by_ind = {}
    for r in day_rows:
        by_ind.setdefault(r["industry"] or "未分类", []).append(r)
    clusters = []
    for ind, members in sorted(by_ind.items(), key=lambda kv: -len(kv[1])):
        if len(members) < CLUSTER_MIN:
            continue
        # 首板判定: 复盘口径看 pattern_type; 引擎行 pattern_type='engine_v2' 无首板字样,
        # 兜底 limit_count==1 (2026-09-09 实锚: 引擎口径日首板判定失效→0簇)
        firstboards = [m for m in members
                       if "首板" in (m["pattern_type"] or "")
                       or str(m.get("limit_count")) == "1"]
        if len(firstboards) < FIRSTBOARD_MIN:
            continue
        # core_driver 关键词共现（长度≥2的词在≥2只成员出现→主题语义线索）
        kw = {}
        for m in members:
            seen = set()
            for seg in (m["core_driver"] or "").replace("｜", "+").split("+"):
                seg = seg.strip()
                if len(seg) >= 2 and seg not in seen:
                    seen.add(seg)
                    kw[seg] = kw.get(seg, 0) + 1
        theme_kw = sorted((k, v) for k, v in kw.items() if v >= 2 and k != ind)
        prev = prev_counts.get(ind, 0)
        clusters.append({
            "industry": ind,
            "total": len(members),
            "firstboards": len(firstboards),
            "prev_total": prev,
            "status": "连续共振" if prev >= CLUSTER_MIN else "新共振",
            "theme_keywords": [k for k, _ in theme_kw[:6]],
            "members": [{
                "code": m["code"], "name": m["name"],
                "pattern_type": m["pattern_type"], "limit_count": m["limit_count"],
                "first_limit_time": m["first_limit_time"], "core_driver": m["core_driver"],
            } for m in sorted(members, key=lambda x: (x["limit_count"] or 0), reverse=True)],
        })
    return clusters


def build_theme_clusters(day_rows):
    """主题聚类（第二层，2026-09-09 补）：复盘口径 core_driver 的"主线"段为结构化
    主题标签（如"农业农化主线"），跨行业共现 >= THEME_MIN 只即主题簇——
    同行业口径抓"窄行业首板广度"，本层抓"主题级扩散"（农化横跨基础化工/
    农林牧渔/食品饮料三级行业的 9/9 形态）。每股归入其首个主题标签防重复计数。"""
    by_theme = {}
    for m in day_rows:
        theme = None
        for seg in (m["core_driver"] or "").replace("/", "+").replace("｜", "+").split("+"):
            seg = seg.strip()
            if seg.endswith("主线"):
                theme = seg
                break
        if theme:
            by_theme.setdefault(theme, []).append(m)
    out = []
    for theme, members in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
        if len(members) < THEME_MIN:
            continue
        firstboards = [m for m in members if "首板" in (m["pattern_type"] or "")]
        inds = sorted({m["industry"] or "未分类" for m in members})
        out.append({
            "type": "主题",
            "industry": theme,
            "total": len(members),
            "firstboards": len(firstboards),
            "prev_total": None,  # 主题簇跨行业，前值对齐交给行业层
            "status": "主题簇",
            "industries_spanned": inds,
            "theme_keywords": sorted(inds)[:0] or [],  # 主题名即语义，不再抽词
            "members": [{
                "code": m["code"], "name": m["name"],
                "pattern_type": m["pattern_type"], "limit_count": m["limit_count"],
                "first_limit_time": m["first_limit_time"], "core_driver": m["core_driver"],
            } for m in sorted(members, key=lambda x: (x["limit_count"] or 0), reverse=True)],
        })
    return out


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="首板集群共振探测器 v1（感知层零交易权）")
    ap.add_argument("--date", help="YYYYMMDD，缺省=库内最新")
    a = ap.parse_args()
    t0 = time.time()
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    try:
        dates = [int(r[0]) for r in con.execute(
            "SELECT DISTINCT trade_date FROM limit_up_patterns ORDER BY trade_date DESC LIMIT 2")]
        if not dates:
            _out("[cluster] ERROR limit_up_patterns 为空（归因断链），fail-open 退出")
            return 0
        date = int(a.date.replace("-", "")) if a.date else dates[0]
        day_rows = load_day(con, date)
        if not day_rows:
            _out(f"[cluster] WARN {date} 无涨停归因数据（表停更或非交易日），fail-open 退出")
            return 0
        prev_dates = [d for d in dates if d < date]
        prev_counts = {}
        if prev_dates:
            # 2026-09-10 B4 同族修复: COUNT(*) → COUNT(DISTINCT code), 昨日基线同样
            # 按唯一码计数(engine_v2+复盘双行并存的翻倍失真同源)
            for ind, n in con.execute(
                "SELECT industry, COUNT(DISTINCT code) FROM limit_up_patterns WHERE trade_date=? "
                "GROUP BY industry", (prev_dates[0],)):
                prev_counts[ind or "未分类"] = n
        clusters = build_clusters(day_rows, prev_counts)
        theme_clusters = build_theme_clusters(day_rows)
        report = {
            "schema": "firstboard_cluster_v1",
            "trade_date": date,
            "data_date": date,
            "params_fp": f"cluster_min={CLUSTER_MIN}|firstboard_min={FIRSTBOARD_MIN}|theme_min={THEME_MIN}",
            "prev_trade_date": prev_dates[0] if prev_dates else None,
            "n_limitup": len(day_rows),
            "n_clusters": len(clusters),
            "clusters": clusters,
            "theme_clusters": theme_clusters,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        con.close()
    OUT_DIR.mkdir(exist_ok=True)
    dated = OUT_DIR / f"firstboard_cluster_{date}.json"
    latest = OUT_DIR / "firstboard_cluster_latest.json"
    for p in (dated, latest):
        p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    _out(f"[cluster] {date}: 涨停{len(day_rows)}只 → 行业共振簇{len(clusters)}+主题簇{len(theme_clusters)} "
         f"(阈值 行业>={CLUSTER_MIN}且首板>={FIRSTBOARD_MIN}; 主题>={THEME_MIN}) → {latest.name} ({time.time()-t0:.1f}s)")
    for c in clusters:
        _out(f"[cluster]   {c['status']} {c['industry']} {c['total']}只(首板{c['firstboards']}, 昨{c['prev_total']}) "
             f"关键词:{'/'.join(c['theme_keywords'][:4]) or '-'} | "
             + " ".join(f"{m['code']}{m['name']}" for m in c["members"][:6]))
    for c in theme_clusters:
        _out(f"[cluster]   主题[{c['industry']}] {c['total']}只(首板{c['firstboards']}) "
             f"跨{'/'.join(c['industries_spanned'][:4])} | "
             + " ".join(f"{m['code']}{m['name']}" for m in c["members"][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
