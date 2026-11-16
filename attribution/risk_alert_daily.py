# -*- coding: utf-8 -*-
"""risk_alert_daily.py — 高位突破衰竭风险提示器（生产版，每日盘前 schtasks 触发）。

定位：只读提示层（第四信息源）。卖出三元=-8%止损/趋势走坏/主人指令 不变；
本提示器**无任何交易权**，不进闸门、不自动卖出（2026-09-11 主人「现在就执行」接线裁决）。
证据链（三条独立实证）：ENGINE-B-HIGHPOS-BREAKOUT 高位突破 20日超额 -1.74pp/t-10.09（9/9）；
BREAKOUT-MIDPOS high 带突破组日聚簇 t-6.16/n=156322（9/11）；
BREAKOUT-PULLBACK-ENTRY 高位子带追入臂 -0.72%（9/11）。
语义：持仓股处于高位(pos250>=0.70)且近5个交易日命中突破/炸板/跳空事件
     => 该域 20 日条件期望为负 => 今日不宜加仓，注意风险（预判已列反向风险）。

数据：持仓=westock_client.WeStockClient().positions()（唯一真值源，与 trade_executor 同源）；
     行情=workbuddyspace dragon_quant_qfq.db（生产主库 fail-closed）。
输出：reports/risk_alert_{date}.md（每日覆盖）+ RISK_ALERT 级 toast（notify.py send，本机通道）。
告警通道=本机 toast+notifications.log（手机推送永久禁止，宪法级禁令）。
退出码：0=正常（含无提示）；2=fail-closed（持仓拉取失败/数据缺失，已告警）。
用法：python risk_alert_daily.py [--date YYYYMMDD]
"""
import os, sys, json, time, sqlite3, argparse, subprocess
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"D:/workbuddyspace")
sys.path.insert(0, r"D:/zcodesj")

QFQ_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
REPORT_DIR = r'D:/zcodesj/reports'
LOW_BAND, HIGH_BAND = 0.50, 0.70
EVENT_WINDOW = 5
VOL_X = 1.2
LINE = '=' * 64


NOTIFY_PY = r'D:/zcodesj/notify.py'   # 项目根目录（attribution 子目录脚本须用绝对路径）

def notify(title: str, content: str, level: str = 'warning') -> None:
    try:
        subprocess.run([sys.executable, NOTIFY_PY, 'send',
                        '--title', title, '--content', content, '--level', level],
                       timeout=60)
    except Exception as e:
        print(f'[risk_alert] notify 发送失败: {e}（告警未送达, fail-loud）')


def fetch_positions():
    from westock_client import WeStockClient
    ws = WeStockClient()
    resp = ws.positions()
    data = (resp or {}).get('data', {}).get('positions', [])
    out = []
    for x in data:
        try:
            qty = int(x.get('quantity', 0))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        out.append({'code': str(x.get('code', '')), 'name': str(x.get('name', ''))})
    return out


def scan(positions, asof):
    codes = [p['code'][-6:] for p in positions]   # 去交易所前缀，6位纯数字与qfq库对齐
    con = sqlite3.connect(QFQ_DB)
    ph = ','.join('?' * len(codes))
    df = pd.read_sql(
        f"SELECT code,date,open,high,close,volume FROM stock_daily_meta_qfq "
        f"WHERE date>='20150101' AND substr(code,1,6) IN ({ph}) ORDER BY code,date",
        con, params=codes)
    con.close()
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    df['date'] = df['date'].astype(np.int64)
    rows = []
    for p in positions:
        code6 = p['code'][-6:]
        g = df[df['code6'] == code6].reset_index(drop=True)
        row = {'code': code6, 'name': p['name'], 'level': 'NODATA', 'pos250': None,
               'events': []}
        if len(g) < 261:
            rows.append(row)
            continue
        c = g['close'].to_numpy(); o = g['open'].to_numpy()
        v = g['volume'].to_numpy(); h = g['high'].to_numpy()
        d = g['date'].to_numpy()
        i = len(g) - 1
        s = pd.Series(c)
        mn250 = s.rolling(250, min_periods=250).min().to_numpy()
        mx250 = s.rolling(250, min_periods=250).max().to_numpy()
        prev = s.shift(1)
        box_top = prev.rolling(60, min_periods=60).max().to_numpy()
        vol_ma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        pos = (c[i] - mn250[i]) / (mx250[i] - mn250[i]) if mx250[i] > mn250[i] else np.nan
        row['pos250'] = round(float(pos), 3) if np.isfinite(pos) else None
        evs = []
        for k in range(max(1, i - (EVENT_WINDOW - 1)), i + 1):
            b1 = bool(c[k] > box_top[k] and v[k] >= VOL_X * vol_ma20[k])
            b2 = False
            if np.isfinite(prev.iloc[k]) and prev.iloc[k] > 0:
                lim = round(prev.iloc[k] * (1.2 if code6[:3] == '300' else 1.1), 2)
                b2 = bool(h[k] >= lim and c[k] < lim)
            b3 = bool(prev.iloc[k] > 0 and o[k] > prev.iloc[k] * 1.03 and c[k] > o[k])
            hits = [t for t, b in (('突破', b1), ('炸板', b2), ('跳空', b3)) if b]
            if hits:
                evs.append({'date': int(d[k]), 'types': hits})
        row['events'] = evs
        band = 'high' if pos >= HIGH_BAND else ('mid' if pos >= LOW_BAND else 'low')
        row['level'] = ('RISK_ALERT' if (band == 'high' and evs) else
                        'WATCH' if (band == 'mid' and evs) else 'NONE')
        rows.append(row)
    return rows


