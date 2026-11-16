#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自选池结构监视线 v1 (2026-11-11)

修复缺口（主人令「修复优化迭代」）：自选池此前只是名单存储不是信号输入，
人盯的票系统不盯。起因案例=日发精机 002520。
纯感知零交易权；fail-closed；价格源=qfq 库只读。
"""
import argparse
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj\attribution")
from stage_locator import locate

ROOT = r"D:\zcodesj"
CODES_FILE = os.path.join(ROOT, "data", "watchlist_codes_latest.json")
LEDGER = os.path.join(ROOT, "data", "watchlist_structure_ledger.jsonl")
BRIEF = os.path.join(ROOT, "data", "watchlist_structure_brief_latest.txt")
PORTRAIT = os.path.join(ROOT, "data", "portrait_scores_history.jsonl")
STDOUT_LOG = os.path.join(ROOT, "data", "watchlist_structure_stdout.log")
PORTRAIT_BRIDGE_PCT = 75.0  # 暗区观察桥阈值(主人拍板 2026-11-11)

EVENT_META = {
    "NEW_ANCHOR": "新锚出现（今日涨停=结构起点诞生）",
    "BREAK_ANCHOR": "破锚（结构死, 卖出须问主人）",
    "RECOVER": "破锚后收复（回锚上）",
    "SHOCK_EVENT": "急跌震仓事件（次日判别: 收复=持有, 未收复=离场评估）",
    "OVERHEAT_ON": "追高区（锚上+10%以上, 禁追, 持有者盯派发）",
    "DIST_WARNING_ON": "派发预警(UNVERIFIED, 放量阴线/滞涨)",
    "STAGE_CHANGE": "阶段迁移",
}


def load_codes():
    if not os.path.exists(CODES_FILE):
        print("[FAIL-CLOSED] 名单文件缺失: %s" % CODES_FILE)
        return None
    try:
        d = json.load(open(CODES_FILE, encoding="utf-8"))
    except Exception as e:
        print("[FAIL-CLOSED] 名单解析失败: %r" % e)
        return None
    items = d.get("items") or []
    if not items:
        print("[FAIL-CLOSED] 名单空")
        return None
    return items


def load_prev_snapshot(ledger_path, today):
    """台账中每票上一交易日快照 {code: row}, 仅取 date < today 的最后一条。"""
    prev = {}
    if not os.path.exists(ledger_path):
        return prev, None
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if str(o.get("date", "")) < str(today):
                prev[o.get("code")] = o
    if prev:
        return prev, max(str(o.get("date", "")) for o in prev.values())
    return prev, None


def load_portrait_tail():
    """倒序块读 445MB 画像历史尾部, 返回 (最新日分位map, 最新日期)。"""
    size = os.path.getsize(PORTRAIT)
    with open(PORTRAIT, "rb") as f:
        f.seek(max(0, size - 2_000_000))
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    pat = re.compile(r'\{"date": "(\d{8})"')
    latest = None
    for l in reversed(tail):
        m = pat.match(l)
        if m:
            latest = m.group(1)
            break
    if not latest:
        return {}, None
    rows = {}
    for l in tail:
        m = pat.match(l)
        if m and m.group(1) == latest:
            try:
                o = json.loads(l)
                rows[str(o.get("code"))] = o
            except Exception:
                pass
    return rows, latest


def classify_events(cur, prev_row):
    """与昨日快照 diff, 返回事件列表(可多事件)。"""
    ev = []
    if not prev_row:
        return ev
    cur_anchor = "anchor" in cur
    prev_anchor = "anchor" in prev_row
    cur_broken = cur.get("stage") == "S5x_破锚走坏"
    prev_broken = prev_row.get("stage") == "S5x_破锚走坏"
    if cur_anchor and not prev_anchor:
        ev.append("NEW_ANCHOR")
    if cur_broken and not prev_broken:
        ev.append("BREAK_ANCHOR")
    if prev_broken and not cur_broken and cur_anchor:
        ev.append("RECOVER")
    if cur.get("shock_date") and not prev_row.get("shock_date"):
        if "SHOCK_EVENT" not in ev:
            ev.append("SHOCK_EVENT")
    if cur.get("overheat_g2") and not prev_row.get("overheat_g2"):
        ev.append("OVERHEAT_ON")
    if cur.get("dist_warning") and not prev_row.get("dist_warning"):
        ev.append("DIST_WARNING_ON")
    if not ev and cur.get("stage") != prev_row.get("stage"):
        ev.append("STAGE_CHANGE")
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-log", action="store_true", help="不写stdout日志(防守卫日志污染)")
    ap.add_argument("--asof", default=None, help="回放截面YYYYMMDD(回归验证)")
    args = ap.parse_args()

    items = load_codes()
    if items is None:
        return 2
    asof = args.asof
    today = asof or time.strftime("%Y%m%d")
    prev, prev_date = load_prev_snapshot(LEDGER, today)
    portrait_rows, pdate = load_portrait_tail()

    # ---- 逐只定位 ----
    results = []
    for it in items:
        code = it["code"]
        try:
            r = locate(code, asof=asof)
        except Exception as e:
            results.append({"code": code, "stage": "ERROR", "detail": repr(e)})
            continue
        r["_name"] = it.get("name", "")
        results.append(r)

    # ---- 异动分类 ----
    events = []
    for r in results:
        ev = classify_events(r, prev.get(str(r["code"])))
        if ev:
            events.append((r, ev))

    # ---- 暗区观察桥 ----
    dark = []
    for r in results:
        if r.get("stage") in ("S0_无结构", "S1_吸筹观察", "NODATA"):
            p = portrait_rows.get(str(r["code"]))
            pct = p.get("pct_short") if p else None
            if pct is not None and pct >= PORTRAIT_BRIDGE_PCT:
                dark.append((r, pct))

    # ---- 统计 ----
    stages_count = {}
    for r in results:
        st = r.get("stage", "?")
        stages_count[st] = stages_count.get(st, 0) + 1
    n_err = sum(1 for r in results if r.get("stage") == "ERROR")

    lines = []
    tag = "回放截面" if asof else ("首跑baseline" if not prev else "每日diff")
    lines.append("[自选池结构监视线] asof=%s (%s)" % (today, tag))
    lines.append("名单=%d只 定位OK=%d 错误=%d" % (len(items), len(results) - n_err, n_err))
    lines.append("")
    lines.append("== 异动 == (%d起, 昨快照日=%s)" % (len(events), prev_date or "无"))
    for r, ev in events:
        evs = "+".join(EVENT_META.get(e, e) for e in ev)
        prev_stage = (prev.get(str(r["code"])) or {}).get("stage", "前夜无记录")
        lines.append("  %s %s  %s -> %s  %s" % (r["code"], r.get("_name", ""), prev_stage, r.get("stage"), evs))
    if not events:
        lines.append("  (无)")
    lines.append("")
    lines.append("== 暗区观察 (无结构但画像分位>=%d, 画像映射日=%s) ==" % (int(PORTRAIT_BRIDGE_PCT), pdate or "NA"))
    for r, pct in dark:
        lines.append("  %s %s stage=%s rng20=%s%% vol比=%s 画像分位=%s" % (
            r["code"], r.get("_name", ""), r.get("stage"), r.get("rng20"),
            r.get("vol5_vs_vol60"), pct))
    if not dark:
        lines.append("  (无)")
    lines.append("")
    lines.append("== 池分布 ==")
    for st, n in sorted(stages_count.items()):
        lines.append("  %s: %d只" % (st, n))
    lines.append("")
    lines.append("== 边界 ==")
    lines.append("零交易权纯观测; 异动=提请主人看非买入指令; 破锚类只提示卖出须问主人; 放量锚不在本线(独立预注册影子)")
    brief = "\n".join(lines)

    with open(BRIEF, "w", encoding="utf-8") as f:
        f.write(brief + "\n")

    # 台账幂等: 同日重跑先删同日旧行再追加
    if os.path.exists(LEDGER):
        keep = []
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if str(o.get("date", "")) != str(today):
                    keep.append(line)
        with open(LEDGER, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + ("\n" if keep else ""))

    with open(LEDGER, "a", encoding="utf-8") as f:
        for r in results:
            rec = dict(r)
            rec.pop("_name", None)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if not args.no_log:
        with open(STDOUT_LOG, "a", encoding="utf-8") as lf:
            lf.write("[%s] n=%d 异动=%d 暗区=%d\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), len(results), len(events), len(dark)))
    print(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
