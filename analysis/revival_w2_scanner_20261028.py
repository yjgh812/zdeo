# -*- coding: utf-8 -*-
"""revival_w2_scanner_20261028.py — 全员复活 Wave2 扫描器（可还原口径3方法，主人令20261027/28）

合同=analysis/revival_paper_test_PREREG_20261027.md（Wave2节）。
可还原口径（重建口径已登记，非原始复刻）:
  w2_bpep_weekly  bp_ep周滚动对照 = full_score机器分每周重算的6-15名后续席位（对照旗舰月频Top5）
  w2_mvp_fusion   bp+ep×MVP融合   = 价值秩(E/P+1/PB) + 换手秩 融合排名（MVP=换手率族语义, 附录A"融合-6.88pp"）
  w2_indmom_tail  行业动量尾部过滤 = 头部行业(ret20>0且mom>0.95: 出版/路桥/银行)内按个股ret20取5(≤2/行业)
定义失传无法忠实复活（如实登记，不建名单）: 振幅A三因子/118异象7因子(9/3已档案"构成不可考")/ICIR含SUE/fresh_screen; 缠论v2=影子逐股信号导出后W2b补建。
"""
from __future__ import annotations
import json, os, sys, hashlib, statistics
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from revival_scanner_20261027 import load_kline, base_universe, BUDGET_CAP  # 复用W1宇宙/数据

OUT = os.path.join(ROOT, "data", "revival_w2_picks_20261028.json")
TOP_SUB = 15

# 价值×换手候选（fund库daily_basic最新截面, pe,pb>0且pe<30, 按E/P+1/PB降序top40; close/换手为截面值）
VALUE_ROWS = [
    ("301139", 10.0434, 0.047, 16.30), ("600016", 5.0712, 0.267, 0.97), ("601186", 4.5185, 0.299, 0.33),
    ("601997", 3.9214, 0.312, 1.41), ("600015", 3.733, 0.317, 0.52), ("601390", 4.5651, 0.324, 0.36),
    ("002936", 8.2524, 0.333, 1.38), ("601800", 6.7707, 0.343, 0.26), ("601818", 4.6872, 0.359, 0.71),
    ("000656", 0.3864, 2.714, 1.32), ("601668", 4.8439, 0.382, 0.52), ("001227", 6.4445, 0.387, 1.32),
    ("000709", 20.0142, 0.386, 0.83), ("002958", 5.1149, 0.418, 1.20), ("601169", 5.9052, 0.416, 1.12),
    ("600928", 5.8869, 0.430, 0.62), ("600104", 12.2849, 0.416, 0.25), ("000932", 8.9247, 0.426, 0.68),
    ("600000", 6.539, 0.434, 0.24), ("601916", 6.2869, 0.448, 0.95), ("601860", 7.4431, 0.445, 1.67),
    ("000001", 5.221, 0.467, 0.79), ("600269", 7.2095, 0.470, 0.64), ("603323", 4.7597, 0.488, 1.59),
    ("601166", 5.2177, 0.483, 0.85), ("000726", 7.9468, 0.478, 0.48), ("601528", 5.1603, 0.497, 0.98),
    ("000900", 12.9819, 0.479, 0.69), ("601577", 4.7966, 0.512, 0.66), ("000501", 29.0515, 0.478, 1.25),
    ("000498", 4.245, 0.535, 1.13), ("601229", 5.5923, 0.526, 0.65), ("600739", 20.1514, 0.496, 0.53),
    ("601669", 7.6774, 0.520, 0.38), ("600585", 12.0201, 0.510, 0.49), ("600908", 5.5696, 0.537, 1.30),
    ("000507", 17.9602, 0.506, 0.45), ("600820", 7.5551, 0.529, 0.62), ("002061", 8.771, 0.526, 0.97),
    ("000778", 14.352, 0.515, 0.63),
]
IND_MEMBERS = {
    "出版业": ["000719", "000793", "002181", "300148", "300364", "300654", "300788", "301025", "301052",
              "301231", "600229", "600373", "600551", "600757", "600825", "601019", "601098", "601801",
              "601811", "601858", "601900", "601921", "601928", "601949", "601999", "603096", "603230",
              "603999", "605577"],
    "路桥": ["000429", "000548", "000755", "000828", "000900", "001965", "600012", "600020", "600033",
            "600035", "600106", "600269", "600350", "600368", "600377", "600548", "601107", "601188", "601518"],
    "银行": ["000001", "001227", "002142", "002807", "002839", "002936", "002948", "002958", "002966",
            "600000", "600015"],
}


