# -*- coding: utf-8 -*-
"""swing_dip_scan_20261029.py — 深潜信号源每日扫描器(主人令「继续」·方案B落地)

规则(Phase4 walk-forward 验收口径): 20日跌幅≤-20% × 滚动HMM「上涨趋势」环境 → 次日买入, 持有20日。
OOS 证据: +9.51%/胜率73.1%(14.3万样本), 分年5正3负1弱(2020式结构行情仍会骗, 已知边界如实留档)。

每日盘后运行:
  ① 用截至当日近750根宇宙等权观测滚动拟合HMM(只用过去数据, 无前视), 解码当日状态;
  ② 全宇宙扫 20日跌≤-20% 候选(剔ST/科创/北交所/持仓/价格>30元);
  ③ 状态=上涨趋势 且 有候选 → 生成≤5只 plan(确定性排序: pos250最低优先) 供 executor 次日执行;
     状态≠上涨趋势 → 环境开关关闭, 落盘 no_signal(纪律: 开关不开不下单)。
产物: data/swing_dip_state_latest.json + data/swing_dip_plan_<date>.json
"""
import os, sys, json, sqlite3, time
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20150101 order by date")})
dmin = dates_all[0]
gidx = {d: i for i, d in enumerate(dates_all)}
rows = con.execute(
    "select code,date,open,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, c, v in rows:
    d = int(d)
    s = series.setdefault(code, {"g": [], "c": [], "v": []})
    s["g"].append(gidx[d]); s["c"].append(float(c)); s["v"].append(float(v or 0))
for s in series.values():
    for k in ("g", "c", "v"):
        s[k] = np.array(s[k])
N = len(dates_all)
T_date = dates_all[-1]
print(f"数据截至: {T_date} ({N}日 {len(series)}只, {time.time()-t0:.0f}s)", flush=True)

# 宇宙等权 + 观测
eq = np.zeros(N); cnt = np.zeros(N)
for code, s in series.items():
    if len(s["c"]) < 130 or code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    eq[s["g"]] += s["c"]; cnt[s["g"]] += 1
eq = eq/np.maximum(cnt, 1)
for i in range(1, N):
    if np.isnan(eq[i]): eq[i] = eq[i-1]
lr = np.diff(np.log(eq))
vol20 = np.array([np.std(lr[max(0, i-19):i+1])*np.sqrt(250)*100 for i in range(len(lr))])
OBS = np.column_stack([lr*100, vol20])[20:]

from hmmlearn.hmm import GaussianHMM
h = GaussianHMM(n_components=4, covariance_type="full", n_iter=200, random_state=42)
h.fit(OBS[-750:])
st_all = h.predict(OBS[-750:])
mr = {k: OBS[-750:][st_all == k, 0].mean() for k in range(4)}
order = sorted(range(4), key=lambda k: -mr[k])
up_s, dn_s = order[0], order[-1]
mid = order[1:3]
mv = {k: OBS[-750:][st_all == k, 1].mean() for k in mid}
wide = max(mid, key=lambda k: mv[k]); narrow = min(mid, key=lambda k: mv[k])
lab = {up_s: "上涨趋势", dn_s: "下跌趋势", wide: "宽幅震荡", narrow: "窄幅震荡"}
state_today = lab[st_all[-1]]
print(f"滚动HMM(近750根拟合): 今日状态 = {state_today}", flush=True)

# 持仓
doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref): cc = cc[len(pref):]
    held.add(cc.zfill(6))

# 超跌候选
cands = []
for code, s in series.items():
    n = len(s["c"])
    if n < 130 or code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    gi = s["g"][-1]
    if gi != N - 1:
        continue  # 当日停牌缺数据
    c = s["c"]
    Tc = c[-1]
    if not (0.5 < Tc <= 30):
        continue
    if code in held:
        continue
    r20 = c[-1]/c[-21] - 1
    if r20 > -0.20:
        continue
    lo250 = c[-250:].min(); hi250 = c[-250:].max()
    pos250 = (Tc - lo250)/max(hi250 - lo250, 1e-9)*100
    cands.append({"code": code, "name": "", "price": round(float(Tc), 2),
                  "r20": round(float(r20)*100, 1), "pos250": round(float(pos250), 1)})
from stock_name_lookup import get_name
for x in cands:
    x["name"] = get_name(x["code"]) or ""
cands = [x for x in cands if "ST" not in x["name"].upper() and "退" not in x["name"]]
cands.sort(key=lambda x: x["pos250"])   # 确定性排序: 位置最低优先
cands = cands[:8]

# ── 60分介入闸门(主人多周期框架执行层): 超跌票必须出现B1/B2/B3企稳结构才放行, 防接刀 ──
from m60_gate import judge
gated = []
for x in cands:
    g = judge(x["code"])
    x["gate"] = g
    if g["pass"]:
        gated.append(x)
    print(f"  60分闸[{x['name']:<6}] {g['struct'] or 'WAIT':<10} {g['reason'][:40]}")
cands = gated[:5]
if cands:
    for x in cands:
        if x.get("gate", {}).get("region"):
            x["price"] = x["gate"]["region"][1]   # 限价=介入区域上沿(挂单等回踩)

state_doc = {"scan_date": T_date, "state_today": state_today,
             "state_map": {v: k for k, v in lab.items()},
             "n_candidates": len(cands), "candidates": cands,
             "rule": "20日跌≤-20% × 滚动HMM上涨趋势 → 次日买hold20 (wf-OOS +9.51%/73.1%)",
             "built_at": time.strftime("%Y-%m-%d %H:%M")}
json.dump(state_doc, open(os.path.join(ROOT, "data", "swing_dip_state_latest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# 环境闸v2(20261031主人"继续"令批准): 禁宽幅震荡(十年2029段裁决: 宽幅-1.01%/35.2%全场最差),
# 下跌趋势+0.52%正期望放行(超跌反弹仍有肉)——替代v1"只留上涨趋势"。
# 注: 深潜wf +9.51%口径=上涨趋势最强, 此处放宽下跌趋势是承接2029段一般化裁决, 差异留档观察。
if state_today == "宽幅震荡":
    print(f"\n环境开关关闭(今日={state_today}, 环境闸v2禁宽幅震荡): 无信号, 不生成plan。纪律: 开关不开不下单。")
    print(f"候选(留档不执行): {len(cands)}只 {[(x['name'], x['code']) for x in cands]}")
    print(f"落盘: data/swing_dip_state_latest.json ({time.time()-t0:.0f}s)")
    sys.exit(0)

if not cands:
    print("\n状态=上涨趋势 但 今日无超跌候选: 无信号。")
    sys.exit(0)

plan = [{"code": x["code"], "name": x["name"], "source": "swing_dip_env",
         "price": x["price"], "volume": 100, "sector": "",
         "note": f"深潜: r20={x['r20']}% pos250={x['pos250']}% {x.get('gate',{}).get('struct','')}介入区域{x.get('gate',{}).get('region')}",
         "execute": True}
        for x in cands]
pout = os.path.join(ROOT, "data", "swing_dip_plan_latest.json")
json.dump(plan, open(pout, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n【信号触发】状态={state_today} 候选{len(cands)}只:")
for x in cands:
    print(f"  {x['name']:<8} {x['code']} @{x['price']} r20={x['r20']}% pos250={x['pos250']}%")
print(f"plan落盘: {pout} ({time.time()-t0:.0f}s)")
