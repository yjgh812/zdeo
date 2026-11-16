# -*- coding: utf-8 -*-
"""
validate_st_panel_20261023.py — ST 历史面板 T-S1 验收（PD-049 提案 §二，跑前冻结口径）
=====================================================================
前置: data/st_history.db 面板构建完成（pull_done 数 ≥ meta.universe_n 的 99%）。
四道检查（全部只读，零改动）:
  S1 锚点: sh.600079 isST 于 20251216 翻转（三源互证锚，提案 §二/报告 §十）。
  S2 快照一致: 每票面板末昨日 is_st == dragon stock_info.is_st（容许当日新 ST 的
     少量滞后差异，单独计数——面板末日=baostock 最近可用交易日，今日盘中数据未入库）。
  S3 板限签名对齐（提案 T-S1 同款）: 抽样 20 票（分层：主板翻转<2026-07-06 /
     主板翻转≥2026-07-06 / 创业板翻转≥2020-08-24），取面板内首次 0→1 翻转日 d：
     主板<20260706 → 翻转后 10 交易日 max|ret| ≤ 5.5%（5% 板帽）；
     主板≥20260706 → 翻转后 10 交易日 max|ret| ≤ 10.5%（10% 板帽）；
     创业板≥20200824 → 翻转后 10 交易日 max|ret| ≤ 21%（20% 板帽）。
     翻转前 10 交易日是否有日 |ret| 超过该帽（对照信号）一并输出（不作为硬判据，
     翻转前从未大涨的票无此信号属正常）。
  S4 完整性: 面板行数/pull_failed 行数/覆盖票数 vs meta.universe_n。
判据: S1=S2(差异≤5 且逐票可归因当日新ST)=S3 全票通过 → PASS。
运行: python analysis/validate_st_panel_20261023.py
输出: reports/st_panel_validation_20261023.json + stdout
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime

PANEL = r"D:\zcodesj\data\st_history.db"
MAIN = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro"
META = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro"
OUT = r"D:\zcodesj\reports\st_panel_validation_20261023.json"


def board_cap(code6: str, flip: int) -> float | None:
    """翻转日适用的板帽(小数)。不适用(科创板/北交/创业板<20200824)返回 None 不抽。"""
    if code6.startswith(("688", "689")) or code6.startswith(("8", "4", "9")):
        return None
    if code6.startswith(("300", "301")):
        return 0.21 if flip >= 20200824 else None
    if code6.startswith(("60", "00")):
        return 0.055 if flip < 20260706 else 0.105
    return None


def main() -> None:
    # 只读连接+锁等待(后台拉取进程持有写事务时最多等30s, 生成物零写入)
    con = sqlite3.connect(f"file:{PANEL}?mode=ro", uri=True, timeout=30)
    universe_n = int(con.execute("SELECT value FROM meta WHERE key='universe_n'").fetchone()[0])
    done_n = con.execute("SELECT COUNT(*) FROM pull_done").fetchone()[0]
    fail_n = con.execute("SELECT COUNT(*) FROM pull_failed").fetchone()[0]
    total_rows = con.execute("SELECT COUNT(*) FROM st_daily").fetchone()[0]
    last_date = con.execute("SELECT MAX(date) FROM st_daily").fetchone()[0]
    out = {"universe_n": universe_n, "done_n": done_n, "fail_n": fail_n,
           "total_rows": total_rows, "last_date": last_date,
           "generated_at": datetime.now().isoformat(timespec="seconds")}

    # S1 锚点
    anchor = con.execute(
        "SELECT date, is_st FROM st_daily WHERE code='sh.600079' "
        "AND date BETWEEN 20251210 AND 20251220 ORDER BY date").fetchall()
    flip_anchor = next((d for d, f in anchor if f == 1), None)
    out["S1_anchor_flip"] = flip_anchor
    s1_pass = flip_anchor == 20251216

    # S2 快照一致
    main_con = sqlite3.connect(MAIN, uri=True)
    snap = {str(r[0]).zfill(6): (r[1], str(r[2] or "")) for r in main_con.execute(
        "SELECT code, is_st, name FROM stock_info")}
    main_con.close()
    mism, lag_today = [], []
    checked = 0
    for code, in con.execute("SELECT DISTINCT code FROM st_daily"):
        c6 = code[-6:]
        if c6 not in snap:
            continue
        row = con.execute(
            "SELECT is_st FROM st_daily WHERE code=? AND date=?", (code, last_date)).fetchone()
        if row is None:
            continue
        checked += 1
        panel_st, snap_st = row[0], snap[c6][0] in (1, "1", 1.0)
        if bool(panel_st) != bool(snap_st):
            (lag_today if snap_st else mism).append(c6)
    out["S2"] = {"checked": checked, "hard_mismatch": sorted(mism)[:20],
                 "hard_mismatch_n": len(mism),
                 "lag_today_newst": sorted(lag_today)[:20], "lag_today_n": len(lag_today)}
    s2_pass = len(mism) <= 5

    # S3 板限签名抽样（分层 20 票）
    meta_con = sqlite3.connect(META, uri=True)
    samples, seen = [], {"main_old": 0, "main_new": 0, "gem": 0}
    flips = con.execute(
        "SELECT code, MIN(date) FROM st_daily WHERE is_st=1 GROUP BY code").fetchall()
    random.seed(20261023)
    random.shuffle(flips)
    for code, first_st in flips:
        c6 = code[-6:]
        cap = board_cap(c6, first_st)
        if cap is None:
            continue
        key = ("main_old" if cap == 0.055 else "main_new" if cap == 0.105 else "gem")
        quota = {"main_old": 8, "main_new": 8, "gem": 4}[key]
        if seen[key] >= quota or len(samples) >= 20:
            continue
        rows = meta_con.execute(
            "SELECT date, close FROM stock_daily_meta WHERE code LIKE ? AND "
            "date BETWEEN ? AND ? ORDER BY date",
            ("%" + c6, first_st - 40, first_st + 40)).fetchall()
        if len(rows) < 8:
            continue
        closes = [(d, c) for d, c in rows if c and c > 0]
        rets = [(closes[i][0], closes[i][1] / closes[i - 1][1] - 1)
                for i in range(1, len(closes)) if closes[i][0] != first_st]
        post = [abs(r) for d, r in rets if d >= first_st][:10]
        pre = [abs(r) for d, r in rets if d < first_st][-10:]
        if not post:
            continue
        cap_after = max(post) <= cap
        pre_exceed = (max(pre) > cap) if pre else False
        samples.append({"code": c6, "flip": first_st, "cap": cap,
                        "post_max": round(max(post), 4), "cap_after_ok": cap_after,
                        "pre_exceed": pre_exceed})
        seen[key] += 1
        if len(samples) >= 20:
            break
    meta_con.close()
    out["S3"] = {"samples": samples,
                 "cap_after_ok_n": sum(1 for s in samples if s["cap_after_ok"]),
                 "pre_exceed_n": sum(1 for s in samples if s["pre_exceed"])}
    s3_pass = bool(samples) and all(s["cap_after_ok"] for s in samples)

    out["verdict"] = ("PASS" if (s1_pass and s2_pass and s3_pass) else
                      "FAIL: " + ", ".join(k for k, v in (("S1", s1_pass), ("S2", s2_pass),
                                                          ("S3", s3_pass)) if not v))
    con.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"S1 锚点翻转={flip_anchor} pass={s1_pass}")
    print(f"S2 快照: checked={checked} 硬不一致={len(mism)} 当日新ST滞后={len(lag_today)} pass={s2_pass}")
    print(f"S3 签名: n={len(samples)} 板帽内={out['S3']['cap_after_ok_n']} "
          f"翻转前超帽对照={out['S3']['pre_exceed_n']} pass={s3_pass}")
    print(f"S4 完整性: done={done_n}/{universe_n} fail={fail_n} rows={total_rows} 末日={last_date}")
    print(f"判定: {out['verdict']}")


if __name__ == "__main__":
    main()
