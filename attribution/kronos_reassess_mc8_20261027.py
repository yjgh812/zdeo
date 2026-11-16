# -*- coding: utf-8 -*-
"""Kronos 重评 MC8 干净窗检验（PREREG: reports/PREREG_KRONOS_REASSESS_MC8_20261027.md, 指纹 9ec75fb203141fe236e7709500af68f5）

唯一变量 = sample_count 1→8（官方 predict_batch 内部自动平均路径）；其余配置与 stage-1 逐字一致（动态加载复用防漂移）。
对照组 = stage-1 已落盘 MC1 干净窗 12 截面（零重跑，同数据同标签）。
判定闸门预锁：改善线 mean>=+0.0966 且 t>=2 → directional-confirm；衰减线 mean<+0.0600 → signal-dampened；中间 → partial。
研究层零接线。诊断性重评，任何结果不直接产生接线行为。

用法：
  python attribution/kronos_reassess_mc8_20261027.py --calib --date 20260731 [--batch 32]  # 单截面校准,不落盘
  python attribution/kronos_reassess_mc8_20261027.py --run [--batch 32]                    # 正式12截面(幂等续跑)
  python attribution/kronos_reassess_mc8_20261027.py --analyze                             # 对照闸门判定
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

# schtasks/非交互控制台 GBK: 强制 UTF-8(防 UnicodeEncodeError 崩溃, fwalk 同款)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PREREG_PATH = Path('reports/PREREG_KRONOS_REASSESS_MC8_20261027.md')
PREREG_MD5 = '9ec75fb203141fe236e7709500af68f5'
RESULTS_JSONL = Path('attribution/kronos_reassess_mc8_results.jsonl')
LOG_PATH = Path('attribution/kronos_reassess_mc8_20261027.log')
STAGE1 = Path(__file__).resolve().parent / 'kronos_zs_stage1_20260904.py'
SAMPLE_COUNT = 8          # 本轮唯一变量(协议锁定, 不做网格)
GATE_CONFIRM_MEAN = 0.0966   # 改善线 = MC1 干净窗 0.1207 × 0.8
GATE_DAMP_MEAN = 0.0600      # 衰减线 ≈ MC1 全样本基线 0.0512
DEFAULT_BATCH = 32           # 等效并发序列 32×8=256; OOM 退避降级

_clean_cache = None


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
    """复用 stage-1 的载入/截面/数据机制(逐字同配置, 不复制代码防漂移; fwalk 同款模式)"""
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


def clean_win_sections(mod):
    """从 stage-1 已落盘结果实测干净窗 12 截面(预测日 >2025-07-31, 与 stage-1 报告分层口径一致)"""
    global _clean_cache
    if _clean_cache is not None:
        return _clean_cache
    rows = read_jsonl(mod.RESULTS_JSONL)
    if len(rows) < 12:
        log(f'❌ stage-1 结果仅 {len(rows)} 截面(<12), 对照基线缺失 fail-closed 中止')
        sys.exit(1)
    clean = [r for r in rows if r['date'] > 20250731]
    if len(clean) != 12:
        log(f'❌ 干净窗截面数 {len(clean)} != 12, 与 PREREG 口径不符 fail-closed 中止')
        sys.exit(1)
    mc1_mean = float(np.mean([r['rankic'] for r in clean]))
    if abs(mc1_mean - 0.1207) > 0.0005:
        log(f'❌ MC1 干净窗 mean={mc1_mean:+.4f} 与 PREREG 基线 +0.1207 不符 fail-closed 中止')
        sys.exit(1)
    _clean_cache = sorted(r['date'] for r in clean)
    log(f'干净窗 12 截面实测对齐: {_clean_cache[0]}..{_clean_cache[-1]}, MC1 mean=+{mc1_mean:.4f}')
    return _clean_cache


def run_section_mc8(predictor, mod, d_end, batch):
    """与 stage-1 run_section 同构, 唯一差异 sample_count=8 + 逐股 signals 落盘"""
    import torch
    t_all = time.time()
    codes, df_list, x_ts, y_ts, last_close, fut = mod.build_cross_section(d_end)
    if len(codes) < 100:
        log(f'❌ {d_end} 截面股票数 {len(codes)} 异常 fail-closed 中止')
        sys.exit(1)
    t_prep = time.time() - t_all

    t_inf = time.time()
    sig = np.full(len(codes), np.nan)
    torch.manual_seed(mod.SEED)
    for i in range(0, len(codes), batch):
        out = predictor.predict_batch(df_list[i:i + batch], x_ts[i:i + batch], y_ts[i:i + batch],
                                      pred_len=mod.PRED_LEN, T=1.0, top_p=0.9,
                                      sample_count=SAMPLE_COUNT, verbose=False)
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
    return dict(arm='KRONOS-REASSESS-MC8', date=d_end, n_stocks=int(ok.sum()), n_raw=len(codes),
                rankic=rankic, mean_signal=float(np.nanmean(sig[ok])),
                topq_excess=float(np.nanmean(fut[top]) - np.nanmean(fut[ok])),
                batch=batch, infer_seconds=round(t_inf, 1),
                signals={str(c).zfill(6): round(float(s), 6) for c, s in zip(codes, sig) if not np.isnan(s)}), t_prep


def run(batch):
    predictor = load_stage1().load_predictor()
    mod = load_stage1()
    dates = clean_win_sections(mod)
    done = {r['date'] for r in read_jsonl(RESULTS_JSONL)}
    log(f'共 {len(dates)} 个干净窗截面, 已完成 {len(done & set(dates))}, 待跑 {len(set(dates) - done)}')
    for d in dates:
        if d in done:
            continue
        rec, t_prep = run_section_mc8(predictor, mod, d, batch)
        with open(RESULTS_JSONL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
        log(f"KRONOS-REASSESS-MC8 {d}: n={rec['n_stocks']} rankic={rec['rankic']:+.4f} "
            f"topq={rec['topq_excess']:+.5f} batch={rec['batch']} prep={t_prep:.0f}s infer={rec['infer_seconds']}s 已落盘")
    log('全部干净窗截面完成')


def run_with_backoff(batch):
    from pyarrow.lib import ArrowMemoryError
    import torch
    b = batch
    for attempt in range(30):
        try:
            run(b)
            return
        except (MemoryError, ArrowMemoryError, torch.cuda.OutOfMemoryError) as e:
            nb = max(8, b // 2)
            log(f'⚠️ 显存/内存争用(第{attempt+1}次): {type(e).__name__} batch {b}→{nb} 退避120s续跑')
            b = nb
            time.sleep(120)
    log('❌ 退避30次仍失败，中止（已完成截面保留，--run 可续）')
    sys.exit(1)


def analyze():
    import scipy.stats as st
    mod = load_stage1()
    dates = clean_win_sections(mod)
    mc8 = {r['date']: r for r in read_jsonl(RESULTS_JSONL) if r['date'] in dates}
    mc1 = {r['date']: r for r in read_jsonl(mod.RESULTS_JSONL) if r['date'] in dates}
    if not mc8:
        print('无 MC8 结果（--run 后再分析）')
        return
    print(f'干净窗 {len(dates)} 截面逐截面对照（MC1=stage-1 已落盘 / MC8=本轮）：')
    rows = []
    for d in dates:
        a, b = mc1.get(d), mc8.get(d)
        if not b:
            print(f'{d}  MC1={a["rankic"]:+.4f}  MC8=未跑')
            continue
        rows.append((a['rankic'], b['rankic']))
        print(f'{d}  MC1={a["rankic"]:+.4f}  MC8={b["rankic"]:+.4f}  Δ={b["rankic"]-a["rankic"]:+.4f}')
    if len(rows) < len(dates):
        print(f'\n⚠️ 仅 {len(rows)}/{len(dates)} 截面完成, 以下为中期观测, 不出判定')
    m8 = np.mean([b for _, b in rows])
    m1 = np.mean([a for a, _ in rows])
    t = st.ttest_1samp([b for _, b in rows], 0).statistic
    print(f'\nMC1 mean={m1:+.4f}  MC8 mean={m8:+.4f}  MC8 t={t:+.2f} 正截面占比={(np.array([b for _, b in rows])>0).mean()*100:.0f}%')
    if len(rows) < len(dates):
        return
    if m8 >= GATE_CONFIRM_MEAN and t >= 2.0:
        verdict = 'directional-confirm（改善线：解码噪声非主因，干净窗区分度实质）'
    elif m8 < GATE_DAMP_MEAN:
        verdict = 'signal-dampened（衰减线：单样本强 IC 含解码运气成分）'
    else:
        verdict = 'partial（中间带：观察登记）'
    print(f'\n闸门判定: {verdict} (改善线>={GATE_CONFIRM_MEAN} 且 t>=2; 衰减线<{GATE_DAMP_MEAN}; 最终以结果报告为准)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--calib', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--analyze', action='store_true')
    ap.add_argument('--date', default='', help='--calib 时的截面日 YYYYMMDD')
    ap.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    a = ap.parse_args()
    if a.analyze:
        analyze()
        return
    check_prereg()
    mod = load_stage1()
    if a.calib:
        d = int(a.date) if a.date else clean_win_sections(mod)[-1]
        predictor = mod.load_predictor()
        t0 = time.time()
        rec, t_prep = run_section_mc8(predictor, mod, d, a.batch)
        log(f"[CALIB] {d} 合计={time.time()-t0:.0f}s n={rec['n_stocks']} batch={rec['batch']} "
            f"rankic={rec['rankic']:+.4f}(仅校准展示,不入结果)")
        return
    run_with_backoff(a.batch)


if __name__ == '__main__':
    main()
