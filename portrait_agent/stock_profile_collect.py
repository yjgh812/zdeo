# -*- coding: utf-8 -*-
"""通用单股画像采集工具(2026-09-06 固化: 金健米业三轮采集方法论产品化).

用法:
  python stock_profile_collect.py --code 600127 [--start 20260101] [--ystart 20260101]
  python stock_profile_collect.py --code 600127,000858   # 批量
  --start  : qfq K线加载起点(指标预热), 默认 20251201
  --ystart : 统计窗口起点(F1-F8 截面 + F9 panel过滤), 默认 20260101; 跨年画像传如 20240101
输出: data/stock_profile_<code>_<date>.json (九族46项特征)

九族维度(金健米业三轮采集固化):
  F1 趋势族(8): YTD/均线乖离x3/pos250/距年高/多头天数/阶段结构
  F2 动量族(6): ret5/10/20/60/连涨现状/最长连涨
  F3 波动族(5): amp20/amp全期/vol20/最大单日涨/跌
  F4 量能族(4): 量比5v20/峰值额/峰值日/放量日数
  F5 事件族(7): 涨停数/最高连板/涨停日期/上下跳空/创20日高/创年高
  F6 结构族(6): MACD金叉死叉零轴背离/K线组合/近5日K线
  F7 资金族(4): 量价齐升/放量下跌/主升段量能/筹码成本x3
  F8 分布族(9): 偏度/峰度/盈亏比/逐月矩阵/日内微观/极端日档案/相对强弱
  F9 体系族(5): PIT时间线/排雷分/缠论买点复盘/33事件轨迹/板块角色
无前视: 全部特征基于<=截面日数据. 输出含体系视角结论模板(正向/反向/状态).
"""
import os, sys, json, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats as sps

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
PIT_DB = r'D:\zcodesj\factor_pit.db'
RISK12 = ['lhb_buy_5', 'limit_break_5', 'new_high_3', 'vwap60_bias', 'vwap20_bias',
          'gap_up_run_5', 'chan_buy3_10', 'new_high20', 'macd_golden_hi', 'rounded_20',
          'red_3_soldiers', 'higher_low']

def load_kline(code, start='20251201'):
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    df = pd.read_sql_query(
        "SELECT date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>=? ORDER BY date", con, params=[code, start])
    con.close()
    return df.reset_index(drop=True)

def ema(s, p):
    out = s.copy().astype(float)
    if len(s) < p: return out*np.nan
    out.iloc[:p-1] = np.nan
    out.iloc[p-1] = s.iloc[:p].mean()
    k = 2/(p+1)
    for i in range(p, len(s)):
        out.iloc[i] = s.iloc[i]*k + out.iloc[i-1]*(1-k)
    return out

