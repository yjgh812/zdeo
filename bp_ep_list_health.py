# -*- coding: utf-8 -*-
"""bp_ep 名单信号健康度日检(P2-D v1 观测版, WINRATE_UPGRADE_PLAN_20260829 §P2-D)

规则(四周试点期锁定): 最近 N=4 期"已满 5 交易日窗口"的名单, 模拟超额(名单等权 5 日收益
- 全市场等权同期 5 日收益)均值:
    ≥ 0        → GREEN  全额(不动 tier)
    (-2pp, 0)  → YELLOW 观察(复盘提示)
    ≤ -2pp     → RED    建议 tier×0.5(模拟盘试点期仅建议, 不自动降档——四周数据后裁决是否接白名单)

模拟口径: 信号生效日(文件名第二段, 非交易日顺延)开盘等权买入 rank≤15, 持有 5 个交易日收盘;
与 winrate_deep_dive_20260829.py 同源不同窗(那边滚动至 8/28, 这里固定 5 日窗——健康度语义)。
数据: 名单 CSV(multifactor/reports/daily_top20/) + qfq 真库。
产物: reports/bp_ep_list_health_YYYYMMDD.json(白名单生成与周五复盘消费)
"""
from __future__ import annotations
import csv
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

BASE = r"D:\zcodesj"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LIST_DIR = os.path.join(BASE, "multifactor", "reports", "daily_top20")
WINDOW = 5          # 持有 5 个交易日(预注册窗口)
LOOKBACK = 4        # 最近 4 期
RED_TH, YELLOW_TH = -2.0, 0.0  # pp


def main() -> int:
    qconn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True, timeout=10)
    qconn.row_factory = sqlite3.Row
    qcur = qconn.cursor()
    qcur.execute("SELECT code, date, open, close FROM stock_daily_meta_qfq WHERE date >= '20260801'")
    px: dict[str, dict[str, tuple]] = {}
    cal = set()
    for r in qcur.fetchall():
        px.setdefault(r["code"], {})[r["date"]] = (r["open"], r["close"])
        cal.add(r["date"])
    cal_s = sorted(cal)
    idx = {d: i for i, d in enumerate(cal_s)}
    today = cal_s[-1]

    def mkt_eq_ret(d0: str, d1: str) -> float | None:
        rets = [(px[c][d1][1] / px[c][d0][1] - 1) * 100
                for c in px if d0 in px[c] and d1 in px[c]]
        return sum(rets) / len(rets) if rets else None

    files = sorted(glob.glob(os.path.join(LIST_DIR, "bp_ep_monthly_*.csv")))
    # 2762行全市场残留文件(8/24覆盖事件)按前15rank取, 与生产消费口径一致(whitelist rank≤15截断)
    per_list = []
    for f in files:
        fname = os.path.basename(f).replace(".csv", "")
        parts = fname.split("_")
        sig, eff = parts[-2], parts[-1]
        exec_d = next((d for d in cal_s if d > eff or d == eff and eff in idx), None)
        if exec_d is None or exec_d not in idx:
            exec_d = next((d for d in cal_s if d > sig), None)
        if exec_d is None or exec_d not in idx:
            continue
        j = idx[exec_d] + WINDOW
        if j >= len(cal_s):   # 未满 5 日窗 → 跳过(健康度只用已满窗期)
            continue
        end_d = cal_s[j]
        rows = []
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("rank") and r.get("code"):
                    try:
                        rows.append((int(float(r["rank"])), r["code"], r.get("name", "")))
                    except (TypeError, ValueError):
                        pass
        rows.sort()
        rets = []
        for _, code, name in rows[:15]:
            d = px.get(code, {})
            if exec_d in d and end_d in d and d[exec_d][0] and d[end_d][1]:
                rets.append((d[end_d][1] / d[exec_d][0] - 1) * 100)
        if len(rets) < 10:
            continue
        lst_ret = sum(rets) / len(rets)
        mkt = mkt_eq_ret(exec_d, end_d)
        per_list.append({
            "list": fname, "exec_date": exec_d, "end_date": end_d, "n": len(rets),
            "list_ret": round(lst_ret, 2), "mkt_ret": round(mkt, 2) if mkt is not None else None,
            "excess_pp": round(lst_ret - mkt, 2) if mkt is not None else None,
        })

    per_list.sort(key=lambda x: x["exec_date"], reverse=True)
    recent = [p for p in per_list if p["excess_pp"] is not None][:LOOKBACK]
    if not recent:
        print("[健康度] 无已满窗期(需信号日+5交易日数据), fail-closed 不给档位")
        return 0
    avg_exc = sum(p["excess_pp"] for p in recent) / len(recent)
    tier = "GREEN" if avg_exc >= YELLOW_TH else ("YELLOW" if avg_exc > RED_TH else "RED")
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rule": f"最近{LOOKBACK}期已满{WINDOW}日窗名单的模拟超额均值; RED≤{RED_TH}pp 建议tier×0.5 / YELLOW<{YELLOW_TH} 观察 / GREEN全额",
        "avg_excess_pp": round(avg_exc, 2), "health": tier,
        "recent_lists": recent,
        "note": "P2-D v1=纯观测(模拟盘四周试点): RED 仅在白名单 note 列提示+周五复盘呈现, "
                "不自动降档——四周试点后凭'开关期vs全额期实际买入超额'数据裁决是否接线",
        "asof_price": today,
    }
    path = os.path.join(BASE, "reports", f"bp_ep_list_health_{today}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}[tier]
    print(f"[bp_ep名单健康度] {icon} {tier}  近{len(recent)}期平均超额 {avg_exc:+.2f}pp")
    for p in recent:
        print(f"  {p['list']}: {p['exec_date']}→{p['end_date']} 名单{p['list_ret']:+.2f}% vs 市场{p['mkt_ret']:+.2f}% = {p['excess_pp']:+.2f}pp")
    print(f"  产物: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
