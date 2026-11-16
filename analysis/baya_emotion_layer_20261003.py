# -*- coding: utf-8 -*-
"""T2: baya 形态验证 × 情绪周期分层统计（PD-030③, 就绪待 10/9 首验数据后运行）
输入: data/baya_pattern_scan_latest.json(信号) + attribution.trade_records(BUY fills, 10/9后)
分层: 赚钱效应档(PD-025口径) 涨停>=61强 / 40-61温 / <40弱 (westock data_changedist 或 hithink limit_up_pool)
判据(预钉死): 共振组期望差异是否由强档独占; 弱档若同样正→分层无意义; 弱档负→情绪闸必要。
运行: python analysis/baya_emotion_layer_20261003.py [--asof YYYYMMDD]
状态: 脚手架, 10/9 前无可回放成交, 运行会输出 NO_FILLS。
"""
import argparse
import json
import os

PATHS = {
    "scan": r"D:\zcodesj\data\baya_pattern_scan_latest.json",
    "fills_hint": r"D:\zcodesj\data\open_orders.jsonl",
}


def load_scan():
    p = PATHS["scan"]
    if not os.path.exists(p):
        return []
    j = json.load(open(p, encoding="utf-8"))
    if isinstance(j, dict):
        return j.get("candidates") or j.get("records") or []
    return j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="")
    a = ap.parse_args()
    cands = load_scan()
    print(f"baya 信号: {len(cands)} 只")
    # 成交回放: 10/9 后由 verification_trades / westock fills 提供, 当前占位
    fills = []
    print(f"可回放成交: {len(fills)} 笔"
          + ("(NO_FILLS: 10/9 首验前正常)" if not fills else ""))
    if not fills:
        print("下一步: 10/9 收盘后运行, 拉取当日成交 → 按 SignalDate 匹配赚钱效应档 → 输出分层 fwd 统计")
        return
    # 分层统计(成交到位后启用):
    # for 档 in [强,温,弱]:
    #   子集 = fills[情绪档]; 输出 n/中位fwd/胜率/启动占比
    # 判据见文件头。


if __name__ == "__main__":
    main()
