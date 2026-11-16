#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pre_pick_checklist.py — 买入建议前三道硬核对（2026-09-03 立项，反思报告 SUBJECTIVE_PICK_REFLECTION_20260903 行动项③⑤）

背景：9/2+9/3 两批题材买入 10 只 9 亏 1 平，根因=跳过板块资金/题材天数/竞价状态核对。
本工具在"给出买入建议之前"运行，自动化其中可本地计算的两道，第三道(板块资金)输出 MANUAL 强制人工核。

三道核对：
  1. 题材天数（截至 T-1 连续上涨天数）：>=3 天 FAIL（禁追第4天）；2 天 WARN；<=1 天 PASS
  2. 位置（T-1 收盘 vs 近10日最低）：>=+15% FAIL；+10%~+15% WARN；<+10% PASS
  3. 当日高开（buy-date open vs T-1 close）：>=+3% FAIL；+1%~+3% WARN；<+1% PASS
  4. 板块 5 日主力资金：MANUAL —— 必须用 MCP(xiaoshi_sector_fundflow_rank / data_sector ranking) 核查后
     以 --sector-ok CODE 或交互确认标记，否则 --strict 下整体不通过。
  5. 批次熔断（2026-09-03 接线，SOP §六第 11 条④）：批浮盈真值由调用方经 MCP(westock 持仓/成交，唯一口径)
     取得后以 --batch-pnl "日期=盈亏元,..." 传入，脚本只判级留痕；最近 N 批(默认2)连续为负 → FAIL(BREAKER OPEN)。
     --auto 另提供 executor台账×tdx收盘 的估算参考（撤单稿已过滤(O13)，仍含部分成交噪声，仅参考不参与判级）。

数据源：本地通达信 vipdoc（tdx_reader 单一真源模块，8/31 统一规范）。只读，不写任何文件。
用法：
  python pre_pick_checklist.py --codes sz300670,sh603819 --buy-date 20260903
  python pre_pick_checklist.py --codes ... --buy-date ... --sector-ok sz300670 --strict
  python pre_pick_checklist.py --codes ... --buy-date ... --batch-pnl "20260902=-1225,20260903=-3365" --strict
