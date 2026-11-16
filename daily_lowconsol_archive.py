# -*- coding: utf-8 -*-
"""低位横盘前瞻验证队列·每日归档与影子篮子记录 (零交易权)。

每日(建议22:40, 扫描器22:32产出后)运行一次:
  1) 按日期留档 data/lowconsol_signal_archive/lowconsol_signal_<scan_date>.json
  2) 计算旗舰影子篮子(M1口径: main_hit池 过滤688, 按volr降序Top10) 并追加到
     data/lowconsol_shadow_track.jsonl — 供 prereg_lowconsol_volr_basket_20261025 前瞻重验
  3) 幂等: 同一scan_date重复运行不产生重复行
P&L跟踪由会话侧(westock MCP)另行追加, 本脚本不触碰交易。
用法: python daily_lowconsol_archive.py
"""
import json
import os
import sys

BASE = r"D:\zcodesj\data"
LATEST = os.path.join(BASE, "lowconsol_signal_latest.json")
ARCH_DIR = os.path.join(BASE, "lowconsol_signal_archive")
TRACK = os.path.join(BASE, "lowconsol_shadow_track.jsonl")
TOPN = 10


def dipday_detect():
    """急跌日触发器检测(prereg#6 B−D前瞻候选): 等权指数单日收益<=-2% → 记录触发+提示。
    依据: 急跌日次日开盘买入vs平常日 T+20超额+1.80pp(127日样本, 2020-2026); 样本外验证前零交易权,
    触发仅提示主人决策。幂等: 同日重复运行不重复记录。"""
    import sqlite3
    LOG = os.path.join(BASE, "dipday_trigger_log.jsonl")
    DIP_THR = -2.0
    try:
        con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
        row = con.execute(
            "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
            "WHERE code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%' "
            "GROUP BY date ORDER BY date DESC LIMIT 2").fetchall()
        con.close()
        if len(row) < 1:
            print("[dipday] 无数据"); return
        latest_d, eq_ret = row[0][0], float(row[0][1])
        prev_d, prev_ret = (row[1][0], float(row[1][1])) if len(row) > 1 else (None, None)
        seen = set()
        if os.path.exists(LOG):
            for line in open(LOG, encoding="utf-8"):
                try:
                    seen.add(json.loads(line).get("date"))
                except Exception:
                    pass
        if latest_d in seen:
            return
        is_dip = eq_ret <= DIP_THR
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"date": latest_d, "eq_ret_pct": round(eq_ret, 2),
                                "prev_date": prev_d, "prev_eq_ret": round(prev_ret, 2) if prev_ret is not None else None,
                                "is_dip": bool(is_dip), "threshold": DIP_THR}, ensure_ascii=False) + "\n")
        if is_dip:
            print(f"[dipday] *** 急跌日触发 {latest_d} 等权收益={eq_ret:.2f}%≤-2% — "
                  f"前瞻假设: 次日开盘篮子(过闸等权)T+20历史超额+1.8pp; 样本外验证中, 仅提示零交易权 ***")
        else:
            print(f"[dipday] {latest_d} 等权收益={eq_ret:.2f}% 未触发(阈-2%)")
    except Exception as e:
        print(f"[dipday] 检测异常(不阻断归档): {e}")


