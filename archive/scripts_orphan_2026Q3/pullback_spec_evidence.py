#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pullback_spec_evidence.py — 回踩确认入场规格 v1.0 参数证据回放（探索性）

目的：主人要求「不能随便改，要有依据」——对规格四个焦点参数做本地全史网格对照，
用数字回答每个档位对【事件确认数 / B-A 配对胜率差 / 配对收益差】的影响，
据此把默认值从"惯例"改为"有证据支撑的值"。

设计（与 reports/PULLBACK_ENTRY_SPEC_20260826.md 一致）：
  D0 突破日：E1 前20日横盘(振幅≤25%,累计涨幅≤15%) + E2 放量长阳(k_v×5日均量, 涨幅≥c_min%)
             + E3 收盘严格破前20日高点 + 非一字板 + ≤20元
  确认日 t∈[d+2,d+12]：P1 回踩深度[-12%,-u] P2 缩量(vol[t]≤s×vol[d], 中途最大≤0.85×)
                       P3 支撑锚不破(收盘严格, 盘中容忍bf) P4 企稳收阳 P5 结构完整
  三臂：A=突破次日开盘买入 / B=确认次日开盘买入 / C=d+3固定延迟开盘买入（open-to-open）

口径铁律：qfq 单一价格源；next-bar 开盘成交；探索性数据已全部看过 → 正式上线仍须预注册+
wfa_gate 新协议。成本为零成本毛收益（配对差分下成本近似相消，正式验证须带成本）。

输出：reports/PULLBACK_EVIDENCE_20260826.md
用法：python pullback_spec_evidence.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"

PREFIX_OK = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605")
SKIP_BARS = 60          # 次新<60交易日近似剔除
MIN_LEN = 100
PB_MAX = -0.12          # P1 回撤硬上限（超过即弃案）
MID_VOL_CAP = 0.85      # P2 回踩期中途放量出货线
HORIZONS = (1, 5, 10, 20)

KV = (1.5, 2.0, 2.5)        # E2 放量倍数
CM = (3, 5)                 # E2 长阳最小涨幅 %
SS = (0.5, 0.7, 0.85)       # P2 缩量阈值
UU = (0.02, 0.03, 0.05)     # P1 回踩触发深度
BB = (0.0, 0.02)            # P3 盘中插针容忍
AN = ("A1", "A2")           # 支撑锚：A1=max(d开盘,前高)；A2=前高

CONFIGS = [(kv, cm, s, u, bf, an) for kv in KV for cm in CM for s in SS for u in UU
           for bf in BB for an in AN]
CIDX = {c: i for i, c in enumerate(CONFIGS)}
NCFG = len(CONFIGS)

BASE_CFG = (2.0, 5, 0.7, 0.03, 0.02, "A1")


class Acc:
    """单配置聚合器：臂级收益统计 + B-A/B-C 配对差（存 fwd10 样本供 Wilcoxon）。"""
    __slots__ = ("ev", "conf", "arm_n", "arm_sum", "arm_win", "pair_n", "pair_n_bc",
                 "pair_win", "samp_ba10", "samp_bc10", "jhist", "n_unfill")

    def __init__(self):
        self.ev = 0
        self.conf = 0
        self.arm_n = {a: Counter() for a in "ABC"}
        self.arm_sum = {a: Counter() for a in "ABC"}
        self.arm_win = {a: Counter() for a in "ABC"}
        self.pair_n = {h: [0, 0, 0.0, 0] for h in HORIZONS}   # nBA,sumBA,winBA; nBC,sumBC,winBC
        self.pair_n_bc = {h: [0, 0.0, 0] for h in HORIZONS}
        self.samp_ba10: list[float] = []
        self.samp_bc10: list[float] = []
        self.jhist = Counter()
        self.n_unfill = 0

    def add_arm(self, arm, h, ret):
        if ret is None:
            return
        self.arm_n[arm][h] += 1
        self.arm_sum[arm][h] += ret
        if ret > 0:
            self.arm_win[arm][h] += 1

    def add_pair(self, h, ba, bc):
        p = self.pair_n[h]
        if ba is not None and bc is not None:
            p[0] += 1; p[1] += ba; p[2] += 1 if ba > 0 else 0
            self.pair_n_bc[h][0] += 1; self.pair_n_bc[h][1] += bc
            self.pair_n_bc[h][2] += 1 if bc > 0 else 0


