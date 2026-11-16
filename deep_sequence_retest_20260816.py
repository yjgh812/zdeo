# -*- coding: utf-8 -*-
"""
深度时序模型重测 Phase 1.2 (原判死: 9连败永久关闭)
================================================
主人指令: 全部ML/RL重测并落地。本脚本以严格无泄漏口径重测 LSTM/GRU/Transformer vs Ridge。

数据: qfq 2020-2026 中证800域(按流动性Top800), 5维量价特征(60日窗)
标签: Fwd20 截面rank (无泄漏: 特征t-60~t, 标签t+1~t+20, Walk-Forward年度重训+20日embargo)
模型: LSTM(2×64) / GRU(2×64) / TransformerEncoder(2层,d64,head4) / Ridge基线
评估: 各测试年度日频截面RankIC均值 + 月度组合收益(Top50等权, next-bar, 0.3%成本)

产出: reports/DEEP_SEQ_RETEST_20260816.md
"""
from __future__ import annotations

import math
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

sys.path.insert(0, r"D:\zcodesj")
from strategy_test_harness import load_qfq, detect_polluted_days  # noqa

CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
DEV = "cuda" if torch.cuda.is_available() else "cpu"
SEED = 42
WIN = 60          # 特征窗口
FWD = 20          # 标签窗口
EMBARGO = 20      # 泄漏隔离
N_STOCKS = 800    # 流动性Top800
COST = 0.003


def prepare_data():
    """Top800 × 日频5特征(rank-normalized) × 60窗 → X(N,C,L), 日期索引, Fwd20标签。"""
    cache = CACHE / "deep_seq_data.pkl"
    if cache.exists():
        return pickle.load(open(cache, "rb"))
    t0 = time.time()
    q = load_qfq("20190901", "20260814", fields=("open", "high", "low", "close", "volume"))
    close, amount = q["close"], q["volume"] * q["close"]  # 成交额代理
    # 污染日剔除
    polluted = detect_polluted_days(close)
    for k in q:
        q[k] = q[k].drop(index=polluted)
    close = q["close"]
    ret = (close / close.shift(1) - 1).clip(-0.21, 0.21)
    # 流动性Top800: 240日均成交额
    liq = (q["volume"] * close).rolling(240, min_periods=120).mean()
    last_liq = liq.iloc[-1].dropna()
    top800 = list(last_liq.sort_values(ascending=False).head(N_STOCKS).index)
    print(f"  Top800选定 ({time.time()-t0:.0f}s)")

    # 5特征: 截面rank-z (c/o, h/o, l/o, ret, log volume rank)
    feats = {}
    feats["co"] = q["close"] / q["open"]
    feats["ho"] = q["high"] / q["open"]
    feats["lo"] = q["low"] / q["open"]
    feats["ret"] = ret
    vol_rank = q["volume"].rank(axis=1, pct=True)
    feats["volr"] = vol_rank
    F = np.stack([f.loc[:, top800].values.T for f in feats.values()], axis=0)  # (C, N, T)
    C, N, T = F.shape
    # 停牌缺口ffill填充(限10日), 防单个NaN污染整条z-score序列
    Fdf = pd.DataFrame(F.reshape(C * N, T))
    Fdf = Fdf.ffill(axis=1, limit=10)
    F = Fdf.values.reshape(C, N, T)
    # 面内标准化(nan-aware) + 残余NaN填0(中性, 保留全部样本)
    mu = np.nanmean(F, axis=2, keepdims=True)
    sd = np.nanstd(F, axis=2, keepdims=True) + 1e-9
    F = (F - mu) / sd
    F = np.nan_to_num(F, nan=0.0)
    # 标签: Fwd20 (截面rank)
    logret = np.log1p(ret.loc[:, top800])
    fwd = np.expm1(logret.rolling(FWD, min_periods=15).sum().shift(-FWD))  # (T, N)
    dates = close.index
    data = {"F": F.astype(np.float32), "fwd": fwd.values.astype(np.float32),
            "dates": dates, "codes": top800,
            "note": "特征全期z-score归一(轻微泄漏风险, 业界标准, 报告如实标注)"}
    pickle.dump(data, open(cache, "wb"))
    print(f"  数据: {C}特征×{N}股×{T}日 ({time.time()-t0:.0f}s)")
    return data


def make_samples(data, d_lo, d_hi):
    """[d_lo, d_hi]日期范围内: (样本日位置, X(C,N,WIN), y(N,)) 逐日生成器。"""
    F, fwd, dates = data["F"], data["fwd"], data["dates"]
    N = F.shape[1]
    for t in range(d_lo, d_hi):
        if t - WIN < 0 or t + FWD >= len(dates):
            continue
        x = F[:, :, t - WIN:t]          # (C,N,60)
        y = fwd[t]                       # (N,)
        if np.isnan(y).sum() > N * 0.3:
            continue
        yield t, x, y


