# -*- coding: utf-8 -*-
"""Kronos 零样本 K 线预测 A 股截面区分度 stage-1（PREREG: reports/PREREG_KRONOS_ZS_STAGE1_20260904.md）

零样本（无微调）官方示例配置：Kronos-base + Tokenizer-base, LOOKBACK=400, pred_len=6,
T=1.0, top_p=0.9, sample_count=1, seed=42, 子批256。32 个月末截面全市场。
信号 = pred_close[4]/last_close - 1（第5根预测bar≈T+5）；标签 = canonical 行 future_return。
研究层零接线。指纹绑定 fail-closed。

用法：
  python attribution/kronos_zs_stage1_20260904.py --calib    # 单截面校准(20260831)，不写结果
  python attribution/kronos_zs_stage1_20260904.py --run      # 正式 32 截面（可续跑+退避）
  python attribution/kronos_zs_stage1_20260904.py --analyze  # 汇总闸门判定
"""
import argparse
import gc
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PREREG_PATH = Path('reports/PREREG_KRONOS_ZS_STAGE1_20260904.md')
PREREG_MD5 = '2b3c68a5b9da87831ed0c7c634b7b7e7'
RESULTS_JSONL = Path('attribution/kronos_zs_stage1_results.jsonl')
LOG_PATH = Path('attribution/kronos_zs_stage1_20260904.log')
DATA_PATH = r'D:/workbuddyspace/workspaces/W3_Backtest_V373/v375_dataset/highwin_v375_hd5_v3_qfq.parquet'
VENDOR = Path(__file__).resolve().parent / 'kronos_vendor'
LOOKBACK = 400
PRED_LEN = 6
BATCH = 64
SEED = 42
OOS_MONTHS = [f'{y}{m:02d}' for y in (2024, 2025, 2026) for m in range(1, 13)
              if not (y == 2026 and m > 8)]
COLS = ['code', 'date', 'open', 'high', 'low', 'close', 'volume', 'future_return']


def log(msg):
    line = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def check_prereg():
    if not PREREG_PATH.exists():
        print(f'❌ 预注册文件缺失: {PREREG_PATH}', flush=True)
        sys.exit(2)
    h = hashlib.md5(PREREG_PATH.read_bytes()).hexdigest()
    if h != PREREG_MD5:
        print(f'❌ 预注册指纹不匹配: 期望 {PREREG_MD5} 实际 {h}', flush=True)
        sys.exit(2)
    log(f'预注册指纹校验通过 {h}')


def load_predictor():
    import os
    os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
    sys.path.insert(0, str(VENDOR))
    import torch
    from model import Kronos, KronosTokenizer, KronosPredictor
    torch.manual_seed(SEED)
    tokenizer = KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base')
    model = Kronos.from_pretrained('NeoQuasar/Kronos-base')
    pred = KronosPredictor(model, tokenizer, device='cuda:0', max_context=512)
    log('模型加载完成 (cuda:0)')
    return pred


def month_end_dates():
    """32 个预测日 = 各 OOS 月前一月的最后一个交易日（从数据实测）"""
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    t = pq.read_table(DATA_PATH, columns=['date'])
    dates = np.unique(np.asarray(t.column('date').to_numpy(), dtype=np.int64))
    t = None
    out = []
    for m in OOS_MONTHS:
        prev_m = (pd.Period(m, 'M') - 1)
        hi = int(prev_m.end_time.strftime('%Y%m%d'))
        lo = int(prev_m.start_time.strftime('%Y%m%d'))
        cand = dates[(dates >= lo) & (dates <= hi)]
        if len(cand) == 0:
            log(f'❌ {prev_m} 无交易日 fail-closed 中止')
            sys.exit(1)
        out.append(int(cand.max()))
    return sorted(set(out))


def build_cross_section(d_end):
    """流式读窗口 → 逐股末400根干净bar → (codes, df_list, x_ts, y_ts, last_close, fut)"""
    import pyarrow.parquet as pq
    import pyarrow as pa
    d_lo = int((pd.Timestamp(str(d_end)) - pd.Timedelta(days=700)).strftime('%Y%m%d'))
    tbl = pq.read_table(DATA_PATH, columns=COLS,
                        filters=[('date', '>=', d_lo), ('date', '<=', d_end)])
    df = tbl.to_pandas(split_blocks=True, self_destruct=True)
    del tbl
    px = ['open', 'high', 'low', 'close', 'volume']
    codes, df_list, x_ts, y_ts, last_close, fut = [], [], [], [], [], []
    for c, g in df.groupby('code', sort=True):
        g = g.sort_values('date')
        if len(g) == 0 or int(g['date'].iloc[-1]) != d_end:
            continue
        w = g.tail(LOOKBACK + 100)
        w = w[~w[px].isnull().any(axis=1)].tail(LOOKBACK)
        if len(w) < LOOKBACK:
            continue
        fr = float(g['future_return'].iloc[-1])
        if np.isnan(fr):
            continue
        codes.append(str(c))
        df_list.append(w[px].reset_index(drop=True))
        x_ts.append(pd.to_datetime(w['date'].astype(str)).reset_index(drop=True))
        y_ts.append(pd.Series(pd.bdate_range(
            start=pd.Timestamp(str(d_end)) + pd.Timedelta(days=1), periods=PRED_LEN)))
        last_close.append(float(w['close'].iloc[-1]))
        fut.append(fr)
    del df
    gc.collect()
    return codes, df_list, x_ts, y_ts, np.asarray(last_close, dtype=np.float64), np.asarray(fut, dtype=np.float64)


