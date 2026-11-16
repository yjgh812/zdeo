# -*- coding: utf-8 -*-
"""
退出监控器 v1.1（持久版, 2026-10-03）— 屠龙顶底工具链·卖侧
持仓29只每日扫描: 爆量退出(vr120>=1.5∧ret20>=20) / 顶部成立(反抽不破) / TB_TOP / 高位量能
市场层: 低区占比 / 高位共振 / ERP分位
输出: data/exit_watch_latest.json
语义: 区域预警非择时; -8%止损与爆量退出为执行触发, 其余为观察分层
"""
import json, sqlite3
import numpy as np
import pandas as pd

DB = r"file:D:/zcodesj/data/dragon_quant_qfq.db?mode=ro"
OUT = r"D:/zcodesj/data/exit_watch_latest.json"
HOLDINGS = [("003006","百亚股份"),("600719","大连热电"),("601919","中远海控"),("300817","双飞集团"),
    ("002688","金河生物"),("600694","大商股份"),("600035","楚天高速"),("600018","上港集团"),
    ("002174","游族网络"),("600827","百联股份"),("301370","国科恒泰"),("002777","久远银海"),
    ("600626","申达股份"),("001390","古麒绒材"),("603209","兴通股份"),("601022","宁波远洋"),
    ("601866","中远海发"),("002966","苏州银行"),("603637","镇海股份"),("601226","华电科工"),
    ("300977","深圳瑞捷"),("300667","必创科技"),("600966","博汇纸业"),("300755","华致酒行"),
    ("002995","天地在线"),("600284","浦东建设"),("603098","森特股份"),("300612","宣亚国际"),
    ("000712","锦龙股份")]


def market_layer(con, last):
    low = hi = tot = 0
    recent2 = con.execute("select distinct date from stock_daily_meta_qfq order by date desc limit 2").fetchone()[0]
    for (code,) in con.execute("select distinct code from stock_daily_meta_qfq where (code like '60%' or code like '00%' or code like '30%')"):
        rows = con.execute("select date,close from stock_daily_meta_qfq where code=? and date<=? order by date desc limit 250",
                           (code, last)).fetchall()
        if len(rows) < 250 or rows[0][0] < recent2: continue
        c = np.array([r[1] for r in rows], float)
        lo_, hi_ = c.min(), c.max()
        if hi_ > lo_:
            p = (c[0] - lo_) / (hi_ - lo_) * 100
            tot += 1
            if p < 40: low += 1
            if p >= 90: hi += 1
    try:
        erp_pct = sqlite3.connect(r"file:D:/zcodesj/erp_history.db?mode=ro", uri=True).execute(
            "select erp_pct_5y from erp_daily order by date desc limit 1").fetchone()[0]
    except Exception:
        erp_pct = None
    return {"date": last, "低区%": round(low/tot*100, 1), "高位%": round(hi/tot*100, 1),
            "ERP分位%": round(erp_pct, 1) if erp_pct else None,
            "高位共振": "是" if hi/tot >= 0.20 else "否"}


def main():
    con = sqlite3.connect(DB, uri=True)
    last = con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0]
    mkt = market_layer(con, last)
    rows_out = []
    for code, name in HOLDINGS:
        rws = con.execute("select date,close,volume from stock_daily_meta_qfq where code=? order by date",
                          (code,)).fetchall()
        c = np.array([r[1] for r in rws], float)
        v = np.array([r[2] for r in rws], float)
        cs, vs = pd.Series(c), pd.Series(v)
        v120 = vs.iloc[-120:].mean()
        vr120 = v[-1] / vs.iloc[-121:-1].mean()
        pre5_vr = vs.iloc[-5:].mean() / v120
        ret20 = (c[-1]/c[-21]-1) * 100
        lo250, hi250 = cs.iloc[-250:].min(), cs.iloc[-250:].max()
        pos = (c[-1]-lo250)/(hi250-lo250)*100
        top_conf, reb_pct = False, None
        w = c[-60:]
        pk = int(np.argmax(w))
        if pk < len(w)-5 and w[pk]/w[0] > 1.02:
            after = w[pk:]
            dn = next((k for k in range(1, len(after)) if after[k]/after[0]-1 <= -0.10), None)
            if dn is not None:
                trough = int(np.argmin(after[dn:])) + dn
                reb = after[trough:].max()
                reb_pct = round(reb/after[0]*100, 1)
                top_conf = reb < after[0]*0.95
        vol_exit = vr120 >= 1.5 and ret20 >= 20
        if vol_exit: verdict = "🔴卖出呈请(爆量退出)"
        elif top_conf and reb_pct is not None and reb_pct < 90: verdict = "🔴强警觉(顶部成立,反抽<90%)"
        elif top_conf: verdict = "🟡警觉(顶部成立)"
        elif pos >= 90: verdict = "🟠高位观察"
        elif vr120 >= 1.5 and pre5_vr >= 1.5: verdict = "🟡量能异动(区域高量观察)"
        else: verdict = "持有"
        rows_out.append({"name": name, "code": code, "判定": verdict,
                         "vr120": round(vr120, 2), "ret20": round(ret20, 1),
                         "pre5_vr": round(pre5_vr, 2), "pos250": round(pos, 0), "反抽收复%": reb_pct})
    con.close()
    order = {"🔴卖出呈请(爆量退出)":0, "🔴强警觉(顶部成立,反抽<90%)":1, "🟡警觉(顶部成立)":2,
             "🟠高位观察":3, "🟡量能异动(区域高量观察)":4, "持有":5}
    rows_out.sort(key=lambda r: order.get(r["判定"], 9))
    print("=== 市场层(退出视角) ===")
    print(json.dumps(mkt, ensure_ascii=False))
    print(f"=== 持仓{len(rows_out)}只退出扫描({last}) ===")
    for r in rows_out:
        print(f"{r['判定']:<22}{r['name']:<6}{r['code']} vr120={r['vr120']:<5} ret20={r['ret20']:+.1f}% "
              f"pre5={r['pre5_vr']:<5} pos={r['pos250']:.0f}% 反抽={r['反抽收复%']}")
    from collections import Counter
    print("\n判定分布:", dict(Counter(r["判定"] for r in rows_out)))
    json.dump({"mkt": mkt, "rows": rows_out}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
