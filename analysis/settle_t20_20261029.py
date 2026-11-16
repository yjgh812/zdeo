# -*- coding: utf-8 -*-
"""settle_t20_20261029.py — T+20 统一结算工具(影子结算·全部信号源source分组)

口径(宪法对齐):
  影子收益: 建仓日T → 第20个交易日后收盘 c[T+20]/c[T]-1 (qfq库, 未复权差分与涨跌幅一致)。
  2026-10-31 增强(缺口1闭合): ①与同期等权全A基准对照 → 超额(excess) ②HMM环境(mkt_state,
  建仓日回看0..5交易日)分组 by_env ③报告顶层声明基准口径。排名用超额/环境双口径, 防普跌月误伤。
  资金真值: westock MCP 唯一真值(本工具不替代), 本工具为快速统计裁决口径。
  未到期(source最新建仓距今<20交易日) → 输出 pending 不结算。
用法: python settle_t20_20261029.py [--asof YYYYMMDD] [--selftest]
"""
import os, sys, json, sqlite3, time, argparse
from collections import defaultdict
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SWING = r"D:/zcodesj/data/swing_labels.db"

ap = argparse.ArgumentParser()
ap.add_argument("--asof", default="")
ap.add_argument("--selftest", action="store_true",
                help="单笔合成建仓自检: 验证 基准窗口/环境标注/超额 计算路径(真实qfq数据, 非生产数据)")
a = ap.parse_args()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
scon = sqlite3.connect(f"file:{SWING}?mode=ro", uri=True)
mkt = dict(scon.execute("select date, label from mkt_state").fetchall())
scon.close()

dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq order by date")})
didx = {d: i for i, d in enumerate(dates_all)}
asof = int(a.asof) if a.asof else dates_all[-1]
asof_i = didx.get(asof, len(dates_all) - 1)

# 1) 台账 → 每source建仓清单(同source同code同日去重, 取最早建仓日)
builds = defaultdict(dict)   # source -> code -> (date, name)
if a.selftest:
    # 自检臂: 真实历史窗口单笔(600519 贵州茅台, 库内倒数第45个交易日, 全程有数据), 仅验证计算路径
    _td = dates_all[-45]
    builds["selftest"] = {"600519": (_td, "贵州茅台")}
