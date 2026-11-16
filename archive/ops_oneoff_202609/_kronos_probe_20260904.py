# -*- coding: utf-8 -*-
"""Kronos 计时探针（不产判定数据）：GPU 批量预测 64 只真实股票的耗时测定，用于预注册规模设计"""
import os, sys, time
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attribution', 'kronos_vendor'))

import numpy as np
import pandas as pd
import torch
import pyarrow.parquet as pq
import pyarrow as pa

DATA_PATH = r'D:/workbuddyspace/workspaces/W3_Backtest_V373/v375_dataset/highwin_v375_hd5_v3_qfq.parquet'
COLS = ['code', 'date', 'open', 'high', 'low', 'close', 'volume']
LOOKBACK = 400
END_DATE = 20260831

print('[1] 载入 parquet OHLCV...')
t0 = time.time()
tbl = pq.read_table(DATA_PATH, columns=COLS)
df = tbl.to_pandas(split_blocks=True, self_destruct=True)
df = df[(df['date'] >= 20150101) & (df['date'] <= END_DATE)]
print(f'    {len(df)} rows, {df.code.nunique()} stocks, {time.time()-t0:.0f}s')

print('[2] 构建上下文（末400根干净bar）...')
ctx = []
codes = sorted(df['code'].unique())
for c in codes:
    g = df[df['code'] == c].sort_values('date').tail(LOOKBACK)
    if len(g) < LOOKBACK:
        continue
    x = g[['open', 'high', 'low', 'close', 'volume']]
    if x.isnull().values.any():
        continue
    ctx.append((c, g['date'].reset_index(drop=True), x.reset_index(drop=True)))
    if len(ctx) >= 64:
        break
print(f'    合格上下文 {len(ctx)} 只')

print('[3] 加载模型 GPU...')
from model import Kronos, KronosTokenizer, KronosPredictor
torch.manual_seed(42)
tokenizer = KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base')
model = Kronos.from_pretrained('NeoQuasar/Kronos-base')
predictor = KronosPredictor(model, tokenizer, device='cuda:0', max_context=512)
print('    loaded')

df_list = [x for _, _, x in ctx]
x_ts = [pd.to_datetime(d.astype(str)) for _, d, _ in ctx]
y_ts = [pd.Series(pd.bdate_range(start=pd.to_datetime(str(d.iloc[-1])) + pd.Timedelta(days=1), periods=6)) for _, d, _ in ctx]

print('[4] 计时 predict_batch 64只 pred_len=6...')
for trial in range(2):
    torch.manual_seed(42)
    t = time.time()
    out = predictor.predict_batch(df_list, x_ts, y_ts, pred_len=6, T=1.0, top_p=0.9, sample_count=1, verbose=False)
    dt = time.time() - t
    print(f'    trial{trial}: {dt:.1f}s → {dt/len(df_list)*1000:.0f} ms/只 | 5日预测收益示例={out[0]["close"].iloc[4]/df_list[0]["close"].iloc[-1]-1:+.4f}')
print('[5] GPU mem:', round(torch.cuda.max_memory_allocated()/1e9, 2), 'GB')
print(f'[6] 外推: 5300只/月末截面 ≈ {5300*(dt/64)/60:.0f} 分钟；32截面 ≈ {5300*(dt/64)*32/3600:.1f} 小时')
