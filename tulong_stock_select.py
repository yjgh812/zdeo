# -*- coding: utf-8 -*-
"""tulong_stock_select.py · 画像观察组观察工具（2026-09-25 名单制废除后适配版）

⚠️【2026-09-25 适配回写（四十九-D）】
  原 2026-09-13 版公式 = 画像Top500 ∪ bp_ep月度名单 + 胜率排序。2026-09-24 主人令名单制全面废除,
  bp_ep 月度名单生成器停机(CSV 冻结在停机日)——本版移除 bp_ep 臂, 候选池重锚 = 画像 Top500
  (short 截面, 研究观察组)。「个股月度胜率排序前提」语义保留(2026-09-13 主人裁决采用, 未被撤销);
  强势池标记/ml分位/画像分位均为零决策权标注列。
定位: 研究观察组生成器 —— 不是选股准入通道。产出仅供研究观察, 禁止作为买入清单或落入任何清单
      (措辞纪律: 研究观察组≠名单)。买入唯一合法路径 = trade_executor(宪法§10-1);
      准入唯一依据 = admit 闸门信号源清单 + R 闸实时判定(现行宪法§10)。
口径: 胜率 = qfq月末收盘近61个月上涨占比(>=30月样本); 辅助标注 ml分位/画像分位/强势池(零或低决策权).
出处: 画像 = portrait_agent/reports/portrait_最新json topn_list(short, 剔ST/价闸由infer发布闸承担) ;
      ml = stock_ml_scores_v22 最新分区 ; 胜率 = stock_daily_meta_qfq.
用法: python tulong_stock_select.py [--top 10]   # 只读; 输出控制台 + data/tulong_select_latest.csv
"""
import os, sys, json, sqlite3, glob, argparse
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
from stock_name_lookup import get_name

BASE = r'D:\zcodesj'
WIN_MONTHS_MIN = 30      # 胜率样本下限(月)
TOP_DEFAULT = 10


def load_candidates():
    """画像Top500(short)研究观察组; bp_ep 臂已随 2026-09-24 名单制废除移除"""
    pj_path = sorted(glob.glob(os.path.join(BASE, 'portrait_agent', 'reports', 'portrait_2026*.json')))[-1]
    pj = json.load(open(pj_path, encoding='utf-8'))
    seg = pj['models']['short'].get('topn_list') or []  # 2026-09-25过渡期top20双读退役(历史截面已全量统一topn_list)
    if not seg:
        raise ValueError(f"截面json缺topn_list({os.path.basename(pj_path)}; infer版本过旧或结构回退)")
    pa500 = [x['code'].zfill(6) for x in seg]
    pa_pct = {x['code'].zfill(6): round(x['分数'] * 100, 1) for x in seg}
    snap_date = pj.get('date', '?')
    pool = sorted(set(pa500))
    print(f"观察池 {len(pool)} 只 = 画像Top500观察组(截面{snap_date}; bp_ep臂已随2026-09-24名单制废除移除)")
    return pool, pa_pct


def load_ml_pct():
    con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro', uri=True, timeout=30)
    ml = pd.read_sql_query("SELECT code, ml_prob FROM stock_ml_scores_v22 "
                           "WHERE date=(SELECT MAX(date) FROM stock_ml_scores_v22)", con)
    con.close()
    ml['code'] = ml['code'].str.zfill(6)
    ml['ml_pct'] = ml['ml_prob'].rank(pct=True) * 100
    return dict(zip(ml['code'], ml['ml_pct']))


