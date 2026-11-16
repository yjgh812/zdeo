# -*- coding: utf-8 -*-
"""探针(只读, 不写任何库): tushare adj_factor 备源可行性双测
① 限频实测: 按日批量(trade_date=全市场)与按票两条路径连发, 钉死真实吞吐
② 影子对拍: 样本股(近期除权票+已知分歧票000626+常规票) sina vs tushare 因子逐日偏差
   + 连续性闸判定 → 回答"新浪断供时 tushare 回退是否可用/可用率"
证据口径: 偏差 = max|sina_factor/tushare_factor - 1| (双源均最新=1.0锚, 可直接比)
"""
import sys, time
sys.path.insert(0, r"D:\zcodesj")
import build_qfq_meta_tdx as m

pro = m._tushare_pro()

# ─── 样本: vipdoc 末 12 个交易日(本地, 零网络) ───
vd = m.read_tdx_day("600519")
dates = [d.strftime("%Y%m%d") for d in vd.index[-12:]]
print(f"[probe] 采样交易日窗: {dates[0]}..{dates[-1]} ({len(dates)}日)")

# ─── ①a 按日批量路径连发限频实测 ───
print("[probe] ①a 按日批量 adj_factor(trade_date=...) 连发12次:")
t0, ok, rows, fails = time.time(), 0, 0, 0
for d in dates:
    ts1 = time.time()
    try:
        df = pro.adj_factor(trade_date=d)
        n = 0 if df is None else len(df)
        rows += n
        ok += 1
        print(f"  {d} rows={n} {time.time()-ts1:.2f}s")
    except Exception as e:
        fails += 1
        print(f"  {d} FAIL {type(e).__name__}: {str(e)[:80]} ({time.time()-ts1:.2f}s)")
dt = time.time() - t0
print(f"[probe] ①a 结果: 成功{ok}/12, 总行数={rows}, 总耗时{dt:.1f}s, "
      f"吞吐≈{(ok/dt*60):.0f}次/分 (失败{fails})")

# ─── ①b 按票路径连发限频实测(5票, 每票10年全史) ───
print("[probe] ①b 按票 adj_factor(ts_code=...) 连发5次:")
t0 = time.time()
for sym in ("600519", "000001", "300750", "000626", "601318"):
    ts1 = time.time()
    try:
        df = pro.adj_factor(ts_code=m._to_backup_symbol(sym, "tushare"),
                            start_date="20160101",
                            end_date=time.strftime("%Y%m%d"))
        n = 0 if df is None else len(df)
        print(f"  {sym} rows={n} {time.time()-ts1:.2f}s")
    except Exception as e:
        print(f"  {sym} FAIL {type(e).__name__}: {str(e)[:80]} ({time.time()-ts1:.2f}s)")
print(f"[probe] ①b 结果: 总耗时{time.time()-t0:.1f}s → 吞吐≈{5/((time.time()-t0)/60):.0f}次/分")

# ─── ② 近期除权票样本采集(pro.dividend 逐日) ───
ex_codes = []
for d in dates[-8:]:
    try:
        df = pro.dividend(ex_date=d, fields="ts_code,div_proc")
        for r in df.itertuples() if df is not None else []:
            if r.div_proc == "实施":
                ex_codes.append(r.ts_code.split(".")[0])
    except Exception as e:
        print(f"[probe] dividend({d}) FAIL: {str(e)[:60]}")
ex_codes = sorted(set(ex_codes))
print(f"[probe] 近8个交易日除权(实施)票: {len(ex_codes)}只 → {ex_codes[:12]}")

# 对拍样本 = 除权票取前4 + 已知分歧票000626 + 常规票600519/000001/300750
sample = list(dict.fromkeys(ex_codes[:4] + ["000626", "600519", "000001", "300750"]))
print(f"[probe] ② 对拍样本({len(sample)}): {sample}")

print("\n[code | sina行数 | tushare行数 | 公共日 | 最大偏差 | tushare过闸? ]")
for sym in sample:
    # sina: 8s超时包装防慢区挂死(0006xx 病态慢区自留量)
    try:
        fs = m._run_with_timeout(lambda s=sym: m.fetch_sina_qfq(s), 15.0)
    except Exception as e:
        fs = None
        print(f"[probe] {sym} sina超时/异常: {type(e).__name__}")
    try:
        ft = m.fetch_tushare_qfq_factor(sym)
    except Exception as e:
        ft = None
        print(f"[probe] {sym} tushare异常: {type(e).__name__}: {str(e)[:60]}")
    def _st(x):
        return "None" if x is None else (x if isinstance(x, str) else f"{len(x)}行")
    if not (fs is not None and not isinstance(fs, str) and len(fs) > 0
            and ft is not None and not isinstance(ft, str) and len(ft) > 0):
        print(f"{sym} | sina={_st(fs)} | tushare={_st(ft)} | 不可比(任一源缺)")
        continue
    common = fs.index.intersection(ft.index)
    if len(common) == 0:
        print(f"{sym} | {len(fs)} | {len(ft)} | 0 | 无公共日 ")
        continue
    a, b = fs.loc[common, "factor"], ft.loc[common, "factor"]
    dev = float((a / b - 1).abs().max())
    gate = m._qfq_continuity_ok(sym, ft)
    # 偏差最大日定位(前3)
    devser = (a / b - 1).abs().sort_values(ascending=False)[:3]
    tops = ", ".join(f"{d.strftime('%Y-%m-%d')}:{v:.4%}" for d, v in devser.items())
    print(f"{sym} | {len(fs)} | {len(ft)} | {len(common)} | {dev:.4%} | "
          f"{'PASS' if gate else 'REJECT'} | top偏差: {tops}")

print("\n[probe] 完成(全程只读, 未写任何库)")