ACC = {CIDX[c]: Acc() for c in CONFIGS}
EV_ACC_KVCM = Counter()   # (kv,cm)->事件数
CONF_JHIST_BASE = Counter()


def limit_up_gap_floor(prefix6: str) -> float:
    """开盘涨幅≥该值视为一字/涨停不可成交（近似闸）。"""
    return 0.195 if prefix6.startswith("30") else 0.095


def process_code(con: sqlite3.Connection, code: str):
    df = pd.read_sql_query(
        "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date", con, params=[code])
    if len(df) < MIN_LEN:
        return
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    v = np.nan_to_num(df["volume"].to_numpy(float), nan=0.0)
    ok = ~(np.isnan(o) | np.isnan(h) | np.isnan(l) | np.isnan(c))
    idx_ok = np.nonzero(ok)[0]
    if len(idx_ok) < 40:
        return
    start = max(SKIP_BARS, int(np.searchsorted(idx_ok, SKIP_BARS))) \
        if idx_ok[0] <= SKIP_BARS else SKIP_BARS
    if len(c) - start < 40:
        return
    cs = pd.Series(c)
    hs = pd.Series(h); ls = pd.Series(l); vs = pd.Series(v)
    bh = hs.rolling(20, min_periods=20).max().shift(1).to_numpy()
    bl = ls.rolling(20, min_periods=20).min().shift(1).to_numpy()
    cumg = (cs.shift(1) / cs.shift(21) - 1).to_numpy()
    vm5 = vs.rolling(5, min_periods=5).mean().shift(1).to_numpy()
    ma20 = cs.rolling(20).mean().to_numpy()
    pct = ((c / np.roll(c, 1)) - 1) * 100.0
    pct[0] = 0.0

    rng20 = np.full_like(bh, 99.0)
    np.divide(bh - bl, bl, out=rng20, where=bl > 0)
    base = (~np.isnan(bh)) & (~np.isnan(bl)) & (bl > 0) & (~np.isnan(vm5)) & (vm5 > 0) \
        & (~np.isnan(ma20)) & ~np.isnan(cumg)
    base &= (l < h) & (c > bh) & (rng20 <= 0.25) & (cumg <= 0.15) & (c <= 20.0)
    base[:start + 22] = False
    n = len(c)
    gap_lim = limit_up_gap_floor(code)

    masks = {}
    for kv in KV:
        for cm in CM:
            m = base & (v >= kv * vm5) & (pct >= cm)
            masks[(kv, cm)] = m

    def arm_ret(e: int, hh: int):
        if e < 0 or e >= n or e + hh >= n or not (o[e] > 0) or not (o[e + hh] > 0):
            return None
        g = o[e] / c[e - 1] - 1.0
        if g >= gap_lim:            # 开盘即涨停，实际不可买入
            return "UNFILL"
        return o[e + hh] / o[e] - 1.0

    for (kv, cm), m in masks.items():
        eid = np.nonzero(m)[0]
        ev_ids = list(map(int, eid))
        for i in ev_ids:
            EV_ACC_KVCM[(kv, cm)] += 1
            ca = c[i]; vi = v[i]; bhi = bh[i]; oai = o[i]
            endj = min(i + 12, n - 1)
            lo = i + 2
            if lo > endj:
                continue
            span = np.arange(i + 1, endj + 1)
            pml = np.minimum.accumulate(l[i + 1:endj + 1])
            pmv = np.maximum.accumulate(v[i + 1:endj + 1])
            pmc = np.minimum.accumulate(c[i + 1:endj + 1])
            cprev = c[span - 1]; ccur = c[span]; ocur = o[span]
            resolved = np.zeros(NCFG, dtype=bool)
            resolved_total = 0
            for jj in range(len(span)):
                j = int(span[jj]); k = jj - 1  # span 从 i+1 起，k 对应 t 偏移
                if k < 1:
                    # j=i+1 不在确认窗内，只推进前缀已在 accumulate 中覆盖
                    pass
                r = pml[jj] / ca - 1.0
                if r < PB_MAX:
                    break                       # 深度破限，永久弃案
                if k < 1:
                    continue
                stab = (ccur[jj] >= cprev[jj]) & (ccur[jj] >= ocur[jj]) & (ccur[jj] > ma20[j]) \
                    & (ccur[jj] > bhi * 0.98)
                if not stab:
                    continue
                sj = None  # noqa: F841 (占位清理)
                vol_t = v[j]
                for cfg_id in range(NCFG):
                    if resolved[cfg_id]:
                        continue
                    kvm, cmm, sm, um, bfm, anm = CONFIGS[cfg_id]
                    if kvm != kv or cmm != cm:
                        continue                # 该配置不属于本事件的 mask 组
                    if r > -um:
                        continue
                    anchor_val = max(oai, bhi) if anm == "A1" else bhi
                    if pmc[jj] <= anchor_val:
                        continue
                    if pml[jj] < anchor_val * (1.0 - bfm):
                        continue
                    if vol_t > sm * vi or pmv[jj] > MID_VOL_CAP * vi:
                        continue
                    resolved[cfg_id] = True
                    resolved_total += 1
                    a = ACC[cfg_id]
                    a.conf += 1
                    a.jhist[j - i] += 1
                    if cfg_id == CIDX[BASE_CFG]:
                        CONF_JHIST_BASE[j - i] += 1
                    ra, rb, rc = {}, {}, {}
                    unfilled_pair = False
                    ea = i + 1
                    eb = j + 1
                    ec = i + 3
                    for hh in HORIZONS:
                        va = arm_ret(ea, hh); vb = arm_ret(eb, hh); vc = arm_ret(ec, hh)
                        if va == "UNFILL" or vb == "UNFILL" or vc == "UNFILL":
                            unfilled_pair = True
                            va = vb = vc = None
                        ra[hh], rb[hh], rc[hh] = va, vb, vc
                    a.n_unfill += 1 if unfilled_pair else 0
                    if unfilled_pair:
                        continue                # 任一臂涨停不可买 → 弃该事件全臂样本
                    for hh in HORIZONS:
                        a.add_arm("A", hh, ra[hh]); a.add_arm("B", hh, rb[hh])
                        a.add_arm("C", hh, rc[hh])
                        ba = rb[hh] - ra[hh] if (rb[hh] is not None and ra[hh] is not None) else None
                        bc = rb[hh] - rc[hh] if (rb[hh] is not None and rc[hh] is not None) else None
                        p = a.pair_n[hh]
                        if ba is not None:
                            p[0] += 1; p[1] += ba; p[2] += 1 if ba > 0 else 0
                        q = a.pair_n_bc[hh]
                        if bc is not None:
                            q[0] += 1; q[1] += bc; q[2] += 1 if bc > 0 else 0
                        if hh == 10:
                            if ba is not None:
                                a.samp_ba10.append(ba)
                            if bc is not None:
                                a.samp_bc10.append(bc)
                if resolved_total == NCFG // (len(KV) * len(CM)):
                    break                       # 本 mask 组全部子配置已解析


