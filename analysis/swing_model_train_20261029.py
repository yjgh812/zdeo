# -*- coding: utf-8 -*-
"""swing_model_train_20261029.py — TinyLSTM 波段前夜分类器(训练+纯OOS评估)

训练: 2018-2022 | OOS: 2023-2026(模型从未见过)
模型: LSTM(13→64, 2层) → Linear(64,3) 三分类(中性/涨前/跌前), CPU。
评估(交易视角):
  ① OOS 分类指标(macro-F1/各类recall)
  ② 模型P(涨前)分数的选样增量: OOS按分数Top10%样本的fwd20 vs OOS全体fwd20
  ③ 分年OOS: P(涨前)>阈值样本的fwd20均值/胜率
"""
import os, sys, json, time
import numpy as np
import torch
import torch.nn as nn

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
t0 = time.time()
torch.manual_seed(42); np.random.seed(42)

d = np.load(os.path.join(ROOT, "data", "swing_model_dataset_20261029.npz"), allow_pickle=True)
X, Y, YR, F = d["X"], d["Y"], d["YR"], d["F"]
train_m = YR <= 2022
oos_m = YR >= 2023
Xtr, Ytr = torch.tensor(X[train_m]), torch.tensor(Y[train_m])
Xo_np, Yo, Fo, YRo = X[oos_m], Y[oos_m], F[oos_m], YR[oos_m]
Xo = torch.tensor(Xo_np)
print(f"训练{int(train_m.sum())} OOS{int(oos_m.sum())} ({time.time()-t0:.0f}s)", flush=True)

class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(13, 64, num_layers=2, batch_first=True, dropout=0.1)
        self.head = nn.Linear(64, 3)
    def forward(self, x):
        o, _ = self.lstm(x)
        return self.head(o[:, -1, :])

cnt = np.bincount(Ytr.numpy(), minlength=3)
w = torch.tensor(len(Ytr)/np.maximum(cnt, 1), dtype=torch.float32)
w = w/w.mean()
model = Net()
opt = torch.optim.Adam(model.parameters(), lr=1e-3)
lossf = nn.CrossEntropyLoss(weight=w)
B = 256
for ep in range(20):
    model.train()
    perm = torch.randperm(len(Xtr))
    tl = 0.0
    for i in range(0, len(Xtr), B):
        idx = perm[i:i+B]
        logits = model(Xtr[idx])
        loss = lossf(logits, Ytr[idx])
        opt.zero_grad(); loss.backward(); opt.step()
        tl += float(loss)*len(idx)
    if (ep+1) % 5 == 0:
        print(f"ep{ep+1} loss={tl/len(Xtr):.4f}", flush=True)
print(f"训练完成 ({time.time()-t0:.0f}s)", flush=True)

# OOS评估
model.eval()
with torch.no_grad():
    logits = []
    for i in range(0, len(Xo), 512):
        logits.append(model(Xo[i:i+512]))
    P = torch.softmax(torch.cat(logits), dim=1).numpy()
pred = P.argmax(1)
acc = float((pred == Yo).mean())*100
macro_f1 = []
for k in range(3):
    tp = ((pred == k) & (Yo == k)).sum(); fp = ((pred == k) & (Yo != k)).sum()
    fn = ((pred != k) & (Yo == k)).sum()
    prec = tp/max(tp+fp, 1); rec = tp/max(tp+fn, 1)
    f1 = 2*prec*rec/max(prec+rec, 1e-9)
    macro_f1.append(f1)
    print(f"  类{k}: precision={prec*100:.1f}% recall={rec*100:.1f}% f1={f1*100:.1f}%")
print(f"OOS acc={acc:.1f}% macroF1={np.mean(macro_f1)*100:.1f}%", flush=True)

# 选样增量: P(涨前)分数
p_up = P[:, 1]
base_mean = float(np.nanmean(Fo))*100
base_win = float(np.nanmean(Fo > 0))*100
top_m = p_up >= np.nanquantile(p_up, 0.9)
top_mean = float(np.nanmean(Fo[top_m]))*100
top_win = float(np.nanmean(Fo[top_m] > 0))*100
print(f"\n=== 选样增量(P涨前Top10%) ===")
print(f"OOS全体: {base_mean:.2f}%/胜{base_win:.1f}%")
print(f"Top10%:  {top_mean:.2f}%/胜{top_win:.1f}% | 增量{top_mean-base_mean:+.2f}pp")

# 分年
yearly = {}
for y in sorted(set(YRo.tolist())):
    m = YRo == y
    tm = m & top_m
    yearly[int(y)] = {"all": {"n": int(m.sum()), "mean": round(float(np.nanmean(Fo[m]))*100, 2),
                               "win": round(float(np.nanmean(Fo[m] > 0))*100, 1)},
                      "top10": {"n": int(tm.sum()), "mean": round(float(np.nanmean(Fo[tm]))*100, 2),
                                 "win": round(float(np.nanmean(Fo[tm] > 0))*100, 1)} if tm.sum() else {"n": 0}}
for y, v in yearly.items():
    a, t = v["all"], v["top10"]
    ts = f"{t['mean']}%/胜{t['win']}% (n={t['n']})" if t["n"] else "n=0"
    print(f"  {y}: 全体{a['mean']}%/胜{a['win']}% | Top10% {ts}")

R = {"built_at": time.strftime("%Y-%m-%d %H:%M"),
     "oos_acc": round(acc, 1), "macro_f1": round(float(np.mean(macro_f1))*100, 1),
     "base": {"mean": round(base_mean, 2), "win": round(base_win, 1)},
     "top10": {"mean": round(top_mean, 2), "win": round(top_win, 1)},
     "edge_pp": round(top_mean - base_mean, 2), "yearly": yearly}
json.dump(R, open(os.path.join(ROOT, "data", "swing_model_eval_20261029.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
torch.save(model.state_dict(), os.path.join(ROOT, "data", "swing_model_20261029.pt"))
print(f"\n落盘: eval json + model pt ({time.time()-t0:.0f}s)")
