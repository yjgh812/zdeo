# -*- coding: utf-8 -*-
r"""twogate_shadow_engine.py — 两道门区域信号影子引擎 (PREREG_TWOGATE_ZONE_20260930 唯一实现 owner)
================================================================
职责: prereg 信号定义的唯一实现(E1箱底/E5强势回踩×主线门×regime门) + 校准回归 + 单票标签。
红线: 与 research 脚本(tmp/twoway_matrix_20260930.py)同式同数据路径; LOCKED 前必须过 calibrate()
  (复算 2024+ 窗口四主格+对照格, 须复现 reports/twoway_mainline_regime_20260930.md 证据值)。
用法:
  python twogate_shadow_engine.py --calibrate   # LOCKED 门禁: 校准回归(PASS 才允许 LOCKED)
  from twogate_shadow_engine import eval_prereg_signals, gate_label  # 扫描器/影子台账调用
"""
import os
import re
import glob
import json
import sqlite3
import hashlib
import collections
import numpy as np
import pandas as pd

from universe_zone_scanner import VIPDOC, read_vipdoc_tail, ALL_FORMULAS

FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
START = 20150101
WIN_FROM = 20240102
W = ALL_FORMULAS["box_window"]
REGIMES = ("冰点≤30", "修复31-60", "温暖61-100", "高潮>100")

_cache = {}


def regime(lu):
    if lu <= 30:
        return "冰点≤30"
    if lu <= 60:
        return "修复31-60"
    if lu <= 100:
        return "温暖61-100"
    return "高潮>100"


def load_sentiment():
    if "sent" not in _cache:
        f = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)
        sent = {}
        for d, lu in f.execute("SELECT trade_date, limit_up FROM feature_market_sentiment").fetchall():
            try:
                sent[int(re.sub(r"\D", "", str(d)))] = int(lu or 0)
            except Exception:
                continue
        f.close()
        _cache["sent"] = sent
    return _cache["sent"]


def load_mainline_sets():
    """主线代理(prereg 主口径声明项): industry_momentum≥0.8 的 (日→代码集)。日期格式混存归一。
    🔴2026-10-01 去填充修复(mainline_depadded_recheck): '其他'填充行 993万行(71%) momentum=1.2
    全部≥0.8 混入主线集 → 加 industry≠'其他' 过滤; 去填充复验=主线门存活
    (修复/温暖/高潮差 +0.95/+0.86/+2.06pp 方向不变), 主线内格几乎不动。"""
    if "ml" not in _cache:
        f = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)
        ml = collections.defaultdict(set)
        for d, tc in f.execute(
                "SELECT trade_date, ts_code FROM feature_industry_momentum "
                "WHERE industry_momentum >= 0.8 AND industry != '其他'").fetchall():
            try:
                d8 = int(re.sub(r"\D", "", str(d)))
            except Exception:
                continue
            ml[d8].add(str(tc).split(".")[0].lstrip("0").zfill(6))
        f.close()
        _cache["ml"] = dict(ml)
    return _cache["ml"]


def eval_prereg_signals(tail):
    """对单票尾部K线(≥130根, 升序 (date,o,h,l,c,vol)) 评估 prereg 信号(最后一根bar=信号日)。
    与 research 脚本逐式同构: runmax/runmin=shift(1).rolling(60); E1: 箱位≤25% ∧ c≥MA60×0.97;
    E5: MA5>MA10>MA20>MA60 ∧ close∈[MA20, MA10×1.02]。
    返回 dict(E1,E5,pos,buy,trig,inv) + V2a 判定三要素(rs市场相对/posb箱位/px信号日收盘)。"""
    if not tail or len(tail) < 130:
        return None
    d = np.array([x[0] for x in tail], dtype=np.int64)
    c = np.array([x[4] for x in tail])
    h = np.array([x[2] for x in tail])
    v = np.array([float(x[5]) for x in tail])
    s = pd.Series(c)
    ma5, ma10 = s.rolling(5).mean().values, s.rolling(10).mean().values
    ma20, ma60 = s.rolling(20).mean().values, s.rolling(60).mean().values
    hs = pd.Series(h)
    runmax = hs.shift(1).rolling(W).max().values[-1]
    runmin_l = pd.Series(np.array([x[3] for x in tail])).shift(1).rolling(W).min().values[-1]
    t = len(c) - 1
    px = c[t]
    if px <= 0 or np.isnan(ma60[t]) or np.isnan(runmax) or runmax <= runmin_l:
        return None
    pos = (px - runmin_l) / (runmax - runmin_l)
    e1 = bool(pos <= 0.25 and px >= ma60[t] * 0.97)
    e5 = bool((ma5[t] > ma10[t] > ma20[t] > ma60[t]) and (ma20[t] <= px <= ma10[t] * 1.02))
    # V2a 三要素: rs=信号日 ret20 - 当日全市场中位 (中位来自 pass1 同款全市场缓存)
    ret20 = np.nan
    if t >= 20 and c[t-20] > 0:
        ret20 = px / c[t-20] - 1
    med = _market_median(int(d[t]))
    rs = (ret20 - med) if (med is not None and not np.isnan(ret20)) else None
    return dict(E1=e1, E5=e5, pos=float(pos), date=int(d[t]), px=float(px),
                rs=rs, posb=float(pos), ret20=(None if np.isnan(ret20) else float(ret20)))


