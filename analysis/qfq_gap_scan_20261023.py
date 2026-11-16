# -*- coding: utf-8 -*-
"""qfq_gap_scan_20261023.py — D1 全库扫描: raw vs qfq 相邻日收益对照, 复权缺口全量证据

背景: 2026-10-23 12题验收 D1 实测——250 只抽样发现 1996-2007 存在 10送10 型除权
qfq 因子漏权(000594/600485/000627), 2016+ 生产域抽样干净。本件把抽样升级为
全库证据, 供主人裁决"登记债务限域 vs 补因子扩域"。

方法:
  ① 全库流式扫 raw 库 stock_daily_raw(仅股票前缀 60/68/00/30/43/83/87/92,
    对齐 lhb_update.py:70 先例口径), 相邻日跌幅 ≤-30% 记为除权/深跌候选;
  ② 逐候选到 qfq 库取同两日收盘, 算 qfq 收益率分桶:
    - |qfq收益|≤21% → 复权生效
    - >21% 且 raw 比价命中送转签名(≈1/2, 2/3, 3/4, 4/5, 5/6) → 除权签名缺口
      (法定涨跌停制度下单日不可能, 因子漏权硬证据)
    - >21% 无签名 → 疑似(含上市初期/股改复牌无涨跌幅期真实深跌可能, 如实标注)
  ③ 附: raw 有而 qfq 全库无行的股票代码数(宇宙覆盖缺口, 幸存者偏差参考)。

只读·零交易权·零生产改动。输出 out json + 控制台摘要。
"""
import json
import sqlite3
import sys
from collections import Counter
from datetime import date

QFQ_URI = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
RAW_URI = "file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
OUT_JSON = r"D:\zcodesj\analysis\qfq_gap_scan_20261023_out.json"

STOCK_PREFIX = ("60", "68", "00", "30", "43", "83", "87", "92")
DROP_THRESH = -0.30          # 相邻日跌幅候选阈值
QFQ_LIMIT = 0.21             # qfq 收益率超过此值 = 非普通涨跌停可解释
LIMIT_ERA = 19961216         # A股涨跌停制度实施日
PROD_FROM = 20160101         # 现行生产回测域起点
SIGNATURES = [(0.5, 0.035), (2 / 3, 0.035), (0.75, 0.03), (0.8, 0.03), (5 / 6, 0.025)]


def ratio_signature(ratio):
    """送转签名: raw 前后比价≈简单分数 → 除权特征。"""
    for v, tol in SIGNATURES:
        if abs(ratio - v) <= tol:
            return v
    return None


