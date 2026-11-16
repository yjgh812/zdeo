# -*- coding: utf-8 -*-
"""持仓组合波段相位探针（2026-09-21 进化周期15 · 知识落地率审计遗漏①接线）

知识源: reports/swing_c_series_20260919/C3_phase_volume.md（相位×量能路径全史统计）
  上涨段(≥20%)典型路径 0.89→1.08→1.65（缩量启动→温和放量→爆量末段, n=90,346, vr3 P50≈1.65）
  下跌段镜像 1.26→0.81→0.71（放量启动→缩量, 衰竭规范=缩量≤0.85）
口径(无前视近似):
  当前段 = zigzag(θ15%) 最后拐点→最新交易日的未确认段（陷阱12: 实盘状态推断用原始zigzag尾部）
  vr_now = 最近5日均量 / 段起点前120交易日日均量（基线仅用段前信息）
  相位标签 = vr_now 对照全史统计画像阈值的特征匹配（描述性, 非精确相位判定）
诚实声明: 段端点含事后信息, 相位为特征近似; 本探针只读提请, 零交易权, 非卖出建议。
用法:
  python swing_phase_probe.py                    # 读 westock 实时持仓
  python swing_phase_probe.py --codes 003006,603230
  python swing_phase_probe.py --json-only        # 仅供程序消费(holdings_risk_scan)
输出: data/swing_phase_holdings_latest.json + stdout 汇总
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 主库(副本滞后禁用于生产呈报)
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402
from wave_feature_engine import zigzag  # noqa: E402  唯一owner复用, 不复制实现

THETA = 0.15          # C3 研究口径 θ15%
BASE_N = 120          # 基线窗口
RECENT_N = 5          # 当前量能窗口
VR_END_UP = 1.5       # 上涨段末段爆量特征阈值(C3: vr3>1.5 占83.9%段; 画像P50 1.65)
VR_QUIET = 0.9        # 缩量特征阈值(启动画像 P50 0.89)
VR_DN_EXH = 0.85      # 下跌衰竭缩量规范(wave_harvest_5yr)
VR_DN_HEAVY = 1.26    # 下跌段放量中继画像(顶部画像 1.26x)
AMP_MATURE = 20.0     # 段成熟阈值(C3 研究对象=幅度≥20%的段)
OUT_JSON = ROOT + r"\data\swing_phase_holdings_latest.json"


def f3(x):
    try:
        return float(x)
    except Exception:
        return None


def load_positions() -> list[dict]:
    sys.path.insert(0, r"D:\workbuddyspace")
    from westock_client import WeStockClient
    resp = WeStockClient().call("portfolio_paper_positions", {})
    pos = (resp.get("data") or {}).get("positions") or []
    if not pos:
        raise RuntimeError("westock 持仓返回空")
    return [{"code": p["code"][-6:], "name": p["name"]} for p in pos]


def probe_one(code: str, name: str) -> dict:
    """单票当前波段相位推断。结构化返回, 失败返回 err 字段。"""
    db = sqlite3.connect(QFQ, uri=True)
    rows = db.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date ASC",
        (code,)).fetchall()
    db.close()
    data = [(d, f3(c), f3(v)) for d, c, v in rows if f3(c) is not None]
    if len(data) < 300:
        return {"code": code, "name": name, "err": f"数据不足({len(data)})"}

    dates = [x[0] for x in data]
    closes = [x[1] for x in data]
    vols = [x[2] or 0.0 for x in data]
    w = -750 if len(closes) > 750 else None  # 近三年窗口, 与 wave_engine 一致
    if w:
        dates, closes, vols = dates[w:], closes[w:], vols[w:]

    pivots = zigzag(closes, THETA)
    last_pv = pivots[-1]
    n = len(closes) - 1
    if last_pv >= n:  # 恰在拐点: 取前一段已确认段评估
        if len(pivots) < 3:
            return {"code": code, "name": name, "err": "拐点结构不足"}
        last_pv = pivots[-2]

    seg_chg = (closes[n] / closes[last_pv] - 1) * 100 if closes[last_pv] else 0.0
    direction = "up" if seg_chg > 0 else "down"
    seg_days = n - last_pv
    if last_pv < BASE_N:
        return {"code": code, "name": name, "err": f"基线窗口不足({last_pv})"}
    base = sum(vols[last_pv - BASE_N:last_pv]) / BASE_N
    if base <= 0:
        return {"code": code, "name": name, "err": "基线无效(≤0)"}
    vr_now = (sum(vols[n - RECENT_N + 1:]) / RECENT_N) / base

    # 相位特征标签(描述性: vr_now 对照全史统计画像)
    mature = abs(seg_chg) >= AMP_MATURE
    if direction == "up":
        if vr_now >= VR_END_UP:
            phase = "爆量末段特征"
            flag = "END"
        elif vr_now < VR_QUIET:
            phase = "缩量(启动/中继)特征"
            flag = "QUIET"
        else:
            phase = "温和放量"
            flag = "MID"
    else:
        if vr_now <= VR_DN_EXH:
            phase = "缩量衰竭特征"
            flag = "EXH"
        elif vr_now >= VR_DN_HEAVY:
            phase = "放量下跌(启动/中继)特征"
            flag = "HEAVY"
        else:
            phase = "温和"
            flag = "MID"
    if not mature:
        phase += "(段未成熟<" + str(int(AMP_MATURE)) + "%)"

    return {"code": code, "name": name, "date": dates[n], "direction": direction,
            "seg_chg": round(seg_chg, 1), "seg_days": seg_days,
            "vr_now": round(vr_now, 2), "phase": phase, "flag": flag,
            "seg_mature": mature}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="逗号分隔6位码; 空=读westock持仓")
    ap.add_argument("--json-only", action="store_true", help="仅输出JSON行(程序消费)")
    args = ap.parse_args()

    try:
        if args.codes:
            targets = [{"code": c.strip(), "name": get_name(c.strip()) or "?"}
                       for c in args.codes.split(",") if c.strip()]
        else:
            targets = load_positions()
    except Exception as e:
        print(f'[PHASE-FAIL] 持仓/参数失败: {e}')
        return 2

    recs = [probe_one(t["code"], t["name"]) for t in targets]
    ok = [r for r in recs if "err" not in r]
    bad = [r for r in recs if "err" in r]
    ups = [r for r in ok if r["direction"] == "up"]
    dns = [r for r in ok if r["direction"] == "down"]
    up_end = [r for r in ups if r["flag"] == "END"]
    dn_exh = [r for r in dns if r["flag"] == "EXH"]
    summary = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_total": len(recs), "n_ok": len(ok), "n_err": len(bad),
        "up_segments": len(ups), "up_end_vol": len(up_end),
        "up_end_pct": round(len(up_end) / len(ups) * 100, 1) if ups else None,
        "down_segments": len(dns), "down_exhaust": len(dn_exh),
        "down_exh_pct": round(len(dn_exh) / len(dns) * 100, 1) if dns else None,
        "decl": ("相位=当前未确认段量能特征对照全史统计画像(C3, theta15%/基线120日/近5日vr), "
                 "描述性非信号, 零交易权"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": recs}, f, ensure_ascii=False, indent=1)

    if args.json_only:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    print(f"[PHASE-OK] 组合波段相位: 上涨段 {len(ups)}/{len(ok)} 只(末段爆量特征 {len(up_end)} 只"
          f"{'/' + str(summary['up_end_pct']) + '%' if summary['up_end_pct'] is not None else ''})"
          f" | 下跌段 {len(dns)} 只(衰竭特征 {len(dn_exh)} 只) | 解析失败 {len(bad)} 只")
    for r in sorted(ok, key=lambda x: -x["vr_now"]):
        print(f"  {r['code']} {r['name']}｜{'涨' if r['direction'] == 'up' else '跌'}段"
              f" {r['seg_chg']:+.1f}%/{r['seg_days']}日｜vr_now {r['vr_now']:.2f}｜{r['phase']}")
    for r in bad:
        print(f"  {r['code']} {r['name']}｜解析失败: {r['err']}")
    print(f"[PHASE-OK] 台账: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
