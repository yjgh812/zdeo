r"""rbot_oos_review_20261114.py — 圆弧底真窗 OOS 复核（协议=GATE_ROUND-BOTTOM_20260902 commitment）。
口径=预注册原尺零改动: 检测/尺子/ST时点过滤零复制复用 classical_pattern_prescreen; 真窗=信号日>20260825;
双判据(边界§3主人9/2终裁): 无条件N10均值与 ret20同日同五分位桶残差 都正才可提请立项; 仅均值正=动量延续不接线; 均值负=归档禁复活。
"""
import hashlib
import json
import math
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, r"D:\zcodesj")
import classical_pattern_prescreen as cp  # noqa: E402

cp.SIG_END = "20261113"  # OOS复核窗数据延展(仅本复核读数, 不改预筛器源码/判据)
OOS_AFTER = 20260825     # 真窗=信号日>20260825 (oos_lock保留段)
OUT_JSON = r"D:\zcodesj\reports\RBOT_OOS_REVIEW_20261114.json"
OUT_MD = r"D:\zcodesj\reports\RBOT_OOS_REVIEW_20261114.md"


def main():
    t0 = time.time()
    uni = cp.load_universe()
    df = cp.load_wide(uni)
    print("universe=%d rows=%d" % (len(uni), len(df)))

    oos_trades = []      # (code, date, n10, ret20)
    panel = defaultdict(list)  # date -> [(ret20, n10, is_sig)]
    ndates = set()
    for code, g in df.groupby("code", sort=True):
        c = g["close"].to_numpy(dtype=float)
        o = g["open"].to_numpy(dtype=float)
        h = g["high"].to_numpy(dtype=float)
        l = g["low"].to_numpy(dtype=float)
        v = g["volume"].to_numpy(dtype=float)
        d = g["date"].to_numpy()
        n = len(c)
        rs = cp.rolling_signals(g)
        sig_idx = set(cp.cooldown(np.flatnonzero(rs["rbot"])))
        panel_dates = [i for i in range(20, n) if int(str(d[i])) > OOS_AFTER]
        for t in range(20, n):
            dt = int(str(d[t]))
            if dt <= OOS_AFTER:
                continue
            ndates.add(dt)
            if c[t] is None or (isinstance(c[t], float) and np.isnan(c[t])) or c[t] <= 0:
                continue
            p20 = c[t - 20] if c[t - 20] is not None else np.nan
            ret20 = c[t] / p20 - 1.0 if (isinstance(p20, float) and not np.isnan(p20) and p20 > 0) else np.nan
            is_sig = t in sig_idx and not cp.st_hit(str(code), str(d[t]))
            r = cp.ruler(code, o, h, l, c, v, t)
            n10 = None
            if isinstance(r, dict) and r.get("status") == "ok":
                ex = r["exits"].get(10)
                if ex is not None:
                    n10 = ex["ret"]
            row = (ret20, n10, is_sig)
            panel[dt].append(row)
            if t in sig_idx and not cp.st_hit(str(code), str(d[t])) and n10 is not None and not (isinstance(ret20, float) and np.isnan(ret20)):
                oos_trades.append((str(code), dt, n10, ret20))
    print("OOS信号交易日%d | OOS成交信号%d | panel行%d" % (len(ndates), len(oos_trades), sum(len(v) for v in panel.values())))

    # ---- 无条件均值 ----
    xs = np.array([r[2] for r in oos_trades])
    mu = float(xs.mean())
    sd = float(xs.std(ddof=1))
    t_stat = mu / (sd / math.sqrt(len(xs))) if sd > 0 and len(xs) > 1 else 0.0
    win = float((xs > 0).mean())
    by_month = defaultdict(list)
    for _, dt, n10, _ in oos_trades:
        by_month[str(dt)[:6]].append(n10)
    bym = {m: {"n": len(v), "mean": round(float(np.mean(v)) * 100, 3),
               "win": round(float(np.mean([x > 0 for x in v])) * 100, 1)} for m, v in sorted(by_month.items())}

    # ---- ret20 同日同五分位桶残差(9/2匹配对照法) ----
    residuals = []
    for dt, rows in panel.items():
        valid = [r for r in rows if isinstance(r[0], float) and not np.isnan(r[0])
                 and r[1] is not None]
        if len(valid) < 50:
            continue
        arr = sorted(valid, key=lambda r: r[0])
        m = len(arr)
        q_of = {}
        for qi in range(5):
            lo = int(m * qi / 5)
            hi = int(m * (qi + 1) / 5)
            for j in range(lo, hi):
                q_of[id(arr[j])] = qi
        ctrl = defaultdict(list)
        sigq = {}
        for r in arr:
            qi = q_of[id(r)]
            if r[2]:
                sigq[id(r)] = (qi, r[1])
            else:
                ctrl[qi].append(r[1])
        for sig_key, (qi, n10) in sigq.items():
            cl = ctrl.get(qi)
            if cl and len(cl) >= 10:
                residuals.append(n10 - float(np.mean(cl)))
    rarr = np.array(residuals) if residuals else np.array([np.nan])
    rmu = float(np.nanmean(rarr)) if len(residuals) else None
    rsd = float(np.nanstd(rarr, ddof=1)) if len(residuals) > 1 else None
    rt = (rmu / (rsd / math.sqrt(len(residuals)))) if (rsd and len(residuals) > 1) else None

    # ---- 双判据裁决 ----
    cond_mean = mu > 0
    cond_resid = (rmu is not None and rmu > 0)
    if cond_mean and cond_resid:
        verdict = "双正=可提请立项(供主人裁决)"
    elif cond_mean and not cond_resid:
        verdict = "仅均值正残差负=动量延续载体, 不接线"
    else:
        verdict = "均值负=归档禁复活"

    out = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "protocol": "GATE_ROUND-BOTTOM_20260902 commitment + 边界§3主人9/2终裁双判据",
        "oos_after": OOS_AFTER, "n_oos_signal_days": len(ndates),
        "unconditional": {"n": int(len(xs)), "mean_pct": round(mu * 100, 3),
                          "t": round(t_stat, 2), "win_pct": round(win * 100, 1),
                          "by_month": bym},
        "residual_ret20_bucket": {"n": len(residuals), "mean_pct": None if rmu is None else round(rmu * 100, 3),
                                  "t": None if rt is None else round(rt, 2)},
        "dual_criterion": {"mean_positive": cond_mean, "residual_positive": cond_resid},
        "verdict": verdict,
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    md = ["# 圆弧底真窗 OOS 复核 · 2026-11-14", "",
          "> 协议=GATE_ROUND-BOTTOM_20260902 commitment（真窗=信号日>20260825，≥60交易日复核，11-14在窗口'前后'内，样本期为截至11-13的可完成N10部分）",
          "> 口径=预注册原尺零改动（检测/尺子/ST时点过滤零复制复用预筛器）+ 边界§3主人9/2终裁双判据", "",
          "| 指标 | 读数 |", "|---|---|",
          f"| OOS信号交易日 | {len(ndates)} |",
          f"| 无条件 N10（费后，n={len(xs)}） | {mu*100:+.3f}%（t={t_stat:.2f}，胜率{win*100:.1f}%） |",
          f"| ret20同桶残差（n={len(residuals)}） | {rmu*100:+.3f}%（t={rt:.2f}） |", "",
          "| 月 | n | 均值% | 胜率% |", "|---|---|---|---|"]
    for m_, s in sorted(bym.items()):
        md.append(f"| {m_} | {s['n']} | {s['mean']:+.3f} | {s['win']:.1f} |")
    md += ["", f"## 双判据：无条件均值正={cond_mean}，ret20同桶残差正={cond_resid}",
           f"## 判定：**{verdict}**", "",
           "## 供主人裁决的三岔口",
           "- 双正→可提请立项（走白名单/信号源接线评估）",
           "- 仅均值正→动量延续载体，不接线（与9/2正交检查同结论）",
           "- 均值负→归档禁复活"]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))
    print(json.dumps({k: out[k] for k in ("unconditional", "residual_ret20_bucket", "verdict")},
                     ensure_ascii=False))
    print("elapsed %.0fs -> %s" % (time.time() - t0, OUT_MD))


if __name__ == "__main__":
    main()