def calendar_days(d1, d2):
    """YYYYMMDD 整数间的自然日差（近似，用于上市窗口判定）。"""
    f = lambda v: date(v // 10000, v // 100 % 100, v % 100)
    return abs((f(d2) - f(d1)).days)


def listing_window_rows(code, row_idx, dt):
    """上市初期无涨跌幅窗口（按该股第 N 个交易行计）:
    科创板68(2019-07-22起)/创业板30(2020-08-24注册制起)/主板60·00(2023-02-17全面注册制起)
    前5个交易日无涨跌幅 → 放行前6行; 其余(核准制首日44%帽) → 放行前2行。"""
    reg5 = (code.startswith("68") and dt >= 20190722) or \
           (code.startswith("30") and dt >= 20200824) or \
           (code[:2] in ("60", "00") and dt >= 20230217)
    return row_idx <= (6 if reg5 else 2)


def main():
    con_q = sqlite3.connect(QFQ_URI, uri=True)
    con_r = sqlite3.connect(RAW_URI, uri=True)

    qfq_codes = {r[0] for r in con_q.execute("select distinct code from stock_daily_meta_qfq")}

    cases = []           # 除权/深跌候选 (带该股交易行序号)
    raw_stocks = set()
    cur = con_r.execute("select code, date, close from stock_daily_raw order by code, date")
    prev_code, prev_date, prev_close, row_idx = None, None, None, 0
    while True:
        rows = cur.fetchmany(200_000)
        if not rows:
            break
        for code, date, close in rows:
            if not (code and code.startswith(STOCK_PREFIX)):
                prev_code, prev_close = None, None
                continue
            if code != prev_code:
                prev_code, prev_date, prev_close, row_idx = code, date, close, 1
                raw_stocks.add(code)
                continue
            row_idx += 1
            raw_stocks.add(code)
            if prev_close and close and prev_close > 0:
                r = close / prev_close - 1.0
                if r <= DROP_THRESH:
                    cases.append((code, prev_date, date, prev_close, close, r, row_idx))
            prev_date, prev_close = date, close
    cur.close()

    missing_universe = sorted(raw_stocks - qfq_codes)

    buckets = Counter()
    missing_by_era = Counter()
    prod_missing_cases = []
    sig_cases, suspect_cases, prod_suspects = [], [], []
    for code, pd_, dt, pc, c, r, row_idx in cases:
        q = con_q.execute(
            "select cast(date as text), close from stock_daily_meta_qfq "
            "where code=? and cast(date as text) in (?,?)", (code, str(pd_), str(dt))).fetchall()
        qd = dict(q)
        if str(pd_) not in qd or str(dt) not in qd or not qd[str(pd_)]:
            buckets["qfq缺行"] += 1
            missing_by_era["生产域" if dt >= PROD_FROM else "历史域"] += 1
            if dt >= PROD_FROM and len(prod_missing_cases) < 50:
                prod_missing_cases.append(dict(code=code, prev_date=str(pd_), date=str(dt),
                                               ret_raw=round(r, 4), row_idx=row_idx))
            continue
        rq = qd[str(dt)] / qd[str(pd_)] - 1.0
        rec = dict(code=code, prev_date=str(pd_), date=str(dt),
                   prev_close=pc, close=c, ret_raw=round(r, 4),
                   qfq_prev=qd[str(pd_)], qfq_close=qd[str(dt)], ret_qfq=round(rq, 4),
                   ratio=round(c / pc, 4), in_prod_domain=bool(dt >= PROD_FROM),
                   row_idx=row_idx)
        # 分类优先级: ①上市初期无涨跌幅窗口=真实深跌(先判, 排除签名误吞)
        #            ②|qfq收益|≤21%=复权生效  ③送转签名=因子漏权硬证据  ④其余=疑似
        if listing_window_rows(code, row_idx, dt):
            buckets["上市初期真跌(无涨跌幅窗口)"] += 1
            continue
        if abs(rq) <= QFQ_LIMIT:
            buckets["复权生效"] += 1
            continue
        sig = ratio_signature(c / pc)
        if sig:
            buckets["除权签名缺口"] += 1
            rec["signature"] = sig
            if dt >= PROD_FROM:
                prod_suspects.append(rec)
            sig_cases.append(rec)
        else:
            era_tag = ("疑似(涨跌停制度前真跌可能)" if dt < LIMIT_ERA
                       else "疑似(缺口, 待个案定性)")
            buckets[era_tag] += 1
            rec["note"] = era_tag
            suspect_cases.append(rec)
            if dt >= PROD_FROM:
                prod_suspects.append(rec)

    out = {
        "meta": {"scan_date": "20261023", "drop_thresh": DROP_THRESH, "qfq_limit": QFQ_LIMIT,
                 "raw_stock_codes": len(raw_stocks), "qfq_codes": len(qfq_codes),
                 "raw_stock_codes_missing_from_qfq": len(missing_universe)},
        "buckets": dict(buckets),
        "qfq_missing_by_era": dict(missing_by_era),
        "prod_domain_missing_cases": prod_missing_cases,
        "prod_domain_suspects": prod_suspects,
        "signature_cases": sig_cases[:60],
        "missing_universe_sample": missing_universe[:40],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("=== D1 全库扫描结果 ===")
    print(f"raw股票代码: {len(raw_stocks)} | qfq代码: {len(qfq_codes)} | "
          f"raw有而qfq全缺(宇宙覆盖缺口): {len(missing_universe)}")
    print(f"除权/深跌候选(相邻日≤-30%): {sum(buckets.values())} 例")
    for k, v in buckets.most_common():
        print(f"  {k}: {v}")
    print(f"  qfq缺行分域: {dict(missing_by_era)}")
    print(f"  生产域缺行逐例(前20):")
    for m in prod_missing_cases[:20]:
        print(f"    {m['code']} {m['prev_date']}->{m['date']} ret_raw={m['ret_raw']:+.1%} 行序{m['row_idx']}")
    print(f"\n除权签名缺口明细(前12, 因子漏权硬证据):")
    for r in sig_cases[:12]:
        print(f"  {r['code']} {r['prev_date']}->{r['date']}: raw {r['prev_close']}→{r['close']} "
              f"(比价{r['ratio']:.3f}≈签名{r['signature']:.3f}) ret_raw={r['ret_raw']:+.1%} "
              f"ret_qfq={r['ret_qfq']:+.1%} 生产域={r['in_prod_domain']}")
    print(f"\n生产域(≥20160101)疑似/签名缺口: {len(prod_suspects)} 例")
    for r in prod_suspects[:10]:
        print(f"  {r['code']} {r['prev_date']}->{r['date']} ret_raw={r['ret_raw']:+.1%} "
              f"ret_qfq={r['ret_qfq']:+.1%} note={r.get('note', '签名缺口')}")
    print(f"\nOUT -> {OUT_JSON}")
    con_q.close()
    con_r.close()


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
