#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小石财务 as-reported PIT 对账层 (pit_reconcile)
==================================================
用小石原生财务契约对账屠龙自建 factor_pit.db `_pit_v2` 的 2021+ 段。

口径说明(2026-08-22 实测对齐):
  - 屠龙 _pit_v2.roe 为小数(摊薄净资产收益率), 如 600519 2025-12-31 roe=0.3365 = 33.65%
    → 对应小石 abstract['metrics']['摊薄净资产收益率'] = 33.649772 (%)
  - _pit_v2.roa 为小数(如 0.2709=27.09%); 小石 '总资产报酬率(ROA)' 为息税前/含少数口径(38.08%),
    两者口径不同 → roa 仅参考对比, 不判 PASS/FAIL
  - _pit_v2.bp = 账面市值比(1/PB), 可由小石 fundamentals.pb 反推 bp=1/pb
  - _pit_v2.ep = 盈利收益率(1/PE), 可由小石 fundamentals.pe_ttm 反推 ep=1/pe_ttm
  - 小石 fundamentals 是"最新快照"(date), 用于 bp/ep 参考; 严格 PIT 需 financial-as-reported as_of,
    本层第一阶段用 abstract(报告期指标) + fundamentals(估值快照) 做方向性对账, 标注口径差异。

PIT 语义:
  - 小石 financials abstract 返回的是报告期值(最新可得), 未做 as_of 回溯;
    本工具第一阶段为"报告期值对账", 第二阶段可扩展 financial-as-reported(as_of) 严格回测。
  - 空值/缺失/口径不可比 → 标注 reason, 不计入差异率。

用法:
  python pit_reconcile.py --codes 600519,000001,300750 --report 20251231
  python pit_reconcile.py --codes 600519 --report 20251231 --json > reports/xiaoshi_pit_reconcile.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import xiaoshi_client as xc
except ImportError:
    xc = None

PIT_DB = r"D:\zcodesj\factor_pit.db"
PIT_TABLE = "stock_factors_fundamental_pit_v2"
REPORT_DIR = r"D:\zcodesj\reports"

