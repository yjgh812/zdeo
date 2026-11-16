# -*- coding: utf-8 -*-
"""portrait_discipline_core_20261023.py — 画像研究纪律核心函数（12题验收 P1-P6 判定层闭环）

背景（2026-10-23 12题验收实测）:
  P1 龙虎榜游资样本池 / P2 配对对照 / P4 可成交性30%规则 / P5 leave-one-trader-out
  在系统内无实现；P3 系统 mde_rate 为单比例近似（偏乐观~0.5pp）；P6 无显式年份分段闸
  （同构纪律先例=2026-10-02 画像A/B首验 fwd10 反号→CLOSED，portrait_agent/README.md:60）。
  本件按主人验收套件最终版题干口径实现判定函数层，self-test 即验收。

口径来源:
  - 席位分类关键词对齐系统既有呈现层规则 lhb_seat_brief.py:71-80
    （机构专用/沪股通·深股通·香港/中小投资者散户集体席位剔除）
  - P3 两比例精确式（主人套件公式 n≈7.84×[p1(1-p1)+p0(1-p0)]/Δ²）
  - P5 默认留存阈值 0.5 = 单一游资贡献不得超过格子增量一半 —— 本件新设缺省参数，
    待主人裁决收紧/放宽
  - P6 "当前 regime 验证期增量转负→结构断裂→退回监控-only"；验证期不足同样拒绝
    （对齐"分段外推、2026 只验证一次"纪律）

边界:
  - 纯函数、零 I/O、零交易权、零生产接线；当前唯一消费方=本文件 self-test。
  - 生产画像管线（lhb_seat_detail→样本池→配对→接线闸）的消费接线属立项事项，
    本件不构成立项/启动。
"""
import math
import sys

# ---------------- P1 龙虎榜席位分类 + 游资净买入主样本池 ----------------

_INST_KW = "机构专用"
_NORTH_KWS = ("沪股通", "深股通", "香港")
_RETAIL_KW = "中小投资者"


def classify_seat(name):
    """席位分类。返回 institutional / northbound / retail_collective / trader。
    关键词与 lhb_seat_brief.py:71-80 系统既有规则一致。"""
    if _INST_KW in name:
        return "institutional"
    if any(k in name for k in _NORTH_KWS):
        return "northbound"
    if _RETAIL_KW in name:
        return "retail_collective"
    return "trader"


def hot_money_netbuy_pool(seats, exclude_two_way=True):
    """游资净买入主样本池。

    seats: [(name, side, buy_amt, sell_amt)]，side ∈ {'B','S'}（同席位同日买/卖各一条记录）。
    规则（主人套件 P1）:
      ① 机构专用/沪股通·深股通·香港专用 → 剔除（席位类型判定）；
      ② 中小投资者散户集体席位 → 剔除（非游资，系统既有口径）；
      ③ 同席位同日买卖双向（对倒/做T）→ 剔出主池并单独返回（敏感性分析入口）；
      ④ 其余净买入>0 → 进主样本池。
    返回 (pool, two_way_flagged):
      pool = [(name, net_buy)]；two_way_flagged = [name]（仅 exclude_two_way=True 时剔除）。
    """
    by_seat = {}
    for name, side, buy, sell in seats:
        d = by_seat.setdefault(name, {"buy": 0.0, "sell": 0.0})
        d["buy"] += buy or 0.0
        d["sell"] += sell or 0.0
    pool, flagged = [], []
    for name, d in by_seat.items():
        cls = classify_seat(name)
        if cls in ("institutional", "northbound", "retail_collective"):
            continue
        net = d["buy"] - d["sell"]
        two_way = d["buy"] > 0 and d["sell"] > 0
        if two_way and exclude_two_way:
            flagged.append(name)
            continue
        if net > 0:
            pool.append((name, net))
    return pool, flagged


# ---------------- P2 配对对照匹配 ----------------

def match_control(target, candidates):
    """事件级配对对照（主人套件 P2）。

    target/candidates: dict(day, industry, limit_up, shrink_pctile, has_trader)。
    硬约束: 同日 + 同行业 + 同涨停状态 + 无游资；软排序: 缩量分位差最小。
    返回最优对照 dict，无硬约束通过者返回 None。
    """
    hard = [c for c in candidates
            if c["day"] == target["day"]
            and c["industry"] == target["industry"]
            and c["limit_up"] == target["limit_up"]
            and not c["has_trader"]]
    if not hard:
        return None
    return min(hard, key=lambda c: abs(c["shrink_pctile"] - target["shrink_pctile"]))


# ---------------- P3 MDE 功效（两比例精确式） ----------------

_Z = 1.96 + 0.8416  # (z_{α/2=0.025} + z_{β=0.20})，平方≈7.84


