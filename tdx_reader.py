# -*- coding: utf-8 -*-
"""
tdx_reader.py — 屠龙系统统一通达信本地数据读取模块（纯 stdlib）
================================================================
统一入口，供全系统所有需要读 vipdoc（.day 日线 / .lc1 1分钟 / .lc5 5分钟）的工作复用，
消除散落各脚本的内联 struct 重复与格式漂移。

字节布局（2026-08-31 全量验证，与 mootdx TdxLCMinBarReader / akshare 三方对拍一致）：
- .day   (32B/条)  <IIIIIfII
    date uint32 YYYYMMDD；open/high/low/close int32 ×100（÷100 还原为元）；
    amount float32 元；volume uint32 股；尾 4B reserved
- .lc1/.lc5 (32B/条)  <HHfffffII
    低16位 = 打包日期：year = d//2048 + 2004; month = (d%2048)//100; day = (d%2048)%100
    高16位 = 当日分钟数（575 = 09:35）
    open/high/low/close/amount float32；volume uint32 股；尾 4B reserved

⚠️ 口径警示：
- volume 一律“股”（tickflow/mootdx 日线输出为“手”需 ×100 转换，勿混用）
- .day volume 对日成交超 uint32 上限(42.9亿股)的超大盘股以“手”存储防溢出，
  read_day 已按 amount/中价 判据自动归一为股(20261029)；远古(1990s)部分行
  amount 口径本身异常, 归一以其自洽为准, 残缺行如实透传
- 1 分钟线勿用 mootdx `TdxMinBarReader`（它按 int32×100 解价格，对本机数据是错的）；
  正确读法是本模块（或 mootdx `TdxLCMinBarReader`，与 .lc5 同构）。

用法：
    from tdx_reader import read_day, read_min5, read_min1, latest_day_date
    day = read_day("sh", "600000")            # [(date, o, h, l, c, amount, volume), ...]
    m5  = read_min5("sh", "600000")           # [dict(date,time,dt,o,h,l,c,amount,vol), ...]
    m1  = read_min1("sh", "600000")
    d   = latest_day_date()                   # int YYYYMMDD（全市场众数）

自检/对拍：
    python tdx_reader.py --self-check         # 日线↔分钟末日收盘交叉
    python tdx_reader.py --verify-mootdx      # 与 mootdx TdxLCMinBarReader 全量对拍（bad=0 才算过）
"""
from __future__ import annotations

import os
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_TDX_ROOT = r"D:\anzhuang\tdx\vipdoc"

# 规范字节布局（单一真源）
_DAY_FMT = "<IIIIIfII"     # date, open, high, low, close, amount, volume, reserved
_MIN_FMT = "<HHfffffII"    # date_num, time_min, open, high, low, close, amount, volume, reserved
RECORD_SIZE = 32

DayRow = Tuple[int, float, float, float, float, float, int]   # (date,o,h,l,c,amount,volume)
MinRow = Dict[str, object]  # date,int | time,int | dt,str | o,h,l,c,amount,vol


def _normalize(market: str, code: str) -> Tuple[str, str]:
    """归一化为 (market, 带前缀完整代码)。market 可传 'sh'/'sz'/'bj' 或 ''。
    code 可带前缀（sh600000）或不带（600000）。
    20261030: 非法 market 码改为 fail-loud（本会话三次静默踩坑实证：位置传参错位时
    6位码被当 market 静默透传→拼错路径→返回空列表被误读为"无数据"）。合法输入零影响。
    """
    m = (market or "").strip().lower()
    c = str(code).strip()
    if m and m not in ("sh", "sz", "bj"):
        raise ValueError(
            f"[tdx_reader] 非法 market 参数 {market!r}（合法: '' / 'sh' / 'sz' / 'bj'，"
            f"或 code 带 sh/sz/bj 前缀）。高频错位：read_min5(code) 会把 6 位码当 market，"
            f"正确写法 read_min5('', code)")
    if len(c) >= 2 and c[:2] in ("sh", "sz", "bj"):
        m = m or c[:2]
        c = c[2:]
    if not m:
        # 20261011批1: 920xxx(北交所新码段)必须先于 9→sh 判定归 bj——vipdoc 只有
        # bj/lday/bj920*(351个), sh/lday 无 sh920* → 旧映射下 read_day 恒空 → 上层
        # 恒走 meta 回退(分数口径 change_pct), qfq 库 920 段 2942 行 100 倍单位污染
        # (亲测复现)。900xxx 沪B 不受影响仍归 sh。
        if c.startswith("92"):
            m = "bj"
        elif c.startswith(("6", "9", "5")):
            m = "sh"
        elif c.startswith(("4", "8")):
            m = "bj"
        else:
            m = "sz"
    return m, f"{m}{c}"


def _min_path(root: str, market: str, full: str, subdir: str, ext: str) -> Path:
    return Path(root) / market / subdir / f"{full}.{ext}"


