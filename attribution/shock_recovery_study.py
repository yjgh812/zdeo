r"""震仓事件判别力预注册研究（零交易权，只读 qfq）。
方法来源：主人 2026-11-10 抖音四阶段框架讨论 + 「继续」令。
家族档案：PREREG_FIRSTBOARD_PULLBACK_20261025 (executed_FAIL, 同族入场信号已否)。
差异声明：本研究测震仓事件次日收复判别的区分力（纪律判据），非入场信号；接线需主人批准+新指纹。
用法：--fingerprint-only | --execute
"""
import hashlib, json, os, sqlite3, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT_DIR = r"D:\zcodesj\reports"
PRE_REG_PATH = r"D:\zcodesj\data\PREREG_SHOCK_RECOVERY_20261110.json"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

PARAMS = {
    "study_id": "SHOCK_RECOVERY_20261110",
    "qfq_db": QFQ_DB,
    "table": "stock_daily_meta_qfq",
    "window_start": "20160101",
    "universe": "剔4/8/9+688/689; bars>=60; 停牌占位行(vol<=0或high<=0)剔除",
    "board_rule": "主板chg>=9.8 创业板300/301/302>=19.8 close>=high*0.999 vol>0 不查连板(跟扫描器)",
    "structure": "复刻扫描器: T属板后D3/D4/D5, T缩量vol<=0.5板量 且 anchor<=close[T]<=anchor*1.03 且 板后到T区间收盘>=anchor",
    "event_E": "E属板后D1..T窗内首日: chg<=-3.0 且 low<=prev_close*0.96 且 close>=anchor",
    "recovery": "R=E+1: close>open 且 close>low(E); E+1无bar或停牌=unjudgeable",
    "holding": "持有=close[E+5]/close[E]-1; 回补描述=close[E+5]/close[E+2]-1(仅收复组); E+5缺=unjudgeable",
    "cooldown": 20,
    "gates": {
        "sample_gate": "可判n>=60 否则INSUFFICIENT_DATA",
        "M1": "未收复组持有t5均值/胜率门",
        "M2": "两组持有t5胜率差>=+10pp 且 n_rec>=40 且 n_norec>=20",
        "M3": "收复组持有t5均值>=0",
    },
    "deferred_dims": "-4/-5敏感性未测, 留待新指纹",
    "verdict_rule": "三门全过=PASS_CANDIDATE; 任一未过=FAIL; n<60=INSUFFICIENT_DATA",
}

_F = hashlib.sha256(json.dumps(PARAMS, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

# --- ANCHOR-FUNC ---


def load_code_bars(conn, code):
    rows = conn.execute("SELECT date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
                        "WHERE code=? AND date>=? ORDER BY date", (code, PARAMS["window_start"])).fetchall()
    return [r for r in rows if (r[5] is not None and r[5] > 0 and r[2] and r[2] > 0)]


def stats_of(xs):
    if not xs:
        return {"n": 0, "mean": None, "winrate": None}
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 5),
            "winrate": round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 2)}


