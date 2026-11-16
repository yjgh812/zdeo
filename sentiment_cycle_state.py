#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
sentiment_cycle_state.py — A股情绪周期状态机（影子层，v1 启发式，未经回测标定）

定位（主人 2026-09-09 裁决："消息面融入选股"Phase 1 影子件）：
  每日对市场情绪做结构化打标（冰点/修复/分歧/狂热/退潮/unknown），
  仅落台账、只读展示，不进信号、不拦交易、不挂 cron。放行须走 prereg→影子→三闸。

数据流（agent 喂料合同，照 news_pit_archive.py 模式）：
  agent 从 MCP（hithink 涨停池/炸板池/连板梯队，或 westock 涨跌分布）取当日实况
  -> 写临时 JSON -> python sentiment_cycle_state.py --input <tmp.json>
  -> 台账 data/sentiment_cycle_daily.jsonl（按 date UPSERT，重跑幂等）

输入 JSON 字段（允许部分缺失，缺失→对应指标 null，阶段可能判 unknown）：
  date              交易日 YYYY-MM-DD（必需）
  limit_up_count    涨停家数
  limit_break_count 炸板家数（涨停后开板未回封，hithink limit_break_pool total）
  max_board         当日最高连板数
  prev_max_board    上一交易日最高连板数
  promoted_count    昨日二板及以上今日晋级数（hithink ladder seal_nextday=true 计数）
  prev_board_count  昨日二板及以上家数
  down_limit_count  跌停家数（可选）
  amount            两市成交额（元，可选）
  source            数据来源标注（必需，反溯源）

判定规则 v1（外部方法论来源：渤海证券8/19情绪监测研报四阶段 + 东财社区8/13
情绪周期三指标「连板高度/炸板率>30%分歧、<15%稳定/主线辨识度」；阈值未回测标定，
只作影子观察，禁止作为交易依据）：
  冰点  = 涨停<30 且 最高板<=2
  狂热  = 涨停>=80 且 最高板>=5 且 炸板率<15%
  退潮  = 最高板较昨日回落>=2 级，或 (炸板率>30% 且 最高板<=3)
  分歧  = 炸板率>30%（且不满足退潮）
  修复  = 其余（涨停>=30）
  unknown = 关键指标缺失

⛔ 停用标记（2026-09-20 主人裁决 PD-03 选 C 停用，升级替代同日早间的降级标记）：
  设施整体停用——本脚本与喂料链不再运行，台账 data/sentiment_cycle_daily.jsonl 只读封存。
  证据链（falsified 第63条 SENTIMENT-CYCLE-V1）：全史重放 2016-2026（n=2604）三大主力
  stage 差异<0.5pp 且方向与语义相反（"退潮"后 ex10 +0.37pp 为正），分布病态（狂热 11 年
  仅 2 天），与 P-B1 拐点日证伪（n=335，拐点后 fwd10 +0.86pp 反向）双路独立同向。
  裁决材料：reports/sentiment_cycle_verdict_material_20260920.md。
  替代覆盖：regime/情绪职能=MARKETSTATE_2000（日更）；涨停生态原始数据=limit_up_patterns 库。
  复活条件：仅允许以可回测新判据走 prereg→影子→三闸重设计（第三期自省 P0 排程
  "情绪周期判据定型"），禁止复活 v1 阈值（含降级后的纯展示——展示误导性标签同样有害）。

🔴 前序降级标记留档（2026-09-20 主人授权自行进化，PENDING_DECISIONS 项⑤收口，已被上方停用裁决取代）：
  stage=纯展示标签，立项复核通过前【不作任何决策输入】（含过滤/择时/仓位/呈报结论）。
  依据两件：①9/9 falsified 第42条 SENTIMENT-CYCLE-DAILY-ALPHA（日频择时判死）；
  ②2026-09-19 B1 实证（reports/swing_p_recon_20260919/）：「连板降+炸板升」拐点后
  fwd10 +0.86pp 方向反向——本机"退潮"判定内嵌该启发式，转弱语义不成立。
  消费面核查（20260920）：直接读者仅 sentiment 研究族（eval/calib/backfill/shadow_daily）；
  preflight/作战清单/vibe_card 用的是 sentiment_composite(M-6 已标定) 非本台账。

退出码：0 成功；2 输入缺必需字段；3 输入解析失败
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
LEDGER = ROOT / "data" / "sentiment_cycle_daily.jsonl"
REQUIRED = ("date", "source")

BREAK_RATE_DIV_THRESH = 0.30   # 炸板率分歧阈值（东财社区口径）
BREAK_RATE_CALM_THRESH = 0.15  # 炸板率稳定阈值


