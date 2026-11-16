#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 卖出回头看三轴重评（2026-09-13）
去重政策未裁决前，按 async回报=canonical 显然选择去重，对 31 笔干净卖出算 fwd5+卖后最高+理由规模轴。
标 preliminary，去重政策确认后以 owner 裁决为准。"""
import sqlite3, re

ATR = r"D:\zcodesj\attribution\attribution.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TOTAL_ASSETS = 920260.0

ac = sqlite3.connect(ATR); ac.row_factory = sqlite3.Row
qc = sqlite3.connect(QFQ); qc.row_factory = sqlite3.Row

# 取9月SELL，按(code,price,vol)去重保留最早(async回报优先)
rows = ac.execute("select trade_date,code,name,price,volume,notes from trade_records where action='SELL' and trade_date>='20260901' order by trade_date").fetchall()
seen=set(); clean=[]
for r in rows:
    k=(r['code'], r['price'], r['volume'])
    if k in seen: continue
    seen.add(k); clean.append(r)

def qfq_close_after(code, sell_date, n=5):
    """返回 (sell_close, fwd_n_close, max_high_in_window) 或 None"""
    c = code[2:] if code[:2] in ('sh','sz') else code
    rows = qc.execute("select date,close,high from stock_daily_meta_qfq where code=? and date>=? order by date limit ?",(c, sell_date, n+1)).fetchall()
    if len(rows) < 1: return None
    p0 = rows[0]['close']
    p5 = rows[-1]['close'] if len(rows)>=2 else None
    hmax = max((r['high'] or r['close'] or 0) for r in rows)
    return (p0, p5, hmax)

def holding_period(code, sell_date):
    """持有交易日数=最近BUY日→sell日之间的交易日数(含sell不含buy)"""
    c = code[2:] if code[:2] in ('sh','sz') else code
    r = ac.execute("select max(trade_date) from trade_records where action='BUY' and code=? and trade_date<=?",(code, sell_date)).fetchone()
    bd = r[0] if r else None
    if not bd: return None, None
    cnt = qc.execute("select count(*) from (select distinct date from stock_daily_meta_qfq where code=? and date>? and date<=?)",(c, bd, sell_date)).fetchone()[0]
    return cnt, bd

def classify_reason(notes):
    n = notes or ''
    if 'S-5' in n or '亏损-8' in n: return 'S5止损'
    if 'R5a' in n or 'holding_period' in n: return 'R5a周期(已停)'
    if '趋势走坏' in n or '走坏' in n: return '趋势走坏'
    if '主人' in n or '清仓' in n or '换仓' in n or '全退' in n or '资金效率' in n: return '主人指令'
    if '调仓' in n: return '调仓'
    if '止损' in n: return '止损(非S5)'
    return '其他'

print(f"干净卖出 {len(clean)} 笔（已排除3对dup）")
print(f"{'日期':>8} {'代码':<8} {'名称':<8} {'价':>6} {'量':>6} {'额':>7} {'规模%':>5} {'持有':>4} {'理由':<10} {'P0':>6} {'P5':>6} {'Pmax':>6} {'fwd5%':>7} {'卖飞%':>6} {'判定':<6}")
buckets={'卖对':0,'卖飞':0,'中性':0,'不评':0}
for r in clean:
    code=r['code']; sd=r['trade_date']
    qa=qfq_close_after(code, sd, 5)
    hold, _ = holding_period(code, sd)
    hold_s = str(hold) if hold is not None else '?'
    if not qa or qa[1] is None:
        # 9/11 sells可能fwd不足
        print(f"{sd} {code} {r['name']:<8} {r['price']:>6} {r['volume']:>6} {'':>7} {'':>5} {hold_s:>4} {classify_reason(r['notes']):<10} {'N/A':>6} {'N/A':>6} {'N/A':>6} {'待9/18':>7} {'':>6} {'数据不足':<6}")
        buckets['不评']+=1; continue
    p0,p5,hmax=qa
    fwd5=(p5-p0)/p0*100 if p5 else None
    sellfly=(hmax-p0)/p0*100
    amt=r['price']*r['volume']; sz=amt/TOTAL_ASSETS*100
    reason=classify_reason(r['notes'])
    # 判定
    if reason in ('主人指令','调仓','R5a周期(已停)','其他'):
        verdict='不评'
    elif reason=='S5止损':
        verdict='卖飞' if (fwd5 or 0)>=3 else ('卖对' if (fwd5 or 0)<=-3 else '中性')
    elif reason=='趋势走坏':
        verdict='卖对' if (fwd5 or 0)<=0 else ('卖飞' if (fwd5 or 0)>=5 else '中性')
    elif reason=='止损(非S5)':
        verdict='卖对' if (fwd5 or 0)<0 else '中性'
    else:
        verdict='不评'
    buckets[verdict]+=1
    print(f"{sd} {code} {r['name']:<8} {r['price']:>6} {r['volume']:>6} {amt:>7.0f} {sz:>5.1f} {hold_s:>4} {reason:<10} {p0:>6.2f} {p5:>6.2f} {hmax:>6.2f} {fwd5:>7.2f} {sellfly:>6.2f} {verdict:<6}")

print("\n=== 判定分布 ===")
for k,v in buckets.items(): print(f"  {k}: {v}")
print(f"\n（preliminary：去重政策=async回报canonical，待owner裁决；9/11四只S5 fwd5待9/18数据）")
