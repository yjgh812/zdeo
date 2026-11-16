# -*- coding: utf-8 -*-
"""复验脚本 2026-09-23: 上升趋势确立+位置未过热+胜率最大+涨幅最大 四维打分"""
import json, sqlite3, sys
sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
# 6强多头 + 3贴线备选 + 1主线扫描票(森特,已判偏弱留对照)
CANDS = ["sz300755","sh600284","sz002995","sz300667","sz300538","sh600966",
         "sz300887","sh688586","sh603918","sh603098"]
# 实时价/今日涨幅(13:20 tencent快照, 不复权实际价)
RT = {"sz300755":(11.74,1.21),"sh600284":(5.13,-0.97),"sz002995":(17.47,-2.29),
      "sz300667":(15.35,1.66),"sz300538":(15.50,1.71),"sh600966":(4.97,None),
      "sz300887":(7.04,None),"sh688586":(11.28,None),"sh603918":(10.99,None),"sh603098":(10.78,None)}

def f(x):
    try: return float(x)
    except Exception: return None

def main():
    db = sqlite3.connect(QFQ_DB, uri=True)
    cur = db.cursor()
    out = []
    for full in CANDS:
        code = full[2:]
        r = cur.execute("SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780",(code,)).fetchall()
        if len(r) < 400: continue
        closes=[f(x[0]) for x in r]; vols=[f(x[1]) or 0 for x in r]
        if any(v is None for v in closes[:300]): continue
        c=closes[0]
        ma=lambda n: sum(closes[:n])/n
        ma5,ma10,ma20,ma60 = ma(5),ma(10),ma(20),ma(60)
        full_arr = ma5>ma10>ma20>ma60
        # ADX14 近似(trend_checker同构): 用简化DI
        trs=[];pds=[];mds=[]
        for i in range(14):
            hi=None
            tr=abs(closes[i]-closes[i+1]); trs.append(tr)
            up=closes[i]-closes[i+1]; dn=closes[i+1]-closes[i]
            pds.append(max(up,0)); mds.append(max(dn,0))
        atr=sum(trs)/14 or 1e-9
        pdi=100*sum(pds)/atr/14; mdi=100*sum(mds)/atr/14
        dx=100*abs(pdi-mdi)/max(pdi+mdi,1e-9)
        # 动量/确立度
        lo250=min(closes[:250]); off_lo250=(c/lo250-1)*100
        ret20=(c/closes[20]-1)*100; ret60=(c/closes[60]-1)*100
        bias6=(c/ma(6)-1)*100
        hi250=max(closes[:250]); off_hi250=(c/hi250-1)*100
        yr3=closes[:750]; lo3=min(yr3); pos3=100*(c-lo3)/(max(yr3)-lo3)*1.0
        pos250=100*(c-lo250)/(hi250-lo3 if False else hi250-lo250)
        # 量价配合20日
        up_v=dn_v=0.0;up_n=dn_n=0
        for i in range(20):
            ch=closes[i]-closes[i+1]
            if ch>0: up_v+=vols[i];up_n+=1
            elif ch<0: dn_v+=vols[i];dn_n+=1
        vp=(up_v/up_n)/(dn_v/dn_n) if up_n and dn_n and dn_v else 1.0
        # 20日日均量 vs 250日均量(能量状态)
        v20=sum(vols[:20])/20; v250=sum(vols[:250])/250
        vr=v20/v250
        px_rt,chg_rt = RT.get(full,(None,None))
        out.append(dict(code=code,name=get_name(code) or '',full=full,c=c,px_rt=px_rt,chg_rt=chg_rt,
            full_arr=bool(full_arr),ma20=round(ma20,2),ma60=round(ma60,2),
            pdi=round(pdi,1),mdi=round(mdi,1),dx=round(dx,1),
            off_lo250=round(off_lo250,1),off_hi250=round(off_hi250,1),
            pos250=round(pos250),pos3=round(pos3),
            ret20=round(ret20,1),ret60=round(ret60,1),bias6=round(bias6,2),
            vp=round(vp,2),vr=round(vr,2)))
    # 打分: 趋势确立(full_arr 3分+确认窗口5~25%内3分/完全确立26~34% 2分+dx>=25 1分)
    for o in out:
        s=0
        if o['full_arr']: s+=3
        if 5<=o['off_lo250']<=25: s+=3
        elif 25<o['off_lo250']<=34: s+=2
        if o['dx']>=25 and o['pdi']>o['mdi']: s+=1
        # 未过热(PD-020裁决2026-09-25: 口径对齐G4闸=20日涨幅透支型, 旧ret60/bias6/今日复合口径退役;
        # 依据=56月条件测试剔B增量+0.11pp组内最优且误杀仅13%, A口径误杀58%增量近零; 阈值40%与G4闸同源)
        if o['ret20'] is None or o['ret20'] <= 40: s += 3
        elif o['ret20'] <= 50: s += 1
        # 胜率(vp>=1.3 +2 / >=1.15 +1) + 空间(pos3<=25 +2 / <=40 +1)
        if o['vp']>=1.3: s+=2
        elif o['vp']>=1.15: s+=1
        if o['pos3']<=25: s+=2
        elif o['pos3']<=40: s+=1
        o['score']=s
    out.sort(key=lambda x:-x['score'])
    hdr=f"{'代码':<8}{'名称':<6}{'现价':>6}{'今日%':>6}{'全排列':>4}{'PDI':>5}{'DX':>5}{'距1年底%':>8}{'距1年高%':>8}{'pos250':>7}{'pos3':>5}{'ret20':>6}{'ret60':>6}{'BIAS6':>6}{'vp':>5}{'量能':>5}{'分':>3}"
    print(hdr)
    for o in out:
        print(f"{o['code']:<8}{o['name']:<6}{o['px_rt'] or o['c']:>6}{o['chg_rt'] if o['chg_rt'] is not None else '':>6}{('Y' if o['full_arr'] else 'N'):>4}{o['pdi']:>5}{o['dx']:>5}{o['off_lo250']:>8}{o['off_hi250']:>8}{o['pos250']:>7}{o['pos3']:>5}{o['ret20']:>6}{o['ret60']:>6}{o['bias6']:>6}{o['vp']:>5}{o['vr']:>5}{o['score']:>3}")
    json.dump(out,open(r"D:\zcodesj\reports\universe_recheck_20260923.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)

if __name__=='__main__':
    main()
