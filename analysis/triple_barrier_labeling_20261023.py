# -*- coding: utf-8 -*-
"""triple_barrier_labeling_20261023.py — 三重障碍收盘标签器 + AFML 平均唯一性权重（12题验收 D4/D5 缺口落地）

背景（2026-10-23 12题验收实测）:
  D4 三重障碍标签 / D5 唯一性权重在系统内无实现（analysis/addposition_plan_audit5_20261020.md:15-16
  已登记 RiskLabAI.get_av_uniqueness_from_triple_barrier 不存在、须自实现）。本件按主人验收套件
  最终版题干口径自实现，作为两题的闭环与未来标签管线的可复用核心函数。

口径（主人 12 题验收套件最终版）:
  D4: 入场价×乘数定上下障碍（本题 1.05/0.92，0.92 与加仓线"成本×(1-8%)"一致），
      收盘价判定，逐日先触先判，时间障碍(20日)到期未触=0。收盘为单值，
      同日收盘不可能同时越上又破下，无歧义序问题。
  D5: 平均唯一性 = mean(1/并发数, t∈自身标签窗口)，并发数由窗口集合推导。

边界:
  - 纯研究函数，零交易权，零生产接线；当前唯一消费方=本文件 self-test（12题 D4/D5 验收）。
  - 未来消费方=加仓 Week1+ 标签管线——按 addpos_week0_design_20261020.md 该线未获裁决不启动，
    本件不构成启动。
  - 盘中(high/low)判定变体不在本件（addpos_week0 模拟器为 low 触发近似口径，两者差异已在
    12题验收报告 D4 口径前提中说明，统一口径待裁决）。
"""
import sys
from collections import Counter


def triple_barrier_label(closes, entry_price, up_mult=1.05, down_mult=0.92, horizon=20):
    """收盘价判定三重障碍标签。

    closes: 入场后逐日收盘序列 [D1, D2, ...]（时间升序）
    entry_price: 入场价，障碍基准（上=×up_mult，下=×down_mult）
    horizon: 时间障碍（交易日数）
    返回 (label, touch_day): label ∈ {+1, -1, 0}；
        touch_day = 触达日(1-based D#)；时间到期=horizon；序列不足=序列长度。
    """
    up = entry_price * up_mult
    dn = entry_price * down_mult
    seq = list(closes)[:horizon]
    for i, c in enumerate(seq, start=1):
        if c >= up:
            return +1, i
        if c <= dn:
            return -1, i
    return 0, min(horizon, len(seq))


def concurrency_by_day(windows):
    """各时点并发数：当日被多少个标签窗口覆盖。

    windows: [(start_day, end_day)] 闭区间，同一 1-based 坐标系（如全局交易日序号）。
    """
    cnt = Counter()
    for s, e in windows:
        for t in range(s, e + 1):
            cnt[t] += 1
    return cnt


def avg_uniqueness(windows):
    """AFML 平均唯一性（每样本）。

    windows: [(start_day, end_day)] 闭区间标签窗口
    返回 [round(mean(1/并发数 over 自身窗口), 3), ...]，顺序与输入一致。
    """
    cnt = concurrency_by_day(windows)
    out = []
    for s, e in windows:
        days = list(range(s, e + 1))
        out.append(round(sum(1.0 / cnt[t] for t in days) / len(days), 3))
    return out


def _self_test():
    """12题验收 D4/D5 标准答案对照（全部断言精确值，任一失败 exit 1）。"""
    res = []

    def check(name, got, want):
        ok = got == want
        res.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got} want={want}")

    # ---- D4 三重障碍（主人套件标准输入：入场10.00 上10.50 下9.20 时20日 收盘判定）----
    check("D4路径A(收盘9.15<9.20先触下→-1@D4)",
          triple_barrier_label([10.20, 9.80, 9.30, 9.15], 10.00), (-1, 4))
    check("D4路径B(收盘10.55>10.50先触上→+1@D2)",
          triple_barrier_label([10.30, 10.55], 10.00), (+1, 2))
    check("D4时间障碍(20日全程带内→0@20)",
          triple_barrier_label([10.05, 10.10, 10.00, 9.95] * 5, 10.00), (0, 20))
    check("D4边界(收盘恰=上障碍10.50计触达)",
          triple_barrier_label([10.49, 10.50], 10.00), (+1, 2))
    check("D4首日即越上",
          triple_barrier_label([10.51], 10.00), (+1, 1))

    # ---- D5 平均唯一性（主人套件标准输入：A=t1-t3 B=t2-t4 C=t3-t5）----
    w = [(1, 3), (2, 4), (3, 5)]
    cnt = concurrency_by_day(w)
    check("D5并发数推导(应与题干给定 t1..t5=1,2,3,2,1 一致)",
          [cnt[t] for t in range(1, 6)], [1, 2, 3, 2, 1])
    check("D5平均唯一性 A/B/C(手算 0.611/0.444/0.611)",
          avg_uniqueness(w), [0.611, 0.444, 0.611])

    print()
    print(f"SELF-TEST: {'ALL PASS' if all(res) else 'FAILED'} ({sum(res)}/{len(res)})")
    return 0 if all(res) else 1


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_self_test())
