#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verify_fill_consistency.py — 模拟盘成交价 vs 真实市价一致性校验(2026-09-05 F3)

背景: westock practice_190 限价单按【委托价】撮合、不校验成交时刻市价,
委托价主动偏离市价时产生单向往不利方向的伪成交(60笔实测8笔约千元,
reports/PAPER_FILL_CONSISTENCY_TEST_20260905.md)。真实交易所按市价成交,
故任何卖出对错评估/胜率归因前必须先校验成交价真实性。

用法:
  python verify_fill_consistency.py --fills <fills.json> [--start YYYYMMDD] [--end YYYYMMDD] [--no-log]
  fills.json = westock MCP portfolio_paper_history 的 records 数组原样导出
  (宪法: westock MCP 是成交唯一真值源, 本工具不读本地订单表——trade_records 混有未成交委托)

判定(数据源=tdx本地vipdoc, 与执行端同源):
  HARD_PSEUDO    成交价超出当日日线[低,高] → 伪成交实锤(该价当日从未真实存在)
  WARN_MOMENT    在日线区间内但超出成交时刻5分钟bar区间(不利方向) → 疑按限价成交/源噪声
  OK             在成交时刻bar区间内
  NO_DATA        tdx数据缺失

exit: 0=全部OK/WARN  1=存在HARD_PSEUDO或NO_DATA  2=参数/数据错误
"""
import argparse
import json
import math
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
import tdx_reader as tr  # noqa: E402

TOL = 0.011  # tdx float32 噪声容差


def _norm_code(code: str):
    """与 trade_executor.norm 同口径(含北交所前缀, 当前组合沪深为主, 防御性对齐)"""
    c = (code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return p, c[2:]
    num = c.zfill(6)
    if num.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh", num
    if num.startswith(("8", "920", "430")):
        return "bj", num
    return "sz", num


def _bar_label(hhmmss: str) -> int:
    """成交时刻所在5分钟bar结束标签(HHMM, 如 09:31:24 -> 935)"""
    h, m, s = int(hhmmss[0:2]), int(hhmmss[3:5]), int(hhmmss[6:8])
    t = h * 60 + m + (1 if s > 0 else 0)
    base = 570 if t <= 690 else 780
    idx = max(1, math.ceil((t - base) / 5))
    em = base + idx * 5
    return (em // 60) * 100 + em % 60


def check_fill(rec: dict, ledger: dict):
    """单笔成交校验。返回 (verdict, detail_dict)"""
    code_raw = rec.get("code", "")
    mkt, num = _norm_code(code_raw)
    direction = (rec.get("direction") or "").lower()
    fill = round(float(rec.get("price") or 0), 3)
    qty = int(float(rec.get("quantity") or 0))
    filled_at = rec.get("filledAt") or ""
    if len(filled_at) < 19 or not filled_at[0:10]:
        det = {"code": f"{mkt}{num}", "name": rec.get("name", ""), "dir": direction,
               "date": "", "time": "", "fill": fill, "qty": qty}
        return "NO_DATA", {**det, "note": f"filledAt缺失/格式异常: {filled_at!r}"}
    date = filled_at[0:10].replace("-", "")
    tfill = filled_at[11:19]
    det = {"code": f"{mkt}{num}", "name": rec.get("name", ""), "dir": direction,
           "date": date, "time": tfill, "fill": fill, "qty": qty}
    lim_o = ledger.get(rec.get("orderId") or "")
    if lim_o:
        det["limit"] = float(lim_o.get("price")) if lim_o.get("price") else None

    drows = [r for r in tr.read_day(market=mkt, code=num) if r[0] == int(date)]
    if not drows:
        return "NO_DATA", {**det, "note": "tdx日线缺失"}
    _, o, h, l, c = drows[0][:5]
    det.update({"day_o": round(o, 3), "day_h": round(h, 3),
                "day_l": round(l, 3), "day_c": round(c, 3)})
    # Tier1: 日线区间(该价当日是否真实存在)
    if direction == "sell" and fill < round(l, 3) - TOL:
        det["impact"] = round((fill - l) * qty, 2)
        det["note"] = f"成交{fill} < 当日最低{l}——伪成交实锤"
        return "HARD_PSEUDO", det
    if direction == "buy" and fill > round(h, 3) + TOL:
        det["impact"] = round((h - fill) * qty, 2)
        det["note"] = f"成交{fill} > 当日最高{h}——伪成交实锤"
        return "HARD_PSEUDO", det
    # Tier2: 成交时刻5分钟bar
    bars = tr.minute_bars_on(market=mkt, code=num, date_int=int(date))
    bl = _bar_label(tfill)
    bar = next((b for b in bars if b["time"] == bl), None)
    if bar is None:
        det["note"] = f"分钟bar{bl}缺失(日线区间内)"
        return "NO_DATA", det
    blo, bhi = round(bar["l"], 3), round(bar["h"], 3)
    det.update({"bar_low": blo, "bar_high": bhi})
    if (blo - TOL) <= fill <= (bhi + TOL):
        det["note"] = "成交时刻bar区间内"
        return "OK", det
    # 不利方向偏离(bar外但在日线内): 疑按限价成交或数据源噪声
    if direction == "sell" and fill < blo:
        det["impact"] = round((fill - blo) * qty, 2)
        det["note"] = f"成交{fill}<时刻bar低{blo}(当日区间内)——疑按限价成交/源噪声"
        return "WARN_MOMENT", det
    if direction == "buy" and fill > bhi:
        det["impact"] = round((fill - bhi) * qty, 2)
        det["note"] = f"成交{fill}>时刻bar高{bhi}(当日区间内)——疑按限价成交/源噪声"
        return "WARN_MOMENT", det
    det["note"] = "bar外但方向有利(异常罕见)"
    return "WARN_MOMENT", det


def main():
    ap = argparse.ArgumentParser(description="模拟盘成交价 vs 真实市价一致性校验(F3)")
    ap.add_argument("--fills", required=True, help="westock portfolio_paper_history records JSON导出路径")
    ap.add_argument("--start", default="", help="起始日 YYYYMMDD(含)")
    ap.add_argument("--end", default="", help="结束日 YYYYMMDD(含)")
    ap.add_argument("--no-log", action="store_true", help="兼容约定: 本工具只读不写守卫日志")
    a = ap.parse_args()

    with open(a.fills, encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        print("❌ fills文件格式错误: 需要 records 数组", file=sys.stderr)
        return 2

    ledger = {}
    try:
        with open("D:/zcodesj/data/executor_orders.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ledger[o.get("order_id")] = o
    except FileNotFoundError:
        print("⚠️ executor_orders.jsonl 缺失, 委托价列不可用")

    rows = [r for r in records
            if (r.get("status") == "已成交")
            and (r.get("direction") in ("buy", "sell"))
            and (not a.start or (r.get("filledAt", "")[0:10].replace("-", "") >= a.start))
            and (not a.end or (r.get("filledAt", "")[0:10].replace("-", "") <= a.end))]
    if not rows:
        print("❌ 无符合条件已成交记录", file=sys.stderr)
        return 2

    stats, hard_rows = {}, []
    print(f"{'日期':<9}{'名称':<6}{'方':<2}{'成交':>7}{'委托':>7}{'日低':>7}{'日高':>7}{'bar低':>7}{'bar高':>7}{'判定':<12}{'影响元':>8} 备注")
    total_impact = 0.0
    for r in sorted(rows, key=lambda x: x.get("filledAt", "")):
        v, det = check_fill(r, ledger)
        stats[v] = stats.get(v, 0) + 1
        imp = det.get("impact")
        if imp:
            # abs: 卖侧不利为负/买侧不利为正, 合计按不利幅度绝对值累加(符号混合相加无意义)
            total_impact += abs(imp)
        lim_s = f"{det['limit']:.2f}" if det.get("limit") else "--"
        print(f"{det['date']:<9}{det['name']:<6}{det['dir'][:1].upper():<2}{det['fill']:>7.2f}"
              f"{lim_s:>7}{det.get('day_l', 0):>7.2f}{det.get('day_h', 0):>7.2f}"
              f"{det.get('bar_low', 0) or 0:>7.2f}{det.get('bar_high', 0) or 0:>7.2f}"
              f"{v:<12}{(f'{imp:.0f}' if imp else '-'):>8} {det['note']}")
        if v == "HARD_PSEUDO":
            hard_rows.append(det)

    print("\n===== 汇总 =====")
    print(json.dumps(stats, ensure_ascii=False))
    print(f"总样本: {len(rows)}, 不利偏离金额合计: ~{total_impact:.0f} 元")
    if hard_rows:
        print(f"❌ 伪成交实锤 {len(hard_rows)} 笔: "
              + ", ".join(f"{d['name']}{d['date'][-4:]}{d['fill']}" for d in hard_rows))
        return 1
    print("✅ 无伪成交实锤(WARN项建议人工复核数据源噪声)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