def run_section(predictor, d_end, write=True):
    import torch
    t_all = time.time()
    codes, df_list, x_ts, y_ts, last_close, fut = build_cross_section(d_end)
    if len(codes) < 100:
        log(f'❌ {d_end} 截面股票数 {len(codes)} 异常 fail-closed 中止')
        sys.exit(1)
    t_prep = time.time() - t_all

    t_inf = time.time()
    sig = np.full(len(codes), np.nan)
    torch.manual_seed(SEED)
    for i in range(0, len(codes), BATCH):
        out = predictor.predict_batch(df_list[i:i + BATCH], x_ts[i:i + BATCH], y_ts[i:i + BATCH],
                                      pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=False)
        for j, o in enumerate(out):
            sig[i + j] = o['close'].iloc[4] / last_close[i + j] - 1.0
    t_inf = time.time() - t_inf
    del df_list, x_ts, y_ts
    gc.collect()
    torch.cuda.empty_cache()

    ok = ~np.isnan(sig) & ~np.isnan(fut)
    rankic = float(pd.Series(sig[ok]).corr(pd.Series(fut[ok]), method='spearman'))
    thr = np.nanquantile(sig[ok], 0.8)
    top = ok & (sig >= thr)
    rec = dict(arm='KRONOS-ZS', date=d_end, n_stocks=int(ok.sum()), n_raw=len(codes),
               rankic=rankic, mean_signal=float(np.nanmean(sig[ok])),
               topq_excess=float(np.nanmean(fut[top]) - np.nanmean(fut[ok])),
               prep_seconds=round(t_prep, 1), infer_seconds=round(t_inf, 1))
    if write:
        with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
    return rec


def load_done():
    done = set()
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding='utf-8').splitlines():
            try:
                done.add(json.loads(line)['date'])
            except Exception:
                continue
    return done


def run():
    predictor = load_predictor()
    dates = month_end_dates()
    log(f'共 {len(dates)} 个截面: {dates[0]}..{dates[-1]}')
    done = load_done()
    for d in dates:
        if d in done:
            continue
        rec = run_section(predictor, d)
        log(f"KRONOS-ZS {d}: n={rec['n_stocks']} rankic={rec['rankic']:+.4f} "
            f"topq={rec['topq_excess']:+.5f} prep={rec['prep_seconds']}s infer={rec['infer_seconds']}s")
    log('全部截面完成')


def run_with_backoff():
    from pyarrow.lib import ArrowMemoryError
    import torch
    oom = (MemoryError, ArrowMemoryError, torch.cuda.OutOfMemoryError)
    for attempt in range(30):
        try:
            run()
            return
        except oom as e:
            log(f'⚠️ 内存/显存争用(第{attempt+1}次): {type(e).__name__} → 退避120s续跑')
            time.sleep(120)
    log('❌ 退避30次仍失败，中止（已完成截面保留，--run 可续）')
    sys.exit(1)


def analyze():
    import scipy.stats as st
    if not RESULTS_JSONL.exists():
        print('无结果文件')
        return
    rows = [json.loads(l) for l in RESULTS_JSONL.read_text(encoding='utf-8').splitlines() if l.strip()]
    s = pd.Series({r['date']: r['rankic'] for r in rows}).sort_index()
    print(f'截面数 {len(s)}')
    for d, v in s.items():
        print(f'{d}  rankic={v:+.4f}')
    t = st.ttest_1samp(s.values, 0).statistic
    print(f'\nmean={s.mean():+.4f} t={t:+.2f} 正截面占比={(s > 0).mean() * 100:.0f}%')
    yr = {}
    for d, v in s.items():
        y = str(d)[:4]
        yr.setdefault(y, []).append(v)
    for y, v in sorted(yr.items()):
        print(f'  {y}: mean={np.mean(v):+.4f} (n={len(v)})')
    gate = s.mean() >= 0.02 and t >= 2.0
    print(f'\n闸门初判(以报告为准): mean={s.mean():+.4f} t={t:+.2f} → {"PASS方向" if gate else "未达"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--calib', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--analyze', action='store_true')
    a = ap.parse_args()
    if a.analyze:
        analyze()
        return
    check_prereg()
    if a.calib:
        predictor = load_predictor()
        t0 = time.time()
        rec = run_section(predictor, 20260831, write=False)
        log(f"[CALIB] 20260831 合计={time.time()-t0:.0f}s n={rec['n_stocks']} "
            f"rankic={rec['rankic']:+.4f}(仅校准展示,不入结果)")
        return
    run_with_backoff()


if __name__ == '__main__':
    main()
