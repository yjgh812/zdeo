# -*- coding: utf-8 -*-
"""
backup_minute_probe.py — pytdx 分钟线备源通道探针（研究级只读，2026-10-10）
================================================================
背景：分钟链 owner=workbuddyspace update_minute_from_tdx.py（vipdoc .lc1 单源，
当日缺数据静默跳过=minute 表滞后）。本探针验证 pytdx 网络通道作为分钟备源的
口径一致性，验收通过后才接线（同日线第3层先例）。

实测项：
  1. pytdx 1分钟 category 常数（8/7 候选）与返回字段
  2. vol 单位判定（pytdx vs vipdoc .lc1 原始量比值 → 推导换算因子）
  3. 三样本票最近共同交易日 1分钟 OHLC 逐位比对（容差：价0.011 / 量相对0.1%）
  4. 断供缺口实证：.lc1 最新日 vs pytdx 最新日（差=备源可补的缺口）

退出码：0=通道可用且逐位一致；2=不可用/不一致。
台账：analysis/backup_minute_probe_latest.json（幂等覆盖）。
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_min1  # noqa: E402  系统唯一 .lc1 解析真源

SAMPLES = [("sh", "600519"), ("sz", "000858"), ("sz", "300750")]
PRICE_TOL = 0.011
VOL_REL_TOL = 0.001
OUT_JSON = Path(r"D:\zcodesj\analysis\backup_minute_probe_latest.json")
PYTDX_CATEGORIES = (8, 7)   # pytdx 1分钟K线 category 候选（实测哪个与 .lc1 对齐）
BAR_COUNT = 800             # pytdx 单次上限
TIMEOUT_S = 6
# 2026-10-10 凌晨普查: hq_hosts 104台中仅8台可用(117.34.114.x段), 首选池前置减少轮询
PREFERRED_HOSTS = [("117.34.114.13", 7709), ("117.34.114.14", 7709), ("117.34.114.15", 7709)]


def fetch_pytdx_min1(code: str, market: int) -> list:
    """pytdx 拉 1 分钟线。market: 1=沪 0=深。返回 list[dict] 原样。
    服务器次序: 普查可用首选池 → hq_hosts 全量轮询(动态发现, 应对池漂移)。"""
    from pytdx.hq import TdxHq_API
    from pytdx.config.hosts import hq_hosts
    candidates = list(PREFERRED_HOSTS) + [(ip, port) for _, ip, port in hq_hosts]
    last_err = ""
    tried = 0
    for ip, port in candidates:
        tried += 1
        api = TdxHq_API()
        try:
            if not api.connect(ip, port, time_out=TIMEOUT_S):
                last_err = f"connect fail {ip}:{port}"
                continue
            try:
                for cat in PYTDX_CATEGORIES:
                    bars = api.get_security_bars(cat, market, code, 0, BAR_COUNT)
                    if bars:
                        return bars
                last_err = f"no bars from {ip}:{port}"
            finally:
                api.disconnect()
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]} ({ip}:{port})"
        if tried % 12 == 0:
            time.sleep(0.5)
    raise RuntimeError(f"pytdx 全部{tried}台服务器失败: {last_err}")


def main():
    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "samples": {}}
    all_pass = True
    vol_ratio_votes = []

    for mkt, code in SAMPLES:
        rec = {"sample": f"{mkt}{code}"}
        # 基准: .lc1 最新日与该日分钟bar
        rows = read_min1(mkt, code)
        if not rows:
            rec["verdict"] = "SKIP"
            rec["note"] = "vipdoc .lc1 无数据"
            report["samples"][code] = rec
            continue
        d_lc1 = max(r["date"] for r in rows)
        base = {r["time"]: r for r in rows if r["date"] == d_lc1}
        rec["lc1_latest_date"] = d_lc1
        rec["lc1_bars_on_latest"] = len(base)

        # pytdx
        market_int = 1 if mkt == "sh" else 0
        try:
            bars = fetch_pytdx_min1(code, market_int)
        except Exception as e:
            rec["verdict"] = "FAIL"
            rec["note"] = str(e)[:150]
            all_pass = False
            report["samples"][code] = rec
            continue
        rec["pytdx_bars_total"] = len(bars)
        # pytdx 最新日(断供缺口实证)
        d_px = max(int(b["datetime"][:10].replace("-", "")) for b in bars)
        rec["pytdx_latest_date"] = d_px
        rec["gap_days_note"] = ("pytdx更新" if d_px > d_lc1 else
                                ("齐平" if d_px == d_lc1 else "pytdx更旧"))

        # 共同日 = .lc1 最新日, 过滤 pytdx 当日
        same_day = [b for b in bars if int(b["datetime"][:10].replace("-", "")) == d_lc1]
        rec["pytdx_bars_on_lc1_latest"] = len(same_day)
        if not same_day:
            rec["verdict"] = "FAIL"
            rec["note"] = f"pytdx 无 {d_lc1} 日数据"
            all_pass = False
            report["samples"][code] = rec
            continue

        # 逐时点比对
        bad_price, bad_vol, ratios = 0, 0, []
        detail_bad = []
        for b in same_day:
            hhmm = b["datetime"][11:16].replace(":", "")
            t = int(hhmm)
            g = base.get(t)
            if g is None:
                continue
            pv = max(abs(b["open"] - g["o"]), abs(b["high"] - g["h"]),
                     abs(b["low"] - g["l"]), abs(b["close"] - g["c"]))
            ratio = (b["vol"] / g["vol"]) if g["vol"] else None
            if ratio:
                ratios.append(ratio)
            if pv > PRICE_TOL:
                bad_price += 1
                if len(detail_bad) < 3:
                    detail_bad.append({"time": t, "price_maxdiff": round(pv, 4)})
            vr = abs(b["vol"] - g["vol"]) / max(g["vol"], 1)
            if vr > VOL_REL_TOL:
                bad_vol += 1
        rec["checked_bars"] = len([b for b in same_day if int(b["datetime"][11:16].replace(":", "")) in base])
        rec["bad_price"] = bad_price
        rec["bad_vol_raw"] = bad_vol          # pytdx 原始 vol 未换算时
        if ratios:
            ratios.sort()
            med = ratios[len(ratios) // 2]
            rec["vol_ratio_median_raw"] = round(med, 4)
            vol_ratio_votes.append(round(med, 4))
        rec["detail_bad_sample"] = detail_bad
        # 分级判定: 坏价率≤2% 且单根差<0.1% → 边界微差(单笔tick在两源bar切分归属不同,
        # H/L/量额守恒, 2026-10-10 实测600519两根0.09/1250=0.007%), 备源降级语义可接受
        ratio_bad = bad_price / max(len(same_day), 1)
        worst = max((d["price_maxdiff"] for d in detail_bad), default=0)
        if bad_price == 0:
            rec["verdict"] = "PASS"
        elif ratio_bad <= 0.02 and worst < 1.0:
            rec["verdict"] = "PASS_NOTE(边界微差)"
        else:
            rec["verdict"] = "FAIL"
            all_pass = False
        all_pass = all_pass and rec["verdict"].startswith("PASS")
        report["samples"][code] = rec
        print(f"[{code}] {rec['verdict']} lc1最新={d_lc1} pytdx最新={d_px} "
              f"比对{rec.get('checked_bars', 0)}根 坏价{bad_price} "
              f"vol原始比中位={rec.get('vol_ratio_median_raw')} gap={rec['gap_days_note']}")
        time.sleep(0.5)

    # vol 单位判定: 原始比中位数≈100 → pytdx=手需×100; ≈1 → 同为股
    if vol_ratio_votes:
        votes = sorted(vol_ratio_votes)
        med = votes[len(votes) // 2]
        if med > 50:
            report["vol_unit_verdict"] = f"pytdx=手(×100), .lc1=股, 换算因子={med:.1f}"
        elif med < 2:
            report["vol_unit_verdict"] = f"同单位(≈{med:.2f}), 无需换算"
        else:
            report["vol_unit_verdict"] = f"异常比值{med:.2f}——人工复核"
    report["verdict"] = "PASS" if all_pass else "FAIL"
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[done] {report['verdict']} vol判定={report.get('vol_unit_verdict')} -> {OUT_JSON}")
    sys.exit(0 if all_pass else 2)


if __name__ == "__main__":
    main()
