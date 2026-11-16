# -*- coding: utf-8 -*-
"""排序目标函数 stage-2：未触摸确认窗 2022-01~2023-12（PREREG: reports/PREREG_RANKLOSS_S2_20260904.md）

框架与 stage-1 逐字同框，仅窗口不同。两臂共享同一切片（一次读盘双臂训练）：
  A0 = 二分类 label_combined（在产参数 seed=42，同 regime_adapt A0）
  C1 = L2 回归 on 拟合窗 1%/99% winsorized future_return（同 rankloss_stage1 C1）
主判定 = 确认窗 24 月配对 ΔC1−A0；2018-2021 为永久 holdout 不动。
研究层零接线。指纹绑定 fail-closed。

用法：
  python attribution/rankloss_s2_power_20260904.py --calib    # 单月校准，不写结果
  python attribution/rankloss_s2_power_20260904.py --run      # 正式 2臂×24月（可续跑+退避）
  python attribution/rankloss_s2_power_20260904.py --analyze  # 合并三源判定
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
import lightgbm as lgb
import pyarrow.parquet as pq

PREREG_PATH = Path('reports/PREREG_RANKLOSS_S2_20260904.md')
PREREG_MD5 = '7d4b91cf4139ce46c3283bc8f6ff1f01'
S1_C1_JSONL = Path('attribution/rankloss_stage1_results.jsonl')
S1_A0_JSONL = Path('attribution/regime_adapt_stage1_results.jsonl')
RESULTS_JSONL = Path('attribution/rankloss_s2_results.jsonl')
LOG_PATH = Path('attribution/rankloss_s2_power_20260904.log')
DATA_PATH = r'D:/workbuddyspace/workspaces/W3_Backtest_V373/v375_dataset/highwin_v375_hd5_v3_qfq.parquet'

EXCLUDE = {'code', 'date', 'close', 'label', 'future_return', 'entry_open',
           'max_return_5d', 'min_return_5d', 'label_max5', 'label_stop', 'label_combined'}
S2_MONTHS = [f'{y}{m:02d}' for y in (2022, 2023) for m in range(1, 13)]
BATCH_LOG = 1
SEED = 42
LGB_PARAMS = dict(
    learning_rate=0.02, num_leaves=63, max_depth=7, min_data_in_leaf=50,
    feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
    lambda_l1=0.1, lambda_l2=1.0, verbose=-1, n_jobs=8, seed=SEED,
)


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


def period(ym):
    return pd.Period(str(ym), 'M')


def month_end_int(ym):
    return int(period(ym).end_time.strftime('%Y%m%d'))


def month_start_int(ym):
    return int(f'{ym}01')


def shift_month(ym, k):
    return (period(ym) + k).strftime('%Y%m')


def read_slice(lo=None, hi=None):
    import pyarrow as pa
    filt = []
    if lo is not None:
        filt.append(('date', '>=', lo))
    if hi is not None:
        filt.append(('date', '<=', hi))
    tbl = pq.read_table(DATA_PATH, columns=None, filters=filt)
    feats = [c for c in tbl.schema.names if c not in EXCLUDE]
    date = np.asarray(tbl.column('date').to_numpy(), dtype=np.int64)
    fut = np.asarray(tbl.column('future_return').cast(pa.float32()).to_numpy(), dtype=np.float32)
    lab = np.asarray(tbl.column('label_combined').cast(pa.float32()).to_numpy(), dtype=np.float32)
    sub = tbl.select(feats)
    del tbl
    schema32 = pa.schema([(f.name, pa.float32()) for f in sub.schema])
    F = np.ascontiguousarray(sub.cast(schema32).to_pandas(split_blocks=True, self_destruct=True).to_numpy(dtype=np.float32))
    del sub
    return F, date, fut, lab


def feat_stats(F_fit):
    n, p = F_fit.shape
    med = np.empty(p, dtype=np.float32)
    q_lo = np.empty(p, dtype=np.float32)
    q_hi = np.empty(p, dtype=np.float32)
    for j in range(p):
        col = F_fit[:, j]
        col = col[~np.isinf(col)]
        col = col[~np.isnan(col)]
        if len(col) == 0:
            med[j] = q_lo[j] = q_hi[j] = 0.0
            continue
        med[j] = np.median(col)
        q_lo[j], q_hi[j] = np.quantile(col, [0.001, 0.999])
    return med, q_lo, q_hi


def transform_inplace(X, med, q_lo, q_hi):
    X[np.isinf(X)] = np.nan
    bad = np.isnan(X)
    if bad.any():
        cols = np.where(bad)[1]
        X[bad] = med[cols]
    np.clip(X, q_lo, q_hi, out=X)
    return X


def oos_metrics(pred, fut, lab):
    from sklearn.metrics import roc_auc_score
    out = {}
    s_pred, s_fut = pd.Series(pred), pd.Series(fut)
    ok = s_fut.notna().to_numpy()
    out['rankic'] = float(s_pred[ok].corr(s_fut[ok], method='spearman'))
    lab_ok = ok & ~np.isnan(lab)
    try:
        out['auc_vs_label'] = float(roc_auc_score(lab[lab_ok], pred[lab_ok]))
    except ValueError:
        out['auc_vs_label'] = None
    return out


def run_month(ym, write=True):
    """一次读盘双臂训练：A0 二分类 + C1 回归，同切片同变换"""
    import pyarrow as pa
    m3, m2 = shift_month(ym, -3), shift_month(ym, -2)
    t_all = time.time()

    F_fit, d_fit, fut_fit, lab_fit = read_slice(None, month_end_int(m3))
    med, qlo, qhi = feat_stats(F_fit)
    X_fit = transform_inplace(F_fit, med, qlo, qhi)
    del F_fit
    gc.collect()

    F_val, _, fut_val, lab_val = read_slice(month_start_int(m2), month_end_int(m2))
    X_val = transform_inplace(F_val, med, qlo, qhi)
    del F_val
    gc.collect()

    F_oos, _, fut_oos, lab_oos = read_slice(month_start_int(ym), month_end_int(ym))
    X_oos = transform_inplace(F_oos, med, qlo, qhi)
    del F_oos
    gc.collect()

    recs = {}
    # C1：回归，目标 winsorize（拟合窗 1%/99%），NaN 目标行剔除
    ok_fit = ~np.isnan(fut_fit)
    t_lo, t_hi = np.nanquantile(fut_fit, [0.01, 0.99])
    y_c1_fit = np.clip(fut_fit[ok_fit].astype(np.float64), t_lo, t_hi).astype(np.float32)
    ok_val = ~np.isnan(fut_val)
    y_c1_val = np.clip(fut_val[ok_val].astype(np.float64), t_lo, t_hi).astype(np.float32)
    t0 = time.time()
    booster = lgb.train({**LGB_PARAMS, 'objective': 'regression', 'metric': 'l2'},
                        lgb.Dataset(X_fit[ok_fit], label=y_c1_fit), num_boost_round=2000,
                        valid_sets=[lgb.Dataset(X_val[ok_val], label=y_c1_val)],
                        callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)])
    t_c1 = time.time() - t0
    pred = booster.predict(X_oos)
    recs['C1'] = dict(arm='C1', month=ym, rounds=booster.current_iteration(),
                      train_seconds=round(t_c1, 1), **oos_metrics(pred, fut_oos, lab_oos))
    del booster, pred, y_c1_fit, y_c1_val, ok_fit, ok_val
    gc.collect()

    # A0：二分类 label_combined
    okf = ~np.isnan(lab_fit)
    okv = ~np.isnan(lab_val)
    t0 = time.time()
    booster = lgb.train({**LGB_PARAMS, 'objective': 'binary', 'metric': 'auc'},
                        lgb.Dataset(X_fit[okf], label=lab_fit[okf].astype(int)), num_boost_round=2000,
                        valid_sets=[lgb.Dataset(X_val[okv], label=lab_val[okv].astype(int))],
                        callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)])
    t_a0 = time.time() - t0
    pred = booster.predict(X_oos)
    recs['A0'] = dict(arm='A0', month=ym, rounds=booster.current_iteration(),
                      train_seconds=round(t_a0, 1), **oos_metrics(pred, fut_oos, lab_oos))
    del booster, pred, X_fit, X_val, X_oos
    gc.collect()

    if write:
        with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
            for arm in ('A0', 'C1'):
                f.write(json.dumps(recs[arm], ensure_ascii=False) + '\n')
            f.flush()
    log(f"{ym}: A0 rankic={recs['A0']['rankic']:+.4f} | C1 rankic={recs['C1']['rankic']:+.4f} "
        f"| Δ={recs['C1']['rankic']-recs['A0']['rankic']:+.4f} | train A0={t_a0:.0f}s C1={t_c1:.0f}s "
        f"| total={time.time()-t_all:.0f}s")
    return recs


def load_done():
    done = set()
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding='utf-8').splitlines():
            try:
                done.add(json.loads(line)['month'])
            except Exception:
                continue
    return done


def run():
    for ym in S2_MONTHS:
        if ym in load_done():
            continue
        run_month(ym)
    log('确认窗全部完成')


def run_with_backoff():
    from pyarrow.lib import ArrowMemoryError
    import torch
    oom = (MemoryError, ArrowMemoryError, torch.cuda.OutOfMemoryError)
    for attempt in range(30):
        try:
            run()
            return
        except oom as e:
            log(f'⚠️ 内存争用(第{attempt+1}次): {type(e).__name__} → 退避120s续跑')
            time.sleep(120)
    log('❌ 退避30次仍失败，中止（已完成月保留，--run 可续）')
    sys.exit(1)


def analyze():
    import scipy.stats as st

    def load(path, arm_filter=None):
        out = {}
        if Path(path).exists():
            for line in Path(path).read_text(encoding='utf-8').splitlines():
                r = json.loads(line)
                if arm_filter is None or r['arm'] == arm_filter:
                    out[r['month']] = r['rankic']
        return out

    a0 = {**load(S1_A0_JSONL, 'A0'), **load(RESULTS_JSONL, 'A0')}
    c1 = {**load(S1_C1_JSONL, 'C1'), **load(RESULTS_JSONL, 'C1')}
    confirm = [m for m in S2_MONTHS if m in a0 and m in c1]
    print(f'确认窗完成月数: {len(confirm)}/24 | 合并总月数: {len(set(a0) & set(c1))}')
    if not confirm:
        return
    print('\n=== 确认窗月度 RankIC（主判定） ===')
    for m in confirm:
        print(f'{m}  A0={a0[m]:+.4f}  C1={c1[m]:+.4f}  Δ={c1[m]-a0[m]:+.4f}')
    d = np.array([c1[m] - a0[m] for m in confirm])
    tt = st.ttest_rel([c1[m] for m in confirm], [a0[m] for m in confirm]).statistic
    print(f'\n确认窗配对 ΔC1−A0: n={len(d)} mean={d.mean():+.4f} t={tt:+.2f} 正占比={(d > 0).mean() * 100:.0f}%')
    allm = sorted(set(a0) & set(c1))
    dall = np.array([c1[m] - a0[m] for m in allm])
    tall = st.ttest_rel([c1[m] for m in allm], [a0[m] for m in allm]).statistic
    print(f'合并 {len(allm)} 月参考(非判定): meanΔ={dall.mean():+.4f} t={tall:+.2f}')
    for y in ('2022', '2023', '2024', '2025', '2026'):
        dy = [c1[m] - a0[m] for m in allm if m.startswith(y)]
        if dy:
            print(f'  {y}: meanΔ={np.mean(dy):+.4f} (n={len(dy)})')
    gate = (d.mean() >= 0.005) and (tt >= 2.0) and (dall.mean() >= 0)
    print(f'\n闸门初判(以报告为准): 确认窗 mean={d.mean():+.4f} t={tt:+.2f} 合并mean={dall.mean():+.4f} '
          f'→ {"PASS方向" if gate else "FAIL"}')


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
        t0 = time.time()
        recs = run_month('202212', write=False)
        log(f"[CALIB] 202212 合计={time.time()-t0:.0f}s A0={recs['A0']['rankic']:+.4f} "
            f"C1={recs['C1']['rankic']:+.4f}(仅校准展示,不入结果)")
        return
    run_with_backoff()


if __name__ == '__main__':
    main()
