r"""portrait_ts2vec_r1_20261117.py — 画像系统·检索器升级标定 E-TS2VEC-1（PREREG_PORTRAIT_TS2VEC_R1_V1，指纹697240e5c7aae3c8）。

两目标(上证最近30日/600519贵州茅台最近30日)对全市场历史30日窗口库(约719万)检索：
粗筛=自实现TS2Vec核心(分层对比学习TCN编码器)窗口嵌入余弦Top-500 → 精配自实现DTW(30x30全带宽)Top-150
→ 后续20行收益分布+era分布+零后续占比诊断。纯描述零预测声称, 零交易权。
用法: python -I portrait_ts2vec_r1_20261117.py [run2]
"""
import os

os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"  # 须在 torch/CUDA 初始化前设置

import hashlib
import heapq
import itertools
import json
import sqlite3
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
PREREG = r"D:\zcodesj\data\PREREG_PORTRAIT_TS2VEC_R1_V1_20261117.json"
OUT1 = r"D:\zcodesj\reports\PORTRAIT_TS2VEC_R1_20261117.json"
OUT2 = OUT1.replace(".json", "_run2.json")
WIN = 30
COARSE_K = 500
TOP_K = 150
FWD = 20
SEED = 20261117
STRIDE = 37
MASK_RATIO = 0.10
CROP = 27
LEVELS = 3
TEMP = 0.2
HIDDEN = 64
DIMS = 64
LR = 1e-3
BATCH = 512
EPOCHS = 10
ENC_BATCH = 4096


def norm01(x):
    x = np.asarray(x, dtype=np.float64)
    lo = x.min()
    hi = x.max()
    rng = hi - lo
    if rng <= 0:
        return None
    return (x - lo) / rng


def build_windows(closes):
    """closes=行序close列表。返回[(row_idx, vec)]，30行连续且close>0且归一化有效。"""
    out = []
    n = len(closes)
    for i in range(WIN - 1, n):
        w = closes[i - WIN + 1:i + 1]
        if any(c is None or c <= 0 for c in w):
            continue
        v = norm01(w)
        if v is None:
            continue
        out.append((i, v))
    return out


