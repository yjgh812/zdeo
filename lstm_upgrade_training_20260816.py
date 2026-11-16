# -*- coding: utf-8 -*-
"""
LSTM超参升级训练 — 翻案深化 (2026-08-16晚)
================================================
基线: LSTM_ROLLING_RETRAIN翻案版(5特征×60窗×hidden64×2层) IC=0.0600
升级实验:
  V1: 模型放大 hidden128×3层 (特征不变5维)
  V2: 特征增强 8维(+mom5/mom20/vol20/volr_chg 从基础特征派生, rolling无前视)
     + hidden128×3层
判定: OOS年度IC均值 > 0.0600基线 = 升级有效

产出: reports/LSTM_UPGRADE_20260816.md + _cache/lstm_upgrade_best.pt(若胜出)
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

sys.path.insert(0, r"D:\zcodesj")
from deep_sequence_retest_20260816 import DEV, WIN, FWD, eval_year  # noqa: E402

CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
SEED = 42
BASELINE_IC = 0.0600


class LSTM3(nn.Module):
    """LSTM hidden×3层 可配宽度。"""
    def __init__(self, cin=5, hidden=128, layers=3):
        super().__init__()
        self.lstm = nn.LSTM(cin, hidden, layers, batch_first=True, dropout=0.15)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):                # (B,N,C,L)
        B, N, C, L = x.shape
        x = x.permute(0, 1, 3, 2).reshape(B * N, L, C)
        out, _ = self.lstm(x)
        return self.head(out[:, -1]).reshape(B, N)


def load_base():
    return pickle.load(open(CACHE / "deep_seq_data_rolling.pkl", "rb"))


def expand_features(data):
    """5维 → 8维(rolling派生, 无前视; 基于已rolling归一的特征再派生后二次rank-z)。"""
    F = data["F"]  # (5,N,T) rolling-z后
    ret = F[3:4]   # ret特征(行3)
    volr = F[4:5]  # volr特征(行4)
    mom5 = ret[:, :, :-5] - ret[:, :, 5:]  # 近5日收益和(近似: 已归一ret的差分求和)
    mom5 = np.concatenate([np.full((1, F.shape[1], 5), np.nan), mom5], axis=2)
    mom20 = np.zeros_like(ret)
    for k in range(1, 21):
        mom20 += np.concatenate([np.full((1, F.shape[1], k), np.nan),
                                 ret[:, :, :-k] if k > 0 else ret], axis=2)
    vol20 = np.full_like(ret, np.nan)
    # 滚动std (向量化近似: 20日平方和-均值平方)
    cs = np.nancumsum(ret, axis=2)
    cs2 = np.nancumsum(ret * ret, axis=2)
    n = np.arange(1, ret.shape[2] + 1)[None, None, :]
    s20 = cs - np.concatenate([np.zeros((1, ret.shape[1], 20)), cs[:, :, :-20]], axis=2)
    s2_20 = cs2 - np.concatenate([np.zeros((1, ret.shape[1], 20)), cs2[:, :, :-20]], axis=2)
    vol20 = np.sqrt(np.maximum(s2_20 / 20 - (s20 / 20) ** 2, 0))[:, :, 20:]
    vol20 = np.concatenate([np.full((1, F.shape[1], 20), np.nan), vol20], axis=2)
    volr_chg = np.concatenate([np.full((1, F.shape[1], 5), np.nan),
                               volr[:, :, 5:] - volr[:, :, :-5]], axis=2)
    new = np.concatenate([F, mom5, mom20, vol20, volr_chg], axis=0)  # (9,N,T)
    # NaN→0(中性)
    new = np.nan_to_num(new, nan=0.0)
    return new.astype(np.float32)


def train_wf(model, F, fwd, dates, test_years, epochs=3, lr=5e-4):
    """与翻案实验同协议WF训练+评估。"""
    N = F.shape[1]
    results = {}
    for yr in test_years:
        years_train = tuple(y for y in range(yr - 3, yr))
        d_idx = [t for t in range(len(dates))
                 if dates[t].year in years_train and t - WIN >= 0 and t + FWD < len(dates)]
        model.train()
        opt = torch.optim.Adam(model.parameters(), lr=lr)
        lossf = nn.MSELoss()
        rng = np.random.default_rng(SEED)
        for ep in range(epochs):
            rng.shuffle(d_idx)
            tot, cnt = 0.0, 0
            for i in range(0, len(d_idx), 8):
                batch = d_idx[i:i + 8]
                xs, ys = [], []
                for t in batch:
                    x = F[:, :, t - WIN:t]
                    y = fwd[t]
                    if np.isnan(y).all():
                        continue
                    xs.append(x.transpose(1, 0, 2))
                    ys.append(pd.Series(y).rank(pct=True).fillna(0.5).values)
                if not xs:
                    continue
                xb = torch.tensor(np.array(xs), dtype=torch.float32, device=DEV)
                yb = torch.tensor(np.array(ys), dtype=torch.float32, device=DEV)
                pred = model(xb)
                loss = lossf(pred, yb)
                opt.zero_grad(); loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                tot += loss.item(); cnt += 1
        # 评估(复用eval_year语义但本地F)
        model.eval()
        ics = []
        with torch.no_grad():
            for t in range(len(dates)):
                if dates[t].year != yr or t - WIN < 0 or t + FWD >= len(dates):
                    continue
                x = F[:, :, t - WIN:t]
                y = fwd[t]
                if np.isnan(y).all():
                    continue
                xb = torch.tensor(x.transpose(1, 0, 2)[None], dtype=torch.float32, device=DEV)
                pred = model(xb)[0].cpu().numpy()
                yy = pd.Series(y)
                ok = yy.notna() & np.isfinite(pred)
                if ok.sum() < 100:
                    continue
                ics.append(pd.Series(pred[ok]).rank().corr(yy[ok].rank()))
        results[yr] = float(np.nanmean(ics)) if ics else np.nan
        print(f"    {yr}: IC={results[yr]:.4f} n={len(ics)} ({time.time():.0f})",
              flush=True)
    return results


def main():
    t0 = time.time()
    print(f"=== LSTM超参升级训练 (device={DEV}) ===")
    data = load_base()
    dates = data["dates"]
    fwd = data["fwd"]
    test_years = [2022, 2023, 2024, 2025, 2026]

    # V1: 放大模型(5特征)
    print("  [V1] LSTM-128x3 (5特征)")
    m1 = LSTM3(cin=5, hidden=128, layers=3).to(DEV)
    r1 = train_wf(m1, data["F"], fwd, dates, test_years)

    # V2: 特征增强(9维)
    print("  [V2] LSTM-128x3 (9特征增强)")
    F9 = expand_features(data)
    m2 = LSTM3(cin=F9.shape[0], hidden=128, layers=3).to(DEV)
    r2 = train_wf(m2, F9, fwd, dates, test_years)

    v1m, v2m = np.nanmean(list(r1.values())), np.nanmean(list(r2.values()))
    lines = [
        "# LSTM超参升级训练",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 基线(翻案版64x2×5特征): IC={BASELINE_IC:.4f}",
        "- 协议: rolling expanding归一+WF年度重训+3epochs, 完全同翻案实验",
        "",
        "| 版本 | " + " | ".join(map(str, test_years)) + " | 均值 |",
        "|------|" + "---|" * 6,
    ]
    for name, r, m in (("V1放大128x3", r1, v1m), ("V2增强9特征", r2, v2m)):
        lines.append(f"| {name} | " + " | ".join(f"{r[y]:.4f}" for y in test_years)
                     + f" | {m:.4f} |")
    winner = None
    if max(v1m, v2m) > BASELINE_IC + 0.005:
        winner = "V1放大" if v1m > v2m else "V2增强"
        lines += ["", f"**升级有效**: {winner}(IC={max(v1m,v2m):.4f})超基线{BASELINE_IC:.4f}",
                  "- 建议灰度轨道模型切换为升级版"]
        best_model = m1 if v1m > v2m else m2
        torch.save(best_model.state_dict(), CACHE / "lstm_upgrade_best.pt")
    else:
        lines += ["", f"**升级无效**: 最好{max(v1m,v2m):.4f}未显著超基线{BASELINE_IC:.4f}",
                  "- 灰度轨道维持原64x2模型(简单优于复杂再验证)"]
    lines += ["", f"耗时 {(time.time()-t0)/60:.0f}min", ""]
    p = REPORT / f"LSTM_UPGRADE_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}\n  V1={v1m:.4f} V2={v2m:.4f} 基线={BASELINE_IC:.4f}")


if __name__ == "__main__":
    main()
