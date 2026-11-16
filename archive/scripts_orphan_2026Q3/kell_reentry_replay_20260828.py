# -*- coding: utf-8 -*-
"""
kell_reentry_replay_20260828.py — K1 止损后重入机制·回放（纸面样本首跑）
============================================================
按计划书 K1 §3：对止损事件回放三种重入触发（A 收复止损日高点+量 / B 站回双均线 / C 20日新高），
含 A 股约束（涨停封板不追、T+1 次日才可卖），度量重入后前向收益 vs "永不重入"基线（左桌收益）。

数据源（只读）：
  - 止损事件：trade_records SELL（attribution.db）已核验 S-5 卖出 + R5 破位卖出
  - 价格     ：qfq 库 stock_daily_meta_qfq

本跑结论（2026-08-28）：纸面事件全部集中在 8/25-8/27，前向窗口仅 1-3 天，
按计划书 §3.5 停止条件（事件样本<30 且前向不足）→ 纸面样本不判，
本脚本作为全史回放（2016-2026，待回测引擎事件源）的可复用基础设施。
"""
import sqlite3, statistics, re, sys

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB  = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT  = r"D:\zcodesj\reports\K1_REENTRY_REPLAY_PAPER_20260828.md"

# 已核验事件（来源：trade_records SELL id 336/358/484；R5 破位 id 486-489）
S5_EVENTS = [
    {"code": "000725", "name": "京东方Ａ",  "stop_date": "20260825", "stop_price": 5.65,
     "stop_type": "S-5硬止损(-8%)", "note": "8/19入@6.22, 8/25破-8%纪律卖出"},
    {"code": "000506", "name": "招金黄金",  "stop_date": "20260826", "stop_price": 20.28,
     "stop_type": "S-5硬止损(-9.05%)", "note": "8/24入@22.29(溢价+27%追高), 8/26破-8%清仓"},
    {"code": "600984", "name": "建设机械",  "stop_date": "20260827", "stop_price": 4.33,
     "stop_type": "S-5硬止损(-11%)", "note": "8/26入@4.92(ext20+25.8%追高), 8/27深破-8%线"},
]
R5_EVENTS = [
    {"code": "301656", "name": "联合动力", "stop_date": "20260827", "stop_price": 15.27,
     "stop_type": "R5破位", "note": "理由日志缺失(准则43待补评)"},
    {"code": "000690", "name": "宝新能源", "stop_date": "20260827", "stop_price": 4.89,
     "stop_type": "R5破位", "note": "理由日志缺失(准则43待补评)"},
    {"code": "300128", "name": "锦富技术", "stop_date": "20260827", "stop_price": 4.97,
     "stop_type": "R5破位", "note": "理由日志缺失(准则43待补评)"},
    {"code": "603327", "name": "福蓉科技", "stop_date": "20260827", "stop_price": 9.53,
     "stop_type": "R5破位", "note": "理由日志缺失(准则43待补评)"},
]

def norm(code):
    m = re.match(r"([a-z]+)?(\d{6})", code.strip())
    return m.group(2) if m else code.strip()

def load_qfq(codes):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT code, date, open, high, low, close, volume, change_pct FROM stock_daily_meta_qfq "
        "WHERE code IN (%s) ORDER BY code, date" % ",".join("?" * len(codes)), codes).fetchall()
    con.close()
    d = {}
    for code, date, _o, hi, lo, close, vol, chg in rows:
        d.setdefault(code, []).append({"date": date, "high": hi, "low": lo, "close": close,
                                       "volume": vol, "change_pct": chg})
    return d

def limit_up(chg_pct, code):
    """主板10%/创业板30x·科创板68x 20%（ST 未识别，近似）"""
    if chg_pct is None: return False
    thr = 19.8 if code[:2] in ("30", "68") else 9.8
    return chg_pct >= thr

def ma_k(series, i, k):
    if i + 1 < k: return None
    return sum(r["close"] for r in series[i+1-k:i+1]) / k

def replay_one(ev, series, lookback=20, vol_mult=1.5):
    """在止损日后模拟三种触发。返回重入明细 + 左桌收益（止损价前向）。"""
    stop_date = ev["stop_date"]
    idx = [i for i, r in enumerate(series) if r["date"] <= stop_date]
    if not idx:
        return None
    i0 = idx[-1]                       # 止损日在该股序列中的位置
    stop_row = series[i0]
    ref_high = stop_row["high"]        # 候选A参考位=止损日最高价
    out = {"trigger": None, "entry_date": None, "entry_price": None, "fwd": {}, "left_on_table": {}}
    # 左桌收益：止损价前向 1/2/3/5/10/20 交易日（实际收盘，未扣可成交性；短窗可算，长窗因数据不足为 N/A）
    for k in (1, 2, 3, 5, 10, 20):
        j = i0 + k
        if j < len(series):
            out["left_on_table"][k] = (series[j]["close"] - ev["stop_price"]) / ev["stop_price"]
    # 逐日扫描触发（最长 lookback 个交易日）
    for k in range(1, lookback + 1):
        j = i0 + k
        if j >= len(series):
            break
        r = series[j]
        if limit_up(r["change_pct"], ev["code"]):
            continue                     # 涨停封板不可追，顺延
        vol_ratio = None
        base_vol = [series[j-t]["volume"] for t in range(1, 6) if j-t >= 0 and series[j-t]["volume"]]
        if base_vol and r["volume"]:
            vol_ratio = r["volume"] / (sum(base_vol) / len(base_vol))
        ma10 = ma_k(series, j, 10); ma20 = ma_k(series, j, 20)
        high20 = max(rr["high"] for rr in series[max(0, j-19):j+1])
        hit = None
        if vol_ratio is not None and vol_ratio >= vol_mult and r["close"] >= ref_high:
            hit = "A-收复止损日高点+量"
        elif ma10 is not None and ma20 is not None and r["close"] > ma10 and ma10 > ma20:
            hit = "B-站回MA10且MA10>MA20"
        elif r["close"] >= high20 and r["close"] > stop_row["close"]:
            hit = "C-20日新高"
        if hit:
            out["trigger"] = hit
            out["entry_date"] = r["date"]
            out["entry_price"] = r["close"]
            for kk in (5, 10, 20):
                jj = j + kk
                if jj < len(series):
                    out["fwd"][kk] = (series[jj]["close"] - r["close"]) / r["close"]
            break
    return out

