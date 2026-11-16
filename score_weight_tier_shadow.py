#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_weight_tier_shadow.py — #802 score 加权持仓分层 60 日影子日更件
=====================================================================
prereg: data/PREREG_SCORE_WEIGHT_TIER_20261016.json (指纹 03021090…653, 参数单一真源,
        缺字段 fail-closed 退出; 立项依据=主人 2026-10-16「继续」令, 先例=10/9 强中卖哨 prereg)
语义:   bp_ep 月度 Top20(生产选股器 multifactor/bp_ep_monthly_picks.py 唯一真源, --no-sync 只读)
        按滚动 IS(向前全部年份月选 score)三分位切点分带, 高带权重 1.5 / 中带 1.0 / 低带 0.5
        (归一化), 对照=等权 base; 日频 qfq 收盘 mark-to-market, 链式日超额
        d=(1+r_w)/(1+r_b)-1, 激活日(20261016)起 60 交易日窗口。
三门:   G1 晋级门=窗口日>=60 且 窗口累计超额>=+1.0pp; G2 kill=20日滚动超额<-1.5pp;
        G3 kill=40日滚动超额<-1.0pp(kill 粘滞)。门读数仅记录, 判定权=影子末主人裁决。
边界:   零交易权零接线(不接 executor/闸门/交易 cron), 仅写自己台账;
        费用差=两臂同批持仓的二阶差, 影子不计(如实声明); 停牌股价格沿用最近收盘。
台账:   data/score_weight_tier_shadow_ledger.jsonl(按日期 UPSERT, 原子重写)
        + data/score_weight_tier_shadow_latest.json + data/score_weight_tier_scores_history.json
