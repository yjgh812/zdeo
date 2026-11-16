# -*- coding: utf-8 -*-
"""R46 继续轮脚本1: F1高能量梯队 + F3前高量80% 复现 (2026-10-09)
主人令"全部继续推进": R46呈报候选F1/F3验证轮 (纯描述性统计, 零接线零交易权)
F1=每日各连板板位的一字板数量最多者=高能量板位, 对照其晋级率 vs 其他板位 (民间"高能量梯队"主张)
F3=首板次日成交量/前高量(60日最高收盘日量) 分桶 -> fwd5 收益 (民间"前高量80%合理量"主张)
口径: qfq库 stock_daily_meta_qfq, 2016-2026, 仅60/00/30开头(剔科创/北交/B股)
限制: qfq除权日pct失真可能误判涨停(描述性统计可接受,已标注); 停牌跨日的"次日"=下一bar
输出: tmp/r46_f1_f3_result_20261009.json
"""
import json
import statistics
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = Path(r"D:\zcodesj\tmp\r46_f1_f3_result_20261009.json")
START = "20160101"

LIMIT_TH = {"60": 9.9, "00": 9.9, "30": 19.9}


def limit_th(code: str):
    return LIMIT_TH.get(code[:2])


def med(xs):
    return round(statistics.median(xs), 5) if xs else None


def mean(xs):
    return round(sum(xs) / len(xs), 5) if xs else None


