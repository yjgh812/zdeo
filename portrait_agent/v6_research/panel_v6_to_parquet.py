# -*- coding: utf-8 -*-
"""panel_v6_full.pkl → panel_v6_full.parquet 列式派生缓存 (20260910).

背景: pkl 6.5GB 整载瞬时 commit >10GB, 本机低 commit 水位时 MemoryError(9/9 实锤,
当时 commit 仅剩 7.4GB). parquet 列式存储后, 研究侧可用列/日期投影加载, 内存可控:
    import pandas as pd
    q = pd.read_parquet(r'D:\\zcodesj\\portrait_agent\\cache\\panel_v6_full.parquet',
                        columns=['code','date','close','pos250'],
                        filters=[('date','=','20260909')])
边界:
  - 生产消费方(engine_a_shadow_scan / daily_portrait_update / combo_c_shadow 等)继续用
    panel_v6_full.pkl, 合同不变; 本文件只是研究侧只读派生缓存.
  - 重建: python portrait_agent/v6_research/panel_v6_to_parquet.py (幂等覆盖, 建议每日 panel 更新后跑;
    2026-09-25 线B直跑件迁入v6_research).
  - 转换需全量载入 pkl(瞬时 commit 峰值 ~15GB), 低水位时段勿跑.
"""
import os, sys, time, gc
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-09-25 迁入v6_research后, cache仍在父目录 portrait_agent/cache
CACHE = os.path.join(os.path.dirname(HERE), 'cache')
SRC = os.path.join(CACHE, 'panel_v6_full.pkl')
DST = os.path.join(CACHE, 'panel_v6_full.parquet')

t0 = time.time()
p = pd.read_pickle(SRC)
print(f'[{time.time()-t0:.0f}s] pkl 载入 {len(p)}行 x {len(p.columns)}列', flush=True)
anchor = p[(p['code'] == '600971') & (p['date'].astype(str) == '20260909')][
    ['code', 'date', 'close', 'pos250', 'ret20']].copy()
a1 = anchor.to_dict('records')
print('锚点(pkl):', a1, flush=True)

p.to_parquet(DST, engine='pyarrow', compression='zstd', index=False)
del p, anchor
gc.collect()
print(f'[{time.time()-t0:.0f}s] parquet 已写出({os.path.getsize(DST)/1e9:.2f}GB), 内存释放', flush=True)

q = pd.read_parquet(DST, columns=['code', 'date', 'close', 'pos250', 'ret20'])
a2 = q[(q['code'] == '600971') & (q['date'].astype(str) == '20260909')].to_dict('records')
print('锚点(parquet):', a2, flush=True)
ok = (len(a1) == len(a2) == 1) and all(
    abs(float(a1[0][k]) - float(a2[0][k])) < 1e-9 for k in ('close', 'pos250', 'ret20'))
print('锚点一致:', ok)
if not ok:
    sys.exit(2)
q2 = pd.read_parquet(DST, columns=['code', 'date'])
print(f'[{time.time()-t0:.0f}s] 验证: parquet {len(q2)}行, 日期 {q2["date"].min()}~{q2["date"].max()}, '
      f'code数 {q2["code"].nunique()}')
print(f'落盘 {DST}')
