#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_feed.py · 画像系统→屠龙生产链 唯一对接适配器（2026-09-05 主人"全面对接"裁决落地）。

定位（单一owner）：
  生产链任何消费方（白名单标注/AB台账/巡检/晨报）只读本脚本产出的标准化快照
  data/portrait_feed.json，不直接读 portrait_agent 内部文件——画像内部结构变更不冲击生产链。

快照合同（data/portrait_feed.json）：
  {
    "snapshot_date": "20260904",      # 画像截面日
    "built_at": ISO时间,              # 快照生成时间
    "freshness": {"ok": true, "lag_days": 1, "note": ""},
    "shadow": {"phase": "low", "weight": 0.15},   # 影子灰度记账(画像内部口径, 非生产资金权重)
    "scores_short": {"600363": {"prob": 0.49, "pct": 96.2}, ...},   # 全截面分位(0~100)
    "topn_short": ["code", ...],                    # Top500 候选池(20260913主人裁决放宽)
    "scores_mid": {...}, "topn_mid": [...],         # mid=无信号实证(20260905体检), 仅观察级透出
    "unavailable": null                             # 结构化不可用原因(fail-closed时非null)
  }

fail-closed：画像文件缺失/超龄(>5自然日)/解析失败 → unavailable 非null + 退出码1，消费方按"无画像"处理。
纯标准库。用法：
  python portrait_feed.py build     # 生成/刷新快照(白名单/巡检前跑; 幂等)
  python portrait_feed.py show      # 查看快照摘要
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime, timedelta

from portrait_manifest import write_manifest

sys.stdout.reconfigure(encoding="utf-8")

BASE = r"D:\zcodesj"
PORTRAIT_DIR = os.path.join(BASE, "portrait_agent", "reports")
STATE_F = os.path.join(BASE, "portrait_agent", "models", "shadow_state.json")
OUT_F = os.path.join(BASE, "data", "portrait_feed.json")
WINNER5_F = os.path.join(BASE, "data", "universe_portrait_winner5_latest.json")
MAX_AGE_DAYS = 5  # 截面日距今天然日上限(周末+停牌容错), 超龄=结构化不可用


def _latest(pattern: str) -> tuple[str | None, str]:
    files = sorted(glob.glob(os.path.join(PORTRAIT_DIR, pattern)))
    if not files:
        return None, ""
    path = files[-1]
    name = os.path.basename(path)
    date = name.replace(pattern.replace("*", ""), "").split("_")[0]
    return date, path


