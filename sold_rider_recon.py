# -*- coding: utf-8 -*-
"""sold_rider_recon.py — 卖飞样本底部侦察件 (2026-09-25 立项, 主人框定「卖飞=知道了什么是底,上涨有什么特征」).

定位: 侦察级只读观察 (影子件, 零交易权零拦截权, 禁入生产规则——回踩买入一般形态已被 R31 十三年
PIT 证伪, 本件种群=「自己卖出票的底部确认」属不同种群, 样本标签仅积累不接线)。

数据链: 上游=stop_reentry_plan.py 每交易日14:32产出 data/reentry_plan_*.json (卖单台账);
本件读最新台账 + qfq 库, 对每笔卖单计算:
  bottom_dist  = (卖价-卖后最低)/卖价          → 卖点离底的距离(主人洞察: 通常仅2-4%)
  verdict      = 卖飞(>1%) / 卖对(<-1%) / 平    → 按最新收盘动态翻转(海南橡胶9/28实测翻转)
  STABILIZED   = 卖后近3根缩量(量<卖日量50%)且收盘距卖价±4%且近2日未创新低  → 底部企稳标签
  IGNITED      = 当日量>前3日均量2倍且涨幅>3%且收盘>卖价                    → 启动标签(需2-3日确认)
记账: data/sold_rider_recon_ledger.jsonl, 键(day,code)幂等(重跑替换当日行)。
用法: python sold_rider_recon.py [--date YYYYMMDD]   # 默认最新台账
铁律: 只读计算+台账追加; 禁下单/禁改策略/禁把侦察标签接任何买卖通道; 代码必带名称。
"""
import os, sys, json, glob, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import pandas as pd

BASE = r'D:\zcodesj'
DATA = os.path.join(BASE, 'data')
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
LEDGER = os.path.join(DATA, 'sold_rider_recon_ledger.jsonl')
STAB_VOL_RATIO = 0.5   # 企稳: 近3根量 < 卖日量×0.5
STAB_PCT_BAND = 0.04   # 企稳: 收盘距卖价 ±4%
IGN_VOL_MULT = 2.0     # 启动: 当日量 > 前3日均量×2
IGN_CHG = 0.03         # 启动: 当日涨幅 > 3%


