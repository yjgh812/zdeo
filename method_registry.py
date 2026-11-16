#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方法名权威对照表（单一真源, 2026-11-12 建立）

起因: 新能股份溯源事故（主人令「修复优化迭代, 再不要搞错」）——同一方法三个名字漂浮
（臂代号 dip_fb_v1 / 实验标签"底部反转首板" / 正式名「探底修复 v1」），成交台账无选股
来源字段, 溯源只能考古。本表钉死 代号 ↔ 正式名 ↔ 状态，全系统统一引用。

规则:
  - 名称只取系统既有定义（四法定义/计划注记原文）或主人定谳, 禁止新造语义;
  - 新方法上线必须先入本表再允许出买入信号（防再生别名漂浮）;
  - 修改本表须主人裁决留痕。
"""
import json

# 四法对照实验臂（名称来源: 主人定谳 + bt_full_2016_2026.py 四法定义 + 周一12只计划注记）
REGISTRY = {
    "dip_fb_v1": {
        "name": "探底修复 v1",
        "aliases": ["底部反转首板", "底反首板"],
        "family": "四法对照实验臂",
        "verified": True,
        "verified_by": "主人定谳 2026-11-12（新能股份溯源事故定谳令）",
        "evidence": "主人令原文「dip_fb_v1『探底修复 v1』选的——机器方法自动选票」",
    },
    "dip_up_v1": {
        "name": "起涨型 v1",
        "aliases": ["起涨型严格池", "起涨型黄金带", "起涨型真超跌", "起涨型次档"],
        "family": "四法对照实验臂",
        "verified": True,
        "verified_by": "主人授权代定名 2026-11-12（名称取自四法定义与计划注记原文）",
        "evidence": "monday_buy12_plan_20261107.py 注记「起涨型XX池/档」+ bt_full 四法定义「起涨型」",
    },
    "dip_ret_v1": {
        "name": "回踩确认 v1",
        "aliases": ["回踩确认型"],
        "family": "四法对照实验臂",
        "verified": True,
        "verified_by": "主人授权代定名 2026-11-12（名称取自四法定义与计划注记原文）",
        "evidence": "monday_buy12_plan_20261107.py 注记「回踩确认型」+ bt_full 四法定义「回踩确认」",
    },
    "dip_cons_v1": {
        "name": "横盘介入 v1",
        "aliases": [],
        "family": "四法对照实验臂",
        "verified": True,
        "verified_by": "主人授权代定名 2026-11-12（名称取自四法定义）",
        "evidence": "bt_full 四法定义「横盘介入」; 周一12只计划无本臂样本（预留臂）",
    },
}


def get_method_name(method_id: str) -> str:
    """方法 id -> 正式名; 未知返回空串(调用方如实标未知, 禁止编名)。"""
    e = REGISTRY.get(str(method_id or "").strip())
    return e["name"] if e else ""


def is_known_method(method_id: str) -> bool:
    return str(method_id or "").strip() in REGISTRY


def dump() -> str:
    return json.dumps(REGISTRY, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    print(dump())
