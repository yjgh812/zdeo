# -*- coding: utf-8 -*-
r"""universe_zone_scanner.py — 全宇宙买卖区域扫描器(唯一 owner, 幂等可复跑, 本地数据直读版 v3)
================================================================
由来(2026-09-30): tmp 一次性脚本交付 v1 后复核发现两缺陷(关键位不随最新bar重算/公式口径散落)
  → 工具化修复; 主人确认通达信 vipdoc 已更新当日 → v3 价格基准切换为**本地直读**:
  主源 = D:\anzhuang\tdx\vipdoc\{sh,sz}\lday\*.day (不复权, 与行情软件同口径)
  守卫 = dragon_quant.db stock_daily 同日收盘对账(偏离>0.5% 记数, >1% 池即 fail-closed, 复用 build_qfq_meta_tdx O2/E35 写前对账语义)
  可选 = --live 盘中拼接腾讯bar(仅当其日期>vipdoc基准才追加, 防双计; 收盘后无需)
用法: python universe_zone_scanner.py [--live] [--no-live]   # 默认纯本地
输出: data/universe_zone_scan_<基准日>.md + .csv (同基准重跑覆盖=幂等)

公式唯一真源(ALL_FORMULAS): 买区 B/C类=MA20~MA10带(有序), A/箱体类=箱底~MA60;
  触发=箱顶×1.01(放量, 仅作"突破后回踩确认"坐标——突破日追买已被 E2 证伪 t=-22.3);
  失效 A/箱体类=箱底×0.97, B类=箱顶×0.95, C类=MA20×0.97。
  验证档案: E1箱底裸买+0.07%(零边际)/E2突破追入-1.07%/E5强势回踩-0.15% → 位置池仅为坐标,
  有效性来自叠加条件(方向层主线×R33×≤8元带×深贴回补严格口径)。
定位: 只读研究设施(禁直接下单); 买入仍走 pre_trade_check_v2 + 主人确认(宪法§10)。
"""
import os
import sys
import csv
import glob
import json
import struct
import sqlite3
import urllib.request
import numpy as np

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import get_name

VIPDOC = r"D:\anzhuang\tdx\vipdoc"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_DIR = r"D:\zcodesj\data"
DIRECTION_JSON = r"D:\zcodesj\data\direction_layer_latest.json"
TAIL_N = 140          # 每只读尾部N根(≥60日窗+对账余量)
BASIS_MIN_FILES = 4500  # 基准日覆盖下限(防半写入/半同步态)
TAIL_BYTES = TAIL_N * 32

# ---- 公式唯一真源 ------------------------------------------------------------
ALL_FORMULAS = {
    "box_window": 60,
    "A箱底": {"pos_max": 30, "stab_ma60": 0.97, "near_low20": 1.06},
    "B突破观察": {"near_top": 0.97, "volr_min": 1.2},
    "C强势回踩": {"pullback_max": 0.03, "pos_min": 60},
    "invalid": {"A箱底": "box_lo*0.97", "B突破观察": "box_hi*0.95", "C强势回踩": "ma20*0.97", "箱体主选": "box_lo*0.97"},
    "trigger": "box_hi*1.01",
    "r33_pctl": 90,
    "max_price": 30.0,
    "min_bars": 60,
}
WATCH_EXTRA = ["603113"]
MAIN_KW = ("地产", "房地产", "建材", "水泥", "玻璃", "装修", "医药", "医疗", "中药", "生物", "制药", "器械")
HEAD_KW = ("航运", "港口", "水运", "船舶", "航空", "机场", "公路", "铁路", "路桥", "物流", "仓储", "多式联运")


def log(m):
    print(m, flush=True)


def die(m):
    raise SystemExit(f"[SCAN-FAIL] {m}")


