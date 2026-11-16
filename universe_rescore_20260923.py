# -*- coding: utf-8 -*-
"""95只命中池全量重打分 2026-09-23: 补验未验的79只, 回答'有没有更好的'
判定口径与trend_checker对齐: 站上全部均线(c>MA5,MA10,MA20,MA60) + 趋势态(ADX>25且+DI>-DI)
四维分: 趋势确立(全排列+确认窗口+DX) + 未过热(ret20透支型,PD-020对齐G4) + 胜率(vp) + 空间(pos3)
"""
import json, sqlite3, sys
sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
scan = json.load(open(r"D:\zcodesj\reports\universe_startup_scan_20260923.json", encoding="utf-8"))
rows = scan['hits']

def f(x):
    try: return float(x)
    except Exception: return None

def main():
    db = sqlite3.connect(QFQ_DB, uri=True)
    cur = db.cursor()
    out = []
    for r in rows:
        code = r['code']
        d = cur.execute("SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780",(code,)).fetchall()
        if len(d) < 400: continue
        closes=[f(x[0]) for x in d]; vols=[f(x[1]) or 0 for x in d]
        if any(v is None for v in closes[:300]): continue
        c=closes[0]
        ma=lambda n: sum(closes[:n])/n
        ma5,ma10,ma20,ma60 = ma(5),ma(10),ma(20),ma(60)
        above_all = c>ma5 and c>ma10 and c>ma20 and c>ma60
        full_arr = ma5>ma10>ma20>ma60
        # ADX14简化
        trs=[];pds=[];mds=[]
        for i in range(14):
            trs.append(abs(closes[i]-closes[i+1]))
            up=closes[i]-closes[i+1]; dn=closes[i+1]-closes[i]
            pds.append(max(up,0)); mds.append(max(dn,0))
        atr=sum(trs)/14 or 1e-9
        pdi=100*sum(pds)/atr/14; mdi=100*sum(mds)/atr/14
        dx=100*abs(pdi-mdi)/max(pdi+mdi,1e-9)
        trend_ok = above_all and dx>=20 and pdi>mdi  # trend_checker核心: 站上全部均线+趋势态
        lo250=min(closes[:250]); off_lo250=(c/lo250-1)*100
        hi250=max(closes[:250])
        pos250=100*(c-lo250)/(hi250-lo250) if hi250>lo250 else 50
        yr3=closes[:750]; lo3=min(yr3); pos3=100*(c-lo3)/(max(yr3)-lo3) if max(yr3)>lo3 else 50
        ret20=(c/closes[20]-1)*100; ret60=(c/closes[60]-1)*100
        bias6=(c/ma(6)-1)*100
        up_v=dn_v=0.0;up_n=dn_n=0
        for i in range(20):
            ch=closes[i]-closes[i+1]
            if ch>0: up_v+=vols[i];up_n+=1
            elif ch<0: dn_v+=vols[i];dn_n+=1
        vp=(up_v/up_n)/(dn_v/dn_n) if up_n and dn_n and dn_v else 1.0
        # 四维分
        s=0
        if full_arr: s+=3
        if above_all: s+=1
        if 5<=off_lo250<=25: s+=3
        elif 25<off_lo250<=34: s+=2
        if dx>=25 and pdi>mdi: s+=1
        # 未过热(PD-020裁决2026-09-25: 对齐G4闸=20日涨幅透支型, 旧ret60/bias6复合口径退役, 阈值40%同源)
        if ret20 is None or ret20 <= 40: s += 3
        elif ret20 <= 50: s += 1
        if vp>=1.3: s+=2
        elif vp>=1.15: s+=1
        if pos3<=25: s+=2
        elif pos3<=40: s+=1
        out.append(dict(code=code,name=get_name(code) or '',ind=r['ind'],px=r['px'],
            trend=trend_ok,above=above_all,full=full_arr,dx=round(dx,1),
            off_lo=round(off_lo250,1),pos250=round(pos250),pos3=round(pos3),
            ret20=round(ret20,1),ret60=round(ret60,1),bias6=round(bias6,2),vp=round(vp,2),score=s))
    out.sort(key=lambda x:-x['score'])
    held={'300667','600966','300755','002995','600284'}
    print(f"{'代码':<8}{'名称':<6}{'行业':<8}{'趋势✓':>4}{'全排':>4}{'DX':>5}{'距底%':>6}{'pos250':>7}{'pos3':>5}{'ret20':>6}{'ret60':>6}{'vp':>5}{'分':>3} 持有")
    for o in out[:35]:
        mark='◆' if o['code'] in held else ''
        print(f"{o['code']:<8}{o['name']:<6}{o['ind']:<8}{('Y' if o['trend'] else 'N'):>4}{('Y' if o['full'] else 'N'):>4}{o['dx']:>5}{o['off_lo']:>6}{o['pos250']:>7}{o['pos3']:>5}{o['ret20']:>6}{o['ret60']:>6}{o['vp']:>5}{o['score']:>3} {mark}")
    n_trend=sum(1 for o in out if o['trend'])
    print(f"\n全池趋势达标(trend_checker核心口径)={n_trend}/95; 已持有5只={sorted(held)}")
    json.dump(out,open(r"D:\zcodesj\reports\universe_rescore_20260923.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)

if __name__=='__main__':
    main()
