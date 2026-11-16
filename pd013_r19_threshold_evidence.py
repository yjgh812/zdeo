# -*- coding: utf-8 -*-
"""pd013_r19_threshold_evidence.py —— PD-013(R19 转拦截)阈值证据件（只读研究, 零交易权）

来源: R19 数学段落地后的裁决证据缺口（主人令"继续"第五轮; PD-013 SLA=5交易日）
问题: 当前只知弘信电子(300657) 权重26%/RC@ρ0.3=50.9%, 不知道这在组合结构空间里多罕见、
      减仓到多少能修复 → 本件给 PD-013 裁决供两张表:
  第一部分(参数化, 恒可算): 当前真实持仓×真实σ20 的**减仓敏感表**——
      弘信 26%→{20,15,10,5}% 四情景(腾出权重按现比例回配其余持仓)下的 N_eff/σp@ρ0.3/0.7/最大单票RC。
  第二部分(历史回放, 带验证闸): trade_records(7/22~9/16)+executor_orders 补充 BUY 重建逐日持仓权重,
      逐日算 HHI/N_eff/最大RC 序列; **验证闸**=末日重建持仓码集 vs westock 实时42只, 覆盖率<90% 整段降级
      (App端成交缺口/数量不齐时历史权重不可信, 如实标注只用参数化)。
输出: data/pd013_evidence_20260920.json + reports/pd013_threshold_evidence_20260920.md
红线: 不设阈值建议值(裁决归主人); RC 公式 owner=tulong_math_toolkit(等ρ近似, 簇结构不适用已注明)。
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
WS_PATH = r"D:\workbuddyspace"
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
sys.path.insert(0, ROOT)
sys.path.insert(0, WS_PATH)
from tulong_math_toolkit import (  # noqa: E402
    effective_positions, equicor_risk_shares, hhi, portfolio_vol_equicor)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_current() -> tuple[list[dict], dict[str, float]]:
    """当前持仓(权重) + σ20; 复用 R19 的通道与函数(owner 唯一)"""
    from holdings_concentration_monitor import load_positions, qfq_vol20
    positions = load_positions()
    total = sum(p["mv"] for p in positions)
    for p in positions:
        p["w"] = p["mv"] / total
    return positions, qfq_vol20([p["code"] for p in positions])


def metrics(weights: list[float], sigmas: list[float]) -> dict:
    var = hhi(weights)
    rc03 = equicor_risk_shares(weights, sigmas, 0.3)
    rc07 = equicor_risk_shares(weights, sigmas, 0.7)
    return {"n_eff": round(effective_positions(weights), 1),
            "sigma_p_annual_03": round(portfolio_vol_equicor(weights, sigmas, 0.3) * 250 ** 0.5 * 100, 1),
            "sigma_p_annual_07": round(portfolio_vol_equicor(weights, sigmas, 0.7) * 250 ** 0.5 * 100, 1),
            "max_w_pct": round(max(weights) * 100, 1),
            "max_rc03_pct": round(max(rc03) * 100, 1), "max_rc07_pct": round(max(rc07) * 100, 1)}


def parametric(positions: list[dict], vols: dict[str, float]) -> dict:
    """减仓敏感表: 最大权重持仓压到 cap, 腾出权重按现比例回配其余"""
    med = sorted(v for v in vols.values() if v == v)
    med = med[len(med) // 2] if med else 0.02
    order = sorted(positions, key=lambda p: -p["w"])
    top = order[0]

    def build(cap: float | None):
        ws, ss = [], []
        tw = cap if cap is not None else top["w"]
        rest_total = 1.0 - tw
        rest_w = sum(p["w"] for p in positions if p["code"] != top["code"])
        for p in positions:
            if p["code"] == top["code"]:
                w = tw
            else:
                w = p["w"] / rest_w * rest_total if rest_w > 0 else 0.0
            v = vols.get(p["code"], float("nan"))
            ws.append(w)
            ss.append(med if v != v else v)
        return ws, ss

    out = {"top_code": top["code"], "top_name": top["name"], "scenarios": {}}
    for cap in (None, 0.20, 0.15, 0.10, 0.05):
        ws, ss = build(cap)
        label = f"压到{cap * 100:.0f}%" if cap else "现状"
        out["scenarios"][label] = metrics(ws, ss)
    return out


def history_replay(westock_codes: set[str]) -> tuple[dict | None, float]:
    """逐日权重重建; 返回 (序列摘要或None, 验证覆盖率)"""
    events: dict[str, list[tuple[str, str, float]]] = {}   # date8 -> [(code, ±vol)]
    seen_buy = set()
    try:
        con = sqlite3.connect("file:" + ATTR_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
        for code, td, action, vol in con.execute(
                "SELECT code, trade_date, action, volume FROM trade_records ORDER BY trade_date, id"):
            code, td, vol = str(code)[-6:], str(td).replace("-", "")[:8], float(vol or 0)
            if not vol:
                continue
            if action == "BUY":
                seen_buy.add((code, td))
                events.setdefault(td, []).append((code, vol))
            elif action == "SELL":
                events.setdefault(td, []).append((code, -vol))
        con.close()
    except Exception as e:
        print(f"[PD013-WARN] trade_records 读取失败: {e}")
        return None, 0.0
    try:  # executor_orders BUY 补充(同code同日已记则跳过防双计)
        for line in open(os.path.join(ROOT, "data", "executor_orders.jsonl"), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("action") != "BUY":
                continue
            code, td = str(d.get("code", ""))[-6:], str(d.get("trade_date", "")).replace("-", "")[:8]
            q = float(d.get("qty") or 0)
            if code and td and q and (code, td) not in seen_buy:
                events.setdefault(td, []).append((code, q))
    except Exception as e:
        print(f"[PD013-WARN] executor_orders 补充读取失败: {e}")

    if not events:
        return None, 0.0
    qfq = sqlite3.connect(QFQ_DB, uri=True)
    days = [str(r[0]) for r in qfq.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE code='600000' ORDER BY date")]
    d0 = min(events)
    days = [d for d in days if d >= d0]
    codes = {c for evs in events.values() for c, _ in evs}
    closes: dict[str, dict[str, float]] = {}
    ph = ",".join("?" * len(codes))
    for code, d, c in qfq.execute(
            f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>=?", tuple(codes) + (d0,)):
        closes.setdefault(code, {})[str(d)] = float(c)
    qfq.close()

    shares: dict[str, float] = {}
    series, anomalies = [], 0
    for t in days:
        for code, dv in events.get(t, []):
            shares[code] = max(0.0, shares.get(code, 0.0) + dv)
            if shares[code] == 0.0 and dv < 0:
                anomalies += 1
        vals = {}
        for code, q in shares.items():
            if q <= 0:
                continue
            cl = closes.get(code, {}).get(t)
            if cl is None:  # 停牌 → 最近收盘
                cl = next((v for d, v in sorted(closes.get(code, {}).items(), reverse=True) if d <= t), None)
            if cl:
                vals[code] = q * cl
        total = sum(vals.values())
        if len(vals) < 5 or total <= 0:
            continue
        ws = [v / total for v in vals.values()]
        # σ: 各code截至t的20日滚动σ(不足用全日中位)
        sigs, pool = [], []
        for code in vals:
            ser = sorted(closes.get(code, {}).items())
            idx = [i for i, (d, _) in enumerate(ser) if d <= t]
            rets = [ser[i][1] / ser[i - 1][1] - 1 for i in idx[-20:] if i > 0 and ser[i - 1][1] > 0]
            if len(rets) >= 10:
                m = sum(rets) / len(rets)
                s = (sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5
            else:
                s = None
            if s is not None:
                pool.append(s)
            sigs.append(s)
        med = sorted(pool)[len(pool) // 2] if pool else 0.02
        sigs = [med if s is None else s for s in sigs]
        m = metrics(ws, sigs)
        m["date"] = t
        m["n_hold"] = len(vals)
        series.append(m)
    if not series:
        return None, 0.0
    last_codes = {c for c, q in shares.items() if q > 0 and c in closes}
    cover = len(last_codes & westock_codes) / max(len(westock_codes), 1)
    if cover < 0.9:
        return {"rejected": True, "coverage": round(cover, 3), "anomalies": anomalies}, cover
    n_eff = sorted(s["n_eff"] for s in series)
    rc = sorted(s["max_rc03_pct"] for s in series)
    mx = sorted(s["max_w_pct"] for s in series)
    k = len(series)
    return {"rejected": False, "coverage": round(cover, 3), "anomalies": anomalies,
            "days": k, "range": f"{series[0]['date']}~{series[-1]['date']}",
            "n_eff_pct": {"min": n_eff[0], "median": n_eff[k // 2], "max": n_eff[-1]},
            "max_w_pct": {"min": mx[0], "median": mx[k // 2], "max": mx[-1]},
            "max_rc03_pct": {"min": rc[0], "median": rc[k // 2], "max": rc[-1]},
            "tail_samples": [s for s in series if s["max_rc03_pct"] >= 50][-5:]}, cover


def main() -> int:
    positions, vols = load_current()
    para = parametric(positions, vols)
    westock_codes = {p["code"] for p in positions}
    hist, cover = history_replay(westock_codes)

    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "current": metrics([p["w"] for p in positions],
                              [vols.get(p["code"], 0.02) for p in positions]),
           "parametric": para, "history": hist, "history_coverage": round(cover, 3),
           "caveats": ["等ρ近似不适用簇结构(电子簇26.8%需另看R19第二节)", "σ20为当前静态值历史段用滚动σ",
                       "历史重建含App端缺口风险已设90%验证闸", "本件不设阈值建议值, 裁决归主人(PD-013)"]}
    json.dump(out, open(os.path.join(ROOT, "data", "pd013_evidence_20260920.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)

    L = ["# PD-013 阈值证据：R19 集中度/RC 减仓敏感表 + 历史回放（只读）",
         "",
         f"> 当前组合: {json.dumps(out['current'], ensure_ascii=False)}",
         "",
         f"## 一、减仓敏感表（{para['top_name']}({para['top_code']}) 权重压制情景, 腾出权重按比例回配其余）",
         "| 情景 | N_eff | σp年@ρ0.3 | σp年@ρ0.7 | 最大权重% | 最大RC@ρ0.3% | 最大RC@ρ0.7% |",
         "|---|---|---|---|---|---|---|"]
    for label, m in para["scenarios"].items():
        L.append(f"| {label} | {m['n_eff']} | {m['sigma_p_annual_03']} | {m['sigma_p_annual_07']} "
                 f"| {m['max_w_pct']} | {m['max_rc03_pct']} | {m['max_rc07_pct']} |")
    L += ["", "## 二、历史回放（验证闸: 覆盖率≥90%才采用）"]
    if hist and not hist.get("rejected"):
        L += [f"- 采用✅ 覆盖率 {hist['coverage']} ｜ {hist['days']} 个交易日（{hist['range']}）｜ 卖空截断 {hist['anomalies']} 次",
              f"- 历史 N_eff 分布: min/median/max = {hist['n_eff_pct']}",
              f"- 历史最大权重%: {hist['max_w_pct']}",
              f"- 历史最大RC@ρ0.3%: {hist['max_rc03_pct']}",
              "- 近端 RC≥50% 样本: " + json.dumps(hist["tail_samples"], ensure_ascii=False)]
    else:
        L.append(f"- 降级❌ 覆盖率 {hist['coverage'] if hist else 0} <0.9（App端缺口/数量不齐）——历史分布不可信, 仅用第一部分参数化证据")
    L += ["", "## 三、口径局限", *[f"- {c}" for c in out["caveats"]],
          "", "## 使用说明",
          "- 本表回答\"减到多少修复结构\"的物理量; 阈值定多少(权重线/RC线/双线)与是否转拦截=主人 PD-013 裁决;",
          "- RC 由权重×σ共同决定: 压权重与换低波动票是两条修复路径, 表仅展示第一条。"]
    open(os.path.join(ROOT, "reports", "pd013_threshold_evidence_20260920.md"), "w", encoding="utf-8").write("\n".join(L))
    print(f"[PD013-OK] 当前: N_eff={out['current']['n_eff']} maxRC@0.3={out['current']['max_rc03_pct']}%")
    for label, m in para["scenarios"].items():
        print(f"  {label}: N_eff={m['n_eff']} maxRC@0.3={m['max_rc03_pct']}% σp@0.3={m['sigma_p_annual_03']}%")
    print(f"  历史回放: {'采用 cover=' + str(hist['coverage']) if hist and not hist.get('rejected') else '降级 ' + str(hist)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
