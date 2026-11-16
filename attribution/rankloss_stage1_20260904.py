# -*- coding: utf-8 -*-
"""排序目标函数 stage-1：回归(C1) vs 在产二分类(A0)（PREREG: reports/PREREG_RANKLOSS_STAGE1_20260904.md）

框架与 REGIME-ADAPT-STAGE1 完全同框（窗口/embargo/变换/种子/参数），仅目标函数不同：
  A0 = 复用 attribution/regime_adapt_stage1_results.jsonl 的 A0 臂（二分类，不重跑）
  C1 = objective='regression'(L2)，目标=future_return 拟合窗 1%/99% winsorize
排除臂（宪法 §10-8，见 PREREG §一）：lambdarank（8/28 v23 冒烟方向证伪）、pairwise（同族负先验）。
研究层零接线。指纹绑定 fail-closed。

架构：逐折流式读盘（pyarrow 谓词下推），峰值 ≈5GB（全量常驻版曾在晨间 cron 内存争用下
ArrowMemoryError），配合逐折 JSONL 可续跑；MemoryError 自动退避重试。

用法：
  python attribution/rankloss_stage1_20260904.py --calib   # 单折计时校准，不写结果
  python attribution/rankloss_stage1_20260904.py --run     # 正式 C1 32 折（可续跑+自动退避）
  python attribution/rankloss_stage1_20260904.py --analyze # 合并 A0+C1 配对分析
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
import pyarrow as pa
import pyarrow.dataset as pads

PREREG_PATH = Path('reports/PREREG_RANKLOSS_STAGE1_20260904.md')
PREREG_MD5 = '2847e6b283dfd0fab72f21be005da29c'
A0_JSONL = Path('attribution/regime_adapt_stage1_results.jsonl')
RESULTS_JSONL = Path('attribution/rankloss_stage1_results.jsonl')
LOG_PATH = Path('attribution/rankloss_stage1_20260904.log')
DATA_PATH = Path(r'D:/workbuddyspace/workspaces/W3_Backtest_V373/v375_dataset/highwin_v375_hd5_v3_qfq.parquet')

EXCLUDE = {'code', 'date', 'close', 'label', 'future_return', 'entry_open',
           'max_return_5d', 'min_return_5d', 'label_max5', 'label_stop', 'label_combined'}
OOS_MONTHS = [f'{y}{m:02d}' for y in (2024, 2025, 2026) for m in range(1, 13)
              if not (y == 2026 and m > 8)]
SEED = 42
LGB_PARAMS = dict(
    objective='regression', metric='l2', learning_rate=0.02,
    num_leaves=63, max_depth=7, min_data_in_leaf=50,
    feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
    lambda_l1=0.1, lambda_l2=1.0, verbose=-1, n_jobs=8, seed=SEED,
)
_DATASET = None


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


def get_dataset():
    global _DATASET
    if _DATASET is None:
        _DATASET = pads.dataset(str(DATA_PATH))
    return _DATASET


def read_slice(lo=None, hi=None):
    """按 date 区间流式读取（int64 谓词下推），返回 (F float32, date int64, fut float32, lab float32)"""
    filt = None
    if lo is not None:
        filt = pads.field('date') >= lo
    if hi is not None:
        filt = (pads.field('date') <= hi) if filt is None else filt & (pads.field('date') <= hi)
    tbl = get_dataset().to_table(filter=filt)
    feats = [c for c in tbl.schema.names if c not in EXCLUDE]
    date = np.asarray(tbl.column('date').to_numpy(), dtype=np.int64)
    fut = np.asarray(tbl.column('future_return').cast(pa.float32()).to_numpy(), dtype=np.float32)
    lab = np.asarray(tbl.column('label_combined').cast(pa.float32()).to_numpy(), dtype=np.float32)
    sub = tbl.select(feats)
    schema32 = pa.schema([(f.name, pa.float32()) for f in sub.schema])
    F = np.ascontiguousarray(sub.cast(schema32).to_pandas(split_blocks=True, self_destruct=True).to_numpy(dtype=np.float32))
    del tbl, sub
    return F, date, fut, lab


def feature_transform_params(F_fit):
    """逐列统计（中位数/0.1%-99.9% 分位）：单列副本 ~40MB，内存与总行数解耦"""
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
    thr = np.nanquantile(pred, 0.8)
    top = ok & (pred >= thr)
    fm = np.nanmean(fut[ok])
    out['topq_excess'] = float(np.nanmean(fut[top]) - fm) if top.any() else None
    return out


def train_fold(X_fit, y_fit, X_val, y_val):
    t0 = time.time()
    dtrain = lgb.Dataset(X_fit, label=y_fit)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)
    booster = lgb.train(LGB_PARAMS, dtrain, num_boost_round=2000, valid_sets=[dval],
                        callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)])
    return booster, time.time() - t0


def append_record(rec):
    with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        f.flush()


def load_done():
    done = set()
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                done.add(r['month'])
            except Exception:
                continue
    return done


def run_fold(ym, write=True):
    m3, m2 = shift_month(ym, -3), shift_month(ym, -2)
    t_all = time.time()

    # 拟合窗流式读取 → 目标掩码先裁 → 特征变换原位
    F_fit, d_fit, fut_fit, _ = read_slice(None, month_end_int(m3))
    ok_fit = ~np.isnan(fut_fit)
    t_lo, t_hi = np.nanquantile(fut_fit, [0.01, 0.99])
    n_fit_raw = int(len(F_fit))
    F_fit = F_fit[ok_fit]
    med, qlo, qhi = feature_transform_params(F_fit)
    X_fit = transform_inplace(F_fit, med, qlo, qhi)
    y_fit = np.clip(fut_fit[ok_fit].astype(np.float64), t_lo, t_hi).astype(np.float32)
    fut_fit = None
    gc.collect()

    # 验证月（早停）流式读取
    F_val, _, fut_val, _ = read_slice(month_start_int(m2), month_end_int(m2))
    ok_val = ~np.isnan(fut_val)
    F_val = F_val[ok_val]
    X_val = transform_inplace(F_val, med, qlo, qhi)
    y_val = np.clip(fut_val[ok_val].astype(np.float64), t_lo, t_hi).astype(np.float32)
    F_val = fut_val = None
    gc.collect()

    if not (len(y_fit) and len(y_val)):
        log(f'❌ {ym} 目标全空 fail-closed 中止')
        sys.exit(1)

    booster, t_train = train_fold(X_fit, y_fit, X_val, y_val)
    del X_fit
    gc.collect()

    # OOS 月流式读取 → 用拟合窗统计量变换（无泄漏）→ 指标
    F_oos, _, fut_oos, lab_oos = read_slice(month_start_int(ym), month_end_int(ym))
    X_oos = transform_inplace(F_oos, med, qlo, qhi)
    F_oos = None
    gc.collect()
    pred = booster.predict(X_oos)
    mets = oos_metrics(pred, fut_oos, lab_oos)
    rec = dict(arm='C1', month=ym, n_fit=n_fit_raw, n_train=int(len(y_fit)),
               n_val=int(len(y_val)), n_oos=int(len(pred)),
               clip_lo=float(t_lo), clip_hi=float(t_hi),
               rounds=booster.current_iteration(),
               train_seconds=round(t_train, 1), total_seconds=round(time.time() - t_all, 1),
               **mets)
    if write:
        append_record(rec)
    del booster, X_val, X_oos, pred
    gc.collect()
    return rec


def run():
    for ym in OOS_MONTHS:
        if ym in load_done():
            continue
        rec = run_fold(ym)
        log(f"C1 {ym}: rankic={rec['rankic']:+.4f} auc={rec['auc_vs_label']} topq={rec['topq_excess']} "
            f"rounds={rec['rounds']} n_train={rec['n_train']} train={rec['train_seconds']}s "
            f"total={rec['total_seconds']}s clip=[{rec['clip_lo']:.4f},{rec['clip_hi']:.4f}]")
    log('C1 全部完成')


def run_with_backoff():
    """晨间 cron 内存争用容错：MemoryError/ArrowMemoryError 退避 120s 后续跑（JSONL 跳过已完成折）"""
    from pyarrow.lib import ArrowMemoryError
    for attempt in range(30):
        try:
            run()
            return
        except (MemoryError, ArrowMemoryError) as e:
            log(f'⚠️ 内存争用(第{attempt+1}次): {type(e).__name__} → 退避120s后续跑（已完成折保留）')
            time.sleep(120)
    log('❌ 退避30次仍失败，中止（JSONL 已完成折保留，可直接 --run 续跑）')
    sys.exit(1)


def analyze():
    import scipy.stats as st
    a0 = {}
    for line in A0_JSONL.read_text(encoding='utf-8').splitlines():
        r = json.loads(line)
        if r['arm'] == 'A0':
            a0[r['month']] = r['rankic']
    c1 = {}
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            c1[r['month']] = r['rankic']
    print(f'A0 折数 {len(a0)} | C1 折数 {len(c1)}')
    common = sorted(set(a0) & set(c1))
    if not common:
        return
    print('\n=== 月度 RankIC ===')
    for m in common:
        print(f'{m}  A0={a0[m]:+.4f}  C1={c1[m]:+.4f}  Δ={c1[m]-a0[m]:+.4f}')
    d = np.array([c1[m] - a0[m] for m in common])
    tt = st.ttest_rel([c1[m] for m in common], [a0[m] for m in common]).statistic if len(d) > 1 else float('nan')
    print(f'\n配对 ΔC1-A0: n={len(d)} mean={d.mean():+.4f} t={tt:+.2f}')
    for y in ('2024', '2025', '2026'):
        dy = [c1[m] - a0[m] for m in common if m.startswith(y)]
        if dy:
            print(f'  {y}: mean={np.mean(dy):+.4f} (n={len(dy)})')


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
        rec = run_fold('202608', write=False)
        log(f"[CALIB] C1 202608 合计={time.time()-t0:.0f}s 训练={rec['train_seconds']}s "
            f"rounds={rec['rounds']} rankic={rec['rankic']:+.4f}(仅校准展示,不入结果)")
        return
    run_with_backoff()


if __name__ == '__main__':
    main()
