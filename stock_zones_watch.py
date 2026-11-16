# -*- coding: utf-8 -*-
"""stock_zones_watch.py — 持仓买卖区域状态哨 v1 (20261004, 轻接线·只读提醒层)
定位: 盘前对持仓跑 stock_zones_agent 引擎, 提取卖出区/追高风险/下跌中继命中 → toast 告警; 无命中静默。
与既有链边界: universe_bs_zone_locator=宇宙扫描命中票价格带标注(0745链); 09:15 AI晨报=dossier解读; 本哨=持仓买卖区域状态(独立增量)。
铁律: 零交易权(不碰 trade_executor/不下单); 非交易日自动跳过; 持仓清单=data/holdings_codes_latest.json 快照(真值仍以westock实时为准)。
用法: python stock_zones_watch.py            # 正常哨
      python stock_zones_watch.py --force    # 忽略非交易日守卫(演示/测试)
"""
import sys, json, sqlite3
from pathlib import Path

ROOT = Path(r'D:\zcodesj')
sys.path.insert(0, str(ROOT))

def trading_day_guard():
    """qfq 最新交易日 == 今天(或今天非交易日时>=最近交易日)才跑; 假期跳过"""
    conn = sqlite3.connect(f'file:{ROOT / "data" / "dragon_quant_qfq.db"}?mode=ro', uri=True)
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) FROM stock_daily_meta_qfq")
    latest = cur.fetchone()[0]
    conn.close()
    import datetime
    today = datetime.date.today().strftime('%Y%m%d')
    return latest >= today, latest

def main():
    force = '--force' in sys.argv
    ok, latest = trading_day_guard()
    if not ok and not force:
        print(f'[SKIP] qfq最新={latest} 非今日, 假期/数据未更新, 哨跳过')
        return 0
    holdings = json.loads((ROOT / 'data' / 'holdings_codes_latest.json').read_text(encoding='utf-8'))
    positions = holdings.get('positions', [])
    if not positions:
        print('[SKIP] 持仓快照为空')
        return 0
    import stock_zones_agent as agent
    alerts = []
    lines = []
    for p in positions:
        code6 = p['code'][2:] if p['code'][:2] in ('sz', 'sh') else p['code']
        name = p.get('name', code6)
        try:
            report = agent.analyze(code6, name=name)
        except SystemExit:
            continue
        except Exception as e:
            lines.append(f'{code6} {name}: 引擎异常 {e}')
            continue
        hits = []
        for ln in report.splitlines():
            if '🔴命中' in ln or '⚠️命中' in ln or '⚠️疑似' in ln or '接近命中' in ln:
                hits.append(ln.strip())
        if hits:
            alerts.append((code6, name, hits))
            lines.append(f'{code6} {name}: ' + ' | '.join(x.split(' ')[0] + x[x.find('['):][:60] for x in hits))
        else:
            lines.append(f'{code6} {name}: 中性区无命中')
    # 日志落盘(幂等覆盖)
    out = ROOT / 'data' / 'stock_zones_watch_latest.txt'
    out.write_text(f'snapshot {latest}\n' + '\n'.join(lines), encoding='utf-8')
    # 告警
    if alerts:
        msg = '; '.join(f'{n}({c}): {h[0][0:20]}' for c, n, h in alerts[:6])
        print(f'[ALERT] {len(alerts)} 只持仓命中买卖区域状态:')
        for c, n, h in alerts:
            for x in h:
                print(f'  {c} {n}: {x[:110]}')
        try:
            import subprocess
            subprocess.run([sys.executable, str(ROOT / 'notify.py'), 'send',
                            '--title', '持仓买卖区域哨', '--content', msg, '--level', 'warn'],
                           capture_output=True, timeout=60)
            print('[TOAST] 已发送')
        except Exception as e:
            print(f'[TOAST-FAIL] {e}')
    else:
        print('[OK] 全部持仓处于中性观察区, 无告警')
    return 0

if __name__ == '__main__':
    sys.exit(main())