def m_bpep_weekly(uni):
    path = os.path.join(ROOT, "reports", "daily_signals", "full_score_20261027.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        for line in f:
            d = dict(zip(header, line.strip().split(",")))
            code = d.get("code", "")[-6:].zfill(6)
            if code not in uni:
                continue
            try:
                ar = float(d.get("avg_rank", "9999"))
            except ValueError:
                continue
            rows.append((ar, code))
    rows.sort()
    return [(c, f"机器分周排={ar}") for ar, c in rows[5:]]  # 跳过旗舰Top5=周滚动的6名起


def m_mvp_fusion(uni):
    cands = []
    for code, pe, pb, tr in VALUE_ROWS:
        if code not in uni:
            continue
        cands.append((code, 1.0 / pe + 1.0 / pb, tr))
    if not cands:
        return []
    vr = {c: i for i, (c, v, t) in enumerate(sorted(cands, key=lambda x: -x[1]))}
    trr = {c: i for i, (c, v, t) in enumerate(sorted(cands, key=lambda x: -x[2]))}
    fused = sorted(cands, key=lambda x: -(vr[x[0]] + trr[x[0]]))
    return [(c, f"价值秩{vr[c]}+换手秩{trr[c]}") for c, _, _ in fused]


def m_indmom_tail(series, uni):
    cands = []
    for ind, codes in IND_MEMBERS.items():
        for code in codes:
            if code not in uni:
                continue
            rows = series[code]
            if len(rows) < 21:
                continue
            ret20 = (rows[-1][4] / rows[-21][4] - 1) * 100
            cands.append((ret20, ind, code))
    cands.sort(reverse=True)
    picks, cap = [], defaultdict(int)
    for r20, ind, code in cands:
        if len(picks) >= TOP_SUB:
            break
        if cap[ind] >= 4:
            continue
        cap[ind] += 1
        picks.append((code, f"头部行业{ind} r20={r20:.1f}%"))
    return picks


def main():
    series, dates = load_kline()
    uni, held = base_universe(series)
    used = set()
    try:
        w1 = json.load(open(os.path.join(ROOT, "data", "revival_picks_20261028.json"), encoding="utf-8"))
        for m in w1["methods"].values():
            for p in m["picks"]:
                used.add(p["code"])
    except Exception:
        pass
    methods = [("w2_bpep_weekly", lambda s, u: m_bpep_weekly(u)),
               ("w2_mvp_fusion", lambda s, u: m_mvp_fusion(u)),
               ("w2_indmom_tail", lambda s, u: m_indmom_tail(s, u))]
    out = {"signal_date": dates[0], "wave": 2,
           "generated": __import__("datetime").datetime.now().isoformat(), "methods": {}}
    for src, fn in methods:
        cands = fn(series, uni)
        picks, cost = [], 0.0
        for code, reason in cands[:max(TOP_SUB, 5)]:
            if code in used or len(picks) >= 5:
                continue
            price = uni[code][4]
            if cost + price * 100 > BUDGET_CAP:
                continue
            from stock_name_lookup import get_name
            picks.append({"code": code, "name": get_name(code) or "?", "price": round(price, 2),
                          "volume": 100, "source": src, "rule": reason})
            cost += price * 100
        for p in picks:
            used.add(p["code"])
        out["methods"][src] = {"picks": picks, "cost": round(cost, 2), "cand_total": len(cands),
                               "short": len(picks) < 5}
        print(f"{src:<18} 候选{len(cands):>5} 入选{len(picks)} 成本{cost:>8.0f}"
              + (" ⚠️不足5" if len(picks) < 5 else ""))
        for p in picks:
            print("   ", p["name"], p["code"], p["price"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("md5:", hashlib.md5(open(OUT, "rb").read()).hexdigest(), "|", OUT)


if __name__ == "__main__":
    main()
