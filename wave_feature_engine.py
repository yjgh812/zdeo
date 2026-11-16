# -*- coding: utf-8 -*-
"""波段特征引擎（2026-09-19 主人令：画像系统融合波段）

职责:
  1) Zigzag 波段划分(可调阈值) + 每波段全维度特征(价格/量能/时长/量价关系/位置)
  2) 滚动高低点区域判别(仅用截至当日数据, 无前视):
     底部得分 = 下跌衰竭(波段跌幅递减) + 末端缩量 + 双底回踩不创新低 + 深跌背景
     顶部得分 = 距段起涨幅大 + 急涨 + 天量/放量滞涨 + 大乖离
  3) 输出: 每日双得分序列 + 当前状态结论(供画像/扫描器融合)
依据: 8只盈利票波段解剖 + 6913启动事件大样本(介入位单调性/诱多段/衰竭结构) + 量价规则库S5/S6
用法:
  python wave_feature_engine.py --code 003006                # 单票全史波段+得分曲线(验证模式)
  python wave_feature_engine.py --code 003006 --last 1      # 仅当前状态(供融合)
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402


def f3(x):
    try:
        return float(x)
    except Exception:
        return None


def zigzag(closes, pct=0.12):
    """返回转折点索引列表(时间正序)。"""
    if len(closes) < 5:
        return [0, len(closes) - 1]
    pivots = [0]
    direction = 0
    ext = 0
    for i in range(1, len(closes)):
        if direction >= 0:
            if closes[i] > closes[ext]:
                ext = i
            elif closes[i] / closes[ext] - 1 <= -pct:
                pivots.append(ext)
                direction = -1
                ext = i
        else:
            if closes[i] < closes[ext]:
                ext = i
            elif closes[i] / closes[ext] - 1 >= pct:
                pivots.append(ext)
                direction = 1
                ext = i
    pivots.append(ext if ext > pivots[-1] else len(closes) - 1)
    return pivots


def wave_features(closes, vols, pivots, avg_v):
    """每个波段的全维度特征。"""
    feats = []
    for k in range(len(pivots) - 1):
        a, b = pivots[k], pivots[k + 1]
        if b <= a:
            continue
        seg = closes[a:b + 1]
        segv = vols[a:b + 1]
        chg = (seg[-1] / seg[0] - 1) * 100 if seg[0] else 0
        vmean = sum(segv) / len(segv)
        up_v = dn_v = 0.0
        up_n = dn_n = 0
        for j in range(1, len(seg)):
            d = seg[j] - seg[j - 1]
            if d > 0:
                up_v += segv[j]; up_n += 1
            elif d < 0:
                dn_v += segv[j]; dn_n += 1
        vp = (up_v / up_n) / (dn_v / dn_n) if up_n and dn_n and dn_v else None
        feats.append({
            'kind': 'UP' if chg > 0 else 'DOWN', 'a': a, 'b': b,
            'chg': round(chg, 1), 'days': b - a,
            'vol_ratio': round(vmean / avg_v, 2) if avg_v else None,
            'slope': round(chg / max(b - a, 1), 3),
            'vp': round(vp, 2) if vp else None,
            'end_shrink': vmean < 0.7 * avg_v,
        })
    return feats


def rolling_scores(closes, vols):
    """每日底部/顶部得分(0-4), 仅用截至当日数据。返回两个列表。"""
    n = len(closes)
    bot, top = [0] * n, [0] * n
    for t in range(120, n):
        c = closes[t]
        win = closes[max(0, t - 250):t + 1]
        wv = vols[max(0, t - 250):t + 1]
        avg_v = sum(wv) / len(wv)
        hi, lo = max(win), min(win)
        # ---- 底部得分 ----
        s = 0
        if hi and c / hi - 1 <= -0.30:  # 深跌背景
            s += 1
        # 下跌衰竭: 近一段zigzag的下跌波幅度递减
        seg = closes[max(0, t - 120):t + 1]
        pv = zigzag(seg, 0.10)
        downs = [(seg[pv[k + 1]] / seg[pv[k]] - 1) * 100 for k in range(len(pv) - 1)
                 if seg[pv[k + 1]] < seg[pv[k]]]
        if len(downs) >= 2 and downs[-1] > downs[-2]:  # 最近一跌比前一跌浅
            s += 1
        v20 = sum(vols[max(0, t - 20):t + 1]) / 20
        if avg_v and v20 / avg_v <= 0.7:  # 末端缩量
            s += 1
        # 双底: 近60日曾反弹>=8%后回踩不创新低
        w60 = closes[max(0, t - 60):t + 1]
        if len(w60) > 20:
            lo60_i = w60.index(min(w60))
            if lo60_i < len(w60) - 5:
                rebound = max(w60[lo60_i:]) / min(w60) - 1
                if rebound >= 0.08 and min(w60[lo60_i:]) > min(w60) * 1.005 and \
                   abs(c / min(w60) - 1) < 0.12:
                    s += 1
        bot[t] = s
        # ---- 顶部得分 ----
        u = 0
        lo120 = min(closes[max(0, t - 120):t + 1])
        if lo120 and c / lo120 - 1 >= 0.40:  # 距段起涨40%+
            u += 1
        if t >= 5 and closes[t - 5] and c / closes[t - 5] - 1 >= 0.12:  # 5日急涨12%+
            u += 1
        if wv and vols[t] >= max(wv[:-1]) and c / (closes[t - 1] or c) - 1 <= 0.01:  # 天量滞涨
            u += 1
        ma20 = sum(closes[t - 20:t + 1]) / 20
        if ma20 and c / ma20 - 1 >= 0.10:  # 大乖离
            u += 1
        top[t] = u
    return bot, top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--last', type=int, default=0, help='>0=只输出最近N日状态(融合模式)')
    ap.add_argument('--thresh', type=float, default=0.12)
    args = ap.parse_args()
    db = sqlite3.connect(QFQ, uri=True)
    rows = db.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date ASC",
        (args.code,)).fetchall()
    db.close()
    data = [(d, f3(c), f3(v)) for d, c, v in rows if f3(c) is not None]
    if len(data) < 260:
        print(f'[WAVE-FAIL] {args.code} 数据不足({len(data)})')
        return 2
    dates = [x[0] for x in data]; closes = [x[1] for x in data]; vols = [x[2] or 0 for x in data]
    avg_v = sum(vols) / len(vols)
    # 近三年窗口
    w = -750 if len(closes) > 750 else None
    closes3, dates3, vols3 = (closes[w:], dates[w:], vols[w:]) if w else (closes, dates, vols)
    pivots = zigzag(closes3, args.thresh)
    feats = wave_features(closes3, vols3, pivots, avg_v)
    bot, top = rolling_scores(closes3, vols3)
    name = get_name(args.code) or '?'
    if args.last:
        t = len(closes3) - 1
        verdict = []
        if bot[t] >= 3:
            verdict.append('🟢底部区域特征强(得分{}/4)'.format(bot[t]))
        if top[t] >= 3:
            verdict.append('🔴顶部区域特征强(得分{}/4)'.format(top[t]))
        if not verdict:
            verdict.append('⚪中段(底{}顶{})'.format(bot[t], top[t]))
        print(f'{args.code} {name} {dates3[t]} 收{closes3[t]:.2f} | ' + '；'.join(verdict))
        return 0
    # 验证模式: 全史波段表 + 得分峰值与真实高低点吻合检查
    print(f'{args.code} {name} 近三年波段结构(阈值{args.thresh:.0%}):')
    print(f'{"段":>3}{"起点":>10}{"终点":>10}{"起价":>8}{"终价":>8}{"涨跌":>8}{"历时":>5}{"量比":>6}{"量价":>6}{"形态"}')
    for k, f in enumerate(feats[-14:]):
        note = '末端缩量' if f['end_shrink'] else ''
        print(f'{k+1:>3}{dates3[f["a"]]:>10}{dates3[f["b"]]:>10}{closes3[f["a"]]:>8.2f}{closes3[f["b"]]:>8.2f}'
              f'{f["chg"]:>+7.1f}%{f["days"]:>4}日{f["vol_ratio"]:>6.2f}{(str(f["vp"])+"x") if f["vp"] else "-":>6} {note}')
    # 得分验证: 低点确认天然滞后(双底需低点后成形), 检验低点后0-25日峰值; 顶部同理前5后10
    lo_i = closes3.index(min(closes3)); hi_i = closes3.index(max(closes3))
    lo_win = bot[lo_i:min(lo_i + 26, len(bot))]
    hi_win = top[max(0, hi_i - 5):min(hi_i + 11, len(top))]
    lo_peak_day = dates3[lo_i + bot[lo_i:lo_i + 26].index(max(lo_win))] if lo_win else dates3[lo_i]
    print()
    print(f'验证: 三年低点 {dates3[lo_i]} -> 低点后0-25日 底部得分峰值={max(lo_win) if lo_win else 0}/4(峰现于{lo_peak_day})')
    print(f'      三年高点 {dates3[hi_i]} -> 高点前5后10日 顶部得分峰值={max(hi_win) if hi_win else 0}/4')
    print(f'      注: 顶部判别对急涨型V顶有效, 缓涨钝化顶(如长牛磨顶)为已知弱项')
    print(f'当前 {dates3[-1]}: 底部得分={bot[-1]}/4 顶部得分={top[-1]}/4')
    return 0


if __name__ == '__main__':
    sys.exit(main())
