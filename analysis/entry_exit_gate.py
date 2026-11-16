# -*- coding: utf-8 -*-
"""entry_exit_gate.py — 买卖点判定层 v1 (SOP二百三十五铁律: 选股→买卖点判定→区域买入)。
三要素: 信号当天可识别 + 确认条件 + 失效位。无买点=WAIT不追。事后最低价禁止作为买点依据。
数据源: chanlun_engine(qfq口径, data_loader已根修)。用法:
  python analysis/entry_exit_gate.py --code 002003        # 判定单只
  python analysis/entry_exit_gate.py --pool               # 查等待池
  python analysis/entry_exit_gate.py --code X --add       # 判定并加入等待池
  python analysis/entry_exit_gate.py --code X --remove    # 出池作废
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

POOL_PATH = os.path.join(ROOT, "data", "entry_wait_pool.json")
POOL_MAX_DAYS = 10  # 等待超期(交易日)作废
STALE_PCT = 0.15    # 信号价偏离现价15%以上判过期


def load_structure(code: str) -> dict:
    """缠论结构供数: 中枢zg/zd + 日线信号 + 位置 + 均线。"""
    from chanlun_engine.signal import scan_signal
    from chanlun_engine.data_loader import get_daily_kline
    import pandas as pd

    sig = scan_signal(code)
    k = get_daily_kline(code, start="20260101")
    if k is None or k.empty:
        raise ValueError(f"{code} 无K线数据")
    close = k["close"].astype(float)
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1])
    price = float(close.iloc[-1])
    last_date = str(k["date"].iloc[-1])
    low20 = float(k["low"].astype(float).tail(10).min())
    high20 = float(k["high"].astype(float).tail(10).max())
    pivot = (sig.pivot_info or {}) if sig else {}
    zg = pivot.get("zg")
    zd = pivot.get("zd")
    sig_type = sig.signal_type.value if sig else "none"
    stale, stale_reason = check_stale(sig_type, sig.price if sig else 0, price,
                                      details=(sig.details if sig else ""))
    pos = "unknown"
    if zg and zd:
        pos = "above" if price > zg else ("below" if price < zd else "inside")
    return dict(code=code, asof=last_date, price=round(price, 2),
                ma5=round(ma5, 2), ma20=round(ma20, 2),
                low10=low20, high10=high20,
                pivot_zg=zg, pivot_zd=zd, pos_vs_pivot=pos,
                chan_signal=dict(type=sig_type,
                                 price=round(sig.price, 2) if sig else None,
                                 conf=round(sig.confidence, 2) if sig else 0,
                                 details=sig.details if sig else "",
                                 stale=stale, stale_reason=stale_reason))


def check_stale(sig_type: str, sig_price: float, price: float, details: str = "") -> tuple:
    """信号时效过滤: ①事件价偏离现价超15%判过期; ②看跌信号遇价站上中枢上沿=已被走势否定。"""
    if sig_type == "none" or not sig_price:
        return True, "无有效信号"
    import re
    m = re.search(r"(?:反弹|回踩|回撤|突破|跌破)([0-9]+\.?[0-9]*)", details or "")
    ev_price = float(m.group(1)) if m else sig_price
    dev = abs(price / ev_price - 1)
    if dev > STALE_PCT:
        return True, f"信号事件价{ev_price}偏离现价{price}达{dev:.0%}, 判过期重判"
    return False, ""


def make_verdict(s: dict) -> dict:
    """判定规则 v1.1 (可解释四档): BUY_ZONE三买区 / CHASE_OK主升后段实验可追 / WAIT中枢内 / NO_TRADE破位不接。
    CHASE_OK=主人令20261103[主升后段暂定可追, 模拟盘实验后再定拦截]: 放行+强制标注实验属性, 攒样本复盘胜率。"""
    price = s["price"]
    zg, zd = s["pivot_zg"], s["pivot_zd"]
    pos = s["pos_vs_pivot"]
    sig = s["chan_signal"]
    stop = zd if zd else round(price * 0.92, 2)
    v = dict(action="WAIT", buy_zone=None, stop_loss=stop, note="", experiment="")
    # 冲突检测: 看跌信号遇价站上中枢上沿 = 已被走势否定
    if sig["type"].startswith("sell") and pos == "above":
        sig["stale"] = True
        sig["stale_reason"] = (sig["stale_reason"] or "") + \
            f"; {sig['type']}看跌信号遇价{price}站回中枢上沿{zg}上方, 已被走势否定"
    above_dev = (price / zg - 1) if zg else 9.9
    if pos == "below":
        v["action"] = "NO_TRADE"
        v["note"] = f"价{price}在中枢下沿{zd}下方(三卖区), 不接飞刀; 底分型+反包站上MA5企稳确认后才转BUY"
    elif pos == "inside":
        v["action"] = "WAIT"
        v["buy_zone"] = [round(zd * 1.01, 2), round(zd * 1.05, 2)]
        v["note"] = f"中枢内震荡{zd}~{zg}, 只在回踩下沿{v['buy_zone']}接, 突破zg{zg}放量才算三买启动"
    else:  # above
        if above_dev <= 0.20:
            v["action"] = "BUY_ZONE"
            v["buy_zone"] = [round(zg, 2), round(zg * 1.03, 2)]
            v["stop_loss"] = zd
            v["note"] = f"三买区: 价{price}站上中枢上沿{zg}未远, 回踩{v['buy_zone']}区域买, 失效=跌破zd{zd}"
        else:
            v["action"] = "CHASE_OK"
            v["experiment"] = "chase"
            v["buy_zone"] = None
            v["chase_limit"] = round(price * 1.005, 2)
            v["note"] = (f"【实验追买】主升后段: 价{price}距中枢上沿{zg}已+{above_dev:.0%}, "
                         f"主人令暂定可追(模拟盘攒样本后再定拦截去留); 追买限价{v['chase_limit']}, "
                         f"失效=跌破{zd}; 每次选股须带此标注")
    return v


def pool_load() -> list:
    if os.path.exists(POOL_PATH):
        return json.load(open(POOL_PATH, encoding="utf-8"))
    return []


def pool_save(items: list):
    json.dump(items, open(POOL_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pool_add(entry: dict):
    items = [x for x in pool_load() if x.get("code") != entry["code"]]
    items.append(entry)
    pool_save(items)
    print(f"[POOL] 加入等待池: {entry['code']} {entry.get('name','')} "
          f"买区{entry.get('buy_zone')} 失效{entry.get('stop_loss')}")


def pool_remove(code: str):
    items = [x for x in pool_load() if x.get("code") != code]
    pool_save(items)
    print(f"[POOL] 出池作废: {code}")


def pool_show():
    items = pool_load()
    if not items:
        print("[POOL] 等待池为空")
        return
    print(f"[POOL] 等待池 {len(items)} 只:")
    for x in items:
        print(f"  {x['code']} {x.get('name','')} | 买区{x.get('buy_zone')} "
              f"| 失效{x.get('stop_loss')} | 加入{x.get('added','')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code")
    ap.add_argument("--name", default="")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--pool", action="store_true")
    a = ap.parse_args()
    if a.pool:
        pool_show()
        return
    if a.remove and a.code:
        pool_remove(a.code)
        return
    if not a.code:
        print("用法: --code X 判定 | --add 入池 | --remove 出池 | --pool 查池")
        return
    s = load_structure(a.code)
    s["name"] = a.name
    v = make_verdict(s)
    out = dict(code=s["code"], name=s["name"], asof=s["asof"], price=s["price"],
               structure={k: s[k] for k in ("pivot_zg", "pivot_zd", "pos_vs_pivot", "ma5", "ma20")},
               chan_signal=s["chan_signal"], verdict=v,
               generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.add:
        if v["action"] in ("BUY_ZONE", "WAIT", "CHASE_OK"):
            pool_add(dict(code=s["code"], name=s["name"], buy_zone=v["buy_zone"] or f"追买限价{v.get('chase_limit')}",
                          stop_loss=v["stop_loss"], added=datetime.now().strftime("%Y-%m-%d"),
                          verdict=v["action"], experiment=v.get("experiment", "")))


if __name__ == "__main__":
    main()
