# -*- coding: utf-8 -*-
"""
qlib_bin_update_daily.py — qlib_bin 日频增量（2026-08-22, 替代 8/19/8/21 两个 ad-hoc 脚本）
============================================================================================
契约与 qlib_bin_rebuild.py 一致: qfq 基准(系统正源, 2026-08-22根治) / qlib 标准 bin 格式(首值=start_index 头)
  / 每标的 bin 按其 all.txt end 位置对齐 / 停牌日 NaN 补位。

幂等: 按 day.txt 末位判断待补区间; 重复运行无重复写入(已覆盖则 no-op)。
失步防线(2026-08-27, 8/26与8/27两犯后根治):
  ① 启动抽样(csi300前30只)检测"日历超前于 bin 实际数据", 一致滞后自动回退日历重写(自愈, 备份留档);
  ② 待补交易日源全市场零行(源未就绪)→ 拒绝推进日历, exit 2;
  ③ append 以 bin 实际长度为准, 中断残留补记账, 不重复追加;
  ④ day.txt/all.txt 原子写(tmp+os.replace)。
零值残留防线(2026-09-29, 9/28 撤树中断于 append 后半程实证的值级盲区):
  ⑤ 启动全市场值级检测"bin 长度已含日历末位但末位=0"(位置级①与幂等③双双探测/跳过该态),
    命中即查 qfq 源定点重写 7 字段末位(原子写, 幂等收敛); 源缺行/冲突 → exit 2 fail-closed。
用法:  python qlib_bin_update_daily.py            # 增量到 raw 源最新
       python qlib_bin_update_daily.py --check    # 只校验(不写盘)
并入:  daily_chain_update.py 的 qlib_bin 环节(见该文件 run_qlib_bin)。
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import qlib_bin_rebuild as RB   # 复用 A_PAT / inst_of / load_calendar / load_all_txt / self_check

META = RB.META
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
QLIB = Path(RB.QLIB)
FIELDS = RB.FIELDS
# 2026-08-22 完全根治: qlib_bin 基准切 qfq(系统正源), 日更从 qfq 库取数
SRC_DB, SRC_TBL = QFQ_DB, "stock_daily_meta_qfq"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def trading_days_after(last_iso: str) -> list:
    """index_daily_meta 000001 日历中严格晚于 last_iso 的交易日(ISO, 有序)"""
    con = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    rows = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM index_daily_meta WHERE code='000001' AND date>? ORDER BY date",
        (last_iso.replace("-", ""),)).fetchall()]
    con.close()
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in rows]


def raw_max_date() -> str:   # 名保留兼容; 实际读 qfq 源
    if not str(SRC_TBL).isidentifier():
        raise ValueError(f"非法表名: {SRC_TBL!r}")
    con = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    mx = con.execute(f"SELECT MAX(date) FROM {SRC_TBL}").fetchone()[0]
    con.close()
    return f"{str(mx)[:4]}-{str(mx)[4:6]}-{str(mx)[6:]}"


def load_day_rows(dates: list) -> dict:
    """{date_iso: {code: (open,high,low,close,volume,amount)}}"""
    out = {}
    con = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    for d in dates:
        ymd = d.replace("-", "")
        df = pd.read_sql(
            f"SELECT code, open, high, low, close, volume, amount "
            f"FROM {SRC_TBL} WHERE date=?", con, params=(ymd,))
        out[d] = {r["code"]: (r["open"], r["high"], r["low"], r["close"], r["volume"], r["amount"])
                  for _, r in df.iterrows()}
    con.close()
    return out


def append_bin(inst_dir: Path, values_by_field: dict):
    """values_by_field: {field: [float,...]} — 按字段 append(先于日历扩展调用, 保持位置对齐)"""
    for fld, vals in values_by_field.items():
        if not vals:
            continue
        path = inst_dir / f"{fld}.day.bin"
        with open(path, "ab") as f:
            f.write(np.array(vals, dtype="<f4").tobytes())


# ─────────────── 2026-08-27 失步防线(8/26与8/27两犯后根治) ───────────────
# 病理: 存在"日历已推进但 bin 数据未写"的可达状态(源空数据仍推日历/中断于日历写入前后),
#       且幂等判定只看日历末位 → 该状态永久跳过, 靠人工回退 day.txt 抢救(两次)。
# 防线: ①启动抽样检测失步, 一致滞后自动回退日历重写(自愈); ②源空数据拒绝推进(exit 2);
#       ③append 以 bin 实际长度为准, 中断重跑不重复追加; ④日历/all.txt 原子写。

def bin_real_last_idx(inst: str):
    """该标的 close.day.bin 实际数据末位的日历索引(hdr+len-2); 无文件返回 None。"""
    p = QLIB / "features" / inst / "close.day.bin"
    if not p.exists():
        return None
    raw = np.fromfile(p, dtype=np.float32)
    if len(raw) < 2:
        return None
    return int(raw[0]) + len(raw) - 2


def detect_desync(cur_cal: list):
    """抽样(csi300 前30只)检测日历 vs bin 实际末位。
    返回 (state, value):
      ("ok", None)          无失步
      ("heal", common_idx)  一致滞后(全部抽样股 bin 末位相同且 < 日历末位) → 回退日历到 common_idx
      ("manual", detail)    参差/缺 bin → 不自动修, 人工处理(可能是长度错位, 回退不安全)
    """
    csi = QLIB / "instruments" / "csi300.txt"
    if not csi.exists():
        return ("manual", "csi300.txt 缺失, 无法抽样")
    insts = []
    for line in csi.read_text(encoding="utf-8").splitlines()[:30]:
        code = line.split("\t")[0].strip().lower()
        if code:
            insts.append(code)
    lasts = []
    for inst in insts:
        li = bin_real_last_idx(inst)
        if li is None:
            return ("manual", f"抽样 {inst} 缺/空 close.day.bin")
        lasts.append(li)
    cal_last = len(cur_cal) - 1
    if min(lasts) == cal_last and max(lasts) == cal_last:
        return ("ok", None)
    if min(lasts) == max(lasts) < cal_last:
        return ("heal", min(lasts))
    return ("manual", f"抽样末位参差 min={min(lasts)} max={max(lasts)} cal={cal_last}, 疑长度错位")


# ─────────────── 2026-09-29 尾部零值残留防线(9/28 撤树中断实证的盲区) ───────────────
# 病理: 进程被撤树中断于 append 后半程 → 部分标的 bin 长度已扩到日历末位但末位值=0.0
#       (9/28 实证: sz000001 平安银行/sz300059 东方财富/sz000002 万科Ａ 等)。
# 双盲区: ①detect_desync/bin_real_last_idx 只比位置(hdr+len-2), 位置已对 → "无失步";
#         ②append 幂等以 bin 实际长度为准, target<=cur → no-op, 重跑增量永远不重写。
#       结果=自愈无效, 只能全量重建(9/28 消耗一次)。
# 防线: 值级检测(all.txt end==日历末位 的标的, 天然排除退市/滞后股):
#       末位==0 且 前一位非0非NaN(主体健康) → 查 qfq 源该日行:
#         源有非零 close → 定点重写 7 字段末位(tmp+os.replace 原子写;
#                          修复中再中断留 0, 下次检测再修, 幂等收敛);
#         源缺行/源 close 空 → bin=0 与 end 声明冲突, 记 bad → 调用方 exit 2(fail-closed)。

def _atomic_write_bin(path: Path, arr: np.ndarray):
    tmp = path.with_suffix(path.suffix + ".tmp")
    arr.tofile(tmp)
    os.replace(tmp, path)


def detect_and_fix_zero_tail(cur_cal: list, check_only: bool = False):
    """值级检测日历末位零值残留; check_only=False 时定点重写修复。
    返回 (found, fixed, bad): found=残留标的数; fixed=实际修复数(check 模式恒 0);
    bad=[(inst, reason)] 不可修清单, 非空时调用方必须 exit 2 拒绝继续。"""
    cal_last = len(cur_cal) - 1
    last_iso = cur_cal[-1]
    all_txt = RB.load_all_txt()
    suspects = []
    for inst, (_start_s, end_s) in all_txt.items():
        if end_s != last_iso:
            continue                    # 未声明末位数据(退市/滞后) → 归失步检测管
        p = QLIB / "features" / inst / "close.day.bin"
        if not p.exists():
            continue
        raw = np.fromfile(p, dtype=np.float32)
        if len(raw) < 3:
            continue
        if int(raw[0]) + len(raw) - 2 != cal_last:
            continue                    # 位置 != 日历末位 → 非本盲区
        if float(raw[-1]) != 0.0:
            continue                    # 末位非零, 健康
        prev = float(raw[-2])
        if prev == 0.0 or np.isnan(prev):
            continue                    # 前一位也空: 非"主体健康+尾部残留"态, 不误伤
        suspects.append(inst)
    if not suspects:
        return (0, 0, [])
    con = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    rows = con.execute(
        f"SELECT code, open, high, low, close, volume, amount FROM {SRC_TBL} WHERE date=?",
        (last_iso.replace("-", ""),)).fetchall()
    con.close()
    src = {RB.inst_of(str(r[0])): r[1:] for r in rows}
    fixed, bad = [], []
    for inst in suspects:
        r = src.get(inst)
        if r is None or r[3] is None:
            bad.append((inst, "源缺行/源close空, 与 all.txt end 声明冲突"))
            continue
        o, h, l, c, v, a = (float(x) for x in r)
        if c == 0.0:
            continue                    # 源该日 close 即 0: bin 与源一致, 非残留
        vwap = a / v if v and v > 0 else float("nan")
        vals = [o, h, l, c, v, a, vwap]
        ok = True
        for i, fld in enumerate(FIELDS):
            bp = QLIB / "features" / inst / f"{fld}.day.bin"
            if not bp.exists():
                bad.append((inst, f"缺 {fld}.day.bin"))
                ok = False
                break
            arr = np.fromfile(bp, dtype=np.float32)
            if len(arr) < 2:
                bad.append((inst, f"{fld}.day.bin 空短"))
                ok = False
                break
            if not check_only:
                arr[-1] = np.float32(vals[i])
                _atomic_write_bin(bp, arr)
        if ok:
            fixed.append(inst)
    return (len(suspects), len(fixed), bad)


def atomic_write_text(path: Path, text: str):
    """tmp + os.replace 原子落盘, 缩小中断窗口(day.txt/all.txt 已不再可能写一半)。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def build_new_instrument(code: str, cal_idx: dict):
    """新上市标的: 从 qfq 源拉全史, 按日历对齐写全部字段 + 返回 all.txt 行"""
    con = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    df = pd.read_sql(
        f"SELECT date, open, high, low, close, volume, amount "
        f"FROM {SRC_TBL} WHERE code=? AND date>=? ORDER BY date",
        con, params=(code, RB.START_DATE))
    con.close()
    if df.empty:
        return None
    df = df.copy()
    df["d_iso"] = df["date"].astype(str).map(lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}")
    df = df[df["d_iso"].isin(cal_idx)]
    if df.empty:
        return None
    df["vwap"] = np.where(df["volume"] > 0, df["amount"] / df["volume"], np.nan)
    inst = RB.inst_of(code)
    idir = QLIB / "features" / inst
    idir.mkdir(parents=True, exist_ok=True)
    d0, d1 = df["d_iso"].iloc[0], df["d_iso"].iloc[-1]
    start_i, last_i = cal_idx[d0], cal_idx[d1]
    pos = (df["d_iso"].map(cal_idx).values - start_i)
    width = int(last_i - start_i) + 1
    for fld in FIELDS:
        vals = np.full(width, np.nan, dtype=np.float32)
        vals[pos] = df[fld].astype(np.float32).values
        buf = np.empty(width + 1, dtype="<f4")
        buf[0] = float(start_i)
        buf[1:] = vals
        buf.tofile(idir / f"{fld}.day.bin")
    return f"{inst.upper()}\t{d0}\t{d1}"


