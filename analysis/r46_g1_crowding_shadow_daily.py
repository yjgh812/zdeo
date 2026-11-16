# -*- coding: utf-8 -*-
"""G1_CROWDING_PCTL 市值分组成交额占比分位影子日更(风格拥挤状态, 观测层零交易权)
对应 PREREG_G1_CROWDING_PCTL_SHADOW_20261009, 主人2026-10-09「升级」令激活影子层。
口径与验证轮 analysis/r46_g1_smallcap_crowding_study_20261009.py 逐位一致:
daily_basic.total_mv 按日排序 -> 微盘组(最低400)/中证500代理(排名301-800)成交额占比
(qfq库amount), 250日滚动分位=拥挤度; warn线=本方分布描述线(影子期只记不发)。
参数单一真源=data/PREREG_G1_CROWDING_PCTL_SHADOW_20261009.json params 字段
(缺字段即fail-closed退出, 禁止回退硬编码)。
幂等: 全史重算+台账原子覆盖, 重跑安全。运行约1-2分钟。
边界: 零交易权禁接闸门; 代理口径=市值排名非真实中证500指数成分已标注。

用法: python analysis/r46_g1_crowding_shadow_daily.py [--report]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_G1_CROWDING_PCTL_SHADOW_20261009.json")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
LEDGER = Path(r"D:\zcodesj\data\g1_crowding_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\g1_crowding_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\g1_crowding_shadow_brief_latest.txt")


def fail(msg: str):
    print(f"[G1-SHADOW][FATAL] {msg}")
    sys.exit(2)


def load_params() -> dict:
    if not PREREG.exists():
        fail(f"prereg 缺失 {PREREG}")
    d = json.loads(PREREG.read_text(encoding="utf-8"))
    if d.get("status") != "ACTIVE":
        fail(f"prereg status={d.get('status')} 非 ACTIVE, fail-closed")
    p = d.get("params") or {}
    for k in ("micro_n", "zz500_band", "roll", "warn_zz500_pctl", "warn_micro_pctl", "min_stocks"):
        if k not in p:
            fail(f"prereg params 缺 {k}, fail-closed")
    return p


def group_rows(cursor, key_idx):
    cur_key, buf = None, []
    for row in cursor:
        k = row[key_idx]
        if k != cur_key:
            if cur_key is not None:
                yield cur_key, buf
            cur_key, buf = k, []
        buf.append(row)
    if cur_key is not None:
        yield cur_key, buf


def main() -> None:
    p = load_params()
    micro_n, zlo, zhi = int(p["micro_n"]), int(p["zz500_band"][0]), int(p["zz500_band"][1])
    roll, wz, wm, min_stocks = int(p["roll"]), float(p["warn_zz500_pctl"]), float(p["warn_micro_pctl"]), int(p["min_stocks"])

    con_q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    cur_q = con_q.cursor()
    con_f = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    cur_f = con_f.cursor()

    # 2026-10-19 a1提速(夜链瓶颈审计呈请): 合并行(逐日期 micro_pct/zz500_pct)缓存
    # (data/g1_crowding_rows_cache.json, 原子覆盖), 夜间仅对缓存尾日期之后做
    # idx_qfq_date/idx_db_date 区间扫+同规则合并(秒级); 缓存缺失/损坏→全量重算
    # (=原行为)。合并按日期确定性, 产出与全量重算逐位一致(md5对拍为验收)。
    CACHE = Path(r"D:\zcodesj\data\g1_crowding_rows_cache.json")
    rows = []  # (date, micro_pct, zz500_pct) 未取整内部值
    cache_ok = False
    if CACHE.exists():
        try:
            ca = json.loads(CACHE.read_text(encoding="utf-8"))
            if ca.get("v") == 1 and isinstance(ca.get("rows"), list) and ca["rows"]:
                rows = [(r[0], r[1], r[2]) for r in ca["rows"]]
                cache_ok = True
        except Exception:
            cache_ok = False
    last_d = rows[-1][0] if cache_ok else None

    def _merge_rows(mv_iter, amt_map, exact_match=True):
        out = []
        for d_mv, rows_mv in mv_iter:
            kv = amt_map.get(d_mv)
            if kv is None:
                if exact_match:
                    continue
            merged = []
            for _d, ts, mv in rows_mv:
                if not mv or mv <= 0:
                    continue
                a = kv.get(ts[:6]) if kv else None
                if a and a > 0:
                    merged.append((ts[:6], mv, a))
            if len(merged) < min_stocks:
                continue
            merged.sort(key=lambda x: x[1])
            total = sum(x[2] for x in merged)
            out.append((d_mv, sum(x[2] for x in merged[:micro_n]) / total * 100,
                        sum(x[2] for x in merged[zlo - 1:zhi]) / total * 100))
        return out

    if cache_ok:
        # 区间增量: 两表均走日期索引
        amt_map = {}
        for d, code, amount in cur_q.execute(
                """SELECT date, code, amount FROM stock_daily_meta_qfq
                   WHERE (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%') AND date > ?
                   ORDER BY date""", (last_d,)):
            amt_map.setdefault(str(d), {})[code] = (amount or 0)
        def mv_iter():
            for d, ts, mv in cur_f.execute(
                    "SELECT trade_date, ts_code, total_mv FROM daily_basic WHERE trade_date > ? "
                    "ORDER BY trade_date", (last_d,)):
                yield (str(d), ts, mv)
        grp = {}
        for d, ts, mv in mv_iter():
            grp.setdefault(str(d), []).append((str(d), ts, mv))
        rows.extend(_merge_rows(sorted(grp.items()), amt_map))
        con_q.close()
        con_f.close()
        tmpc = CACHE.with_suffix(".json.tmp")
        tmpc.write_text(json.dumps({"v": 1, "rows": rows}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmpc, CACHE)
    else:
        cur_q.execute(
            """SELECT date, code, amount FROM stock_daily_meta_qfq
               WHERE date >= '20160101' AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
               ORDER BY date"""
        )
        cur_f.execute(
            "SELECT trade_date, ts_code, total_mv FROM daily_basic WHERE trade_date >= '20160101' ORDER BY trade_date"
        )
        amt_iter = group_rows(cur_q, 0)
        amt_cache, amt_date, amt_done = {}, None, False
        for d_mv, rows_mv in group_rows(cur_f, 0):
            while not amt_done and (amt_date is None or amt_date < d_mv):
                try:
                    d_q, rows_q = next(amt_iter)
                    amt_cache = {r[1]: (r[2] or 0) for r in rows_q if r[2]}
                    amt_date = d_q
                except StopIteration:
                    amt_done = True
                    break
            if amt_date != d_mv:
                continue
            got = _merge_rows([(d_mv, rows_mv)], {d_mv: amt_cache})
            rows.extend(got)
        con_q.close()
        con_f.close()
        tmpc = CACHE.with_suffix(".json.tmp")
        tmpc.write_text(json.dumps({"v": 1, "rows": rows}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmpc, CACHE)
    if len(rows) < roll + 5:
        fail(f"序列过短 {len(rows)}")

    pm_hist, p5_hist = [], []
    ledger = []
    nan_cnt = 0
    for d, pm, p5 in rows:
        pr_pm = round(sum(1 for v in pm_hist if v <= pm) / len(pm_hist) * 100, 1) if len(pm_hist) >= 20 else None
        pr_5 = round(sum(1 for v in p5_hist if v <= p5) / len(p5_hist) * 100, 1) if len(p5_hist) >= 20 else None
        if pr_pm is None or pr_5 is None:
            nan_cnt += 1
        flags = []
        if pr_5 is not None and pr_5 >= wz:
            flags.append("zz500_extreme")
        if pr_pm is not None and pr_pm >= wm:
            flags.append("micro_crowded")
        ledger.append({"date": d, "micro_pct": round(pm, 2), "zz500_pct": round(p5, 2),
                       "micro_pctl": pr_pm, "zz500_pctl": pr_5, "flags": flags})
        pm_hist.append(pm)
        p5_hist.append(p5)

    last = ledger[-1]
    gap_days = 0 if last["date"] < "20261009" else 0  # activation未到=0; 生效后由日报断供
    snap = {
        "asof": last["date"],
        "micro_pct": last["micro_pct"], "zz500_pct": last["zz500_pct"],
        "micro_pctl": last["micro_pctl"], "zz500_pctl": last["zz500_pctl"],
        "flags": last["flags"],
        "days_since_activation_data": gap_days,
        "nan_ratio": round(nan_cnt / len(ledger), 4),
        "warn_lines": {"zz500_extreme": f"pctl>={wz}(本方分布描述线, 非民间阈值)", "micro_crowded": f"pctl>={wm}"},
        "acceptance": "60交易日; kill=数据断供>5交易日或NaN>10%提请停影; warn只记不发",
    }
    LEDGER.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ledger) + "\n", encoding="utf-8")
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        f"G1拥挤度分位影子 {snap['asof']} micro={last['micro_pct']}%({last['micro_pctl']}pctl) zz500代理={last['zz500_pct']}%({last['zz500_pctl']}pctl) flags={last['flags'] or '无'}",
        f"NaN比={snap['nan_ratio']} 断供检查=activation后{gap_days}日",
        f"验收门: {snap['acceptance']}",
        "边界: 风格拥挤状态描述, 零交易权; 代理口径=市值排名非真实指数成分",
    ]
    BRIEF.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if "--report" in sys.argv:
        print("\n".join(lines))
    print(f"[G1-SHADOW] OK days={len(ledger)} asof={snap['asof']} flags={last['flags']}")


if __name__ == "__main__":
    main()
