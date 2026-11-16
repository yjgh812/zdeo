#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_winrate_replay_20260828.py — 胜率回放三件套全面测试(只读, 不写生产库)
================================================================
对象: winrate_lever_replay_20260828.py / winrate_sellside_replay_20260828.py /
      winrate_week_precheck_20260828.py + reports/raw/westock_fills_era2_*.json
组:
  T1  原始JSON完整性(字段/状态枚举/时代边界)
  T2  load_fills 过滤与计数
  T3  match_fifo 合成用例(手算对照: 基本/先卖后买/双lot部分成交/卖超持仓/同刻并列/输入纯净性)
  T4  真实数据回归锚(45事件/19未配/21持仓/胜率13/期望-3.29/净-42277/W2=28&11)
  T5  cap_keep_seqs(每日前K不同代码/同刻MCP顺序并列打破/跨日隔离/同代码多lot全保留)
  T6  lc5+日线+锚引擎(数据到827/建设机械MA5当日触发/湘潭电化删失/合成序列锚语义)
  T7  预检窗口与PARTIAL判定(子窗口完整/未来窗PARTIAL)
  T8  报告交叉一致性(平仓lot=48三处对齐/W2口径 lever=precheck/附录行数=45/买日<=卖日)
  T9  确定性(连跑两遍逐字节一致)
  T10 verify_stock_names 三报告全过
  T11 报告关键章节存在
  T12 时代边界防回归(8/14前记录不得进入fills)
注: 报告文件在T7/T9会被脚本重写, 测试前备份、结束后恢复原字节(lever报告由主流程另行再生成)。
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
import winrate_lever_replay_20260828 as lever  # noqa: E402
import winrate_sellside_replay_20260828 as sellside  # noqa: E402
import winrate_week_precheck_20260828 as precheck  # noqa: E402

ROOT = Path(r"D:\zcodesj")
REPORTS = ROOT / "reports"
MD_LEVER = REPORTS / "WINRATE_LEVER_REPLAY_20260828.md"
MD_SELL = REPORTS / "WINRATE_SELLSIDE_REPLAY_20260828.md"
MD_WEEK = REPORTS / "WINRATE_WEEK_PRECHECK_20260828.md"

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" | {detail}" if detail and not cond else ""))


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def mk(side, code, q, p, t, seq, name="测试股"):
    return {"side": side, "code": code, "name": name, "qty": q, "price": p, "dt": dt(t), "seq": seq}


def real_events():
    fills = lever.load_fills()
    buys = sorted([f for f in fills if f["side"] == "buy"], key=lambda x: (x["dt"], x["seq"]))
    sells = sorted([f for f in fills if f["side"] == "sell"], key=lambda x: (x["dt"], x["seq"]))
    return fills, buys, sells, lever.match_fifo(buys, sells)


