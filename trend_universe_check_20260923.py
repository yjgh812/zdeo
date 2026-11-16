# -*- coding: utf-8 -*-
"""对照验证: 放开'低位'约束, 全市场'上升趋势确立'票有多少/最强是谁 2026-09-23
条件: 站上全部均线 + MA全排列 + 趋势态(DX>=25,+DI>-DI) + 排雷(当日未>=3%看实时, 此处用昨日口径)
输出: 达标总数 + 按距一年高(剩余空间)排序Top15 + 持有票位置
"""
import json, sqlite3, sys
sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
RAW_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
HELD = {'300667':'必创科技','600966':'博汇纸业','300755':'华致酒行','002995':'天地在线','600284':'浦东建设'}

def f(x):
    try: return float(x)
    except Exception: return None

def main():
    raw = sqlite3.connect(RAW_DB, uri=True)
    universe = [c for (c,) in raw.execute("SELECT code FROM stock_info WHERE instrument_type='stock'").fetchall()]
    raw.close()
    db = sqlite3.connect(QFQ_DB, uri=True)
    cur = db.cursor()
    ok = []
    n = 0
    for code in universe:
        n += 1
        nm = get_name(code) or ''
        if 'ST' in nm.upper() or '退' in nm: continue
        d = cur.execute("SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780",(code,)).fetchall()
        if len(d) < 400: continue
        closes=[f(x[0]) for x in d]; vols=[f(x[1]) or 0 for x in d]
        if any(v is None for v in closes[:300]): continue
        c=closes[0]
        if not c or c > 60 or c < 2: continue  # 放宽价格到60元(趋势票可能高价)
        ma=lambda k: sum(closes[:k])/k
        ma5,ma10,ma20,ma60 = ma(5),ma(10),ma(20),ma(60)
        if not (c>ma5 and c>ma10 and c>ma20 and c>ma60): continue
        if not (ma5>ma10>ma20>ma60): continue
        trs=[];pds=[];mds=[]
        for i in range(14):
            trs.append(abs(closes[i]-closes[i+1]))
            up=closes[i]-closes[i+1]; dn=closes[i+1]-closes[i]
            pds.append(max(up,0)); mds.append(max(dn,0))
        atr=sum(trs)/14 or 1e-9
        pdi=100*sum(pds)/atr/14; mdi=100*sum(mds)/atr/14
        dx=100*abs(pdi-mdi)/max(pdi+mdi,1e-9)
        if dx < 25 or pdi <= mdi: continue
        hi250=max(closes[:250]); lo250=min(closes[:250])
        pos250=100*(c-lo250)/(hi250-lo250) if hi250>lo250 else 50
        off_hi=(c/hi250-1)*100
        ret60=(c/closes[60]-1)*100
        up_v=dn_v=0.0;up_n=dn_n=0
        for i in range(20):
            ch=closes[i]-closes[i+1]
            if ch>0: up_v+=vols[i];up_n+=1
            elif ch<0: dn_v+=vols[i];dn_n+=1
        vp=(up_v/up_n)/(dn_v/dn_n) if up_n and dn_n and dn_v else 1.0
        ok.append(dict(code=code,name=nm,pos250=round(pos250),off_hi=round(off_hi,1),
                       ret60=round(ret60,1),dx=round(dx,1),vp=round(vp,2)))
    print(f"全市场扫描 {n} 只 | '上升趋势确立'(站全均线+全排列+趋势态DX>=25多头) 达标 = {len(ok)} 只")
    ok.sort(key=lambda x: -x['off_hi'])
    print("\n-- 距一年高点最近(剩余空间最小=已涨最多, 追高禁追区) Top8 --")
    for o in ok[:8]:
        m='◆持仓' if o['code'] in HELD else ''
        print(f"{o['code']} {o['name']:<6} pos250={o['pos250']}% 距高{o['off_hi']:+.1f}% ret60={o['ret60']:+.1f}% DX={o['dx']} vp={o['vp']} {m}")
    print("\n-- 距一年高点最远(剩余空间最大, 且趋势已确立) Top12 --")
    for o in sorted(ok, key=lambda x: x['off_hi'])[:12]:
        m='◆持仓' if o['code'] in HELD else ''
        print(f"{o['code']} {o['name']:<6} pos250={o['pos250']}% 距高{o['off_hi']:+.1f}% ret60={o['ret60']:+.1f}% DX={o['dx']} vp={o['vp']} {m}")
    held_in=[o for o in ok if o['code'] in HELD]
    print(f"\n持有5只在'趋势确立'对照池中: {[o['code'] for o in held_in]}")
    json.dump(ok,open(r"D:\zcodesj\reports\trend_universe_20260923.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)

if __name__=='__main__':
    main()