def run_threshold(conn, chg_thr):
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq")]
    structures = events = 0
    hold_rec, hold_norec, topup_rec = [], [], []
    for code in codes:
        s = str(code)
        if s.startswith(("4", "8", "9")) or s.startswith(("688", "689")):
            continue
        bars = load_code_bars(conn, code)
        if len(bars) < 60:
            continue
        thr = 19.8 if s.startswith(("300", "301", "302")) else 9.8
        cooldown_until = -1
        for bi in range(len(bars) - 1):
            d0, o0, h0, l0, c0, v0, chg0 = bars[bi]
            if chg0 is None or chg0 < thr or c0 < h0 * 0.999 or v0 <= 0:
                continue
            if bi < cooldown_until:
                continue
            struct_ok, struct_ti = False, -1
            for toff in (3, 4, 5):
                ti = bi + toff
                if ti >= len(bars):
                    continue
                b_bars = bars[bi + 1: ti + 1]
                if len(b_bars) < 2:
                    continue
                t = bars[ti]
                if t[5] > v0 * 0.5:
                    continue
                if min(w[4] for w in b_bars) < c0:
                    continue
                if not (c0 <= t[4] <= c0 * 1.03):
                    continue
                struct_ok, struct_ti = True, ti
                break
            if not struct_ok:
                continue
            structures += 1
            e_i = None
            for k in range(bi + 1, struct_ti + 1):
                w = bars[k]
                if w[6] is None:
                    continue
                if w[6] <= chg_thr and w[3] <= bars[k - 1][4] * 0.96 and w[4] >= c0:
                    e_i = k
                    break
            if e_i is None:
                continue
            if e_i + 1 >= len(bars) or e_i + 5 >= len(bars):
                continue
            e1 = bars[e_i + 1]
            if e1[5] is None or e1[5] <= 0:
                continue
            recovered = (e1[4] > e1[1]) and (e1[4] > bars[e_i][3])
            e5c = bars[e_i + 5][4]
            hold = e5c / bars[e_i][4] - 1.0
            topup = None
            if e_i + 2 < len(bars) and bars[e_i + 2][5] and bars[e_i + 2][5] > 0:
                topup = e5c / bars[e_i + 2][4] - 1.0
            if recovered:
                hold_rec.append(hold)
                if topup is not None:
                    topup_rec.append(topup)
            else:
                hold_norec.append(hold)
            events += 1
            cooldown_until = e_i + 20
    return {"structures": structures, "events": events, "rec": stats_of(hold_rec),
            "norec": stats_of(hold_norec), "topup": stats_of(topup_rec)}


def verdict(res):
    rec, norec = res["rec"], res["norec"]
    n_total = rec["n"] + norec["n"]
    if n_total < 60:
        return "INSUFFICIENT_DATA", {"sample_gate": False, "n_judgeable": n_total}
    if rec["winrate"] is None or norec["winrate"] is None:
        return "FAIL", {"sample_gate": True, "n_judgeable": n_total, "error": "winrate missing"}
    m1 = (norec["mean"] < 0 and norec["winrate"] < 40.0)
    win_diff = rec["winrate"] - norec["winrate"]
    m2 = (rec["n"] >= 40 and norec["n"] >= 20 and win_diff >= 10.0)
    m3 = (rec["mean"] >= 0)
    return ("PASS_CANDIDATE" if (m1 and m2 and m3) else "FAIL"), {
        "sample_gate": True, "n_judgeable": n_total,
        "M1_defense": bool(m1), "M2_discrimination": bool(m2), "M3_recovery_cand": bool(m3),
        "winrate_diff_pp": round(win_diff, 2)}


def main():
    if "--fingerprint-only" in sys.argv:
        print(_F)
        return 0
    if "--execute" not in sys.argv:
        print("用法: --fingerprint-only | --execute")
        return 2
    if not os.path.exists(PRE_REG_PATH):
        print("[FAIL-CLOSED] 预注册缺失")
        return 2
    with open(PRE_REG_PATH, encoding="utf-8") as f:
        pre = json.load(f)
    if pre.get("fingerprint") != _F:
        print("[FAIL-CLOSED] 指纹不符: 预注册=%s 脚本=%s" % (pre.get("fingerprint"), _F))
        return 2
    conn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    res = run_threshold(conn, -3.0)
    conn.close()
    v, gd = verdict(res)
    out = {"study_id": PARAMS["study_id"], "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "fingerprint": _F, "prereg_ref": PRE_REG_PATH,
           "main_threshold_-3pct": {"structures": res["structures"], "events": res["events"],
                                    "recovered": res["rec"], "not_recovered": res["norec"],
                                    "topup_desc": res["topup"]},
           "gates": gd, "verdict": v, "deferred_dims": PARAMS["deferred_dims"]}
    jp = os.path.join(OUT_DIR, "SHOCK_RECOVERY_PREREG_RESULT_20261110.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({"gates": gd, "verdict": v}, ensure_ascii=False, indent=1))
    print("结构 %d / 事件 %d / 收复组 %s / 未收复组 %s / 回补(描述) %s" % (
        res["structures"], res["events"], res["rec"], res["norec"], res["topup"]))
    print("报告: %s" % jp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