def write_report(rows, asof, date_str):
    alerts = [r for r in rows if r['level'] == 'RISK_ALERT']
    watch = [r for r in rows if r['level'] == 'WATCH']
    lines = [
        f'# RISK_ALERT_{date_str}',
        '',
        f'> 高位突破衰竭风险提示（第四信息源；卖出三元不变；本报告无交易权）。数据截至 {asof} 收盘。',
        '> 口径：pos250≥0.70×近5日{突破(>前60日高+量1.2×)/炸板/跳空}；WATCH=0.50-0.70 同事件。',
        '> 证据：-1.74pp/t-10.09(9/9) + high带t-6.16/n156322(9/11) + 高位子带追入-0.72%(9/11)。',
        '',
        f'## RISK_ALERT（{len(alerts)} 只）——今日不宜加仓',
        '',
    ]
    for r in alerts:
        ev = '；'.join(f"{e['date']}({'/'.join(e['types'])})" for e in r['events'])
        lines.append(f"- {r['code']} {r['name']} pos250={r['pos250']} 事件：{ev}")
    lines += ['', f'## WATCH（{len(watch)} 只）——观察', '']
    for r in watch:
        ev = '；'.join(f"{e['date']}({'/'.join(e['types'])})" for e in r['events'])
        lines.append(f"- {r['code']} {r['name']} pos250={r['pos250']} 事件：{ev}")
    lines += ['', f'其余 {len(rows) - len(alerts) - len(watch)} 只无提示。',
              '', '【预判，存在不确定性】高位突破=统计负期望域，非卖出信号；主线动量与高位衰竭的张力见',
              'RISK_ALERT_SHADOW_20260911.md §二。反向风险：动量延续期高位股可继续上行。']
    path = os.path.join(REPORT_DIR, f'risk_alert_{date_str}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path, alerts, watch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    t0 = time.time()
    import datetime
    today = args.date or datetime.date.today().strftime('%Y%m%d')
    try:
        positions = fetch_positions()
    except Exception as e:
        notify('风险提示器FAIL-CLOSED', f'westock 持仓拉取失败: {type(e).__name__} {e}', 'error')
        print(f'[risk_alert] FAIL-CLOSED positions: {e}')
        return 2
    if not positions:
        print('[risk_alert] 无持仓，空转退出')
        return 0
    con = sqlite3.connect(QFQ_DB)
    asof = int(con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0])
    con.close()
    rows = scan(positions, asof)
    path, alerts, watch = write_report(rows, asof, today)
    print(f'[risk_alert] asof={asof} 持仓={len(rows)} RISK={len(alerts)} WATCH={len(watch)} -> {path}')
    if alerts:
        names = '、'.join(f"{r['code']} {r['name']}(pos{r['pos250']})" for r in alerts)
        notify('高位突破风险提示', f'{len(alerts)} 只持仓处于高位突破衰竭域，今日不宜加仓：{names}。详见 reports/risk_alert_{today}.md（提示非卖出指令）', 'warning')
    print(f'[risk_alert] done {time.time()-t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