def main():
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    cur = con.cursor()
    print("[load] streaming stock_daily_meta_qfq ...")
    cur.execute(
        """SELECT code, date, open, high, low, close, volume, change_pct
           FROM stock_daily_meta_qfq
           WHERE date >= ? AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
           ORDER BY code, date""",
        (START,),
    )

    # per-code 序列流式聚合
    series = defaultdict(list)  # code -> list[(date,o,h,l,c,v,pct)]
    cur_code, buf = None, []

    def flush(code, rows):
        if not code or len(rows) < 5:
            return
        th = limit_th(code)
        if th is None:
            return
        n = len(rows)
        flags = []  # (date,o,h,l,c,v,pct,is_limit,streak) 或 None
        s = 0
        for i in range(n):
            d, o, h, l, c, v, pct = rows[i]
            if not c or c <= 0 or pct is None:
                flags.append(None)
                continue
            lim = pct >= th
            s = s + 1 if lim else 0
            flags.append((d, o, h, l, c, v, pct, lim, s))
        # F1: 上报 (date, board=streak>=2, is_yizi, next_limit)
        for i, f in enumerate(flags):
            if not f:
                continue
            d, o, h, l, c, v, pct, lim, s = f
            if lim and s >= 2:
                is_yizi = abs(h - l) <= 1e-6 * max(c, 0.01)
                nxt_lim = 0
                if i + 1 < n and flags[i + 1]:
                    nxt_lim = 1 if flags[i + 1][7] else 0
                f1_rows.append((d, s, 1 if is_yizi else 0, nxt_lim))
            # F3: 首板(streak==1), 三口径前高量
            if lim and s == 1 and i >= 60 and i + 5 < n:
                t1 = flags[i + 1]
                t5 = flags[i + 5]
                if not t1 or not t5 or not v or not t1[5]:
                    continue
                win = [r for r in flags[i - 60:i] if r]
                win20 = [r for r in flags[i - 20:i] if r]
                if len(win) < 50 or not win20:
                    continue
                hi_close = max(r[4] for r in win)
                phv_a = None
                for r in reversed(win):
                    if abs(r[4] - hi_close) < 1e-9:
                        phv_a = r[5]
                        break
                phv_b = max(r[5] for r in win20)
                if not phv_a or phv_a <= 0 or not phv_b or phv_b <= 0:
                    continue
                o1_gain = (t1[1] / c - 1) * 100
                if o1_gain >= th - 0.4:
                    continue
                fwd5 = (t5[4] / t1[1] - 1) * 100
                f3_rows.append((d, phv_a, phv_b, v, t1[5], fwd5))

    f1_rows = []
    f3_rows = []
    for code, date, o, h, l, c, v, pct in cur:
        if code != cur_code:
            flush(cur_code, buf)
            cur_code, buf = code, []
        buf.append((date, o, h, l, c, v, pct))
    flush(cur_code, buf)
    con.close()
    print(f"[load] F1 events={len(f1_rows)}, F3 events={len(f3_rows)}")

    # ---- F1 聚合: (date, board) -> [n, yizi, promoted] ----
    agg = defaultdict(lambda: [0, 0, 0])
    for d, b, yz, pr in f1_rows:
        a = agg[(d, b)]
        a[0] += 1
        a[1] += yz
        a[2] += pr
    by_date = defaultdict(dict)
    for (d, b), (n, yz, pr) in agg.items():
        by_date[d][b] = (n, yz, pr)

    diffs, he_boards, yearly = [], defaultdict(int), defaultdict(list)
    for d, boards in sorted(by_date.items()):
        he = None
        best_yz = 0
        for b in sorted(boards, reverse=True):
            yz = boards[b][1]
            if yz > best_yz:
                best_yz, he = yz, b
        if he is None:
            continue
        he_boards[he] += 1
        n_he, _, pr_he = boards[he]
        oth_n = sum(v[0] for b, v in boards.items() if b != he)
        oth_pr = sum(v[2] for b, v in boards.items() if b != he)
        if n_he < 3 or oth_n < 3:
            continue
        r_he, r_ot = pr_he / n_he, oth_pr / oth_n
        diffs.append(r_he - r_ot)
        yearly[d[:4]].append(r_he - r_ot)

    f1_out = {
        "def": "高能量板位=当日一字板最多的连板板位; 对照其晋级率-其他板位合并晋级率(pp)",
        "days_compared": len(diffs),
        "he_board_dist": {str(k): v for k, v in sorted(he_boards.items())},
        "diff_mean_pp": mean(diffs),
        "diff_median_pp": med(diffs),
        "he_better_ratio": round(sum(1 for x in diffs if x > 0) / len(diffs), 4) if diffs else None,
        "yearly_mean_pp": {y: mean(v) for y, v in sorted(yearly.items())},
    }

    # ---- F3 聚合: 三口径对照 (A=60日最高收盘日量 B=近20日最大量 C=首板日量) ----
    buckets = [(0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.1), (1.1, 1.5), (1.5, 1e9)]

    def bucket_key(r):
        for lo, hi in buckets:
            if lo <= r < hi:
                return f"{int(lo*100)}-{int(hi*100)}%" if hi < 1e9 else ">150%"
        return "NA"

    modes = {"A_60dhighclosevol": 1, "B_20dmaxvol": 2, "C_firstlimit_vol": 3}
    f3_out = {
        "def": "首板次日T1量/参照量 比值分桶 -> fwd5=close(T+5)/open(T1)-1 %; 可实现过滤(T1开盘涨幅<阈值-0.4pp)",
        "n_total": len(f3_rows),
        "baseline_fwd5_med": med([x[5] for x in f3_rows]),
        "modes": {},
    }
    for mname, idx in modes.items():
        bstat = defaultdict(list)
        yr_best = defaultdict(list)
        for x in f3_rows:
            ref = x[idx]
            if not ref or ref <= 0:
                continue
            r = x[4] / ref
            k = bucket_key(r)
            bstat[k].append(x[5])
            if k == "70-90":
                yr_best[x[0][:4]].append(x[5])
        f3_out["modes"][mname] = {
            "buckets": {k: {"n": len(v), "med": med(v), "mean": mean(v),
                            "win": round(sum(1 for x in v if x > 0) / len(v), 4)}
                        for k, v in sorted(bstat.items())},
            "b70_90_yearly_med": {y: med(v) for y, v in sorted(yr_best.items())},
        }

    out = {"f1_high_energy_ladder": f1_out, "f3_prevhigh_vol80": f3_out}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
