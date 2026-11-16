# -*- coding: utf-8 -*-
"""Regime 自适应训练机制 stage-1（PREREG: reports/PREREG_REGIME_ADAPT_STAGE1_20260904.md）

三臂 A0(expanding 全窗) / B1(24 月短窗) / B2(warm-start 增量)，OOS 2024-01..2026-08。
研究层零接线：只读 canonical parquet，不写任何生产模型/配置目录。
指纹绑定 fail-closed：预注册 md5 不匹配即 exit 2。

用法：
  python attribution/regime_adapt_stage1_20260904.py --calib        # 单折计时校准，不写结果
  python attribution/regime_adapt_stage1_20260904.py --arm all      # 正式三臂（可续跑）
  python attribution/regime_adapt_stage1_20260904.py --arm B1       # 单臂
  python attribution/regime_adapt_stage1_20260904.py --analyze      # 汇总已完成的折
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
import pyarrow as pa

PREREG_PATH = Path('reports/PREREG_REGIME_ADAPT_STAGE1_20260904.md')
PREREG_MD5 = '9bffe28ecf33a49f60778acaa9e0f5ae'
DATA_PATH = Path(r'D:/workbuddyspace/workspaces/W3_Backtest_V373/v375_dataset/highwin_v375_hd5_v3_qfq.parquet')
RESULTS_JSONL = Path('attribution/regime_adapt_stage1_results.jsonl')
LOG_PATH = Path('attribution/regime_adapt_stage1_20260904.log')

EXCLUDE = {'code', 'date', 'close', 'label', 'future_return', 'entry_open',
           'max_return_5d', 'min_return_5d', 'label_max5', 'label_stop', 'label_combined'}
OOS_MONTHS = [f'{y}{m:02d}' for y in (2024, 2025, 2026) for m in range(1, 13)
              if not (y == 2026 and m > 8)]
ARMS = ('A0', 'B1', 'B2')
SEED = 42

LGB_PARAMS = dict(
    objective='binary', metric='auc', learning_rate=0.02,
    num_leaves=63, max_depth=7, min_data_in_leaf=50,
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
        print(f'❌ 预注册指纹不匹配: 期望 {PREREG_MD5} 实际 {h}（文件在指纹落定后被改动）', flush=True)
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


def load_master():
    """一次性载入为 float32 连续矩阵：features F + date/fut/label 辅助列（只读，不修改）"""
    log(f'载入 {DATA_PATH}（arrow → float32）...')
    t0 = time.time()
    tbl = pq.read_table(DATA_PATH)
    feats = [c for c in tbl.schema.names if c not in EXCLUDE]
    date = np.asarray(tbl.column('date').cast(pa.int64()).to_numpy(), dtype=np.int64)
    fut = np.asarray(tbl.column('future_return').cast(pa.float32()).to_numpy(), dtype=np.float32)
    lab = np.asarray(tbl.column('label_combined').cast(pa.float32()).to_numpy(), dtype=np.float32)
    sub = tbl.select(feats)
    schema32 = pa.schema([(f.name, pa.float32()) for f in sub.schema])
    F = np.ascontiguousarray(sub.cast(schema32).to_pandas(split_blocks=True, self_destruct=True).to_numpy(dtype=np.float32))
    del tbl, sub
    gc.collect()
    log(f'载入完成 {F.shape[0]} 行 × {F.shape[1]} 特征，耗时 {time.time()-t0:.0f}s')
    return F, date, fut, lab, feats


def slice_rows(F, date, mask):
    return F[mask].copy(), date[mask], mask


def transform_fit_stats(F_fit):
    """拟合窗统计量（中位数/0.1%-99.9% 分位），返回变换参数（仅拟合窗计算，防泄漏）"""
    med = np.nanmedian(np.where(np.isinf(F_fit), np.nan, F_fit), axis=0)
    with np.errstate(invalid='ignore'):
        q_lo, q_hi = np.nanquantile(np.where(np.isinf(F_fit), np.nan, F_fit), [0.001, 0.999], axis=0)
    return med.astype(np.float32), q_lo.astype(np.float32), q_hi.astype(np.float32)


def apply_transform(F_part, med, q_lo, q_hi):
    X = F_part.copy()
    X[np.isinf(X)] = np.nan
    bad = np.isnan(X)
    if bad.any():
        cols = np.where(bad)[1]
        X[bad] = med[cols]
    np.clip(X, q_lo, q_hi, out=X)
    return X


def train_fold(arm, X_fit, y_fit, X_val, y_val, prev_booster=None):
    t0 = time.time()
    dtrain = lgb.Dataset(X_fit, label=y_fit)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)
    if arm == 'B2' and prev_booster is not None:
        booster = lgb.train(LGB_PARAMS, dtrain, num_boost_round=300,
                            init_model=prev_booster, valid_sets=[dval],
                            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])
    else:
        booster = lgb.train(LGB_PARAMS, dtrain, num_boost_round=2000,
                            valid_sets=[dval],
                            callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)])
    return booster, time.time() - t0


def oos_metrics(pred, fut, lab):
    out = {}
    s_pred, s_fut = pd.Series(pred), pd.Series(fut)
    ok = s_fut.notna().to_numpy()
    out['rankic'] = float(s_pred[ok].corr(s_fut[ok], method='spearman'))
    lab_ok = ok & ~np.isnan(lab)
    from sklearn.metrics import roc_auc_score
    try:
        out['auc'] = float(roc_auc_score(lab[lab_ok], pred[lab_ok]))
    except ValueError:
        out['auc'] = None
    thr = np.nanquantile(pred, 0.8)
    top = ok & (pred >= thr)
    fm = np.nanmean(fut[ok])
    out['topq_excess'] = float(np.nanmean(fut[top]) - fm) if top.any() else None
    return out


def fold_masks(date, ym):
    m3 = shift_month(ym, -3)
    m2 = shift_month(ym, -2)
    m26 = shift_month(ym, -26)
    fit_a0 = date <= month_end_int(m3)
    fit_b1 = (date >= month_start_int(m26)) & (date <= month_end_int(m3))
    val_m = (date >= month_start_int(m2)) & (date <= month_end_int(m2))
    oos_m = (date >= month_start_int(ym)) & (date <= month_end_int(ym))
    return fit_a0, fit_b1, val_m, oos_m


def load_done():
    done = set()
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                done.add((r['arm'], r['month']))
            except Exception:
                continue
    return done


def purge_arm(arm, done):
    """warm-start 链是进程内状态，跨进程不可续：B2 启动即清空自身旧记录全链重跑（fail-closed 防状态断裂）"""
    if not RESULTS_JSONL.exists():
        return done
    kept = []
    for line in RESULTS_JSONL.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(line)
            if r['arm'] == arm:
                continue
        except Exception:
            pass
        kept.append(line)
    RESULTS_JSONL.write_text('\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8')
    if any(a == arm for a, _ in done):
        log(f'{arm} 旧记录已清空（状态链不可跨进程复用），自首月全链重跑')
    return {(a, m) for a, m in done if a != arm}


def append_record(rec):
    with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        f.flush()


def run_arm(arm, F, date, fut, lab, feats, calib=False):
    if calib:
        ym = '202608'
        fa0, fb1, vm, om = fold_masks(date, ym)
        fmask = fa0 if arm == 'A0' else fb1
        F_fit, _, fmask_r = slice_rows(F, date, fmask)
        F_val, _, _ = slice_rows(F, date, vm)
        F_oos, _, _ = slice_rows(F, date, om)
        log(f'[CALIB] {arm} {ym}: fit={F_fit.shape[0]} val={F_val.shape[0]} oos={F_oos.shape[0]}')
        t0 = time.time()
        med, qlo, qhi = transform_fit_stats(F_fit)
        t1 = time.time()
        X_fit = apply_transform(F_fit, med, qlo, qhi)
        del F_fit
        gc.collect()
        X_val = apply_transform(F_val, med, qlo, qhi)
        X_oos = apply_transform(F_oos, med, qlo, qhi)
        t2 = time.time()
        y_fit = lab[fmask_r]
        y_val = lab[vm]
        booster, t_train = train_fold(arm, X_fit, y_fit.astype(int), X_val, y_val.astype(int))
        del X_fit
        gc.collect()
        pred = booster.predict(X_oos)
        mets = oos_metrics(pred, fut[om], lab[om])
        log(f'[CALIB] {arm} {ym} 耗时: 统计={t1-t0:.0f}s 变换={t2-t1:.0f}s 训练={t_train:.0f}s 预测+指标={time.time()-t2-t_train:.0f}s'
            f' | rounds={booster.current_iteration()} rankic={mets["rankic"]:.4f}(仅校准展示,不入结果)')
        return
    done = load_done()
    if arm == 'B2':
        done = purge_arm('B2', done)
    prev = None
    for ym in OOS_MONTHS:
        if (arm, ym) in done:
            continue
        fa0, fb1, vm, om = fold_masks(date, ym)
        fmask = fa0 if arm == 'A0' else fb1
        if not fmask.any() or not vm.any() or not om.any():
            log(f'❌ {arm} {ym} 空窗 fail-closed 中止')
            sys.exit(1)
        F_fit, _, _ = slice_rows(F, date, fmask)
        F_val, _, _ = slice_rows(F, date, vm)
        F_oos, _, _ = slice_rows(F, date, om)
        med, qlo, qhi = transform_fit_stats(F_fit)
        X_fit = apply_transform(F_fit, med, qlo, qhi)
        del F_fit
        gc.collect()
        X_val = apply_transform(F_val, med, qlo, qhi)
        X_oos = apply_transform(F_oos, med, qlo, qhi)
        del F_val, F_oos
        y_fit = lab[fmask]
        y_val = lab[vm]
        if arm == 'B2' and prev is None:
            log(f'B2 首折 {ym} 全新训练（warm-start 链起点）')
        booster, t_train = train_fold(arm, X_fit, y_fit.astype(int), X_val, y_val.astype(int),
                                      prev_booster=prev if arm == 'B2' else None)
        del X_fit
        gc.collect()
        pred = booster.predict(X_oos)
        mets = oos_metrics(pred, fut[om], lab[om])
        rec = dict(arm=arm, month=ym, n_fit=int(fmask.sum()), n_val=int(vm.sum()),
                   n_oos=int(om.sum()), rounds=booster.current_iteration(),
                   train_seconds=round(t_train, 1), **mets)
        append_record(rec)
        log(f'{arm} {ym}: rankic={mets["rankic"]:+.4f} auc={mets["auc"]} topq={mets["topq_excess"]} '
            f'rounds={rec["rounds"]} fit={rec["n_fit"]} {t_train:.0f}s')
        if arm == 'B2':
            prev = booster
        del booster, X_val, X_oos, pred
        gc.collect()


def analyze():
    import scipy.stats as st
    if not RESULTS_JSONL.exists():
        print('无结果文件')
        return
    rows = [json.loads(l) for l in RESULTS_JSONL.read_text(encoding='utf-8').splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    if df.empty:
        print('结果为空')
        return
    piv = df.pivot(index='month', columns='arm', values='rankic')
    print('\n=== 各臂月度 RankIC ===')
    print(piv.round(4).to_string())
    print('\n=== 汇总 ===')
    for arm in ARMS:
        s = df[df.arm == arm]['rankic'].dropna()
        if len(s) == 0:
            continue
        t = st.ttest_1samp(s, 0).statistic if len(s) > 1 else float('nan')
        print(f'{arm}: n={len(s)} mean={s.mean():+.4f} t={t:+.2f}')
    for arm in ('B1', 'B2'):
        both = piv[[c for c in ('A0', arm) if c in piv.columns]].dropna()
        if len(both) < 5:
            continue
        d = both[arm] - both['A0']
        tt = st.ttest_rel(both[arm], both['A0']).statistic if len(d) > 1 else float('nan')
        print(f'\n配对 Δ{arm}-A0: n={len(d)} mean={d.mean():+.4f} t={tt:+.2f}')
        for y in ('2024', '2025', '2026'):
            dy = d[[m for m in d.index if m.startswith(y)]]
            if len(dy):
                print(f'  {y}: mean={dy.mean():+.4f} (n={len(dy)})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--calib', action='store_true')
    ap.add_argument('--arm', default='all', choices=['A0', 'B1', 'B2', 'all'])
    ap.add_argument('--analyze', action='store_true')
    a = ap.parse_args()
    if a.analyze:
        analyze()
        return
    check_prereg()
    F, date, fut, lab, feats = load_master()
    arms = list(ARMS) if a.arm == 'all' else [a.arm]
    if a.calib:
        for arm in arms:
            run_arm(arm, F, date, fut, lab, feats, calib=True)
        return
    for arm in arms:
        log(f'=== 臂 {arm} 开始 ===')
        run_arm(arm, F, date, fut, lab, feats)
    log('全部完成')


if __name__ == '__main__':
    main()
