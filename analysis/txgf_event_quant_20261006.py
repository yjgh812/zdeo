# -*- coding: utf-8 -*-
"""案例⑤退学炒股 锚点日×K线机械验证: 2019百万挑战段已读图事件
事件=读图所得(卖出日/持仓日明确者); pos60[T-1]/volr/is_zt/gene10/f5-f20 同案例④§九口径。
注: 买入日多数未直接可见(持仓快照反推), 本表仅验证「锚点日」位置与卖后走势。
"""
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

# date=锚点日(卖出/持仓快照日), act=SELL/HOLD, note=图面证据
EV = [
    (20190225, "000859", "国风塑业", "HOLD", "全仓43.12万股,成本7.009,浮盈+14.3%"),
    (20190304, "000070", "特发信息", "SELL", "清仓+29.19万(+7.3%),转持币400.8万"),
    (20190311, "603011", "合锻智能", "SELL", "清仓-10.98万,持币448.8万"),
    (20190318, "002356", "赫美集团", "HOLD", "满仓36.3万股,成本15.923,现价17.15"),
    (20190325, "002307", "北新路桥", "SELL", "清仓,持币735.1万=赫美崩塌(3-27起5连跌停)前逃顶"),
    (20190410, "300099", "精准信息", "SELL", "清仓-23.48万"),
    (20190410, "300194", "福安药业", "SELL", "清仓-68.36万(账户735→541万两周-26%)"),
    (20190410, "002748", "世龙实业", "HOLD", "22.49万股,成本11.963,现价13.17"),
    (20190417, "600081", "东方科技", "SELL", "清仓+25.47万"),
    (20190417, "000958", "东方能源", "SELL", "清仓+20.27万"),
    (20190417, "002783", "凯龙股份", "HOLD", "18.19万股,成本15.751,现价19.03(+20.8%)"),
    (20190419, "601949", "中国出版", "HOLD", "43.63万股,成本7.69"),
    (20190419, "000038", "深大通", "HOLD", "20.49万股,成本15.821"),
]


def main():
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    print(f"{'锚点日':8s} {'代码':6s} {'名称':6s} {'动作':4s} {'pos60':6s} {'volr':5s} {'zt':3s} {'f5':7s} {'f10':7s} {'f20':7s}")
    agg = {"sell_f5": [], "sell_f20": [], "hold_f5": [], "hold_f20": [], "pos": []}
    for d, code, name, act, note in EV:
        hist = conn.execute(
            "SELECT date,close,volume,change_pct FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date", (code,)).fetchall()
        dates = [int(x[0]) for x in hist]
        if d not in dates:
            print(f"{d} {code} {name} {act}  无该日bar(min={dates[0] if dates else '-'})")
            continue
        i = dates.index(d)
        pos = volr = None
        if i >= 59:
            w = [x[1] for x in hist[i - 59:i]]
            lo, hi = min(w), max(w)
            pos = round((hist[i][1] - lo) / (hi - lo) * 100, 1) if hi > lo else None
        if i >= 5:
            v5 = sum(x[2] for x in hist[i - 5:i]) / 5
            volr = round(hist[i][2] / v5, 2) if v5 else None
        zt = bool(hist[i][3] is not None and hist[i][3] >= 9.5)
        f = {}
        for k in (5, 10, 20):
            if i + k < len(hist):
                f[k] = round((hist[i + k][1] / hist[i][1] - 1) * 100, 2)
        line = f"{d} {code} {name} {act} {str(pos):>6s} {str(volr):>5s} {'Y' if zt else 'N':>3s}"
        for k in (5, 10, 20):
            line += f" {f.get(k, 'NA'):>7}"
        print(line)
        key = "sell" if act == "SELL" else "hold"
        if 5 in f:
            agg[f"{key}_f5"].append(f[5])
        if 20 in f:
            agg[f"{key}_f20"].append(f[20])
        if pos is not None:
            agg["pos"].append(pos)
    conn.close()
    print("\n=== 聚合 ===")
    for k, v in agg.items():
        if v:
            s = sorted(v)
            win = sum(1 for x in v if x > 0) / len(v) * 100
            print(f"{k}: n={len(v)} win={win:.0f}% med={s[len(s)//2]:+.2f} mean={sum(v)/len(v):+.2f}")


if __name__ == "__main__":
    main()