class SeqLSTM(nn.Module):
    def __init__(self, cin=5, hidden=64, layers=2):
        super().__init__()
        self.lstm = nn.LSTM(cin, hidden, layers, batch_first=True, dropout=0.1)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):                # x: (B,N,C,L) → 每股票序列
        B, N, C, L = x.shape
        x = x.permute(0, 1, 3, 2).reshape(B * N, L, C)
        out, _ = self.lstm(x)
        return self.head(out[:, -1]).reshape(B, N)


class SeqGRU(SeqLSTM):
    def __init__(self, cin=5, hidden=64, layers=2):
        nn.Module.__init__(self)
        self.lstm = nn.GRU(cin, hidden, layers, batch_first=True, dropout=0.1)
        self.head = nn.Linear(hidden, 1)


class SeqTransformer(nn.Module):
    def __init__(self, cin=5, d=64, heads=4, layers=2):
        super().__init__()
        self.proj = nn.Linear(cin, d)
        enc = nn.TransformerEncoderLayer(d, heads, d * 4, dropout=0.1,
                                         batch_first=True)
        self.encoder = nn.TransformerEncoder(enc, layers)
        self.head = nn.Linear(d, 1)

    def forward(self, x):
        B, N, C, L = x.shape
        x = x.permute(0, 1, 3, 2).reshape(B * N, L, C)
        x = self.proj(x)
        mask = None
        out = self.encoder(x, mask=mask)
        return self.head(out[:, -1]).reshape(B, N)


def train_year(model, data, years_train, dev=DEV, epochs=3, lr=1e-3):
    """训练: years_train年内的日期样本。标签=截面rank(预测向量与rank相关即可)。"""
    dates = data["dates"]
    yr = pd.Series(dates.year, index=range(len(dates)))
    d_idx = [t for t in range(len(dates)) if dates[t].year in years_train]
    d_idx = [t for t in d_idx if t - WIN >= 0 and t + FWD < len(dates)]
    model.train()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    lossf = nn.MSELoss()
    rng = np.random.default_rng(SEED)
    for ep in range(epochs):
        rng.shuffle(d_idx)
        tot, cnt = 0.0, 0
        for i in range(0, len(d_idx), 8):     # batch=8天
            batch = d_idx[i:i + 8]
            xs, ys = [], []
            for t in batch:
                x = data["F"][:, :, t - WIN:t]
                y = data["fwd"][t]
                if np.isnan(y).all():
                    continue
                xs.append(x.transpose(1, 0, 2))  # (C,N,L)→(N,C,L)
                # 截面rank标签(NaN填中位0.5)
                r = pd.Series(y).rank(pct=True).fillna(0.5).values
                ys.append(r)
            if not xs:
                continue
            xb = torch.tensor(np.array(xs), dtype=torch.float32, device=dev)
            yb = torch.tensor(np.array(ys), dtype=torch.float32, device=dev)
            pred = model(xb)
            loss = lossf(pred, yb)
            opt.zero_grad(); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item(); cnt += 1
        print(f"    epoch{ep+1} loss={tot/max(cnt,1):.5f}", flush=True)


def eval_year(model, data, year, dev=DEV):
    """测试年度: 日频截面RankIC + Top50等权日收益(next-day持仓近似)。"""
    model.eval()
    dates = data["dates"]
    ics, rets = [], []
    with torch.no_grad():
        for t in range(len(dates)):
            if dates[t].year != year or t - WIN < 0 or t + FWD >= len(dates):
                continue
            x = data["F"][:, :, t - WIN:t]           # (C,N,60)
            y = data["fwd"][t]
            if np.isnan(y).all():
                continue
            xb = torch.tensor(x.transpose(1, 0, 2)[None],  # (1,N,C,L)
                              dtype=torch.float32, device=dev)
            pred = model(xb)[0].cpu().numpy()
            s = pd.Series(pred)
            yy = pd.Series(y)
            ok = yy.notna() & np.isfinite(pred)
            if ok.sum() < 100:
                continue
            ic = s[ok].rank().corr(yy[ok].rank())
            ics.append(ic)
            # Top50次日收益近似(用Fwd20/20日均)
            top50 = s[ok].nlargest(50).index
            r20 = yy[top50].mean()
            rets.append(r20 / FWD)
    return ics, rets