def load_ledger(path):
    if not os.path.exists(path):
        return []
    out = []
    for l in open(path, encoding='utf-8'):
        l = l.strip()
        if not l:
            continue
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='台账YYYYMMDD, 默认最新')
    a = ap.parse_args()
    pat = os.path.join(DATA, 'reentry_plan_*.json')
    files = sorted(glob.glob(pat))
    if a.date:
        files = [f for f in files if a.date in os.path.basename(f)] or files
    if not files:
        print('[recon] FAIL 无再入场台账(上游 stop_reentry_plan.py 14:32 产出)')
        return 2
    plan = json.load(open(files[-1], encoding='utf-8'))
    rows, seen = [], set()
    for x in plan.get('rows', []):
        code = str(x.get('code', '')).replace('sh', '').replace('sz', '').zfill(6)
        k = (code, str(x.get('date', '')))
        if k in seen or not code or not x.get('price'):
            continue
        seen.add(k)
        rows.append({'code': code, 'name': x.get('name', ''), 'sell_price': float(x['price']),
                     'sell_date': str(x.get('date', ''))})
    if not rows:
        print('[recon] 台账无有效卖单')
        return 0

    con = sqlite3.connect(QFQ_URI, uri=True, timeout=60)
    out_rows = []
    for r in rows:
        px = pd.read_sql_query(
            "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>? ORDER BY date", con, params=(r['code'], r['sell_date']))
        if px.empty:
            continue
        px['date'] = px['date'].astype(str)
        px['volume'] = pd.to_numeric(px['volume'], errors='coerce')
        px['close'] = pd.to_numeric(px['close'], errors='coerce')
        px = px.dropna(subset=['close'])
        if px.empty:
            continue
        sell = r['sell_price']
        low_after = float(px['low'].min())
        last = px.iloc[-1]
        cur = float(last['close'])
        pct_vs_sell = cur / sell - 1
        bottom_dist = (sell - low_after) / sell
        verdict = '卖飞' if pct_vs_sell > 0.01 else ('卖对' if pct_vs_sell < -0.01 else '平')
        # STABILIZED: 近3根缩量企稳
        stab = False
        if len(px) >= 3:
            last3 = px.tail(3)
            pre_vols = px['volume'].iloc[:-3]
            sell_day_vol = pre_vols.iloc[0] if len(pre_vols) else None
            ref = sell_day_vol if (sell_day_vol and sell_day_vol > 0) else px['volume'].head(3).mean()
            low2 = px['low'].iloc[-2:]
            stab = bool((last3['volume'] < ref * STAB_VOL_RATIO).all()
                        and abs(pct_vs_sell) <= STAB_PCT_BAND
                        and float(last2_min := low2.min()) >= low_after - 1e-9
                        and cur >= low_after)
        # IGNITED: 当日放量启动
        ign = False
        if len(px) >= 4:
            v_today = float(last['volume'])
            v_ref = float(px['volume'].iloc[-4:-1].mean())
            prev_close = float(px['close'].iloc[-2])
            day_chg = cur / prev_close - 1
            ign = bool(v_ref > 0 and v_today > v_ref * IGN_VOL_MULT
                       and day_chg > IGN_CHG and cur > sell)
        out_rows.append({'code': r['code'], 'name': r['name'], 'sell_date': r['sell_date'],
                         'sell_price': round(sell, 3), 'last_date': str(last['date']),
                         'last_close': round(cur, 3), 'pct_vs_sell': round(pct_vs_sell * 100, 2),
                         'low_after': round(low_after, 3),
                         'bottom_dist_pct': round(bottom_dist * 100, 2),
                         'verdict': verdict, 'STABILIZED': stab, 'IGNITED': ign})
    con.close()

    # 幂等记账: 替换当日同码行
    today = max(r['last_date'] for r in out_rows) if out_rows else ''
    led = load_ledger(LEDGER)
    led = [l for l in led if not (l.get('day') == today and any(
        l.get('code') == o['code'] and l.get('sell_date') == o['sell_date'] for o in out_rows))]
    for o in out_rows:
        o['day'] = today
        o['kind'] = 'sold_rider_recon'
    led.extend(out_rows)
    with open(LEDGER, 'w', encoding='utf-8') as f:
        for l in led:
            f.write(json.dumps(l, ensure_ascii=False) + '\n')

    fly = [o for o in out_rows if o['verdict'] == '卖飞']
    right = [o for o in out_rows if o['verdict'] == '卖对']
    stab = [o for o in out_rows if o['STABILIZED']]
    ign = [o for o in out_rows if o['IGNITED']]
    bds = sorted(o['bottom_dist_pct'] for o in out_rows)
    med = bds[len(bds)//2] if bds else None
    print(f'[recon] 样本{len(out_rows)}笔(卖单台账{os.path.basename(files[-1])}) | '
          f'卖飞{len(fly)}/卖对{len(right)}/平{len(out_rows)-len(fly)-len(right)} | '
          f'中位底距{med:.1f}% | 企稳标签{len(stab)} 启动标签{len(ign)}')
    print('=== 卖飞组(距底/最新/状态) ===')
    for o in sorted(fly, key=lambda x: -x['pct_vs_sell']):
        flags = ('[企稳]' if o['STABILIZED'] else '') + ('[启动]' if o['IGNITED'] else '')
        print(f"  {o['name']}({o['code']}) 卖@{o['sell_price']} {o['sell_date'][4:]} → "
              f"{o['last_close']} {o['pct_vs_sell']:+.1f}% 底距{o['bottom_dist_pct']:.1f}% {flags}")
    print('=== 企稳标签组(含卖对侧, 侦察观察) ===')
    for o in stab:
        print(f"  {o['name']}({o['code']}) 卖@{o['sell_price']} → {o['last_close']} "
              f"{o['pct_vs_sell']:+.1f}% (判读:{o['verdict']})")
    if ign:
        print('=== 启动标签组 ===')
        for o in ign:
            print(f"  {o['name']}({o['code']}) {o['last_close']} {o['pct_vs_sell']:+.1f}%")
    print(f'记账 {LEDGER} (day={today}, 幂等)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
