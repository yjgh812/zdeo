# -*- coding: utf-8 -*-
"""Kronos 零样本实时前 walk（PREREG: reports/PREREG_KRONOS_FWALK_20260905.md, 指纹 e41ffc46dd7dfcd22556231ff2173e01）

协议本质: 每月末收盘后用当时完整数据做零样本预测, **先于未来实现**落盘留痕(append-only+墙钟ts);
T+5 标签自然完成后回填评分。禁止重跑 20260930 之前的历史月(已被 stage-1 消耗)。
配置与 stage-1 逐字一致(官方示例, seed=42, 子批64); 研究层零接线。

用法:
  python attribution/kronos_fwalk_20260905.py --predict [--date YYYYMMDD]  # 月末预测落盘(幂等,同日已存则跳过)
  python attribution/kronos_fwalk_20260905.py --score                      # 标签已完整的月回填 RankIC
  python attribution/kronos_fwalk_20260905.py --analyze                    # 检查点判定(n=12/18/24, ICIR>=0.5)
  python attribution/kronos_fwalk_20260905.py --calib --date D             # 校准运行, 不落盘
"""
import argparse
import gc
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# schtasks 非交互控制台为 GBK: 打印 emoji/中文必须强制 UTF-8(否则 UnicodeEncodeError 崩溃)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PREREG_PATH = Path('reports/PREREG_KRONOS_FWALK_20260905.md')
PREREG_MD5 = 'e41ffc46dd7dfcd22556231ff2173e01'
PRED_JSONL = Path('attribution/kronos_fwalk_predictions.jsonl')
SCORE_JSONL = Path('attribution/kronos_fwalk_scores.jsonl')
LOG_PATH = Path('attribution/kronos_fwalk_20260905.log')
STAGE1 = Path(__file__).resolve().parent / 'kronos_zs_stage1_20260904.py'
FIRST_FWALK_DATE = 20260930
COMPLETENESS = 0.95  # 标签非NaN行占比阈值(达标才可评分)
BATCH = 64


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


