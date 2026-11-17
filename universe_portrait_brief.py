#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""universe_portrait_brief.py — A股宇宙画像驱动实战简报 v1（侦察级·零交易权）
================================================================================
主人令 2026-10-01:「A股宇宙深度研究画像系统,提高胜率,寻找买卖点,买卖区域」(第三遍收敛交付)

定位: 九轮研究(情绪质量/fresh/全宇宙画像格/爆量毒药/急追证伪)的**每日实战收敛件**——
     从全宇宙画像快照筛出三池+逐票买卖点位, 供主人晨间研判。侦察级, 不进任何闸门。

选池规则(全部来自已验证结论, 无新发明):
  A池 买区候选 = 正期望画像格(趋势∈{震荡,空头} × pos250<40 × vr120<1.0 × 价≤10元)
               ∧ 未爆量 ∧ 回踩带条件(low≤MA10≤close 或 收盘距MA10带≤3%)
               —— 依据: 画像格统计「超跌×缩量×低价」+2.3~2.8%; 买卖点=回踩企稳(禁急追)
  B池 爆量禁区 = vr120≥1.5 ∧ ret20≥20% (任何趋势/价位) —— 依据: 全宇宙毒药格中位≈-8%
  C池 次级观察 = 多头排列 × pos250 60-80 × 缩量(vr<1.0) × 5-20元 —— 依据: +1.5~1.7% 格

每票标注: 现价(qfq) / MA10回踩带[lo,hi] / 确认窗(年低×1.05~1.10) / -8%止损锚 / 20日低点锚
输出: reports/universe_portrait_brief_<date>.md (名称经 stock_name_lookup 校验)

用法: python universe_portrait_brief.py            # 默认主库最新截面
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

ROOT = r"D:\zcodesj"
QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(zc副本停更10/09已废, SOP一百三十二)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_names():
    """名称接口=stock_name_lookup 唯一真源(宪法§8); 兜底空。"""
    try:
        sys.path.insert(0, ROOT)
        from stock_name_lookup import get_name
        return get_name
    except Exception:
        return lambda code: ""


