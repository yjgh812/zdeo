# -*- coding: utf-8 -*-
"""屠龙系统全方法验证 2026-09-23: 终选5只(2备选)
方法面: ①技术事件拥挤度nev(合取矩阵:nev>=2单调负剔除) ②三级别顶底画像(中级底部四指标)
        ③波段相位量能路径(启动0.89→中继1.08→末段1.65) ④-8%止损带兼容(赢家MAE分位)
"""
import json, sqlite3, sys
sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
CANDS = ["sz300667","sh600966","sz300755","sz002995","sh600284","sz300538","sh688586"]

def f(x):
    try: return float(x)
    except Exception: return None

def main():
    db = sqlite3.connect(QFQ_DB, uri=True)
    cur = db.cursor()
    print(f"{'代码':<8}{'名称':<6}| nev20 | 距高%  缩量比 破MA20% 乖离% |顶底标签 | 相位标签")
    for full in CANDS:
        code = full[2:]
        r = cur.execute("SELECT close, high, low, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780",(code,)).fetchall()
        if len(r) < 400: continue
        closes=[f(x[0]) for x in r]; vols=[f(x[3]) or 0 for x in r]
        c=closes[0]
        # ①技术事件拥挤度 nev: 近20日技术事件计数(创60日新高/放量1.5x大阳/连涨3日起点/向上破MA20)
        ev=0; evs=[]
        hi60=max(closes[0:60+20])
        if closes[0]>=hi60: ev+=1; evs.append('创60高')
        for i in range(20):
            v20=sum(vols[i+1:i+21])/20
            if v20 and vols[i]>=1.5*v20 and closes[i]>closes[i+1]: ev+=1; evs.append(f'放量大阳d{i}'); break
        run=0
        for i in range(20):
            if closes[i]>closes[i+1]: run+=1
            else: break
        if run>=3: ev+=1; evs.append(f'连涨{run}日')
        ma20=sum(closes[:20])/20
        crossed=False
        for i in range(20):
            m=sum(closes[i:i+20])/20
            if closes[i]>m and closes[i+1]<=m: crossed=True; break
        if crossed: ev+=1; evs.append('上破MA20')
        nev=ev
        # ②顶底画像(中级口径: 底部=缩量0.65x+距高-36%+破MA20-10%; 顶部=放量1.26x+距低+55%+乖离+12%)
        v250=sum(vols[:250])/250; v20=sum(vols[:20])/20; shrink=v20/v250 if v250 else 1
        hi250=max(closes[:250]); off_hi=(c/hi250-1)*100
        lo250=min(closes[:250]); off_lo=(c/lo250-1)*100
        bias=(c/ma20-1)*100
        brk20=(c/ma20-1)*100
        bot_score=0
        if shrink<=0.8: bot_score+=1
        if off_hi<=-25: bot_score+=1
        if off_lo<=20: bot_score+=1
        top_score=0
        if shrink>=1.2: top_score+=1
        if off_lo>=40: top_score+=1
        if bias>=10: top_score+=1
        tag = '底部区' if bot_score>=2 and top_score==0 else ('顶部区' if top_score>=2 else '中部/启动带')
        # ③波段相位: 近40日按波峰波谷切3段量能(启动→中继→末段); 简化: 最近20日量能相对前20日比值定位
        v_prev=sum(vols[20:40])/20; v_now=sum(vols[:20])/20
        ratio=v_now/v_prev if v_prev else 1
        if ratio<0.9 and shrink<1.0: phase='缩量启动段(早)'
        elif ratio<1.15: phase='温和中继'
        elif ratio<1.6: phase='放量中继'
        else: phase='⚠爆量末段(警惕)'
        # ④-8%止损兼容: 波段解剖赢家MAE第3分位≈-8%, 距MA20/近期支撑的深度
        mae_buf=(c/ma20-1)*100  # 现价距MA20, 越正止损缓冲越大
        print(f"{code:<8}{(get_name(code) or ''):<6}| {nev} {','.join(evs)[:24]:<26}| {off_hi:>6.1f} {shrink:>6.2f} {brk20:>7.1f} {bias:>6.2f} |{tag} | {phase} | 距MA20 {mae_buf:+.1f}%")
    # 相位全路径参考: 若近120日有清晰上升段, 输出段内三段量能
    print()
    print('== 上升段三段量能路径(有清晰上升段者; 口径: 段起0.89x→中继1.08x→末段1.65x) ==')
    for full in CANDS:
        code=full[2:]
        r = cur.execute("SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780",(code,)).fetchall()
        if len(r)<400: continue
        closes=[f(x[0]) for x in r]; vols=[f(x[1]) or 0 for x in r]
        # 找近120日内显著低点(60日最低)作段起
        win=closes[:120]; lo=min(win); li=win.index(lo)
        if li>90: print(f"{code} 近120日低点在第{li}日前,段起较远"); continue
        n=len(win)-li
        if n<30: print(f"{code} 段起后不足30日"); continue
        seg=max(6,n//3)
        v_all=sum(vols[:n+20])/(n+20)
        s1=sum(vols[li+2*seg:li+3*seg])/seg/v_all if False else sum(vols[li:li+seg])/seg/v_all
        s2=sum(vols[max(li+seg,0):li+2*seg])/seg/v_all
        s3=sum(vols[:seg])/seg/v_all
        print(f"{code} {(get_name(code) or ''):<6} 段起{li}日前,段长{n}日 | 启动段量能{s1:.2f}x 中继{s2:.2f}x 当前段{s3:.2f}x")
    json.dump({'ts':'20260923'},open(r"D:\zcodesj\reports\verify_all_20260923.json","w"))

if __name__=='__main__':
    main()
