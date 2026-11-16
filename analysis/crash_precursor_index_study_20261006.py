# -*- coding: utf-8 -*-
"""
W3 大阴小阳崩溃前兆指数级验证 (B站学习第40轮, 2026-10-06)
清华演讲可量化条目: 「大阳小阴调整 / 大阴小阳崩溃前兆」(tmp/bili_deepdive_20261006/opus_qinghua_speech.txt)

判据 LOCKED (写死于看结果前, 设计值非拟合):
- 大阴日: 上证 pct_chg <= -2.0%
- 反弹日 = 大阴后第一个交易日, 分组:
    weak   小阳组   0 < chg < +1.0%
    strong 强反弹   chg >= +1.0%
    flat   续弱组   chg <= 0
- 镜像: 大阳日 pct >= +2.0%, 次日小阴 -1.0% < chg < 0 (=「大阳小阴调整」) vs 次日 chg >= 0
- 前向: 反弹日收盘起指数自身 fwd1/fwd5/fwd20 (等权代理=指数, 无成本, 描述层)

边界: 单市场单资产描述统计; 不立仓位规则(SENTIMENT-CYCLE证伪族边界, 引用prereg_preflight 20261006查证);
PD-034(PREREG_PANIC_OVERSHOOT_20261006 ACTIVE)为恐慌事件买入时机研究, 本研究是其事件内部
「反弹质量」分层变量的独立口径验证, 非重复立项(条件差异: 全史宽事件集 vs 26事件A×B合取)。
观测层零交易权。
"""
import json
import os
import sys

import tushare as ts

HERE = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
OUT_JSON = os.path.join(HERE, "tmp", "crash_precursor_index_20261006.json")

TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()
if not TOKEN:
    sys.exit("FATAL: TUSHARE_TOKEN missing")


def pct_stats(rets):
    rets = [r for r in rets if r is not None]
    if not rets:
        return {"n": 0}
    rs = sorted(rets)
    n = len(rs)
    med = rs[n // 2] if n % 2 else (rs[n // 2 - 1] + rs[n // 2]) / 2
    return {
        "n": n,
        "med": round(med, 4),
        "mean": round(sum(rs) / n, 4),
        "win": round(100.0 * sum(1 for r in rs if r > 0) / n, 1),
    }


def main():
    pro = ts.pro_api(TOKEN)
    df = pro.index_daily(ts_code="000001.SH", start_date="19900101", end_date="20261006")
    if df is None or df.empty:
        sys.exit("FATAL: index_daily empty")
    df = df.sort_values("trade_date").reset_index(drop=True)
    chg = df["pct_chg"].tolist()
    dates = df["trade_date"].tolist()
    closes = df["close"].tolist()
    n = len(chg)
    print(f"index rows={n} span={dates[0]}..{dates[-1]}")

    def fwd(idx, k):
        j = idx + k
        if j >= n:
            return None
        return 100.0 * (closes[j] / closes[idx] - 1.0)

    big_down_idx = [i for i in range(n - 1) if chg[i] <= -2.0]
    big_up_idx = [i for i in range(n - 1) if chg[i] >= 2.0]

    groups = {"weak": [], "strong": [], "flat": []}
    mirror_adj, mirror_cont = [], []  # 大阳后小阴(调整) vs 大阳后续涨
    rows = []
    for i in big_down_idx:
        j = i + 1
        g = "weak" if 0 < chg[j] < 1.0 else ("strong" if chg[j] >= 1.0 else "flat")
        rec = {
            "rebound_date": dates[j],
            "big_down_date": dates[i],
            "rebound_chg": chg[j],
            "group": g,
            "fwd1": fwd(j, 1),
            "fwd5": fwd(j, 5),
            "fwd20": fwd(j, 20),
        }
        groups[g].append(rec)
        rows.append(rec)
    for i in big_up_idx:
        j = i + 1
        rec = {
            "after_date": dates[j],
            "big_up_date": dates[i],
            "next_chg": chg[j],
            "fwd5": fwd(j, 5),
            "fwd20": fwd(j, 20),
        }
        if -1.0 < chg[j] < 0:
            mirror_adj.append(rec)
        elif chg[j] >= 0:
            mirror_cont.append(rec)

    def block(recs, keys=("fwd1", "fwd5", "fwd20")):
        return {k: pct_stats([r[k] for r in recs]) for k in keys if any(r.get(k) is not None for r in recs)}

    result = {
        "meta": {
            "rows": n,
            "span": [dates[0], dates[-1]],
            "locked_thresholds": {"big_down": -2.0, "weak_rebound": "(0,1)", "strong_rebound": ">=1", "big_up": 2.0, "small_yin": "(-1,0)"},
        },
        "big_down_followup": {g: block(recs) for g, recs in groups.items()},
        "big_up_followup": {"adj_small_yin": block(mirror_adj, ("fwd5", "fwd20")), "cont_up": block(mirror_cont, ("fwd5", "fwd20"))},
    }

    # 分年段稳健性: 1990-2014 / 2015-2026
    for seg, lo, hi in (("1990-2014", "19900101", "20141231"), ("2015-2026", "20150101", "20261231")):
        sub = {g: [r for r in recs if lo <= r["rebound_date"] <= hi] for g, recs in groups.items()}
        result[f"segment_{seg}"] = {g: block(recs) for g, recs in sub.items()}

    # PD-034 事件子集交叉(仅参考, n极小): 26恐慌事件日期不在本脚本重建, 略——以全史宽口径为准
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"result": result, "rows": rows + mirror_adj + mirror_cont}, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"saved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