_MED_CACHE = {}


def _market_median(d8):
    """当日全市场 ret20 中位(按信号日, vipdoc 全宇宙单遍); 同日缓存。"""
    if d8 in _MED_CACHE:
        return _MED_CACHE[d8]
    vals = []
    for p in glob.glob(os.path.join(VIPDOC, "sh", "lday", "sh??????.day"))[:600] + \
             glob.glob(os.path.join(VIPDOC, "sz", "lday", "sz??????.day"))[:600]:
        try:
            dtn = np.dtype([("d", "<u4"), ("o", "<u4"), ("h", "<u4"), ("l", "<u4"), ("c", "<u4"),
                            ("amt", "<f4"), ("v", "<u4"), ("r", "<u4")])
            rec = np.fromfile(p, dtype=dtn)
        except Exception:
            continue
        rec = rec[rec["d"] >= 20150101]
        if len(rec) < 25:
            continue
        hit = rec[rec["d"] == d8]
        if len(hit) == 0:
            continue
        i = np.where(rec["d"] == d8)[0][0]
        if i < 20 or rec["c"][i-20] <= 0:
            continue
        vals.append(rec["c"][i] / rec["c"][i-20] - 1)
    med = float(np.median(vals)) if len(vals) >= 300 else None
    _MED_CACHE[d8] = med
    return med


def gate_label(code, signal_date, cat=None):
    """单票两道门标签: 返回 dict(mainline, regime, gate_pass, signals)。signal_date=int YYYYMMDD。"""
    sent = load_sentiment()
    lu = sent.get(signal_date)
    if lu is None:
        return None
    g = regime(lu)
    ml = code in load_mainline_sets().get(signal_date, set())
    tail = read_vipdoc_tail(code)
    sig = eval_prereg_signals(tail) if tail else None
    sigs = {"E1": sig["E1"], "E5": sig["E5"]} if sig else {"E1": False, "E5": False}
    gate = bool((sigs["E1"] or sigs["E5"]) and ml and g != "冰点≤30")
    return dict(mainline=ml, regime=g, gate_pass=gate, signals=sigs, limit_up=lu)


# ---- 校准回归(LOCKED 门禁) ----------------------------------------------------
# 期望值 = reports/twoway_mainline_regime_20260930.md 实测格 (n, mean%, win%)
EXPECTED = {
    ("E1箱底", "修复31-60", "主线内"): (22936, 0.63, 48), ("E1箱底", "修复31-60", "主线外"): (1405, -0.48, 42),
    ("E1箱底", "温暖61-100", "主线内"): (17791, 1.16, 51), ("E1箱底", "温暖61-100", "主线外"): (1808, -0.38, 43),
    ("E1箱底", "高潮>100", "主线内"): (5366, 0.90, 53), ("E1箱底", "高潮>100", "主线外"): (612, -2.19, 34),
    ("E1箱底", "冰点≤30", "主线内"): (3021, 1.83, 53),
    ("E5强势回踩", "修复31-60", "主线内"): (87656, 0.44, 49), ("E5强势回踩", "修复31-60", "主线外"): (8606, -1.20, 37),
    ("E5强势回踩", "温暖61-100", "主线内"): (86924, 0.80, 50), ("E5强势回踩", "温暖61-100", "主线外"): (6691, -1.31, 40),
    ("E5强势回踩", "高潮>100", "主线内"): (36070, 1.35, 53), ("E5强势回踩", "高潮>100", "主线外"): (2060, -3.51, 34),
    ("E5强势回踩", "冰点≤30", "主线内"): (10171, -0.14, 44),
}