def main():
    check_only = "--check" in sys.argv

    # 锚定 day.txt 当前末位(不做日历扩展), 待补 = 其后到 raw 源的交易日
    cur_cal = [l.strip() for l in (QLIB / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
               if l.strip()]
    # 失步自愈: 日历末位可能超前于 bin 实际数据(历史两犯), 抽样检测并自动回退
    state, val = detect_desync(cur_cal)
    if state == "ok":
        log("失步检测: 抽样 bin 与日历一致, 无失步")
    elif state == "heal":
        bak = QLIB / "calendars" / f"day.txt.bak_desync_{time.strftime('%Y%m%d_%H%M%S')}"
        bak.write_text("\n".join(cur_cal) + "\n", encoding="utf-8")
        cur_cal = cur_cal[: val + 1]
        atomic_write_text(QLIB / "calendars" / "day.txt", "\n".join(cur_cal) + "\n")
        log(f"⚠️ 检测到日历失步(抽样 bin 一致滞后): 已备份 {bak.name} 并回退日历至 {cur_cal[-1]}, 走增量重写自愈")
    elif state == "manual":
        log(f"✗ 失步检测异常, 不自动修复, 请人工处理: {val}")
        sys.exit(2)
    # 尾部零值残留检测+定点自愈(2026-09-29): 位置级失步检测的值级盲区, 9/28 撤树中断实证
    zfound, zfixed, zbad = detect_and_fix_zero_tail(cur_cal, check_only)
    if zfound:
        if check_only:
            log(f"[check] ⚠️ 尾部零值残留 {zfound} 只(check 模式不修复, 正常运行将定点重写自愈)")
        else:
            log(f"⚠️ 检测到尾部零值残留 {zfound} 只, 已定点重写修复 {zfixed} 只(值级对齐 {SRC_TBL})")
    if zbad:
        log(f"✗ 尾部零值残留含不可修标的, 请人工处理: {zbad[:5]}")
        sys.exit(2)
    raw_max = raw_max_date()
    new_dates = [d for d in trading_days_after(cur_cal[-1]) if d <= raw_max]
    extended_cal = sorted(set(cur_cal + new_dates))
    cal_idx = {d: i for i, d in enumerate(extended_cal)}
    log(f"日历末位 {cur_cal[-1]} | raw 源末位 {raw_max} | 待补 {len(new_dates)} 个交易日: {new_dates}")

    if check_only:
        errs = RB.self_check(extended_cal, RB.load_all_txt())
        if errs:
            for e in errs:
                log(f"   ✗ {e}")
            log("   → 自检 FAIL")
            sys.exit(2)
        log("   自检 OK")
        return

    if not new_dates:
        log("已是最新, 无需更新")
        return

    all_txt = RB.load_all_txt()
    day_rows = load_day_rows(new_dates)
    # 空数据防线: 待补交易日全部在源中零行 = 源未就绪/链路时序破坏(个股停牌零行是正常的,
    # 全市场零行不是)。此情形若继续, 会"扩展 0 只但日历照推", 制造 8/26、8/27 两犯的失步态。
    if new_dates and not any(day_rows.values()):
        log(f"✗ 源 {SRC_TBL} 在待补日 {new_dates} 全市场零行(源未就绪?), 拒绝推进日历, 中止")
        sys.exit(2)
    # 新上市/新增标的
    new_inst_lines = []
    for d, rows in day_rows.items():
        for code in rows:
            # 2026-08-24 P2修复: 增量新增标的过 A_PAT 口径——北交所(92/8/430开头)不进
            # qlib_bin（与 8/23 全量 rebuild 口径一致；此前无过滤致 338 只北交回流，
            # 每次 rebuild 删、次日增量又加回，instruments 集合反复抖动）。
            if not RB.A_PAT.match(code):
                continue
            inst = RB.inst_of(code)
            if inst in all_txt or (QLIB / "features" / inst / "close.day.bin").exists():
                continue
            line = build_new_instrument(code, cal_idx)
            if line:
                new_inst_lines.append(line)
                all_txt[line.split("\t")[0].lower()] = (line.split("\t")[1], line.split("\t")[2])
    if new_inst_lines:
        log(f"新增标的 {len(new_inst_lines)}: {[l.split(chr(9))[0] for l in new_inst_lines]}")

    # 逐 bin 增量
    insts = sorted(d for d in os.listdir(QLIB / "features") if (QLIB / "features" / d).is_dir())
    n_ext = n_skip = 0
    for inst in insts:
        start_s, end_s = all_txt.get(inst, (None, None))
        if start_s not in cal_idx or end_s not in cal_idx:
            continue
        rows_by_date = {d: day_rows.get(d, {}).get(inst[2:]) for d in new_dates}
        # 该股在这些日期的最后交易日
        data_dates = [d for d in new_dates if rows_by_date.get(d) is not None]
        if not data_dates:
            n_skip += 1
            continue
        target = cal_idx[data_dates[-1]]
        declared = cal_idx[end_s]
        # append 幂等化: 以 close.bin 实际长度为准。中断可能发生在 append 之后、all.txt 记账
        # 之前(声明 end 落后于实际), 此时以实际末位续写并补记账, 防旧逻辑重复追加致长度错位。
        real_last = bin_real_last_idx(inst)
        cur = real_last if (real_last is not None and real_last > declared) else declared
        if target <= cur:
            if cur > declared:
                all_txt[inst] = (start_s, extended_cal[cur])  # 补记账残留
            continue
        need_dates = extended_cal[cur + 1: target + 1]
        # 逐字段构造 (pos 对齐: 缺失日 NaN)
        by_field = {fld: [] for fld in FIELDS}
        for d in need_dates:
            row = rows_by_date.get(d)
            if row is None:
                for fld in FIELDS:
                    by_field[fld].append(float("nan"))
                continue
            o, h, l, c, v, a = row
            vwap = a / v if v and v > 0 else float("nan")
            vals = [o, h, l, c, v, a, vwap]
            for i, fld in enumerate(FIELDS):
                by_field[fld].append(float(vals[i]))
        append_bin(QLIB / "features" / inst, by_field)
        all_txt[inst] = (start_s, extended_cal[target])
        n_ext += 1

    # 写 day.txt / all.txt(原子写: tmp+os.replace, 杜绝写一半被杀的中间态)
    atomic_write_text(QLIB / "calendars" / "day.txt", "\n".join(extended_cal) + "\n")
    lines = sorted(f"{k.upper()}\t{v[0]}\t{v[1]}" for k, v in all_txt.items())
    atomic_write_text(QLIB / "instruments" / "all.txt", "\n".join(lines) + "\n")
    log(f"扩展 {n_ext} 只 / 停牌无新数据跳过 {n_skip} 只 / 新增 {len(new_inst_lines)} 只; 日历 -> {extended_cal[-1]}")

    # 自检
    log("自检...")
    errs = RB.self_check(extended_cal, RB.load_all_txt())
    if errs:
        for e in errs:
            log(f"   ✗ {e}")
        log("   → 自检 FAIL")
        sys.exit(2)
    log("自检 OK ✅")


if __name__ == "__main__":
    main()
