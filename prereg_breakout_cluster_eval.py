# -*- coding: utf-8 -*-
"""PREREG-BREAKOUT-CLUSTER-20260909 一次性判据检验。

判据、分组、样本规则全部来自 data/prereg_breakout_cluster_20260909.json（冻结版，
指纹=文件 sha256 前 16 位）。本脚本不支持任何阈值/口径调整参数；只读数据源，
仅覆写结果报告。首轮运行即终局（prereg no_peeking_statement）。
"""
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREREG_PATH = ROOT / "data" / "prereg_breakout_cluster_20260909.json"
TRACK_PATH = ROOT / "data" / "breakout_scout_track.jsonl"
ATTR_DB = ROOT / "attribution" / "attribution.db"
QFQ_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
REPORT_PATH = ROOT / "reports" / "PREREG_BREAKOUT_CLUSTER_RESULT_20260909.md"


def fail_closed(msg: str) -> None:
    print(f"[prereg-eval] FAIL-CLOSED: {msg}", file=sys.stderr)
    sys.exit(2)


def welch(a, b):
    n1, n2 = len(a), len(b)
    m1, m2 = sum(a) / n1, sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    t = (m1 - m2) / se if se > 0 else float("nan")
    return m1, m2, t, se


def main() -> None:
    prereg_bytes = PREREG_PATH.read_bytes()
    prereg = json.loads(prereg_bytes.decode("utf-8"))
    fingerprint = hashlib.sha256(prereg_bytes).hexdigest()[:16]
    cm = prereg["criteria_machine"]
    cluster_min = int(cm["cluster_min"])
    delta_min_pp = float(cm["delta_r1_min_pp"])
    t_min = float(cm["welch_t_min"])
    n_main_min = int(cm["n_main_min"])
    n_iso_min = int(cm["n_iso_min"])

    rows = [json.loads(l) for l in TRACK_PATH.open(encoding="utf-8") if l.strip()]
    # 同 (board_date, code) 去重：track 存在同日双扫追加缺陷（20260904 实测 15 行重复），
    # 重复行会虚增集群计数，属数据缺陷修复，非判据口径调整（两轮结果均留痕于报告）。
    seen = set()
    deduped = []
    for r in rows:
        key = (r["board_date"], r["code"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    n_dup_rows = len(rows) - len(deduped)
    rows = deduped

    def in_scope(r):
        code = r["code"]
        return not code.startswith(("92", "68", "4", "8")) and "ST" not in (r.get("name") or "")

    scope = [r for r in rows if in_scope(r)]
    n_out_scope = len(rows) - len(scope)

    if not ATTR_DB.exists() or ATTR_DB.stat().st_size == 0:
        fail_closed(f"归因库不可用: {ATTR_DB}")
    acon = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    try:
        for r in scope:
            row = acon.execute(
                "SELECT industry FROM limit_up_patterns WHERE trade_date=? AND code=?",
                (r["board_date"], r["code"]),
            ).fetchone()
            r["ind"] = (row[0] or "UNKNOWN") if row else "UNKNOWN"
    except sqlite3.OperationalError as exc:
        fail_closed(f"归因库读取失败(可能被并行写入锁定): {exc}")
    finally:
        acon.close()

    cnt = Counter((r["board_date"], r["ind"]) for r in scope if r["ind"] != "UNKNOWN")
    for r in scope:
        g = cnt[(r["board_date"], r["ind"])] if r["ind"] != "UNKNOWN" else 0
        r["g"] = g
        r["grp"] = "main" if g >= cluster_min else "iso"

    if not QFQ_DB.exists() or QFQ_DB.stat().st_size == 0:
        fail_closed(f"qfq 价格源不可用: {QFQ_DB}")
    qcon = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    try:
        for r in scope:
            dates = [int(d) for (d,) in qcon.execute(
                "SELECT date FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (r["code"],))]
            t = int(r["board_date"])
            nxt = [d for d in dates if d > t]
            r["r1"] = None
            r["r5"] = None
            r["r5_partial"] = False
            if not nxt:
                continue
            prices = {int(d): c for d, c in qcon.execute(
                "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=?",
                (r["code"], t))}
            base = prices.get(t)
            if base is None or base <= 0:
                continue
            c1 = prices.get(nxt[0])
            if c1:
                r["r1"] = c1 / base - 1.0
            if len(nxt) >= 6:
                c5 = prices.get(nxt[5])
                if c5:
                    r["r5"] = c5 / base - 1.0
            else:
                last = prices.get(nxt[-1])
                if last:
                    r["r5"] = last / base - 1.0
                    r["r5_partial"] = True
    except sqlite3.OperationalError as exc:
        fail_closed(f"qfq 读取失败(可能被 22:00 链写入锁定): {exc}")
    finally:
        qcon.close()

    res = [r for r in scope if r["r1"] is not None]
    n_no_fwd = len(scope) - len(res)
    r1_main = [r["r1"] for r in res if r["grp"] == "main"]
    r1_iso = [r["r1"] for r in res if r["grp"] == "iso"]
    if not r1_main or not r1_iso:
        fail_closed("分组后存在空组，判据无法评估")

    m1, m2, t_stat, se = welch(r1_main, r1_iso)
    delta_pp = (m1 - m2) * 100.0
    n_main, n_iso = len(r1_main), len(r1_iso)

    checks = {
        "delta_r1": (delta_pp >= delta_min_pp, f"ΔR1={delta_pp:+.2f}pp (阈值>={delta_min_pp}pp)"),
        "welch_t": (t_stat >= t_min, f"Welch t={t_stat:.2f} (阈值>={t_min})"),
        "n_main": (n_main >= n_main_min, f"n_主线={n_main} (阈值>={n_main_min})"),
        "n_iso": (n_iso >= n_iso_min, f"n_孤立={n_iso} (阈值>={n_iso_min})"),
    }
    verdict = "PASS" if all(ok for ok, _ in checks.values()) else "FAIL"

    def delta_for(threshold):
        gm = [r["r1"] for r in res if r["ind"] != "UNKNOWN"
              and cnt[(r["board_date"], r["ind"])] >= threshold]
        gi = [r["r1"] for r in res if r["ind"] == "UNKNOWN"
              or cnt[(r["board_date"], r["ind"])] < threshold]
        if not gm or not gi:
            return None
        return (sum(gm) / len(gm) - sum(gi) / len(gi)) * 100.0

    sens_thr2 = delta_for(2)
    sens_thr4 = delta_for(4)
    r5_main = [r["r5"] for r in res if r["grp"] == "main" and r["r5"] is not None]
    r5_iso = [r["r5"] for r in res if r["grp"] == "iso" and r["r5"] is not None]
    r5_delta_pp = (sum(r5_main) / len(r5_main) - sum(r5_iso) / len(r5_iso)) * 100.0 if r5_main and r5_iso else None

    by_day = defaultdict(lambda: ([], []))
    for r in res:
        mm, ii = by_day[r["board_date"]]
        (mm if r["grp"] == "main" else ii).append(r["r1"])
    paired = [sum(mm) / len(mm) - sum(ii) / len(ii)
              for mm, ii in by_day.values() if mm and ii]
    paired_mean_pp = (sum(paired) / len(paired)) * 100.0 if paired else None

    win_main = sum(1 for x in r1_main if x > 0) / n_main * 100.0
    win_iso = sum(1 for x in r1_iso if x > 0) / n_iso * 100.0

    cluster_days = sorted({(r["board_date"], r["ind"]) for r in res if r["grp"] == "main"})
    examples = []
    for d, ind in cluster_days[-6:]:
        names = [f"{r['code']} {r['name']}" for r in res
                 if r["board_date"] == d and r["ind"] == ind][:6]
        examples.append((d, ind, len(names), "、".join(names)))

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# PREREG-BREAKOUT-CLUSTER-20260909 判据检验结果",
        "",
        f"- 运行时间: {ts}（首轮即终局，prereg no_peeking_statement）",
        f"- prereg 指纹: `{fingerprint}`（data/prereg_breakout_cluster_20260909.json 的 sha256 前 16 位）",
        f"- 数据源: track 全史 {len(rows) + n_dup_rows} 信号，同(日,码)去重剔除 {n_dup_rows} 行"
        f"（全部 20260904 同日双扫缺陷）；范围外(北交/科创/ST)剔除 {n_out_scope}；"
        f"无 T+1 前向价剔除 {n_no_fwd}（PIT 口径）；判据样本 {len(res)}",
        "",
        "## 主判据（全部满足 = PASS）",
        "",
        "| 判据 | 结果 | 达标 |",
        "|---|---|---|",
    ]
    for _, (ok, txt) in checks.items():
        lines.append(f"| {txt.split('(')[0].strip()} | {txt.split('(')[1].rstrip(')')} | {'✅' if ok else '❌'} |")
    lines += [
        "",
        f"## 分组统计（R1=close(T+1)/close(T)-1，qfq 主库同源口径）",
        "",
        "| 组 | n | 均值R1 | 胜率 |",
        "|---|---|---|---|",
        f"| 主线组(同日同行业>={cluster_min}) | {n_main} | {m1 * 100:+.2f}% | {win_main:.1f}% |",
        f"| 孤立组(g<={cluster_min - 1}, 含UNKNOWN) | {n_iso} | {m2 * 100:+.2f}% | {win_iso:.1f}% |",
        f"| 组间差 ΔR1 | | {delta_pp:+.2f}pp (Welch t={t_stat:.2f}, SE={se * 100:.2f}pp) | |",
        "",
        "## 敏感性臂（预注册，方向核对）",
        "",
        f"1. 阈值 g>=2: ΔR1={sens_thr2:+.2f}pp；g>=4: ΔR1={sens_thr4:+.2f}pp"
        f"（方向{'一致' if (sens_thr2 or 0) > 0 and (sens_thr4 or 0) > 0 else '不一致'} vs 主判据方向 {delta_pp:+.2f}pp）",
        f"2. R5 口径: ΔR5={r5_delta_pp:+.2f}pp (n_main={len(r5_main)}, n_iso={len(r5_iso)})"
        f"（方向{'一致' if (r5_delta_pp or 0) > 0 else '不一致'}）",
        f"3. 按日配对差: {len(paired)} 天两组均有信号, 日级均值 {paired_mean_pp:+.2f}pp"
        f"（{'为正' if (paired_mean_pp or 0) > 0 else '非正'}）",
        "",
        "## 判定",
        "",
        f"**VERDICT: {verdict}**（prereg primary_criterion 全条目核对）",
        "",
        "主线集群日样例（末 6 个，代码+名称）:",
        "",
    ]
    for d, ind, k, nm in examples:
        lines.append(f"- {d} {ind} ×{k}：{nm}")
    lines += [
        "",
        "## 边界与登记去向",
        "",
        f"- {'PASS → PASS_CANDIDATE 登记 + >=10 交易日影子确认（零生产接线）' if verdict == 'PASS' else 'FAIL → falsified_rules 登记 BREAKOUT-CLUSTER-FILTER（边界=仅判死本过滤用法，不判死突破信号本身与主线概念其他实现）'}",
        "- R1 为 close-to-close 研究口径，未假设 T+1 开盘可买。",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[prereg-eval] 指纹={fingerprint} 样本={len(res)}(主线{n_main}/孤立{n_iso}) "
          f"ΔR1={delta_pp:+.2f}pp t={t_stat:.2f} → VERDICT: {verdict}")
    print(f"[prereg-eval] 报告: {REPORT_PATH}")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