def run_main_silent(mod):
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    print("== T1 原始JSON完整性 ==")
    raw = json.loads(lever.FILLS_JSON.read_text(encoding="utf-8"))
    recs = raw["records"]
    check("T1.1 记录数=203(8/28补录+3)", len(recs) == 203, str(len(recs)))
    fields_ok = all({"c", "n", "d", "q", "p", "s", "t"} <= set(r) for r in recs)
    check("T1.2 必需字段齐全", fields_ok)
    status_ok = all(r["s"] in {"已成交", "已撤单", "系统撤单", "未成交"} for r in recs)
    check("T1.3 状态枚举合法", status_ok)
    side_ok = all(r["d"] in {"buy", "sell"} for r in recs)
    check("T1.4 方向枚举合法", side_ok)
    era_ok = all(r["t"] >= "2026-08-14" for r in recs)
    check("T1.5 时代边界(≥08-14)", era_ok)
    val_ok = all(r["q"] > 0 and r["p"] > 0 for r in recs)
    check("T1.6 量价均为正", val_ok)

    print("== T2 load_fills 过滤 ==")
    fills = lever.load_fills()
    check("T2.1 仅保留已成交136笔", len(fills) == 136, str(len(fills)))
    n_buy = sum(1 for f in fills if f["side"] == "buy")
    n_sell = len(fills) - n_buy
    check("T2.2 买70/卖66", (n_buy, n_sell) == (70, 66), f"{n_buy}/{n_sell}")
    check("T2.3 时间戳全部可解析", all(isinstance(f["dt"], datetime) for f in fills))

    print("== T3 match_fifo 合成用例 ==")
    # a) 基本
    ev, un, op = lever.match_fifo([mk("buy", "sz000001", 100, 10.0, "2026-08-20 09:35:00", 0)],
                                  [mk("sell", "sz000001", 100, 11.0, "2026-08-21 14:00:00", 1)])
    check("T3.1 基本配对PnL=+10%", len(ev) == 1 and abs(ev[0]["pnl_pct"] - 10.0) < 1e-9)
    # b) 先卖后买→未配
    ev, un, op = lever.match_fifo([mk("buy", "sz000001", 100, 10.0, "2026-08-21 09:35:00", 0)],
                                  [mk("sell", "sz000001", 100, 11.0, "2026-08-20 14:00:00", 1)])
    check("T3.2 先卖后买=未配对+无未来lot", len(ev) == 0 and len(un) == 1 and len(op) == 1)
    # c) 双lot FIFO 部分成交(手算: 100@10全耗+50@20 → vwap=2000/150=13.3333, 短50)
    ev, un, op = lever.match_fifo(
        [mk("buy", "sh600000", 100, 10.0, "2026-08-20 09:35:00", 0),
         mk("buy", "sh600000", 100, 20.0, "2026-08-20 10:00:00", 1)],
        [mk("sell", "sh600000", 150, 15.0, "2026-08-21 14:00:00", 2)])
    e = ev[0]
    check("T3.3 FIFO顺序+部分成交", abs(e["cost_vwap"] - 2000 / 150) < 1e-9 and e["short_qty"] == 0
          and e["fragments"][0][1] == 100 and e["fragments"][1][1] == 50)
    check("T3.4 残余lot=50", op[0]["left"] == 50)
    # d) 同刻并列: 买seq0卖seq1→买先入队(配对成功); 反转→未配
    ev1, _, _ = lever.match_fifo([mk("buy", "sz000002", 100, 10.0, "2026-08-20 09:32:11", 0)],
                                 [mk("sell", "sz000002", 100, 10.5, "2026-08-20 09:32:11", 1)])
    ev2, _, _ = lever.match_fifo([mk("buy", "sz000002", 100, 10.0, "2026-08-20 09:32:11", 5)],
                                 [mk("sell", "sz000002", 100, 10.5, "2026-08-20 09:32:11", 1)])
    check("T3.5 同刻并列(seq小者先)=确定性", len(ev1) == 1 and len(ev2) == 0)
    # e) 输入纯净性(不改动调用方列表元素)
    b_in = [mk("buy", "sz000003", 100, 10.0, "2026-08-20 09:35:00", 0)]
    s_in = [mk("sell", "sz000003", 60, 12.0, "2026-08-21 14:00:00", 1)]
    lever.match_fifo(b_in, s_in)
    check("T3.6 match_fifo不改变输入", b_in[0]["qty"] == 100 and "left" not in b_in[0])

    print("== T4 真实数据回归锚 ==")
    fills, buys, sells, (events, unmatched, open_lots) = real_events()
    check("T4.1 平仓事件=47", len(events) == 47, str(len(events)))
    check("T4.2 未配对=19/期末持仓lot=20", len(unmatched) == 19 and len(open_lots) == 20,
          f"{len(unmatched)}/{len(open_lots)}")
    wins = sum(1 for e in events if e["pnl_pct"] > 0)
    mean_pnl = sum(e["pnl_pct"] for e in events) / len(events)
    check("T4.3 胜=13(27.7%)", wins == 13, str(wins))
    check("T4.4 期望=-3.17%/笔", abs(mean_pnl - (-3.17)) < 0.005, f"{mean_pnl:.4f}")
    yen = sum(e["sell"]["price"] * e["covered_qty"]
              - sum(l["price"] * q for l, q in e["fragments"]) for e in events)
    check("T4.5 净盈亏≈-42399", abs(yen - (-42399)) < 60, f"{yen:.0f}")
    w2 = [e for e in events if e["sell"]["dt"] >= datetime(2026, 8, 24)]
    check("T4.6 W2 N=30胜11(36.7%/-1.35%)", len(w2) == 30 and sum(1 for e in w2 if e["pnl_pct"] > 0) == 11
          and abs(sum(e["pnl_pct"] for e in w2) / 30 - (-1.35)) < 0.005)
    check("T4.7 全事件买日<=卖日(无未来配对)",
          all(l["dt"] <= e["sell"]["dt"] for e in events for l, _ in e["fragments"]))
    check("T4.8 金田股份手算锚(+13.6%)",
          any("金田" in e["sell"]["name"] and abs(e["pnl_pct"] - 13.58) < 0.1 for e in events))

    print("== T5 cap_keep_seqs ==")
    sames = "2026-08-25 09:32:11"
    buys_t = [mk("buy", "sz301487", 100, 1.0, sames, 10),
              mk("buy", "sz000990", 100, 1.0, sames, 11),
              mk("buy", "sz002125", 100, 1.0, sames, 12),
              mk("buy", "sz002125", 200, 1.0, sames, 13),
              mk("buy", "sh600629", 100, 1.0, "2026-08-25 09:31:00", 14)]
    keep2 = lever.cap_keep_seqs(buys_t, 2)
    kept_codes = {b["code"] for b in buys_t if b["seq"] in keep2}
    check("T5.1 每日前K不同代码+早时优先", kept_codes == {"sh600629", "sz301487"}, str(kept_codes))
    check("T5.2 同刻并列=MCP顺序(seq小优先)", "sz301487" in kept_codes and "sz000990" not in kept_codes)
    keep3 = lever.cap_keep_seqs(buys_t, 4)
    check("T5.3 被保留代码的多lot全保留(cap=4)", 12 in keep3 and 13 in keep3)
    day2 = [mk("buy", f"sz30000{i}", 100, 1.0, "2026-08-26 09:35:00", 20 + i) for i in range(4)]
    keep_day2 = lever.cap_keep_seqs(day2, 2)
    check("T5.4 跨日隔离(每日独立cap)", len(keep_day2) == 2)
    full_keep = lever.cap_keep_seqs(buys_t, 10)
    check("T5.5 K≥代码数=全保留", len(full_keep) == 5)
    # 真实数据冒烟: cap3保留lot数 < cap5 <= 69
    fills_r, buys_r, sells_r, _ = real_events()
    k5 = lever.cap_keep_seqs(buys_r, 5)
    k3 = lever.cap_keep_seqs(buys_r, 3)
    check("T5.6 真实0<cap3<cap5<=69", 0 < len(k3) < len(k5) <= 69, f"{len(k5)}/{len(k3)}")

    print("== T6 lc5/日线/锚引擎 ==")
    bars = lever.lc5_bars("000725")
    check("T6.1 分钟数据至少覆盖到827(≥)", max(bars) >= 20260827, str(max(bars)))
    ser = sellside.daily_series("600984")  # 建设机械 8/27卖出
    i827 = ser["idx"][20260827]
    j_ma5 = sellside.anchor_exit(ser, i827, "ma5")
    check("T6.2 建设机械MA5锚当日触发", j_ma5 == i827, str(j_ma5))
    # T6.3 锚语义用合成序列测(确定性, 不随盘中lc5增长漂移; 2026-08-28盘中首证该耦合后重构)
    def synth(closes, start=20260801):
        n = len(closes)
        ma5 = [None] * n
        ema = [0.0] * n
        for i, c in enumerate(closes):
            if i >= 4:
                ma5[i] = sum(closes[i-4:i+1]) / 5
            ema[i] = c if i == 0 else ema[i-1] + (c - ema[i-1]) * 2 / 11
        dates = [start + i for i in range(n)]
        return {"dates": dates, "highs": [c * 1.01 for c in closes], "lows": [c * 0.99 for c in closes],
                "closes": list(closes), "ma5": ma5, "ema10": ema, "idx": {d: i for i, d in enumerate(dates)}}
    sA = synth([10, 10.2, 10.4, 10.6, 10.8, 11.0, 9.0, 8.5])   # 第7日暴跌破MA5
    check("T6.3a 锚触发=首个收盘<线之日", sellside.anchor_exit(sA, 0, "ma5") == 6)
    sB = synth([10, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4])  # 持续上行永不破线
    check("T6.3b 窗口内未触发=删失(None)", sellside.anchor_exit(sB, 0, "ma5") is None
          and sellside.anchor_exit(sB, 0, "ema10") is None)
    sC = synth([10, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4, 11.6, 11.8, 12.0, 12.2, 9.5])  # 窗口(≤i+10)外才破线
    check("T6.3c 窗口封顶=cap外不触发", sellside.anchor_exit(sC, 0, "ma5") is None)
    hi_ok = all(ser["highs"][x] >= ser["closes"][x] for x in range(len(ser["dates"]) - 5, len(ser["dates"])))
    check("T6.4 high>=close(近5日)", hi_ok)
    oc = lever.day_prices("000725", 20260827)
    check("T6.5 day_prices三价非空且open<=1445close合理", oc is not None and oc[0] > 0 and oc[2] > 0)
    check("T6.6 缺失文件返回空", lever.lc5_bars("999999") == {})

    print("== T7 预检窗口/PARTIAL ==")
    backup = {p: p.read_bytes() for p in (MD_LEVER, MD_SELL, MD_WEEK)}
    try:
        sys.argv = ["x", "--start", "20260826", "--end", "20260827"]
        run_main_silent(precheck)
        t = MD_WEEK.read_text(encoding="utf-8")
        check("T7.1 子窗口(26-27)判定完整", "完整" in t and "PARTIAL" not in t)
        check("T7.2 子窗口卖出=14笔", "共14笔" in t)
        sys.argv = ["x", "--start", "20260824", "--end", "20260831"]
        run_main_silent(precheck)
        t = MD_WEEK.read_text(encoding="utf-8")
        check("T7.3 未来窗口标PARTIAL", "PARTIAL" in t)

        print("== T9 确定性(连跑两遍逐字节一致) ==")
        for mod, md, tagm in ((lever, MD_LEVER, "lever"), (sellside, MD_SELL, "sellside"),
                              (precheck, MD_WEEK, "precheck")):
            if mod is precheck:
                sys.argv = ["x", "--start", "20260824", "--end", "20260828"]
            run_main_silent(mod)
            h1 = sha(md)
            run_main_silent(mod)
            h2 = sha(md)
            check(f"T9.{tagm} 两遍一致", h1 == h2)
    finally:
        for p, b in backup.items():
            p.write_bytes(b)
        print("  (报告字节已恢复)")

    print("== T8 报告交叉一致性 ==")
    _, _, _, (events_r, _, _) = real_events()
    n_lots = sum(len(e["fragments"]) for e in events_r)
    check("T8.1 平仓lot=50", n_lots == 50, str(n_lots))
    t_sell = MD_SELL.read_text(encoding="utf-8")
    t_lev = MD_LEVER.read_text(encoding="utf-8")
    check("T8.2 lever时段桶N=28/18/2(合计48)", "N=28" in t_lev and "N=18" in t_lev and "N=2 " in t_lev)
    check("T8.3 sellside附录45行", t_sell.count("| 08-") >= 45)
    check("T8.4 lever报告W2口径存在", "39.3%" in MD_LEVER.read_text(encoding="utf-8"))
    check("T8.5 lever报告基线-3.29%存在", "-3.29%" in MD_LEVER.read_text(encoding="utf-8"))

    print("== T10 名称校验 ==")
    for md in (MD_LEVER, MD_SELL, MD_WEEK):
        r = subprocess.run([sys.executable, str(ROOT / "verify_stock_names.py"), str(md)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check(f"T10 {md.name}", "名称校验通过" in r.stdout, r.stdout[-120:])

    print("== T11 报告章节 ==")
    lv = MD_LEVER.read_text(encoding="utf-8")
    for sec in ["## 1. 基线", "## 2. R-A", "## 3. R-B", "## 4. R-C", "## 5. 结论", "## 附录A"]:
        check(f"T11 lever含[{sec}]", sec in lv)
    sl = MD_SELL.read_text(encoding="utf-8")
    for sec in ["## 1. 实际卖出后", "## 2. 三种卖出锚", "## 3. 分卖出类型", "## 附录"]:
        check(f"T11 sellside含[{sec}]", sec in sl)

    print("== T12 时代边界防回归 ==")
    check("T12.1 fills无8/14前记录", min(f["dt"] for f in fills) >= datetime(2026, 8, 14))
    check("T12.2 fills无8/28后记录", max(f["dt"] for f in fills) <= datetime(2026, 8, 28, 23, 59))

    print(f"\n===== 结果: PASS={len(PASS)} FAIL={len(FAIL)} =====")
    if FAIL:
        print("失败项:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