def n_required_two_prop(p0, p1, delta, ):
    """主人套件公式: n ≈ 7.84×[p1(1-p1)+p0(1-p0)]/Δ²。p0/p1 为小数，delta 为小数。"""
    return _Z * _Z * (p1 * (1 - p1) + p0 * (1 - p0)) / delta ** 2


def mde_two_prop_closed(n, p0, p1):
    """套件给定闭式: MDE = sqrt(7.84×[p1(1-p1)+p0(1-p0)]/n)。
    注: 系统 dualkill mde_rate 旧口径=本式取 p1→p0（2·p0(1-p0) 方差近似），
    低基率+明显增量时系统性低估 MDE（偏乐观）。"""
    return math.sqrt(_Z * _Z * (p1 * (1 - p1) + p0 * (1 - p0)) / n)


def mde_two_prop(n, p0, iters=40):
    """严格迭代: p1 = p0 + MDE 自洽（两比例精确方差），返回小数。"""
    d = 0.05
    for _ in range(iters):
        p1 = p0 + d
        d = _Z * math.sqrt((p1 * (1 - p1) + p0 * (1 - p0)) / n)
    return d


def detectable(n, p0, delta):
    """n 样本能否检测到 delta 增量（p0 基率，均为小数）。"""
    return mde_two_prop(n, p0) < delta


# ---------------- P4 可成交性过滤 ----------------

def tradability_flag(target_amount, executable_amount, threshold=0.30):
    """可成交性判定（主人套件 P4）。

    占比 = 目标买入金额 / 开盘30分钟可成交金额；占比 > threshold → 不可实现
    （从主统计剔除，保留全样本做对照）。
    返回 (ratio, realizable)。
    """
    if executable_amount <= 0:
        return float("inf"), False
    ratio = target_amount / executable_amount
    return ratio, ratio <= threshold


# ---------------- P5 leave-one-trader-out 稳定性 ----------------

def loo_stable(baseline_uplift_pp, loo_uplifts_pp, min_retention=0.5):
    """LOO 群体泛化判定（主人套件 P5，含负值格镜像规则）。

    正格（baseline≥0）: 任一剔除后增量跌破 baseline×min_retention → 不稳定。
    负格（baseline<0, 影子基线实证存在）: 效应量=|baseline|；任一剔除后 |增量| 跌破
      |baseline|×min_retention（即最接近 0 的 loo 仍深于 baseline/2）→ 稳定，否则不稳定。
      语义: 负向效应（如"避开名单"价值）同样须为群体一致而非个体驱动。
    min_retention=0.5 缺省 = 单一游资贡献不得超过格子增量（含负向）的一半（待主人裁决）。
    返回 (stable: bool, extreme: 正格=剔除后最低增量 / 负格=剔除后最接近0的增量)。
    """
    if baseline_uplift_pp >= 0:
        floor = baseline_uplift_pp * min_retention
        extreme = min(loo_uplifts_pp)
        return extreme >= floor, extreme
    ceiling = baseline_uplift_pp * min_retention   # 负×0.5 = 向零收拢一半
    extreme = max(loo_uplifts_pp)
    return extreme <= ceiling, extreme


# ---------------- P6 时间外推与结构断裂闸 ----------------

def extrapolation_gate(val_increments_pp, require_strictly_positive=True, min_periods=2):
    """分段外推接线闸（主人套件 P6）。

    val_increments_pp: 验证期增量序列，按时间升序（如 [2025年+5, 2026年-1] → [5.0, -1.0]）。
    规则:
      ① 验证期数 < min_periods → 拒绝（当前 regime 未验证，"2026 只验证一次"纪律）；
      ② 最近验证期增量 ≤ 0 → 结构断裂，拒绝接线，退回监控-only；
      ③ require_strictly_positive 且任一验证期 ≤ 0 → 按 prereg 同号纪律拒绝
         （对齐 2026-10-02 画像A/B首验 fwd10 反号→CLOSED 先例）。
    返回 (allow: bool, reason: str)。allow=True 仅指"可进入接线评估"，
    仍须过 P3 功效 / P4 可成交 / P5 LOO 闸，不等于自动接线。
    """
    if len(val_increments_pp) < min_periods:
        return False, ("验证期不足(%d<%d): 当前 regime 未验证, 拒绝接线"
                       % (len(val_increments_pp), min_periods))
    recent = val_increments_pp[-1]
    if recent <= 0:
        return False, ("regime断裂: 最近验证期增量%+.1fpp转负, 该画像结构在当前regime不成立, "
                       "拒绝接线, 退回监控-only" % recent)
    if require_strictly_positive and any(v <= 0 for v in val_increments_pp):
        return False, "验证期增量非同号, 按prereg同号纪律拒绝接线"
    return True, "全部验证期增量为正, 可进入接线评估(仍须过功效/可成交/LOO闸)"


