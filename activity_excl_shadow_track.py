# -*- coding: utf-8 -*-
"""ACT-EXCL-V1 资金活跃度负面清单 · 影子跟踪器(直跑制, 幂等)
prereg: data/prereg_activity_excl_20260924.json (指纹见文件)
规则: 信号日t收盘, 小石cn-daily截面换手率前10%(base_ok宇宙) → 禁买排除;
影子观察: 被剔组 vs 保留组 等权20交易日收益差(下一交易日收盘进, T+1)。
用法: python activity_excl_shadow_track.py [--refresh]
  --refresh: 先调 xiaoshi-data download 刷新本地2026年文件(需网络与密钥env)
输出: data/activity_excl_shadow_hits.jsonl (每信号日一行, 含被剔代码)
      data/activity_excl_shadow_settled.jsonl (满20交易日结算差值)
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

DATA_DIR = r"D:\zcodesj\xiaoshi\official\data"
HITS = r"D:\zcodesj\data\activity_excl_shadow_hits.jsonl"
SETTLED = r"D:\zcodesj\data\activity_excl_shadow_settled.jsonl"
SHADOW_START = "2026-09-22"
HOLD = 20
TOP_FRAC = 0.10
EXE = r"D:\zcodesj\xiaoshi\official\venv\Scripts\xiaoshi-data.exe"


def load_jsonl(p):
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def append_jsonl(p, obj):
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main():
    if "--refresh" in sys.argv:
        env = dict(os.environ)
        try:
            cfg = json.load(open(r"C:\Users\Administrator\.zcode\cli\config.json", encoding="utf-8"))
            env["XIAOSHI_API_KEY"] = cfg["mcp"]["servers"]["xiaoshi"]["env"]["XIAOSHI_API_KEY"]
            r = subprocess.run([EXE, "download", "--dataset", "cn-daily", "--adjust", "qfq",
                                "--year", "2026", "--data-dir", DATA_DIR],
                               env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
            print("refresh rc:", r.returncode, (r.stderr or "")[-120:].replace("\n", " "))
        except Exception as ex:
            print("refresh_failed_continue_with_local:", repr(ex)[:200])

    files = sorted(glob.glob(os.path.join(DATA_DIR, "**", "*.parquet"), recursive=True))
    cols = ["symbol", "trade_date", "close", "turnover_pct", "amount", "is_suspended"]
    frames = []
    for f in files:
        import pyarrow.parquet as pq
        use = [c for c in cols if c in pq.ParquetFile(f).schema_arrow.names]
        frames.append(pd.read_parquet(f, columns=use))
    data = pd.concat(frames, ignore_index=True)
    data["code"] = data["symbol"].astype(str).str.extract(r"(\d{6})")[0]
    data = data[data["code"].str.startswith(("60", "00", "30"), na=False)].copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    data = data.sort_values(["code", "trade_date"])
    s = data["is_suspended"]
    if not pd.api.types.is_bool_dtype(s):
        s = s.astype(str).str.lower().isin(("true", "1", "1.0", "yes"))
    data["susp_flag"] = s.astype(bool)

    close = data.pivot_table(index="trade_date", columns="code", values="close", aggfunc="last")
    dates = close.index
    amt = data.pivot_table(index="trade_date", columns="code", values="amount", aggfunc="last").reindex(index=dates, columns=close.columns)
    to = data.pivot_table(index="trade_date", columns="code", values="turnover_pct", aggfunc="last").reindex(index=dates, columns=close.columns)
    susp = data.pivot_table(index="trade_date", columns="code", values="susp_flag", aggfunc="max").reindex(index=dates, columns=close.columns).fillna(True).astype(bool)

    listed = close.notna().cumsum() + close.iloc[0].notna() * 60
    sig_ok = close.notna() & (~susp) & (amt > 0) & to.notna() & (listed >= 60)  # 建清单只用当日信息
    base_ok = sig_ok & (~susp.shift(-1).fillna(True)) & (listed >= 60)
    fwd20 = close.shift(-(1 + HOLD)) / close.shift(-1) - 1
    ok20 = base_ok & fwd20.notna() & (~susp.shift(-(1 + HOLD)).fillna(True))
    print("panel:", close.shape, dates[0].date(), "->", dates[-1].date())

    hits = load_jsonl(HITS)
    settled = load_jsonl(SETTLED)
    hit_days = {h["signal_date"] for h in hits}
    settled_days = {s["signal_date"] for s in settled}
    pos = {d: i for i, d in enumerate(dates)}

    # 1) 新信号日入账(被剔代码清单固化, 结算不依赖重算)
    # 换手率源滞后: 用"最近一个换手率可用的交易日"截面建清单, 如实记录滞后天数
    avail_idx = [i for i in range(len(dates)) if int(to.iloc[i].notna().sum()) >= 100]
    import bisect
    new_hits = 0
    for i in range(len(dates)):
        d = str(dates[i].date())
        if d < SHADOW_START or d in hit_days:
            continue
        j = bisect.bisect_right(avail_idx, i) - 1
        if j < 0:
            continue
        k = avail_idx[j]                      # 换手率最近可用日
        lag = i - k
        m = sig_ok.iloc[k]
        if m.sum() < 100:
            continue
        torow = to.iloc[k]
        xr = torow[m].rank(pct=True)
        excl = sorted(xr[xr >= (1 - TOP_FRAC)].index.tolist())
        kept = sorted(xr[xr < (1 - TOP_FRAC)].index.tolist())
        append_jsonl(HITS, {"signal_date": d, "asof_date": str(dates[k].date()),
                            "lag_days": lag, "n_excl": len(excl), "n_kept": len(kept),
                            "excl_codes": ",".join(excl), "kept_codes": ",".join(kept)})
        hit_days.add(d)
        new_hits += 1
    print("new_hits:", new_hits)

    # 2) 满20个交易日结算差值
    new_settled = 0
    for h in hits:
        d = h["signal_date"]
        if d in settled_days or d not in pos:
            continue
        i = pos[d]
        if i + 1 + HOLD >= len(dates):
            continue  # 未到期
        codes_ex = h["excl_codes"].split(",") if h["excl_codes"] else []
        codes_kp = h["kept_codes"].split(",") if h["kept_codes"] else []
        f = fwd20.iloc[i]
        m20 = ok20.iloc[i]
        ex = f.reindex(codes_ex)[m20.reindex(codes_ex).fillna(False)]
        kp = f.reindex(codes_kp)[m20.reindex(codes_kp).fillna(False)]
        if len(ex.dropna()) < 20 or len(kp.dropna()) < 100:
            continue
        spread = float(ex.mean() - kp.mean())
        append_jsonl(SETTLED, {"signal_date": d,
                               "settle_date": str(dates[i + 1 + HOLD].date()),
                               "fwd20_excl_pct": round(float(ex.mean()) * 100, 3),
                               "fwd20_kept_pct": round(float(kp.mean()) * 100, 3),
                               "spread_pp": round(spread * 100, 3),
                               "n_excl_used": int(len(ex.dropna())),
                               "n_kept_used": int(len(kp.dropna()))})
        settled_days.add(d)
        new_settled += 1
    print("new_settled:", new_settled)

    # 3) 期中读数
    se = load_jsonl(SETTLED)
    if se:
        sp = [x["spread_pp"] for x in se]
        print(f"settled_n={len(se)} mean_spread_pp={np.mean(sp):.3f} "
              f"neg_share={np.mean([x < 0 for x in sp]):.2f}")
    else:
        print("settled_n=0 (观察窗口未满,属预期)")


if __name__ == "__main__":
    main()