else:
    for line in open(os.path.join(ROOT, "data", "executor_orders.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("action", "")).upper() != "BUY":
            continue
        if str(r.get("status", "")).upper() not in ("SUBMITTED", "FILLED", "OPEN"):
            continue
        src, code = r.get("source"), str(r.get("code", ""))
        td = str(r.get("trade_date", ""))
        if not src or len(code) != 6 or not td.isdigit():
            continue
        code = code.zfill(6)
        td_i = int(td)
        if code not in builds[src] or td_i < builds[src][code][0]:
            builds[src][code] = (td_i, r.get("name", ""))

# 2) 价格map(按需加载)
need_codes = sorted({c for m in builds.values() for c in m})
if not need_codes:
    print("无建仓记录, 退出")
    sys.exit(0)
ph = ",".join("?" * len(need_codes))
rows = con.execute(
    f"select code,date,close from stock_daily_meta_qfq where date>=20150101 and code in ({ph}) order by code,date",
    need_codes).fetchall()
px = defaultdict(dict)
for code, d, c in rows:
    px[code][int(d)] = float(c)

# 2b) 等权全A基准日收益序列(SQL窗口LAG一次聚合; 代理口径: 全A剔除科创68/北交8/4/9前缀,
#     含ST——无ST历史库, 代理偏差如实标注于报告 bench.note。窗口: 建仓日+1..+20 累计)
start_d = min(td for m in builds.values() for (td, _) in m.values())
brows = con.execute(
    """select date, avg(1.0*close/prev-1.0) from (
         select code, date, close,
                lag(close) over (partition by code order by date) as prev
         from stock_daily_meta_qfq
         where date>=? and substr(code,1,2) in ('60','00','30')
       ) where prev>0 group by date""", (start_d,)).fetchall()
bench = {int(d): float(r) for d, r in brows if r is not None}
con.close()

# 3) 逐source结算
report = {"asof": asof, "generated_at": time.strftime("%Y-%m-%d %H:%M"),
          "bench": {"kind": "equal_weight_allA_proxy",
                    "note": "等权全A代理: 全A剔除科创68/北交前缀(含ST未剔除); 建仓日次日起20交易日等权累计",
                    "days": len(bench)},
          "sources": {}}
pending_all = []
for src, m in sorted(builds.items()):
    rets = []; xs_list = []; env_lists = defaultdict(list)
    pend = 0; skip_no_px = 0
    for code, (td_i, name) in m.items():
        p = px.get(code, {})
        if td_i not in didx or td_i not in p:
            skip_no_px += 1
            continue
        ti = didx[td_i]
        j = ti + 20
        if j >= asof_i or dates_all[min(j, len(dates_all) - 1)] > asof:
            pend += 1
            continue
        d20 = dates_all[j]
        c0 = p.get(td_i)
        c20 = p.get(d20)
        if c0 is None or c20 is None:
            # 建仓价用建仓日收盘缺失时回退后一交易日
            alt = p.get(dates_all[min(ti + 1, len(dates_all) - 1)])
            c0 = alt if alt else None
            if c0 is None:
                skip_no_px += 1
                continue
        ret = c20 / c0 - 1
        rets.append(ret)
        # 基准同窗累计: 建仓日次日起20个交易日
        cum = 1.0
        for k in range(ti + 1, ti + 21):
            cum *= (1.0 + bench.get(dates_all[k], 0.0))
        xs_list.append(ret - (cum - 1.0))
        # HMM环境: 建仓日回看0..5交易日(与 horse_race 提前参考版同口径)
        env = "无状态"
        for off in range(6):
            if td_i - off in mkt:
                env = mkt[td_i - off]
                break
        env_lists[env].append(ret)
    if rets:
        arr = np.array(rets)
        xa = np.array(xs_list)
        stat = {"n": len(arr), "mean": round(float(arr.mean()) * 100, 2),
                "median": round(float(np.median(arr)) * 100, 2),
                "win": round(float((arr > 0).mean()) * 100, 1),
                "excess": round(float(xa.mean()) * 100, 2)}
        by_env = {}
        for env, lst in env_lists.items():
            ea = np.array(lst)
            by_env[env] = {"n": len(ea), "mean": round(float(ea.mean()) * 100, 2),
                           "win": round(float((ea > 0).mean()) * 100, 1)}
    else:
        stat = {"n": 0}
        by_env = {}
    report["sources"][src] = {"settled": stat, "by_env": by_env, "pending": pend,
                              "no_price": skip_no_px, "total_builds": len(m)}
    if pend:
        pending_all.append((src, pend))

print(f"=== T+20影子结算 (asof {asof}, 基准={report['bench']['kind']} {len(bench)}日) ===")
for src, v in report["sources"].items():
    st = v["settled"]
    if st["n"]:
        print(f"  {src:<26} 已结算{st['n']:>3} 均值{st['mean']:>+7}% 中位{st['median']:>+7}% "
              f"胜率{st['win']:>5}% 超额{st['excess']:>+7}% | 未到期{v['pending']} 无价{v['no_price']}")
    else:
        print(f"  {src:<26} 未到期{v['pending']} 无价{v['no_price']} (total builds {v['total_builds']})")
if a.selftest:
    print("selftest 模式不落盘生产报告文件")
else:
    out = os.path.join(ROOT, "data", f"settle_t20_report_{asof}.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"落盘: {out}")

if a.selftest:
    print("--- selftest 原始数据 ---")
    v = report["sources"]["selftest"]
    print("settled:", json.dumps(v["settled"], ensure_ascii=False))
    print("by_env:", json.dumps(v["by_env"], ensure_ascii=False))
    ti = didx[_td]
    d20 = dates_all[ti + 20]
    cum = 1.0
    for k in range(ti + 1, ti + 21):
        cum *= (1.0 + bench.get(dates_all[k], 0.0))
    print(f"窗口 {_td}→{d20} 基准累计={(cum - 1.0) * 100:+.2f}% 环境标注={list(v['by_env']) or '无状态'}")
    assert v["settled"]["n"] == 1 and "excess" in v["settled"], "selftest: 超额字段缺失"
    assert v["by_env"], "selftest: 环境分组为空"
    print("selftest PASS: 基准窗口/环境标注/超额 路径全通")
