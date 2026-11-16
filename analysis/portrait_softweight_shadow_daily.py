#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画像软权重影子实验 日更器（2026-10-09, PREREG_PORTRAIT_SOFTWEIGHT_SHADOW_20261009）

定位: B臂实验数据积累器——主线低位启动候选集内, 画像short分位双臂排序前瞻对比。
边界: 零拦截零排序权(晨报排序恒为 BASE_SORT_KEY 不动); 升级须过 prereg 三门+主人批准。
口径(LOCKED, 指纹 fail-closed):
  - forward-only: 候选集每日实跑落账, 禁回溯重建历史日(防 C1 行业集/数据版本前视);
    历史行仅 fwd 随 qfq 成熟回填, 候选集永不重建。
  - 双臂同域: 仅取有画像分的交集公平对照, 禁全集 vs 子集混淆。
  - 扫描语义单一 owner: import mainline_startup_scan.scan_candidates, 禁复制。
用法:
  python portrait_softweight_shadow_daily.py            # 日更(落账+fwd回填)
  python portrait_softweight_shadow_daily.py --status   # 只读评读(进度+门读数)
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PREREG_PATH = os.path.join(ROOT, "data", "prereg_portrait_softweight_shadow_20261009.json")
LEDGER_PATH = os.path.join(ROOT, "data", "portrait_softweight_shadow_ledger.jsonl")
BRIEF_PATH = os.path.join(ROOT, "data", "ptwt_shadow_brief_latest.txt")
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FEED_PATH = os.path.join(ROOT, "data", "portrait_feed.json")


def _fail(msg, code=2):
    print(f"[ptwt][ERROR] {msg}")
    sys.exit(code)