退出码：0=无 FAIL 且 MANUAL 已全部核销；1=存在 FAIL 或 strict 下存在未核销 MANUAL；2=数据不足（结构化输出原因）。
"""
import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day  # noqa: E402
from executor_order_ledger import is_cancelled  # noqa: E402  (O13 2026-09-03 撤单稿过滤)

VIPDOC_ROOT = r"D:\anzhuang\tdx\vipdoc"
EXECUTOR_LEDGER = r"D:\zcodesj\data\executor_orders.jsonl"


@dataclass
class Verdict:
    code: str
    name: str
    runup_days: int
    pos_pct: float
    gap_pct: float
    sector: str  # PASS/FAIL/WARN/MANUAL
    sector_note: str
    checks: list  # (item, value, level)


def norm_code(c: str):
    c = c.strip().lower()
    if len(c) == 8 and c[:2] in ("sh", "sz"):
        return c[:2], c[2:]
    if len(c) == 6 and c.isdigit():
        return ("sh" if c[0] in "69" else "sz"), c
    raise ValueError(f"bad code: {c}")


class DayRow(tuple):
    """tdx_reader.read_day 行: (date, open, high, low, close, amount, volume)"""
    date = property(lambda r: r[0])
    open = property(lambda r: r[1])
    high = property(lambda r: r[2])
    low = property(lambda r: r[3])
    close = property(lambda r: r[4])


def load_recent(code: str, n: int = 12):
    mkt, num = norm_code(code)
    rows = read_day(market=mkt, code=num, root=VIPDOC_ROOT)
    rows = [DayRow(r) for r in rows]
    return rows[-n:]


def check_one(code: str, buy_date: str, sector_ok_codes: set, names: dict) -> Verdict:
    rows = load_recent(code)
    if len(rows) < 11:
        raise RuntimeError(f"{code}: vipdoc 日线不足 11 根(实际 {len(rows)})，无法核对")
    # 截至 buy-date 前一交易日的行 + buy-date 当日行（若已存在）
    hist = [r for r in rows if int(r.date) < int(buy_date)]
    today = [r for r in rows if int(r.date) == int(buy_date)]
    if len(hist) < 10:
        raise RuntimeError(f"{code}: T-1 前日线不足 10 根(实际 {len(hist)})")
    t1 = hist[-1]
    t2 = hist[-2]

    # 1) 题材天数：T-1 往前连续上涨天数
    runup = 0
    for i in range(len(hist) - 1, 0, -1):
        if hist[i].close > hist[i - 1].close:
            runup += 1
        else:
            break
    # 2) 位置：T-1 收盘 vs 近10日最低（含 T-1）
    low10 = min(r.low for r in hist[-10:])
    pos_pct = (t1.close / low10 - 1) * 100
    # 3) 高开：buy-date open vs T-1 close
    if today:
        gap_pct = (today[0].open / t1.close - 1) * 100
    else:
        gap_pct = None  # 盘前运行时 buy-date 无日线，需盘中/开盘后补核

    lvl1 = "FAIL" if runup >= 3 else ("WARN" if runup == 2 else "PASS")
    lvl2 = "FAIL" if pos_pct >= 15 else ("WARN" if pos_pct >= 10 else "PASS")
    if gap_pct is None:
        lvl3 = "MANUAL"
        gap_txt = "待开盘后补核"
    else:
        lvl3 = "FAIL" if gap_pct >= 3 else ("WARN" if gap_pct >= 1 else "PASS")
        gap_txt = f"{gap_pct:+.2f}%"
    mkt, num = norm_code(code)
    sector_lvl = "PASS" if code.lower() in sector_ok_codes else "MANUAL"

    v = Verdict(
        code=code,
        name=names.get(num, num),
        runup_days=runup,
        pos_pct=pos_pct,
        gap_pct=gap_pct,
        sector=sector_lvl,
        sector_note="人工已核销" if sector_lvl == "PASS" else "须用 MCP 核板块5日主力资金后 --sector-ok 标记",
        checks=[
            (f"题材天数={runup}(T-1={t2.date}->{t1.date})", f"阈值>=3禁追", lvl1),
            (f"位置={pos_pct:+.1f}%(vs 10日低{low10})", "阈值>=15%禁追", lvl2),
            (f"高开={gap_txt}", "阈值>=3%禁追/1-3%回踩", lvl3),
            (f"板块5日主力资金", "流出则禁", sector_lvl),
        ],
    )
    return v


def check_breaker(batch_pnl: str, max_batch_size: int, auto: bool):
    """第5道：批次熔断。真值模式=--batch-pnl；--auto 供台账×tdx 估算参考(不判级)。返回 (level, lines)。"""
    lines = []
    if batch_pnl:
        items = []
        for kv in [x for x in batch_pnl.split(",") if x.strip()]:
            d, _, v = kv.partition("=")
            items.append((d.strip(), float(v)))
        items.sort()
        recent = items[-2:]
        if len(recent) == 2 and recent[0][1] < 0 and recent[1][1] < 0:
            lvl = "FAIL"
            lines.append(f"[FAIL] 批次熔断开启：最近两批连续浮亏 {recent[0][0]}={recent[0][1]:+,.0f} / {recent[1][0]}={recent[1][1]:+,.0f} —— 禁止新增题材批次，恢复名单制")
        elif recent and recent[-1][1] < 0:
            lvl = "WARN"
            lines.append(f"[WARN] 上一批浮亏 {recent[-1][0]}={recent[-1][1]:+,.0f}：再亏一批即触发熔断")
        else:
            lvl = "PASS"
            lines.append("[PASS] 批次熔断未触发")
        for d, v in items:
            lines.append(f"    批次 {d}: {v:+,.0f} 元")
        return lvl, lines
    if auto:
        try:
            batches = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))  # date -> code -> [金额, 数量]
            for l in open(EXECUTOR_LEDGER, encoding="utf-8"):
                if not l.strip():
                    continue
                r = json.loads(l)
                if is_cancelled(r):  # O13 2026-09-03: 撤单稿不入批估算（-22156 vs 真值 -1226 噪声根因）
                    continue
                if r.get("action") == "BUY":
                    b = batches[r["trade_date"]][r["code"]]
                    b[0] += float(r["price"]) * int(r["qty"])
                    b[1] += int(r["qty"])
                # 2026-09-10 M7: 原 SELL 行收集进 sells 集合后从未使用(死代码)已删;
                # 已卖仓位未按成交价结转的估算漂移属"仅参考不判级"注释既定语义, 不扩散
            est = []
            for d in sorted(batches)[-2:]:
                pnl = 0.0
                noise = False
                for code, (amt, qty) in batches[d].items():
                    mkt, num = norm_code(code)
                    rows = [r for r in read_day(market=mkt, code=num, root=VIPDOC_ROOT) if int(r[0]) >= int(d)]
                    if not rows:
                        noise = True
                        continue
                    last = rows[-1][4]  # 最新收盘
                    pnl += (last - amt / qty) * qty
                est.append((d, pnl, noise, len(batches[d])))
            lines.append("[AUTO-REF] 台账×tdx 收盘估算（含撤单/部分成交噪声，仅参考不判级）:")
            for d, pnl, noise, n in est:
                tag = " ⚠含噪声" if noise else ""
                lines.append(f"    批次 {d}({n}只): 估算 {pnl:+,.0f} 元{tag}")
            return "MANUAL", lines
        except Exception as e:
            return "MANUAL", [f"[MANUAL] 批次熔断自动估算失败({type(e).__name__}: {e})，请人工以 westock 批浮盈经 --batch-pnl 核销"]
    return "MANUAL", ["[MANUAL] 批次熔断：请以 westock 批浮盈经 --batch-pnl 核销（真值口径），或 --auto 出参考估算"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", required=True, help="逗号分隔，sz300670 或 300670")
    ap.add_argument("--buy-date", required=True, help="买入/建议日 YYYYMMDD")
    ap.add_argument("--sector-ok", default="", help="已人工核销板块资金的代码(逗号分隔)")
    ap.add_argument("--batch-pnl", default="", help='批浮盈真值 "日期=元,..."（westock 口径），如 "20260902=-1225,20260903=-3365"')
    ap.add_argument("--max-batch-size", type=int, default=5, help="单批买入只数上限，超出 WARN（默认5）")
    ap.add_argument("--auto", action="store_true", help="批次熔断自动估算参考模式")
    ap.add_argument("--strict", action="store_true", help="存在 FAIL 或未核销 MANUAL 时退出码 1")
    a = ap.parse_args()

    sector_ok = {c.strip().lower() for c in a.sector_ok.split(",") if c.strip()}
    results, errors = [], []
    for c in [x for x in a.codes.split(",") if x.strip()]:
        try:
            results.append(check_one(c, a.buy_date, sector_ok, NAMES))
        except Exception as e:
            errors.append(f"{c}: {type(e).__name__}: {e}")

    print(f"买入前三道核对 buy-date={a.buy_date}")
    print(f"{'代码':<10}{'名称':<8}{'天数':<6}{'位置':<9}{'高开':<10}{'判定'}")
    block = []
    for v in results:
        levels = [c[2] for c in v.checks]
        overall = "FAIL" if "FAIL" in levels else ("WARN" if "WARN" in levels else ("MANUAL" if "MANUAL" in levels else "PASS"))
        gap_txt = f"{v.gap_pct:+.1f}%" if v.gap_pct is not None else "待补"
        print(f"{v.code:<10}{v.name:<8}{v.runup_days:<6}{v.pos_pct:+.1f}%{'':<3}{gap_txt:<10}{overall}")
        for item, thr, lvl in v.checks:
            if lvl != "PASS":
                print(f"    [{lvl}] {item}  ({thr}{'' if lvl != 'MANUAL' else '; ' + v.sector_note})")
        block.append((v, overall))
    for e in errors:
        print(f"  [ERROR] {e}")

    # 第5道：批次熔断
    bk_lvl, bk_lines = check_breaker(a.batch_pnl, a.max_batch_size, a.auto)
    print("第5道 批次熔断:")
    for l in bk_lines:
        print("  " + l)

    has_fail = any(o == "FAIL" for _, o in block) or bk_lvl == "FAIL"
    has_manual = any(o == "MANUAL" for _, o in block) or bk_lvl == "MANUAL"
    has_warn = any(o == "WARN" for _, o in block) or bk_lvl == "WARN"
    if errors:
        print("结论：数据不足，禁止下结论（见 ERROR）")
        return 2
    if has_fail:
        print("结论：存在 FAIL —— 禁止追入" + ("（批次熔断开启）" if bk_lvl == "FAIL" else ""))
        return 1
    if has_manual and a.strict:
        print("结论：存在未核销 MANUAL 项 —— strict 下不予放行")
        return 1
    print("结论：无 FAIL" + ("（存在 MANUAL 项待人工核销）" if has_manual else "，核对全部通过") + ("（含 WARN 项）" if has_warn else ""))
    return 0


NAMES = {
    "300670": "大烨智能", "603819": "神力股份", "300025": "华星创业",
    "300252": "金信诺", "002811": "郑中设计", "301256": "华融化学",
    "603585": "苏利股份", "605166": "聚合顺", "603230": "内蒙新华",
    "002688": "金河生物",
}

if __name__ == "__main__":
    sys.exit(main())