def collect(code, name, industry, start, ystart='20260101'):
    df = load_kline(code, start)
    if len(df) < 80:
        return {'error': f'K线不足({len(df)}<80), 扩大start预热区间'}
    o, h, l, c = (df[k].astype(float) for k in ['open', 'high', 'low', 'close'])
    v, amt, chg = df['volume'].astype(float), df['amount'].astype(float), df['change_pct'].astype(float)
    dates = df['date'].tolist()
    ystart_date = ystart  # F9 panel 过滤用日期串; 下一行起 ystart 被遮蔽为 int 索引
    ystart = next((i for i, d in enumerate(dates) if d >= ystart), 0)
    R = {'股票': f'{name}({code}) {industry}', '区间': f'{dates[ystart]}~{dates[-1]}({len(dates)-ystart}交易日)'}

    # ===== F1 趋势族 =====
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    hi250 = h.rolling(250, min_periods=60).max(); lo250 = l.rolling(250, min_periods=60).min()
    bull = ((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60) & (c > ma5))[ystart:]
    mret = {}
    for mth in sorted({d[:6] for d in dates[ystart:]}):
        ii = [i for i, d in enumerate(dates) if d.startswith(mth)]
        if len(ii) >= 2 and ii[0] > 0:
            mret[mth] = round((c.iloc[ii[-1]]/c.iloc[ii[0]-1]-1)*100, 1)
    R['F1趋势'] = {'YTD%': round((c.iloc[-1]/c.iloc[ystart]-1)*100, 1),
                 '现价': round(c.iloc[-1], 2),
                 'ma5乖离%': round((c.iloc[-1]/ma5.iloc[-1]-1)*100, 1),
                 'ma20乖离%': round((c.iloc[-1]/ma20.iloc[-1]-1)*100, 1),
                 'ma60乖离%': round((c.iloc[-1]/ma60.iloc[-1]-1)*100, 1),
                 'pos250%': round((c.iloc[-1]-lo250.iloc[-1])/(hi250.iloc[-1]-lo250.iloc[-1])*100),
                 '距一年高点%': round((c.iloc[-1]/hi250.iloc[-1]-1)*100, 1),
                 '多头天数/占比%': f'{int(bull.sum())}/{bull.mean()*100:.0f}',
                 '月度收益%': mret}

    # ===== F2 动量族 =====
    up = (c > c.shift(1)); streak = 0
    for i in range(len(up)-1, ystart, -1):
        if up.iloc[i]: streak += 1
        else: break
    R['F2动量'] = {f'ret{k}%': round((c.iloc[-1]/c.iloc[-k-1]-1)*100, 1) for k in (5, 10, 20, 60)} | {
        '当前连涨': streak,
        '区间最长连涨': int(up[ystart:].groupby((~up[ystart:]).cumsum()).sum().max())}

    # ===== F3 波动族 =====
    amp = (h-l)/c.shift(1)*100
    R['F3波动'] = {'amp20%': round(amp.iloc[-20:].mean(), 1), 'amp区间%': round(amp[ystart:].mean(), 1),
                 'vol20%': round(c.pct_change().iloc[-20:].std()*100, 2),
                 '最大单日涨%': round(chg[ystart:].max(), 1), '最大单日跌%': round(chg[ystart:].min(), 1)}

    # ===== F4 量能族 =====
    spike = (amt > 2*amt.rolling(20).mean())[ystart:]
    amt20q = amt.rolling(20).mean()
    amt_peak250 = amt20q.rolling(250, min_periods=60).max().iloc[-1]
    amt_ratio = round(float(amt20q.iloc[-1]/amt_peak250), 2) if (amt_peak250 and amt_peak250 > 0) else None
    R['F4量能'] = {'量比5v20': round(amt.rolling(5).mean().iloc[-1]/amt.rolling(20).mean().iloc[-1], 2),
                 '峰值成交亿': round(amt[ystart:].max()/1e8, 2),
                 '峰值日': dates[int(amt[ystart:].idxmax())],
                 '放量日数(>2x)': int(spike.sum()),
                 '地量分位(20日均额/年峰)': amt_ratio if amt_ratio is not None else 'N/A',
                 '地量标记(<0.35)': bool(amt_ratio is not None and amt_ratio < 0.35)}

    # ===== F5 事件族 =====
    lim = chg >= 9.8
    lianban = lim.groupby((~lim).cumsum()).cumsum()
    ph, pl = h.shift(1), l.shift(1)
    R['F5事件'] = {'涨停次数': int(lim[ystart:].sum()),
                 '最高连板': int(lianban[ystart:].max()),
                 '涨停日期': [dates[i] for i in np.where(lim[ystart:].values)[0]],
                 '上跳空': int((l > ph)[ystart:].sum()), '下跳空': int((h < pl)[ystart:].sum()),
                 '创20日新高天': int((c >= h.shift(1).rolling(20).max()*0.998)[ystart:].sum()),
                 '创年新高天': int((c >= h.shift(1).rolling(250, min_periods=60).max()*0.998)[ystart:].sum())}

    # ===== F6 结构族 =====
    e12, e26 = ema(c, 12), ema(c, 26)
    dif = e12-e26; dea = dif.copy()
    if len(dif) > 33:
        dea.iloc[:33] = np.nan; dea.iloc[33] = dif.iloc[25:34].mean()
        for i in range(34, len(dif)): dea.iloc[i] = dif.iloc[i]*0.2 + dea.iloc[i-1]*0.8
    hist = dif-dea
    body = (c-o).abs(); rng = (h-l).replace(0, np.nan)
    soldiers = int((( (c>o)&(c>c.shift(1)) ).astype(float).rolling(3).sum() == 3)[ystart:].sum())
    engulf = int(((c.shift(1)<o.shift(1))&(c>o)&(o<=c.shift(1))&(c>=o.shift(1))&(body>body.shift(1)))[ystart:].sum())
    topdiv = [dates[i] for i in range(40, len(df))
              if i >= ystart and c.iloc[i] >= c.iloc[i-40:i].max() and dif.iloc[i] < dif.iloc[i-40:i].max()]
    near5 = [{'日期': dates[i], '阴阳': '阳' if c.iloc[i] > o.iloc[i] else '阴',
              '涨跌%': round(chg.iloc[i], 1)} for i in range(len(df)-5, len(df))]
    R['F6结构'] = {'MACD金叉': [dates[i] for i in np.where((hist>0)&(hist.shift(1)<=0))[0] if i >= ystart],
                 'MACD死叉': [dates[i] for i in np.where((hist<0)&(hist.shift(1)>=0))[0] if i >= ystart],
                 'DIF上穿零轴': [dates[i] for i in np.where((dif>0)&(dif.shift(1)<=0))[0] if i >= ystart],
                 '顶背离': topdiv, '红三兵天': soldiers, '看涨吞没天': engulf, '近5日K线': near5}

    # ===== F7 资金族 =====
    up_day = c > c.shift(1)
    aug_months = sorted({d[:6] for d in dates[ystart:]})[-2:]
    vol_ratio_m = {}
    for mth in aug_months:
        ii = [i for i, d in enumerate(dates) if d.startswith(mth)]
        if ii: vol_ratio_m[mth] = round(amt.iloc[ii].mean()/1e8, 2)
    vwc20 = (c*v).rolling(20).sum()/v.rolling(20).sum()
    vwc60 = (c*v).rolling(60).sum()/v.rolling(60).sum()
    vwc120 = (c*v).rolling(120).sum()/v.rolling(120).sum()
    R['F7资金'] = {'量价齐升天': int((up_day & (amt > amt.shift(1)))[ystart:].sum()),
                 '放量下跌天(出货嫌疑)': int(((~up_day) & (amt > amt.shift(1)))[ystart:].sum()),
                 '近两月日均额亿': vol_ratio_m,
                 '筹码乖离': {'20日%': round((c.iloc[-1]/vwc20.iloc[-1]-1)*100, 1),
                            '60日%': round((c.iloc[-1]/vwc60.iloc[-1]-1)*100, 1),
                            '120日%': round((c.iloc[-1]/vwc120.iloc[-1]-1)*100, 1)}}

    # ===== F8 分布族 =====
    rets = c.pct_change().iloc[ystart+1:].dropna()*100
    up_d, dn_d = rets[rets > 0], rets[rets < 0]
    pos_ir = ((c-l)/(h-l)).iloc[ystart:]
    ext = df.iloc[ystart:][chg.iloc[ystart:].abs() > 5][['date', 'close', 'change_pct']].copy()
    ext['amount亿'] = [round(amt.iloc[i]/1e8, 1) for i in ext.index]
    R['F8分布'] = {'偏度': round(float(sps.skew(rets)), 2), '峰度': round(float(sps.kurtosis(rets)), 2),
                 '胜率%': round((rets > 0).mean()*100, 1),
                 '盈亏比': round(up_d.mean()/abs(dn_d.mean()), 2) if len(dn_d) and dn_d.mean() != 0 else None,
                 '极端日数(|chg|>5%)': len(ext),
                 '高开/低开天': f"{int((o > c.shift(1))[ystart:].sum())}/{int((o < c.shift(1))[ystart:].sum())}",
                 '收盘位置近20日%': round(pos_ir.iloc[-20:].mean()*100),
                 '极端日明细': ext.to_dict('records')}

    # ===== F9 体系族(panel/排雷/缠论/相对强弱) =====
    try:
        global _PANEL_CACHE   # 批量时单次加载(5.8GB≈40s), 复用
        if '_PANEL_CACHE' not in globals() or _PANEL_CACHE is None:
            _PANEL_CACHE = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
        m = _PANEL_CACHE
        sub = m[(m['code'] == code) & (m['date'].astype(str) >= ystart_date)].sort_values('date')
        last = sub.iloc[-1] if len(sub) else None
        sys_view = {}
        if last is None:
            sys_view['说明'] = '池外股票(panel池=剔ST/北交所/收盘>20元/流动性, 高价股无panel行)——排雷/事件轨迹/板块超额仅支持池内股票; F1-F8量价族不受影响'
        if last is not None:
            trg = [f for f in RISK12 if pd.notna(last.get(f)) and last[f] > 0.5]
            sys_view['排雷分'] = f'{len(trg)}/12 触发={trg or "无"}'
            sys_view['PIT'] = (f"ROE={last['pit_roe']:.2f}% EP={last['pit_ep']:.4f}"
                              f"({'盈' if last['pit_ep'] > 0 else '亏'})" if pd.notna(last['pit_ep']) else 'PIT缺失')
            EVENTS = ['island_5','limit_break_5','gap_up_run_5','new_high_3','dip_reb_10','rounded_20',
                      'chan_buy1_10','chan_buy2_10','chan_buy3_10','chan_bdiv_10','lhb_buy_5','ind_lim_ge3',
                      'vwap60_bias','vwap20_bias','up4of5','higher_low','new_high20','gap_up_hold']
            trig = {}
            for f in EVENTS:
                if f not in sub.columns: continue
                on = sub[sub[f].fillna(0) > 0.5]
                if len(on): trig[f] = f"{len(on)}天({on['date'].iloc[0]}~{on['date'].iloc[-1]})"
            sys_view['33事件触发轨迹'] = trig
            L1 = industry.split('-')[0] if industry else ''
            ag = m[m['L1'] == L1]
            jj = sub.set_index('date'); jj_ret = (jj['close']/jj['close'].shift(1)-1)*100
            ag_ret = ag.groupby('date').apply(
                lambda g: (g['close'].mean()/g['close'].shift(1).mean()-1)*100 if len(g) > 10 else np.nan,
                include_groups=False).dropna()
            both = pd.DataFrame({'a': jj_ret, 'b': ag_ret}).dropna()
            if len(both):
                both['rel'] = both['a'] - both['b']
                sys_view['板块相对超额%'] = {'60日': round(both['rel'].tail(60).sum(), 1),
                                          '20日': round(both['rel'].tail(20).sum(), 1),
                                          '5日': round(both['rel'].tail(5).sum(), 1),
                                          '口径': '日差累计pp(Σ日收益差), 非区间复利超额, 勿与ret60直接对比'}
            # 板块内排名(新增: ret20/pos250名次, F9板块角色量化)
            sec = ag[ag['date'] == last['date']]
            if len(sec) > 5:
                rr = sec['ret20'].rank(ascending=False)[sec['code'] == code]
                pr = sec['pos250'].rank(ascending=False)[sec['code'] == code]
                if len(rr):
                    sys_view['板块内排名'] = (f"ret20第{int(rr.iloc[0])}/{len(sec)}位, "
                                           f"pos250第{int(pr.iloc[0])}/{len(sec)}位")
        R['F9体系'] = sys_view
    except Exception as e:
        R['F9体系'] = {'error': str(e)}

    # ===== F9 补充: 披露窗(直查factor_pit, 池内外均可用; 20260915新特征面) =====
    # 注: roe原始列跨期单位混乱(小数/百分数混存, 20260915实锤), ROE真项一律用 ep/bp 自洽推导
    try:
        pc = sqlite3.connect(f'file:{PIT_DB}?mode=ro', uri=True)
        prows = pc.execute('SELECT pit_avail_date, report_date, ep, bp FROM stock_factors_fundamental_pit_v2 '
                           'WHERE code=? AND ep>0 AND bp>0 ORDER BY pit_avail_date', (code,)).fetchall()
        pc.close()
        from datetime import datetime as _dt
        today = _dt.strptime(dates[-1], '%Y%m%d')
        avail = [r for r in prows if int(r[0]) <= int(dates[-1])]
        if avail:
            la, rep, epn, bpn = avail[-1]
            days = (today - _dt.strptime(str(la), '%Y%m%d')).days
            pe, pb = (avail[-2][2], avail[-2][3]) if len(avail) >= 2 else (None, None)
            parts = []
            if pe and pe > 0 and epn and pb and pb > 0 and bpn:
                parts.append(f"ep{(epn/pe-1)*100:+.1f}%")
                parts.append(f"ROE真项{((epn/bpn)/(pe/pb)-1)*100:+.1f}%")
                parts.append(f"B/M项{(bpn/pb-1)*100:+.1f}%")
            jump = ' '.join(parts) if parts else 'ep无前值'
            fresh = '刚披露(<=10天)' if days <= 10 else f'{days}天前'
            tgt = R.get('F9体系')
            if not isinstance(tgt, dict) or 'error' in tgt:
                R['F9体系'] = tgt = {}
            tgt['披露窗'] = f'{fresh}({la},报告期{rep}) {jump}'
    except Exception as e:
        tgt = R.get('F9体系')
        if isinstance(tgt, dict):
            tgt['披露窗'] = f'N/A({e})'
    return R