def calibrate():
    """复算 2024+ 窗口全格并与证据值对账; n 须精确一致, mean/win 容差 0.06pp。任一格 FAIL → exit 2。"""
    sent = load_sentiment()
    ml = load_mainline_sets()
    cell = collections.defaultdict(list)
    files = glob.glob(os.path.join(VIPDOC, "sh", "lday", "sh??????.day")) + \
            glob.glob(os.path.join(VIPDOC, "sz", "lday", "sz??????.day"))
    files = [p for p in files if os.path.basename(p)[2:8][:2] in ("60", "00", "30")
             and not os.path.basename(p)[2:8].startswith("688")]
    for i, p in enumerate(files):
        code = os.path.basename(p)[2:8]
        try:
            import struct
            dt = np.dtype([("d", "<u4"), ("o", "<u4"), ("h", "<u4"), ("l", "<u4"), ("c", "<u4"),
                           ("amt", "<f4"), ("v", "<u4"), ("r", "<u4")])
            rec = np.fromfile(p, dtype=dt)
        except Exception:
            continue
        rec = rec[rec["d"] >= START]
        if len(rec) < 130:
            continue
        d = rec["d"].astype(np.int64)
        c = rec["c"] / 100.0
        h = rec["h"] / 100.0
        v = rec["v"].astype(np.float64)
        s = pd.Series(c)
        ma5, ma10 = s.rolling(5).mean().values, s.rolling(10).mean().values
        ma20, ma60 = s.rolling(20).mean().values, s.rolling(60).mean().values
        hs = pd.Series(h)
        runmax = hs.shift(1).rolling(W).max().values
        runmin_l = pd.Series(rec["l"] / 100.0).shift(1).rolling(W).min().values
        n = len(c)
        fwd10 = np.full(n, np.nan)
        fwd10[:-10] = c[10:] / c[:-10] - 1
        for t in range(W + 1, n - 10):
            lu = sent.get(int(d[t]))
            if lu is None:
                continue
            if d[t] < WIN_FROM or d[t] not in ml:
                continue
            g = regime(lu)
            px = c[t]
            if px <= 0 or np.isnan(ma60[t]) or np.isnan(runmax[t]) or runmax[t] <= runmin_l[t]:
                continue
            posb = (px - runmin_l[t]) / (runmax[t] - runmin_l[t])
            k = "主线内" if code in ml[d[t]] else "主线外"
            if posb <= 0.25 and px >= ma60[t] * 0.97:
                cell[("E1箱底", g, k)].append(fwd10[t])
            if (ma5[t] > ma10[t] > ma20[t] > ma60[t]) and (ma20[t] <= px <= ma10[t] * 1.02):
                cell[("E5强势回踩", g, k)].append(fwd10[t])
        if (i + 1) % 1500 == 0:
            print(f"[CAL] ...{i+1}/{len(files)}", flush=True)
    fails = []
    for key, (en, em, ew) in sorted(EXPECTED.items()):
        vals = cell.get(key, [])
        n = len(vals)
        if n != en:
            fails.append(f"{key}: n={n} 期望{en}")
            continue
        m = float(np.mean(vals)) * 100
        w = float((np.array(vals) > 0).mean()) * 100
        if abs(m - em) > 0.06 or abs(w - ew) > 0.6:
            fails.append(f"{key}: mean={m:.3f} win={w:.1f} 期望{em}/{ew}")
    if fails:
        for x in fails:
            print("[CAL-FAIL]", x)
        raise SystemExit(2)
    print(f"[CAL-PASS] {len(EXPECTED)} 格全部复现 (n 精确一致, mean/win 容差内) —— 引擎实现与研究脚本恒等, 允许 LOCKED")


# ---- 影子台账结算与中期读数 ----------------------------------------------------
LEDGER = r"D:\zcodesj\data\twogate_shadow_ledger.jsonl"
STATUS = r"D:\zcodesj\data\twogate_shadow_status.json"
COST_RT = 0.003   # prereg scope.cost 双边0.3%