def compute_metrics(data: dict) -> dict:
    m = {}
    lu = data.get("limit_up_count")
    lb = data.get("limit_break_count")
    m["limit_up_count"] = lu
    m["limit_break_count"] = lb
    m["max_board"] = data.get("max_board")
    m["prev_max_board"] = data.get("prev_max_board")
    m["down_limit_count"] = data.get("down_limit_count")
    m["amount"] = data.get("amount")
    if isinstance(lu, int) and isinstance(lb, int) and (lu + lb) > 0:
        m["break_rate"] = round(lb / (lu + lb), 4)
    else:
        m["break_rate"] = None
    pc, pb = data.get("promoted_count"), data.get("prev_board_count")
    if isinstance(pc, int) and isinstance(pb, int) and pb > 0:
        m["promotion_rate"] = round(pc / pb, 4)
    else:
        m["promotion_rate"] = None
    return m


def classify(metrics: dict) -> tuple[str, list]:
    """返回 (阶段, 证据列表)。规则顺序：极端态优先，未知保底。"""
    ev = []
    lu = metrics.get("limit_up_count")
    mb = metrics.get("max_board")
    pmb = metrics.get("prev_max_board")
    br = metrics.get("break_rate")

    if lu is not None and mb is not None and lu < 30 and mb <= 2:
        ev.append(f"涨停{lu}<30 且最高板{mb}<=2")
        return "冰点", ev
    if lu is not None and mb is not None and br is not None \
            and lu >= 80 and mb >= 5 and br < BREAK_RATE_CALM_THRESH:
        ev.append(f"涨停{lu}>=80 且最高板{mb}>=5 且炸板率{br:.0%}<15%")
        return "狂热", ev
    if mb is not None and pmb is not None and (pmb - mb) >= 2:
        ev.append(f"最高板{pmb}→{mb}回落{pmb - mb}级")
        return "退潮", ev
    if br is not None and br > BREAK_RATE_DIV_THRESH and (mb is None or mb <= 3):
        ev.append(f"炸板率{br:.0%}>30% 且最高板{mb}<=3")
        return "退潮", ev
    if br is not None and br > BREAK_RATE_DIV_THRESH:
        ev.append(f"炸板率{br:.0%}>30%")
        return "分歧", ev
    if None not in (lu, mb) and lu >= 30:
        ev.append(f"涨停{lu}>=30 且最高板{mb}，无极端信号")
        return "修复", ev
    ev.append("关键指标缺失（涨停家数/最高板/炸板率不全）")
    return "unknown", ev


def upsert_ledger(record: dict) -> int:
    """按 date UPSERT 整文件重写（原子写），返回台账总行数。"""
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"[WARN] 台账坏行跳过: {line[:80]}", file=sys.stderr)
    rows = [r for r in rows if r.get("date") != record["date"]]
    rows.append(record)
    rows.sort(key=lambda r: r.get("date", ""))
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(LEDGER)
    return len(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="情绪周期状态机（影子层）")
    p.add_argument("--input", required=True, help="agent 生成的当日情绪实况 JSON 文件")
    p.add_argument("--dry-run", action="store_true", help="只打印不落台账")
    args = p.parse_args()

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[FAIL] 输入读取/解析失败: {e}", file=sys.stderr)
        return 3
    if not isinstance(data, dict):
        print("[FAIL] 输入必须是 JSON 对象", file=sys.stderr)
        return 3
    missing = [f for f in REQUIRED if not data.get(f)]
    if missing:
        print(f"[FAIL] 缺必需字段: {missing}", file=sys.stderr)
        return 2

    metrics = compute_metrics(data)
    stage, evidence = classify(metrics)
    record = {
        "date": data["date"],
        "source": data["source"],
        "stage": stage,
        "stage_usage": "display_only_not_decision_input",
        "evidence": evidence,
        "metrics": metrics,
        "schema_version": "sentiment_cycle_v1_heuristic_unbacktested",
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    print("=" * 60)
    print(f"🌡️ 情绪周期打标（影子） {data['date']}  源:{data['source']}")
    print(f"   指标: 涨停{metrics['limit_up_count']} 炸板{metrics['limit_break_count']}"
          f" 炸板率{metrics['break_rate'] if metrics['break_rate'] is not None else 'N/A'}"
          f" 最高板{metrics['max_board']}(昨{metrics['prev_max_board']})"
          f" 晋级率{metrics['promotion_rate'] if metrics['promotion_rate'] is not None else 'N/A'}")
    print(f"   判定: 【{stage}】")
    for e in evidence:
        print(f"   依据: {e}")
    print("   ⚠️ v1 启发式未经回测标定，仅影子观察；stage=纯展示标签，不作任何决策输入")
    print("      （B1反向证据20260919：连板降+炸板升拐点后fwd10+0.86pp，退潮≠转弱判据）")
    print("=" * 60)

    if args.dry_run:
        print("[dry-run] 未落台账")
        return 0
    total = upsert_ledger(record)
    print(f"[OK] 台账 {LEDGER} 共 {total} 行（按 date UPSERT）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
