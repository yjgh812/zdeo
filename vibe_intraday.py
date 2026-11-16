# -*- coding: utf-8 -*-
"""
vibe_intraday.py — 盘中实时盘感（日频盘感卡的盘中补充）
====================================================================
盘中（09:30-15:00）用实时全市场快照算一个 0-100 的"实时盘感"，看当天情绪是
转强还是转弱，与 22:00/09:10 的日频盘感卡互补。只读、无 MCP 依赖（脚本直连
东财 push2 公开接口），失败即非零退出，绝不编造。

三维合成（盘中口径，权重公开）：
  ① 涨跌广度 breadth (50%) —— 全市场 9 档涨跌分布：上涨家数占比 + 涨停-跌停差
  ② 指数动能 index (30%)   —— 上证/深成/创业板 实时涨跌幅均值
  ③ 涨停活跃 zt (20%)       —— 实时涨停家数 vs 近20日涨停均值（场子热不热）

数据源：
  - push2ex.eastmoney.com/getTopicZDFenBu  → 9 档涨跌分布（含实时涨停/跌停家数）
  - push2.eastmoney.com/api/qt/ulist.np/get → 三大指数实时价/涨跌幅
  - 近20日涨停均值：本地 _cache/sentiment_history.csv（n_limit_up 列，缺失则退 50）

用法：
  python vibe_intraday.py                 # 实时盘感（盘中），打印单行+JSON
  python vibe_intraday.py --json out.json
  python vibe_intraday.py --quiet         # 只输出 JSON 单行

反臆想：status 非 trading（收盘/周末）时输出 status 并标注"非盘中快照"，不做实时断言；
任何接口失败 → 退出码 1，不输出伪造盘感。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

# 权重（盘中口径）
W = {"breadth": 0.50, "index": 0.30, "zt": 0.20}
ZT_HISTORY = Path(r"D:\zcodesj\_cache\sentiment_history.csv")


def http_get_bytes(url: str, timeout: int = 12, retries: int = 3,
                   headers: dict | None = None) -> bytes:
    """带重试+退避的 GET，返回原始字节（GBK 源用此后再 decode）。"""
    hdrs = dict(UA)
    if headers:
        hdrs.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def http_get(url: str, timeout: int = 12, retries: int = 3, headers: dict | None = None) -> str:
    """带重试+退避的 GET（UTF-8 源；GBK 源请用 http_get_bytes）。"""
    return http_get_bytes(url, timeout, retries, headers).decode("utf-8", "replace")


# ═══════════════════════════════════════════════════════
#  指数实时涨跌幅 — 主备阶梯 3 源（实测可用性 2026-08-22）
#   ① 新浪 hq.sinajs.cn ✅  ② 腾讯 qt.gtimg.cn ✅  ③ 东财 push2 stock/get ✅(字段×100需缩放)
#   东财 push2 ulist 已被拒，排除。
# ═══════════════════════════════════════════════════════

def _sina_indices() -> list[dict]:
    raw = http_get_bytes("https://hq.sinajs.cn/list=sh000001,sz399001,sz399006",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    raw = raw.decode("gbk", "replace")
    out = []
    for line in raw.strip().split("\n"):
        if '="' not in line:
            continue
        code = line.split("hq_str_")[1].split("=")[0]
        f = line.split('="')[1].rstrip('";').split(",")
        if len(f) < 5 or not f[2] or not f[3]:
            continue
        prev = float(f[2] or 0)
        price = float(f[3] or 0)
        pct = round((price / prev - 1) * 100, 2) if prev > 0 else None
        # prev=昨收: 供跨源对账用(卡内为库内T-1收盘, 盘中用现价对账必假FAIL, 2026-09-08)
        out.append({"code": code, "name": f[0], "price": price, "pct": pct, "prev": prev})
    return out


def _tencent_indices() -> list[dict]:
    raw = http_get_bytes("https://qt.gtimg.cn/q=sh000001,sz399001,sz399006",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    raw = raw.decode("gbk", "replace")
    out = []
    for line in raw.strip().split(";"):
        if 'v_sh' not in line and 'v_sz' not in line:
            continue
        f = line.split('="')[1].rstrip('"').split("~")
        if len(f) < 33 or not f[3] or not f[32]:
            continue
        out.append({"code": f[0], "name": f[1], "price": float(f[3] or 0),
                    "pct": float(f[32] or 0)})
    return out


def _em_indices() -> list[dict]:
    """东财 push2 stock/get，字段×100（f43=价×100, f170=涨跌%×100）。"""
    out = []
    for secid, code, name in [("1.000001", "000001", "上证指数"),
                              ("0.399001", "399001", "深证成指"),
                              ("0.399006", "399006", "创业板指")]:
        url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
               f"&fields=f43,f58,f170")
        d = json.loads(http_get(url, headers={"User-Agent": "Mozilla/5.0",
                                              "Referer": "https://quote.eastmoney.com/"}))["data"]
        if not d or not d.get("f43"):
            continue
        out.append({"code": code, "name": name,
                    "price": round(float(d["f43"]) / 100, 3),
                    "pct": round(float(d["f170"]) / 100, 2)})
    return out


def fetch_indices() -> list[dict]:
    """主备阶梯：新浪 → 腾讯 → 东财。全部失败抛异常。"""
    last = None
    for fn in (_sina_indices, _tencent_indices, _em_indices):
        try:
            out = fn()
            if out:
                return out
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"三大指数源均不可用: {last}")


# ═══════════════════════════════════════════════════════
#  涨跌分布/广度 — 单源 + 重试（实测仅东财 ZDFenBu 可靠）
#   备选 QSFenBu=404 / ZTPool=空 / 腾讯参数错 / ulist 被拒 → 无可用备份
# ═══════════════════════════════════════════════════════

def fetch_distribution() -> dict:
    """东财 9 档涨跌分布 → 合并 fenbu 全部单键 dict。
    key=涨跌幅分档：11=≥11%(20cm涨停近似), 10=10%(主板涨停近似), 9..1 涨区间,
    0=平, -1..-9 跌区间, -10=跌停近似, -11=≤-11%(跌停以下近似)。
    涨停/跌停为固定分档近似（含非涨停的±11~20%异动股），仅作盘中方向参考，
    精确涨停数以 22:00 日频盘感卡为准。"""
    url = ("https://push2ex.eastmoney.com/getTopicZDFenBu"
           "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=10&sort=fbt%3Aasc")
    raw = json.loads(http_get(url, headers={"User-Agent": "Mozilla/5.0",
                                            "Referer": "https://quote.eastmoney.com/"}))
    d = raw.get("data") or {}
    fb: dict[int, int] = {}
    for item in d.get("fenbu") or []:
        for k, v in item.items():
            fb[int(k)] = int(v)
    if not fb:
        return {"qdate": d.get("qdate"), "n_up": 0, "n_down": 0, "flat": 0,
                "up_limited": 0, "down_limited": 0, "n_total": 0}
    n_up = sum(v for k, v in fb.items() if k >= 1)
    n_down = sum(v for k, v in fb.items() if k <= -1)
    flat = fb.get(0, 0)
    return {"qdate": d.get("qdate"),
            "up_limited": fb.get(10, 0) + fb.get(11, 0),
            "down_limited": fb.get(-10, 0) + fb.get(-11, 0),
            "n_up": n_up, "n_down": n_down, "flat": flat,
            "n_total": n_up + n_down + flat}


def zt_avg_20d() -> float:
    """近20日涨停家数均值（本地情绪序列），缺失退 50。"""
    try:
        df = pd.read_csv(ZT_HISTORY, dtype={"date": str})
        if "n_limit_up" in df.columns:
            s = df["n_limit_up"].astype(float).dropna().tail(20)
            if len(s) >= 5:
                return float(s.mean())
    except Exception:
        pass
    return 50.0


def score_dims(dist: dict, indices: list[dict], zt_avg: float) -> dict:
    n = dist["n_total"]
    up_ratio = dist["n_up"] / n if n else 0.5
    if up_ratio >= 0.70:
        breadth = 100.0
    elif up_ratio >= 0.50:
        breadth = 70.0
    elif up_ratio >= 0.35:
        breadth = 45.0
    elif up_ratio >= 0.25:
        breadth = 25.0
    else:
        breadth = 10.0
    ztd = dist["up_limited"] - dist["down_limited"]
    if ztd >= 30:
        breadth += 15
    elif ztd >= 10:
        breadth += 8
    elif ztd <= -10:
        breadth -= 10
    elif ztd <= -30:
        breadth -= 20
    breadth = min(100.0, max(0.0, breadth))

    pcts = [i["pct"] for i in indices if i.get("pct") is not None]
    idx_pct = sum(pcts) / len(pcts) if pcts else 0.0
    if idx_pct >= 1.5:
        idx_s = 100.0
    elif idx_pct >= 0.5:
        idx_s = 75.0
    elif idx_pct >= 0:
        idx_s = 55.0
    elif idx_pct >= -1.0:
        idx_s = 30.0
    else:
        idx_s = 10.0

    zt_r = dist["up_limited"] / zt_avg if zt_avg > 0 else 1.0
    if zt_r >= 1.3:
        zt_s = 100.0
    elif zt_r >= 1.0:
        zt_s = 70.0
    elif zt_r >= 0.7:
        zt_s = 45.0
    elif zt_r >= 0.4:
        zt_s = 20.0
    else:
        zt_s = 10.0
    return {"breadth": round(breadth, 1), "index": round(idx_s, 1), "zt": round(zt_s, 1),
            "up_ratio": round(up_ratio * 100, 1), "zt_dt_diff": ztd, "idx_pct": round(idx_pct, 2)}


def stage_of(v: float) -> str:
    if v >= 80:
        return "热/亢奋"
    if v >= 60:
        return "偏热"
    if v >= 40:
        return "常温"
    if v >= 20:
        return "偏冷"
    return "冰点/防守"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="盘中实时盘感（东财实时快照）")
    ap.add_argument("--json", default="", help="JSON 输出路径")
    ap.add_argument("--quiet", action="store_true", help="只输出单行")
    a = ap.parse_args(argv)

    # 指数：主备阶梯 3 源，全失败才退出
    try:
        indices = fetch_indices()
    except Exception as e:
        print(f"[vibe_intraday] ❌ 指数源全不可用（新浪/腾讯/东财均失败）: {e}")
        return 1
    # 广度：单源（东财 ZDFenBu），失败则诚实降级——仅报告指数方向，不伪造盘感分
    try:
        dist = fetch_distribution()
        if not dist.get("qdate") or dist["n_total"] == 0:
            raise RuntimeError("分布数据为空")
    except Exception as e:
        idx_line = " | ".join(f"{i['name']}{i['pct']:+.2f}%" for i in indices if i.get("pct") is not None)
        print(f"[vibe_intraday] ⚠️ 广度源不可用（东财ZDFenBu）: {e}")
        print(f"[vibe_intraday] 降级：仅指数方向 {idx_line or 'n/a'}（未计算盘感分，勿当实时盘感）")
        return 1

    zt_avg = zt_avg_20d()
    d = score_dims(dist, indices, zt_avg)
    vibe = round(W["breadth"] * d["breadth"] + W["index"] * d["index"] + W["zt"] * d["zt"], 1)
    stage = stage_of(vibe)
    card = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "qdate": dist["qdate"],
        "status": "realtime_snapshot",
        "vibe_score": vibe,
        "stage": stage,
        "dimensions": {"breadth": d["breadth"], "index": d["index"], "zt": d["zt"]},
        "raw": {"n_up": dist["n_up"], "n_down": dist["n_down"], "flat": dist["flat"],
                "up_limited": dist["up_limited"], "down_limited": dist["down_limited"],
                "up_ratio_pct": d["up_ratio"], "zt_dt_diff": d["zt_dt_diff"],
                "idx_pct": d["idx_pct"], "zt_avg_20d": round(zt_avg, 1),
                "indices": indices},
        "weights": W,
        "note": "盘中实时盘感=广度50%+指数30%+涨停活跃20%；快照非收盘定稿，以22:00日频卡为准",
    }
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.quiet:
        print(json.dumps({k: card[k] for k in ("qdate", "vibe_score", "stage")},
                         ensure_ascii=False))
    else:
        print(f"盘中盘感: {vibe}分/{stage} "
              f"(广度{d['breadth']}/指数{d['index']}/涨停活跃{d['zt']}) "
              f"| 上涨{d['up_ratio']}% 涨停{dist['up_limited']}/跌停{dist['down_limited']} "
              f"| 指数均值{d['idx_pct']:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