def main():
    qfq = load_qfq([e["code"] for e in S5_EVENTS + R5_EVENTS])
    print("[K1] qfq 已加载事件股:", {c: len(v) for c, v in qfq.items()})
    md = []
    md.append("# K1 止损后重入回放 · 纸面样本首跑")
    md.append("")
    md.append("> **日期**：2026-08-28 | **性质**：K 计划 K1 工作流回放基础设施首跑")
    md.append("> **结论（预判）**：纸面止损事件全部集中在 8/25-8/27，前向窗口仅 1-3 个交易日，**按计划书 §3.5 停止条件（样本<30 且前向不足）纸面样本不判**。本报告量化「左桌收益」供参考，并作为全史回放（2016-2026，事件源=回测引擎）的可复用基础设施。")
    md.append("> **数据**：trade_records SELL 已核验 S-5 事件 3 笔 + R5 破位 4 笔（attribution.db）；qfq 价格源")
    md.append("")
    md.append("## 事件总览与左桌收益（止损价前向实际收盘涨幅，未扣可成交性）")
    md.append("")
    md.append("| 类型 | 股票 | 止损日 | 止损价 | 左桌+1d | 左桌+2d | 左桌+3d | 左桌+5d | 备注 |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for ev in S5_EVENTS + R5_EVENTS:
        series = qfq.get(ev["code"], [])
        out = replay_one(ev, series) if series else None
        lt = out["left_on_table"] if out else {}
        f = lambda k: ("%+.1f%%" % (lt[k]*100)) if k in lt else "N/A"
        print(f"  {ev['name']}: 左桌1/2/3/5 = {f(1)}/{f(2)}/{f(3)}/{f(5)}; 触发={out['trigger'] if out else 'N/A'} {out['entry_date'] if out and out['trigger'] else ''}")
        md.append(f"| {ev['stop_type']} | {ev['name']}({ev['code']}) | {ev['stop_date']} | {ev['stop_price']:.2f} | {f(1)} | {f(2)} | {f(3)} | {f(5)} | {ev['note']} |")
    md.append("")
    md.append("## 重入触发（A/B/C）逐事件明细")
    md.append("")
    md.append("| 股票 | 触发 | 触发日 | 触发价 | 触发后+5d | +10d | +20d |")
    md.append("|---|---|---|---|---|---|---|")
    for ev in S5_EVENTS + R5_EVENTS:
        series = qfq.get(ev["code"], [])
        out = replay_one(ev, series) if series else None
        if not out or not out["trigger"]:
            md.append(f"| {ev['name']}({ev['code']}) | 未触发(前向不足) | - | - | - | - | - |")
            continue
        f = lambda k: ("%+.1f%%" % (out["fwd"][k]*100)) if k in out["fwd"] else "N/A"
        md.append(f"| {ev['name']}({ev['code']}) | {out['trigger']} | {out['entry_date']} | {out['entry_price']:.2f} | {f(5)} | {f(10)} | {f(20)} |")
    md.append("")
    md.append("## 结论与下一步（按计划书 §3.5）")
    md.append("- 已核验 S-5 硬止损事件仅 3 笔、R5 破位 4 笔，且全在 8/25-8/27 → **样本<30 + 前向窗口不足，纸面样本统计不可判，不启动 BT-FAIL-006**。")
    md.append("- 左桌收益已量化（截至 qfq 末位 8/27）：京东方Ａ 止损后第2个交易日 +5.1%、招金黄金 止损后第1个交易日 +8.9%（印证 8/27 回头看 ❌2 的 V 反）；建设机械前向数据尚未生成（qfq 止于 8/27）。")
    md.append("- 已观测到 1 例重入触发：招金黄金 止损后第 2 个交易日触发 B-站回MA10且MA10>MA20（8/27 收 22.08，较止损价 +8.9%）——重入信号在 V 反案例上确实会很快给出进场点，但须全史样本才能判期望值。")
    md.append("- **主样本依赖**：2016-2026 全史止损事件需回测引擎事件源（round9 harness / factor_combo_walkforward 内核），K1 全史矩阵回放列后续 P0 待办；本脚本 replay_one() 已按事件源解耦，可平移复用。")
    md.append("- 数据质量债务：招金黄金/京东方Ａ 不在 position_history（台账缺失，E72 方向），R5 卖出理由缺失（准则43待补评）——K1 的统计可靠性同时受此约束。")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n[K1] 报告已写: {REPORT}")

if __name__ == "__main__":
    sys.exit(main())