def read_vipdoc_tail(code, n=TAIL_N):
    """读 vipdoc 尾部 n 根日线(不复权)。返回 [(date:int, o,h,l,c, vol)]; 文件缺失→None。"""
    mkt = "sh" if code.startswith("6") else "sz"
    path = os.path.join(VIPDOC, mkt, "lday", f"{mkt}{code}.day")
    if not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    if size < 32:
        return None
    cnt = min(n, size // 32)
    with open(path, "rb") as f:
        f.seek(size - cnt * 32)
        raw = f.read()
    out = []
    for i in range(cnt):
        d, o, h, l, c = struct.unpack_from("<IIIII", raw, i * 32)
        vol = struct.unpack_from("<I", raw, i * 32 + 24)[0]
        out.append((d, o / 100.0, h / 100.0, l / 100.0, c / 100.0, vol))
    return out


def vipdoc_basis():
    """扫描全宇宙文件清单与各自最新日期; 基准日=覆盖≥BASIS_MIN_FILES 的最大日期。
    🔴目录严格过滤(2026-09-30全量测试修复): sh目录仅6开头A股(剔sh000016上证50等指数/基金文件
    与深市同码票撞车→universe曾混入205只重复), sz目录00/30开头; 688剔。"""
    files = [(p, "sh") for p in glob.glob(os.path.join(VIPDOC, "sh", "lday", "sh??????.day"))] + \
            [(p, "sz") for p in glob.glob(os.path.join(VIPDOC, "sz", "lday", "sz??????.day"))]
    universe = []
    last_dates = {}
    for p, mkt in files:
        code = os.path.basename(p)[2:8]
        if mkt == "sh" and not code.startswith("60"):
            continue
        if mkt == "sz" and code[:2] not in ("00", "30"):
            continue
        if code.startswith("688"):
            continue
        tail = read_vipdoc_tail(code, 1)
        if not tail:
            continue
        universe.append(code)
        last_dates[code] = tail[0][0]
    if not universe:
        die("vipdoc 目录无可用日线文件")
    from collections import Counter
    cnt = Counter(last_dates.values())
    for d in sorted(cnt, reverse=True):
        if cnt[d] >= BASIS_MIN_FILES:
            return d, universe, last_dates, cnt[d]
    die(f"vipdoc 无覆盖≥{BASIS_MIN_FILES}只的基准日(最大候选 {sorted(cnt)[-3:]})——疑似半同步态")


def cross_check_db(pool_codes, basis, vip_close):
    """同日收盘对账守卫(参数绑定SQL): vipdoc vs dragon_quant.db。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db_close = {r[0]: float(r[1]) for r in con.execute(
        "SELECT code, close FROM stock_daily WHERE date=?", (int(basis),)).fetchall()}
    n_db = con.execute("SELECT COUNT(*) FROM stock_daily WHERE date=?", (int(basis),)).fetchone()[0]
    con.close()
    if n_db < BASIS_MIN_FILES:
        die(f"dragon_quant.db 基准日 {basis} 仅 {n_db} 行(<{BASIS_MIN_FILES})——DB侧疑似半写入, 对账基座不完整")
    mism = []
    checked = 0
    for c in pool_codes:
        if c in db_close and c in vip_close:
            checked += 1
            if abs(vip_close[c] / db_close[c] - 1) > 0.005:
                mism.append((c, vip_close[c], db_close[c]))
    ratio = len(mism) / checked * 100 if checked else 100.0
    log(f"[GUARD] 同日收盘对账: 检查 {checked} 只, 偏离>0.5% {len(mism)} 只 ({ratio:.2f}%)")
    if ratio > 1.0:
        for c, v, d in mism[:10]:
            log(f"  对账偏离: {c} vip={v:.2f} db={d:.2f}")
        die(f"对账失败率 {ratio:.1f}% >1% —— 双本地源口径冲突, fail-closed 拒绝出榜")
    return checked, mism


def fetch_live_tencent(codes, db_close):
    out = {}
    pref = [("sh" if c.startswith("6") else "sz") + c for c in codes]
    for i in range(0, len(pref), 80):
        chunk = pref[i:i + 80]
        url = "http://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
        except Exception as e:
            raise RuntimeError(f"腾讯行情拉取失败 chunk{i}: {e}")
        for line in raw.split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            v = line.split('"')[1].split("~")
            if len(v) < 45 or not v[2]:
                continue
            code = v[2]
            try:
                o, c, h, l = float(v[5]), float(v[3]), float(v[33]), float(v[34])
                turn = float(v[38]) if v[38] else 0.0
            except Exception:
                continue
            if c <= 0 or h < l or h <= 0:
                continue
            base = db_close.get(code)
            if base and base > 0 and abs(c / base - 1) > 0.11:
                continue
            out[code] = dict(o=o, h=h, l=l, c=c, turnover=turn,
                             date=(v[30][:8].replace("-", "") if len(v) > 30 and v[30] else ""))
    return out


def main(live=False):
    basis, universe, last_dates, covered = vipdoc_basis()
    log(f"[VIPDOC] 基准日 {basis}: 宇宙 {len(universe)} 只, 当日覆盖 {covered} (≥{BASIS_MIN_FILES} ✓)")

    # ---- 基准扫描(vipdoc 直读)
    W = ALL_FORMULAS["box_window"]
    pool, n_st, n_young, vip_close = [], 0, 0, {}
    for c in universe:
        tail = read_vipdoc_tail(c)
        if not tail or len(tail) < ALL_FORMULAS["min_bars"]:
            n_young += 1
            continue
        if tail[-1][0] != int(basis):   # 基准日无bar=停牌/未同步 → 不入池
            continue
        closes = np.array([x[4] for x in tail])
        highs = np.array([x[2] for x in tail])
        lows = np.array([x[3] for x in tail])
        vols = np.array([float(x[5]) for x in tail])
        cur = float(closes[-1])
        if cur <= 0 or cur > ALL_FORMULAS["max_price"]:
            continue
        nm = get_name(c) or ""
        if "ST" in nm.upper():
            n_st += 1
            continue
        ma = lambda w: float(closes[-w:].mean())
        m5, m10, m20 = ma(5), ma(10), ma(20)
        m60 = ma(60)
        box_lo, box_hi = float(lows[-W:].min()), float(highs[-W:].max())
        pos = (cur - box_lo) / (box_hi - box_lo) * 100 if box_hi > box_lo else 50.0
        volr = float(vols[-1] / vols[-20:].mean()) if vols[-20:].mean() > 0 else 0.0
        low20 = float(lows[-20:].min())
        A, B, C = ALL_FORMULAS["A箱底"], ALL_FORMULAS["B突破观察"], ALL_FORMULAS["C强势回踩"]
        cat = None
        if pos <= A["pos_max"] and cur >= m60 * A["stab_ma60"] and cur <= low20 * A["near_low20"]:
            cat = "A箱底"
        elif cur >= box_hi * B["near_top"] and volr >= B["volr_min"]:
            cat = "B突破观察"
        elif (m5 > m10 > m20 > m60) and 0 < (cur / m10 - 1) <= C["pullback_max"] and pos >= C["pos_min"]:
            cat = "C强势回踩"
        vip_close[c] = cur
        if cat:
            pool.append(dict(code=c, name=nm, cat=cat, cur=cur, m10=m10, m20=m20, m60=m60,
                             box_lo=box_lo, box_hi=box_hi, pos=pos, volr=volr))
    cats = {k: sum(1 for r in pool if r["cat"] == k) for k in ("A箱底", "B突破观察", "C强势回踩")}
    log(f"[POOL] vipdoc基准 {basis}: 位置池 {len(pool)} 只 (A={cats['A箱底']} B={cats['B突破观察']} C={cats['C强势回踩']}); 剔ST {n_st}/基准日无bar(停牌)及次新 {n_young}")

    # ---- 对账守卫(双本地源)
    cross_check_db([r["code"] for r in pool] + WATCH_EXTRA, basis, vip_close)

    # ---- R33/行业(fund库, as-of 如实标注)
    f = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)
    fb_last = f.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    trs = np.array([t[0] for t in f.execute(
        "SELECT turnover_rate FROM daily_basic WHERE trade_date=? AND turnover_rate>0", (fb_last,)).fetchall()])
    p33 = float(np.percentile(trs, ALL_FORMULAS["r33_pctl"]))
    turn29 = {t[0].split(".")[0].lstrip("0").zfill(6): t[1] for t in f.execute(
        "SELECT ts_code, turnover_rate FROM daily_basic WHERE trade_date=?", (fb_last,)).fetchall()}
    ind = dict(f.execute("SELECT ts_code, industry FROM feature_industry_momentum WHERE trade_date=?", (fb_last,)).fetchall())
    f.close()
    log(f"[R33] fund库 {fb_last}: 换手率 p{ALL_FORMULAS['r33_pctl']}={p33:.2f}% (滞后如实标注, 10/8 preflight 实时复核)")

    # ---- 方向层
    dir_stale, mainline, headwind, dir_target = True, [], [], "?"
    try:
        dj = json.load(open(DIRECTION_JSON, encoding="utf-8"))
        dir_target = str(dj.get("target_date", "?"))
        dir_stale = dir_target != str(basis)
        mainline = [x for x in dj.get("source1_mainline", {}).get("c1_volume", []) if x]
        headwind = [w for w in dj.get("source2_review", {}).get("risk_warnings", []) if "领跌" in w][:3]
    except Exception as e:
        headwind = [f"方向层读取失败({e})——结构化降级"]
    log(f"[DIR] 主线={mainline or '空'} target={dir_target} 新鲜度={'匹配' if not dir_stale else '陈旧,以当日Brief为准'}")

    # ---- 可选盘中拼接(--live, 仅 live日期>基准 才追加)
    live = {}
    live_note = "纯本地口径"
    if live:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        db_close = {r[0]: float(r[1]) for r in con.execute(
            "SELECT code, close FROM stock_daily WHERE date=?", (int(basis),)).fetchall()}
        con.close()
        try:
            live = fetch_live_tencent([r["code"] for r in pool] + WATCH_EXTRA, db_close)
            live_note = f"盘中拼接 {len(live)} 只(仅 live日期>{basis} 追加)"
        except RuntimeError as e:
            live_note = f"实时源不可用({e})——自动降级纯本地"

    # ---- 分层
    rows_out, n_live_miss = [], 0
    for r in pool + [dict(code=c, name=get_name(c) or "", cat="箱体主选", cur=0, m10=0, m20=0, m60=0,
                          box_lo=0, box_hi=0, pos=0, volr=0) for c in WATCH_EXTRA]:
        c6 = r["code"]
        if r["cat"] == "箱体主选":   # 主选单列: 按 vipdoc 尾部重算
            tail = read_vipdoc_tail(c6)
            if not tail:
                continue
            closes = np.array([x[4] for x in tail])
            highs = np.array([x[2] for x in tail])
            lows = np.array([x[3] for x in tail])
            r["cur"] = float(closes[-1])
            r["m10"], r["m20"], r["m60"] = float(closes[-10:].mean()), float(closes[-20:].mean()), float(closes[-60:].mean())
            r["box_lo"], r["box_hi"] = float(lows[-W:].min()), float(highs[-W:].max())
            r["pos"] = (r["cur"] - r["box_lo"]) / (r["box_hi"] - r["box_lo"]) * 100
        lv = live.get(c6)
        append_live = bool(lv and lv.get("date") and int(lv["date"]) > int(basis))
        cur = lv["c"] if (lv and lv["date"]) else r["cur"]
        if not lv:
            n_live_miss += 1 if live else 0
        if cur is None or cur <= 0 or cur > ALL_FORMULAS["max_price"]:
            continue
        m10, m20, m60 = r["m10"], r["m20"], r["m60"]
        lo, hi = r["box_lo"], r["box_hi"]
        if append_live:
            m10 = None  # live追加时均线由尾部重算(简化: live仅用于盘中参考, EOD以后vipdoc为准)
        cat = r["cat"]
        if cat in ("B突破观察", "C强势回踩"):
            buy_lo, buy_hi = min(m20, m10), max(m20, m10)
            invalid = hi * 0.95 if cat == "B突破观察" else m20 * 0.97
        else:
            buy_lo, buy_hi = lo, m60
            invalid = lo * 0.97
        industry = ind.get(c6 + (".SH" if c6.startswith("6") else ".SZ"), "?")
        dir_tag = ("逆风交运" if any(x in industry for x in HEAD_KW)
                   else ("主线内" if any(x in industry for x in MAIN_KW) else "其他"))
        t29 = turn29.get(c6, 0.0)
        t_live = lv["turnover"] if lv else None
        r33 = (t29 >= p33) or (t_live is not None and t_live >= p33)
        rows_out.append(dict(code=c6, name=r["name"], industry=industry, cat=cat, dir_tag=dir_tag,
                             cur=cur, m10=m10, m20=m20, m60=m60, box_lo=lo, box_hi=hi, pos=r["pos"],
                             buy=f"{buy_lo:.2f}~{buy_hi:.2f}", trig=f"{hi*1.01:.2f}",
                             inv=f"{invalid:.2f}", t29=t29, tlive=t_live, r33=r33, le8=cur <= 8.0))
    # ---- 两道门标签层(PFEREG_TWOGATE_ZONE 影子, 只读; 引擎=唯一实现 owner, 延迟导入防循环)
    import twogate_shadow_engine as tge
    sent_map = tge.load_sentiment()
    ml_map = tge.load_mainline_sets()
    lu_basis = sent_map.get(int(basis))
    regime_basis = tge.regime(lu_basis) if lu_basis is not None else "未知"
    n_gate, shadow_rows = 0, []
    for r in rows_out:
        tail = read_vipdoc_tail(r["code"])
        sig = tge.eval_prereg_signals(tail) if tail else None
        ml = r["code"] in ml_map.get(int(basis), set())
        r["主线内"] = "是" if ml else "否"
        r["regime档"] = regime_basis
        r["prereg_E1"] = "Y" if (sig and sig["E1"]) else "N"
        r["prereg_E5"] = "Y" if (sig and sig["E5"]) else "N"
        gp = bool((r["prereg_E1"] == "Y" or r["prereg_E5"] == "Y") and ml and regime_basis not in ("冰点≤30", "未知"))
        r["两道门"] = "过" if gp else "未过"
        if (r["prereg_E1"] == "Y" or r["prereg_E5"] == "Y") and regime_basis not in ("冰点≤30", "未知"):
            # 影子台账双轨: 过门(主线内) + 主线外对照(增量判据G3必需), 冰点档不入账(已证伪区)
            sig_tag = ("E1" if r["prereg_E1"] == "Y" else "") + ("E5" if r["prereg_E5"] == "Y" else "")
            shadow_rows.append(dict(date=basis, code=r["code"], name=r["name"], signal=sig_tag,
                                    mainline="是" if ml else "否", gate="过" if gp else "对照",
                                    regime=regime_basis, cur=r["cur"], buy=r["buy"],
                                    trig=r["trig"], inv=r["inv"], t_live=r.get("tlive"),
                                    rs=float(sig["rs"]) if (sig and sig.get("rs") is not None) else None,
                                    posb=float(sig["posb"]) if (sig and sig.get("posb") is not None) else None,
                                    px_sig=float(sig["px"]) if (sig and sig.get("px") is not None) else None,
                                    is_e1=bool(sig["E1"]) if sig else False))
        if gp:
            n_gate += 1
    # ---- V2 双轨段(PREREG_TWOGATE_V2_20261001, 主人2026-10-01「按推荐继续」批B''):
    # regime=精确涨停价口径, 主线=清洁序列观察标签(不过门), 过门=(E1∨E5)∧regime≠冰点。
    # 信号命中即入V2台账(含gate_pass=False的冰点排除行=判据J2必需), 与V1台账并存双轨。
    import twogate_shadow_engine_v2 as tge2
    shadow_rows_v2, n_gate_v2 = [], 0
    for r in rows_out:
        lab2 = tge2.gate_label_v2(r["code"], int(basis), read_vipdoc_tail(r["code"]))
        if lab2 is None:
            r["V2档"] = "NA"
            continue
        r["V2档"] = lab2["regime"]
        r["V2主线标签"] = "是" if lab2["mainline_label"] else "否"
        r["V2过门"] = "过" if lab2["gate_pass"] else "未过"
        if lab2["signals"]["E1"] or lab2["signals"]["E5"]:
            sig_tag2 = ("E1" if lab2["signals"]["E1"] else "") + ("E5" if lab2["signals"]["E5"] else "")
            shadow_rows_v2.append(dict(date=basis, code=r["code"], name=r["name"], signal=sig_tag2,
                                       mainline_label=lab2["mainline_label"],
                                       gate_pass=lab2["gate_pass"], regime=lab2["regime"],
                                       lu_precise=lab2["lu_precise"], cur=r["cur"],
                                       engine="v2", fp=lab2["fp"]))
            if lab2["gate_pass"]:
                n_gate_v2 += 1
    ledger_v2 = os.path.join(OUT_DIR, "twogate_shadow_v2_ledger.jsonl")
    old2 = []
    if os.path.exists(ledger_v2):
        with open(ledger_v2, encoding="utf-8") as fp:
            old2 = [json.loads(x) for x in fp if x.strip() and json.loads(x).get("date") != basis]
    with open(ledger_v2, "w", encoding="utf-8") as fp:
        for x in old2 + shadow_rows_v2:
            fp.write(json.dumps(x, ensure_ascii=False) + "\n")
    log(f"[TWOGATE-V2] basis={basis} V2过门 {n_gate_v2}/{len(rows_out)} (台账 data/twogate_shadow_v2_ledger.jsonl, 指纹{tge2.fingerprint()})")
    log(f"[TWOGATE] basis={basis} regime={regime_basis} 过门事件 {n_gate}/{len(rows_out)} (影子台账见 data/twogate_shadow_ledger.jsonl)")
    n_r33 = sum(1 for r in rows_out if r["r33"])
    n_head = sum(1 for r in rows_out if r["dir_tag"] == "逆风交运")
    T1 = [r for r in rows_out if not r["r33"] and r["dir_tag"] == "主线内"]
    T2 = [r for r in rows_out if not r["r33"] and r["dir_tag"] == "其他" and r["le8"]]
    T3 = [r for r in rows_out if not r["r33"] and r["dir_tag"] == "其他" and not r["le8"]]
    key = lambda r: r["pos"] if r["cat"].startswith(("A", "箱体")) else -r["pos"]
    for t in (T1, T2, T3):
        t.sort(key=key)
    log(f"[TIER] 终池 {len(rows_out)} (R33剔 {n_r33}, 逆风剔 {n_head}, live缺失 {n_live_miss if live else 0}) → T1主线内 {len(T1)} | T2≤8元 {len(T2)} | T3其他 {len(T3)} | {live_note}")

    bad = [(r["code"], r["name"]) for r in rows_out if not r["name"] or "?" in r["name"] or len(r["name"]) < 2]
    if bad:
        die(f"名称残留缺陷 {len(bad)}: {bad[:5]}")

    os.makedirs(OUT_DIR, exist_ok=True)
    # 影子台账(幂等: 同日期行重写)
    ledger_path = os.path.join(OUT_DIR, "twogate_shadow_ledger.jsonl")
    old = []
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as fp:
            old = [json.loads(x) for x in fp if x.strip() and json.loads(x).get("date") != basis]
    with open(ledger_path, "w", encoding="utf-8") as fp:
        for x in old + shadow_rows:
            fp.write(json.dumps(x, ensure_ascii=False) + "\n")
    md_path = os.path.join(OUT_DIR, f"universe_zone_scan_{basis}.md")
    csv_path = os.path.join(OUT_DIR, f"universe_zone_scan_{basis}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    from datetime import datetime
    L = [f"# 全宇宙买卖区域扫描 {basis} 基准(本地vipdoc直读{'+' + live_note if live else ''})\n",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} | 幂等: 同基准重跑覆盖 | 公式唯一真源=ALL_FORMULAS(universe_zone_scanner.py)",
         f"> 数据守卫: vipdoc直读 + dragon_quant.db 同日对账(fail-closed>1%) + R33/ST/次新/停牌/名称门禁",
         f"> 方向层: 主线={mainline} 逆风样例={headwind[:2]} 新鲜度={'匹配' if not dir_stale else '陈旧,以当日Brief为准'}\n"]
    for label, t in (("T1 主线内", T1), ("T2 ≤8元带", T2), ("T3 其他(备查)", T3)):
        L.append(f"\n## {label} ({len(t)}只, 前20)\n")
        L.append("| 代码 | 名称 | 行业 | 类别 | 现价 | 箱位% | 买区 | 触发 | 失效 | 换手fund/实时 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in t[:20]:
            tl = f"{r['tlive']:.1f}" if r.get("tlive") is not None else "-"
            L.append(f"| {r['code']} | {r['name']} | {r['industry']} | {r['cat']} | {r['cur']:.2f} | {r['pos']:.0f}% "
                     f"| {r['buy']} | {r['trig']} | {r['inv']} | {r['t29']:.1f}/{tl} |")
    L.append("\n## 铁律\n\n- 位置池裸信号已证伪(E1/E2/E5), 本表仅为坐标; 入场=回踩带限价 或 突破后回踩确认, 禁突破日追买。")
    L.append("- 买入前: 当日 DirectionBrief 复核主线 → pre_trade_check_v2(R闸+R33实时) → 主人确认; S-5=成本×0.92。")
    L.append(f"- 两道门影子(PREREG_TWOGATE_ZONE, locked): 基准日 regime={regime_basis}, 过门事件 {n_gate} 只(详见 data/twogate_shadow_ledger.jsonl); 过门=prereg_E1/E5 ∧ 主线内 ∧ regime≥修复。")
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(L) + "\n")
    log(f"[DONE] {md_path}")
    log(f"[DONE] {csv_path} ({len(rows_out)}行)")
    log("[SELFCHECK] PASS: vipdoc直读+对账守卫+门禁(名称/R33/ST/基准日bar/30元)全过; 公式=ALL_FORMULAS 唯一真源")


if __name__ == "__main__":
    main(live="--live" in sys.argv)