def main():
    con = sqlite3.connect(QFQ_DB, uri=True)
    rows = con.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "WHERE (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date").fetchall()
    con.close()
    panel = {}
    for code, d, o, h, low, c, v in rows:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue
        p = panel.setdefault(code, ([], [], [], [], [], []))
        p[0].append(d); p[1].append(o); p[2].append(h); p[3].append(low); p[4].append(c); p[5].append(v)
    latest = max(v[0][-1] for v in panel.values())
    get_name = load_names()

    def ma(x, win):
        out = np.full(len(x), np.nan)
        if len(x) >= win:
            cs = np.cumsum(np.insert(np.asarray(x, float), 0, 0.0))
            out[win - 1:] = (cs[win:] - cs[:-win]) / win
        return out

    A, B, C = [], [], []
    n_all = 0
    gate = dict(n_valid=0, n_low=0, n_ret20pos=0, n_bias20=0)  # 市况门聚合(M2定谳双门)+乖离20%计数(R闸候选)
    for code, (dates, opens, highs, lows, closes, vols) in panel.items():
        t = len(dates) - 1
        if dates[t] != latest or t < 260:
            continue
        n_all += 1
        c = np.asarray(closes[: t + 1], float)
        v = np.asarray(vols[: t + 1], float)
        px = float(closes[t])
        if not px or px <= 0:
            continue
        ma5, ma10, ma20, ma60 = ma(c, 5)[-1], ma(c, 10)[-1], ma(c, 20)[-1], ma(c, 60)[-1]
        vma5, vma120 = ma(v, 5)[-1], ma(v, 120)[-1]
        if np.isnan(ma60) or np.isnan(vma120) or np.isnan(ma10):
            continue
        w = c[-250:]
        lo250, hi250 = float(w.min()), float(w.max())
        pos250 = (px - lo250) / (hi250 - lo250) * 100 if hi250 > lo250 else 50.0
        vr = float(vma5 / vma120) if vma120 else None
        ret20 = px / float(c[-21]) - 1.0 if len(c) >= 21 and c[-21] else None
        if ma5 > ma10 > ma20 > ma60:
            trend = "多头"
        elif ma5 < ma10 < ma20 < ma60:
            trend = "空头"
        else:
            trend = "震荡"
        ma20_up = ma(c, 20)[-1] > ma(c, 20)[-6]
        gate["n_valid"] += 1
        if pos250 < 40:
            gate["n_low"] += 1
        if ret20 is not None and ret20 > 0:
            gate["n_ret20pos"] += 1
        if ma20 and px > float(ma20) * 1.20:
            gate["n_bias20"] += 1
        name = get_name(code) or "?"
        # 买卖点位(与 bs_zone 同式)
        band_lo, band_hi = (float(ma20), float(ma10)) if (ma20_up and ma10 > ma20) else (None, None)
        z1_lo, z1_hi = lo250 * 1.05, lo250 * 1.10
        stop = max(float(min(c[-20:])), round(px * 0.92, 2))
        base = dict(code=code, name=name, px=round(px, 2), trend=trend,
                    pos250=round(pos250), vr=round(vr, 2) if vr else None,
                    band=(round(band_lo, 2), round(band_hi, 2)) if band_lo else None,
                    z1=(round(z1_lo, 2), round(z1_hi, 2)), stop=round(stop, 2),
                    ret20=round(ret20 * 100, 1) if ret20 is not None else None)
        blowoff = bool(vr and vr >= 1.5 and ret20 is not None and ret20 >= 0.20)
        if blowoff:
            B.append(base)
            continue
        # A池 v2(2026-10-02 全历史验证修正): 格 ∧ 回踩企稳 ∧ 趋势基础(pop) ∧ 缩量
        # 验证(tmp/portrait_rule_backtest_20261002.py): 无趋势基础的格∧回踩仅+0.89%;
        # +趋势基础修复至+2.71%且2026年+0.79%唯一熊市转正(低价带防御)。
        in_band = band_lo is not None and float(lows[t]) <= ma10 <= px
        near_band = band_lo is not None and 0 < (px - ma10) / ma10 <= 0.03
        # 趋势基础(与Z2s的pop同式): 低位(pos≤50)+价在MA20上+MA20>MA60+60日内放量大阳+价≤30
        w60 = c[-60:]
        chg_arr = w60[1:] / w60[:-1] - 1.0
        v60 = v[-60:]
        vma20_60 = ma(v, 20)[-60:]
        big60 = bool(np.any((v60[1:] >= 2.0 * vma20_60[1:]) & (chg_arr >= 0.03))) if len(v60) >= 60 else False
        pop = bool(pos250 <= 50 and px > float(ma20) and float(ma20) > float(ma60) and big60 and px <= 30)
        shrink = bool(vols[t] < float(vma5))
        if (trend in ("震荡", "空头") and pos250 < 40 and vr and vr < 1.0 and px <= 10
                and pop and (in_band or near_band) and shrink):
            base["state"] = ("回踩带内" if in_band else "贴带3%内")
            # 金叉标注(PREGEG_GRID_GOLDENX_OBSERVE 影子判读用, 不改变选池规则)
            ma5_arr, ma10_arr = ma(c, 5), ma(c, 10)
            base["golden"] = bool(t >= 1 and not np.isnan(ma5_arr[t - 1]) and not np.isnan(ma10_arr[t - 1])
                                  and ma5_arr[t - 1] <= ma10_arr[t - 1] and ma5_arr[t] > ma10_arr[t])
            A.append(base)
        elif (trend == "多头" and 60 <= pos250 < 80 and vr and vr < 1.0 and 5 < px <= 20):
            base["state"] = "缩量整理"
            C.append(base)

    A.sort(key=lambda r: (r["state"] != "回踩带内", r["pos250"]))
    B.sort(key=lambda r: -(r["vr"] or 0))
    C.sort(key=lambda r: -r["pos250"])
    A, C = A[:40], C[:20]

    def tbl(rs):
        if not rs:
            return "(空)"
        out = ["| 代码 | 名称 | 现价(qfq) | 趋势 | pos250 | vr120 | 状态 | 回踩带[lo~hi] | 确认窗 | 止损锚 |",
               "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rs:
            band = f"{r['band'][0]}~{r['band'][1]}" if r["band"] else "N/A"
            out.append(f"| {r['code']} | {r['name']} | {r['px']} | {r['trend']} | {r['pos250']} | "
                       f"{r['vr']} | {r.get('state','')} | {band} | {r['z1'][0]}~{r['z1'][1]} | {r['stop']} |")
        return "\n".join(out)

    d8 = latest
    low_pct = round(gate["n_low"] / gate["n_valid"] * 100, 1) if gate["n_valid"] else 0.0
    mom_pct = round(gate["n_ret20pos"] / gate["n_valid"] * 100, 1) if gate["n_valid"] else 0.0
    gateA = "开" if low_pct >= 70 else "关"
    gateC = "开" if mom_pct > 50 else "关"
    # 形态检索面板(TS2Vec日更, PREREG_PORTRAIT_TS2VEC_DAILY_V1): 纯描述零预测; 缺失/不可用/过期=N/A禁伪健康
    retr_path = os.path.join(ROOT, "data", "ts2vec_retrieval_latest.json")
    retr = None
    try:
        with open(retr_path, "r", encoding="utf-8") as f:
            retr = json.load(f)
        if not isinstance(retr, dict):
            retr = None
    except FileNotFoundError:
        retr = None
    retr_ok = bool(retr) and bool(retr.get("availability")) and int(retr.get("asof") or 0) == int(d8)
    if retr_ok:
        s1 = retr["targets"]["T1_sh"]["stats"]
        s2 = retr["targets"]["T2_600519"]["stats"]
        nwin = retr["targets"]["T1_sh"].get("n_lib_windows", 0)
        retr_panel = f"""## 形态检索面板（TS2Vec形态相似度日更·纯描述）

全市场 30 日窗口库（{nwin} 窗）按"长得像"检索 Top-150，其后 20 个交易日走势分布：

| 目标 | 有效样本 | 后续20日均值 | 中位 | 上涨占比 | 大涨≥10% | 大跌≤-10% |
|---|---|---|---|---|---|---|
| 长得像当前上证 | {s1['n_valid']} | {s1['mean_pct']}% | {s1['median_pct']}% | {s1['share_gt0_pct']}% | {s1['share_ge10_pct']}% | {s1['share_le_n10_pct']}% |
| 长得像当前茅台600519 | {s2['n_valid']} | {s2['mean_pct']}% | {s2['median_pct']}% | {s2['share_gt0_pct']}% | {s2['share_ge10_pct']}% | {s2['share_le_n10_pct']}% |

> 描述"历史上像现在这样走的片段之后通常怎样"，零预测非信号；禁作选股公式，任何信号化须新预注册并对照证伪#151。
"""
        retr_json = dict(availability=True, asof=int(retr["asof"]), T1_sh=s1, T2_600519=s2)
    else:
        if not retr:
            reason = "no_data"
        elif not retr.get("availability"):
            reason = "unavailable:%s" % retr.get("reason")
        else:
            reason = "stale:asof=%s!=brief=%s" % (retr.get("asof"), d8)
        retr_panel = f"""## 形态检索面板（TS2Vec形态相似度日更·纯描述）

N/A（结构化不可用: {reason}）——历史形状相似片段的后续分布今日未产出，不编造。
"""
        retr_json = dict(availability=False, reason=reason)
    md = f"""# A股宇宙画像驱动实战简报（侦察级·零交易权）{d8}

> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 引擎 universe_portrait_brief.py | 截面 {d8}（{n_all} 只全宇宙画像）
> 选池规则全部来自 2026-10-01 九轮验证结论（画像格统计/爆量毒药/急追证伪/回踩企稳），零新发明。
> 🔴铁律: A池是「侦察观察组非名单」; 买入仍须 trade_executor+R闸全跑+主人确认; 禁急追(距启动≤5日未回踩=负期望); 爆量票(B池)任何画像下禁追。

## 市况双门（{d8}，M2 定谳：门A 开=A 池历史 +20.7%/胜率 80.6%；门C 开=显著反向 t=-3.67）

| 门 | 定义 | 当日读数 | 状态 |
|---|---|---|---|
| 门A 深度冰点 | 低区(pos<40)占比≥70% | {low_pct}% | **{gateA}** |
| 门C 市场动量 | 20日上涨票占比>50% | {mom_pct}% | **{gateC}** |
| 乖离≥20%禁买观察 | 乖离MA20≥20%票数（R闸候选 PREREG_BIAS20_BAN，中位-9%毒药档） | {gate['n_bias20']} 只 | 禁买观察区 |

最优窗口=门A开∧门C关（历史 4 年仅 123 天）；门C 开时 A 池减效（逆向策略属性）。

## A池 v2 · 买区候选（画像格 ∧ 回踩企稳 ∧ 趋势基础 ∧ 缩量; 全历史验证 +2.71%/2026+0.79%唯一熊市转正; 无命中=当日无票满足, 诚实输出）

{tbl(A)}

> 依据: 「震荡/空头×深底/低位×缩量×≤10元」画像格 t20e +1.8~2.8%（2024/25 贡献, 2026 年内该格 -4~-5%——regime 门未开时仅观察）; 回踩带内=Z2s 同源条件（回踩 MA10 企稳, 禁突破日追买）。

## B池 · 爆量禁区（全宇宙毒药格, 追入中位≈-8%）

{tbl(B)}

> 任何画像下爆量追入皆负期望（167 格 BOTTOM 无例外）; 持仓内爆量票同步见 holdings_risk_scan E 层减仓提示。

## C池 · 次级观察（多头×高位缩量整理, 前20）

{tbl(C)}

{retr_panel}

## 使用说明（人话）

- A 池=底部区缩量低价、且已经回到 10 日线附近企稳的票——这是全部研究收敛后的最优买区画像; 但 2026 年内这个画像还在亏, **regime 门（上证站回 MA20 且修复档以上）没开之前只看不买**。
- B 池=爆量票, 碰都别碰; 已持有的看 E 层提示考虑减仓。
- C 池=强势票缩量整理, 次级机会。
"""
    out_md = os.path.join(ROOT, "reports", f"universe_portrait_brief_{d8}.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md)
    # 机读产物: 供 portrait_gate_shadow.py 消费(门状态+三池命中明细)
    out_j = os.path.join(ROOT, "data", f"universe_portrait_brief_{d8}.json")
    with open(out_j, "w", encoding="utf-8") as f:
        json.dump(dict(asof=d8, n_valid=gate["n_valid"], low_pct=low_pct, mom_pct=mom_pct,
                       gateA=gateA, gateC=gateC, recon_only=True,
                       pool_A=[dict(code=r["code"], name=r["name"], px=r["px"], state=r.get("state"),
                                    golden=r.get("golden")) for r in A],
                       pool_B=[dict(code=r["code"], name=r["name"], px=r["px"], vr=r["vr"]) for r in B],
                       retr=retr_json),
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"[PORTRAIT-BRIEF] asof={d8} A={len(A)} B={len(B)} C={len(C)} 门A={gateA}({low_pct}%) 门C={gateC}({mom_pct}%) 检索面板={'OK' if retr_ok else 'N/A'} -> {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
