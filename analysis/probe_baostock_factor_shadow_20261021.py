# -*- coding: utf-8 -*-
"""探针v2(只读): sina↔baostock 因子影子对拍 + baostock 回退吞吐实测
背景: v1实证 tushare adj_factor 当前token档位=1次/分钟(且小时窗更紧), 批量不可行;
     链内真正断供回退源=baostock(fetch_baostock_qfq_factor 自洽比值法, 非涨跌幅复权因子)。
回答两问: ①baostock 因子与 sina 主口径逐日偏差多大/连续性闸通过率(=断供时可回退率)
         ②baostock 每票耗时(login+双查询) → 断供 Sweep N 票的吞吐预算
样本: 多次分红送转老票 + 已知三源分歧票000626 + 创业板300xxx(闸阈值22%分支)
"""
import sys, time
sys.path.insert(0, r"D:\zcodesj")
import build_qfq_meta_tdx as m

SAMPLE = ["600519", "601318", "000001", "600585", "000626", "300750", "000333", "600036"]

print("[probe-v2] ① baostock 每票耗时(login+raw/qfq双查询, 3票连测):")
bt0 = time.time()
for sym in SAMPLE[:3]:
    t1 = time.time()
    try:
        r = m.fetch_baostock_qfq_factor(sym)
        kind = "EMPTY" if isinstance(r, str) else ("None" if r is None else f"{len(r)}行")
        print(f"  {sym}: {kind} {time.time()-t1:.1f}s")
    except Exception as e:
        print(f"  {sym}: FAIL {type(e).__name__}: {str(e)[:70]} {time.time()-t1:.1f}s")
print(f"[probe-v2] ① baostock 3票总耗时 {time.time()-bt0:.1f}s → 吞吐≈{3/((time.time()-bt0)/60):.1f}票/分")

print("\n[probe-v2] ② sina↔baostock 对拍 (8样本):")
print("[code | sina | baost | 公共日 | 最大偏差 | baost过闸? | top偏差日]")
n_pass = n_total = 0
for sym in SAMPLE:
    try:
        fs = m._run_with_timeout(lambda s=sym: m.fetch_sina_qfq(s), 15.0)
    except Exception:
        fs = None
    try:
        fb = m.fetch_baostock_qfq_factor(sym)
    except Exception as e:
        print(f"  {sym} baostock异常: {type(e).__name__}: {str(e)[:60]}")
        fb = None
    s_state = "None" if fs is None else ("EMPTY" if isinstance(fs, str) else f"{len(fs)}行")
    b_state = "None" if fb is None else ("EMPTY" if isinstance(fb, str) else f"{len(fb)}行")
    ok_fs = fs is not None and not isinstance(fs, str) and len(fs) > 0
    ok_fb = fb is not None and not isinstance(fb, str) and len(fb) > 0
    if not (ok_fs and ok_fb):
        print(f"{sym} | {s_state} | {b_state} | 不可比")
        continue
    n_total += 1
    common = fs.index.intersection(fb.index)
    if len(common) == 0:
        print(f"{sym} | {s_state} | {b_state} | 0公共日")
        continue
    a, b = fs.loc[common, "factor"], fb.loc[common, "factor"]
    dev = float((a / b - 1).abs().max())
    gate = m._qfq_continuity_ok(sym, fb)
    n_pass += 1 if gate else 0
    devser = (a / b - 1).abs().sort_values(ascending=False)[:2]
    tops = ", ".join(f"{d.strftime('%Y-%m-%d')}:{v:.3%}" for d, v in devser.items())
    print(f"{sym} | {s_state} | {b_state} | {len(common)} | {dev:.4%} | "
          f"{'PASS' if gate else 'REJECT'} | {tops}")

print(f"\n[probe-v2] ② 汇总: 可比{n_total}只, baostock过闸{n_pass}只 "
      f"(过闸率={n_pass/max(n_total,1):.0%})")
print("[probe-v2] 完成(全程只读, 未写任何库)")