def dtw_dist(a, b):
    """自实现DTW, 30x30全带宽DP。a,b=等长float序列。"""
    n = len(a)
    D = np.full((n + 1, n + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, n + 1):
            c = abs(ai - b[j - 1])
            D[i, j] = c + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return D[n, n]


def pctile(xs, q):
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    pos = (n - 1) * q / 100.0
    lo = int(np.floor(pos))
    hi = min(lo + 1, n - 1)
    f = pos - lo
    return xs[lo] + (xs[hi] - xs[lo]) * f


def load_prereg():
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace((' "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b' "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = (fp == prereg["fingerprint"])
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))
    if not fp_ok:
        print("结构化错误: 预注册指纹不匹配, fail-closed exit 2")
        sys.exit(2)
    return prereg, fp


def scan_and_sample(con):
    """Pass A: 全库扫窗+等距抽样训练窗(展平编号%37==0)。返回(train[n,30] float32, nwin)。"""
    t0 = time.time()
    train = []
    nwin = 0
    cur = con.execute("SELECT code,date,close FROM stock_daily ORDER BY code,date")
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        rows = list(grp)
        closes = [r[2] for r in rows]
        wins = build_windows(closes)
        for _w in wins:
            nwin += 1
            if (nwin - 1) % STRIDE == 0:
                train.append(_w[1])
    train_arr = np.asarray(train, dtype=np.float32)
    print("Pass A: 库窗口=%d 训练抽样=%d 耗时%.0fs" % (nwin, len(train_arr), time.time() - t0))
    return train_arr, nwin


class TCN(nn.Module):
    """自实现TS2Vec核心编码器: 膨胀卷积TCN, 输出每时间步DIMS维。"""

    def __init__(self):
        super().__init__()
        self.convs = nn.ModuleList([
            nn.Conv1d(1, HIDDEN, 3, padding=1, dilation=1),
            nn.Conv1d(HIDDEN, HIDDEN, 3, padding=2, dilation=2),
            nn.Conv1d(HIDDEN, HIDDEN, 3, padding=4, dilation=4),
            nn.Conv1d(HIDDEN, DIMS, 3, padding=8, dilation=8),
        ])

    def forward(self, x):
        # x=[B,30] → [B,1,30] → convs(长度保持) → [B,30,DIMS]
        h = x.unsqueeze(1)
        for c in self.convs:
            h = F.relu(c(h))
        return h.transpose(1, 2)


def ntxent(z1, z2, temp=TEMP):
    """NT-Xent, batch内全负对。z1,z2=[B,D]。"""
    z = torch.cat([z1, z2], dim=0)
    z = F.normalize(z, dim=1)
    sim = z @ z.t() / temp
    n = z1.size(0)
    eye = torch.eye(2 * n, dtype=torch.bool, device=z.device)
    pos = torch.zeros(2 * n, 2 * n, dtype=torch.bool, device=z.device)
    unit = torch.eye(n, dtype=torch.bool, device=z.device)
    pos[:n, n:] = unit
    pos[n:, :n] = unit
    sim = sim.masked_fill(eye, -1e9)
    log_prob = sim - torch.logsumexp(sim, dim=1, keepdim=True)
    return -(log_prob * pos).sum() / pos.sum()


def pooled(e):
    """时间维max池化 [B,30,D]→[B,D]。"""
    return e.max(dim=1).values


def hcl(e1, e2):
    """分层对比损失: 全序列级 + 3级(左端锚定裁剪×右端镜像裁剪)。"""
    loss = ntxent(pooled(e1), pooled(e2))
    for i in range(LEVELS):
        a1 = e1[:, i:i + CROP]
        b2 = e2[:, WIN - CROP - i:WIN - i]
        loss = loss + ntxent(pooled(a1), pooled(b2))
        a2 = e2[:, i:i + CROP]
        b1 = e1[:, WIN - CROP - i:WIN - i]
        loss = loss + ntxent(pooled(a2), pooled(b1))
    return loss


def mask_aug(x, dev):
    """时间戳掩码增广: bernoulli等价(rand<0.10)置0。"""
    m = torch.rand(x.shape, device=dev) < MASK_RATIO
    return x.masked_fill(m, 0.0)


def train_encoder(train_arr, dev):
    t0 = time.time()
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    model = TCN().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    X = torch.from_numpy(train_arr)
    n = X.size(0)
    rng = np.random.default_rng(SEED)
    for ep in range(EPOCHS):
        perm = rng.permutation(n)
        tot = 0.0
        nb = 0
        for s in range(0, n, BATCH):
            idx = perm[s:s + BATCH]
            xb = X[idx].to(dev)
            x1 = mask_aug(xb, dev)
            x2 = mask_aug(xb, dev)
            e1 = model(x1)
            e2 = model(x2)
            loss = hcl(e1, e2)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += float(loss.detach())
            nb += 1
        print("epoch %d/%d loss=%.4f" % (ep + 1, EPOCHS, tot / max(nb, 1)))
    print("训练耗时%.0fs" % (time.time() - t0))
    return model


def encode_norm(model, x_np, dev):
    """编码+时间池化+L2归一化 → [N,D] numpy float32。输入[30]自动升维[1,30]。"""
    x = np.asarray(x_np, dtype=np.float32)
    if x.ndim == 1:
        x = x.reshape(1, -1)
    xb = torch.from_numpy(x).to(dev)
    with torch.no_grad():
        e = model(xb)
        z = pooled(e)
        z = F.normalize(z, dim=1)
    return z.cpu().numpy().astype(np.float32)


def heap_push(heap, item, k):
    if len(heap) < k:
        heapq.heappush(heap, item)
    elif item[0] > heap[0][0]:
        heapq.heapreplace(heap, item)


def coarse_scan(model, dev, con, t1_emb, t2_emb, leak_code=None, leak_date=None):
    """Pass B: 全库GPU编码, 同批对双目标算余弦, 各自维护Top-500堆。"""
    t0 = time.time()
    heap1 = []
    heap2 = []
    nwin = 0
    cur = con.execute("SELECT code,date,close FROM stock_daily ORDER BY code,date")
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        rows = list(grp)
        dates = [int(r[1]) for r in rows]
        closes = [r[2] for r in rows]
        wins = build_windows(closes)
        if not wins:
            continue
        nwin += len(wins)
        arrs = np.asarray([w[1] for w in wins], dtype=np.float32)
        idxs = [w[0] for w in wins]
        for s in range(0, len(arrs), ENC_BATCH):
            stop = min(s + ENC_BATCH, len(arrs))
            z = encode_norm(model, arrs[s:stop], dev)  # [B,64] L2归一化
            s1 = z @ t1_emb
            s2 = z @ t2_emb
            ends = [dates[i] for i in idxs[s:stop]]
            m = stop - s
            for heap, sims, tcode, tdate in ((heap1, s1, None, None),
                                             (heap2, s2, leak_code, leak_date)):
                for k in range(m):
                    d_end = ends[k]
                    if tcode is not None and code == tcode and d_end >= tdate:
                        continue
                    heap_push(heap, (float(sims[k]), code, idxs[s + k], d_end), COARSE_K)
    print("Pass B: 编码扫描完成 库窗口=%d 耗时%.0fs" % (nwin, time.time() - t0))
    return heap1, heap2, nwin


def run_target(tname, t_vec, cand, con, nwin):
    """精配: 候选窗DTW Top-150 + 后续20行统计。"""
    t0 = time.time()
    codes = sorted(set(c[1] for c in cand))
    qmarks = ",".join("?" * len(codes))
    rows = con.execute("SELECT code,date,close FROM stock_daily WHERE code IN (%s) "
                       "ORDER BY code,date" % qmarks, codes).fetchall()
    by_code = {}
    for code, grp in itertools.groupby(rows, key=lambda r: r[0]):
        by_code[code] = list(grp)
    refined = []
    for (sim, code, i_end, d_end) in cand:
        cls = [r[2] for r in by_code[code]]
        w = cls[i_end - WIN + 1:i_end + 1]
        v = norm01(w)
        if v is None:
            continue
        dw = dtw_dist(t_vec, v)
        refined.append((dw, code, i_end, d_end, sim))
    refined.sort()
    top = refined[:TOP_K]
    result = {"n_lib_windows": nwin, "coarse_k": COARSE_K, "top_k": TOP_K,
              "top": [], "stats": {}, "era": {},
              "diag": {"zero_followup_count": 0, "zero_followup_share_pct": 0.0}}
    f20s = []
    zcnt = 0
    for (dw, code, i_end, d_end, sim) in top:
        cls = [r[2] for r in by_code[code]]
        tail_end = (i_end == len(cls) - 1)
        if tail_end:
            zcnt += 1
        j = i_end + FWD
        trunc = False
        if j >= len(cls):
            j = len(cls) - 1
            trunc = True
        while j > i_end and (cls[j] is None or cls[j] <= 0):
            j -= 1
        if j <= i_end:
            f20 = None
        else:
            f20 = cls[j] / cls[i_end] - 1.0
        f20s.append(f20)
        result["top"].append({"code": code, "end_date": d_end,
                              "dtw": round(dw, 4),
                              "fwd20_pct": None if f20 is None else round(f20 * 100.0, 4),
                              "truncated": trunc, "tail_end": tail_end})
    vals = [x for x in f20s if x is not None]
    n_v = len(vals)
    if n_v:
        s = sorted(vals)
        result["stats"] = {"n_valid": n_v,
                           "mean_pct": round(sum(vals) / n_v * 100.0, 4),
                           "median_pct": round(pctile(s, 50) * 100.0, 4),
                           "p25_pct": round(pctile(s, 25) * 100.0, 4),
                           "p75_pct": round(pctile(s, 75) * 100.0, 4),
                           "share_gt0_pct": round(sum(1 for x in vals if x > 0) / n_v * 100.0, 2),
                           "share_ge10_pct": round(sum(1 for x in vals if x >= 0.10) / n_v * 100.0, 2),
                           "share_le_n10_pct": round(sum(1 for x in vals if x <= -0.10) / n_v * 100.0, 2)}
    era = {}
    for item in result["top"]:
        if item["fwd20_pct"] is not None:
            yr = str(item["end_date"])[:4]
            era[yr] = era.get(yr, 0) + 1
    result["era"] = {k: era[k] for k in sorted(era)}
    result["diag"]["zero_followup_count"] = zcnt
    result["diag"]["zero_followup_share_pct"] = round(zcnt / max(len(top), 1) * 100.0, 2)
    print("[%s] 精配Top%d后续: %s" % (tname, len(top), result["stats"]))
    print("[%s] era=%s 零后续=%d/%d" % (tname, result["era"], zcnt, len(top)))
    print("[%s] 耗时%.0fs" % (tname, time.time() - t0))
    return result


def main():
    t0 = time.time()
    prereg, fp = load_prereg()
    if not torch.cuda.is_available():
        print("结构化错误: CUDA不可用——本件冻结为GPU确定性口径, 拒绝CPU静默回退(exit 3)")
        sys.exit(3)
    dev = torch.device("cuda")
    con = sqlite3.connect("file:%s?mode=ro" % SRC_DB, uri=True)
    # T1 = 上证指数最后30个交易日
    rows = con.execute("SELECT date, idx_sh_c FROM market_daily WHERE idx_sh_c IS NOT NULL "
                       "ORDER BY date DESC LIMIT 30").fetchall()
    rows.reverse()
    t1 = norm01([float(r[1]) for r in rows])
    t1_end = int(rows[-1][0])
    # T2 = 600519 最后30行close>0
    rows2 = con.execute("SELECT date, close FROM stock_daily WHERE code=600519 AND close>0 "
                        "ORDER BY date DESC LIMIT 30").fetchall()
    rows2.reverse()
    t2 = norm01([float(r[1]) for r in rows2])
    t2_end = int(rows2[-1][0])
    print("目标: T1=上证至%s / T2=600519至%s" % (t1_end, t2_end))
    train_arr, nwin_a = scan_and_sample(con)
    model = train_encoder(train_arr, dev)
    model.eval()
    t1_emb = encode_norm(model, t1, dev)[0]
    t2_emb = encode_norm(model, t2, dev)[0]
    heap1, heap2, nwin = coarse_scan(model, dev, con, t1_emb, t2_emb,
                                     leak_code=600519, leak_date=t2_end)
    r1 = run_target("T1_上证", t1, sorted(heap1), con, nwin)
    r2 = run_target("T2_600519", t2, sorted(heap2), con, nwin)
    con.close()
    n_ok = sum(1 for r in (r1, r2) if r["stats"].get("n_valid", 0) >= 40)
    verdict = "RETRIEVAL_OK" if n_ok == 2 else "DEGENERATE"
    out = {"prereg_fp": fp, "fp_ok": prereg["fingerprint"] == fp,
           "targets": {"T1_sh": r1, "T2_600519": r2},
           "verdict": verdict}
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("判定=%s 已写入 %s 总耗时%.0fs" % (verdict, path, time.time() - t0))


if __name__ == "__main__":
    main()