def main():
    dipday_detect()
    if not os.path.exists(LATEST):
        print("[archive] latest.json 不存在, 数据未更新"); return 1
    sig = json.load(open(LATEST, encoding="utf-8"))
    scan_date = str(sig.get("scan_date", ""))
    if not scan_date:
        print("[archive] scan_date 缺失"); return 1

    os.makedirs(ARCH_DIR, exist_ok=True)
    arch_path = os.path.join(ARCH_DIR, f"lowconsol_signal_{scan_date}.json")
    if not os.path.exists(arch_path):
        with open(arch_path, "w", encoding="utf-8") as f:
            json.dump(sig, f, ensure_ascii=False, indent=2)
        print(f"[archive] 留档 {arch_path}")
    else:
        print(f"[archive] {scan_date} 已留档, 跳过")

    mh = sig.get("main_hit_list", [])
    basket = sorted(
        (r for r in mh if not r.get("star") and r.get("volr") is not None),
        key=lambda r: -r["volr"])[:TOPN]
    shadow = [{"code": r["code"], "name": r["name"], "volr": round(r["volr"], 2),
               "close": r["close"], "pos250": round(r.get("pos250", 0), 1)}
              for r in basket]

    seen = set()
    if os.path.exists(TRACK):
        with open(TRACK, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line).get("scan_date"))
                except Exception:
                    pass
    queue_mtm(scan_date)
    if scan_date in seen:
        print(f"[track] {scan_date} 已在影子日志, 跳过"); return 0
    row = {"scan_date": scan_date,
           "n_total": sig.get("n_total"), "n_main_hit": sig.get("n_main_hit"),
           "amt_q": sig.get("phase_switch", {}).get("amt_q"),
           "switch": sig.get("phase_switch", {}).get("switch"),
           "shadow_basket": shadow}
    with open(TRACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[track] 影子篮子已记录 scan_date={scan_date} 池={sig.get('n_main_hit')} "
          f"相位={row['switch']}(amt_q={row['amt_q']})")
    for i, s_ in enumerate(shadow, 1):
        print(f"  {i:>2} {s_['code']} {s_['name']:<6} volr={s_['volr']:.2f} "
              f"价={s_['close']} pos250={s_['pos250']}%")


def queue_mtm(scan_date: str):
    """队列持仓MTM(前瞻验证证据流): 台账entry价 × qfq最新收盘, 每日一行追加。
    幂等: 同scan_date已在queue_mtm_track中则跳过。entry=台账委托价(限价成交口径, 如实声明)。"""
    QMTM = os.path.join(BASE, "queue_mtm_track.jsonl")
    QUEUE_MARK = ("PREREG_LOWCONSOL_FORWARD_QUEUE", "WEAKPHASE_POOL_BATCH2")
    if os.path.exists(QMTM):
        for line in open(QMTM, encoding="utf-8"):
            try:
                if json.loads(line).get("scan_date") == scan_date:
                    print(f"[queue] {scan_date} MTM已记录, 跳过"); return
            except Exception:
                pass
    entries = {}
    try:
        for line in open(os.path.join(BASE, "executor_orders.jsonl"), encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("action") == "BUY" and any(m in (d.get("note") or "") for m in QUEUE_MARK):
                code = d["code"]
                if code not in entries and d.get("price"):
                    entries[code] = {"entry": float(d["price"]), "entry_date": str(d.get("trade_date", ""))}
    except FileNotFoundError:
        print("[queue] 台账不存在"); return
    if not entries:
        print("[queue] 无队列持仓"); return
    import sqlite3
    con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    mtm = {}
    for code, e in entries.items():
        r = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 1",
            (code,)).fetchone()
        if r and r[1] and r[1] > 0:
            d0 = con.execute(
                "SELECT COUNT(DISTINCT date) FROM stock_daily_meta_qfq WHERE code=? AND date>? AND date<=?",
                (code, e["entry_date"], r[0])).fetchone()[0]
            mtm[code] = {"entry": e["entry"], "close": float(r[1]),
                         "ret_pct": round((float(r[1]) / e["entry"] - 1) * 100, 2),
                         "days_held": int(d0)}
    con.close()
    with open(QMTM, "a", encoding="utf-8") as f:
        f.write(json.dumps({"scan_date": scan_date, "queue_mtm": mtm}, ensure_ascii=False) + "\n")
    print("[queue] 队列MTM已记录: " + ", ".join(
        f"{c} {v['ret_pct']:+.2f}%({v['days_held']}d)" for c, v in mtm.items()))


if __name__ == "__main__":
    sys.exit(main())
