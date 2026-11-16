# -*- coding: utf-8 -*-
"""右侧启动买点复核 (主人令: 复核是否找错了)
① 抽样20事件: 打印段起日前后原始数据, 人工核对四维(放量大阳/深跌25%/缩量/介入合格)
② 实弹对账: 9/23以来scanner实弹名单 vs 事件库(该票是否有对应段起)
③ 与qfq原始bar逐位重算抽查
输出: reports/rightside_startup_20261011/recheck_report.txt
"""
import os, sys, json, random, glob
import pandas as pd
import numpy as np
import sqlite3

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

D = r"D:\zcodesj\reports\rightside_startup_20261011"
OUT = open(os.path.join(D, "recheck_report.txt"), "w", encoding="utf-8")
em = pd.read_csv(os.path.join(D, "events_main.csv"), dtype={"code": str})
em["code"] = em["code"].str.zfill(6)  # TRAPS: pandas读码丢前导零
db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)

def w(*a):
    print(*a, file=OUT, flush=True)

# ---------- ① 抽样20事件逐位核对 ----------
random.seed(7)
sample = random.sample(range(len(em)), 20)
w("=" * 80)
w("① 抽样20事件逐位核对 (段起s四维 + 介入D + 入场)")
w("=" * 80)
n_pass = 0
issues = []
for idx in sample:
    r = em.iloc[idx]
    code, s_date, d_date = r["code"], int(r["s_date"]), int(r["d_date"])
    rows = db.execute("SELECT date,close,volume FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND date<=? ORDER BY date",
                      (code, s_date - 1000000, d_date + 300)).fetchall()
    dt = [int(x[0]) for x in rows]
    c = np.array([float(x[1]) for x in rows])
    v = np.array([float(x[2]) for x in rows])
    try:
        si = dt.index(s_date)
        di = dt.index(d_date)
    except ValueError:
        w(f"[MISS] {code} {r['name']} s={s_date} 不在序列"); issues.append((code, "s_date缺失")); continue
    pct_s = (c[si] / c[si - 1] - 1) * 100
    v20_s = v[max(0, si - 19):si + 1].mean()
    vr = v[si] / v20_s
    pre60 = c[max(0, si - 60):si]
    pre_dd = (pre60.min() / pre60.max() - 1) * 100 if len(pre60) >= 40 else 0.0
    pre20v = v[max(0, si - 20):si].mean()
    v250 = v[max(0, si - 249):si + 1].mean()
    shrink = pre20v / v250 if v250 > 0 else 9
    lo250 = c[max(0, si - 249):si + 1].min()
    lo_pos = int(np.argmin(c[max(0, si - 249):si + 1]))
    seg_before_lo = c[max(0, si - 249) + lo_pos:si]
    # 首段起: lo→s之间无更早放量大阳
    ok_cand = True
    for i in range(max(0, si - 249) + lo_pos + 1, si):
        v20i = v[max(0, i - 19):i + 1].mean()
        if v[i] >= 2 * v20i and c[i] / c[i - 1] - 1 >= 0.03:
            ok_cand = False
            break
    checks = {
        "放量大阳量比≥2": vr >= 2, "段起涨≥3%": pct_s >= 3,
        "前60深跌≥25%": pre_dd <= -25 if len(pre60) >= 40 else True,
        "启动前缩量≤0.95": shrink <= 0.95 if len(pre60) >= 40 else True,
        "首段起(窗内无更早)": ok_cand,
    }
    passed = all(checks.values())
    n_pass += passed
    w(f"\n#{idx} {code} {r['name']} 段起{s_date} 介入{d_date}(days={r['days']}) px={r['px_D']}")
    w(f"   量比={vr:.2f}(≥2:{'✓' if checks['放量大阳量比≥2'] else '✗'}) 涨幅={pct_s:.2f}%(≥3:{'✓' if checks['段起涨≥3%'] else '✗'}) "
      f"深跌={pre_dd:.1f}%(≤-25:{'✓' if checks['前60深跌≥25%'] else '✗'}) 缩量={shrink:.2f}(≤0.95:{'✓' if checks['启动前缩量≤0.95'] else '✗'}) "
      f"首段起={'✓' if ok_cand else '✗'} → {'PASS' if passed else 'FAIL'}")
    w(f"   段起前5日: " + " ".join(f"{dt[si-k-1]}:{(c[si-k]/c[si-k-1]-1)*100:+.1f}%" for k in range(min(5, si))))
    w(f"   介入日D: close={c[di]:.2f} off_start={(c[di]/c[si]-1)*100:.1f}% 入场T+1开盘={r['entry']:.2f}")
    if not passed:
        issues.append((code, [k for k, ok in checks.items() if not ok]))
w(f"\n抽样核对结果: {n_pass}/20 PASS" + (f"  ⚠问题事件: {issues}" if issues else "  全部口径复算一致"))

# ---------- ② 实弹名单对账 ----------
w("\n" + "=" * 80)
w("② 实弹名单对账 (scanner每日命中票 → 事件库应有对应段起)")
w("=" * 80)
scans = sorted(glob.glob(r"D:\zcodesj\reports\universe_startup_scan_202609*.json") +
               glob.glob(r"D:\zcodesj\reports\universe_startup_scan_202610*.json"))
hit_n = miss_n = 0
for p in scans[-3:]:
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    rows = d.get("rows") or d.get("hits") or []
    w(f"\n{os.path.basename(p)}: 实弹命中 {len(rows)} 只")
    for r0 in rows[:15]:
        code = str(r0.get("code", "")).zfill(6)[-6:]
        nm = r0.get("name", "")
        # 事件库中该票在扫描日±160日内的段起
        scan_date = int(str(d.get("date", "20261009")).replace("-", "")[:8] or 20261009)
        m = em[(em.code == code) & (em.s_date <= scan_date) &
               (em.s_date >= scan_date - 16000)]
        if len(m):
            hit_n += 1
            w(f"   {code} {nm} → 段起 {sorted(m.s_date.tolist())[-3:]} ✓")
        else:
            miss_n += 1
            w(f"   {code} {nm} → 事件库无近期段起 ⚠ (可能段起超40日窗/缩量门状态差异)")
w(f"\n实弹对账: {hit_n} 命中 / {miss_n} 未命中 (未命中≠错, scanner含启动窗2-40日追踪态, 事件库锚段起首日)")

# ---------- ③ 结构统计 ----------
w("\n" + "=" * 80)
w("③ 事件结构一致性")
w("=" * 80)
w(f"主事件唯一性: code+s_date唯一率={(~em.duplicated(['code','s_date'])).mean()*100:.1f}%")
w(f"介入D≥段起s: {(em.d_date >= em.s_date).mean()*100:.1f}%  days∈[0,40](交易日): {em.days.between(0,40).mean()*100:.1f}%")
w(f"入场=次日开>0: {(em.entry > 0).mean()*100:.1f}%")
w(f"off_start∈[-3,17]: {em.off_start.between(-3, 17).mean()*100:.1f}%")
w(f"价格2-30: {em.px_D.between(2, 30).mean()*100:.1f}%")
OUT.close()
print(open(os.path.join(D, "recheck_report.txt"), encoding="utf-8").read()[:3500])
