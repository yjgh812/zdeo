r"""prereg_shock_breakout_study.py — 震仓后放量收复突破前高入场口径预注册研究（零交易权，只读 qfq）。
方法来源: 主人 2026-11-10「继续」令, 验证第四份框架次优买点②严格版。
家族档案: PREREG_FIRSTBOARD_PULLBACK_20261025(FAIL, 确认日次收入场负先验) + PREREG_SHOCK_RECOVERY_20261110(PASS_CANDIDATE, 判别力成立, 回补口径-0.48%负)。
事件域与 shock_recovery_study 完全一致(S1-S4结构全集+事件E+20日冷却)。
差异=入场口径(框架原文冻结): 确认日C属于[E+1,E+3]首个满足: 放量(vol>=1.5x前5日均量, 倍数取自框架主买点标准) 且 收复失地(close>high(E)) 且 突破震仓前高(close>E前5日最高收盘); 入场=C次一交易日收盘(收盘确认口径, 同FIRSTBOARD先例)。
收益: 主口径=入场到C+5收盘; 辅助口径=入场到E+5收盘(与回补口径同终点可比)。
门禁LOCKED: G1 n>=40; G2 主口径t5均值>回补对照+1.0pp 且胜率>=45%; G3 主口径t5均值>0; 三门全过=PASS_CANDIDATE提请主人, 任一未过=FAIL(该口径判死, 翻案须新指纹)。
deferred: 量倍2.0x/前高窗口3日变体未测。
用法: --fingerprint-only | --execute
"""
import hashlib, json, os, sqlite3, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT_DIR = r"D:\zcodesj\reports"
PRE_REG_PATH = r"D:\zcodesj\data\PREREG_SHOCK_BREAKOUT_20261110.json"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

PARAMS = {
    "study_id": "SHOCK_BREAKOUT_20261110",
    "qfq_db": QFQ_DB,
    "table": "stock_daily_meta_qfq",
    "window_start": "20160101",
    "universe": "剔4/8/9+688/689; bars>=60; 停牌占位行剔除 (与shock_recovery_study一致)",
    "event_domain": "S1-S4结构全集(T属板后D3/D4/D5,T缩量<=0.5板量,anchor<=close<=anchor*1.03,区间收盘>=anchor) + 事件E(chg<=-3.0且low<=prev_close*0.96且close>=anchor, E属板后D1..T首日) + 同code事件后20bar冷却",
    "confirm_C": "C属[E+1,E+3]首个满足: vol>=1.5x其前5日均量 且 close>high(E) 且 close>max(close[E前5日])",
    "entry": "C次一交易日收盘(停牌占位剔除)",
    "returns": "主口径 ret5=close[C+5]/entry-1; 辅助口径 retE5=close[E+5]/entry-1; 回补对照=E+1收复时 E+2收盘买入到E+5 (同事件域直接对照)",
    "cooldown": 20,
    "gates": {
        "G1_sample": "可判入场 n>=40 否则INSUFFICIENT_DATA",
        "G2_relative": "主口径t5均值 > 回补对照均值+1.0pp 且 主口径胜率>=45%",
        "G3_absolute": "主口径t5均值 > 0",
        "verdict_rule": "三门全过=PASS_CANDIDATE(提请主人); 任一未过=FAIL(突破前高入场口径判死, 震仓只保留持有/离场纪律, 翻案须新指纹)",
    },
    "deferred_dims": "量倍2.0x/前高窗口3日变体未测, 留待新指纹",
}
_F = hashlib.sha256(json.dumps(PARAMS, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def load_code_bars(conn, code):
    rows = conn.execute("SELECT date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
                        "WHERE code=? AND date>=? ORDER BY date", (code, PARAMS["window_start"])).fetchall()
    return [r for r in rows if (r[5] is not None and r[5] > 0 and r[2] and r[2] > 0)]


def stats_of(xs):
    if not xs:
        return {"n": 0, "mean": None, "winrate": None}
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 5),
            "winrate": round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 2)}


def run(conn):
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq")]
    events = 0
    ret5_list, retE5_list, ref_list = [], [], []
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
            e_i = None
            for k in range(bi + 1, struct_ti + 1):
                w = bars[k]
                if w[6] is None:
                    continue
                if w[6] <= -3.0 and w[3] <= bars[k - 1][4] * 0.96 and w[4] >= c0:
                    e_i = k
                    break
            if e_i is None:
                continue
            if e_i + 5 >= len(bars):
                continue
            E = bars[e_i]
            e_pre = bars[max(e_i - 5, 0):e_i]
            if not e_pre:
                continue
            pre_high = max(b[4] for b in e_pre)
            c_i = None
            for k in range(e_i + 1, min(e_i + 4, len(bars))):
                w = bars[k]
                if w[5] is None or w[5] <= 0:
                    continue
                seg = bars[max(k - 5, 0):k]
                v5 = sum(b[5] for b in seg) / len(seg) if seg else 0
                if v5 <= 0 or w[5] < 1.5 * v5:
                    continue
                if not (w[4] > E[2] and w[4] > pre_high):
                    continue
                c_i = k
                break
            if c_i is None:
                continue
            if c_i + 1 >= len(bars) or c_i + 5 >= len(bars):
                continue
            entry = bars[c_i + 1]
            if entry[5] is None or entry[5] <= 0:
                continue
            ret5 = bars[c_i + 5][4] / entry[4] - 1.0
            retE5 = bars[e_i + 5][4] / entry[4] - 1.0
            ret5_list.append(ret5)
            retE5_list.append(retE5)
            e1 = bars[e_i + 1]
            if e1[5] and e1[5] > 0 and e1[4] > e1[1] and e1[4] > E[3] and e_i + 2 < len(bars):
                ref_list.append(bars[e_i + 5][4] / bars[e_i + 2][4] - 1.0)
            events += 1
            cooldown_until = e_i + 20
    return {"events": events, "entry": stats_of(ret5_list), "entryE5": stats_of(retE5_list),
            "ref_topup": stats_of(ref_list)}


def verdict(res):
    ent = res["entry"]
    ref = res["ref_topup"]
    if ent["n"] < 40:
        return "INSUFFICIENT_DATA", {"G1": False, "n": ent["n"]}
    if ent["mean"] is None or ref["mean"] is None:
        return "FAIL", {"G1": True, "error": "mean missing"}
    g2 = (ent["mean"] > ref["mean"] + 0.01) and (ent["winrate"] >= 45.0)
    g3 = ent["mean"] > 0
    return ("PASS_CANDIDATE" if (g2 and g3) else "FAIL"), {
        "G1": True, "G2_relative": bool(g2), "G3_absolute": bool(g3),
        "excess_vs_topup_pp": round((ent["mean"] - ref["mean"]) * 100, 2)}


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
    res = run(conn)
    conn.close()
    v, gd = verdict(res)
    out = {"study_id": PARAMS["study_id"], "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "fingerprint": _F, "prereg_ref": PRE_REG_PATH, "result": res,
           "gates": gd, "verdict": v, "deferred_dims": PARAMS["deferred_dims"]}
    jp = os.path.join(OUT_DIR, "SHOCK_BREAKOUT_PREREG_RESULT_20261110.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({"gates": gd, "verdict": v}, ensure_ascii=False, indent=1))
    print("事件 %d / 突破入场 %s / 辅助E+5 %s / 回补对照 %s" % (
        res["events"], res["entry"], res["entryE5"], res["ref_topup"]))
    print("报告: %s" % jp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