def settle():
    """对台账中信号日+10个交易日后已有数据的行回填 fwd10(毛/净); 幂等可重跑。
    另输出中期读数 status json(主线内格 vs 主线外对照, 对账 prereg 门槛)。"""
    rows = []
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as fp:
            rows = [json.loads(x) for x in fp if x.strip()]
    changed = 0
    for r in rows:
        if r.get("fwd10_net") is not None:
            continue
        d8 = int(r["date"])
        tail = read_vipdoc_tail(str(r["code"]))
        if not tail:
            continue
        dates = [x[0] for x in tail]
        if d8 not in dates:
            continue
        i = dates.index(d8)
        if i + 10 >= len(tail):
            continue
        fwd = tail[i + 10][4] / tail[i][4] - 1
        r["fwd10_gross"] = round(fwd, 6)
        r["fwd10_net"] = round(fwd - COST_RT, 6)
        r["settled_date"] = int(tail[i + 10][0])
        changed += 1
    with open(LEDGER, "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    def stat(sub):
        vals = [r["fwd10_net"] for r in sub]
        if not vals:
            return dict(n=0)
        a = np.array(vals)
        return dict(n=len(a), net_mean=round(float(a.mean()), 5),
                    win=round(float((a > 0).mean()), 4))

    settled = [r for r in rows if r.get("fwd10_net") is not None]
    in_g = stat([r for r in settled if r["mainline"] == "是"])
    out_g = stat([r for r in settled if r["mainline"] == "否"])
    diff = (round(in_g["net_mean"] - out_g["net_mean"], 5)
            if in_g.get("n") and out_g.get("n") else None)
    gates = {
        "G1_主线内净均值>0": bool(in_g.get("n") and in_g["net_mean"] > 0),
        "G2_主线内胜率≥48%": bool(in_g.get("n") and in_g["win"] >= 0.48),
        "G3_主线外差≥0.8pp": bool(diff is not None and diff >= 0.008),
    }
    # V2a 对照读数(过门子集内: RS≤5% + px≤15 + E1'箱位≥10%)
    def v2a_ok(r):
        rs, posb, pxs, ise1 = r.get("rs"), r.get("posb"), r.get("px_sig"), r.get("is_e1")
        if rs is None or pxs is None:
            return None   # 旧行无三要素(回填前), 不计入 V2a 对照
        if rs > 0.05 or pxs > 15:
            return False
        if ise1 and (posb is not None and posb < 0.10):
            return False
        return True
    v2a_rows = [r for r in settled if v2a_ok(r) is True]
    v2a_na = sum(1 for r in settled if v2a_ok(r) is None)
    v2a_g = stat(v2a_rows)
    n_days = len({r["date"] for r in settled})
    status = dict(asof=max((int(r["date"]) for r in rows), default=0),
                  total_rows=len(rows), settled=len(settled),
                  pending=sum(1 for r in rows if r.get("fwd10_net") is None),
                  signal_days=n_days,
                  主线内=in_g, 主线外=out_g, 差值pp=diff, gates=gates,
                  V2a对照=v2a_g, V2a旧行无三要素=v2a_na,
                  sample_target="≥300中期/≥800终读",
                  note="聚簇相关: 同信号日事件截面相关, 事件数≠有效样本——读数需事件数达标且信号日数≥20(中期)/≥40(终读); 读数不足时仅监控不作裁决; FAIL判定以prereg预声明条件为准; V1(过门全集) vs V2a(过门∩RS≤5%∩≤15元∩E1'≥10%)对照随结算自动产出")
    with open(STATUS, "w", encoding="utf-8") as fp:
        json.dump(status, fp, ensure_ascii=False, indent=1)
    print(f"[SETTLE] 新结算 {changed} 行 | 已结算 {len(settled)} | 未到期 {len(rows)-len(settled)}")
    print(f"[READOUT] 主线内 {in_g} | 主线外 {out_g} | 差 {diff} | 门槛 {gates}")
    return status


if __name__ == "__main__":
    import sys
    if "--calibrate" in sys.argv:
        calibrate()
    elif "--settle" in sys.argv:
        settle()
    else:
        print(__doc__)