"""
import csv, json, os, sqlite3, subprocess, sys, tempfile
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
IDX_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
TRADES_CSV = r"D:\zcodesj\reports\backtest_full_20261015_eod\bp_ep_core_trades_full.csv"
PICKER = r"D:\zcodesj\multifactor\bp_ep_monthly_picks.py"
PICKS_DIR = r"D:\zcodesj\multifactor\reports\daily_top20"
PREREG_PATH = r"D:\zcodesj\data\PREREG_SCORE_WEIGHT_TIER_20261016.json"
LEDGER = r"D:\zcodesj\data\score_weight_tier_shadow_ledger.jsonl"
LATEST = r"D:\zcodesj\data\score_weight_tier_shadow_latest.json"
SCORES_HIST = r"D:\zcodesj\data\score_weight_tier_scores_history.json"
REBALANCE_ANCHOR = "每月第一个交易日(bp_ep 生产调仓口径)"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_params():
    p = json.load(open(PREREG_PATH, encoding="utf-8")).get("params")
    need = ["weights", "activation_since", "window_days", "gates", "window_hard_cap"]
    if not p or any(k not in p for k in need):
        raise SystemExit(f"[fail-closed] prereg params 缺字段: {PREREG_PATH}")
    return p


def connect_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def qfq_max_date():
    con = connect_ro(QFQ_DB)
    try:
        return con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    finally:
        con.close()


def index_max_date():
    con = connect_ro(IDX_DB)
    try:
        return con.execute("SELECT MAX(date) FROM index_daily").fetchone()[0]
    finally:
        con.close()


def trading_days_in_month(qfq_max, ym):
    """当月(截至今日)的 qfq 交易日列表, ym=YYYYMM; 月首个交易日=调仓日"""
    con = connect_ro(QFQ_DB)
    try:
        rows = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date LIKE ? AND date<=? ORDER BY date",
            (ym + "%", qfq_max)).fetchall()]
    finally:
        con.close()
    return rows


def closes_for(codes, d0, dmax):
    """返回 {code: {date: close}}(缺失日由调用方沿用前收)"""
    con = connect_ro(QFQ_DB)
    out = {c: {} for c in codes}
    try:
        for c in codes:
            for d, v in con.execute(
                    "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND date<=?",
                    (c, d0, dmax)):
                out[c][str(d)] = float(v)
    finally:
        con.close()
    return out


def bootstrap_scores_history():
    """切点底史 = 引擎 bp_ep 主臂全部已实现交易 score(按月); 返回 {'YYYYMM': [scores]}"""
    import re
    hist = {}
    for r in csv.DictReader(open(TRADES_CSV, encoding="utf-8-sig")):
        m = re.search(r"score=([0-9.]+)", r["买入决策理由"])
        if m:
            d = r["买入日期"].replace("-", "")[:6]
            hist.setdefault(d, []).append(float(m.group(1)))
    return hist


def load_scores_history():
    if os.path.exists(SCORES_HIST):
        return json.load(open(SCORES_HIST, encoding="utf-8"))
    return bootstrap_scores_history()


def save_json_atomic(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def cuts_from_prior(hist, cur_ym):
    """滚动 IS: 严格早于当前批次月的全部月选 score; 零前视"""
    pool = sorted(s for ym, ss in hist.items() if ym < cur_ym for s in ss)
    if len(pool) < 300:
        raise SystemExit(f"[fail-closed] 切点底史不足: {len(pool)} < 300")
    return pool[len(pool) // 3], pool[2 * len(pool) // 3], len(pool)


def compute_weights(batch, lo, hi, weights):
    """带权归一化: 高(s>=hi)×w_high/中×w_mid/低(s<=lo)×w_low, Σ=1; 返回 {code: w}"""
    tot_w = weights["high"] + weights["mid"] + weights["low"]
    per = [(weights["high"] if b["score"] >= hi else
            weights["low"] if b["score"] <= lo else weights["mid"]) / tot_w
           for b in batch]
    norm = sum(per)
    return {b["code"]: p / norm for b, p in zip(batch, per)}


def run_picker(date_yyyymmdd):
    """生产选股器只读调用(--no-sync 防生产名单副作用); 返回批次 [{code,name,score,close}]"""
    before = set(os.listdir(PICKS_DIR))
    r = subprocess.run([sys.executable, PICKER, "--date", date_yyyymmdd, "--no-sync"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=180, cwd=r"D:\zcodesj\multifactor")
    if r.returncode != 0:
        raise SystemExit(f"[fail-closed] 选股器非零退出 {r.returncode}: {(r.stderr or '')[-200:]}")
    new = [f for f in os.listdir(PICKS_DIR)
           if f.startswith(f"bp_ep_monthly_{date_yyyymmdd}_") and f.endswith(".csv")
           and f not in before]
    if not new:
        raise SystemExit("[fail-closed] 选股器未产出新 CSV")
    path = os.path.join(PICKS_DIR, sorted(new)[-1])
    batch = []
    for row in csv.DictReader(open(path, encoding="utf-8-sig")):
        batch.append({"code": str(row["code"]), "name": row.get("name", ""),
                      "score": float(row["composite_score"]), "close0": float(row["close"])})
    if len(batch) < 15:
        raise SystemExit(f"[fail-closed] 批次只数异常: {len(batch)} < 15")
    return batch, os.path.basename(path)


def chain(series):
    """链式: Π(1+x)-1, 输入为日超额序列(小数)"""
    out = 1.0
    for x in series:
        out *= (1.0 + x)
    return out - 1.0


def gates_state(window_days, window_cum, roll20, roll40, kill_flags, params):
    g = params["gates"]
    g1_thr = float(g["g1_promote_cum_pp"]); g2_thr = float(g["g2_kill_roll20_pp"])
    g3_thr = float(g["g3_kill_roll40_pp"])
    return {
        "G1_promote": {"armed": window_days >= int(params["window_days"]),
                       "pass": bool(window_days >= int(params["window_days"]) and window_cum * 100 >= g1_thr),
                       "threshold_pp": g1_thr},
        "G2_kill": {"triggered": bool(roll20 * 100 < g2_thr) or kill_flags.get("G2", False),
                    "threshold_pp": g2_thr},
        "G3_kill": {"triggered": bool(roll40 * 100 < g3_thr) or kill_flags.get("G3", False),
                    "threshold_pp": g3_thr},
    }



# ── a2 脚本级缓解(2026-10-19「按推荐继续」批准, SOP一百六十四a2项): 单实例守卫 ──
# 并发卫生守卫: 防本脚本被重复注册/手动双开时并发实例交错写同一台账
# (20261020 勘误: 初装依据「leader/swt 双注册」经查系清单扫描映射错位的假阳性——
# 二者实为不同脚本; 守卫保留作防御性卫生件, 正常单注册路径零触发)。
# 守卫=锁文件(PID|时间戳): 在位且持锁进程存活(或20分钟内不可判)→后到者 [SKIP]
# 让位 exit 0; 陈旧(进程死或 mtime>20分钟)→接管。psutil 判存活, 不可用退化
# mtime TTL 保守语义。退出 finally 清理(仅清自己 PID 的锁)。零交易语义变更。
_LOCK_PATH = r"D:\zcodesj\data\.score_weight_tier_shadow.lock"
_LOCK_TTL_S = 1200


def _pid_alive(pid):
    try:
        pid = int(str(pid).strip())
    except Exception:
        return None
    if pid <= 0:
        return None
    try:
        import psutil
        return bool(psutil.pid_exists(pid))
    except Exception:
        return None  # psutil 不可用 → 退化仅按 mtime TTL 判陈旧(保守)


def _acquire_single_instance():
    import time as _time
    for _attempt in range(2):
        try:
            fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()}|{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            return _LOCK_PATH
        except FileExistsError:
            pass
        try:
            age = _time.time() - os.path.getmtime(_LOCK_PATH)
            with open(_LOCK_PATH, encoding="utf-8") as f:
                pid = f.read().strip().split("|")[0]
            alive = _pid_alive(pid)
            if age < _LOCK_TTL_S and (alive is None or alive):
                return None  # 在位且持锁进程存活(或不可判) → 让位
            os.remove(_LOCK_PATH)  # 陈旧(进程死或>20分钟) → 接管
        except OSError:
            if os.path.exists(_LOCK_PATH):
                try:
                    os.remove(_LOCK_PATH)
                except OSError:
                    return None
    return None


def _release_single_instance(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                pid = f.read().strip().split("|")[0]
            if str(pid) == str(os.getpid()):
                os.remove(path)
    except OSError:
        pass


def main():
    params = load_params()
    weights = params["weights"]
    activation = str(params["activation_since"])

    # 1. 新鲜度闸: qfq 尾须>=指数尾(链后语义), 否则 fail-closed skip
    qm, im = qfq_max_date(), index_max_date()
    if str(qm) < str(im):
        log(f"[skip] qfq 尾 {qm} < 指数尾 {im}(链未落), fail-closed")
        return 0

    # 2. 当前批次(每月第一个交易日锚定; 已有则复用, 无则调生产选股器)
    hist = load_scores_history()
    qm_s = str(qm)
    cur_ym = qm_s[:6]
    month_days = trading_days_in_month(qm_s, cur_ym)
    d0 = month_days[0]
    state = json.load(open(LATEST, encoding="utf-8")) if os.path.exists(LATEST) else {}
    batch_info = state.get("batch") if state.get("batch", {}).get("month") == cur_ym else None
    if batch_info:
        batch = batch_info["picks"]
        d0 = batch_info["d0"]
        src = batch_info.get("source_file", "stored")
    else:
        batch, src = run_picker(d0)
        hist.setdefault(cur_ym, [])  # 当月 score 由批次补齐(去重月键)
        hist[cur_ym] = sorted({round(b["score"], 6) for b in batch} |
                              set(hist.get(cur_ym, [])))
        save_json_atomic(SCORES_HIST, hist)
        log(f"[batch] {cur_ym} 新批次 {len(batch)} 只 <- {src} (调仓锚={REBALANCE_ANCHOR})")

    # 3. 滚动 IS 切点(严格早于当月, 零前视)
    lo, hi, npool = cuts_from_prior(hist, cur_ym)
    w = compute_weights(batch, lo, hi, weights)
    wb = {b["code"]: 1.0 / len(batch) for b in batch}

    # 4. 日频 mark-to-market(qfq 缺价沿用前收=停牌平)
    prices = closes_for([b["code"] for b in batch], d0, qm_s)
    dates = sorted({d for c in prices.values() for d in c})
    last_px = {b["code"]: b["close0"] for b in batch}
    ledger = {}
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                ledger[row["date"]] = row
    prev = None
    for d in dates:
        sh_w = {c: w[c] / (prices[c][d0] if d0 in prices[c] else last_px[c]) for c in w}
        # 停牌沿用前收: 先补价
        px = {}
        for c in prices:
            p = last_px.get(c)
            for dd in dates:
                if dd in prices[c]:
                    p = prices[c][dd]
                px[(c, dd)] = p
        nav_w = sum(sh_w[c] * px[(c, d)] for c in w)
        nav_b = sum((1.0 / len(batch)) / prices[c][d0] * px[(c, d)] for c in w)
        if prev:
            dw = nav_w / prev[0] - 1.0
            db = nav_b / prev[1] - 1.0
            d_ex = (1.0 + dw) / (1.0 + db) - 1.0
            ledger[d] = {"date": d, "month": cur_ym, "nav_w": round(nav_w, 6),
                         "nav_b": round(nav_b, 6), "d_excess": round(d_ex, 8)}
        prev = (nav_w, nav_b)
        for c in prices:  # 滚动前收
            if prices[c].get(d):
                last_px[c] = prices[c][d]

    # 5. 窗口读数(激活日起) + 门
    days_all = sorted(ledger)
    win_days = [d for d in days_all if d >= activation]
    d_ex_series = [ledger[d]["d_excess"] for d in days_all]
    win_series = [ledger[d]["d_excess"] for d in win_days]
    cum_win = chain(win_series)
    roll20 = chain(d_ex_series[-20:]) if len(d_ex_series) >= 1 else 0.0
    roll40 = chain(d_ex_series[-40:]) if len(d_ex_series) >= 1 else 0.0
    kills = state.get("kill_flags", {})
    if roll20 * 100 < float(params["gates"]["g2_kill_roll20_pp"]):
        kills.setdefault("G2", d)
    if roll40 * 100 < float(params["gates"]["g3_kill_roll40_pp"]):
        kills.setdefault("G3", d)

    # 6. 原子落账 + latest
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LEDGER), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for d in sorted(ledger):
            f.write(json.dumps(ledger[d], ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
    hard_cap = str(params["window_hard_cap"])
    expired = win_days and (len(win_days) >= int(params["window_days"]) or win_days[-1] > hard_cap)
    new_state = {
        "asof": qm_s, "activation_since": activation, "window_day": len(win_days),
        "window_hard_cap": hard_cap, "window_expired": bool(expired),
        "batch": {"month": cur_ym, "d0": d0, "source_file": src, "picks": batch},
        "cuts": {"lo": lo, "hi": hi, "pool_n": npool},
        "weights": w,
        "readings": {"window_cum_excess_pp": round(cum_win * 100, 3),
                     "roll20_excess_pp": round(roll20 * 100, 3),
                     "roll40_excess_pp": round(roll40 * 100, 3)},
        "gates": gates_state(len(win_days), cum_win, roll20, roll40, kills, params),
        "kill_flags": kills,
        "boundary": "零交易权零接线, 判定权=影子末主人裁决",
    }
    save_json_atomic(LATEST, new_state)
    log(f"[ok] 窗口日={len(win_days)} 累计={cum_win*100:+.2f}pp roll20={roll20*100:+.2f}pp "
        f"roll40={roll40*100:+.2f}pp 切点=[{lo:.4f},{hi:.4f}](底史{npool}) 台账{len(ledger)}日")
    return 0


if __name__ == "__main__":
    _lk = _acquire_single_instance()
    if _lk is None:
        print("[skip] [SKIP] single-instance: 活跃实例持锁, 让位退出(并发卫生)")
        sys.exit(0)
    try:
        sys.exit(main())
    finally:
        _release_single_instance(_lk)