def fmt_bps(x: float | None) -> str:
    return f"{x*10000:+.0f}bp" if x is not None else "-"


def table_slice(kv: float, cm: int, vary: str, vals: tuple,
                fixed_s=0.7, fixed_u=0.03, fixed_bf=0.02, fixed_an="A1") -> list[str]:
    lines = []
    lines.append("| 变量取值 | 事件数 | 确认数 | 确认率 | B-A配对n | fwd10配对均值 | fwd10配对中位 | 胜率(B-A>0) | B-C配对均值 | fwd10中位(B-C) | 胜率(B-C>0) | B臂fwd10胜率 | A臂fwd10胜率 | C臂fwd10胜率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for val in vals:
        if vary == "kv":
            cfg = (val, cm, fixed_s, fixed_u, fixed_bf, fixed_an)
        elif vary == "cm":
            cfg = (kv, val, fixed_s, fixed_u, fixed_bf, fixed_an)
        elif vary == "s":
            cfg = (kv, cm, val, fixed_u, fixed_bf, fixed_an)
        elif vary == "u":
            cfg = (kv, cm, fixed_s, val, fixed_bf, fixed_an)
        elif vary == "bf":
            cfg = (kv, cm, fixed_s, fixed_u, val, fixed_an)
        else:
            cfg = (kv, cm, fixed_s, fixed_u, fixed_bf, val)
        aid = CIDX[cfg]
        a = ACC[aid]
        conf_rate = a.conf / a.ev if a.ev else 0.0
        p = a.pair_n[10]
        q = a.pair_n_bc[10]
        nb = a.arm_n["B"][10]; na = a.arm_n["A"][10]; nc = a.arm_n["C"][10]
        wb = a.arm_win["B"][10] / nb if nb else 0
        wa = a.arm_win["A"][10] / na if na else 0
        wc = a.arm_win["C"][10] / nc if nc else 0
        pair_mean = p[1] / p[0] if p[0] else None
        pair_bc = q[1] / q[0] if q[0] else None
        med_ba = float(np.median(a.samp_ba10)) if a.samp_ba10 else None
        med_bc = float(np.median(a.samp_bc10)) if a.samp_bc10 else None
        wr_ba = f"{(p[2] / p[0]) * 100:.1f}%" if p[0] else "-"
        wr_bc = f"{(q[2] / q[0]) * 100:.1f}%" if q[0] else "-"
        lines.append(
            f"| {vary}={val} | {a.ev} | {a.conf} | {conf_rate:.1%} | {p[0]} "
            f"| {fmt_bps(pair_mean)} | {fmt_bps(med_ba)} | {wr_ba} "
            f"| {fmt_bps(pair_bc)} | {fmt_bps(med_bc)} | {wr_bc} "
            f"| {wb:.1%} | {wa:.1%} | {wc:.1%} |")
    return lines


def main():
    from scipy.stats import wilcoxon
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    name_con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    names = {}
    st_set = set()
    for code, name in name_con.execute("SELECT code,name FROM industry_map_ths"):
        c6 = str(code).zfill(6)
        nm = str(name or "").strip().upper()
        names[c6] = nm
        if "ST" in nm:
            st_set.add(c6)

    codes_all = [r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq")]
    codes = [x for x in codes_all if x.zfill(6)[:3] in PREFIX_OK]
    skipped_st = sum(1 for x in codes if x.zfill(6) in st_set)
    processed = 0
    print(f"[universe] 全部{len(codes_all)} | 板块过滤后{len(codes)} | 其中ST剔除{skipped_st}")
    batch = 250
    for bi in range(0, len(codes), batch):
        chunk = codes[bi:bi + batch]
        for cd in chunk:
            c6 = cd.zfill(6)
            if c6 in st_set:
                continue
            try:
                process_code(con, c6)
                processed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {c6} 处理失败: {exc}")
        print(f"[progress] {bi + len(chunk)}/{len(codes)} codes", flush=True)
    con.close(); name_con.close()

    # 把每配置的 ev 补齐：事件数按 (kv,cm) 扇出到子配置
    for cfg, cid in CIDX.items():
        kv, cm, *_rest = cfg
        ACC[cid].ev = EV_ACC_KVCM.get((kv, cm), 0)

    out: list[str] = []
    out.append("# 回踩确认规格 · 参数证据回放（探索性）\n")
    out.append("> 运行日期：2026-08-26 | 数据：qfq 全史 2016~20260826 | 口径：next-bar open-to-open，零成本毛收益\n")
    out.append("> 性质：**探索性**（本表所有格均已看过 → 正式上线证据必须另走 --prereg + wfa_gate 新协议，见边界规则 B7/B4）。\n")
    out.append(f"> universe：主板+创业板、剔 ST({skipped_st})/科创/北交/次新60日近似；任一臂 T+1 开盘触涨停不可成交即整事件弃案（计 {sum(a.n_unfill for a in ACC.values())} 次）。\n")

    kv_, cm_, s_, u_, bf_, an_ = BASE_CFG
    a0 = ACC[CIDX[BASE_CFG]]
    base_items = [(j, cnt) for j, cnt in CONF_JHIST_BASE.items() if j > 0]
    avg_wait = (sum(j * n for j, n in base_items) / sum(n for _j, n in base_items)) \
        if base_items else 0.0
    out.append("\n## 0. 基线（当前规格默认值）全景\n")
    tot_conf = sum(ACC[CIDX[(k, c2, s_, u_, bf_, an_)]].conf for k in KV for c2 in CM)
    out.append(f"- 全参数区事件总数(k_v,c_m 各组合合计): {sum(EV_ACC_KVCM.values())}\n")
    out.append(f"- 基线配置 (k_v={kv_}, c_min={cm_}, s={s_}, u={u_}, buf={bf_}, 锚{an_}): "
               f"事件 {a0.ev}, 确认 {a0.conf} ({a0.conf/max(a0.ev,1):.1%}), 平均等待 "
               f"{avg_wait:.1f} 个交易日后企稳\n")
    for h in HORIZONS:
        p = a0.pair_n[h]; q = a0.pair_n_bc[h]
        out.append(f"- fwd{h}: B-A 配对 n={p[0]} 均值 {fmt_bps(p[1]/p[0] if p[0] else None)} "
                   f"胜率 {(p[2]/p[0]*100 if p[0] else 0):.1f}% ；B-C 配对 n={q[0]} "
                   f"均值 {fmt_bps(q[1]/q[0] if q[0] else None)} 胜率 {(q[2]/q[0]*100 if q[0] else 0):.1f}%\n")
    try:
        samp = np.array(a0.samp_ba10)
        if len(samp) >= 30:
            stat, pv = wilcoxon(samp)
            boot = [float(np.mean(np.random.default_rng(s).choice(samp, len(samp))))
                    for s in range(2000)]
            out.append(f"- fwd10 B-A 差分 Wilcoxon p={pv:.2e}, bootstrap95CI "
                       f"[{np.percentile(boot,2.5)*1e4:.0f}bp, {np.percentile(boot,97.5)*1e4:.0f}bp]\n")
    except Exception as exc:  # noqa: BLE001
        out.append(f"- (Wilcoxon 未算出: {exc})\n")

    dims = [("kv", KV, kv_), ("cm", CM, cm_), ("s", SS, s_), ("u", UU, u_),
            ("an", AN, an_), ("bf", BB, bf_)]
    titles = {"kv": "E2 放量倍数 k_v", "cm": "E2 长阳阈值 c_min%", "s": "P2 缩量阈值",
              "u": "P1 回踩触发深度 u", "an": "P3 支撑锚", "bf": "P3 盘中插针容忍"}
    out.append("\n## 1~6. 单变量切片（其余保持基线）\n")
    for vary, vals, cur in dims:
        out.append(f"\n### {titles[vary]}\n")
        out.extend(table_slice(kv_, cm_, vary, vals))

    path = r"D:\zcodesj\reports\PULLBACK_EVIDENCE_20260826.md"
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    print(f"[done] 报告写入 {path}")

    if "--final-cfg" in sys.argv:
        parts = sys.argv[sys.argv.index("--final-cfg") + 1].split(",")
        cfg = (float(parts[0]), int(parts[1]), float(parts[2]), float(parts[3]),
               float(parts[4]), parts[5].upper())
        a = ACC[CIDX[cfg]]
        print(f"\n[final-cfg] {cfg}")
        for h in HORIZONS:
            p = a.pair_n[h]; q = a.pair_n_bc[h]
            nb, na, nc = a.arm_n["B"][h], a.arm_n["A"][h], a.arm_n["C"][h]
            wb = a.arm_win["B"][h] / nb if nb else 0
            wa = a.arm_win["A"][h] / na if na else 0
            wc = a.arm_win["C"][h] / nc if nc else 0
            mba = p[1] / p[0] if p[0] else None
            mbc = q[1] / q[0] if q[0] else None
            mab = a.arm_sum["B"][h] / nb if nb else None
            mac = a.arm_sum["A"][h] / na if na else None
            mad = a.arm_sum["C"][h] / nc if nc else None
            dmed = f"{np.median(a.samp_ba10)*1e4:+.0f}bp" if h == 10 and a.samp_ba10 else ""
            print(f"fwd{h:>2}: nBA={p[0]:<5} BA均值={fmt_bps(mba):>8} {dmed} "
                  f"胜率BA={(p[2]/p[0]*100 if p[0] else 0):.1f}% | "
                  f"BC均值={fmt_bps(mbc)} 胜率BC={(q[2]/q[0]*100 if q[0] else 0):.1f}% | "
                  f"B/AB/AC臂胜率={wb:.1%}/{wa:.1%}/{wc:.1%} | "
                  f"绝对均值B/A/C={fmt_bps(mab)}/{fmt_bps(mac)}/{fmt_bps(mad)}")
        if len(a.samp_ba10) >= 30:
            samp = np.array(a.samp_ba10)
            _st, pv = wilcoxon(samp)
            rng_boot = np.random.default_rng(7)
            boot = np.array([np.mean(rng_boot.choice(samp, len(samp))) for _ in range(2000)])
            print(f"B-A fwd10 Wilcoxon p={pv:.2e}; bootstrap95CI="
                  f"[{np.percentile(boot,2.5)*1e4:.0f}bp,{np.percentile(boot,97.5)*1e4:.0f}bp]; "
                  f"样本n={len(samp)}; 年度分布待Phase2分层")
    for line in out:
        if line.startswith(("> ", "#")) or "|" in line:
            print(line.rstrip())


if __name__ == "__main__":
    main()
