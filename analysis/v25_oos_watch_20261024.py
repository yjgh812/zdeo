# -*- coding: utf-8 -*-
"""v25 ml_h20_wfa 观察转正监控件 (2026-10-24, 零交易权, 直跑制)

依据: PROJECT_BOUNDARY §3 v25 裁决(2026-10-24 A案维持现状)的重启条件四件套:
  ① 新指纹 prereg  ② 信号日产链恢复  ③ 2026真窗OOS绝对转正  ④ 影子期≥1季度正验收
本件只读探测 ②的现势性并给出复核提示; 不跑回测、不产信号、不下单、不挂cron(直跑制,
portrait_shadow_v1 先例)。冻结判定: MAX(date)>20260902 即 SUPPLY_RESUMED →
提示"新数据出现, 须以新指纹重跑 wfa_gate 四门"(禁直接复用旧指纹, WFA一票制)。

用法: python analysis/v25_oos_watch_20261024.py [--json]
"""
import sqlite3, json, argparse, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
DBQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FROZEN_MAX = 20260902  # 裁决落册日实证的信号表断供点

ap = argparse.ArgumentParser()
ap.add_argument("--json", action="store_true", help="结构化JSON输出(供上层消费)")
A, _ = ap.parse_known_args()

con = sqlite3.connect(DB, uri=True)
v25_max, v25_rows, v25_codes = con.execute(
    "select max(date), count(*), count(distinct code) from stock_ml_scores_v25_round12wf").fetchone()
v23_max = con.execute("select max(date) from stock_ml_scores_v23_wfa").fetchone()[0]
recent = con.execute("select date, count(*) from stock_ml_scores_v25_round12wf "
                     "where date>=20261001 group by date order by date desc limit 5").fetchall()
con.close()
cq = sqlite3.connect(DBQ, uri=True)
qfq_max = cq.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0]
cq.close()

resumed = int(v25_max) > FROZEN_MAX
stale_days_gt_qfq = 0
try:
    import datetime as _dt
    d1 = _dt.datetime.strptime(str(int(qfq_max)), "%Y%m%d")
    d2 = _dt.datetime.strptime(str(int(v25_max)), "%Y%m%d")
    stale_days_gt_qfq = (d1 - d2).days
except Exception:
    pass

conds = [
    ("① 判定协议 prereg", "locked_active(20261024-v25-2026reeval-v1, E1=2026-12-31主判; 打分协议指纹380b95ff不变)"),
    ("② 信号供给", f"RESUMED(MAX={v25_max}, 2026-10-24 A案批量重打落地; 维持现势=重跑--rescore 2026, 日线打分器B案G1过前缓建)" if resumed else f"STALE(MAX={v25_max}, 落后qfq({qfq_max})约{stale_days_gt_qfq}自然日, 10月行数={len(recent)})"),
    ("③ 2026真窗OOS转正", "冻结底数-0.02%(截至2026-10-08, trial C4); 判定仅E1/E2注册时点执行(反窥探条款)"),
    ("④ 影子期≥1季度", "not started(③为前置)"),
]
out = dict(
    probe="v25_oos_watch", ts_table="stock_ml_scores_v25_round12wf",
    v25=dict(max_date=int(v25_max), rows=int(v25_rows), codes=int(v25_codes)),
    v23_same_domain_max=int(v23_max), qfq_max=int(qfq_max),
    supply_status="RESUMED" if resumed else "STALE",
    restart_conditions={k: v for k, v in conds},
    frozen_rule="MAX>20260902 即须新指纹重跑 wfa_gate 四门(禁复用旧指纹)",
    trading_right="none")
if A.json:
    print(json.dumps(out, ensure_ascii=False, indent=1))
else:
    print(f"[v25_oos_watch] 信号表 stock_ml_scores_v25_round12wf: MAX={v25_max} 行={v25_rows:,} 覆盖{v25_codes}只")
    print(f"  同域对照 v23_wfa MAX={v23_max} | qfq MAX={qfq_max}")
    print(f"  供给状态: {out['supply_status']}" + ("" if not resumed else " → 须新指纹重跑 wfa_gate 四门"))
    for k, v in conds:
        print(f"  {k}: {v}")
    print("  判定时点(prereg 20261024-v25-2026reeval-v1 已锁定): E1=2026-12-31(主) | E2=2027-01-15(补判缓冲) | 供给未恢复触发线=2026-11-30(NOT_EVALUABLE回主人)")
    print("  零交易权 | 直跑制(不挂cron) | 复核入口=PROJECT_BOUNDARY §3 v25段")
