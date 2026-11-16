# -*- coding: utf-8 -*-
"""日频信号生产管线 v4 — 真日更, qlib_bin直接计算 (2026-08-19晚)
发现: qlib_bin实际有数据到8/17, 不是之前以为的8/13(那是模型测试集切分点)
流程: qlib Alpha158计算到目标日 → MASTER/HIST推理 → Top20
"""
from __future__ import annotations

import glob
import os
import sys
import time

import numpy as np
import pandas as pd
import torch

BASE = r"D:\zcodesj"
OUT_DIR = rf"{BASE}\reports\daily_signals"
os.makedirs(OUT_DIR, exist_ok=True)
try:
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
except Exception:
    pass


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def latest_qlib_date():
    with open(r"D:\workbuddyspace\qlib_data\qlib_bin\calendars\day.txt") as f:
        return [l.strip() for l in f if l.strip()][-1]


def main():
    # 2026-08-24 P2修复: 兼容 --date=YYYYMMDD 与 --date YYYYMMDD 两种形式（旧写法对空格形式 IndexError 裸崩）
    target = None
    if len(sys.argv) > 1 and sys.argv[1].startswith("--date"):
        v = sys.argv[1].split("=", 1)
        target = v[1] if len(v) == 2 else (sys.argv[2] if len(sys.argv) > 2 else None)
    target_date = target or latest_qlib_date()
    # (2026-08-23修复) 日期格式归一化: latest_qlib_date() 读 day.txt 为 "2026-08-21"(横线),
    # 手动 --date 也可能传横线 → 输出文件名随输入漂移成 signal_2026-08-21.csv, 与白名单消费端
    # signal_{%Y%m%d}.csv 不匹配。统一去横线 → 输出恒为 signal_YYYYMMDD.csv。
    target_date = str(target_date).strip().replace("-", "")
    log(f"=== 日频双信号 v4 | 目标: {target_date} (qlib最新) ===")

    # 0) 上游数据完整性闸门(根治: qlib_bin 陈旧/错位 → 自愈 → 治不好拒绝出信号, 绝不静默用错数据)
    sys.path.insert(0, BASE)
    from qlib_bin_preflight import run_preflight
    ok, pmsg = run_preflight(target_date)
    if not ok:
        log(f"[preflight FAIL] {pmsg} → 上游数据不可靠, 拒绝出信号, 退出码2")
        sys.exit(2)
    log(f"[preflight OK] {pmsg}")

    # 0.5) 模型-数据基准一致性闸门(2026-08-22 qfq 根治): MASTER 模型 mtime 必须 >= qlib_bin 最近重建时间,
    #      否则是旧(raw)基准模型跑在 qfq 特征上 = 静默错配 → 拒绝出信号。
    #      (2026-08-23) 一次性文档化放行: 当重建为"数据修正"(训练窗特征未变, 已人工确认并写 REBUILD_ACKNOWLEDGED.txt)时跳过,
    #      放行必须留痕(日志打印依据), 非修正性重建必须删标记文件强制重训。
    import os as _os
    from pathlib import Path as _Path
    _rebuild_info = _Path(r"D:\workbuddyspace\qlib_data\qlib_bin\REBUILD_INFO.txt")
    _rebuild_ts = _rebuild_info.stat().st_mtime if _rebuild_info.exists() else 0.0
    _ack = _Path(r"D:\workbuddyspace\qlib_data\qlib_bin\REBUILD_ACKNOWLEDGED.txt")
    _ack_active = _ack.exists() and _ack.stat().st_mtime >= _rebuild_ts
    _old_models = []
    for _s in range(5):
        _mp = _Path(rf"{BASE}\strategy_libs\MASTER\model\tulong_csi300_{_s}.pkl")
        if not _mp.exists() or _mp.stat().st_mtime < _rebuild_ts:
            _old_models.append(f"tulong_csi300_{_s}.pkl")
    if _old_models and not _ack_active:
        log(f"[basis FAIL] MASTER 模型未随 qfq 重建重训({_old_models} mtime < qlib_bin 重建) → 拒绝出信号, 退出码2")
        sys.exit(2)
    if _old_models and _ack_active:
        log(f"[basis OVERRIDE] 重建为数据修正已人工确认(REBUILD_ACKNOWLEDGED.txt, 训练窗未变), 放行旧 qfq 基准模型 {_old_models} 出信号")
    else:
        log("[basis OK] MASTER 模型基准与 qlib_bin(qfq) 一致")

    # 用qlib算Alpha158到target_date
    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=r"D:\workbuddyspace\qlib_data\qlib_bin", region=REG_CN, kernels=4)
    from qlib.contrib.data.handler import Alpha158
    h = Alpha158(instruments="csi300", start_time="2016-06-01", end_time=target_date)
    cfg = h.get_feature_config()
    fields, names = cfg[0], cfg[1]
    feat = qlib.data.D.features(qlib.data.D.instruments("csi300"), fields,
                                start_time="2016-06-01", end_time=target_date)
    feat.columns = names
    lab = qlib.data.D.features(qlib.data.D.instruments("csi300"),
                               ["Ref($close,-2)/Ref($close,-1)-1"],
                               start_time="2016-06-01", end_time=target_date)
    lab.columns = ["LABEL0"]
    df = feat.join(lab, how="left")
    log(f"[feat] {df.shape} 至{df.index.get_level_values('datetime').max().date()}")

    # 市场信息(从缓存, 若无则现算; 缓存滞后目标日→自动重建, 2026-08-22修复: 8/14硬编码致master全NaN)
    mkt_cache = rf"{BASE}\strategy_libs\MASTER\data\tulong\_market_cache.parquet"
    mkt = None
    if os.path.exists(mkt_cache):
        try:
            _mkt = pd.read_parquet(mkt_cache)
            if pd.Timestamp(_mkt.index.max()) >= pd.Timestamp(target_date):
                mkt = _mkt
            else:
                log(f"[market] 缓存最新={_mkt.index.max().date()} < 目标{target_date}, 重建")
        except Exception as e:
            log(f"[market] 缓存读取失败({e}), 重建")
    if mkt is None:
        sys.path.insert(0, rf"{BASE}\strategy_libs\MASTER")
        from tulong_build_data import build_market
        mkt = build_market()
        mkt.to_parquet(mkt_cache)
        log(f"[market] 已重建缓存 {len(mkt)} 行 至 {mkt.index.max().date()}")
    mkt_cols = [c for c in mkt.columns]
    feat_cols = [c for c in df.columns if c != "LABEL0"]   # 合并前捕获158特征列
    mkt = mkt.reindex(df.index.get_level_values("datetime").unique())
    df = df.reset_index().merge(mkt, left_on="datetime", right_index=True, how="left")
    df = df.set_index(["datetime", "instrument"]).sort_index()
    # 列序强制 feat(158)+mkt(63)+LABEL0(1) — 对齐MASTER模型 gate_input=x[:,:,158:221]
    # (2026-08-22修复: 原 merge 把 LABEL0 置于158列, 其NaN(无未来收盘)灌入gate→预测全NaN)
    df = df[feat_cols + mkt_cols + ["LABEL0"]]
    log(f"[feat+market] {df.shape} (含{len(mkt_cols)}市场列)")

    # RobustZScoreNorm(clip3, fit=train期) — 用缓存的统计量
    norm_cache = rf"{BASE}\strategy_libs\MASTER\data\tulong\_norm_stats.parquet"
    _ri = _Path(r"D:\workbuddyspace\qlib_data\qlib_bin\REBUILD_INFO.txt")
    _norm_stale = (not os.path.exists(norm_cache)) or (
        _ri.exists() and os.path.getmtime(norm_cache) < _ri.stat().st_mtime)  # 早于 qfq 重建=旧基准缓存
    if os.path.exists(norm_cache) and not _norm_stale:
        ns = pd.read_parquet(norm_cache)
    else:
        if _norm_stale and os.path.exists(norm_cache):
            log(f"[norm] 缓存早于 qfq 重建, 重算统计量")
        feat_cols = [c for c in df.columns if c != "LABEL0"]
        tr = df[df.index.get_level_values("datetime") <= pd.Timestamp("2024-12-31")]
        med = tr[feat_cols].median()
        mad = (tr[feat_cols] - med).abs().median() * 1.4826
        mad = mad.replace(0, np.nan)
        ns = pd.DataFrame({"med": med, "mad": mad})
        ns.to_parquet(norm_cache)
    feat_cols = [c for c in df.columns if c != "LABEL0"]
    z = (df[feat_cols] - ns["med"].reindex(feat_cols).values) / ns["mad"].reindex(feat_cols).values
    df[feat_cols] = z.clip(-3, 3).fillna(0.0)
    log(f"[norm] done")

    # 手动滑窗构造(不依赖TulongMasterDS, 直接用df)
    sys.path.insert(0, rf"{BASE}\strategy_libs\MASTER")
    dates = sorted(df.index.get_level_values("datetime").unique())
    dt = pd.Timestamp(target_date)
    if dt not in dates:
        log(f"[err] {target_date} 不在特征日期中(可用最新={dates[-1].date()})")
        # 2026-08-24 P1修复: 此前静默回退到最新可用日继续出信号，架空 preflight
        # 双闸门（过期信号冒充目标日、文件名随之漂移）。与 preflight 同力度 fail-closed。
        sys.exit(2)
    pos = dates.index(dt)
    if pos < 8:
        # 20261011批4: 错误路径此前 return(exit 0)——违背本文件 fail-closed 契约(其余错误路径均 exit 2)
        log("[err] 历史不足8天 → exit 2")
        sys.exit(2)
    need = dates[pos - 8:pos + 1]
    sub = df[df.index.get_level_values("datetime").isin(need)]
    arrs, idx = [], []
    for ins, g in sub.groupby(level="instrument"):
        if len(g) < 9:
            continue
        a = g.values.astype(np.float32)
        w = np.lib.stride_tricks.sliding_window_view(a, 8, axis=0).transpose(0, 2, 1)
        arrs.append(w[-1:])
        idx.append(g.index[-1])
    data = np.concatenate(arrs, axis=0)
    index = pd.Index([t[1] for t in idx], name="instrument")
    # 推理不需要label, 但模型输入222列(末列=label占位0)
    data[:, :, -1] = 0.0
    _nn = np.isnan(data).sum(axis=(0, 1))
    if _nn.any():
        log(f"[dbg] data含NaN 按列区: feat={int(_nn[:158].sum())} mkt={int(_nn[158:221].sum())} label={int(_nn[221:].sum())}")
    log(f"[master] {target_date} 样本数 {len(data)} shape={data.shape} gate_input列数={data[:, -1, 158:221].shape}")

    # MASTER 5-seed推理
    from master import MASTERModel
    ens = []
    _miss_seeds = []
    for s in range(5):
        pkl = glob.glob(rf"{BASE}\strategy_libs\MASTER\model\tulong_csi300_{s}.pkl")
        if not pkl:
            _miss_seeds.append(s)
            continue
        m = MASTERModel(d_feat=158, d_model=256, t_nhead=4, s_nhead=2,
                        T_dropout_rate=0.5, S_dropout_rate=0.5, beta=5,
                        gate_input_start_index=158, gate_input_end_index=221,
                        n_epochs=1, lr=1e-5, GPU=0, seed=s,
                        train_stop_loss_thred=0.95,
                        save_path=rf"{BASE}\strategy_libs\MASTER\model", save_prefix="tulong_csi300")
        m.load_param(pkl[-1])
        m.init_model()
        m.model.to(m.device).eval()
        with torch.no_grad():
            # 模型内部自行切gate_input(src[:,:,158:221]=市场), 传完整222列(末列label=0占位)
            x = torch.from_numpy(data).to(m.device).float()
            pred = m.model(x).detach().cpu().numpy().ravel()
        ens.append(pd.Series(pred, index=index))
    # 2026-08-24 P2修复: seed 权重缺失不再静默缩水（与HIST侧闸门对齐：全缺失 exit 2）
    if _miss_seeds:
        log(f"[master][warn] 缺失 seed 权重: {_miss_seeds}（ensemble {5-len(_miss_seeds)}/5）")
        # 2026-08-25 O4: 缺失≥3(ensemble过半退化)硬闸门 exit 2, 与 HIST 侧 fail-closed 哲学对齐
        if len(_miss_seeds) >= 3:
            # 20261011批4: >=3 已覆盖全缺(5)情形, 原独立 ==5 分支在其后永不可达, 已删
            log("[master][FATAL] seed 权重缺失过半 → ensemble 质量不可信, exit 2")
            sys.exit(2)
    m = pd.concat(ens, axis=1).mean(axis=1)
    log(f"[master] {m.shape} top5={m.nlargest(5).to_dict()}")

    # HIST(用最新预测)
    h_ens = []
    _h_miss = []
    for s in range(1, 6):
        # 20261011: hist_pred_seed 工件 pkl→parquet(消除反序列化执行类, Mimosa扫描遗留加固)
        pkl = rf"{BASE}\qlib_exp\hist_data\hist_pred_seed{s}.parquet"
        if os.path.exists(pkl):
            p = pd.read_parquet(pkl)
            p.name = "score"
            h_ens.append(p)
        else:
            _h_miss.append(s)
    if _h_miss:
        log(f"[hist][warn] 缺失 seed 权重: {_h_miss}（ensemble {5-len(_h_miss)}/5）")
        # 2026-09-10: 与 MASTER 侧闸门对齐(O4 语义)——缺失≥3(ensemble 过半退化)硬闸
        # exit 2; 此前 HIST 侧缺失只 WARN, 极端下 1 个 seed 也能出「Top20」, 两侧不对称
        if len(_h_miss) >= 3:
            log("[hist][FATAL] seed 权重缺失过半 → ensemble 质量不可信, exit 2")
            sys.exit(2)
    # 20261011批4: 空检查此前在 concat 之后——h_ens 为空时 pd.concat([]) 先抛 ValueError,
    # 结构化 FAIL 分支永不可达(裸 traceback 退出)。移到 concat 之前。
    if len(h_ens) == 0:
        log("[hist FAIL] 无任何 hist_pred_seed*.parquet, HIST 信号缺失 → 拒绝出信号(双信号缺一), 退出码2")
        sys.exit(2)
    h = pd.concat(h_ens, axis=1).mean(axis=1, skipna=True)
    h_today = h.xs(h.index.get_level_values(0).max(), level=0)
    _hdate = h.index.get_level_values(0).max().date()
    if _hdate < pd.Timestamp(target_date).date():
        # (2026-08-22根治) HIST 已日常化(hist_daily_update.py), 最新日应==目标日; 若落后=链异常,
        # 与 preflight/basis 同力度硬闸门: 拒绝出信号, 绝不半静默混入过期 hist 列。
        log(f"[hist FAIL] HIST最新日={_hdate} < 目标{target_date} → HIST 未随链更新, 拒绝出信号, 退出码2")
        sys.exit(2)
    log(f"[hist] {h_today.shape} 最新日={_hdate}")

    # 合并Top20
    combined = pd.DataFrame({"master": m, "hist": h_today}).dropna()
    combined["rank_m"] = combined["master"].rank(ascending=False)
    combined["rank_h"] = combined["hist"].rank(ascending=False)
    # (2026-08-22修复) 必须 axis=1 逐行均值; 原默认axis=0算列均值→avg_rank全NaN→sort空操作→top20退化为代码升序前20
    combined["avg_rank"] = combined[["rank_m", "rank_h"]].mean(axis=1)
    combined["agree"] = (combined["rank_m"] <= 50) & (combined["rank_h"] <= 50)
    combined["code"] = combined.index
    # (2026-08-24修复) 边界执行: PROJECT_BOUNDARY §0 剔除科创板/北交所 universe
    # csi300 含 SH688xxx 科创板成分（已纳入指数），违反 universe 边界声明；
    # 下游 pre_trade_check_v2.py:347 已硬拦截，此处消除信号 CSV 误导。
    _n_pre = len(combined)
    combined = combined[~combined.index.str.startswith("SH688")]
    log(f"[universe] 剔除科创板后 {_n_pre}→{len(combined)} 只 (PROJECT_BOUNDARY §0 边界)")
    top20 = combined.sort_values("avg_rank").head(20)

    _codes6 = top20["code"].str.replace(r"^(SH|SZ)", "", regex=True)
    top20["name"] = pd.NA
    try:
        import akshare as ak
        uni = ak.index_stock_cons_csindex(symbol="000300")
        name_map = dict(zip(uni["成分券代码"].str.replace(r"^(SH|SZ)", "", regex=True), uni["成分券名称"]))
        top20["name"] = _codes6.map(name_map)
    except Exception as e:
        log(f"[warn] akshare 名称映射失败({type(e).__name__} {e}) → stock_name_lookup 兜底")
    # (2026-08-24修复) 铁律兜底: 涉及代码必带名称; akshare 失败/缺项用项目唯一名称接口补齐,
    # 查不到才保留代码占位并告警(不再直接降级为纯代码)
    if top20["name"].isna().any():
        from stock_name_lookup import get_name
        top20["name"] = top20["name"].fillna(_codes6.map(get_name))
        _na_n = int(top20["name"].isna().sum())
        if _na_n:
            log(f"[warn] 名称兜底后仍有 {_na_n} 只未知名 → 保留代码占位")
            top20["name"] = top20["name"].fillna(_codes6)

    log(f"\n=== {target_date} 双信号Top20 ===")
    log(top20[["code", "name", "master", "hist", "rank_m", "rank_h", "avg_rank", "agree"]].to_string())
    out = rf"{OUT_DIR}\signal_{target_date}.csv"
    top20[["code", "name", "master", "hist", "rank_m", "rank_h", "avg_rank", "agree"]].to_csv(out, index=False)
    log(f"落盘: {out}")

    # (2026-08-25) 全市场分数落盘：为 WFA 门禁信号级验证积累历史（tulong_wfa_gate.py --signals-dir 消费）。
    # 增量幂等，不改变 signal_YYYYMMDD.csv 合同；combined 已含 code 列且已剔除 SH688（PROJECT_BOUNDARY §0）。
    full_out = rf"{OUT_DIR}\full_score_{target_date}.csv"
    combined[["code", "master", "hist", "rank_m", "rank_h", "avg_rank", "agree"]].to_csv(full_out, index=False)
    log(f"落盘(全市场分数, {len(combined)}只): {full_out}")

    # ==== 输出自检（2026-08-21 交付, M1: 8/13空文件/8/18乱序教训代码化）====
    import pandas as _pd
    chk = _pd.read_csv(out)
    errors = []
    if len(chk) != 20:
        errors.append(f"行数={len(chk)} ≠ 20")
    if "avg_rank" in chk.columns and not chk["avg_rank"].is_monotonic_increasing:
        errors.append("avg_rank 未按升序排列(乱序)")
    bad = chk[chk["code"].astype(str).str.replace(r"^(SH|SZ)", "", regex=True).str.fullmatch(r"\d{6}", na=False) == False]
    if len(bad) > 0:
        errors.append(f"存在非法代码行 {len(bad)} 条")
    if "agree" in chk.columns and chk["agree"].isna().any():
        errors.append("agree 字段存在空值")
    if errors:
        log(f"[自检FAIL] signal_{target_date}.csv: {'; '.join(errors)} → 退出码2")
        sys.exit(2)
    log(f"[自检OK] signal_{target_date}.csv 行数={len(chk)} 排序=升序 代码合法 agree完整")


if __name__ == "__main__":
    main()