def load_prereg():
    """参数真源=prereg json, 指纹复算不一致即 fail-closed(参数被改未留痕→拒绝运行)。"""
    try:
        doc = json.load(open(PREREG_PATH, encoding="utf-8"))
    except Exception as e:
        _fail(f"prereg不可读: {e}")
    if not str(doc.get("status", "")).startswith("ACTIVE"):
        _fail(f"prereg非ACTIVE: {doc.get('status')}(已归档/废案?)")
    fp = hashlib.sha256(json.dumps(
        {"params": doc.get("params"), "readout": doc.get("readout")},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    if fp != doc.get("fingerprint"):
        _fail(f"prereg指纹不一致: 算得{fp}≠登记{doc.get('fingerprint')}(参数被改?须主人留痕)")
    return doc


def qfq_max_day(db):
    # 库内 date 列 TEXT: MAX() 返回 str, 强转 int 与台账 date 对齐(breakout_scout 同款坑)
    return int(db.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])


def load_feed(anchor_d, max_lag):
    """画像 feed 读取(唯一对接=portrait_feed.json)。不可用/超龄→(None, err)→SKIP留痕。"""
    try:
        pf = json.load(open(FEED_PATH, encoding="utf-8"))
    except Exception as e:
        return None, f"feed不可读:{e}"
    if pf.get("unavailable"):
        return None, f"feed unavailable:{pf.get('unavailable')}"
    if pf.get("freshness") and pf["freshness"].get("ok") is False:
        return None, f"feed freshness FAIL:{pf['freshness']}"
    sd = str(pf.get("snapshot_date", ""))
    if not (sd.isdigit() and len(sd) == 8):
        return None, f"feed snapshot_date异常:{sd!r}"
    lag = (datetime.strptime(str(anchor_d), "%Y%m%d") - datetime.strptime(sd, "%Y%m%d")).days
    if lag < 0 or lag > max_lag:
        return None, f"feed快照{sd}相对数据日{anchor_d}超龄(lag={lag}>{max_lag})"
    pct = {str(k).zfill(6): float(v.get("pct", 0)) if isinstance(v, dict) else float(v)
           for k, v in (pf.get("scores_short") or {}).items()}
    return {"pct": pct, "date": sd}, ""


def collect_today(db):
    """当日候选集(实跑, 不回溯)+双臂排序; 失败/跳过返回 (None, msg)。"""
    from mainline_startup_scan import BASE_SORT_KEY, scan_candidates
    pre = load_prereg()
    P = pre["params"]
    D = qfq_max_day(db)
    if db.execute("SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE date=?", (D,)).fetchone()[0] == 0:
        print(f"[ptwt] SKIP {D}: 无行情(非交易日?)")
        return None, 0
    try:
        rows, ctx = scan_candidates()
    except RuntimeError as e:
        if "C1 主线为空" in str(e):
            # 主线空=合法无候选日(20261008实测c1_count=0, 扫描器设计行为), 非数据事故→SKIP留痕
            print(f"[ptwt] SKIP {qfq_max_day(db)}: C1主线空(无扫描对象)")
            return None, 0
        _fail(f"扫描失败: {e}")
    # 锚一致性闸(2026-10-09补): 行业量能状态由 IndustryVolState_2252 schtasks 在22:00链之后生成,
    # 若 c1_date 落后于 qfq 数据日=候选集锚错配(晨报同对口径被破坏), 禁采集——本影子正确挂点=次日晨(09:35 cron)
    if str(ctx["c1_date"]) != str(D):
        print(f"[ptwt] SKIP {D}: C1行业集日期{ctx['c1_date']}≠qfq数据日{D}(锚错配禁采集)")
        return None, 0
    if not rows:
        print(f"[ptwt] SKIP {D}: 候选集空(六层过滤无幸存者)")
        return None, 0
    feed, err = load_feed(D, P["feed_lag_max_natural_days"])
    if feed is None:
        print(f"[ptwt] SKIP {D}: {err}")
        return None, 0
    pt = feed["pct"]
    inter = [r for r in rows if r["code"] in pt]
    if len(inter) < P["min_cands_pt"]:
        print(f"[ptwt] SKIP {D}: 交集n={len(inter)}<{P['min_cands_pt']}(候选{len(rows)}/画像覆盖{len(pt)})")
        return None, 0
    base_order = sorted(inter, key=BASE_SORT_KEY)
    pt_order = sorted(inter, key=lambda x: (-pt[x["code"]], x["code"]))
    rec = {
        "date": D,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "n_total": len(rows), "n_pt": len(inter), "pt_date": feed["date"],
        "c1": ctx["c1"][:6], "ms_state": ctx["ms_state"],
        "base_top5": [r["code"] for r in base_order[:P["top_k"][0]]],
        "base_top10": [r["code"] for r in base_order[:P["top_k"][1]]],
        "pt_top5": [r["code"] for r in pt_order[:P["top_k"][0]]],
        "pt_top10": [r["code"] for r in pt_order[:P["top_k"][1]]],
        "fwd": {}, "judged": False,
    }
    return rec, 0


def backfill_fwd(db, recs, windows):
    """fwd 回填(幂等): 每 code 取该 code 序列 date>=d 的 max(w)+1 根, closes[0]=锚。
    窗不足→整行留待下次; 已 judged 跳过。"""
    need = max(windows) + 1
    for rec in recs:
        if rec.get("judged"):
            continue
        codes = sorted(set(rec["base_top5"]) | set(rec["base_top10"])
                       | set(rec["pt_top5"]) | set(rec["pt_top10"]))
        fwd, ok = {}, True
        for c in codes:
            bars = db.execute(
                "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date LIMIT ?",
                (c, rec["date"], need)).fetchall()
            if len(bars) < need or not bars[0][0]:
                ok = False
                break
            base_c = float(bars[0][0])
            fwd[c] = {f"fwd{w}": round((float(bars[w][0]) / base_c - 1) * 100, 3) for w in windows}
        if ok and fwd:
            rec["fwd"] = fwd
            rec["judged"] = True


def load_ledger():
    recs = []
    if os.path.exists(LEDGER_PATH):
        for line in open(LEDGER_PATH, encoding="utf-8"):
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def save_ledger(recs):
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _med(vals):
    s = sorted(vals)
    m = len(s)
    if not m:
        return None
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2


def evaluate(recs, P):
    """两级中位读数: 日内臂中位差→日间中位。返回 {key: (med_diff, n_days)}。"""
    judged = [r for r in recs if r.get("judged")]
    out = {}
    for k in P["top_k"]:
        for w in P["fwd_windows"]:
            diffs = []
            for r in judged:
                fa = _med([r["fwd"][c][f"fwd{w}"] for c in r[f"pt_top{k}"] if c in r["fwd"]])
                fb = _med([r["fwd"][c][f"fwd{w}"] for c in r[f"base_top{k}"] if c in r["fwd"]])
                if fa is not None and fb is not None:
                    diffs.append(fa - fb)
            m = _med(diffs)
            out[f"top{k}_fwd{w}"] = (m, len(diffs))
    return out, len(judged)


def status_report():
    pre = load_prereg()
    P, R = pre["params"], pre["readout"]
    recs = load_ledger()
    judged_n_all = sum(1 for r in recs if r.get("judged"))
    print(f"[ptwt-status] prereg={pre['prereg_id']} 激活={P['activation_date']} "
          f"落账日={len(recs)} 有效日(judged)={judged_n_all} 台账={LEDGER_PATH}")
    if not judged_n_all:
        print("[ptwt-status] 尚无成熟fwd行——正常(需≥6交易日后首批fwd5回填)")
        write_brief(pre, recs)
        return 0
    ev, _ = evaluate(recs, P)
    for key, (m, n) in sorted(ev.items()):
        print(f"[ptwt-status] {key}: med差={('N/A' if m is None else f'{m:+.2f}pp')} (n={n}日)")
    kill_n = min(20, judged_n_all)
    m10_5 = ev.get("top10_fwd5", (None, 0))[0]
    if judged_n_all < int(R["window"].split("个")[0]):
        print(f"[ptwt-status] 窗未满({judged_n_all}/60)——以上为过程读数非判定")
    if m10_5 is not None and judged_n_all >= 20 and m10_5 < -1.0:
        print("[ptwt-status] 🔴KILL窗触发条件命中: 前20有效日 med5差(Top10)<-1.0pp → 提请废案")
    write_brief(pre, recs)
    return 0


def write_brief(pre, recs):
    """简报面(屋内 *_brief_latest.txt 惯例): 幂等覆盖, 供晨报/主人只读; 零交易权。"""
    P, R = pre["params"], pre["readout"]
    judged_n = sum(1 for r in recs if r.get("judged"))
    L = [f"画像软权重影子简报 (更新 {datetime.now().isoformat(timespec='seconds')})",
         f"prereg: {pre['prereg_id']} | 指纹 {pre.get('fingerprint')} | 激活 {P['activation_date']}",
         f"窗口进度: 落账日 {len(recs)} / 有效日(judged) {judged_n} / 满窗 {R['window'].split('个')[0]}",
         ]
    if judged_n == 0:
        L.append("读数: 尚无成熟fwd行(首批fwd5需锚日后≥6交易日)——过程正常")
    else:
        ev, _ = evaluate(recs, P)
        for key in ("top10_fwd5", "top10_fwd20", "top5_fwd5", "top5_fwd20"):
            m, n = ev.get(key, (None, 0))
            ms = "N/A" if m is None else f"{m:+.2f}pp"
            L.append(f"读数 {key}: med差={ms} (n={n}日)")
        m10_5 = ev.get("top10_fwd5", (None, 0))[0]
        if m10_5 is not None and judged_n >= 20 and m10_5 < -1.0:
            L.append("🔴KILL窗条件命中(前20有效日med5差<-1.0pp)→提请废案")
        else:
            L.append(f"kill窗: 未触发(前{min(20, judged_n)}有效日)")
    L.append("边界: 零拦截零排序权, 晨报排序恒BASE_SORT_KEY; 判读于满窗后按G1/G2/G3呈主人裁决")
    try:
        with open(BRIEF_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    except OSError as e:
        print(f"[ptwt][WARN] 简报写入失败(不影响台账): {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只读评读(不落账不回填)")
    args = ap.parse_args()
    if args.status:
        return status_report()
    pre = load_prereg()
    P = pre["params"]
    db = sqlite3.connect(QFQ_DB, uri=True)
    try:
        rec, _ = collect_today(db)
        recs = load_ledger()
        backfill_fwd(db, recs, P["fwd_windows"])
        if rec is not None:
            recs = [r for r in recs if r["date"] != rec["date"]]  # 同日重跑覆盖, 历史行不动
            recs.append(rec)
        save_ledger(recs)
    finally:
        db.close()
    judged_n = sum(1 for r in recs if r.get("judged"))
    write_brief(load_prereg(), recs)  # SKIP日也刷新简报(幂等覆盖, 读数面常青)
    if rec is not None:
        print(f"[ptwt-OK] {rec['date']} 落账: 候选{rec['n_total']} 交集{rec['n_pt']} "
              f"pt快照{rec['pt_date']} | 台账{len(recs)}日 有效(judged){judged_n}")
    else:
        print(f"[ptwt-OK] 今日SKIP(见上行), 仅fwd回填: 台账{len(recs)}日 有效{judged_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
