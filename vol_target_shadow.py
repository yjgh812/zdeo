#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vol_target_shadow.py — 波动率目标仓位 账户级影子（PD-015 采纳路径第一步, 10/2 主人「继续」批准）

纯记录零交易权（不下单不调仓, 只记"若按波动率目标调仓"的反事实数据）, 60 交易日期满判读:
  模拟曲线(账户日收益×当日目标权重) vs 实际账户净值 → 波动/MDD/CAGR 三对照 → 呈主人裁决是否采用。
判据冻结 = data/PREREG_VOLTARGET_SHADOW_20261002.json。

每日记录（收盘后, schtasks VolTargetShadow_1540）:
  date / total_assets(总资产,westock唯一真值) / exposure(实际仓位=持仓市值/总资产) /
  nav_vol20_ann(账户已实现20日年化波动, NAV序列≥21行才输出, 否则null) /
  idx_vol20_ann(沪深300代理波动, 本地vipdoc即刻可用) / w15_account / w15_idx / w10_idx /
  ret_1d(账户日收益, 次日起) 。同日重跑=UPSERT最后一行赢。

诚实口径: 主口径=账户已实现波动(前20交易日为积累期, 输出null); 代理口径=指数波动(即刻可用但
指数≠持仓, 仅作早期参考); 两者在台账并行标注, 判读时分开呈现不混用。
"""
import json
import os
import struct
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
SHADOW = os.path.join(ROOT, "data", "vol_target_shadow.jsonl")
DAY300 = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000300.day"
WINDOW, C15, C10 = 20, 0.15, 0.10


def _today():
    return time.strftime("%Y%m%d")


def _ann_vol(rets):
    if len(rets) < WINDOW:
        return None
    mean = sum(rets[-WINDOW:]) / WINDOW
    var = sum((r - mean) ** 2 for r in rets[-WINDOW:]) / (WINDOW - 1)
    return (var ** 0.5) * (244 ** 0.5)


def _idx_vol_only():
    """沪深300代理波动(本地vipdoc sh000300.day 20日年化); 指数≠持仓仅早期参考"""
    try:
        closes = []
        with open(DAY300, "rb") as f:
            while True:
                buf = f.read(32)
                if len(buf) < 32:
                    break
                d, o, h, l, c, amount, vol, _ = struct.unpack("<IIIIIfII", buf)
                closes.append(c / 100.0)
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        return _ann_vol(rets)
    except Exception as e:
        print(f"[WARN] 指数代理波动读取失败: {type(e).__name__}: {e}")
        return None


def main() -> int:
    from trade_day_guard import is_trading_day
    today = _today()
    if not is_trading_day(today):
        print(f"[GUARD] {today} 非交易日 → 跳过")
        return 0
    if r"D:\workbuddyspace" not in sys.path:
        sys.path.insert(0, r"D:\workbuddyspace")
    from westock_client import WeStockClient
    ws = WeStockClient()
    pf = ws.portfolio() or {}
    inner = ((pf.get("data") or {}).get("portfolio")) or pf.get("data") or pf
    total = float(inner.get("totalAssets") or inner.get("total_assets") or 0)
    pos = ((ws.positions().get("data") or {}).get("positions")) or []
    mv = 0.0
    for p in pos:
        px = float(p.get("currentPrice") or p.get("current_price") or 0)
        q = float(p.get("quantity") or p.get("qty") or 0)
        if px > 0 and q > 0:
            mv += px * q
    if total <= 0:
        print("[FAIL-CLOSED] 总资产不可得(westock) → 本轮不记录")
        return 3
    exposure = mv / total

    rows = [json.loads(l) for l in open(SHADOW, encoding="utf-8") if l.strip()] \
        if os.path.exists(SHADOW) else []
    hist = [r for r in rows if r.get("date") != today]
    navs = [float(r["total_assets"]) for r in hist[-(WINDOW + 1):]]
    rets, r1 = [], None
    if navs and navs[-1] > 0:
        r1 = total / navs[-1] - 1
        all_navs = [float(r["total_assets"]) for r in hist]
        rets = [all_navs[i] / all_navs[i - 1] - 1 for i in range(1, len(all_navs))
                if all_navs[i - 1] > 0]
    nav_vol = _ann_vol(rets) if len(rets) >= WINDOW else None
    idx_vol = _idx_vol_only()

    row = {"date": today, "total_assets": round(total, 2), "exposure": round(exposure, 4),
           "ret_1d": round(r1, 6) if r1 is not None else None,
           "nav_vol20_ann": round(nav_vol, 4) if nav_vol else None,
           "idx_vol20_ann": round(idx_vol, 4) if idx_vol else None,
           "w15_account": round(min(1.0, C15 / nav_vol), 4) if nav_vol else None,
           "w15_idx": round(min(1.0, C15 / idx_vol), 4) if idx_vol else None,
           "w10_idx": round(min(1.0, C10 / idx_vol), 4) if idx_vol else None,
           "n_nav_points": len(hist) + 1,
           "ts": time.strftime("%F %T")}
    out = hist + [row]
    with open(SHADOW, "w", encoding="utf-8", newline="\n") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[SHADOW] {today} 总资产{total:,.0f} 实际仓位{exposure:.1%} "
          f"账户波动={f'{nav_vol:.1%}' if nav_vol else f'积累中({len(rets)}/{WINDOW}日)'} "
          f"指数代理波动={f'{idx_vol:.1%}' if idx_vol else 'N/A'} "
          f"w15_idx={row['w15_idx']} → {SHADOW}")
    return 0


def _idx_vol_only():
    try:
        closes = []
        with open(DAY300, "rb") as f:
            while True:
                buf = f.read(32)
                if len(buf) < 32:
                    break
                d, o, h, l, c, amount, vol, _ = struct.unpack("<IIIIIfII", buf)
                closes.append(c / 100.0)
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        return _ann_vol(rets)
    except Exception as e:
        print(f"[WARN] 指数代理波动读取失败: {type(e).__name__}: {e}")
        return None


if __name__ == "__main__":
    sys.exit(main())
