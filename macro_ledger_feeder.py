# -*- coding: utf-8 -*-
"""macro_ledger_feeder.py — 宏观货币状态台账月度喂料器(P1积木, 影子层, 零交易联动).

数据源(全部脚本直连, 无MCP依赖):
  M2/M1    akshare macro_china_money_supply(央行, 全史)
  社融增量 akshare macro_china_shrzgm
  LPR      akshare macro_china_lpr
  十债     本地 erp_history.db bond_yield(erp_daily)

输出: data/macro_state_monthly.jsonl 按 data_month UPSERT(幂等);
      data/macro_ledger_feeder.log 一行日志.
分类(v1观察规则, 声明于源码): 两轴各自全史分位——
  货币轴: 十债分位≤30=宽 / ≥70=紧 / 其余=中
  信用轴: M2同比分位≥70=宽 / ≤30=紧 / 其余=中
  state = "货币X-信用Y"; 阈值改动须新版本号并留档.
退出码: 0 成功; 2 数据不可用.
用法: python macro_ledger_feeder.py
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import akshare as ak

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'D:\zcodesj')
LEDGER = ROOT / 'data' / 'macro_state_monthly.jsonl'
LOG = ROOT / 'data' / 'macro_ledger_feeder.log'
ERP_DB = r'D:\zcodesj\erp_history.db'
SOURCE = 'akshare(money_supply/shrzgm/lpr)+erp_history.bond_yield'


def pctile(series, v) -> float:
    s = [x for x in series if x is not None]
    if not s:
        return float('nan')
    return round(sum(1 for x in s if x <= v) / len(s) * 100, 1)


def main() -> int:
    # ---- M2/M1 ----
    ms = ak.macro_china_money_supply()
    ms['m'] = ms['月份'].str.extract(r'(\d{4})年(\d{2})月').apply(lambda t: f'{t[0]}-{t[1]}', axis=1)
    ms = ms.sort_values('m')
    last = ms.iloc[-1]
    data_month = last['m']
    m2_yoy = float(last['货币和准货币(M2)-同比增长'])
    m1_yoy = float(last['货币(M1)-同比增长'])
    # ---- 社融(12月滚动增量) ----
    sf = ak.macro_china_shrzgm()
    sf['m'] = sf['月份'].astype(str).str[:4] + '-' + sf['月份'].astype(str).str[4:6]
    sf = sf.sort_values('m')
    sf12 = float(sf['社会融资规模增量'].tail(12).sum())
    # ---- LPR ----
    lpr = ak.macro_china_lpr().sort_values('TRADE_DATE')
    lpr['month'] = lpr['TRADE_DATE'].astype(str).str[:7]
    lm = lpr.groupby('month').last()
    lpr_1y, lpr_5y = float(lm.iloc[-1]['LPR1Y']), float(lm.iloc[-1]['LPR5Y'])
    lpr_flat_months = int(len(lm) - 1 - len(lm[(lm['LPR1Y'] != lpr_1y)]))
    # ---- 十债(本地) ----
    con = sqlite3.connect(f'file:{ERP_DB}?mode=ro', uri=True)
    rows = [r0[0] for r0 in con.execute('SELECT bond_yield FROM erp_daily ORDER BY date').fetchall()]
    con.close()
    cn10y = float(rows[-1])

    m2_p = pctile(ms['货币和准货币(M2)-同比增长'].tolist(), m2_yoy)
    c10_p = pctile(rows, cn10y)
    money_axis = '宽' if c10_p <= 30 else ('紧' if c10_p >= 70 else '中')
    credit_axis = '宽' if m2_p >= 70 else ('紧' if m2_p <= 30 else '中')

    rec = {'record_date': datetime.now().strftime('%Y-%m-%d'), 'data_month': data_month,
           'kind': 'macro_money_state', 'source': SOURCE,
           'metrics': {'m2_yoy_pct': m2_yoy, 'm1_yoy_pct': m1_yoy, 'm1_m2_gap_pct': round(m1_yoy - m2_yoy, 2),
                       'social_financing_12m_yi': round(sf12, 0), 'lpr_1y_pct': lpr_1y, 'lpr_5y_pct': lpr_5y,
                       'lpr_flat_months': lpr_flat_months, 'cn10y_pct': cn10y,
                       'm2_yoy_hist_pctile': m2_p, 'cn10y_hist_pctile': c10_p},
           'state': f'货币{money_axis}-信用{credit_axis}',
           'note': 'P1积木影子件v1: 两轴分位观察规则(源码声明), 纯记录零交易联动'}

    lines = []
    if LEDGER.exists():
        lines = [json.loads(l) for l in LEDGER.read_text(encoding='utf-8').splitlines() if l.strip()]
    lines = [x for x in lines if x.get('data_month') != data_month]
    lines.append(rec)
    lines.sort(key=lambda x: x.get('data_month', ''))
    LEDGER.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in lines) + '\n', encoding='utf-8')
    msg = f'{datetime.now():%Y-%m-%d %H:%M:%S} OK {data_month} m2={m2_yoy} cn10y={cn10y} state={rec["state"]}'
    print(msg)
    print(f'台账UPSERT: {LEDGER} 共{len(lines)}条')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'{datetime.now():%Y-%m-%d %H:%M:%S} FAIL {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(2)
