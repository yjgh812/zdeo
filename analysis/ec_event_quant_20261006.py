# -*- coding: utf-8 -*-
"""案例⑥二池 买卖动作×K线机械验证: 实盘赛结构化台账(ec_spmatch_trades_20261006.json)
三场比赛(721/784/951)全部 买入/卖出 事件逐笔映射 qfq。
口径: pos60=当日收盘在前60日(不含当日)高低区间位置(与深位首板/案例④⑤同式);
绿盘低吸检验=买入日 change_pct<0 占比; 打板派对照=94%在H区(>80)。
噪声处理: 多股日列错位→动作字段在 act/stock 间任一命中即为该动作, 代码取 u 的 fullCode。
诚实注记: 784 段=2024-10~2025-03 强牛腿(924行情), fwd 含高 beta, 绝对值不可跨市况比较;
核心指标=pos60 分布(入场位置, regime 相对量)与买入日涨跌方向。
"""
import json
import re
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SRC = r"D:\zcodesj\data\ec_spmatch_trades_20261006.json"
ACTIONS = {"买入", "卖出", "减仓", "空仓", "持仓"}


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    events = []
    for match, rows in d["matches"].items():
        for r in rows:
            act = r["act"] if r["act"] in ACTIONS else (r["stock"] if r["stock"] in ACTIONS else None)
            if act not in ("买入", "卖出", "减仓"):
                continue
            m = re.search(r"fullCode=(?:sh|sz)(\d{6})", r.get("u") or "")
            dm = re.search(r"dateNum=(\d{8})", r.get("u") or "")
            if not m or not dm:
                continue
            code, date = m.group(1), int(dm.group(1))
            events.append({"match": match.split("_")[0], "date": date, "code": code,
                           "name": r["stock"] if act != "买入" and r["stock"] not in ACTIONS else (r["act"] if False else r["stock"]),
                           "act": act})
    # 去重
    seen = set()
    uniq = []
    for e in events:
        k = (e["date"], e["code"], e["act"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    print(f"动作事件(去重)={len(uniq)}  买入={sum(1 for e in uniq if e['act']=='买入')}  "
          f"卖出={sum(1 for e in uniq if e['act']=='卖出')}  减仓={sum(1 for e in uniq if e['act']=='减仓')}")

    def qfq_metrics(code, d):
        hist = conn.execute(
            "SELECT date,close,volume,change_pct FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date", (code,)).fetchall()
        dates = [int(x[0]) for x in hist]
        if d not in dates:
            return None
        i = dates.index(d)
        out = {"pos60": None, "volr": None, "chg": hist[i][3], "zt": bool(hist[i][3] is not None and hist[i][3] >= 9.5)}
        if i >= 59:
            w = [x[1] for x in hist[i - 59:i]]
            lo, hi = min(w), max(w)
            if hi > lo:
                out["pos60"] = round((hist[i][1] - lo) / (hi - lo) * 100, 1)
        if i >= 5 and sum(x[2] for x in hist[i - 5:i]) > 0:
            out["volr"] = round(hist[i][2] / (sum(x[2] for x in hist[i - 5:i]) / 5), 2)
        for k in (5, 10, 20):
            if i + k < len(hist):
                out[f"f{k}"] = round((hist[i + k][1] / hist[i][1] - 1) * 100, 2)
        return out

    def bucket(p):
        if p is None: return "N/A"
        if p <= 25: return "<=25深位"
        if p <= 40: return "25-40G"
        if p <= 80: return "40-80中"
        return ">80高"

    buys = []
    for e in uniq:
        if e["act"] != "买入":
            continue
        m = qfq_metrics(e["code"], e["date"])
        if m:
            e.update(m)
            e["bucket"] = bucket(m["pos60"])
            buys.append(e)
    print(f"\n=== 买入事件×K线 (可定位 {len(buys)} 笔) ===")
    for b in ("<=25深位", "25-40G", "40-80中", ">80高", "N/A"):
        sub = [e for e in buys if e["bucket"] == b]
        if not sub:
            print(f"  {b:8s} n=0")
            continue
        f5 = [e["f5"] for e in sub if "f5" in e]
        f20 = [e["f20"] for e in sub if "f20" in e]
        green = sum(1 for e in sub if e["chg"] is not None and e["chg"] < 0)
        line = f"  {b:8s} n={len(sub):3d} 绿盘买入={green}/{len(sub)}"
        if f5:
            s = sorted(f5)
            line += f"  f5 med={s[len(s)//2]:+.2f}% win={sum(1 for x in f5 if x>0)/len(f5)*100:.0f}%"
        if f20:
            s = sorted(f20)
            line += f"  f20 med={s[len(s)//2]:+.2f}%"
        print(line)
    chgs = [e["chg"] for e in buys if e["chg"] is not None]
    zt = sum(1 for e in buys if e["zt"])
    if chgs:
        s = sorted(chgs)
        print(f"\n买入日涨跌: 中位={s[len(s)//2]:+.2f}%  绿盘占比={green_all(chgs):.0f}%  涨停日买入={zt}/{len(chgs)}")
    sells = []
    for e in uniq:
        if e["act"] not in ("卖出", "减仓"):
            continue
        m = qfq_metrics(e["code"], e["date"])
        if m and "f5" in m:
            sells.append({**e, "f5": m["f5"], "f10": m.get("f10"), "f20": m.get("f20")})
    if sells:
        f5 = [e["f5"] for e in sells]
        s = sorted(f5)
        print(f"\n卖出/减仓后 f5: n={len(f5)} 中位={s[len(s)//2]:+.2f}% 胜率(卖后涨=卖飞方向)={sum(1 for x in f5 if x>0)/len(f5)*100:.0f}%")
    conn.close()


def green_all(chgs):
    return sum(1 for c in chgs if c < 0) / len(chgs) * 100


if __name__ == "__main__":
    main()
