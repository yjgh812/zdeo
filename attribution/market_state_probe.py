# -*- coding: utf-8 -*-
"""market_state_probe.py — 市场状态探针+vipdoc新鲜度检查（每日 20:00，regime 意识落地件）。
vipdoc检查(9/17主人确认扩每日): 交易日 20:00 vipdoc 缺当日线(双源交叉: baostock 已有当日而 tdx 无=确凿未下载)→toast 提醒开通达信——封堵 9/11(周五)/9/18(周四) 两晚时序错位盲区。
状态判定（全部收盘后事前可知）：
  W1: 市场等权 pos250 中位（全A剔ST，qfq）
  W2: 20日换手能量比（近5日全A换手中位 / 前20日换手中位）
  LOW_VOL = W1<0.30 且 W2<0.9 → 「低位缩量负期望窗口」：
      历史依据=9/16-9/17三重归因（selection_deep_analysis/月度归因/SELECTION-CHECKLIST独立段）：
      该状态下所有选股方法同失效（手动链fwd5 -2.19pp/白名单PASS搁置组-1.0pp/检查单3中组-0.36pp），
      建议降低新仓频率。
告警策略：状态切换日 toast 一次（防每日噪音），每日均落盘 json。
退出码：0=正常；2=数据不可用 fail-closed。"""
import os, sys, json, time, sqlite3, datetime, subprocess
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FD = r'D:/workbuddyspace/databases/fundamental_data.db'
DQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
NOTIFY_PY = r'D:/zcodesj/notify.py'
STATE_FILE = r'D:/zcodesj/data/market_state_probe.json'
OUT = r'D:/zcodesj/reports/market_state_latest.json'

def notify(title, content, level='warning'):
    try:
        subprocess.run([sys.executable, NOTIFY_PY, 'send', '--title', title,
                        '--content', content, '--level', level], timeout=60)
    except Exception as e:
        print(f'[probe] notify 失败: {e}')


VIPDOC_DAY = r'D:/anzhuang/tdx/vipdoc/sh/lday/sh000001.day'
def vipdoc_max_date():
    import struct
    dates = []
    with open(VIPDOC_DAY, 'rb') as f:
        data = f.read()
    rec = struct.Struct('<IIIIIfII')
    for i in range(0, len(data)-31, 32):
        d = rec.unpack_from(data, i)[0]
        if d > 20200101:
            dates.append(d)
    return max(dates) if dates else 0

def baostock_max_date():
    con = sqlite3.connect(r'D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db', timeout=10)
    r = con.execute('SELECT MAX(date) FROM stock_daily_meta').fetchone()[0]
    con.close()
    return int(str(r).replace('-','')[:8])

def main():
    t0 = time.time()
    today = datetime.date.today().strftime('%Y%m%d')
    con0 = sqlite3.connect(DQ, timeout=10)
    codes = [r[0] for r in con0.execute(
        "SELECT substr(code,1,6) FROM stock_info WHERE is_st='0' AND instrument_type LIKE '%stock%' "
        "AND substr(code,1,3) IN ('600','601','603','605','000','001','002','003','300') GROUP BY substr(code,1,6)")]
    con0.close()
    con = sqlite3.connect(QFQ, timeout=120)
    mx = con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0]
    ph = ','.join('?'*len(codes))
    df = pd.read_sql(f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
                     f"WHERE date>=? AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=[f'{int(mx[:4])-1}0401'] + codes)
    con.close()
    df['date'] = df['date'].astype(str).str.replace('-','')
    df['close'] = df['close'].astype(float)
    print(f'asof={mx} rows={len(df)} {time.time()-t0:.0f}s', flush=True)

    pos_list = []
    for c, g in df.groupby('code6', sort=False):
        if len(g) < 250: continue
        s = g['close'].reset_index(drop=True)
        tail = s.iloc[-250:]
        mn, mxv = tail.min(), tail.max()
        if mxv > mn:
            pos_list.append((s.iloc[-1]-mn)/(mxv-mn))
    pos_med = float(np.median(pos_list)) if pos_list else None

    # 换手能量比：近5日 vs 前20日 全A换手中位
    con = sqlite3.connect(FD, timeout=60)
    db = pd.read_sql("SELECT trade_date, turnover_rate FROM daily_basic WHERE trade_date>=? ORDER BY trade_date",
                     con, params=(f'{int(mx[:4])-1}0401',))
    con.close()
    med = db.groupby('trade_date')['turnover_rate'].median()
    med = med[med.index <= str(mx)]
    w2 = 0.9
    if len(med) >= 25:
        recent = med.iloc[-5:].median(); prev = med.iloc[-25:-5].median()
        w2 = round(float(recent/prev), 3) if prev > 0 else None
    low_vol = (pos_med is not None and pos_med < 0.30 and w2 is not None and w2 < 0.9)
    state = 'LOW_VOL' if low_vol else 'NORMAL'
    print(f'pos250_med={pos_med:.3f} turn_energy={w2} state={state}', flush=True)

    # vipdoc 新鲜度检查(每日, 双源交叉防节假日误报; 9/18主人确认扩每日)
    vipdoc_max = vipdoc_max_date()
    bs_max = baostock_max_date()
    vipdoc_ok = vipdoc_max >= int(today)
    print(f'[vipdoc] today={today} vipdoc_max={vipdoc_max} baostock_max={bs_max}', flush=True)
    if not vipdoc_ok and bs_max >= int(today):
        vmsg = (f'通达信 vipdoc 缺 {today} 日线(vipdoc_max={vipdoc_max}, baostock 已有 {bs_max})——'
                f'今晚 23:30 链将数据降级, 明早白名单源缺失——请立即打开通达信补盘后数据')
        print(f'[probe] VIPDOC ALERT: {vmsg}')
        notify('vipdoc 缺当日线提醒', vmsg, 'error')

    prev_state = None
    if os.path.exists(STATE_FILE):
        try: prev_state = json.load(open(STATE_FILE, encoding='utf-8')).get('state')
        except Exception: pass
    result = {'date': today, 'asof': mx, 'pos250_med': round(pos_med, 3) if pos_med else None,
              'turn_energy_5v20': w2, 'state': state, 'prev_state': prev_state,
              'generated': datetime.datetime.now().strftime('%F %H:%M')}
    json.dump(result, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump(result, open(STATE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if state == 'LOW_VOL' and prev_state != 'LOW_VOL':
        notify('市场状态：低位缩量窗口',
               f'pos250中位={pos_med:.2f}<0.30 且换手能量={w2}——历史该状态下选股期望为负(8-9月三重归因)，建议降低新仓频率。详见 reports/market_state_latest.json', 'warning')
        print('[probe] 已推送状态切换告警')
    print('saved', OUT, f'total {time.time()-t0:.0f}s')
    return 0

if __name__ == '__main__':
    sys.exit(main())
