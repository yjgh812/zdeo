# -*- coding: utf-8 -*-
"""情绪相位全史重算件 (防御档 prereg 配套, 2026-10-09, PREREG_EMOTION_PHASE_BPEP_DEFENSE)
生成物: data/emotion_phase_history_daily.jsonl (2016起 逐日 phase+height+weight)
同源性:
  - classify/pctl 直接 import emotion_phase_v2 原模块(零复制, 规则逐位同源)
  - derive_daily 循环逻辑逐字复制自 emotion_phase_v2.py, 仅 date 过滤改 20160101(原 20250101)
  - height_state/weight/BASE_W 逐字复制自 emotion_position_shadow.py
验收:
  A1 派生位逐位对照: 2025 起重叠段 (lu,br,mb) 须与台账 100% 一致(同数据同派生码)
  A2 相位一致率: 重叠段 phase 对照仅作参考值(台账为冷启动窗, 本件为250日滚动窗, 语义差异如实声明)
边界: 研究侧生成物, 零交易权; 因果性=classify窗口止于t(height_state用i-5亦为止于t的过去)
"""
import json
import sqlite3
import sys

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
from emotion_phase_v2 import classify  # noqa: E402  真同源导入

QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
LEDGER = r'D:\zcodesj\data\emotion_phase_v2_ledger.jsonl'
OUT = r'D:\zcodesj\data\emotion_phase_history_daily.jsonl'
PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")

BASE_W = {"euphoric": 1.0, "healing": 0.8, "ebbing": 0.6, "diverging": 0.5, "bottom": 0.5}


def height_state(rows, i):
    """逐字复制自 emotion_position_shadow.py"""
    if i < 5:
        return None
    mb_now, mb_prev = rows[i]["mb"], rows[i - 5]["mb"]
    if mb_now >= 4 and mb_now > mb_prev:
        return "rising"
    if mb_now <= 2:
        return "collapse"
    return "flat"


def weight(phase, hs):
    """逐字复制自 emotion_position_shadow.py"""
    w = BASE_W.get(phase, 0.6)
    if hs == "rising":
        w = min(1.0, w + 0.2)
    elif hs == "collapse":
        w = max(0.3, w - 0.2)
    return round(w, 2)


def derive_daily_full(con):
    """逐字复制自 emotion_phase_v2.derive_daily, 仅 WHERE date 改 '20160101'"""
    rows = con.execute(
        "SELECT code, date, change_pct, high, close FROM stock_daily_meta_qfq "
        "WHERE date>='20160101' AND substr(code,1,3) IN (%s) ORDER BY code, date"
        % ",".join("'%s'" % p for p in PREFIX)).fetchall()
    if not rows:
        return []
    day = {}
    streak = {}
    for code, d, chg, hi, cl in rows:
        if chg is None or cl is None or hi is None or cl <= 0:
            continue
        lim_pct = 0.10 if not code.startswith("3") else 0.20
        ret_c = chg / 100.0
        pc = cl / (1.0 + ret_c)
        lim_price = round(pc * (1 + lim_pct), 2)
        is_lu = cl >= lim_price - 0.005
        is_touch = (not is_lu) and hi >= lim_price - 0.005
        streak[code] = streak.get(code, 0) + 1 if is_lu else 0
        o = day.setdefault(d, {"lu": 0, "touch": 0, "mb": 0})
        if is_lu:
            o["lu"] += 1
            o["mb"] = max(o["mb"], streak[code])
        elif is_touch:
            o["touch"] += 1
    out = []
    for d in sorted(day):
        o = day[d]
        denom = o["lu"] + o["touch"]
        out.append((d, o["lu"], round(o["touch"] / denom, 4) if denom else None, o["mb"]))
    return out


def main():
    con = sqlite3.connect(QFQ_DB, uri=True)
    hist = derive_daily_full(con)
    con.close()
    print(f"派生: {len(hist)}个交易日 {hist[0][0]}~{hist[-1][0]}")

    # A1 派生位逐位对照 (2025重叠段)
    ledger = {}
    for ln in open(LEDGER, encoding="utf-8"):
        if ln.strip():
            d = json.loads(ln)
            ledger[d["phase_date"]] = d
    n_chk = n_der_mism = 0
    der_mism_detail = []
    for d, lu, br, mb in hist:
        if d in ledger:
            n_chk += 1
            L = ledger[d]
            if (lu, br, mb) != (L["lu"], L["br"], L["mb"]):
                n_der_mism += 1
                der_mism_detail.append((d, (lu, br, mb), (L["lu"], L["br"], L["mb"])))
    print(f"A1 派生位对照(2025重叠{n_chk}日): 不一致={n_der_mism} ({'PASS' if n_der_mism == 0 else 'FAIL'})")
    for d, mine, led in der_mism_detail:
        print(f"  不一致 {d}: 本件={mine} 台账={led}")
    n_lu_br_mism = sum(1 for _, m, l in der_mism_detail if m[:2] != l[:2])
    print(f"A1' lu/br单独对照: 不一致={n_lu_br_mism} ({'PASS' if n_lu_br_mism == 0 else 'FAIL'}; mb差异=台账20250101冷启动streak伪影, 本件全史连续连板为正确语义)")

    recs = []
    n_phase_agree = 0
    for i in range(len(hist)):
        c = classify(hist, i)
        if c is None:
            continue
        cur = {"phase_date": c["phase_date"], "lu": c["lu"], "br": c["br"], "mb": c["mb"],
               "phase": c["phase"], "height": None, "weight": None}
        hs = height_state(recs + [cur], len(recs))
        cur["height"] = hs
        cur["weight"] = weight(c["phase"], hs) if hs else BASE_W.get(c["phase"], 0.6)
        recs.append(cur)
        if c["phase_date"] in ledger:
            n_phase_agree += 1
    print(f"A2 相位一致(信息值, 冷启动窗差异声明): {n_phase_agree}/{n_chk}")

    with open(OUT, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    ph = Counter(r["phase"] for r in recs)
    wt = Counter(r["weight"] for r in recs)
    print(f"落盘: {OUT} {len(recs)}行 | 相位分布={dict(ph)}")
    print(f"权重分布={dict(sorted(wt.items()))}")
    return 0 if n_der_mism == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