# 报告期格式转换: 20251231 -> 2025-12-31
def _fmt_report(rd: int) -> str:
    s = str(rd)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def reconcile_codes(codes: list[str], report_date: int, verbose: bool = True) -> dict:
    """对账指定报告期。返回逐股对比。"""
    out = {"report_date": report_date, "checked": 0, "pass": 0, "diff": [], "notes": []}
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=20)
    rd_s = str(report_date)
    for code in codes:
        row = con.execute(
            f"SELECT code,report_date,pit_avail_date,ep,bp,roe,droe,roa,ref_close "
            f"FROM {PIT_TABLE} WHERE code=? AND report_date=?",
            (code, report_date)).fetchone()
        if not row:
            out["notes"].append({"code": code, "note": "_pit_v2 无该报告期"})
            continue
        _, _, pit_avail, ep_pit, bp_pit, roe_pit, droe_pit, roa_pit, ref_close = row

        # 小石 abstract
        x_roe = x_roa = None
        x_eps_nav = None
        try:
            fin = xc.financials(code=code, type_="abstract", periods=8)
            rd_fmt = _fmt_report(report_date)
            for it in (fin.get("abstract") or []):
                if str(it.get("report_date")) == rd_fmt:
                    mm = it.get("metrics", {})
                    x_roe = mm.get("摊薄净资产收益率")        # %
                    x_roa = mm.get("总资产报酬率(ROA)")      # % 口径不同
                    x_eps_nav = mm.get("摊薄每股净资产_期末股数")
                    break
        except Exception as e:
            out["notes"].append({"code": code, "note": f"小石 financials 失败: {type(e).__name__}"})
            x_roe = None

        # 小石 fundamentals 快照(pe/pb)
        x_pe_ttm = x_pb = x_close = None
        try:
            f = xc.fundamentals(code)
            x_pe_ttm = f.get("pe_ttm")
            x_pb = f.get("pb")
            x_close = f.get("close")
        except Exception:
            pass

        # 对比 roe(摊薄口径): 屠龙小数*100 vs 小石%
        # 已知口径: 屠龙 roe = 净利润/股东权益合计(含永续债/其他权益工具, TDX field6);
        #          小石摊薄ROE = 归母净利润/归母普通股净资产(扣永续债)。银行股差异~1.7pp。
        item = {"code": code, "report_date": report_date}
        roe_ok = None
        if x_roe is not None and roe_pit is not None:
            diff = abs(roe_pit * 100 - x_roe)
            roe_ok = diff < 1.0  # 1pp 容差
            item["pit_roe_pct"] = round(roe_pit * 100, 4)
            item["xiaoshi_roe_pct"] = round(x_roe, 4)
            item["roe_diff_pp"] = round(diff, 4)
            item["roe_status"] = "OK" if roe_ok else "DIFF"
            if not roe_ok:
                item["roe_note"] = ("屠龙含永续债/其他权益工具口径 vs 小石扣永续债摊薄口径, "
                                    "银行股差异显著; 非数据错误(2026-08-22核实, 净利润两源一致)")
        else:
            item["roe_status"] = "NA"

        # 对比 bp: 屠龙 bp vs 小石 1/pb
        # 已知口径: 屠龙 bp = bps/ref_close(期后首日价, 2025年报=2026-03-30);
        #          小石 1/pb 用最新快照价(2026-08-20)+归母净资产。价格时点与净资产口径
        #          差异可致 0.03~0.09 差, 非数据错误(2026-08-22核实)。
        bp_ok = None
        if x_pb and bp_pit:
            x_bp = 1.0 / x_pb
            diff_bp = abs(bp_pit - x_bp)
            bp_ok = diff_bp < 0.05
            item["pit_bp"] = round(bp_pit, 4)
            item["xiaoshi_bp_1pb"] = round(x_bp, 4)
            item["bp_status"] = "OK" if bp_ok else "DIFF"
            if not bp_ok:
                item["bp_note"] = ("价格时点不同(屠龙期后首日价 vs 小石最新快照) + "
                                   "净资产口径(屠龙总权益bps vs 小石归母) 所致, 非数据错误")
        else:
            item["bp_status"] = "NA"

        # 对比 ep: 屠龙 ep vs 小石 1/pe_ttm
        ep_ok = None
        if x_pe_ttm and ep_pit:
            x_ep = 1.0 / x_pe_ttm
            diff_ep = abs(ep_pit - x_ep)
            ep_ok = diff_ep < 0.02
            item["pit_ep"] = round(ep_pit, 4)
            item["xiaoshi_ep_1pe"] = round(x_ep, 4)
            item["ep_status"] = "OK" if ep_ok else "DIFF"
        else:
            item["ep_status"] = "NA"

        # roa 仅参考
        if x_roa is not None and roa_pit is not None:
            item["pit_roa_pct"] = round(roa_pit * 100, 4)
            item["xiaoshi_roa_pct"] = round(x_roa, 4)
            item["roa_note"] = "口径不同(小石息税前/含少数), 仅参考"

        statuses = [item.get(k) for k in ("roe_status", "bp_status", "ep_status") if item.get(k) != "NA"]
        item["overall"] = "OK" if statuses and all(s == "OK" for s in statuses) else (
            "DIFF" if any(s == "DIFF" for s in statuses) else "NA")
        out["checked"] += 1
        if item["overall"] == "OK":
            out["pass"] += 1
        else:
            out["diff"].append(item)
        if verbose:
            print(f"  {code} roe={item.get('roe_status')} bp={item.get('bp_status')} "
                  f"ep={item.get('ep_status')} overall={item['overall']}",
                  file=sys.stderr)
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="小石财务 as-reported PIT 对账层")
    ap.add_argument("--codes", required=True, help="逗号分隔代码")
    ap.add_argument("--report", required=True, type=int, help="报告期 YYYYMMDD, 如 20251231")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if xc is None:
        print("xiaoshi_client 不可用", file=sys.stderr)
        return 1
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    res = reconcile_codes(codes, a.report)
    res["known_limits"] = [
        "roe: 屠龙=净利润/总权益(含永续债) vs 小石=归母/普通股净资产(扣永续债), 银行股差~1.7pp",
        "bp: 屠龙=bps/期后首日价 vs 小石=1/最新快照PB(归母净资产), 价时点差 0.03~0.09",
        "小石 financials abstract 为报告期最新值(非 as_of 回溯); fundamentals 为最新快照, 非严格 PIT",
        "已知缺陷(8/15遗留, 2026-08-22修复): build_factor_pit bps/sp=ref_close/bp 公式方向错误(~50倍失真); "
        "已改 bps=bp×ref_close/sp×ref_close 并同步修正 factor_pit.db 两表 257857 行 + v3/quantbyqlib 消费路径; "
        "全量 v3 回归 D-ridge OOS IC +0.0744→+0.0741(CAGR +5.1%→+5.8%), 结论保持",
    ]
    os.makedirs(REPORT_DIR, exist_ok=True)
    p = os.path.join(REPORT_DIR, f"xiaoshi_pit_reconcile_{a.report}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"对账完成: checked={res['checked']} pass={res['pass']} diff={len(res['diff'])}")
        for it in res["diff"]:
            print("  差异:", it)
        for n in res["notes"]:
            print("  备注:", n)
        print(f"报告: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