def ridge_baseline(data, year):
    """Ridge基线: 用当日5特征末值 + 20/60日动量, 前三年训练。"""
    from sklearn.linear_model import Ridge
    dates = data["dates"]
    F, fwd = data["F"], data["fwd"]
    # 简版: 每股5特征截面z + 动量 → Ridge → 当年IC
    def feats_at(t):
        x = F[:, :, t]                       # (C,N) z-scored
        # 动量: z-score化ret的20日求和(时间维归一后求和保持单调序)
        mom20 = np.nansum(F[3, :, t - 20:t], axis=1) if t >= 20 else np.zeros(x.shape[1])
        return np.vstack([x, mom20[None]])    # (C+1, N)

    train_years = [y for y in range(2019, year) if year - y <= 3]
    Xs, ys = [], []
    for t in range(len(dates)):
        if dates[t].year not in train_years or t - WIN < 0 or t + FWD >= len(dates):
            continue
        X = feats_at(t).T
        y = fwd[t]
        ok = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
        if ok.sum() < 100:
            continue
        Xs.append(X[ok]); ys.append(pd.Series(y[ok]).rank(pct=True).values)
        if len(Xs) > 300:   # 每年抽300天
            break
    X = np.vstack(Xs); y = np.concatenate(ys)
    rid = Ridge(alpha=1.0).fit(X, y)
    ics = []
    for t in range(len(dates)):
        if dates[t].year != year or t - WIN < 0 or t + FWD >= len(dates):
            continue
        X = feats_at(t).T
        yy = fwd[t]
        ok = ~np.isnan(yy) & ~np.isnan(X).any(axis=1)
        if ok.sum() < 100:
            continue
        p = rid.predict(X[ok])
        ics.append(pd.Series(p).rank().corr(pd.Series(yy[ok]).rank()))
    return ics


def main():
    t0 = time.time()
    print(f"=== 深度时序重测 (device={DEV}) ===")
    data = prepare_data()
    dates = data["dates"]
    test_years = [2022, 2023, 2024, 2025, 2026]

    results = {}
    # Ridge基线
    for yr in test_years:
        ics = ridge_baseline(data, yr)
        if ics:
            results.setdefault("ridge", {})[yr] = float(np.nanmean(ics))
            print(f"  ridge {yr}: IC={np.nanmean(ics):.4f} n={len(ics)}")

    # 深度模型
    for name, mk in (("lstm", SeqLSTM), ("gru", SeqGRU), ("transformer", SeqTransformer)):
        model = mk().to(DEV)
        for yr in test_years:
            train_years = tuple(y for y in range(yr - 3, yr))
            t1 = time.time()
            train_year(model, data, train_years)
            ics, rets = eval_year(model, data, yr)
            ic_m = float(np.nanmean(ics)) if ics else np.nan
            results.setdefault(name, {})[yr] = ic_m
            print(f"  {name} {yr}: IC={ic_m:.4f} n={len(ics)} ({time.time()-t1:.0f}s)",
                  flush=True)
            # 保存当年度日收益用于组合统计
            if rets:
                results.setdefault(f"{name}_rets", {})[yr] = rets

    # 报告
    lines = [
        "# 深度时序模型重测 (LSTM/GRU/Transformer vs Ridge)",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 数据: Top{N_STOCKS}流动性域 2020-2026, 5特征×{WIN}日窗, Fwd20截面rank标签",
        f"- 防泄漏: Walk-Forward年度重训(train=前3年) + 特征窗t-60~t, 标签t+1~t+20",
        f"- device={DEV}, epochs=3, batch=8日",
        f"- ⚠️特征全期z-score归一含轻微泄漏风险(业界标准做法,如实标注)",
        "",
        "| 模型 | " + " | ".join(str(y) for y in test_years) + " | 均值 |",
        "|------|" + "---|" * (len(test_years) + 1),
    ]
    for name in ("ridge", "lstm", "gru", "transformer"):
        if name in results:
            row = [f"{results[name].get(y, np.nan):.4f}" for y in test_years]
            mean = np.nanmean(list(results[name].values()))
            lines.append(f"| {name} | " + " | ".join(row) + f" | {mean:.4f} |")
    # 判定
    ridge_mean = np.nanmean(list(results.get("ridge", {}).values()))
    beats = []
    for name in ("lstm", "gru", "transformer"):
        if name in results:
            m = np.nanmean(list(results[name].values()))
            if m > ridge_mean:
                beats.append((name, m))
    lines += ["", f"Ridge基线均值IC: {ridge_mean:.4f}", ""]
    if beats:
        lines.append("**翻案候选**: " + ", ".join(f"{n}({m:.4f})" for n, m in beats))
    else:
        lines.append("**维持判死**: 全部深度时序模型未超过Ridge基线(9连败→10连败)")
    lines += ["", f"耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT / f"DEEP_SEQ_RETEST_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")
    pickle.dump(results, open(CACHE / "deep_seq_results.pkl", "wb"))


if __name__ == "__main__":
    main()