def _load_scores(tag: str, date: str | None) -> dict[str, dict]:
    """读 portrait_scores_{date}_{tag}.csv → {code: {prob, pct}} 分位0~100。"""
    pat = f"portrait_scores_*_{tag}.csv"
    files = sorted(glob.glob(os.path.join(PORTRAIT_DIR, pat)))
    if date:
        files = [f for f in files if date in os.path.basename(f)]
    if not files:
        return {}
    out: dict[str, dict] = {}
    with open(files[-1], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    probs = sorted(float(r["prob"]) for r in rows)
    n = len(probs)
    if n == 0:
        return {}
    for r in rows:
        code = str(r["code"]).strip().zfill(6)
        prob = float(r["prob"])
        # 分位 = 严格小于该分数的占比×100(截面内相对位置, 不依赖分数绝对刻度)
        pct = sum(1 for p in probs if p < prob) / n * 100.0
        out[code] = {"prob": round(prob, 4), "pct": round(pct, 1)}
    return out


def build() -> int:
    # 批1' 元数据(2026-10-11): schema/owner/permissions 零交易权决策位为静态政策, 与数据可用性无关恒写入
    snap: dict = {"schema_version": "portrait.feed.v1.1", "owner": "portrait_feed",
                  "built_at": datetime.now().isoformat(timespec="seconds"),
                  "permissions": {"display": True, "research_shadow": True, "ranking": False,
                                  "filtering": False, "pre_trade_gate": False,
                                  "trade_execution": False},
                  "freshness": {"ok": False, "lag_days": None, "note": ""}, "unavailable": None}
    date, path = _latest("portrait_????????.json")
    if not path:
        snap["unavailable"] = "无画像截面文件(portrait_*.json)"
    else:
        try:
            with open(path, encoding="utf-8") as f:
                pj = json.load(f)
            snap_date = str(pj.get("date", date))
            lag = (datetime.now() - datetime.strptime(snap_date, "%Y%m%d")).days
            if lag > MAX_AGE_DAYS:
                snap["unavailable"] = f"画像截面超龄({snap_date}, lag={lag}d>{MAX_AGE_DAYS}d)"
            else:
                snap["snapshot_date"] = snap_date
                snap["freshness"] = {"ok": True, "lag_days": lag,
                                     "note": "" if lag == 0 else f"T-{lag}口径"}
                # 批1' 血统元数据(2026-10-11): qfq_max=截面日(22:50 infer 在 23:00 qfq 链前, T-1 口径)
                snap["qfq_max_date"] = snap_date
                snap["lineage"] = {"generator": "portrait_feed.py",
                                   "section_source": f"portrait_agent/reports/portrait_{snap_date}.json",
                                   "note": "独立qfq新鲜度复核见health_check qfq探针(不同源双查)"}
                snap["not_a_recommendation"] = True
                snap["model_status"] = "observation_only(falsified#49: 可交易alpha=无, TopN仅观察池)"
                snap["n_universe"] = pj.get("n")
                snap["scores_short"] = _load_scores("short", snap_date)
                snap["scores_mid"] = _load_scores("mid", snap_date)
                # 名单宽度Top500(2026-09-13主人裁决"放宽到Top500"; 常量=model_v1.PORTFOLIO_TOPN单一真源)
                # 2026-09-25 治理债务#3收口: 历史截面已全量统一topn_list(抽验10文件), 过渡期top20双读退役;
                # topn_list缺失=infer版本回退的结构性异常 → 抛错走fail-closed(禁静默空名单)
                def _pick(tag):
                    seg = pj.get("models", {}).get(tag, {})
                    lst = seg.get("topn_list")
                    if not lst:
                        raise ValueError(f"截面json缺topn_list({tag}; infer版本过旧或结构回退)")
                    return [x["code"] for x in lst]
                snap["topn_short"] = _pick("short")
                snap["topn_mid"] = _pick("mid")
                # P4裁决(2026-09-05主人"全部"): y_mid降纯观察——早停1~7树/负RankIC双重实证无信号,
                # mid 仅观察级透出, 禁入任何对接决策(标注/AB/闸门), 修复须新指纹重新登记
                snap["mid_status"] = "observation_only(无信号实证20260905, 禁入对接决策)"
                if not snap["scores_short"]:
                    snap["unavailable"] = "全截面分数文件缺失(需先跑 portrait_infer.py 新版)"
        except Exception as e:
            snap["unavailable"] = f"画像文件解析失败({type(e).__name__}: {e})"
    # 影子状态(独立于截面, 单独容错)
    try:
        with open(STATE_F, encoding="utf-8") as f:
            st = json.load(f)
        snap["shadow"] = {"phase": st.get("phase"), "weight": st.get("weight"),
                          "history_n": len(st.get("history", []))}
    except Exception as e:
        snap["shadow"] = {"phase": None, "weight": None, "note": f"状态读取失败({e})"}
    os.makedirs(os.path.dirname(OUT_F), exist_ok=True)
    with open(OUT_F, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    write_manifest("portrait_feed", {"data/portrait_feed.json": OUT_F})
    if snap["unavailable"]:
        print(f"[feed] ❌ 画像快照不可用: {snap['unavailable']} (fail-closed, 消费方按无画像处理)")
        return 1
    print(f"[feed] ✅ 快照 data/portrait_feed.json: 截面{snap['snapshot_date']} "
          f"lag={snap['freshness']['lag_days']}d 影子={snap['shadow'].get('phase')}"
          f"({snap['shadow'].get('weight')}) short分数{len(snap['scores_short'])}只 "
          f"TopN={len(snap['topn_short'])}只")
    return 0


def winner5_tags_view():
    """赢家五维标签只读视图（2026-10-09 A呈现级补全, 主人令「选股时用到画像」A+B组合）。

    数据源 = WINNER5_F（owner=analysis/winner_profile_dims.py, 08:30 automation 日更）。
    不并入本适配器 22:50 build 快照——该件更新晚于 build, 并入会恒滞后一日; 且该件非
    portrait_agent 内部件, 直读不违 9/5 feed-only 规则。
    返回 ({"asof": "YYYYMMDD", "tags": {code6: "主线·深位·2-3板·人气20"}}, "")；
    失败 → (None, err)。零拦截: 仅呈现标注, 禁止参与任何排序/过滤/闸门(标签层铁律 2026-10-07)。
    """
    try:
        with open(WINNER5_F, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        return None, f"winner5不可读:{e}"
    asof = str(doc.get("asof", "?"))
    tags: dict[str, str] = {}
    for r in doc.get("rows", []):
        code = str(r.get("code", "")).zfill(6)
        if not code:
            continue
        parts = []
        ml = str(r.get("w_mainline", ""))
        if ml in ("主线", "主升主线"):
            parts.append(ml)
        st = str(r.get("w_stage", "N/A"))
        if st and st != "N/A":
            parts.append(st)
        pb = str(r.get("w_posband", ""))
        if pb and "N/A" not in pb:
            parts.append(pb.split("(")[0])
        zt = str(r.get("w_zt10", "无"))
        if zt and zt != "无":
            parts.append(zt)
        pop = str(r.get("w_pop", "未上榜"))
        if pop.startswith("热榜第"):
            parts.append("人气" + pop.replace("热榜第", "").replace("名", ""))
        if parts:
            tags[code] = "·".join(parts)
    return {"asof": asof, "tags": tags}, ""


def show() -> int:
    if not os.path.exists(OUT_F):
        print("快照不存在, 先 build"); return 1
    with open(OUT_F, encoding="utf-8") as f:
        s = json.load(f)
    print(json.dumps({k: v for k, v in s.items()
                      if k not in ("scores_short", "scores_mid")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="画像→屠龙对接适配器")
    p.add_argument("cmd", choices=["build", "show"])
    sys.exit(build() if p.parse_args().cmd == "build" else show())