def read_day(market: str = "", code: str = "", root: str = DEFAULT_TDX_ROOT) -> List[DayRow]:
    """读取 .day 日线。返回 [(date, open, high, low, close, amount, volume), ...] 按日期升序。
    文件不存在/读取失败返回 []。
    """
    m, full = _normalize(market, code)
    path = Path(root) / m / "lday" / f"{full}.day"
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        n = size // RECORD_SIZE
        rows: List[DayRow] = []
        with open(path, "rb") as f:
            buf = f.read(n * RECORD_SIZE)
        for i in range(n):
            b = buf[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
            d, o, h, l, c, amt, vol, _ = struct.unpack(_DAY_FMT, b)
            o, h, l, c = o / 100.0, h / 100.0, l / 100.0, c / 100.0
            vol = int(vol)
            # 20261029 单位归一: .day 的 volume 字段对日成交超 uint32 上限(42.9亿股)的
            # 超大盘股以"手"存储防溢出(实测 000725 京东方 2026-06-26: 存 45,251,768 手
            # = 45.25 亿股; 常规股 000333 同日 38,487,790 为股)。amount(float32 元)与
            # 价格(int×100)不受溢出影响, 以 amount/中价 估算股数裁决: 估算/实测 落在
            # ~100 倍域(50~200)即手单位, ×100 归一为股。
            if amt > 0 and vol > 0:
                est = amt / ((h + l + c) / 3.0)
                r = est / vol
                if 50.0 <= r <= 200.0:
                    vol = vol * 100
            rows.append((d, o, h, l, c, float(amt), vol))
        return rows
    except OSError:
        return []


def _read_min(root: str, market: str, full: str, subdir: str, ext: str) -> List[MinRow]:
    path = Path(root) / market / subdir / f"{full}.{ext}"
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        n = size // RECORD_SIZE
        rows: List[MinRow] = []
        with open(path, "rb") as f:
            buf = f.read(n * RECORD_SIZE)
        for i in range(n):
            b = buf[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
            dnum, tmin, o, h, l, c, amt, vol, _ = struct.unpack(_MIN_FMT, b)
            if dnum == 0 or o <= 0:          # 剔除空/停牌伪记录（与 mootdx 语义对齐）
                continue
            year = dnum // 2048 + 2004
            md = dnum % 2048
            month = md // 100
            day = md % 100
            hour, minute = divmod(tmin, 60)
            rows.append({
                "date": year * 10000 + month * 100 + day,
                "time": hour * 100 + minute,
                "dt": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
                "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                "amount": float(amt), "vol": int(vol),
            })
        return rows
    except OSError:
        return []


def read_min5(market: str = "", code: str = "", root: str = DEFAULT_TDX_ROOT) -> List[MinRow]:
    """读取 .lc5 5分钟线。返回每行 dict(date,time,dt,o,h,l,c,amount,vol)。"""
    m, full = _normalize(market, code)
    return _read_min(root, m, full, "fzline", "lc5")


def read_min1(market: str = "", code: str = "", root: str = DEFAULT_TDX_ROOT) -> List[MinRow]:
    """读取 .lc1 1分钟线。返回每行 dict(date,time,dt,o,h,l,c,amount,vol)。"""
    m, full = _normalize(market, code)
    return _read_min(root, m, full, "minline", "lc1")


def latest_day_date(root: str = DEFAULT_TDX_ROOT, markets=("sh", "sz", "bj"),
                    a_share_only: bool = False,
                    prefixes: Optional[Tuple[str, ...]] = None,
                    max_samples: int = 0,
                    date_min: int = 19900101, date_max: int = 20301231) -> int:
    """扫描各市场 lday 末条日期，返回全市场众数日期（YYYYMMDD int）。

    a_share_only=True 时只统计真 A 股前缀（60/00/30/68/43）文件。
    prefixes 提供时优先用它作精确前缀过滤（可覆盖 a_share_only）。
    max_samples>0 时最多统计这么多文件（性能上限，语义与 daily_chain_update 一致）。
    date_min/date_max 过滤合法日期范围（默认 1990~2030）。
    """
    counts: Counter = Counter()
    samples = 0
    for m in markets:
        d = Path(root) / m / "lday"
        if not d.is_dir():
            continue
        for fp in d.glob("*.day"):
            code = fp.stem[len(m):]
            if prefixes is not None:
                if not code.startswith(prefixes):
                    continue
            elif a_share_only and not code.startswith(("60", "00", "30", "68", "43")):
                continue
            try:
                size = fp.stat().st_size
                if size < RECORD_SIZE:
                    continue
                with open(fp, "rb") as f:
                    f.seek(size - RECORD_SIZE)
                    b = f.read(RECORD_SIZE)
                dte = struct.unpack("<I", b[:4])[0]
                if not (date_min <= dte <= date_max):
                    continue
                counts[dte] += 1
                samples += 1
            except (OSError, struct.error):
                continue
            if max_samples and samples >= max_samples:
                return counts.most_common(1)[0][0]
    if not counts:
        return 0
    return counts.most_common(1)[0][0]


def day_close_on(market: str = "", code: str = "", date_int: int = 0,
                 root: str = DEFAULT_TDX_ROOT) -> Optional[float]:
    """指定交易日收盘价（无则 None）。"""
    for r in read_day(market, code, root=root):
        if r[0] == date_int:
            return r[4]
    return None


def minute_bars_on(market: str = "", code: str = "", date_int: int = 0,
                   period: str = "5", root: str = DEFAULT_TDX_ROOT) -> List[MinRow]:
    """指定交易日全部 1/5 分钟 bar。period: '5' 或 '1'。"""
    fn = read_min5 if period == "5" else read_min1
    return [r for r in fn(market, code, root=root) if r["date"] == date_int]


# ---------------------------------------------------------------- 自检 / 对拍
def self_check(root: str = DEFAULT_TDX_ROOT, code: str = "600000") -> int:
    """日线↔分钟末日收盘交叉校验。偏差>=0.01 计 mism，mism>0 返回 1。"""
    m, full = _normalize("", code)
    day = read_day(m, code, root=root)
    m1 = read_min1(m, code, root=root)
    if not day or not m1:
        print(f"[tdx_reader] 数据为空（{full} day={len(day)} min1={len(m1)}），跳过校验")
        return 2
    day_close = {r[0]: r[4] for r in day}
    checked = mism = 0
    for dt in {r["date"] for r in m1}:
        if dt in day_close:
            checked += 1
            last = [r for r in m1 if r["date"] == dt][-1]["c"]
            if abs(last - day_close[dt]) >= 0.01:
                mism += 1
                if mism <= 5:
                    print(f"[tdx_reader] mism {dt}: min_last={last:.2f} day_close={day_close[dt]:.2f}")
    print(f"[tdx_reader] 分钟↔日线收盘交叉：{checked} 日, 偏差 {mism} 笔")
    print(f"[tdx_reader] 末日：day_close={day[-1][4]:.2f}  min1_last_close={m1[-1]['c']:.2f}")
    return 1 if mism else 0


def verify_mootdx(root: str = DEFAULT_TDX_ROOT, samples: int = 12) -> int:
    """与 mootdx TdxLCMinBarReader 对拍（独立权威，第三方库）。
    samples 个随机 .lc5 文件，每文件抽 200 条全字段比对，bad 必须为 0。
    mootdx 不可用视为 FAIL。
    """
    try:
        from mootdx.reader import TdxLCMinBarReader
    except Exception as e:
        print(f"[tdx_reader] mootdx 不可用，无法对拍: {e}")
        return 1
    import glob
    import random

    random.seed(20260831)
    total = ok = 0
    for m in ("sh", "sz", "bj"):
        files = sorted(glob.glob(os.path.join(root, m, "fzline", "*.lc5")))
        for p in random.sample(files, min(samples, len(files))):
            full = os.path.basename(p)[:-4]          # sh600000
            mt = TdxLCMinBarReader().parse_data_by_file(p)
            rows = read_min5(m, full[len(m):], root=root)
            total += 1
            bad = 0
            if len(rows) != len(mt):
                bad += 1
            else:
                step = max(1, len(mt) // 200)
                for idx in range(0, len(mt), step):
                    r = mt[idx]
                    row = rows[idx]
                    if row["date"] != int(r["date"][:4] + r["date"][5:7] + r["date"][8:10]) \
                       or abs(row["o"] - r["open"]) > 0.005 \
                       or abs(row["h"] - r["high"]) > 0.005 \
                       or abs(row["l"] - r["low"]) > 0.005 \
                       or abs(row["c"] - r["close"]) > 0.005 \
                       or abs(row["amount"] - r["amount"]) > 0.01 \
                       or row["vol"] != int(r["volume"]):
                        bad += 1
            if bad == 0:
                ok += 1
            else:
                print(f"[tdx_reader] MISMATCH {full}: bad={bad}")
    print(f"[tdx_reader] vs mootdx 对拍：{ok}/{total} 一致（bad=0 才通过）")
    return 0 if ok == total else 1


def main():
    ap = __import__("argparse").ArgumentParser(description="屠龙统一 TDX 读取模块")
    ap.add_argument("--self-check", action="store_true", help="日线↔分钟收盘交叉自检")
    ap.add_argument("--verify-mootdx", action="store_true", help="与 mootdx 全量对拍")
    ap.add_argument("--code", default="600000", help="自检标的代码（默认 600000）")
    args = ap.parse_args()

    rc = 0
    if args.self_check:
        rc = max(rc, self_check(code=args.code))
    if args.verify_mootdx:
        rc = max(rc, verify_mootdx())
    if not args.self_check and not args.verify_mootdx:
        d = latest_day_date()
        print(f"[tdx_reader] 全市场最新交易日：{d}")
        print("用法：--self-check / --verify-mootdx")
        rc = 0
    sys.exit(rc)


if __name__ == "__main__":
    main()