def summary_text(code, r):
    """人类可读摘要(新增)."""
    if 'error' in r:
        return f"{code}: 采集失败({r['error']})"
    f1, f9 = r['F1趋势'], r.get('F9体系', {})
    lines = [f"【{r['股票']}】{r['区间']}"]
    lines.append(f"  趋势: YTD{f1['YTD%']:+.1f}% | 现价{f1['现价']} | pos250={f1['pos250%']}% "
                 f"| 距年高{f1['距一年高点%']:+.1f}%")
    lines.append(f"  动量: ret20={r['F2动量']['ret20%']:+.1f}% ret60={r['F2动量']['ret60%']:+.1f}%")
    lines.append(f"  波动: vol20={r['F3波动']['vol20%']}% amp20={r['F3波动']['amp20%']}%")
    lines.append(f"  量能: 量比={r['F4量能']['量比5v20']} 涨停{r['F5事件']['涨停次数']}次(最高{r['F5事件']['最高连板']}连板)")
    f8 = r['F8分布']
    lines.append(f"  分布: 胜率{f8['胜率%']}% 盈亏比{f8['盈亏比']} 偏度{f8['偏度']} 极端日{f8['极端日数(|chg|>5%)']}个")
    if '排雷分' in f9:
        lines.append(f"  体系: 排雷{f9['排雷分']} | PIT {f9.get('PIT', 'N/A')}")
        if '板块相对超额%' in f9:
            e = f9['板块相对超额%']
            lines.append(f"  板块超额(日差累计pp,非复利): 60日{e['60日']:+.1f} 20日{e['20日']:+.1f} 5日{e['5日']:+.1f}"
                        + (f" | {f9['板块内排名']}" if '板块内排名' in f9 else ''))
    elif '说明' in f9:
        lines.append(f"  体系: {f9['说明'][:60]}…")
    if '披露窗' in f9:
        lines.append(f"  披露窗: {f9['披露窗']}")
    # 指纹对齐快照(观察级, 20260915百亚研究固化; 市场门控状态需外部上证pos250另查)
    try:
        conds = [
            ('pos250<=45', float(f1['pos250%']) <= 45),
            ('距高<=-30', float(f1['距一年高点%']) <= -30),
            ('ret60<=0', float(r['F2动量']['ret60%']) <= 0),
            ('ret20>=-12', float(r['F2动量']['ret20%']) >= -12),
            ('vol20<=4', float(r['F3波动']['vol20%']) <= 4),
        ]
        ar = r['F4量能'].get('地量分位(20日均额/年峰)')
        if isinstance(ar, (int, float)):
            conds.append((f'地量{ar}<0.35' if ar < 0.35 else f'量能{ar}', ar < 0.35))
        else:
            conds.append(('地量N/A', False))
        ok = sum(1 for _, v in conds if v)
        detail = ' '.join(f"{n}{'✓' if v else '✗'}" for n, v in conds)
        lines.append(f"  指纹对齐(观察级): {ok}/{len(conds)} [{detail}] (R∧地量∧市场非HIGH=百亚型, 主张以prereg OOS为准)")
    except Exception:
        pass
    return '\n'.join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--start', default=None, help='qfq加载起点(预热), 默认随ystart自动扩约400日历日')
    ap.add_argument('--ystart', default='20260101')
    args = ap.parse_args()
    if args.start is None:
        # ystart 主参数, start 只管预热: 显式跨年 ystart 时自动前扩约400日历日(≈MA250预热),
        # 防止"只传ystart跨年但start默认半年"导致统计窗口被静默截断(20260910实锤);
        # 默认路径(ystart=20260101)保持原 start=20251201 不变(向后兼容, 9/9基线分毫不差)
        from datetime import datetime, timedelta
        if args.ystart != '20260101':
            d0 = datetime.strptime(args.ystart, '%Y%m%d')
            args.start = (d0 - timedelta(days=400)).strftime('%Y%m%d')
            print(f'[auto] --start 自动扩至 {args.start} (ystart 前约400日历日, MA250 预热)')
        else:
            args.start = '20251201'
    codes = [x.strip() for x in args.code.split(',')]
    con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    allnm = dict(con.execute("SELECT code,name||' '||industry FROM industry_map_ths").fetchall())
    con.close()
    out = {}
    summaries = []
    for code in codes:
        name_ind = allnm.get(code, '')
        name = name_ind.split(' ')[0] if name_ind else code
        industry = ' '.join(name_ind.split(' ')[1:]) if name_ind else '未知'
        print(f'采集 {name}({code}) {industry} ...', flush=True)
        r = collect(code, name, industry, args.start, args.ystart)
        out[code] = r
        if 'error' in r:
            print(f'  FAIL: {r["error"]}')
            summaries.append(summary_text(code, r))
            continue
        print(f"  YTD={r['F1趋势']['YTD%']}% pos250={r['F1趋势']['pos250%']}% 排雷={r.get('F9体系', {}).get('排雷分', 'N/A')}")
        summaries.append(summary_text(code, r))
    fp = os.path.join(r'D:\zcodesj\data', f'stock_profile_{args.code.replace(",", "_")}_report.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    # 文本摘要落盘
    fp_txt = fp.replace('.json', '_summary.txt')
    open(fp_txt, 'w', encoding='utf-8').write('\n\n'.join(summaries))
    print('\n' + '\n\n'.join(summaries))
    print(f'\n落盘 {fp} + {fp_txt}')

if __name__ == '__main__':
    main()