# ---------------- self-test：12 题标准输入逐条对照 ----------------

def _self_test():
    res = []

    def check(name, got, want):
        ok = got == want
        res.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got} want={want}")

    # ---- P1（套件输入: 甲机构/乙北向/丙游资/丁双向）----
    seats = [
        ("甲_机构专用", "B", 5000e4, 0.0),
        ("乙_沪股通专用", "B", 3000e4, 0.0),
        ("丙_华鑫上海宛平南路", "B", 2000e4, 0.0),
        ("丁_国泰君安成都北一环", "B", 1500e4, 0.0),
        ("丁_国泰君安成都北一环", "S", 0.0, 800e4),
    ]
    pool, flagged = hot_money_netbuy_pool(seats)
    check("P1主池(仅丙, 净买2000万)", pool, [("丙_华鑫上海宛平南路", 2000e4)])
    check("P1双向席位剔除标记(丁)", flagged, ["丁_国泰君安成都北一环"])
    check("P1散户集体席位剔除",
          classify_seat("中小投资者"), "retail_collective")

    # ---- P2（套件输入: 丙=电子/涨停/缩量20; A=涨停25; B=涨停80; C=未涨停20）----
    T = dict(day="D", industry="电子", limit_up=True, shrink_pctile=20, has_trader=True)
    A = dict(day="D", industry="电子", limit_up=True, shrink_pctile=25, has_trader=False)
    B = dict(day="D", industry="电子", limit_up=True, shrink_pctile=80, has_trader=False)
    C = dict(day="D", industry="电子", limit_up=False, shrink_pctile=20, has_trader=False)
    check("P2选A(同涨停+缩量最近)", match_control(T, [A, B, C]) is A, True)
    check("P2去A后退化为B(硬约束内最近)", match_control(T, [B, C]) is B, True)
    check("P2仅C→无硬约束通过返回None", match_control(T, [C]) is None, True)

    # ---- P3（套件输入: p0=5% p1=8% Δ=3pp）----
    n = n_required_two_prop(0.05, 0.08, 0.03)
    check("P3所需n≈1055(容差±5)", 1050 <= round(n) <= 1060, True)
    check("P3 n=2000能检测3pp", detectable(2000, 0.05, 0.03), True)
    m500 = mde_two_prop(500, 0.05)
    check("P3 n=500 MDE迭代≈4.59pp(标准4.4–4.6)", round(m500 * 100, 2), 4.59)
    check("P3 n=500 MDE闭式≈4.36pp(套件公式口径, p1=8%)",
          round(mde_two_prop_closed(500, 0.05, 0.08) * 100, 2), 4.36)
    check("P3 系统旧口径复现(闭式取p1→p0)=3.86pp, 偏乐观根源在案",
          round(mde_two_prop_closed(500, 0.05, 0.05) * 100, 2), 3.86)
    check("P3 n=500检测不到3pp", detectable(500, 0.05, 0.03), False)

    # ---- P4（套件输入: 50万/120万）----
    ratio, ok = tradability_flag(50e4, 120e4)
    check("P4占比=41.7%", round(ratio, 4), 0.4167)
    check("P4标记不可实现", ok, False)

    # ---- P5（套件输入: 基线+8pp, LOO +7/+6/+2）----
    check("P5不稳定(剔除丙剩+2<基线一半)",
          loo_stable(8.0, [7.0, 6.0, 2.0]), (False, 2.0))
    check("P5稳定对照(最低+6≥4)",
          loo_stable(8.0, [7.0, 6.5, 6.0]), (True, 6.0))
    # P5 负值格镜像（影子基线实证: 负向效应同样须群体一致）
    check("P5负格不稳定(剔除后-2, 效应量2<|−8|一半)",
          loo_stable(-8.0, [-7.0, -6.0, -2.0]), (False, -2.0))
    check("P5负格稳定(最接近0的-6仍深于-4)",
          loo_stable(-8.0, [-7.0, -6.5, -6.0]), (True, -6.0))

    # ---- P6（套件输入: 2025+5pp, 2026-1pp）----
    ok6, why6 = extrapolation_gate([5.0, -1.0])
    check("P6拒绝接线", ok6, False)
    check("P6结论=regime断裂退监控-only", "regime断裂" in why6 and "监控-only" in why6, True)
    check("P6验证期不足同样拒绝",
          extrapolation_gate([5.0])[0], False)
    check("P6两期全正→进入接线评估",
          extrapolation_gate([5.0, 3.0])[0], True)

    print()
    print(f"SELF-TEST: {'ALL PASS' if all(res) else 'FAILED'} ({sum(res)}/{len(res)})")
    return 0 if all(res) else 1


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_self_test())
