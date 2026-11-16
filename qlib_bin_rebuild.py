# -*- coding: utf-8 -*-
"""
qlib_bin_rebuild.py — qlib_bin 全量重建（2026-08-22, 方案 A1: 不复权 raw 基准）
================================================================================
背景: 8/22 实测复核记忆项三条结构性问题:
  ① 各标的记录数不等 = qlib 每标的独立 start_index 头的正常格式, 非错误;
     真正的结构问题 = 4918 只 bin 停在 8/14(落后日历 5 天) + all.txt end_date 与 bin 差 1 天;
  ② "首条 0.0 占位" = qlib 格式的 start_index 头(全历史股=0), 数据与源逐点对齐, 非错位;
  ③ "历史值用旧复权因子" = 实际是"不复权原始价"(与 dragon_quant_meta.stock_daily_meta 完全一致); 8/22 完全根治已切 qfq 基准(见下).

本脚本: 以 qfq 源(stock_daily_meta_qfq, 系统正源) 全量重建 features/ + instruments/all.txt,
  修复: 全市场滞后归零 / all.txt end 与 bin 严格一致 / 补齐 302xxx 创业板新号段(原 filter 漏掉)
        / 补齐新上市标的(原 dump 后新上市 6 只) / day.txt 与源同步。
  保留: qlib 标准 bin 格式(首值=start_index 头), 与训练时同源同口径, 模型无需重训。

安全: 先 os.rename(qlib_bin -> qlib_bin.bak_*), 若有进程持有 bin 文件打开则 rename 失败 → 中止(不碰数据);
      重建完成后自检 FAIL 则退出码 2, 可回滚 .bak。
用法:  python qlib_bin_rebuild.py            # 默认全量重建
       python qlib_bin_rebuild.py --check    # 只跑自检(不写盘)
"""
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
QLIB = Path(r"D:\workbuddyspace\qlib_data\qlib_bin")
FIELDS = ["open", "high", "low", "close", "volume", "amount", "vwap"]
START_DATE = 20160104
# 沪深 A 股(沪 6xxxxx; 深 000/001/002/003 + 创业板 300/301/302)。北交所(4/8/920)不入 qlib_bin(模型均为沪深口径)。
A_PAT = re.compile(r"^(6\d{5}|00[0-3]\d{3}|30\d{4})$")
# 2026-08-22 完全根治: 价格基准由 raw(不复权)切到 qfq(系统正源, 记忆项"历史值用旧复权因子"指向的期望口径)。
# 默认 qfq(读 stock_daily_meta_qfq); --source raw 保留旧口径用于回滚/对照。
SOURCE_DB = {"raw": META, "qfq": QFQ_DB}
SOURCE_TABLE = {"raw": "stock_daily_meta", "qfq": "stock_daily_meta_qfq"}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def inst_of(code: str) -> str:
    return ("sh" if code.startswith("6") else "sz") + code


# ─────────────────────────── 数据读取 ───────────────────────────
def load_calendar() -> list:
    """取现有 day.txt 为日历(已由 8/22 清空行并核验), 并用 index_daily_meta 000001 扩展到源最新。
    返回 YYYY-MM-DD 列表(有序去重)。"""
    p = QLIB / "calendars" / "day.txt"
    if p.exists():
        cal = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        cal = []
    # 校验现有日历: 升序且无重复
    if cal and (len(set(cal)) != len(cal) or cal != sorted(cal)):
        sys.exit("错误: 现有 day.txt 非有序或含重复, 请人工核对后重跑(重建只信任干净日历)")
    # 用 index_daily_meta 000001 扩展(与 qlib_dump_local.py 同源口径)
    con = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    idx_dates = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
        (START_DATE,)).fetchall()]
    con.close()
    idx_iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in idx_dates]
    have = set(cal)
    cal.extend(d for d in idx_iso if d not in have)
    cal = sorted(cal)
    return cal


def load_raw(source: str = "qfq") -> pd.DataFrame:
    db = SOURCE_DB[source]
    tbl = SOURCE_TABLE[source]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql(
        f"SELECT code, date, open, high, low, close, volume, amount "
        f"FROM {tbl} WHERE date>=? ORDER BY code, date",
        con, params=(START_DATE,))
    con.close()
    df["vwap"] = np.where(df["volume"] > 0, df["amount"] / df["volume"], np.nan)
    return df


def load_all_txt() -> dict:
    """读取 QLIB/instruments/all.txt -> {inst: (start_date, end_date)}"""
    out = {}
    p = QLIB / "instruments" / "all.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                out[parts[0].lower()] = (parts[1], parts[2])
    return out


