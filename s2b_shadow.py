#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2B 主线内低位补涨 影子期登记器（2026-09-08 prereg 配套, 零信号权零拦截权）。

prereg: reports/PREREG_S2B_LOWPOS_20260908.md (指纹 c2b3520db9eff4c9, 跑前钉死)
H1: T-1 申万L1行业涨停>=5家(聚集行业)内, 20日动量<=行业中位(低位组)的股票 T+10 超额>0。

数据源(全自动, 修复手工范式库依赖错配):
  涨停=qfq 收盘直算(创业板30开头>=19.5%, 主板>=9.8%; 剔科创688/北交8/4/92);
  行业=dragon_quant_meta.stock_sector_sw1; 动量=qfq T close / T-20交易日 close - 1。
落盘: reports/s2b_lowpos_shadow/s2b_shadow_{T}.json (周截面; schtasks 周五 23:40)。
影子语义: 仅登记观测, 不进白名单/不进 pre_trade/不产生任何买卖建议(prereg §6)。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SW1_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
OUT_DIR = r"D:\zcodesj\reports\s2b_lowpos_shadow"
CLUSTER_MIN = 5          # prereg: 行业涨停>=5家
LOOKBACK_BARS = 21       # T close 与 20 交易日前 close


def main(target_date=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    dates = [r[0] for r in q.execute(
        "select distinct date from stock_daily_meta_qfq order by date desc limit ?", (LOOKBACK_BARS + 2,))]
    dates.sort()
    if target_date:
        T = str(target_date)
        if T not in dates:
            print(f"[FAIL] {T} 不在 qfq 交易日序列(最新{dates[-1]})")
            return 2
    else:
        T = dates[-1]
    iT = dates.index(T)
    if iT < LOOKBACK_BARS - 1:
        print(f"[FAIL] 窗口不足: 需{LOOKBACK_BARS}个交易日, 实得{iT + 1}")
        return 2
    d0 = dates[iT - (LOOKBACK_BARS - 1)]  # 20交易日前

    # 收盘序列(qfq code 带 sz/sh 前缀 → 统一剥离为 6 位)
    # 三日期: T=截面日, T-1=涨停判定前收, T-20=20日动量基期
    d_prev = dates[iT - 1]
    cT, cP, c0 = {}, {}, {}
    for code, close, d in q.execute("select code,close,date from stock_daily_meta_qfq where date in (?,?,?)",
                                    (T, d_prev, d0)):
        code6 = code[-6:] if isinstance(code, str) else ""
        if not code6.isdigit() or code6.startswith(("688", "8", "4", "92")):
            continue
        if d == T:
            cT[code6] = close
        elif d == d_prev:
            cP[code6] = close
        else:
            c0[code6] = close
    q.close()

    # 行业映射
    m = sqlite3.connect(f"file:{SW1_DB}?mode=ro", uri=True)
    sector = {r[0][-6:]: r[1] for r in m.execute("select code,sector_name from stock_sector_sw1")}
    m.close()

    # 涨停判定(qfq close直算, 前收=T-1)
    limit_up = []
    for code, close in cT.items():
        base = cP.get(code)
        if not close or not base:
            continue
        chg = close / base - 1
        thr = 0.195 if code.startswith("30") else 0.098
        if chg >= thr:
            limit_up.append((code, sector.get(code, "未映射"), round(chg * 100, 2)))

    # 行业聚集
    from collections import Counter, defaultdict
    cnt = Counter(s for _, s, _ in limit_up if s != "未映射")
    clusters = {s: n for s, n in cnt.items() if n >= CLUSTER_MIN}

    # 聚集行业内 20日动量 → 低位组(<=行业中位)
    mom_by_sector = defaultdict(dict)
    for code, close in cT.items():
        base = c0.get(code)
        s = sector.get(code, "")
        if not base or s not in clusters:
            continue
        mom_by_sector[s][code] = close / base - 1
    low_pos = {}
    for s, moms in mom_by_sector.items():
        vals = sorted(moms.values())
        med = vals[len(vals) // 2]
        lows = {c: round(v * 100, 2) for c, v in sorted(moms.items(), key=lambda x: x[1]) if v <= med}
        low_pos[s] = {"median_mom": round(med * 100, 2), "n_sector": len(moms),
                      "n_low": len(lows), "low_pos": lows}

    out = {
        "date": T, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": "PREREG_S2B_LOWPOS_20260908 (c2b3520db9eff4c9)",
        "limit_up_total": len(limit_up), "limit_up_detail": limit_up,
        "clusters": clusters, "low_pos_by_sector": low_pos,
        "shadow_note": "影子期零信号权零拦截权, 仅观测登记(prereg §6)",
    }
    path = os.path.join(OUT_DIR, f"s2b_shadow_{T}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[ok] {path}")
    print(f"涨停{len(limit_up)}只 | 聚集行业(>= {CLUSTER_MIN}家): {clusters}")
    for s, v in low_pos.items():
        print(f"  {s}: 中位动量{v['median_mom']:+.1f}% 低位组{v['n_low']}/{v['n_sector']}只")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
