# -*- coding: utf-8 -*-
"""swing_dual_tf_select_20261030.py — 日线+60分钟双层选股管道

流程:
  第一层(日线): ATR自适应zigzag定波段方向 → 只允许up段/forming_up段
  第二层(60分): 拉候选票60分K → 检测B1/B2/B3介入结构 → 只放行有结构的票
  第三层(排序): 60分结构质量+pos250位置综合排序 → 取TOP5
输出: 通过双层的最终买入候选+介入区域限价
"""
import os, sys, json, sqlite3, time
import numpy as np
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from m60_gate import judge, fetch_m60
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"

def daily_wave_direction(code):
    """日线ATR-zigzag波段方向: 返回'up'/'dn'/'forming_up'/'forming_dn'/None"""
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    rows = con.execute("select date,high,low,close from stock_daily_meta_qfq where code=? and date>=20240101 order by date",
                       (code,)).fetchall()
    con.close()
    if len(rows) < 60:
        return None
    hi = np.array([float(r[1]) for r in rows])
    lo = np.array([float(r[2]) for r in rows])
    cl = np.array([float(r[3]) for r in rows])
    n = len(cl)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
    tr[0] = hi[0]-lo[0]
    k = 1/14
    atr = np.empty(n); atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = atr[i-1] + k*(tr[i]-atr[i-1])
    piv = []; trend = 0; ext_i = 0
    for i in range(1, n):
        thr = max(0.05, atr[i]/max(cl[i], 1e-9)*2.2)
        r = cl[i]/cl[ext_i] - 1
        typ_prev = piv[-1][2] if piv else None
        if trend >= 0 and r <= -thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            piv.append((ext_i, cl[ext_i], typ, i)); trend = -1; ext_i = i
        elif trend <= 0 and r >= thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            piv.append((ext_i, cl[ext_i], typ, i)); trend = 1; ext_i = i
        else:
            if (trend >= 0 and cl[i] > cl[ext_i]) or (trend <= 0 and cl[i] < cl[ext_i]) or trend == 0:
                ext_i = i
    if len(piv) < 2:
        return None
    conf = piv  # 所有pivot都是已确认的(形成下一个pivot时前一个才被确认)
    a = conf[-2]; b = conf[-1]
    if a[2] == "L" and b[2] == "H":
        return "up"
    if a[2] == "H" and b[2] == "L":
        return "dn"
    if trend == 1:
        return "forming_up"
    if trend == -1:
        return "forming_dn"
    return None

def daily_ma20_ok(code):
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    rows = con.execute("select close from stock_daily_meta_qfq where code=? and date>=20240101 order by date", (code,)).fetchall()
    con.close()
    if len(rows) < 25: return False
    cl = [float(r[0]) for r in rows]
    ma20 = sum(cl[-20:])/20
    ma20_prev = sum(cl[-23:-3])/20
    return cl[-1] > ma20 and ma20 >= ma20_prev

def run(candidates, label):
    """双层选股: candidates=[{code,name,price}]"""
    results = []
    print(f"\n{'='*60}")
    print(f" 双层选股: {label} ({len(candidates)}只候选)")
    print(f"{'='*60}")
    for c in candidates:
        code = c["code"]
        nm = c.get("name", code)
        # 层1: 日线方向
        dw = daily_wave_direction(code)
        ma_ok = daily_ma20_ok(code)
        # 层2: 60分结构
        g = judge(code)
        # 判定
        daily_pass = dw in ("up", "forming_up") and ma_ok
        m60_pass = g["pass"]
        final = daily_pass and m60_pass
        region = g.get("region")
        results.append({
            "code": code, "name": nm, "daily_wave": dw, "daily_ma20": ma_ok,
            "m60_struct": g.get("struct",""), "m60_pass": m60_pass,
            "m60_region": region, "m60_reason": g.get("reason",""),
            "final_pass": final, "price": c.get("price", 0),
            "limit": round(region[1], 2) if region else round(c.get("price",0)*1.03, 2),
        })
        dw_s = dw or "none"
        m60_s = g.get("struct", "none") or "none"
        flag = "✅✅ 双确认" if final else ("📍 日线✓ 60分✗" if daily_pass else ("📍 日线✗ 60分✓" if m60_pass else "❌ 双✗"))
        print(f"  {flag} {nm:<8} {code} 日线={dw_s:<12} 60分={m60_s:<8} 区域={region}")
    passed = [r for r in results if r["final_pass"]]
    passed.sort(key=lambda x: (x["m60_struct"], x["price"]))
    print(f"\n  双层确认通过: {len(passed)}/{len(candidates)}只")
    for r in passed:
        print(f"    ✅ {r['name']} {r['code']} 介入区域{r['m60_region']} 限价{r['limit']}")
    return results, passed

if __name__ == "__main__":
    # 演示: 用当前持仓中的实验组票做双层判定
    test_codes = [
        {"code": "600320", "name": "振华重工", "price": 4.27},
        {"code": "603697", "name": "有友食品", "price": 9.43},
        {"code": "600098", "name": "广州发展", "price": 6.48},
        {"code": "600688", "name": "上海石化", "price": 2.80},
        {"code": "601577", "name": "长沙银行", "price": 9.78},
        {"code": "600023", "name": "浙能电力", "price": 5.20},
        {"code": "601156", "name": "东航物流", "price": 18.22},
        {"code": "600926", "name": "杭州银行", "price": 17.09},
        {"code": "002534", "name": "西子洁能", "price": 15.40},
        {"code": "600078", "name": "澄星股份", "price": 9.42},
    ]
    results, passed = run(test_codes, "双层选股演示(实验组持仓票)")
    json.dump({"results": results, "passed": passed},
              open(os.path.join(ROOT, "data", "dual_tf_select_20261030.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n落盘: data/dual_tf_select_20261030.json")
