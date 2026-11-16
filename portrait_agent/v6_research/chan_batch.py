# -*- coding: utf-8 -*-
"""缠论买卖点全量批跑(2026-09-05 主人指令: 缠论加入画像特征).

产出: 每股票的 buy1/buy2/buy3 买点 + 底背驰 信号日期序列 → chan_signals_v2.pkl
         v2=确认日口径(P2审计修复): 信号日=分型中心+1(第3根merged K), 未确认不产出; v1(中心日)=带前视已废弃
日期映射: BuySellPoint.price 是分型价格 → 与 fractals[].price 精确匹配(双指针, 时间有序)
         → fractal.kline_idx → merged[kline_idx].date
全量: 4736只×~3400bar×0.12s ≈ 10分钟.
"""
import os, sys, time
import sqlite3
sys.path.insert(0, r'D:\zcodesj')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
from chanlun_engine.analyzer import ChanAnalyzer

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-09-25 迁入v6_research后, cache仍在父目录 portrait_agent/cache
CACHE = os.path.join(os.path.dirname(HERE), 'cache')
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302','688','689')

T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

def map_points_to_dates(points, fractals, merged):
    """points: [(type, price)] 时间序; fractals: [(price, kline_idx)] 时间序.
    双指针: 每个点找 price 精确相等且 kline_idx 递增的最小 fractal. 返回 [(type, date)]."""
    out = []
    fi = 0
    used = -1
    for typ, price in points:
        # 从 max(fi, used+1) 起找
        j = max(fi, used + 1)
        while j < len(fractals):
            if fractals[j][0] == price and fractals[j][1] > used:
                # v2 无前视口径(P2审计修复, 2026-09-06): 分型确认日=第3根merged K,
                # 信号日从中心K(带前视,见P2_CHAN_LOOKAHEAD_AUDIT)右移至 merged[kline_idx+1]
                ci = fractals[j][1] + 1
                used = fractals[j][1]
                fi = j + 1
                if ci < len(merged):
                    out.append((typ, merged[ci].date))
                else:
                    out.append((typ, None))  # 分型未确认(序列末端), 不产出信号
                break
            j += 1
        else:
            out.append((typ, None))  # 未匹配(罕见)
    return out

def main():
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>='20130101'", con)
    con.close()
    df = df[df['code'].str[:3].isin(TRAD_PREFIX)]
    df = df.sort_values(['code', 'date']).reset_index(drop=True)
    log(f'rows={len(df)} stocks={df["code"].nunique()}')

    analyzer = ChanAnalyzer()
    rows = []
    codes = list(df.groupby('code', sort=False))
    for k, (code, g) in enumerate(codes):
        if k % 500 == 0: log(f'stock {k}/{len(codes)}')
        try:
            r = analyzer.analyze(g.reset_index(drop=True))
            merged = r.merged
            # fractals: (price, kline_idx) 时间序(kline_idx升序)
            fr = sorted([(f.price, f.kline_idx) for f in r.fractals], key=lambda x: x[1])
            # 买点(时间序: 按 seg_idx+生成序) + 底背驰
            pts = [(b.type, b.price) for b in r.buy_sell_points if b.type.startswith('buy')]
            # 债务②下游(2026-09-20): 被失效机制取消的底背驰(valid=False)不产出信号
            pts += [('bottom_div', d.price) for d in r.divergences
                    if d.direction == 'bottom' and d.valid]
            pts_sorted = pts  # buy_sell_points按seg_idx升序, divergences近似时间序
            mapped = map_points_to_dates(pts_sorted, fr, merged)
            for typ, dt in mapped:
                if dt is not None:
                    rows.append({'code': code, 'date': dt, 'type': typ})
        except Exception as e:
            if k < 3:  # 前几只打印异常样本, 不静默
                import traceback; traceback.print_exc()
            continue  # 单股失败不阻断
    sig = pd.DataFrame(rows)
    log(f'信号总数={len(sig)} 分布={sig["type"].value_counts().to_dict() if len(sig) else {}}')
    sig.to_pickle(os.path.join(CACHE, 'chan_signals_v2.pkl'))
    log('落盘 cache/chan_signals_v2.pkl')

if __name__ == '__main__':
    main()
