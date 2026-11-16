# -*- coding: utf-8 -*-
"""
backup_daily_probe.py — 备用日线数据源探针（研究级只读，2026-10-08）
================================================================
背景：B站数据源专项研究（tmp/bili_datasource_20261008/）后，为日K数据链
（tickflow→vipdoc 双通道）增加第三层备用源。本探针对备源做逐位一致性验收，
验收通过前不接线生产链（接线须主人裁决）。

通道地图（2026-10-08 实弹状态）：
  主源   vipdoc   本地通达信 .day（tdx_reader 统一读取器）
  备1    baostock 官方库；不复权原始价已与 vipdoc 逐位一致
         ⚠️ 仅限不复权(adjustflag=3)原始价；其复权因子=涨跌幅算法，口径
            与 qfq 库（通达信×新浪因子）不同，严禁用于 qfq 库修正
  备2    tushare  系统 token（env→config/tushare_token.txt）；daily 原始价
  备3    腾讯     web.ifzq.gtimg.cn 无 token HTTP；vol 单位=手（×100 转股）
  观测   东财     push2his 本机 RemoteDisconnected（B站评论同证封控风险），
                  efinance 库同源同命，仅记录不作为备源

口径契约（单一真源=本文件头）：
  备源一律只取【不复权原始价】；复权仍走系统自有新浪 qfq 因子链。
  备源补位时，量字段换算：腾讯 手→股 ×100；tushare vol 手→股 ×100、
  amount 千元→元 ×1000；baostock amount 元原样。

用法（工作目录 D:\\zcodesj）：
  python analysis/backup_daily_probe.py            # 默认样本 3 票 × 最近 5 日
  python analysis/backup_daily_probe.py --days 10
退出码：0=≥1 条备源全部样本通过；2=所有备源失败（fail-closed）；
        3=vipdoc 主源自身不可用（基准缺失，探测无效）。
台账：analysis/backup_daily_probe_latest.json（每次覆盖，幂等）。
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day  # noqa: E402  系统统一 vipdoc 读取器（唯一 .day 解析真源）

SAMPLES = [("sh", "600519"), ("sz", "000858"), ("sz", "300750")]
DAYS = 5
PRICE_TOL = 0.011        # 两位小数价格精度容差
VOL_REL_TOL = 0.001      # 成交量相对容差 0.1%
AMT_REL_TOL = 0.005      # 成交额相对容差 0.5%（float32 + 各源口径微差）
OUT_JSON = Path(r"D:\zcodesj\analysis\backup_daily_probe_latest.json")
TUSHARE_TOKEN_FILE = Path(r"D:\zcodesj\config\tushare_token.txt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"


def _tushare_token() -> str:
    t = os.environ.get("TUSHARE_TOKEN", "").strip()
    if t:
        return t
    if TUSHARE_TOKEN_FILE.is_file():
        return TUSHARE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def fetch_baostock(market: str, code: str):
    import baostock as bs
    full = f"{market}.{code}"
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"login fail: {lg.error_msg}")
    try:
        rs = bs.query_history_k_data_plus(
            full, "date,open,high,low,close,volume,amount",
            start_date="", end_date="", frequency="d", adjustflag="3")
        if rs.error_code != "0":
            raise RuntimeError(f"query fail: {rs.error_msg}")
        rows = []
        while rs.error_code == "0" and rs.next():
            r = rs.get_row_data()
            rows.append({"date": r[0].replace("-", ""), "open": float(r[1]),
                         "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
                         "volume": float(r[5]), "amount": float(r[6])})
        return rows[-DAYS:]
    finally:
        bs.logout()


def fetch_tushare(market: str, code: str):
    import tushare as ts
    token = _tushare_token()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN missing (env→config/tushare_token.txt)")
    pro = ts.pro_api(token)
    df = pro.daily(ts_code=f"{code}.{market.upper()}", start_date="", end_date="")
    if df is None or df.empty:
        raise RuntimeError("daily empty")
    df = df.sort_values("trade_date").tail(DAYS)
    return [{"date": str(r.trade_date), "open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close),
             "volume": float(r.vol) * 100.0, "amount": float(r.amount) * 1000.0}
            for r in df.itertuples()]


def fetch_tencent(market: str, code: str):
    r = requests.get(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={"param": f"{market}{code},day,,,{DAYS},"},
        headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=15)
    j = r.json()
    d = (j.get("data") or {}).get(f"{market}{code}") or {}
    rows = d.get("day") or d.get("qfqday") or []
    if not rows:
        raise RuntimeError("no kline rows")
    out = []
    for it in rows:
        # [date, open, close, high, low, volume(手), ...]
        out.append({"date": it[0].replace("-", ""), "open": float(it[1]),
                    "close": float(it[2]), "high": float(it[3]), "low": float(it[4]),
                    "volume": float(it[5]) * 100.0,
                    "amount": float(it[6]) if len(it) > 6 and it[6] else 0.0})
    return out[-DAYS:]


def fetch_eastmoney(market: str, code: str):
    secid = ("1." if market == "sh" else "0.") + code
    r = requests.get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={"secid": secid, "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56",
                "klt": "101", "fqt": "0", "end": "20500101", "lmt": str(DAYS)},
        headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=15)
    kl = ((r.json().get("data") or {}).get("klines")) or []
    if not kl:
        raise RuntimeError("no klines")
    out = []
    for line in kl:
        p = line.split(",")
        out.append({"date": p[0].replace("-", ""), "open": float(p[1]), "close": float(p[2]),
                    "high": float(p[3]), "low": float(p[4]),
                    "volume": float(p[5]), "amount": float(p[6])})
    return out


CHANNELS = [
    ("baostock", fetch_baostock),
    ("tushare", fetch_tushare),
    ("tencent", fetch_tencent),
    ("eastmoney", fetch_eastmoney),
]


def compare(name: str, base_rows: list, got_rows: list) -> dict:
    """逐位比对最近 DAYS 日（以 vipdoc 日期为准对齐）。返回该样本比对明细。"""
    base = {r[0]: r for r in base_rows}
    got = {int(str(r["date"]).replace("-", "")): r for r in got_rows}
    detail, bad = [], 0
    for d in sorted(base)[-DAYS:]:
        b = base[d]
        g = got.get(d)
        if g is None:
            detail.append({"date": d, "verdict": "MISS"})
            bad += 1
            continue
        pv = max(abs(g["open"] - b[1]), abs(g["high"] - b[2]),
                 abs(g["low"] - b[3]), abs(g["close"] - b[4]))
        vv = abs(g["volume"] - b[6]) / max(b[6], 1)
        ok = pv <= PRICE_TOL and vv <= VOL_REL_TOL
        if not ok:
            bad += 1
        detail.append({"date": d, "price_maxdiff": round(pv, 4),
                       "vol_reldiff": round(vv, 6), "verdict": "OK" if ok else "DIFF"})
    return {"sample": name, "checked": len(detail), "bad": bad,
            "verdict": "PASS" if bad == 0 else "FAIL", "detail": detail}


def main():
    global DAYS
    days_arg = DAYS
    if "--days" in sys.argv:
        days_arg = int(sys.argv[sys.argv.index("--days") + 1])
    DAYS = days_arg

    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "samples": [f"{m}{c}" for m, c in SAMPLES],
              "days": DAYS, "channels": {}}

    # 主源基准
    base = {}
    for m, c in SAMPLES:
        rows = read_day(m, c)
        if len(rows) < DAYS:
            print(f"[FATAL] vipdoc 基准缺失: {m}{c} rows={len(rows)} < {DAYS}（主源不可用，探测无效）")
            report["base"] = "vipdoc_unavailable"
            OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            sys.exit(3)
        base[(m, c)] = rows
    report["base"] = "vipdoc_ok"
    latest = base[SAMPLES[0]][-1][0]
    print(f"[base] vipdoc OK, 最新交易日={latest}, 样本={report['samples']}, 窗口={DAYS}日\n")

    for ch_name, fn in CHANNELS:
        ch = {"samples": []}
        all_pass = True
        for m, c in SAMPLES:
            try:
                got = fn(m, c)
                if not got:
                    raise RuntimeError("empty rows")
                res = compare(f"{m}{c}", base[(m, c)], got)
            except Exception as e:
                res = {"sample": f"{m}{c}", "verdict": "ERROR",
                       "error": f"{type(e).__name__}: {str(e)[:160]}"}
                all_pass = False
            ch["samples"].append(res)
            if res["verdict"] != "PASS":
                all_pass = False
            tag = res["verdict"]
            extra = res.get("error", "") or ""
            print(f"[{ch_name:9s}] {res['sample']} {tag} {extra}")
            time.sleep(0.4)
        ch["verdict"] = "PASS" if all_pass else "FAIL"
        report["channels"][ch_name] = ch
        print(f"[{ch_name:9s}] === {ch['verdict']} ===\n")

    n_pass = sum(1 for ch in report["channels"].values() if ch["verdict"] == "PASS")
    report["summary"] = f"{n_pass}/{len(CHANNELS)} channels pass"
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] {report['summary']} -> {OUT_JSON}")
    sys.exit(0 if n_pass >= 1 else 2)


if __name__ == "__main__":
    main()
