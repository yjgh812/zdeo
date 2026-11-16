# -*- coding: utf-8 -*-
"""ddprotect_gate_daily.py — 回撤保护覆盖层·门控每日信号件(影子接线 Phase 1).

定位(主人 2026-09-14「急需→接趋势门控覆盖层」裁决, PREREG-VOLTARGET-V2 混合臂曲线为证据基座):
  每日 08:00 用 T-1 收盘市场池指数 vs MA20 判门控态(无前视: t收盘观测t+1生效),
  落 data/ddprotect_gate_state.json; 状态翻转时 toast 提醒.
  影子期语义: 不改选股信号/不拦-8%止损链/不自动卖出——正式降仓须主人拍板floor档位
  并确认卖出合同修订(§10-2)后另行接线.

门控规则(rule_version=hyst2%, 2026-09-14起; 依据 PREREG-DDPROTECT-V6 b411db62 实测全面占优):
  门控指数 = 市场等权池价格指数 cumprod(1+全池AVG日收益), 起点2020-01-02
  进防御 = 指数 < MA20×0.98;  复满仓 = 指数 > MA20×1.02 (滞回带2%, 状态机逐日递推)
  满仓 = 非防御态; 防御档建议敞口 floor=0.5或0.7 (档位待主人拍板)
退出码: 0 成功; 2 数据不可用.
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'D:\zcodesj')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
STATE = ROOT / 'data' / 'ddprotect_gate_state.json'
FLOORS = (0.5, 0.7)
MA_N = 20


def main() -> int:
    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    df = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>='20200101' GROUP BY date ORDER BY date", con)
    con.close()
    if len(df) < MA_N + 5:
        print(f'[FAIL] 池日收益不足({len(df)}行)')
        return 2
    r = df.set_index(pd.to_datetime(df['date'].astype(str), format='%Y%m%d'))['ret'].astype(float)
    idx = (1 + r).cumprod()
    ma = idx.rolling(MA_N).mean()
    # 滞回2%状态机(rule_version=hyst2%, 与V6验证口径一致)
    HYST = 0.02
    cur, states = False, []
    for t in idx.index:
        if (not cur) and (idx.loc[t] < ma.loc[t] * (1 - HYST)):
            cur = True
        elif cur and (idx.loc[t] > ma.loc[t] * (1 + HYST)):
            cur = False
        states.append(cur)
    gate = not states[-1]          # True=满仓(非防御)
    last_d = idx.index[-1]
    gap_pct = round((idx.iloc[-1] / ma.iloc[-1] - 1) * 100, 2)

    prev = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    rule_changed = prev.get('rule_version') != 'hyst2%'
    rec = {'date': str(last_d.date()), 'computed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'rule_version': 'hyst2%', 'gate': '满仓' if gate else '防御档', 'index_above_ma20_pct': gap_pct,
           'suggest_exposure': {'floor_0.5': 1.0 if gate else 0.5, 'floor_0.7': 1.0 if gate else 0.7},
           'phase': '影子期(不自动交易)', 'ma_n': MA_N,
           'action_if_enabled': ('目标敞口100%, 无调仓' if gate else
                                 f'目标敞口降至floor档(0.5或0.7待拍板); 若前一日在仓且未降, 预演需减仓'),
           'prev_gate': prev.get('gate')}
    changed = (not rule_changed) and ('gate' in prev) and (prev['gate'] != rec['gate'])
    STATE.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding='utf-8')

    msg = (f"[gate] {rec['date']} 门控={rec['gate']} (指数距MA20 {gap_pct:+.2f}%) "
           f"翻转={changed} 影子期零交易")
    print(msg)
    if changed:
        try:
            from notify import deliver
            direction = '进入防御档(跌破MA20)' if not gate else '恢复满仓档(收复MA20)'
            deliver('回撤保护门控翻转', f'{direction}；{msg}', 'info')
        except Exception as e:
            print(f'[WARN] toast不可用({type(e).__name__}: {e})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
