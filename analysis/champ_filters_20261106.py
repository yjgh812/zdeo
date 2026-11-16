# -*- coding: utf-8 -*-
"""champ_filters_20261106.py — 冠军股归因三条件过滤器(全历史27259事件验证, 主人令20261106)

真过滤器(采纳, 三条件全满足: 5日均+2.97% 胜率56% vs 基准-0.5% 胜率40%):
  LOW_PRICE  : 信号日收盘 < 15 元
  LOW_POS60  : pos60 < 40% (信号日前60日收盘区间位置, 不含信号日)
  QUIET_VOL  : 信号日量 / 前20日均量 < 2.5 (未放量)

证伪条件(不采纳, 防复活):
  MAINLINE_IND(主线行业加分) —— 全样本方向相反: 低位且主线 -0.41%/5日 vs 低位且非主线
  +2.75%/5日。行业20日涨幅前3时板块已炒高, 行业内低位首板多为补涨尾巴(幸存者偏差:
  冠军股东方海洋/龙版传媒均属11/2传媒潮, 但全样本主线内低位票整体亏损)。
  证据件: draft工作区 champ_filter_backtest.py + champ_bt.log (20261106)。

证据边界(诚实标注): 回测事件口径=首板封板事件(2024/01-2026/11, T+1开盘买入)。
接入振幅A/组合C等其他方法属假设迁移, 有效性由模拟盘赛马验证(11/26首考)。
分年警示: 2024 +6.44%/胜率69% -> 2025 +0.12% -> 2026 -0.50%, alpha衰减中,
定位=低胜率高赔率尾部打法(小注广撒+让利润奔跑), 不因个别浮亏加码。

用法:
    from champ_filters_20261106 import champ3
    ok, detail = champ3(code, {"c": [...], "v": [...]}, i=-1)   # i=信号日索引
"""
import numpy as np

LOW_PRICE_MAX = 15.0
LOW_POS60_MAX = 40.0
QUIET_VOLR_MAX = 2.5


def champ3(code: str, s: dict, i: int = -1) -> tuple[bool, dict]:
    """三条件过滤器。s 需含 close/volume 序列(list/np)；i=信号日索引(默认末位)。

    返回 (passed, detail)；passed=False 时 detail 标注被拦条件。
    数据不足(窗口不够)时 fail-open 放行并标注(过滤器是优选器不是硬闸)。
    """
    c = s.get("c") or s.get("close")
    v = s.get("v") or s.get("vol") or s.get("volume")
    if c is None or v is None:
        return True, {"note": "无数据fail-open"}
    n = len(c)
    idx = n + i if i < 0 else i
    d = {"price": round(float(c[idx]), 2)}
    ok = True
    # 低价
    d["low_price"] = bool(c[idx] < LOW_PRICE_MAX)
    # 低位 pos60(前60日, 不含信号日)
    lo = min(idx - 60, idx)
    seg = c[max(0, lo):idx]
    if len(seg) >= 20:
        mn, mx = float(np.min(seg)), float(np.max(seg))
        pos = (c[idx] - mn) / (mx - mn) * 100 if mx > mn else 50.0
        d["pos60"] = round(pos, 1)
        d["low_pos"] = bool(pos < LOW_POS60_MAX)
    else:
        d["low_pos"] = True  # 数据不足fail-open
    # 未放量
    vseg = [x for x in v[max(0, idx - 20):idx] if x and x > 0]
    if vseg and v[idx] and v[idx] > 0:
        volr = float(v[idx]) / float(np.mean(vseg))
        d["volr"] = round(volr, 2)
        d["quiet_vol"] = bool(volr < QUIET_VOLR_MAX)
    else:
        d["quiet_vol"] = True  # 数据不足fail-open
    d["blocked_by"] = [k for k in ("low_price", "low_pos", "quiet_vol") if not d[k]]
    ok = not d["blocked_by"]
    return ok, d