def load_stage1():
    """复用 stage-1 的载入/截面/预测机制(逐字同配置, 不复制代码防漂移)"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('kronos_zs_stage1', str(STAGE1))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_jsonl(path):
    out = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def append_line(path, obj):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')
        f.flush()


def predict_date(mod, d_end, store=True):
    done = {r['date'] for r in read_jsonl(PRED_JSONL)}
    if d_end in done:
        log(f'{d_end} 已有预测记录, 幂等跳过')
        return None
    import torch
    predictor = mod.load_predictor()
    t0 = time.time()
    codes, df_list, x_ts, y_ts, last_close, fut = mod.build_cross_section(d_end)
    if len(codes) < 100:
        log(f'❌ {d_end} 截面股票数 {len(codes)} 异常 fail-closed 中止')
        sys.exit(1)
    sig = np.full(len(codes), np.nan)
    torch.manual_seed(mod.SEED)
    for i in range(0, len(codes), mod.BATCH):
        out = predictor.predict_batch(df_list[i:i + mod.BATCH], x_ts[i:i + mod.BATCH],
                                      y_ts[i:i + mod.BATCH], pred_len=mod.PRED_LEN,
                                      T=1.0, top_p=0.9, sample_count=1, verbose=False)
        for j, o in enumerate(out):
            sig[i + j] = o['close'].iloc[4] / last_close[i + j] - 1.0
    infer = time.time() - t0
    ok = ~np.isnan(sig)
    rec = {'ts': datetime.now().isoformat(timespec='seconds'), 'date': d_end,
           'n': int(ok.sum()), 'n_raw': len(codes),
           'infer_seconds': round(infer, 1),
           'signals': {str(c).zfill(6): round(float(s), 6) for c, s in zip(codes, sig) if not np.isnan(s)}}
    if store:
        append_line(PRED_JSONL, rec)
    del predictor, df_list, x_ts, y_ts
    gc.collect()
    import torch as _t
    _t.cuda.empty_cache()
    log(f"predict {d_end}: n={rec['n']} {infer:.0f}s {'已落盘' if store else '(校准,未落盘)'}")
    return rec


def score_pending():
    """对预测日期逐一评分: 标签非NaN占比>=95% 才视为完整"""
    import pyarrow.parquet as pq
    preds = read_jsonl(PRED_JSONL)
    scored = {r['date'] for r in read_jsonl(SCORE_JSONL)}
    n_new = 0
    for rec in preds:
        d = rec['date']
        if d in scored:
            continue
        rows = pq.read_table(mod_data_path(), columns=['code', 'future_return'],
                             filters=[('date', '=', d)]).to_pandas()
        if len(rows) == 0:
            log(f'score {d}: canonical 无该日行, 跳过(等数据链)')
            continue
        comp = rows['future_return'].notna().mean()
        if comp < COMPLETENESS:
            log(f'score {d}: 标签完整度 {comp:.0%} < {COMPLETENESS:.0%}, 未到期/等数据, 跳过')
            continue
        fut = dict(zip(rows['code'].astype(str).str.zfill(6), rows['future_return']))
        pairs = [(c, float(fut[c])) for c, s in rec['signals'].items()
                 if str(c).zfill(6) in fut and not np.isnan(fut[str(c).zfill(6)])]
        if len(pairs) < 100:
            log(f'❌ score {d} 可配对数 {len(pairs)} 异常 fail-closed 中止')
            sys.exit(1)
        s = pd.Series({c: v for c, v in pairs})
        f = pd.Series({c: v for c, v in pairs})
        rankic = float(s.rank().corr(f.rank()))
        append_line(SCORE_JSONL, {'date': d, 'pred_ts': rec['ts'],
                                  'score_ts': datetime.now().isoformat(timespec='seconds'),
                                  'n': len(pairs), 'rankic': rankic})
        log(f'score {d}: n={len(pairs)} rankic={rankic:+.4f}')
        n_new += 1
    if n_new == 0:
        log('无新可评分月')
    return n_new


def mod_data_path():
    import importlib.util
    spec = importlib.util.spec_from_file_location('kronos_zs_stage1', str(STAGE1))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DATA_PATH


def analyze():
    import scipy.stats as st
    scores = read_jsonl(SCORE_JSONL)
    if not scores:
        print('无评分记录')
        return
    s = pd.Series({r['date']: r['rankic'] for r in scores}).sort_index()
    for d, v in s.items():
        print(f'{d}  rankic={v:+.4f}')
    n = len(s)
    mean, std = s.mean(), s.std(ddof=1)
    t = st.ttest_1samp(s.values, 0).statistic if n > 1 else float('nan')
    icir = mean / std if std > 0 else float('nan')
    print(f'\n已评分月: n={n} mean={mean:+.4f} t={t:+.2f} ICIR={icir:+.2f} 正占比={(s > 0).mean() * 100:.0f}%')
    if n < 12:
        print(f'检查点未到(n<12): 仅中期观测, 不出判定')
        return
    gate = (mean >= 0.02) and (t >= 2.0) and (icir >= 0.5)
    stop = (mean < 0) or (icir < 0.25)
    if n == 12 and stop:
        print(f'n=12 触发提前停止条款(mean<0 或 ICIR<0.25) → 协议关闭登记')
        return
    print(f'检查点判定: {"PASS方向(以报告为准)" if gate else "未达"} (闸门 mean>=0.02 且 t>=2 且 ICIR>=0.5)')


def latest_fwalk_date():
    """≥20260930 的最近已完成月末交易日(从 canonical 实测)"""
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    t = pq.read_table(mod_data_path(), columns=['date'])
    dates = np.unique(np.asarray(t.column('date').to_numpy(), dtype=np.int64))
    dates = dates[dates >= FIRST_FWALK_DATE]
    if len(dates) == 0:
        return None
    last_month = int(str(dates.max())[:6])
    prev_end = (pd.Period(f'{last_month}01', 'M')).end_time.strftime('%Y%m%d')
    cand = dates[dates <= int(prev_end)]
    return int(cand.max()) if len(cand) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--predict', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--analyze', action='store_true')
    ap.add_argument('--calib', action='store_true', help='校准运行, 不落盘')
    ap.add_argument('--date', default='', help='指定月末交易日 YYYYMMDD')
    a = ap.parse_args()
    if a.analyze:
        analyze()
        return
    check_prereg()
    mod = load_stage1()
    if a.score:
        score_pending()
        if not a.predict:
            return
    d = int(a.date) if a.date else latest_fwalk_date()
    # 协议起始守卫仅约束落盘路径(防历史月污染前walk); --calib 为机制校验, 不落盘不受限
    if a.calib:
        if not d:
            print('❌ --calib 需 --date YYYYMMDD')
            return
        t0 = time.time()
        rec = predict_date(mod, d, store=False)
        log(f"[CALIB] {d} 合计={time.time()-t0:.0f}s n={rec['n']} "
            f"signal均值={np.nanmean(list(rec['signals'].values())):+.5f}(仅校准展示)")
        return
    if d is None or d < FIRST_FWALK_DATE:
        print(f'❌ 无 ≥{FIRST_FWALK_DATE} 的可预测月末(协议起始月 2026-09-30), 现阶段无需运行')
        return
    from pyarrow.lib import ArrowMemoryError
    import torch
    for attempt in range(3):
        try:
            predict_date(mod, d, store=True)
            return
        except (MemoryError, ArrowMemoryError, torch.cuda.OutOfMemoryError) as e:
            log(f'⚠️ 内存/显存争用(第{attempt+1}次): {type(e).__name__} → 退避120s重试(幂等,未落盘)')
            time.sleep(120)
    log('❌ 退避3次仍失败, 本月末未预测(下月运行前须先补测该月末, 谨守连续两月未跑即作废条款)')


if __name__ == '__main__':
    main()