def monthly_winrate(pool):
    con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro', uri=True, timeout=180)
    chunks = []
    for i in range(0, len(pool), 50):
        ch = pool[i:i+50]
        ph = ','.join('?' * len(ch))
        chunks.append(pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN (" + ph + ") "
            "AND date>='20210901' ORDER BY code,date", con, params=ch))
    con.close()
    px = pd.concat(chunks, ignore_index=True)
    px['date'] = px['date'].astype(str)
    px['ym'] = px['date'].str[:6]
    me = px.sort_values('date').groupby(['code', 'ym'])['close'].last().reset_index()
    me = me.sort_values(['code', 'ym'])
    me['up'] = me.groupby('code')['close'].pct_change() > 0
    win = me[me['ym'] >= '202109'].groupby('code')['up'].agg(['sum', 'count'])
    win = win[win['count'] >= WIN_MONTHS_MIN]
    win['winrate'] = win['sum'] / win['count'] * 100
    # 近12月胜率(参考列: 区分长期稳/近期强势)
    r12 = me[me['ym'] >= '202509'].groupby('code')['up'].agg(['sum', 'count'])
    r12 = r12[r12['count'] >= 6]
    r12['wr12'] = r12['sum'] / r12['count'] * 100
    res = {}
    for c, r in win.iterrows():
        w12 = float(r12.loc[c, 'wr12']) if c in r12.index else np.nan
        res[c] = (float(r['winrate']), int(r['count']), w12)
    return res


def main():
    ap = argparse.ArgumentParser(description='画像观察组(画像Top500+胜率排序; 研究观察, 非准入通道)')
    ap.add_argument('--top', type=int, default=TOP_DEFAULT)
    args = ap.parse_args()

    pool, pa_pct = load_candidates()
    mlmap = load_ml_pct()
    win = monthly_winrate(pool)

    # 2026-09-15 方案5(胜率提升五方案): 强势池过滤标记——qfq 实证强势池
    # (MA5>MA10>MA20 + close>MA20 + ret20>0) T+5 胜率 43.4% vs 模拟盘实际 20.9%(PREREG_WINRATE_LIFT exp2 chase 组 n=429741)
    def strong_pool_flag(code: str):
        try:
            con = sqlite3.connect(
                r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro',
                uri=True, timeout=60)
            rows = con.execute(
                "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 21",
                (code,)).fetchall()
            con.close()
            if len(rows) < 21:
                return None
            cl = [r[0] for r in rows][::-1]
            if min(cl) <= 0:
                return None
            ma5 = sum(cl[-5:]) / 5
            ma10 = sum(cl[-10:]) / 10
            ma20 = sum(cl[-20:]) / 20
            ret20 = cl[-1] / cl[0] - 1
            return bool(ma5 > ma10 > ma20 and cl[-1] > ma20 and ret20 > 0)
        except Exception:
            return None

    rows = []
    for c in pool:
        if c not in win:
            continue
        wr, months, wr12 = win[c]
        mlv = mlmap.get(c)
        rows.append({'code': c, 'name': get_name(c) or '?',
                     'winrate': round(wr, 1), 'months': months, 'wr12m': round(wr12, 1) if wr12 == wr12 else None,
                     'ml_pct': round(mlv, 1) if mlv == mlv else None,
                     'pa_pct': pa_pct.get(c),
                     'strong_pool': strong_pool_flag(c)})
    out = pd.DataFrame(rows)
    out = out[~out['name'].str.contains('ST', na=False)]           # ST 剔除
    out = out.sort_values(['winrate', 'ml_pct'], ascending=False)  # 胜率排序前提(9/13裁决)
    print(f"胜率可算(>= {WIN_MONTHS_MIN}月)+剔ST: {len(out)} 只")
    cols = ['winrate', 'wr12m', 'months', 'code', 'name', 'ml_pct', 'pa_pct', 'strong_pool']
    print(f"\n=== 按月度胜率 前{args.top} (画像观察组+胜率排序; 研究观察, 非买入清单) ===")
    print(out[cols].head(args.top).to_string(index=False))
    topn = out.head(args.top)
    print(f"\n前{args.top} 画像分位中位: {topn['pa_pct'].median():.1f} | 强势池标记: {int(topn['strong_pool'].fillna(False).sum())}只")
    outp = os.path.join(BASE, 'data', 'tulong_select_latest.csv')
    topn.to_csv(outp, index=False, encoding='utf-8-sig')
    print(f"落盘 {outp} (研究观察组产物, 禁止作为买入清单)")


if __name__ == '__main__':
    main()
