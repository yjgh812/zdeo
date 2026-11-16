# -*- coding: utf-8 -*-
"""
kell_r31_observe_20260828.py — R31 灰度观察工具（只读）
============================================================
统计 R31（个股偏离度 ext20）在 guard_precheck_log 中的命中情况，并跟踪被拦截票的 T+5 前瞻表现。
用途：R31 落地后 2 周灰度观察（计划书 K3 §5.6 / R31_IMPLEMENTATION §5），只读不改生产。

判读：
  - REJECT（ext20≥20%）命中数 / WARN（15-20%带）命中数 —— 拦了谁、拦得多频
  - 被 REJECT 票 T+5 前瞻收益（qfq 收盘）：若显著为负 → 拦截正确（追高负期望验证）；若为正 → 误伤证据，需复核阈值
数据源（只读）：attribution.db guard_precheck_log + qfq 库
"""
import argparse, json, re, sqlite3, statistics, sys
from datetime import datetime, timedelta

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB  = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT_PREFIX = r"D:\zcodesj\reports\R31_OBSERVE"

def parse_list(s):
    if not s:
        return []
    s = s.strip()
    if s.startswith("["):
        try:
            v = json.loads(s)
            return v if isinstance(v, list) else []
        except Exception:
            pass
    return [s]

def fwd5_from_qfq(code6, on_date, px):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date >= ? ORDER BY date LIMIT 8",
        (code6, on_date)).fetchall()
    con.close()
    if not rows or rows[0][0] != on_date:
        return None
    # rows[0]=当日, rows[5]=T+5
    if len(rows) < 6 or not rows[5][1]:
        return None
    return (rows[5][1] - px) / px

def main():
    p = argparse.ArgumentParser(description="R31 灰度观察（只读）")
    p.add_argument("--days", type=int, default=7, help="观察窗口(交易日自然日, 默认7)")
    p.add_argument("--date", default="", help="单日观察 YYYYMMDD")
    a = p.parse_args()
    # 2026-09-11 Mimosa 审计加固: --date 进报告路径拼接, 未校验可路径穿越
    if a.date and not re.fullmatch(r"\d{8}", str(a.date)):
        print(f"[FAIL-CLOSED] --date 非法(须YYYYMMDD): {a.date!r}")
        return 2
    if a.date:
        cutoff = a.date
        label = a.date
    else:
        cutoff = (datetime.now() - timedelta(days=a.days)).strftime("%Y%m%d")
        label = f"近{a.days}日(≥{cutoff})"

    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT trade_date, code, name, price, verdict, rejects, warnings FROM guard_precheck_log "
        "WHERE trade_date >= ? ORDER BY trade_date", (cutoff,)).fetchall()
    con.close()

    reject_hits, warn_hits = [], []
    # 2026-09-11 修复: guard_precheck_log 的 name 由调用方传入, 历史上存在错配
    # (600016 被传成"华夏银行", 正确=民生银行; 8/29 test_scan/pre_sell 行) → 报告回显错名
    # 被 verify_stock_names 拦下。名称一律走唯一接口 stock_name_lookup 复核修正。
    try:
        from stock_name_lookup import get_name as _get_name
    except Exception:
        _get_name = None
    name_fixed = 0
    for td, code, name, price, verdict, rej, warn in rows:
        if _get_name is not None:
            _c6 = re.sub(r"[a-z]+", "", str(code or "")).zfill(6)
            _canon = _get_name(_c6)
            if _canon and _canon != name:
                name_fixed += 1
                name = _canon
        rj = parse_list(rej); wn = parse_list(warn)
        r31_r = [x for x in rj if "R31偏离度" in str(x)]
        r31_w = [x for x in wn if "R31偏离度" in str(x)]
        if r31_r:
            reject_hits.append({"date": td, "code": code, "name": name, "price": price, "msg": r31_r[0]})
        elif r31_w:
            warn_hits.append({"date": td, "code": code, "name": name, "price": price, "msg": r31_w[0]})

    # T+5 前瞻
    fwd = []
    for h in reject_hits:
        code6 = re.sub(r"[a-z]+", "", str(h["code"] or "")).zfill(6)
        r = fwd5_from_qfq(code6, h["date"], h["price"] if h["price"] else 0)
        if r is not None:
            fwd.append((h, r))

    md = []
    md.append(f"# R31 灰度观察 · {label}")
    md.append("")
    md.append(f"> **日期**：{datetime.now().strftime('%Y-%m-%d %H:%M')} | **性质**：只读观察，不改生产")
    md.append(f"> **闸门记录**：guard_precheck_log ≥{cutoff} 共 {len(rows)} 条")
    md.append(f"> **R31 REJECT(ext20≥20%)**：{len(reject_hits)} 次 | **R31 WARN(15-20%带)**：{len(warn_hits)} 次")
    if name_fixed:
        md.append(f"> **名称修正**：{name_fixed} 行日志名称与库不一致，已按 stock_name_lookup 唯一接口修正"
                  f"（如 600016 民生银行；日志原值由调用方传入，8/29 test_scan/pre_sell 行错配）")
    md.append("")
    md.append("## R31 REJECT 命中明细与被拦截票 T+5 前瞻收益")
    md.append("")
    if reject_hits:
        fwd_map = {h["date"] + h["code"]: r for h, r in fwd}
        md.append("| 日期 | 代码 | 名称 | 价格 | 触发信息 | T+5 |")
        md.append("|---|---|---|---|---|---|")
        for h in reject_hits:
            code6 = re.sub(r"[a-z]+", "", str(h["code"] or "")).zfill(6)
            r = fwd_map.get(h["date"] + code6)
            t5 = f"{r*100:+.2f}%" if r is not None else "N/A(未到期)"
            md.append(f"| {h['date']} | {h['code']} | {h['name']} | {h['price']} | {h['msg'][:60]} | {t5} |")
        fv = [r for _, r in fwd]
        md.append("")
        if fv:
            md.append(f"- 可算 T+5 n={len(fv)}：均值 **{statistics.mean(fv)*100:+.2f}%** 中位 {statistics.median(fv)*100:+.2f}%")
            md.append("- 判读：T+5 显著为负 → 拦截正确（追高负期望验证）；为正 → 误伤证据，复核阈值。")
        else:
            md.append(f"- 可算 T+5 n=0（拦截票前向未到期，如当日拦截）——灰度期正常，待 T+5 到期后复跑。")
    else:
        md.append("（无 REJECT 命中。R31 于 2026-08-28 落地，观察期起点。）")
    md.append("")
    md.append("## R31 WARN 命中明细（15-20% 带，建议 tier 减半）")
    md.append("")
    if warn_hits:
        for h in warn_hits:
            md.append(f"- {h['date']} {h['code']} {h['name']} @{h['price']}：{h['msg'][:70]}")
    else:
        md.append("（无 WARN 命中。）")
    md.append("")
    md.append("## 观察结论")
    md.append("- 本报告为灰度观察基线/累计快照；建议 2 周后（约 2026-09-11 起）以 `--days 14` 复核拦截票 T+5 分布。")
    md.append("- 若 REJECT 拦截票 T+5 中位明显为负 → R31 阈值证据成立；若接近 0 或为正 → 上报主人复核阈值。")
    out = f"{REPORT_PREFIX}_{datetime.now().strftime('%Y%m%d')}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[R31-OBS] 记录 {len(rows)} 条 | REJECT {len(reject_hits)} | WARN {len(warn_hits)} | T+5可算 {len(fwd)}")
    print(f"[R31-OBS] 报告已写: {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