# ─────────────────────────── 自检 ───────────────────────────
def self_check(cal: list, all_txt: dict, source: str = "qfq") -> list:
    """返回错误列表(空=通过)。all_txt: inst -> (start_date, end_date)"""
    cal_idx = {d: i for i, d in enumerate(cal)}
    src_db, src_tbl = SOURCE_DB[source], SOURCE_TABLE[source]
    errs = []
    insts = sorted(d for d in os.listdir(QLIB / "features") if (QLIB / "features" / d).is_dir())

    # 1) all.txt 行数与 features 目录数一致
    if len(all_txt) != len(insts):
        errs.append(f"all.txt {len(all_txt)} 行 != features {len(insts)} 目录")

    # 2) 每个 bin: hdr==start 的日历索引, 长度==end-start+1, end 是 raw 最后交易日
    n_bad_hdr = n_bad_len = n_bad_end = 0
    for inst in insts:
        start_s, end_s = all_txt.get(inst, (None, None))
        p = QLIB / "features" / inst / "close.day.bin"
        if not p.exists():
            errs.append(f"{inst} 缺 close.day.bin")
            continue
        raw = np.fromfile(p, dtype=np.float32)
        if len(raw) < 2:
            errs.append(f"{inst} bin 空")
            continue
        hdr = int(raw[0]); ndata = len(raw) - 1
        if start_s not in cal_idx:
            errs.append(f"{inst} all.txt start={start_s} 不在日历")
            continue
        if hdr != cal_idx[start_s]:
            n_bad_hdr += 1
        exp_len = cal_idx[end_s] - cal_idx[start_s] + 1 if end_s in cal_idx else None
        if exp_len is None or ndata != exp_len:
            n_bad_len += 1
        # 2026-08-24 P2修复: 全字段长度校验（原仅 close，其余6字段长度溢出/缺失不检出，
        # 增量 append 崩溃场景只能靠 close 单字段碰运气拦截）
        if exp_len is not None:
            for _fld in FIELDS:
                if _fld == "close":
                    continue
                _fp = QLIB / "features" / inst / f"{_fld}.day.bin"
                if not _fp.exists():
                    errs.append(f"{inst} 缺 {_fld}.day.bin")
                    break
                if (len(np.fromfile(_fp, dtype=np.float32)) - 1) != exp_len:
                    n_bad_len += 1
                    break
    if n_bad_hdr:
        errs.append(f"{n_bad_hdr} 个 bin hdr != all.txt start 的日历索引")
    if n_bad_len:
        errs.append(f"{n_bad_len} 个 bin 长度 != (end-start+1), 对齐检查未过")

    # 3) csi300 每个成员都有 bin
    csi = QLIB / "instruments" / "csi300.txt"
    if csi.exists():
        miss = []
        for line in csi.read_text(encoding="utf-8").splitlines():
            code = line.split("\t")[0].strip().lower()
            if code and not (QLIB / "features" / code / "close.day.bin").exists():
                miss.append(code)
        if miss:
            errs.append(f"csi300 缺 bin: {miss}")

    # 4) 全市场滞后: 对"raw 源里最后一笔在日历末位"的活跃股, bin 也应到日历末位
    con = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    last = cal_idx[cal[-1]]
    lag = 0
    for inst in insts:
        code = inst[2:]
        r = con.execute(f"SELECT MAX(date) FROM {src_tbl} WHERE code=?", (code,)).fetchone()
        if r and r[0]:
            d = f"{str(r[0])[:4]}-{str(r[0])[4:6]}-{str(r[0])[6:]}"
            if d == cal[-1]:  # 源最新日有该股 => 活跃, bin 必须到末位
                raw_bin = np.fromfile(QLIB / "features" / inst / "close.day.bin", dtype=np.float32)
                bin_last = int(raw_bin[0]) + len(raw_bin) - 2
                if bin_last < last:
                    lag += 1
    con.close()
    if lag:
        errs.append(f"{lag} 只活跃股 bin 未到日历末位(滞后)")

    # 5) 抽样 5 只全历史股, 逐点比对 close vs raw 源
    con = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    picks = [i for i in insts if all_txt.get(i, ("", ""))[0] == cal[0]][:5]
    for inst in picks:
        code = inst[2:]
        dates = [cal[i] for i in (0, 400, 1200, 2000, len(cal) - 1) if i < len(cal)]
        raw_bin = np.fromfile(QLIB / "features" / inst / "close.day.bin", dtype=np.float32)
        hdr = int(raw_bin[0])
        for ds in dates:
            i = cal.index(ds)
            if i - hdr < 0 or i - hdr >= len(raw_bin) - 1:
                continue
            b = raw_bin[1 + i - hdr]
            rr = con.execute(f"SELECT close FROM {src_tbl} WHERE code=? AND date=?",
                             (code, ds.replace("-", ""))).fetchone()
            if rr is None or abs(float(rr[0]) - b) > 1e-4:
                errs.append(f"抽样 {inst}@{ds}: bin={b} raw={rr}")
                break
    con.close()
    return errs


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    check_only = "--check" in sys.argv
    source = "qfq"
    for a in sys.argv[1:]:
        if a.startswith("--source"):
            source = a.split("=", 1)[1].lower()
    assert source in SOURCE_DB, f"source 必须为 raw/qfq, 得 {source}"

    log(f"1) 加载日历 & {source} 源...")
    cal = load_calendar()
    cal_idx = {d: i for i, d in enumerate(cal)}
    df = load_raw(source)
    log(f"   日历 {len(cal)} 天 ({cal[0]}..{cal[-1]}), {source} {len(df):,} 行 / {df['code'].nunique()} 只")

    df = df[df["code"].str.match(A_PAT)].copy()
    df["inst"] = df["code"].map(inst_of)
    log(f"   A股筛选后 {df['code'].nunique()} 只")

    if check_only:
        log("--check 模式: 仅校验现有 qlib_bin(不写盘)...")
        all_txt = load_all_txt()
        errs = self_check(cal, all_txt, source)
        if errs:
            for e in errs:
                log(f"   ✗ {e}")
            log(f"   → 自检 FAIL ({len(errs)} 项)")
            sys.exit(2)
        log("   自检 OK")
        return

    # 2) 备份(rename 而非拷贝; 有进程持文件打开时 Windows rename 失败 → 中止)
    bak = QLIB.parent / f"qlib_bin.bak_rebuild_{time.strftime('%Y%m%d_%H%M%S')}"
    if QLIB.exists():
        try:
            os.rename(str(QLIB), str(bak))
            log(f"2) 备份 -> {bak.name}")
        except OSError as e:
            sys.exit(f"备份失败(可能被进程占用): {e} — 未做任何修改, 中止")
    else:
        log("2) 无现有目录, 全新创建")
    (QLIB / "calendars").mkdir(parents=True)
    (QLIB / "instruments").mkdir(parents=True)
    (QLIB / "features").mkdir(parents=True)

    # 3) 写 day.txt
    with open(QLIB / "calendars" / "day.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(cal) + "\n")

    # 4) 写 bins + all.txt
    log("3) 写 bin 文件...")
    inst_lines = []
    n = 0
    t0 = time.time()
    for inst, g in df.groupby("inst"):
        g = g.sort_values("date").copy()
        g["d_iso"] = g["date"].astype(str).map(lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}")
        g = g[g["d_iso"].isin(cal_idx)]          # 只保留日历内交易日
        if g.empty:
            continue
        d0 = g["d_iso"].iloc[0]; d1 = g["d_iso"].iloc[-1]
        start_i = cal_idx[d0]
        last_i = cal_idx[d1]
        day_pos = (g["d_iso"].map(cal_idx).values - start_i)
        width = int(last_i - start_i) + 1
        idir = QLIB / "features" / inst
        idir.mkdir(exist_ok=True)
        for fld in FIELDS:
            vals = np.full(width, np.nan, dtype=np.float32)
            vals[day_pos] = g[fld].astype(np.float32).values
            buf = np.empty(width + 1, dtype="<f4")
            buf[0] = float(start_i)
            buf[1:] = vals
            buf.tofile(idir / f"{fld}.day.bin")
        inst_lines.append(f"{inst.upper()}\t{d0}\t{d1}")
        n += 1
        if n % 500 == 0:
            log(f"   {n} 只 ({time.time() - t0:.0f}s)")
    with open(QLIB / "instruments" / "all.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(inst_lines) + "\n")

    # 5) 从备份恢复 csi300.txt(universe 文件, 不重生成以免改变生产股票池)
    src_csi = bak / "instruments" / "csi300.txt" if bak.exists() else None
    if src_csi and src_csi.exists():
        (QLIB / "instruments" / "csi300.txt").write_bytes(src_csi.read_bytes())
        log(f"4) 恢复 csi300.txt ({len(src_csi.read_text().splitlines())} 行)")
    else:
        log("   ⚠ 无 csi300.txt 可恢复(生产 universe 需人工确认)")

    # 6) 自检
    log("5) 自检...")
    all_txt = load_all_txt()
    errs = self_check(cal, all_txt, source)
    if errs:
        for e in errs:
            log(f"   ✗ {e}")
        log(f"   → 自检 FAIL ({len(errs)} 项); 备份保留在 {bak.name}, 可回滚")
        sys.exit(2)
    log(f"   自检 OK: {n} 只, 全市场到 {cal[-1]}, 无滞后, csi300 全齐")
    log(f"完成 ✅ 备份: {bak.name} (可删: 确认 7 天后)")

    # 记录本次重建元信息
    with open(QLIB / "REBUILD_INFO.txt", "w", encoding="utf-8") as f:
        f.write(f"rebuilt={time.strftime('%Y-%m-%d %H:%M:%S')} source={SOURCE_DB[source]} ({source})\n"
                f"basis={source} calendar={len(cal)} days {cal[0]}..{cal[-1]} insts={n}\n"
                f"backup={bak.name}\n")


if __name__ == "__main__":
    main()
