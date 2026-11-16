#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙端到端交易执行器（端对端接通核心，2026-08-13 建）。

主人 8/13 指示：样本积累、归因库需要端对端接通，运用到实际工作。本执行器把
"信号 → 闸门 → 下单 → 双库记录 → 前瞻跟踪 → 归因反馈"整合为一条命令，消灭
此前"precheck 与下单分离、记录靠手动补"的断点。

数据流（买入一条命令全走完）：
  westock查持仓/资产/行情 → pre_trade_check_v2 全量闸门(R1-R13) → REJECT终止
    → PASS → westock下单 → 成交验证 → trade_state.record_buy
    → 双库记录(attribution.trade_records + dragon_quant.buy_tracker)
    → feed_feedback sync → verification_trades 前瞻跟踪

安全分级：
  买入：默认 dry-run（只判定+给建议），--execute 真下单（过闸门即买，模拟盘验证导向）
  卖出：默认 dry-run 永不真卖；--execute --confirmed 才真卖（卖出三铁律）；
        -8% 硬止损用 --execute --is-stop-loss 可豁免尾盘窗口与确认（趋势走坏+硬底线）

用法：
  # 买入（dry-run，先看闸门判定与建议仓位）
  python trade_executor.py buy --code 002414 --name 高德红外 --source mainline_radar --sector 军工 --amount 50000
  # 买入（真下单，端到端）
  python trade_executor.py buy --code 002414 --name 高德红外 --source mainline_radar --sector 军工 --amount 50000 --execute

  # 卖出（dry-run 预览）
  python trade_executor.py sell --code 600726 --name 华电能源 --reason 趋势走坏
  # 卖出（真卖，需主人确认）
  python trade_executor.py sell --code 600726 --name 华电能源 --reason 趋势走坏 --execute --confirmed
  # -8% 硬止损（自动，豁免窗口）
  python trade_executor.py sell --code xxx --name XXX --reason 止损 --execute --is-stop-loss
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:/workbuddyspace")
sys.path.insert(0, r"D:/zcodesj")

from westock_client import WeStockClient  # noqa: E402
from trade_state import record_buy, record_sell, load_state  # noqa: E402

# 2026-08-29 P0-A(胜率计划 WINRATE_UPGRADE_PLAN_20260829.md): 生产买入 source 白名单——
# 复用 _common 源分级唯一真源(PROVEN=核心/EXPERIMENTAL=灰度), RETIRED(smc/hotspot等)/空/未登记一律拒。
# 依据: 8/11-28 实盘名单外买入 113/170 笔(66%) D+3 均值 -2.63%, 绕闸 20 笔超额 -4.19pp;
# manual_confirmed 属 PROVEN 但仅限主人逐笔特批(buy-batch 已单独拦截批量进入)。
from _common import (  # noqa: E402
    PROVEN_EDGE_SOURCES, EXPERIMENTAL_SOURCES, OBSERVE_SOURCES,
    METHOD_ADMISSION_INTERIM_SOURCES,  # 2026-11-03 暂行方法准入(主人令七领跑组)
    get_backtest_no_edge_sources,  # 2026-11-05 两级制第一级: 回测预筛黑名单(主人令批准)
    get_paper_observed_sources,    # 2026-11-07 模拟盘实盘观察无优势源(实盘证据, 判伪权威级材料)
)
# 2026-10-31 赛马观察名单接入: OBSERVE=降风险停止新开仓(可逆), 与 RETIRED(不可逆禁复活)双轨
# 2026-11-03 暂行方法准入(主人令「七个领跑还是模拟仓」): 仅七数据领跑组+主人特批通道可真实
# (模拟盘)开仓; 其余已登记源一律影子观察(P0-A 拒单, 信号照跑照记)。复审点=11/26 赛马首考
# 或主人令调整; 撤销=恢复下行原式即可。前式=PROVEN∪EXPERIMENTAL-OBSERVE(档案保留只读)。
_BUY_SRC_ALLOWED = METHOD_ADMISSION_INTERIM_SOURCES | {"manual_confirmed"}
# 2026-11-05 两级制第一级(主人令批准「回测预筛+模拟盘判伪」): 回测全历史明显无优势的源
# 不进模拟盘赛道——buy源闸第一道拒绝(在暂行准入之前), 恢复须主人 --owner-approve-prescreen
# 或新证据改档(data/falsified_rules.json backtest_no_edge_observations区, 单一真源)。
_BACKTEST_NO_EDGE_SOURCES = get_backtest_no_edge_sources()
# 2026-11-07 模拟盘实盘观察无优势源（主人令 B+A 并行「先停确定无效的, 让有效候选继续攒样本」）。
# 证据=真实建仓 × qfq 实际走势（模拟盘实盘, 非回测）：method_picks 21笔T+5 -5.04%/胜率9.5%；
# manual_confirmed 29笔T+5 -6.56%/胜率10.3%（T+20 -12.86%/胜率10.0%）。两者按买入位置拆任何位置均亏
# → 问题在源不在位置。T+20 满窗样本不足 → 记观察档不判死。
# 单一真源=data/falsified_rules.json paper_trading_observations区；特批=--owner-approve-paper-obs。
_PAPER_OBSERVED_SOURCES = get_paper_observed_sources()

# 归因反馈（历史重亏标的买入前拦截——"防重复踩同一票的坑"，与闸门v2"防第一次踩坑"互补）
try:
    from attribution_feedback import query_signal_quality  # noqa: E402
except Exception:
    query_signal_quality = None

# 2026-10-21 主人令: 熔断+冷却期闸(原PD-037)整体废除——模拟盘=研究训练场, 防连亏闸属实盘机制;
# 详见 PROJECT_BOUNDARY §6 已否定路线, 恢复须经主人重新立项。

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
DQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"


def _market_env_now():
    # 2026-11-08 批次2规则卡(reports/batch2_trial_rules_20261108.md): market_env 从空串
    # 接线为真实环境值 "阶段|ma60_on/off"。数据: meta库daily_market_stats(涨停/跌停家数,
    # 日更至T-1) + index_daily_meta(MA60闸)。归因字段不阻断交易: 异常返回"na"。
    try:
        c = sqlite3.connect('file:' + META_DB + '?mode=ro', uri=True)
        zt, dt = c.execute("select zt_count, dt_count from daily_market_stats "
                           "order by date desc limit 1").fetchone()
        closes = [x[0] for x in c.execute(
            "select close from index_daily_meta where code='000001' order by date desc limit 62")]
        c.close()
        ma60 = sum(closes[:60]) / 60.0 if len(closes) >= 60 else None
        ma_ok = bool(ma60) and closes[0] > ma60
        ratio = zt / max(dt, 1)
        if zt <= 30:
            stage = '冰点'
        elif ratio <= 0.5:
            stage = '退潮'
        elif zt >= 60 and ratio >= 3:
            stage = '主升'
        else:
            stage = '启动'
        return '%s|ma60_%s' % (stage, 'on' if ma_ok else 'off')
    except Exception:
        return 'na'

# 2026-08-27: trade_records.signal_type 带 CHECK 约束(仅 probability_threshold/top_n_ranking/manual),
# 业务 source(bp_ep_core/mainline 等)禁止直塞——sell 侧曾因此 IntegrityError 崩溃、buy 侧被
# INSERT OR IGNORE 静默吞行(E32"台账零落库"深层根因)。两处落库统一走本归一化。
_SIGNAL_TYPES = ("probability_threshold", "top_n_ranking", "manual")


def _norm_signal_type(src):
    return src if src in _SIGNAL_TYPES else "manual"
PRE_TRADE = r"D:\zcodesj\pre_trade_check_v2.py"
# 卖出前校验（C1-C9防卖飞+冷静期）：唯一权威版在 D:\zcodesj\pre_sell_check.py
PRE_SELL = r"D:\zcodesj\pre_sell_check.py"
# O24(2026-08-27 E48收口): 批量前闸门输入数据(R19解禁/R22双口径)前置预检
PREFLIGHT_INPUTS = r"D:\zcodesj\preflight_gate_inputs.py"

# O19/O20(2026-08-26 E45收口): 提交委托台账+未决委托登记——异步成交归属判定唯一依据
try:
    from executor_order_ledger import log_submitted, log_open, extract_order_id, mark_order_status
except Exception:
    log_submitted = log_open = extract_order_id = mark_order_status = None  # 台账缺失不阻断下单，由 health_check 探针暴露

# 20261011批5: SELL_WINDOW/in_sell_window 死代码删除——卖出时间窗已按 8/14 主人裁决
# 废除(全天可交易), 函数仅存定义无调用方(grep 实证)。

# 2026-08-27 R1b: 挂单价与实时市价偏离熔断阈值(fail-closed)。
# 背景: bp_ep/full_market 月名单参考价滞后(8/26 科森科技 ref16.65 vs 市价19.95 +20%,
# 大连热电反例-11%),白名单层已做最新收盘覆盖(tulong_buy_whitelist 主流程),
# 此处为执行端最后防线;正常名单 limit=0.995×最新收盘,日内偏差<3%,5%不误伤。
REF_DEV_MAX = 0.05
# 2026-08-29 P1-1: 挂价对实时市价上限(执行价纪律, 带内追高截断)
BUY_MKT_CAP = 1.005
# 2026-09-05 F1(伪成交防护): 卖出挂价对实时市价下限(与BUY_MKT_CAP对称)。
# 模拟盘限价单按【委托价】撮合、不校验成交时刻市价(60笔实测8笔伪成交约千元,
# reports/PAPER_FILL_CONSISTENCY_TEST_20260905.md), 委托价远低于市价的卖单会按
# 低价伪成交(9/3焦煤6.53 < 当日真实最低6.85)。真实交易所按最优买价(≥限价)成交,
# 故上修至市价×0.99——仍保证成交(限价低于市价必成交), 让价上限1%有界,
# 且pnl_pct回归真实(消除"挂低价触发假-8%误报"被动override的根因)。
SELL_MKT_FLOOR = 0.99

# 2026-10-10 A1(主人「继续」令批准 owner_decisions_20261012 A1): 跳空限价卖出。
# 事件驱动卖出(S-5止损/走坏提请/爆量哨)挂价由实时价追卖改为 当日开盘价×0.995 限价,
# 10:00 未成交转市价兜底(建议制: fill_confirm_monitor reprice 对 A1_EVENT_SELL
# 未决单升级改价建议; HTTP 数据面无单笔撤单 API, 自动撤改待 MCP 单笔 cancel 接入)。
# 依据: reports/limit_sell_plan_20261009.md 35笔回放 改善中位+2.31pp/85.7%改善/
# 当日成交率94.3%/深跳空组+4.4pp全改善(vipdoc未复权双口径逐位一致)。
# 试运行条款: ≥10笔真实限价回报核对, 实现均价改善<0.5pp 或滞留事故即回滚。
EVENT_SELL_LIMIT_RATIO = 0.995
EVENT_SELL_NOTE_TAG = "A1_EVENT_SELL"  # 台账note标志(fill_confirm_monitor 兜底识别)


def calc_event_sell_limit_price(open_price):
    """A1事件驱动卖出挂价 = 当日开盘价 × EVENT_SELL_LIMIT_RATIO(纯函数, 可单测)。

    返回 (limit_price|None, info|None)。open_price 缺失/非法返回 (None, None),
    调用方 fail-open 回退现行为并 WARN 留痕。A股价格精度 0.01 元。
    """
    try:
        op = float(open_price)
    except (TypeError, ValueError):
        return None, None
    if op <= 0:
        return None, None
    lp = round(op * EVENT_SELL_LIMIT_RATIO, 2)
    return lp, {"open": op, "limit_price": lp, "ratio": EVENT_SELL_LIMIT_RATIO}


def apply_sell_mkt_floor(price, mkt):
    """卖出限价下限校验(纯函数, 可单测)。返回 (price', info|None)。

    mkt/price 任一为 None 时原样返回(行情缺失由调用方告警)。
    info = {"orig","mkt","floored","mult"} 仅在上修时非 None。
    """
    if not mkt or not price:
        return price, None
    floor = round(float(mkt) * SELL_MKT_FLOOR, 2)
    if float(price) < floor:
        return floor, {"orig": price, "mkt": mkt, "floored": floor,
                       "mult": SELL_MKT_FLOOR}
    return price, None


# 2026-09-23 T7(低价废单前置校验): 挂单价<0.10元的委托会被券商判价格非法直接废单
# (9月实测3笔0.05元废单混入撤单记录, 21%撤单率含异常)。纯函数可单测。
MIN_ORDER_PRICE = 0.10


def price_below_floor(price) -> bool:
    """买入挂价下限校验(纯函数, 可单测)。price 缺失时放行(行情缺失由调用方告警)。"""
    return price is not None and float(price) < MIN_ORDER_PRICE


def check_name_consistency(code: str, name: str):
    """code-name 一致性闸(20261030, 纯函数)。10/28 orders#338 实锤: 计划文案名"尤安设计"
    配上下单代码 603278(大业股份), 错误名字一路落台账/报表。本闸在一切业务闸之前拦截:
    名称库有记录且与传入 name 归一化后不一致 → 返回拒绝原因串(fail-closed);
    名称库无记录/查询异常 → fail-open 放行(打印警告留痕, 不阻塞交易链)。"""
    try:
        from stock_name_lookup import get_name
        real = (get_name(norm(code)) or "").strip()
    except Exception as _e:
        print(f"[NAME-GATE] ⚠️ 名称库查询异常, fail-open: {type(_e).__name__}: {_e}", file=sys.stderr)
        return None
    if not real:
        print(f"[NAME-GATE] ⚠️ {norm(code)} 名称库无记录, fail-open 放行", file=sys.stderr)
        return None
    _norm = lambda s: "".join((s or "").split()).replace("\u3000", "")
    if (name or "").strip() and _norm(name) != _norm(real):
        return (f"code-name错配: --code {norm(code)} 名称库='{real}', 传入name='{name}' "
                f"(20261030 orders#338同类错配拦截); 请修正name或确认code后重试")
    return None


def check_instrument_type(code: str):
    """品种闸（20261108, 纯函数）。99指数码撞码污染事件防御（11/08定谳92真指数+7错标真股票，
    三段式立项）。语义三分与名称闸同哲学：
    有记录且 instrument_type != 'stock' → 拒（fail-closed, 正向证据, IDENT-GATE 文案）；
    无记录/NULL → 放（fail-open, 与名称闸"无记录放行"一致, 保住退市股/新股窗口）；
    查询异常 → fail-open 留痕。卖出路径不接本闸（误持非股票仓的退出通道必须保留——
    10.8年3笔污染交易正是经卖出腿离场）。"""
    c6 = norm(code)
    try:
        from stock_name_lookup import get_instrument_type
        itype = get_instrument_type(c6)
    except Exception as _e:
        print(f"[IDENT-GATE] ⚠️ 品种查询异常, fail-open: {type(_e).__name__}: {_e}", file=sys.stderr)
        return None
    if itype is None:
        print(f"[IDENT-GATE] ⚠️ {c6} stock_info 无品种记录, fail-open 放行", file=sys.stderr)
        return None
    if itype.strip().lower() != 'stock':
        return (f"品种闸拦截: {c6} instrument_type='{itype}' 非股票(20261108指数码撞码防御); "
                f"只允许 instrument_type='stock' 标的")
    return None


def norm(code: str) -> str:
    c = code.strip().lower()
    c = c.split(".", 1)[0]   # 20261011批4: 剥 .SH/.SZ 后缀——"600519.SH"此前产出 "sh600519.sh" 直送 ws.buy
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c
    num = c.zfill(6)
    if num.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + num
    if num.startswith(("8", "920", "430")):
        return "bj" + num
    return "sz" + num


def _owner_approve_note(a) -> str:
    """B2(20261015,自省E4闭环): owner-approve 特批动作落台账自解释标注。
    兴齐300573案例实证: 特批不落 trade_records → 归因层无法区分特批与违规,
    误判违规一次。特批 buy 成交回写时拼接 [owner-approve:闸名] 使台账自解释。"""
    flags = [n for n in ("rs5", "r6", "r31", "r30a", "ddgate", "r37", "r38", "r39", "price_cap",
                         "attrib", "heavy")
             if getattr(a, "owner_approve_" + n, False)]
    return (" [owner-approve:" + ",".join(flags) + "]") if flags else ""


def _trade_records_duplicate(conn, code: str, trade_date: str, action: str,
                             price, volume) -> bool:
    """2026-09-23 T8(幂等查重): trade_records 写前查重, 五元组(code,trade_date,action,
    price,volume)精确匹配——双轨重复记账(async回报确认+盘后审计导入各写一次)根因封堵。
    参数绑定SQL、纯查询无副作用, 可单测(内存sqlite)。返回 True=已存在, 调用方跳过写入。"""
    n = conn.execute(
        "SELECT COUNT(*) FROM trade_records "
        "WHERE code=? AND trade_date=? AND action=? AND price=? AND volume=?",
        (code, trade_date, action, price, volume)).fetchone()[0]
    return n > 0



def _tencent_quote_fallback(code: str) -> float | None:
    """行情降级链(20260913韧性加固): westock quote 空壳故障时, 用腾讯公开行情HTTP接口取现价.
    输入6位代码, 自动加 sh/sz 前缀; 失败返回 None. 今天(20260913)实锤: westock行情空壳与下单
    ckv故障同时发生, quote空壳→price=None→vol=0→'建议股数为0' rc=0 静默返回(F2债务的恶化形态)."""
    import urllib.request as _u
    c6 = "".join(ch for ch in code if ch.isdigit())[-6:]
    prefix = "sh" if c6.startswith(("5", "6", "9")) else "sz"
    try:
        r = _u.urlopen(f"https://qt.gtimg.cn/q={prefix}{c6}", timeout=8)
        txt = r.read().decode("gbk", errors="replace")
        parts = txt.split("~")
        px = float(parts[3])
        return px if px > 0 else None
    except Exception:
        return None


def flatten_quote(qraw: dict) -> dict:
    """2026-08-27 P1: westock quote 兼容解包。

    ws.quote().get("data") 实际为 {code:{price/prev_close/changePercent..}} 嵌套，
    此前直接 quote.get("price") 恒 None——买入/卖出/R25 大盘择时/C9 的实时行情字段
    全链空壳 fail-open。本函数将嵌套展平为顶层字段 dict；已是扁平结构则原样剔除子 dict 返回。
    """
    if not isinstance(qraw, dict):
        return {}
    for v in qraw.values():
        if isinstance(v, dict) and (("price" in v) or ("changePercent" in v)
                                    or ("prev_close" in v) or ("change_percent" in v)):
            return v
    return {k: v for k, v in qraw.items() if not isinstance(v, dict)}


def run_gate(a, holdings: int, total_cash: float, quote: dict, ws=None) -> dict:
    """调 pre_trade_check_v2 全量闸门。"""
    cmd = [sys.executable, PRE_TRADE, "--code", a.code, "--name", a.name or "",
           "--source", a.source, "--holdings", str(holdings), "--total-cash", str(total_cash)]
    # R24板块高潮禁追: 转发申万L1板块名（此前漏传致R24恒no_sector fail-open，2026-08-24修复）
    if a.sector:
        cmd += ["--sector", a.sector]
    price = a.price or quote.get("price")
    # 行情降级链(20260913韧性加固): westock quote 空壳时切腾讯公开行情(今天与下单ckv故障同窗出现)
    # 2026-09-18 修复: 降级分支引用未定义的 result → 触发降级即 NameError 崩溃(降级链自上线
    # 首次实弹即炸, 博腾验证单 dry-run 抓出)。孤儿赋值删除, 降级留痕并入提示行。
    if not price:
        _fb = _tencent_quote_fallback(norm(a.code))
        if _fb:
            print(f"  ⚠️ westock行情空壳, 降级用腾讯行情 {_fb}(行情降级链, price_source=tencent_fallback)")
            price = _fb
    if price:
        cmd += ["--price", str(price), "--close", str(price)]
    for attr_v, flag in [(a.prev_close or quote.get("prev_close"), "--prev-close"),
                         (a.amount, "--amount"), (quote.get("high"), "--high"),
                         (quote.get("low"), "--low"), (a.pct_60d, "--pct-60d"),
                         (a.amplitude_40d, "--amplitude_40d"), (a.sector_breadth, "--sector-breadth")]:
        if attr_v is not None:
            cmd += [flag, str(attr_v)]
    # R25大盘择时指数取数: 已退役（2026-11-06 主人令废除 R25——崩盘日买入不再有大盘级拦截）。
    # 原 do_buy 自动获取上证/创业板涨跌幅传入闸门段整块移除(判定块侧同批退役, 该取数段零消费者)。
    if a.intent:
        cmd += ["--intent", a.intent]
    if a.allow_add_position:
        cmd.append("--allow-add-position")
    # 2026-09-06: R-S5特批豁免透传(闸门侧--owner-approve-rs5降WARN留痕)
    if getattr(a, "owner_approve_rs5", False):
        cmd.append("--owner-approve-rs5")
    # 2026-09-06: R6特批豁免透传(主人指令000911周一必买; 闸门侧同构降WARN留痕)
    if getattr(a, "owner_approve_r6", False):
        cmd.append("--owner-approve-r6")
    # 2026-09-08: R31买价豁免透传(主人裁决开豁免口, 闸门侧降WARN留痕)
    if getattr(a, "owner_approve_r31", False):
        cmd.append("--owner-approve-r31")
    # 2026-09-10: R30a行业集中度豁免透传(主人裁决开豁免口, 闸门侧降WARN留痕)
    if getattr(a, "owner_approve_r30a", False):
        cmd.append("--owner-approve-r30a")
    # 2026-11-04: DD_GATE回撤门控已退役(主人令「回撤保护闸(防御档)不合理, 取消」)——
    # 透传与buy侧旗标一并移除; 计划文件批准键断根防线(_OWNER_ONLY_PLAN_KEYS)保留不变。
    # 2026-10-08: R38期望闸/R39落刀闸豁免透传(与ddgate完全同构; 闸门侧降WARN留痕)
    # 2026-10-16: R37情绪档弱档豁免透传(补齐10/15半截落地: 检查器argparse/调用侧已就绪,
    #             执行器缺argparse+透传致拒绝文案指挥用户用不存在的标志; 与r38同构)
    if getattr(a, "owner_approve_r37", False):
        cmd.append("--owner-approve-r37")
    if getattr(a, "owner_approve_r38", False):
        cmd.append("--owner-approve-r38")
    # 2026-11-06: R39落刀/R25大盘择时特批透传已退役(主人令废除两闸, 透传段随判定块同批删除)。
    # 2026-11-06: P0-A/admit暂行准入特批透传(两级制两集不重叠合同下黑名单源主人令买入通道)
    if getattr(a, "owner_approve_admission", False):
        cmd.append("--owner-approve-admission")
    # 2026-10-09: S-3价格铁律豁免透传(主人特批兴齐眼药35.40单批准开豁免口; 闸门侧降WARN留痕)
    if getattr(a, "owner_approve_price_cap", False):
        cmd.append("--owner-approve-price-cap")
    # 2026-09-22: 子进程PYTHONUTF8=1(GBK坑族, 宏桥S-5实弹事故同源)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONUTF8="1"))
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"verdict": "REJECT", "rejects": [f"闸门异常: {proc.stderr[:200]}"]}


def _trading_session_gate(a, action: str) -> dict | None:
    """交易时段闸——2026-11-05 主人令「这个闸有错误，把这个闸删掉，清干净」：整体移除。

    原闸(2026-09-02建)含三道分支: 09:30-15:00时段硬拒 / 周末硬拒(20261011批4) /
    节假日PIT日历硬拒(20261011批5)——**全部分支移除, 本函数现为直通(恒 None=放行)**。
    原防对象=8/28+9/2两次凌晨19万级误挂单; 残余风险承接=断根三件(计划自毁无条件/
    零批准键/僵尸任务停用, 11/3主人令)+下单WARN审计线+新增定时任务人工自审触发时刻。
    恢复参考=git历史本函数(SOP二百四十三/二百四十四)。"""
    return None


# O07(2026-09-03, A6误挂三单收口): 意图预算硬顶倍数——下单金额超过 budget×1.5 即截断股数。
# 9/2凌晨 02:15 调试误触发 --execute 三单按 sizing 默认(现金×20%)各挂约19.2万级, 与主人
# 2万/只验证仓意图差10倍; 时段维度已由 96c1c00 物化, 本常量+do_buy 内 budget/amount
# 双闸补齐金额维度(计划类脚本调用必须带 --budget 或 --volume/--amount 之一)。
BUDGET_CAP_MULT = 1.5


def _same_day_buy_submitted(code: str) -> str | None:
    """do_buy 提交幂等键（2026-09-10）：查当日该码是否已有 BUY 提交。

    依据=executor_orders 台账(当日 BUY 且 OPEN/PARTIAL/FILLED)∪open_orders 未了结
    (OPEN/PARTIAL)。R3(bought_codes)只做成交后的事后拦截, 同一计划重复/并发
    --execute 的竞态窗口内双委托照挂——提交前查台账堵住窗口。撤单行(CANCELLED/
    EXPIRED_READ)不拦=撤后改价重挂合法。返回命中证据描述或 None。
    """
    from executor_order_ledger import EXEC_ORDERS, OPEN_ORDERS
    today = datetime.now().strftime("%Y%m%d")
    c6 = "".join(ch for ch in (code or "") if ch.isdigit())[-6:]

    def _rows(path):
        if not os.path.exists(path):
            return []
        rows = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        rows.append(json.loads(s))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return rows

    for r in _rows(EXEC_ORDERS):
        rc = "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())[-6:]
        if ((r.get("action") or "").upper() == "BUY" and rc == c6
                and r.get("trade_date") == today
                and (r.get("status") or "OPEN") in ("OPEN", "PARTIAL", "FILLED")):
            return f"executor_orders {r.get('status')} @{r.get('price')}"
    for r in _rows(OPEN_ORDERS):
        rc = "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())[-6:]
        if ((r.get("action") or "").upper() == "BUY" and rc == c6
                and (r.get("status") or "") in ("OPEN", "PARTIAL")):
            return f"open_orders {r.get('status')} @{r.get('price')}"
    return None


def _manual_note_guard(a) -> dict | None:
    """manual 特批买入留痕闸(2026-10-15 四维复盘执行缺陷②, 主人「继续」令)。

    10/15 三笔 manual_confirmed 买入台账 note 全空——买入触发理由不可溯源,
    违反宪法§9 归因可追溯(台账 note 是异步回报/盘后导入恢复理由的唯一来源,
    卖侧 2026-08-28 E72 已同构落地, 买侧此前缺失)。fail-closed: 特批 --execute
    必须携带 --note; dry-run 不拦(允许试算)。返回 REJECT dict 或 None(放行)。
    """
    if ((getattr(a, "source", "") or "").strip() != "manual_confirmed"
            or not getattr(a, "execute", False)):
        return None
    if not (getattr(a, "note", "") or "").strip():
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "source": "manual_confirmed", "gate_verdict": "REJECT",
                "gate_rejects": ["manual特批留痕闸: source=manual_confirmed + --execute "
                                 "必须携带 --note 买入理由(20261015四维复盘执行缺陷②; "
                                 "归因可追溯宪法§9, 卖侧E72同构)"],
                "reason": "特批无理由拒绝(fail-closed); 补 --note 后重试"}
    return None


# ---- A1/A3 仓位类闸 [2026-10-26 主人令整体废除] ----
# 「从此不再限定模拟盘持仓的持股数量，以及仓位大小」——终局裁决:
# A1 组合仓位硬约束(2026-10-19立, 0.35→0.80(10-21)→0.99→1.00(10-26晨)逐级放宽后全拆)、
# A3 特批重仓前置(单笔>10万且单票>8%须--owner-approve-heavy)同批拆除;
# _POSITION_GATE_THRESHOLD/_HEAVY_ORDER_THRESHOLD/_HEAVY_SINGLE_STOCK_CAP/
# _log_gate_reject/position_gate_rejects.jsonl 写面 一并退役(历史日志文件保留只读)。
# 保留不变: 30元价格铁律/-8%止损/禁杠杆/R30b行业集中度/其余风控闸; 治理档案=PROJECT_BOUNDARY §6。


def _zone_scan_lookup(code6: str) -> dict:
    """查最新全宇宙买卖区域扫描(主人令20261105「选完股之后，还要看有没有买卖点，买卖区域，才能买」
    =买入区域闸ZONE-GATE的数据面). 返回 {ok, info, basis}: ok=True=该股在最新位置池
    (扫描器判定其有结构买卖点: 买区/触发/失效齐备); 扫描缺失/陈旧(基准>4自然日)或不在池=ok=False.
    幂等只读; 扫描产物=universe_zone_scanner.py生成(唯一owner)."""
    import glob as _g
    scans = sorted(_g.glob(r"D:\zcodesj\data\universe_zone_scan_*.csv"))
    if not scans:
        return {"ok": False, "info": "扫描产物缺失(universe_zone_scanner.py未跑)", "basis": ""}
    latest = scans[-1]  # 文件名含YYYYMMDD, 字典序=时间序
    basis = os.path.basename(latest)[-12:-4]
    try:
        from datetime import date as _date
        y, m, d = int(basis[:4]), int(basis[4:6]), int(basis[6:8])
        _age = (_date.today() - _date(y, m, d)).days
    except Exception:
        return {"ok": False, "info": f"扫描基准日异常: {basis}", "basis": basis}
    if _age > 4:
        return {"ok": False, "info": f"扫描陈旧(基准{basis}, 距今{_age}天>4)", "basis": basis}
    try:
        with open(latest, encoding="utf-8-sig") as f:
            head = f.readline().strip().split(",")
            idx = head.index("code")
            for line in f:
                if line.strip() and line.split(",")[idx] == code6:
                    return {"ok": True, "info": f"在池(基准{basis})", "basis": basis}
    except Exception as e:
        return {"ok": False, "info": f"扫描读取失败: {type(e).__name__}", "basis": basis}
    return {"ok": False, "info": f"不在位置池(基准{basis}, 池186只=A箱底/B突破观察/C强势回踩)", "basis": basis}


def do_buy(a, ws: WeStockClient) -> dict:
    tg = _trading_session_gate(a, "BUY")
    if tg:
        return tg
    _nchk = check_name_consistency(a.code, a.name)
    if _nchk:
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "gate_verdict": "REJECT", "gate_rejects": [f"NAME-GATE: {_nchk}"],
                "reason": _nchk}
    _ichk = check_instrument_type(a.code)
    if _ichk:
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "gate_verdict": "REJECT", "gate_rejects": [f"IDENT-GATE: {_ichk}"],
                "reason": _ichk}
    # 2026-08-29 P0-A: 信号源准入第一道——source 必须显式且属生产源清单
    # (9/21 名单制废止后语义收敛为 admit 闸门执行面: 拒绝空串/未登记任意串/退役源;
    #  全宇宙系统化扫描源经 preflight 验证即为合法, bp_ep Top20 仅参考对照)。
    _src = (getattr(a, "source", "") or "").strip()
    # 2026-11-05 两级制第一级(PD-053主人令批准): 回测预筛闸——回测全历史明显无优势的源
    # 不进模拟盘赛道, 拒绝在暂行准入之前(给对拒绝理由与证据指针, 非判伪定谳;
    # 判伪唯一权威=模拟盘T+20满窗结算+赛马)。主人特批走 --owner-approve-prescreen。
    if _src in _BACKTEST_NO_EDGE_SOURCES and not getattr(a, "owner_approve_prescreen", False):
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "source": _src, "gate_verdict": "REJECT",
                "gate_rejects": [f"PRESCREEN-GATE 回测预筛拒绝: source='{_src}' 在回测无优势观察区"
                                 f"(data/falsified_rules.json backtest_no_edge_observations, "
                                 f"主人20261105裁决「回测判不了伪,模拟盘才能判伪」两级制第一级)——"
                                 f"回测明显无优势的源不进模拟盘赛道; 主人特批请加 --owner-approve-prescreen; "
                                 f"恢复路径=新证据改档或模拟盘赛马过关"],
                "reason": "回测预筛拒绝(两级制第一级; 非判伪, 判伪=模拟盘)"}
    # 2026-11-07 模拟盘实盘观察闸（主人令「先停确定无效的」B+A并行）:
    # 证据来源=模拟盘真实建仓×实际走势(非回测), 语义强于回测预筛但样本未满T+20 → 观察档不判死。
    # 位置在回测预筛之后、准入之前; 主人特批走 --owner-approve-paper-obs。
    if _src in _PAPER_OBSERVED_SOURCES and not getattr(a, "owner_approve_paper_obs", False):
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "source": _src, "gate_verdict": "REJECT",
                "gate_rejects": [f"PAPER-OBS-GATE 模拟盘观察拒绝: source='{_src}' 在模拟盘实盘观察无优势区"
                                 f"(data/falsified_rules.json paper_trading_observations)——"
                                 f"实盘建仓证据: method_picks T+5 -5.04%/胜率9.5%, "
                                 f"manual_confirmed T+5 -6.56%/胜率10.3%, 任何买入位置均亏; "
                                 f"主人特批请加 --owner-approve-paper-obs; "
                                 f"恢复路径=新证据改档(如T+20满窗转正)"],
                "reason": "模拟盘观察无优势拒绝(实盘证据; 非回测, 观察档不判死)"}
    if _src not in _BUY_SRC_ALLOWED and not getattr(a, "owner_approve_admission", False):
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "source": _src, "gate_verdict": "REJECT",
                "gate_rejects": [f"P0-A信号源准入: source='{_src or '(空)'}' 不在暂行方法准入"
                                 f"(2026-11-03主人令: 仅七数据领跑组暂行实买, 其余源影子观察)——"
                                 f"主人特批请走 source=manual_confirmed 或 --owner-approve-admission"
                                 f"(2026-11-06 新增, 主人令批量买黑名单源时两集不得重叠的合规通道); "
                                 f"复审点=11/26赛马首考"],
                "reason": "暂行方法准入拒绝(2026-11-03主人令; 未登记源/退役源/非领跑源一律拒)"}
    # 2026-11-05 主人令「选完股之后，还要看有没有买卖点，买卖区域，才能买」=买入区域闸(ZONE-GATE):
    # 买入标的必须落在最新全宇宙买卖区域扫描的位置池(扫描器判定其有结构买卖点: 买区/触发/失效齐备,
    # A箱底/B突破观察/C强势回踩三类别); 扫描缺失/陈旧(基准>4自然日)或标的不在池=无买卖点=拒绝;
    # 扫描每日07:45自动生成(失效即拒=fail-closed, 断档会立刻暴露而非静默); 主人逐笔特批 --owner-approve-zone。
    _z = _zone_scan_lookup(norm(a.code)[2:])
    if not _z["ok"] and not getattr(a, "owner_approve_zone", False):
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "source": _src, "gate_verdict": "REJECT",
                "gate_rejects": [f"ZONE-GATE 买卖区域闸拒绝: {norm(a.code)} {_z['info']}——"
                                 f"主人令: 选股后必须过买卖点/买卖区域判定才能买"
                                 f"(全宇宙买卖区域扫描=唯一数据面, universe_zone_scanner.py每日07:45生成); "
                                 f"主人逐笔特批请加 --owner-approve-zone"],
                "reason": "买卖区域闸拒绝(无买卖点/区域不合格/扫描不可用)"}
    # manual 特批留痕闸(P0-A 之后、任何 ws 调用之前——REJECT 零副作用可无 mock 单测)
    _mng = _manual_note_guard(a)
    if _mng:
        return _mng
    positions = ws.positions().get("data", {}).get("positions", [])
    holdings = len([x for x in positions if int(x.get("quantity", 0)) > 0])
    portfolio = ws.portfolio().get("data", {}).get("portfolio", {})
    # westock真值源fail-closed(20261011批2立; 2026-10-26仓位限令废除后语义收窄保留):
    # R10/sizing/A1/A3 已废除, 本值仅作台账信息字段; 但 totalAssets 缺失=组合快照取数失败,
    # 真值源半残状态下仍拒买(fail-closed, 与熔断闸同姿态); 卖出路径不消费本值, 止损链不受影响。
    _ta_raw = portfolio.get("totalAssets")
    if _ta_raw is None:
        return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                "gate_verdict": "REJECT",
                "gate_rejects": ["westock组合快照异常: portfolio 无 totalAssets"
                                 "(取数失败≠资产为0, fail-closed拒买)"],
                "reason": "组合快照异常拒绝下单(先复核westock服务再重试)"}
    total_cash = float(_ta_raw)
    quote = flatten_quote(ws.quote(norm(a.code)).get("data", {}))
    # F2收口(2026-10-08主人批): westock行情空壳时单点注入腾讯降级价——降级链此前仅在
    # run_gate侧, 下单价(price = a.price or quote.get("price"))取None→建议股数0→rc=0静默
    # 返回(F2恶化形态, 见_tencent_quote_fallback docstring); 注入quote后403/440/454/486
    # 各取价点与run_gate全部自动生效, 与卖侧同款留痕。
    if not quote.get("price"):
        _fb = _tencent_quote_fallback(norm(a.code))
        if _fb:
            print(f"  ⚠️ westock行情空壳, 降级用腾讯行情 {_fb}(行情降级链, price_source=tencent_fallback)")
            quote["price"] = _fb

    # 2026-08-24 P0修复(历史): 显式指定volume时反推 amount=volume*price——R10 已随
    # 2026-10-26 主人令废除仓位限定, 反推保留用于台账金额一致性与归因减半仓载体。
    # 归因减半仓对 a.amount 的改写在此之后。
    if getattr(a, "volume", None):
        _px0 = a.price or quote.get("price")
        if _px0:
            a.amount = float(a.volume) * float(_px0)

    # ---- 第0层：归因反馈检查（历史重亏标的拦截，先于闸门）----
    attribution_note = None
    if query_signal_quality is not None:
        q = query_signal_quality(norm(a.code))
        if q.get("known"):
            if q.get("trades", 0) >= 2 and q.get("win_rate", 100) < 35 and q.get("pnl_amount", 0) < 0:
                if not getattr(a, "owner_approve_attrib", False):
                    return {"action": "BUY", "code": a.code, "name": a.name, "executed": False,
                            "gate_verdict": "REJECT",
                            "gate_rejects": [f"归因层拦截: {a.name}历史{q['trades']}次交易胜率{q['win_rate']}%"
                                              f"累计亏损{q['pnl_amount']}元（重复踩坑，禁止再入）"],
                            "attribution": q}
                # 2026-11-02 主人特批豁免: 主人令「趋势改变是可以买的」现场裁决, WARN留痕不拦
                attribution_note = (f"归因层主人特批豁免(owner-approve-attrib): "
                                    f"历史{q['trades']}次胜率{q['win_rate']}%亏{q['pnl_amount']}元, "
                                    f"主人裁决趋势改变可买, 留痕不拦")
            elif q.get("pnl_amount", 0) < 0:
                attribution_note = f"归因警示: 历史亏损{q['pnl_amount']}元，建议减半仓"
                a.amount = (a.amount / 2) if a.amount else None  # 自动减半仓

    gate = run_gate(a, holdings, total_cash, quote, ws=ws)
    result = {"action": "BUY", "code": a.code, "name": a.name, "source": a.source,
              "holdings": holdings, "total_cash": total_cash,
              "attribution_note": attribution_note,
              "gate_verdict": gate.get("verdict"), "gate_rejects": gate.get("rejects", []),
              "gate_warnings": gate.get("warnings", []), "sizing": gate.get("sizing", {})}

    if gate.get("verdict") == "REJECT":
        result["executed"] = False
        result["reason"] = "闸门拒绝，终止下单"
        return result

    # sizing 建议股数 [2026-10-26 主人令废除仓位限定]: 闸门不再产出权重帽 sizing,
    # 下单股数全部由显式 --volume/--amount 决定; 归因警示减半仓(2026-08-24 P1)
    # 继续经 a.amount 路径生效(下方 vol_locked_by_amount 消费并打 halved 标记)。
    price = a.price or quote.get("price")
    # 2026-08-25 O1: 不追价纪律物理落地——limit_price 超限截断(白名单已生成 price×0.995 列)
    if getattr(a, "limit_price", None) and price and price > a.limit_price:
        print(f"  ⚠️ 挂单价 {price} > 白名单 limit_price {a.limit_price} → 截断不追价")
        result["price_capped_by_limit"] = {"orig": price, "capped": a.limit_price}
        price = a.limit_price
        result["price"] = price
    # 2026-08-27 R1b: 挂单价与实时市价偏离熔断(fail-closed)——名单参考价滞后最后防线。
    # 基准必须用 ws 实时行情价(而非 a.price/O1 截断后的 price)，否则截断后自比恒为 0 偏离。
    # 兼容两种 quote 结构: 扁平 {price:..} 与 ws 实际返回 {code:{price:..}}(2026-08-27 发现
    # do_buy 的 quote 实为嵌套 dict，此前 quote.get("price") 恒 None=行情空壳，债务另记录)。
    # 极端行情说明: 当日±5%外波动的死单(不可能成交或需追价)一并拦截，符合 R1 不追价精神。
    _lp = getattr(a, "limit_price", None)
    _q = quote.get(norm(a.code), {}) if isinstance(quote, dict) else {}
    _mkt = (_q.get("price") if isinstance(_q, dict) else None) or quote.get("price")
    if _lp and _mkt and abs(_lp / _mkt - 1) > REF_DEV_MAX:
        result["executed"] = False
        result["gate_verdict"] = "REJECT"
        result["reason"] = (f"buy_stale_ref熔断: 挂单价{_lp}与实时市价{_mkt}偏离"
                            f"{abs(_lp / _mkt - 1) * 100:.1f}% > {REF_DEV_MAX:.0%}——"
                            "名单参考价疑似滞后(月名单旧价/复权异常)，须以最新收盘重生成名单后重过闸")
        print(f"  ⛔ {result['reason']}")
        return result
    # 2026-08-29 P1-1(胜率计划): 执行价纪律——最终挂价不得高于实时市价×1.005(带内截断)。
    # 实测 8/11-28 实盘 169 笔买价对当日 OHLC 均值平均 +0.69%/72% 买贵(最贵 +7.66%);
    # R1b 只拦 ±5% 外大偏离、O1 只对白名单 limit 截断——本条补两者之间的小幅系统性追高。
    # 截断=降低挂价(更保守), 无熔断副作用; _mkt 缺失时跳过(与 R1b 同口径, 行情空壳另有债务记录)。
    # 2026-11-05 主人令「确定能买不要被拦截」: --owner-high-hang-pct 批次豁免 P1-1 截断——
    # 挂价=最新收盘×pct 本身即主人授权价(让价上界=pct自建), 市价×1.005 封顶不再适用;
    # 结构判定闸/其余R闸照常。单笔buy路径无此旗标不受影响。
    if _mkt and price and price > _mkt * BUY_MKT_CAP and not getattr(a, "owner_high_hang_pct", None) and not getattr(a, "owner_hang_open_pct", None):
        _capped = round(_mkt * BUY_MKT_CAP, 2)
        print(f"  ⚠️ 挂单价 {price} > 市价{_mkt}×{BUY_MKT_CAP} → 截断至 {_capped}(P1-1执行价纪律)")
        result["price_capped_by_mkt"] = {"orig": price, "mkt": _mkt, "capped": _capped}
        price = _capped
        result["price"] = price
    elif price and not _mkt:
        # 2026-09-05 F2可见性: 行情空壳时封顶校验静默失效=8/26盘前批多付同款漏洞
        # (挂价未经市价核验直接提交), 至少留痕可审计(修复抓手仍为executor默认取实时价)。
        print("  ⚠️ 实时行情不可用, BUY_MKT_CAP市价封顶校验跳过——挂单价未经市价核验")
        result["mkt_quote_unavailable"] = True
    # 2026-10-02 PD-018④(主人「继续」批准执行纪律档): 尾盘批量买入挂当日均价线限价——
    # 时段≥14:00 且当日已买≥3只(批量态, trade_state buy_count 载体)时, 挂价额外截断至
    # westock 报价 avg_price(9/23五连买14:03-14:04收盘全部浮亏0.4-1.0%案);
    # 单笔尾盘买不受限(保留主人有意追价空间, 由 R7/R31/R-S5 管追价);
    # avg_price 缺失跳过留痕(不造数据); state 读取失败不阻断下单主链。
    try:
        _st = load_state() or {}
        _batch_n = int(_st.get("buy_count", 0) or 0)
        _avg = (_q.get("avg_price") if isinstance(_q, dict) else None) or quote.get("avg_price")
        if (datetime.now().hour >= 14 and _batch_n >= 3 and price
                and _avg and float(_avg) > 0 and price > float(_avg)):
            _vcapped = round(float(_avg), 2)
            print(f"  ⚠️ 尾盘批量(当日已买{_batch_n}只)挂价{price} > 当日均价线{_vcapped} "
                  f"→ 截断至均价线(PD-018④执行纪律档)")
            result["price_capped_by_vwap"] = {"orig": price, "vwap": _vcapped, "batch_n": _batch_n}
            price = _vcapped
            result["price"] = price
    except Exception as _e:
        print(f"  [PD-018④] 均价线截断检查异常(不阻断): {type(_e).__name__}: {_e}")
    # 2026-09-23 T7(低价废单前置校验): 挂单价<0.10元直接拒绝(dry-run预演同口径),
    # 不进入股数计算与下单——券商价格非法废单的入口侧封堵。
    if price_below_floor(price):
        result["executed"] = False
        result["gate_verdict"] = "REJECT"
        result["reason"] = (f"低价废单熔断: 挂单价{price} < {MIN_ORDER_PRICE}元, "
                            "直接拒绝下单(价格非法废单前置校验T7)")
        print(f"  ⛔ {result['reason']}")
        return result
    # ---- 下单股数确定(dry-run 预览与真下单同口径) ----
    vol = a.volume
    # O07(2026-09-03): --amount 绑定股数——此前 amount 仅送闸门不约束下单股数,
    # 是 8/28+9/2 两次误挂同根因之一; 无 --volume 时按 amount/price 整手向下折算。
    if not vol and getattr(a, "amount", None) and price:
        vol = int(float(a.amount) / float(price) // 100) * 100
        result["vol_locked_by_amount"] = {"amount": a.amount, "price": price, "vol": vol}
        if attribution_note:
            result["halved_by_attribution"] = True  # P1 减半仓经 amount 路径生效(sizing 废除后唯一载体)
    # 2026-08-24 P0修复: A股整手约束——按100股向下取整。
    # [2026-10-26 主人令废除仓位限定] 隐式 sizing 默认股数(`vol or shares`)与
    # 超-sizing 截断一并拆除: 无 --volume/--amount 时零股在下方显式拒绝, 不再按
    # 现金×20% 隐式下重单(9/2 凌晨三单×19.2万误挂事故的隐式默认源就此消灭)。
    # [20261027补] 无股数结构化拒绝兜底(10/26重构漏网: vol=None 直落 int(None) 崩溃,
    # 实锤=tests/test_poa_source_gate 旧夹具 amount无price; 按主人令"零股结构化拒绝"语义补齐):
    if vol is None:
        result["status"] = "REJECT"
        result["reason"] = ("无显式股数拒绝(10/26主人令废除隐式默认sizing): "
                            "--amount 折算股数需搭配 --price(当前缺失), 或直接给 --volume")
        print(f"  ⛔ {result['reason']}")
        return result
    vol = int(vol) // 100 * 100
    # O07(2026-09-03): 意图预算硬顶——下单金额不得超过 budget×1.5(特批/验证仓护栏;
    # 不传 --budget 完全不受影响)。预算不足一手=真下单时拒绝。
    # 注: budget=调用方自报意图预算的一致性护栏, 非仓位上限政策, 2026-10-26 仓位限令废除后保留。
    _budget = getattr(a, "budget", None)
    if _budget and price and vol > 0:
        _cap_amt = float(_budget) * BUDGET_CAP_MULT
        if vol * price > _cap_amt:
            _bvol = int(_cap_amt / float(price) // 100) * 100
            result["vol_capped_by_budget"] = {"budget": _budget, "cap_amt": round(_cap_amt, 2),
                                              "orig_vol": vol, "capped_vol": _bvol}
            print(f"  ⚠️ 下单金额{vol * price:.0f}超意图预算{_budget}×{BUDGET_CAP_MULT}"
                  f"={_cap_amt:.0f} → 截断至{_bvol}股(O07金额硬闸)")
            vol = _bvol
    # ---- A3 特批重仓前置约束 [2026-10-26 主人令废除] ----
    # 「不再限定仓位大小」——单笔>10万且单票>8%须 --owner-approve-heavy 的前置约束整体拆除
    # (2026-10-19 立, 拦截案例见 data/position_gate_rejects.jsonl 历史日志, 只读保留)。
    if not a.execute:
        result["executed"] = False
        result["reason"] = "dry-run（未加 --execute）。将买 {vol}股 @{price}".format(vol=vol, price=price)
        return result

    # ---- 真下单 ----
    # 2026-09-10: 提交幂等键(见 _same_day_buy_submitted)——同码当日已有提交/未了结委托
    # 拒绝重复下单; 加仓显式 --allow-add-position 豁免(与 R13 语义一致)
    _dup = _same_day_buy_submitted(norm(a.code))
    if _dup and not getattr(a, "allow_add_position", False):
        result["executed"] = False
        result["gate_verdict"] = "REJECT"
        result["reason"] = f"提交幂等拦截: 当日已有买入提交/未了结委托({_dup}), 拒绝重复下单"
        print(f"  ⛔ {result['reason']}")
        return result
    if vol <= 0:
        result["executed"] = False
        result["reason"] = ("下单股数为0: 未显式指定 --volume/--amount(2026-10-26 主人令废除"
                            "仓位限定后无默认 sizing), 或金额过小不足一手")
        return result
    # 下单前已有持仓量（成交验证用持仓差，修复加仓场景把存量也记成本次买入的污染）
    held_before = next((x for x in positions if norm(x.get("code", "")) == norm(a.code)), None)
    qty_before = int(held_before.get("quantity", 0)) if held_before else 0
        # ckv自愈重试(20260913韧性加固): westock服务端证券主档间歇缺失(get ckv key failed ret-13200)
    # 实测同刻深市成功沪市失败/601866早盘成功午后失败=按股按时间间歇 → 重建客户端+间隔重试自愈。
    # 循环结束即止(不额外补买): order=最后一次返回, 由下方提交失败检查块统一判定。
    order = None
    for _ckv_try in range(3):
        order = ws.buy(norm(a.code), float(price), int(vol))
        _raw0 = str((order or {}).get("raw", ""))
        if "ckv" not in _raw0:
            break
        print(f"  ⚠️ westock主档间歇缺失({_ckv_try+1}/3): {_raw0[:80]}")
        if _ckv_try < 2:
            try:
                ws = WeStockClient()   # 重建会话(记忆先例: 会话老化重连自愈)
            except Exception:
                pass
            time.sleep(30)
    # 提交失败检查(20260913胜率Top10批量实锤修复): ws.buy 服务端拒绝(如 "get ckv key failed"/
    # "可用资金不足")时返回 {"raw": "提交委托失败: ..."} 无order字段——旧代码不检查直接查持仓差,
    # 持仓差=0 → 写假OPEN "限价未成交" → rc=0 PASS_WITH_WARN = 静默吞错(实锤: 8单全败伪装成挂单等待)。
    # 现在: 提交失败 → 不写台账、立即FAIL返回 rc=2。
    _raw = str((order or {}).get("raw", ""))
    if "失败" in _raw or "拒绝" in _raw or (order or {}).get("ok") is False:
        result["executed"] = False
        result["submit_failed"] = True
        result["gate_verdict"] = "SUBMIT_FAIL"
        result["submit_error"] = _raw[:200]
        print(f"  X 提交失败: {_raw[:150]}")
        return result
    # O19(2026-08-26 E45): 委托提交即登记台账——异步成交归属判定唯一依据(order_id 幂等)
    # 2026-10-06 三尾巴①: source 信号源落台账(此前 25 行 source 全空无法溯源)
    oid = log_submitted(order, norm(a.code), a.name or "", "BUY", float(price), int(vol),
                        note=(getattr(a, "note", "") or ""),
                        source=a.source) \
        if log_submitted else None
    time.sleep(1.5)
    # 成交验证：持仓差>0 视为成交（挂单未成交则持仓不变→executed=False，待成交后补录归因）
    pos_after = ws.positions().get("data", {}).get("positions", [])
    held = next((x for x in pos_after if norm(x.get("code", "")) == norm(a.code)), None)
    qty_after = int(held.get("quantity", 0)) if held else 0
    filled = max(0, qty_after - qty_before)
    result["order_resp"] = order
    result["executed"] = filled > 0
    result["filled_qty"] = filled
    result["qty_after"] = qty_after
    if not result["executed"]:
        # O19: 限价委托未即时成交 → 未决台账 OPEN 行, 由 fill_confirm_monitor 轮询回填
        if log_open:
            log_open(norm(a.code), a.name or "", "BUY", float(price), int(vol), qty_before, oid,
                     source=a.source)
        result["order_id"] = oid
        result["fill_status"] = "PENDING_CONFIRM"

    if result["executed"]:
        today = datetime.now().strftime("%Y%m%d")
        now = datetime.now().isoformat(timespec="seconds")
        # 1) trade_state
        record_buy(a.code)
        # 2) attribution.trade_records
        conn = sqlite3.connect(ATTR_DB)
        # 2026-09-23 T8(幂等查重): 五元组命中=双轨重复记账, 跳过写入(duplicate skipped留痕)
        if _trade_records_duplicate(conn, norm(a.code), today, "BUY", price, filled):
            print(f"  ⚠️ trade_records duplicate skipped: {norm(a.code)} {today} "
                  f"BUY {price} x{filled}(幂等查重T8)")
        else:
            _cur = conn.execute(
                "INSERT OR IGNORE INTO trade_records (code,name,trade_date,action,price,volume,amount,"
                "strategy_version,signal_type,subsystems,market_env,sector,notes,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (norm(a.code), a.name or "", today, "BUY", price, filled, round(price * filled, 2),
                 today, _norm_signal_type(a.source), "trade_executor",
                 _market_env_now(), a.sector or "",
                 "trade_executor端到端" + _owner_approve_note(a), now))
            if _cur.rowcount == 0:
                # 20261011批4: T8 五元组查重已过但 INSERT OR IGNORE 仍吞行=其他约束冲突
                # 静默丢行(E32"台账零落库"同型), 必须留痕
                print(f"  ⚠️ trade_records INSERT OR IGNORE 被约束吞行(非T8路径): {norm(a.code)} {today} BUY")
        conn.commit()
        conn.close()
        # 3) dragon_quant.buy_tracker
        conn2 = sqlite3.connect(DQ_DB)
        conn2.execute(
            "INSERT INTO buy_tracker (code,name,buy_date,buy_price,buy_qty,signal_source,"
            "stop_loss_pct,status,verdict,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code,buy_date) DO UPDATE SET "
            "buy_qty=buy_tracker.buy_qty+excluded.buy_qty, "
            "buy_price=ROUND((buy_tracker.buy_price*buy_tracker.buy_qty"
            "+excluded.buy_price*excluded.buy_qty)"
            "/(buy_tracker.buy_qty+excluded.buy_qty),4), "
            "updated_at=excluded.updated_at, status='HOLD'",
            (norm(a.code), a.name or "", today, price, filled, a.source, -0.08, "HOLD", "PENDING", now))
        conn2.commit()
        conn2.close()
        # 4) 反馈闭环 sync (20261011批4: 此前 fire-and-forget, 失败仍写死成功字样=固定值冒充诊断)
        _fb = subprocess.run([sys.executable, r"D:\zcodesj\feed_feedback_tables.py", "sync"],
                             capture_output=True, text=True, encoding="utf-8",
                             env=dict(os.environ, PYTHONUTF8="1"))
        if _fb.returncode:
            result["recorded"] = (f"trade_state+trade_records+buy_tracker 落库OK, 但 feedback sync "
                                  f"rc={_fb.returncode}: {(_fb.stderr or _fb.stdout or '')[-150:]}")
        else:
            result["recorded"] = "trade_state + trade_records + buy_tracker + verification_trades"
    return result


def _c6_note_str(c6: dict | None) -> str:
    """C6 判定落库串（准则37）：板块退潮=强势/中性/退潮 + 申万L1 + 1d/5d 涨幅（T-1口径）。"""
    if not c6 or not c6.get("found"):
        return "C6板块判定=无数据(T-1)"
    p5 = c6.get("pct_5d")
    p5s = f"{p5:+.1f}%5d" if isinstance(p5, (int, float)) else "n/a5d"
    return (f"C6板块判定={c6['c6_verdict']}({c6['sector']} "
            f"{c6['pct_1d']:+.1f}%1d/{p5s},T-1)")


def decide_c6_halve(c6: Optional[dict], is_stop_loss: bool, pnl_pct: Optional[float],
                    no_c6_halve: bool = False) -> bool:
    """R-20260828-01 C6 板块强势趋势卖出减半判定（纯函数，可单测）。

    返回 True 表示应把趋势卖出量减半：c6 数据可得(found)且板块当日>3% 且
    非 -8% 硬止损(S-5豁免) 且未整体关闭(no_c6_halve)。数据缺失 fail-open 返回 False。
    """
    if no_c6_halve or not c6 or not c6.get("found"):
        return False
    if is_stop_loss:
        return False
    if pnl_pct is not None and pnl_pct <= -0.08:
        return False
    p1 = c6.get("pct_1d")
    return p1 is not None and float(p1) > 3.0


def _same_day_sell_open(code: str) -> str | None:
    """do_sell 提交幂等键（2026-09-10）：查该码未了结 SELL 委托。

    open_orders 的 OPEN/PARTIAL 行(fill_confirm cmd_expire 已确保陈化行按期过期,
    存活行=真在挂) + executor_orders 当日 SELL 提交。返回命中证据描述或 None。
    """
    from executor_order_ledger import EXEC_ORDERS, OPEN_ORDERS
    today = datetime.now().strftime("%Y%m%d")
    c6 = "".join(ch for ch in (code or "") if ch.isdigit())[-6:]

    def _rows(path):
        if not os.path.exists(path):
            return []
        rows = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        rows.append(json.loads(s))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return rows

    for r in _rows(OPEN_ORDERS):
        rc = "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())[-6:]
        if ((r.get("action") or "").upper() == "SELL" and rc == c6
                and (r.get("status") or "") in ("OPEN", "PARTIAL")):
            return f"open_orders {r.get('status')} @{r.get('price')}"
    for r in _rows(EXEC_ORDERS):
        rc = "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())[-6:]
        if ((r.get("action") or "").upper() == "SELL" and rc == c6
                and r.get("trade_date") == today
                and (r.get("status") or "OPEN") in ("OPEN", "PARTIAL")):
            return f"executor_orders {r.get('status')} @{r.get('price')}"
    return None


def _fetch_sell_guard_inputs(code6: str, price: float | None) -> dict:
    """S1(2026-09-10): 卖出前防卖飞 C1-C5 数据自动取数。

    此前 do_sell 只传 --code/--name/--hold-days/--horizon/--loss-pct 等, C1-C5 六类
    参数一个不传——pre_sell 防卖飞在唯一执行入口恒空转(guard_precheck_log price 列
    100% NULL 实证), 唯一生效面= cron LLM 手工传参(非确定)。接线后每次卖出必检:
      C1 PE/PB + C3 股息率 ← fundamental_data.daily_basic 最新行(pe_ttm/pb/dv_ttm)
      C2 净利变动 ← earnings_yjyg 业绩预告最新行(pct_change, 与白名单预告标注同源)
      C4 近5日主力净流入 ← em_flow_snapshot.flow_snapshot em_5d(与 R22 同源)
      C5 MA5/MA20 ← qfq 收盘序列(21 根)
    全部失败/缺失 fail-open: -8% 止损绝不被取数失败卡住, 仅 stderr 留痕。
    """
    out = {}
    import sqlite3 as _sq
    ts = ("sh" if code6.startswith(("6", "9", "5")) else "sz") + code6
    # daily_basic/fina 系列 ts_code 为带点后缀格式 601916.SH(与本地库实际一致)
    ts_dot = code6 + (".SH" if ts.startswith("sh") else ".SZ")
    # C1/C3
    try:
        con = _sq.connect(r"file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro",
                          uri=True, timeout=10)
        r = con.execute("SELECT pe_ttm, pb, dv_ttm FROM daily_basic WHERE ts_code=? "
                        "ORDER BY trade_date DESC LIMIT 1", (ts_dot,)).fetchone()
        con.close()
        if r:
            if r[0] and float(r[0]) > 0:
                out["pe"] = float(r[0])
            if r[1] and float(r[1]) > 0:
                out["pb"] = float(r[1])
            if r[2] and float(r[2]) > 0:
                out["dividend_yield"] = float(r[2])
    except Exception as e:
        print(f"[卖出链] C1/C3取数失败(fail-open): {type(e).__name__}: {e}", file=sys.stderr)
    # C2: 业绩预告净利变动幅度(与白名单中报预告标注同源; 预告幅度≠已实现同比, 提示语义)
    try:
        con = _sq.connect(r"file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro",
                          uri=True, timeout=10)
        r = con.execute("SELECT pct_change FROM earnings_yjyg WHERE code=? AND pct_change IS NOT NULL "
                        "ORDER BY ann_date DESC LIMIT 1", (code6,)).fetchone()
        con.close()
        if r and r[0] is not None:
            out["earnings_yoy"] = float(r[0])
    except Exception as e:
        print(f"[卖出链] C2取数失败(fail-open): {type(e).__name__}: {e}", file=sys.stderr)
    # C4
    try:
        con = _sq.connect(r"file:D:\zcodesj\data\em_flow_snapshot.db?mode=ro", uri=True, timeout=10)
        r = con.execute("SELECT em_5d FROM flow_snapshot WHERE code=? "
                        "ORDER BY trade_date DESC LIMIT 1", (code6,)).fetchone()
        con.close()
        if r and r[0] is not None:
            out["net_inflow_5d"] = float(r[0])
    except Exception as e:
        print(f"[卖出链] C4取数失败(fail-open): {type(e).__name__}: {e}", file=sys.stderr)
    # C5: MA5/MA20(与 pre_sell 趋势判定同口径, qfq 唯一价格源)
    try:
        con = _sq.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro",
            uri=True, timeout=10)
        rows = [r[0] for r in con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? "
            "ORDER BY date DESC LIMIT 21", (code6,)).fetchall() if r[0]]
        con.close()
        if len(rows) >= 20:
            out["trend_ma5"] = round(sum(rows[:5]) / 5, 4)
            out["trend_ma20"] = round(sum(rows[:20]) / 20, 4)
    except Exception as e:
        print(f"[卖出链] C5取数失败(fail-open): {type(e).__name__}: {e}", file=sys.stderr)
    return out


def do_sell(a, ws: WeStockClient) -> dict:
    tg = _trading_session_gate(a, "SELL")
    if tg:
        return tg
    _nchk = check_name_consistency(a.code, a.name)
    if _nchk:
        return {"action": "SELL", "code": a.code, "name": a.name, "executed": False,
                "gate_verdict": "REJECT", "gate_rejects": [f"NAME-GATE: {_nchk}"],
                "reason": _nchk}
    positions = ws.positions().get("data", {}).get("positions", [])
    held = next((x for x in positions if norm(x.get("code", "")) == norm(a.code)), None)
    if not held or int(held.get("quantity", 0)) <= 0:
        return {"action": "SELL", "code": a.code, "executed": False, "reason": "无持仓"}
    qty = int(held.get("quantity", 0))
    cost = float(held.get("costPrice", 0))
    quote = flatten_quote(ws.quote(norm(a.code)).get("data", {}))
    price = a.price or quote.get("price")
    if not price:
        # 2026-08-24 P1修复: 行情获取失败且未传 --price 时此前 float(None) 崩溃；盲卖也违背铁律
        return {"action": "SELL", "code": a.code, "name": a.name, "executed": False,
                "reason": "无有效价格（行情获取失败且未传--price），拒绝盲卖，请显式传 --price"}
    # 2026-10-10 A1(主人批准): 事件驱动卖出挂价改 当日开盘×0.995 限价。
    # S-5(--is-stop-loss) 自动启用; 其他事件驱动(走坏提请/爆量哨)走 --event-driven-sell。
    # 开盘价缺失/非法 fail-open 回退现行为并 WARN 留痕; 后续 F1 市价下限校验照常兜底
    # (急杀时 F1 不触发——挂价 open×0.995 通常高于 市价×0.99; 反弹时 F1 上修反而卖更高)。
    is_event_sell = bool(getattr(a, "is_stop_loss", False)) or bool(getattr(a, "event_driven_sell", False))
    if is_event_sell and not a.price:
        _lp, _a1_info = calc_event_sell_limit_price(quote.get("open"))
        if _lp:
            price = _lp
            print(f"  [A1] 事件驱动卖出限价: 开盘{_a1_info['open']}×{_a1_info['ratio']} → 挂 {_lp}"
                  f"(10:00未成交转市价, 兜底=reprice建议制)")
        else:
            print("  ⚠️ [A1] 事件驱动卖出但开盘价缺失, 回退实时价追卖(fail-open留痕)")
    # 2026-09-05 F1: 卖出限价下限校验(与买侧BUY_MKT_CAP对称; 须在pnl_pct之前——
    # 上修后pnl反映真实让价, 消除挂低价触发的假-8%误报, 9/3焦煤被迫override同根因)。
    _mkt_s = quote.get("price")
    price, _floor_info = apply_sell_mkt_floor(price, _mkt_s)
    if _floor_info:
        print(f"  ⚠️ 卖出挂单价 {_floor_info['orig']} < 市价{_mkt_s}×{SELL_MKT_FLOOR} "
              f"→ 上修至 {_floor_info['floored']}(伪成交防护F1, 仍保证成交)")
    elif a.price and not _mkt_s:
        print("  ⚠️ 实时行情不可用, 卖出限价未经市价下限校验(F1跳过)")
    pnl_pct = (price / cost - 1) if (price and cost) else None

    result = {"action": "SELL", "code": a.code, "name": a.name, "held_qty": qty,
              "cost": cost, "price": price, "pnl_pct": round(pnl_pct, 4) if pnl_pct else None,
              "reason": a.reason}
    if _floor_info:
        result["price_floored_by_mkt"] = _floor_info
    if is_event_sell:
        # A1 留痕: 事件驱动卖出标志+兜底口径(reprice建议制), 台账note同步带 EVENT_SELL_NOTE_TAG
        result["event_sell_a1"] = {"enabled": True, "open": quote.get("open"),
                                   "limit_ratio": EVENT_SELL_LIMIT_RATIO,
                                   "fallback": "10:00未成交转市价(fill_confirm_monitor reprice建议制)",
                                   "open_missing_fallback": not bool(quote.get("open"))}

    # ---- C6 板块强度判定（2026-08-28 准则37: 卖出链必落C6判定; fail-open 不阻断）----
    # 数据源=sector_strength_lookup（申万L1 T-1口径，本地双库只读）。
    # 注意: pre_sell_check 的 --sector-pct 接线刻意不做——C6 触发即 verdict=HOLD 硬拦卖出,
    # 会与 14:30 链"强势板块减半仓"语义冲突，接线须主人裁决后另行落地；本段只做判定落库。
    c6 = None
    try:
        from sector_strength_lookup import lookup as _c6_lookup
        c6 = _c6_lookup(norm(a.code))
    except Exception as e:
        c6 = None
        print(f"[C6] 板块强度查询失败, 该检查跳过: {type(e).__name__} {e}")
    if c6 and c6.get("found"):
        result["c6_sector"] = c6["sector"]
        result["c6_verdict"] = c6["c6_verdict"]
        result["c6_pct_1d"] = c6["pct_1d"]
        result["c6_pct_5d"] = c6["pct_5d"]

    # ---- R-20260828-01: C6 板块强势 → 趋势卖出量减半（主人2026-08-28授权接线）----
    # 语义: R5/趋势卖出在板块当日>3%时由清仓降级为减半仓(防踏空, 8/24踏空6笔共性=板块未退潮);
    # -8%硬止损(S-5)豁免不降(铁律2); 数据缺失 fail-open 不降; --no-c6-halve 可整体关闭。
    # 与 pre_sell --sector-pct 硬拦(HOLD)语义区分: 本处是量级降级, 非拦截。
    c6_halve = decide_c6_halve(c6, is_stop_loss=getattr(a, "is_stop_loss", False),
                               pnl_pct=pnl_pct, no_c6_halve=getattr(a, "no_c6_halve", False))
    if c6_halve:
        result["c6_halve"] = True
        result["c6_halve_note"] = (f"板块强势 {c6.get('sector')} 当日{float(c6['pct_1d']):+.1f}%>3% "
                                   f"→ 趋势卖出减半(防踏空 R-20260828-01)")

    # ---- 卖出前校验 pre_sell_check（C1-C9）----
    sell_check = None
    try:
        sc_cmd = [sys.executable, PRE_SELL, "--code", norm(a.code), "--name", a.name or ""]
        # ---- C7-R28 持有期标签自动补全（2026-08-24 分层守卫，失败不阻断）----
        try:
            from holding_horizon import query_position
            _pos = query_position(norm(a.code))
            if _pos["found"]:
                if _pos["hold_days"] is not None:
                    sc_cmd += ["--hold-days", str(_pos["hold_days"])]
                if _pos["horizon"] in ("long", "short"):
                    sc_cmd += ["--horizon", _pos["horizon"]]
        except Exception as e:
            print(f"[卖出链] 持仓属性读取失败, hold-days/horizon 参数缺失: {type(e).__name__} {e}")
        # ---- S1(2026-09-10): C1-C5 防卖飞数据自动取数接线(此前六类参数零透传, 防卖飞
        # 在唯一入口空转; 取数失败 fail-open 绝不阻断 -8% 止损) ----
        try:
            _gi = _fetch_sell_guard_inputs(norm(a.code)[2:], price)
            for _k, _flag in (("pe", "--pe"), ("pb", "--pb"),
                              ("dividend_yield", "--dividend-yield"),
                              ("earnings_yoy", "--earnings-yoy"),
                              ("net_inflow_5d", "--net-inflow-5d"),
                              ("trend_ma5", "--trend-ma5"), ("trend_ma20", "--trend-ma20")):
                if _gi.get(_k) is not None:
                    sc_cmd += [_flag, f"{_gi[_k]:.4f}"]
            # L1 同批: --price 透传(guard_precheck_log price 列 100% NULL 修复)
            if price:
                sc_cmd += ["--price", f"{float(price):.4f}"]
        except Exception as e:
            print(f"[卖出链] C1-C5取数异常(fail-open): {type(e).__name__}: {e}", file=sys.stderr)
        if pnl_pct is not None:
            sc_cmd += ["--loss-pct", f"{pnl_pct:.4f}"]
        if getattr(a, "owner_emergency", False):
            sc_cmd += ["--owner-emergency"]
        if getattr(a, "is_stop_loss", False):
            # 2026-09-05 A案(主人裁决): S-5止损单守卫放行留痕——pre_sell对显式声明
            # 且亏损达线的卖单按宪法铁律2返回SELL_OK(holds转overridden_holds审计);
            # 执行端HOLD豁免分支保留为第二道防线(旧版守卫/异常路径兜底)。
            sc_cmd += ["--stop-loss"]
        if getattr(a, "owner_override_hold", False):
            sc_cmd += ["--owner-override-hold"]  # 2026-08-29 P0-E: 主人显式授权卖出(豁免建议性保留)
        try:
            sh_idx = flatten_quote(ws.quote("sh000001").get("data", {}))
            mkt_pct = sh_idx.get("change_percent") or sh_idx.get("changePercent")
            if mkt_pct is not None:
                sc_cmd += ["--market-pct", str(mkt_pct)]
        except Exception:
            pass  # 大盘数据获取失败则C9静默跳过（fail-open）
        # ---- C8 高位退潮自动数据（2026-08-23复盘线索；提示性，失败静默不阻断）----
        try:
            import sqlite3 as _sq
            from datetime import datetime as _dt
            _ad = _sq.connect(r"D:\zcodesj\attribution\attribution.db", timeout=5)
            _r = _ad.execute(
                "SELECT limit_count FROM limit_up_patterns WHERE trade_date=? AND code=? "
                "ORDER BY id DESC LIMIT 1",
                (_dt.now().strftime("%Y%m%d"), norm(a.code)[2:])).fetchone()
            _ad.close()
            if _r and int(_r[0]) >= 3:
                sc_cmd += ["--boards", str(int(_r[0]))]
        except Exception:
            pass
        try:
            import sqlite3 as _sq
            _qfq = _sq.connect(
                r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro",
                uri=True, timeout=10)
            _rows = _qfq.execute(
                # 2026-09-10 M6: LIMIT 22→21(原 22 根时 rows[-1]=第 22 根=21 个交易日前,
                # 20 日涨幅窗口漂移 1 天; 21 根时 rows[-1]=rows[20]=恰 20 日前)
                "SELECT close FROM stock_daily_meta_qfq WHERE code=? "
                "ORDER BY date DESC LIMIT 21", (norm(a.code)[2:],)).fetchall()
            _qfq.close()
            if len(_rows) >= 21 and float(_rows[-1][0]) > 0:
                _c20 = (float(_rows[0][0]) / float(_rows[-1][0]) - 1) * 100
                if _c20 >= 30:
                    sc_cmd += ["--chg-20d", f"{_c20:.1f}"]
        except Exception:
            pass
        sc_proc = subprocess.run(sc_cmd, capture_output=True, text=True,
                                 encoding="utf-8", timeout=60, shell=False,
                                 env=dict(os.environ, PYTHONUTF8="1"))
        sell_check = json.loads(sc_proc.stdout)
    except Exception as e:
        # (2026-08-25 主人裁决1A) fail-closed: pre_sell_check 崩溃时拒绝盲卖, 不再默认 SELL_OK 放行;
        # -8% 硬止损豁免(铁律2: S-5 触发即执行不等待)
        if a.is_stop_loss and pnl_pct is not None and pnl_pct <= -0.08:
            sell_check = {"verdict": "SELL_OK",
                          "error": f"pre_sell_check异常但-8%止损豁免执行: {type(e).__name__} {e}"}
        else:
            return {"action": "SELL", "code": a.code, "name": a.name, "held_qty": qty,
                    "cost": cost, "price": price,
                    "pnl_pct": round(pnl_pct, 4) if pnl_pct else None,
                    "executed": False,
                    "reason": f"pre_sell_check异常fail-closed(拒绝盲卖): {type(e).__name__} {e}"}
    result["sell_check"] = sell_check
    if sell_check and sell_check.get("hold_reasons"):
        result["sell_hold_warnings"] = sell_check["hold_reasons"]
    if sell_check and sell_check.get("risk_hints"):
        result["sell_risk_hints"] = sell_check["risk_hints"]
    # (2026-08-25 主人裁决1A) HOLD 硬拦截: C1-C9 建议保留的票(低估值/高股息/业绩拐点/趋势完好)
    # 禁止 --execute --confirmed 直接卖飞; -8% 硬止损豁免。与 guarded_trade_executor 语义对齐。
    if sell_check and sell_check.get("verdict") == "HOLD":
        if a.is_stop_loss and pnl_pct is not None and pnl_pct <= -0.08:
            print("  🚨 -8%硬止损豁免HOLD拦截: S-5触发即执行(铁律2)")
        else:
            result["executed"] = False
            result["reason"] = ("防卖飞拦截(pre_sell HOLD): "
                                f"{sell_check.get('hold_reasons', [])}")
            return result

    # 四检查（信息不足时脚本返回可卖，但需主人确认兜底）
    sell_qty = a.volume or qty
    if c6_halve and not a.volume:  # 板块强势降半(仅未显式指定volume时; 显式volume尊重调用方)
        sell_qty = max(100, int(qty // 2 // 100 * 100))
        result["c6_halve_applied"] = f"卖出量 {qty}→{sell_qty} 股"
    # 2026-09-10: 卖出侧整手/负数/超持仓校验(与买入侧对称)——此前 volume 为负/零/
    # 非整手会直传 ws.sell 裸奔(买入侧有整手+预算双闸, 卖出侧不对称缺陷)
    try:
        sell_qty = int(sell_qty)
    except (TypeError, ValueError):
        result["executed"] = False
        result["reason"] = f"卖出量非法(非整数): {a.volume!r}"
        return result
    if sell_qty <= 0:
        result["executed"] = False
        result["reason"] = f"卖出量非法(<=0): {sell_qty}"
        return result
    sell_qty = min(sell_qty, qty)          # 不得超过持仓
    # 2026-09-10 M4: 碎股随整手一次全清——持仓非整百(如 550)时整手化留 50 股残尾
    # 永久清不掉(A股零股须一次性卖出); 仅意图全清(卖量=持仓)时生效, 减仓不动
    _want_full = sell_qty >= qty
    sell_qty = sell_qty // 100 * 100       # 整手对齐
    if _want_full and 0 < qty - sell_qty < 100:
        sell_qty = qty
    if sell_qty <= 0:
        result["executed"] = False
        result["reason"] = "整手化后不足 1 手, 拒绝卖出"
        return result
    if not a.execute:
        result["executed"] = False
        result["reason_note"] = f"dry-run（未加 --execute --confirmed）。将卖 {sell_qty}股 @{price}"
        return result
    if not a.is_stop_loss and not a.confirmed:
        result["executed"] = False
        result["reason_note"] = "卖出需 --confirmed（卖出三铁律：卖多少必须问主人）"
        return result
    # [已删除] 非尾盘窗口拒单 — 主人8/14裁决：任何时间可以卖出（见 s4-fabricated-rule-correction）

    # 2026-09-10 M2: 卖出侧提交幂等键(与买侧 _same_day_buy_submitted 对称)——同码当日
    # 已有未了结 SELL 委托(open_orders OPEN/PARTIAL)或当日 SELL 提交即拒, 防重复/并发
    # --execute 超卖(上海银行 9/7 同码同日两条 SELL 先例; stop_loss 侧已有同类守卫,
    # 本入口自身无)。显式 --allow-resell 豁免(与 --allow-add-position 同构)。
    _pending_sell = _same_day_sell_open(norm(a.code))
    if _pending_sell and not getattr(a, "allow_resell", False):
        result["executed"] = False
        result["reason"] = f"卖出提交幂等拦截: 当日已有未了结卖出委托({_pending_sell}), 拒绝重复挂单"
        print(f"  ⛔ {result['reason']}")
        return result

    order = ws.sell(norm(a.code), float(price), int(sell_qty))
    # 卖出侧提交失败检查(20261011批2, 与买侧20260913检查对称): 服务端拒绝时 ws.sell 返回
    # {"raw": "提交委托失败: ..."} 无order字段——旧代码不检查→sold=0→照写幽灵OPEN台账,
    # _same_day_sell_open 反过来封锁当日重试卖出(含 S-5 止损重试须 --allow-resell 才放行),
    # 且 rc=0 静默吞错。现在: 提交失败 → 不写任何台账、SUBMIT_FAIL 返回(main 统一判 rc=2),
    # 当日重试通道保持畅通。
    _raw_sell = str((order or {}).get("raw", ""))
    if "失败" in _raw_sell or "拒绝" in _raw_sell or (order or {}).get("ok") is False:
        result["executed"] = False
        result["submit_failed"] = True
        result["gate_verdict"] = "SUBMIT_FAIL"
        result["submit_error"] = _raw_sell[:200]
        print(f"  X 卖出提交失败: {_raw_sell[:150]}")
        return result
    # O19(2026-08-26 E45): 委托提交即登记台账——异步成交归属判定唯一依据(order_id 幂等)
    # 2026-08-28 准则43/E72: note 携带卖出理由+C6判定——异步回报/盘后导入恢复 notes 的唯一来源
    # （此处 sold 未返回，执行口径按委托意图量 sell_qty 记；即时成交路径的 _exec_tag 不受影响）
    _sell_note = (f"理由={a.reason}|{_c6_note_str(c6)}"
                  f"|执行={'全清' if sell_qty >= qty else f'减仓{sell_qty}/{qty}股'}")
    if is_event_sell:
        # A1: 兜底识别标志(fill_confirm_monitor 对含本标记的未决 SELL 在 ≥10:00 升级转市价建议)
        _sell_note += f"|{EVENT_SELL_NOTE_TAG}|兜底=10:00未成交转市价(建议制)"
    oid = log_submitted(order, norm(a.code), a.name or "", "SELL", float(price),
                        int(sell_qty), note=_sell_note, source=a.source) if log_submitted else None
    time.sleep(1.5)
    # 2026-08-24 P1修复: 成交验证用持仓差（与买入侧对称）——挂单未成交持仓不变，
    # executed=False 不落库；成交后由盘后补录（待成交单语义与买入侧一致）。
    pos_after = ws.positions().get("data", {}).get("positions", [])
    held_after = next((x for x in pos_after if norm(x.get("code", "")) == norm(a.code)), None)
    qty_after = int(held_after.get("quantity", 0)) if held_after else 0
    sold = max(0, qty - qty_after)
    result["order_resp"] = order
    result["sold_qty"] = sold
    result["qty_after"] = qty_after
    # 2026-11-05 主人令「卖出要通知我」: 每笔卖出提交即桌面弹窗+通知日志(notify.py
    # 既有合规通道: 桌面toast+reports/notifications.log; 手机推送9/8裁决禁止不变)。
    # 无论即时成交还是挂单待确认, 提交即通知——通知不依赖成交回填, 尽力而为不阻断卖出链。
    try:
        import notify as _notify
        _notify.send_toast(
            "屠龙卖出 %s" % (a.name or norm(a.code)),
            "%d股 @%.2f | %s" % (sell_qty, float(price), str(a.reason or "")[:60]))
        _notify.write_log("warn", "卖出通知 %s" % (a.name or norm(a.code)),
                          "%d股 @%.2f | %s" % (sell_qty, float(price), str(a.reason or "")[:80]),
                          "sell_notify", True)
    except Exception as _ne:
        print(f"  ⚠️ 卖出通知失败(不影响交易): {type(_ne).__name__}", file=sys.stderr)
    if sold <= 0:
        result["executed"] = False
        result["reason_note"] = "挂单未确认（持仓未即时减少），已登记未决台账待回报回填"
        # O19: 未决台账 OPEN 行，fill_confirm_monitor 持仓差轮询回填 SELL 成交
        # （note 同 _sell_note 全量口径，open_orders→回报回填链可恢复完整理由+C6）
        if log_open:
            log_open(norm(a.code), a.name or "", "SELL", float(price), int(sell_qty),
                     int(qty), oid, note=_sell_note, source=a.source)
        result["order_id"] = oid
        result["fill_status"] = "PENDING_CONFIRM"
        return result
    record_sell(a.code)
    conn = sqlite3.connect(ATTR_DB)
    now = datetime.now().isoformat(timespec="seconds")
    _exec_tag = "全清" if sell_qty >= qty else f"减仓{sold}/{qty}股"
    # 2026-08-29 P0-E: 主人显式授权卖出时, 把被豁免的 pre_sell 保留理由全量落台账(豁免依据)
    _override_note = ""
    _ov = (sell_check or {}).get("overridden_holds") if isinstance(sell_check, dict) else None
    if _ov:
        _override_note = f" | 主人override pre_sell HOLD({len(_ov)}条: {'; '.join(str(x)[:40] for x in _ov[:3])})"
    # 2026-09-23 T8(幂等查重): 五元组命中=双轨重复记账, 跳过写入(duplicate skipped留痕)
    _tday = datetime.now().strftime("%Y%m%d")
    if _trade_records_duplicate(conn, norm(a.code), _tday, "SELL", price, sold):
        print(f"  ⚠️ trade_records duplicate skipped: {norm(a.code)} {_tday} "
              f"SELL {price} x{sold}(幂等查重T8)")
    else:
        conn.execute(
            "INSERT INTO trade_records (code,name,trade_date,action,price,volume,amount,"
            "strategy_version,signal_type,subsystems,market_env,sector,notes,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (norm(a.code), a.name or "", _tday, "SELL", price, sold,
             round(price * sold, 2), _tday,
             _norm_signal_type(a.source or "manual_confirmed"),
             "trade_executor",
             _market_env_now(), a.sector or "",
             f"卖出:{a.reason} | {_c6_note_str(c6)} | 执行={_exec_tag}{_override_note}", now))
    conn.commit()
    conn.close()
    # PD-017(2026-09-23): 即时成交分支此前从不回写台账状态——46条SELL行OPEN陈化实证(复盘
    # =reports/s5_rc1_postmortem_20260923.md)。纯簿记不影响交易语义;失败不阻断下单主流程
    # (成交真源=westock+attribution.db, 本回写仅台账卫生)。历史陈化行按prereg保护冻结不改。
    if oid and mark_order_status:
        try:
            _st = "FILLED" if sold >= sell_qty else "PARTIAL"
            if not mark_order_status(oid, _st, f"即时成交(持仓差验证sold={sold}/{sell_qty}), 状态回写PD-017"):
                print(f"  ⚠️ 台账状态回写未命中 order_id={oid}", file=sys.stderr)
        except Exception as _e:
            print(f"  ⚠️ 台账状态回写失败(不阻断): {_e}", file=sys.stderr)
    result["executed"] = True
    return result


ROOT_DIR_FOR_BATCH_REPLAN = r"D:\zcodesj\data"  # 被闸拦单自动复挂预案落盘目录(2026-11-06 复盘改进)

BUY_ARG_FIELDS = ("code", "name", "source", "sector", "price", "volume", "amount", "budget",
                  "execute", "prev_close", "pct_60d", "amplitude_40d", "sector_breadth",
                  "intent", "allow_add_position", "limit_price")
                  # 20261103断根(主人令「硬编码主人批准清零」): owner_approve_ddgate/owner_approve_p2/
                  # owner_approve_r31/owner_override_session 四字段移出计划文件透传清单——计划文件
                  # 不得携带主人批准标志(10/28-11/3五波越权买入根因=生成脚本预印批准章经本通道自授权)。
                  # 批准唯一合法通道=CLI 显式旗标(主人令在下单时刻由执行会话传入)或 manual_confirmed。
                  # 含批准键的计划条目整条拒绝(fail-loud, _ns_from_entry 抛 ValueError)。
                  # 历史: 20261026 P2批量透传 / 20261027 r31批量透传 / 20261028 盘前SESSION透传均退役。
_OWNER_ONLY_PLAN_KEYS = ("owner_approve_ddgate", "owner_approve_p2",
                         "owner_approve_r31", "owner_override_session")


def _ns_from_entry(entry: dict, execute: bool) -> argparse.Namespace:
    """把批量计划 JSON 条目转成与 buy 子命令等价的参数对象。未知键忽略。
    20261103断根: 含主人批准键(_OWNER_ONLY_PLAN_KEYS)的条目直接抛错——计划文件无权携带批准。"""
    present = [k for k in _OWNER_ONLY_PLAN_KEYS if k in entry]
    if present:
        raise ValueError(
            f"计划条目携带主人批准键 {present} —— 批准不得写进计划文件(2026-11-03主人令断根)。"
            f"如需豁免请删掉这些键, 由执行会话持主人令在 CLI 显式传旗标")
    ns = argparse.Namespace()
    for f in BUY_ARG_FIELDS:
        v = entry.get(f)
        if f == "execute":
            v = execute
        if f == "allow_add_position":
            v = bool(v)
        if v is None:
            v = "" if f in ("name", "source", "sector", "intent") else None
        setattr(ns, f, v)
    return ns


def _gate_inputs_preflight(codes: list[str]) -> dict | None:
    """O24: 批量下单前对 R19/R22 输入数据做新鲜度/覆盖度预检。失败返回 None(fail-open 但留痕)。"""
    try:
        cmd = [sys.executable, PREFLIGHT_INPUTS, "--json"]
        if codes:
            cmd += ["--codes", ",".join(dict.fromkeys(codes))]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60,
                              env=dict(os.environ, PYTHONUTF8="1"))
        if proc.returncode in (0, 1) and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception:
        return None
    return None


def do_buy_batch(path: str, ws: WeStockClient, execute: bool,
                 require_fresh_inputs: bool = False,
                 owner_high_hang_pct: float | None = None,
                 owner_approve_zone: bool = False,
                 owner_approve_prescreen: bool = False,
                 owner_approve_paper_obs: bool = False,
                 owner_approve_admission: bool = False,
                 owner_approve_r37: bool = False,
                 owner_direct_buy: bool = False,
                 owner_approve_r30a: bool = False,
                 owner_approve_r31: bool = False,
                 owner_approve_crash_dip: bool = False,
                 owner_hang_open_pct: float | None = None) -> dict:
    """O1 批量买入强制 executor 循环（2026-08-26 自省 E31 收口）。

    背景: 8/24-25 批量买入经"另一条链路"完成——名单/闸门语义合规但 executor 未参与,
    limit_price 失效、trade_records 靠盘后 reconcile 补录。本入口把批量计划收敛为
    逐笔 do_buy 端到端(归因层→闸门→下单→成交验证→双库落库), 顺序执行使持仓数
    逐笔刷新(R3/铁律3 实时生效)。

    计划文件格式: JSON 数组, 每项字段与 buy 子命令同名(code/name/source 必填,
    source 禁止 manual_confirmed 批量默认——特批须逐笔单独执行)。
    """
    try:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
    except Exception as e:
        return {"action": "BUY_BATCH", "file": path, "error": f"计划文件读取失败: {e}",
                "executed": False}
    if not isinstance(plan, list) or not plan:
        return {"action": "BUY_BATCH", "file": path, "error": "计划必须为非空 JSON 数组",
                "executed": False}

    # ---- O24(2026-08-27 E48): 批量前预检 R19/R22 输入数据就绪度 + 时序倒挂防御 ----
    batch_meta: dict = {}
    preflight = _gate_inputs_preflight([str(e.get("code", "")) for e in plan])
    if preflight:
        degr = [c for c in preflight.get("checks", []) if c.get("status") != "OK"]
        batch_meta["preflight_gate_inputs"] = {"verdict": preflight.get("verdict"),
                                               "degraded": [c.get("gate") for c in degr]}
        if require_fresh_inputs and preflight.get("verdict") != "READY":
            return {"action": "BUY_BATCH", "file": path,
                    "error": f"--require-fresh-inputs 且闸门输入 DEGRADED: "
                             f"{batch_meta['preflight_gate_inputs']['degraded']} → 拒绝批量(fail-closed)",
                    "preflight": preflight, "executed": False}
    else:
        # O24 docstring 承诺"留痕"的兑现(2026-09-07 吞错审计): 预检不可用必须留痕, 不得静默跳过
        batch_meta["preflight_gate_inputs"] = {"verdict": "UNAVAILABLE",
                                               "note": "预检子进程失败/无输出 → fail-open(留痕)"}

    # ---- 批量计划价对账(原白名单价差 REJECT, 2026-09-24 主人令「全面废除TopN名单限制」退役) ----
    # 原科森整改防线(计划价 vs 最新白名单价>3% → REJECT)随名单制废除整体移除——白名单不再是
    # 任何判定的输入。防陈旧计划残留机制=下方 plan_age_minutes 提示 + do_buy 单笔闸
    # (R27b 昨收溢价 WARN / R28 hotspot 追价 REJECT / R31 ext20 / R32 chg20, 均名单无关)。

    # ---- 2026-11-06 复盘改进(主人令「改」): 崩盘日低吸一键模式展开(行情类闸特批一次到位; R25/R39已退役, 现清单=R30a/R31/P2) ----
    if owner_approve_crash_dip:
        # 2026-11-06 R25/R39 已随并行退役批删除; P2 未退役(主人未点名)仍现役——一键清单=R30a/R31/P2
        owner_approve_r30a = True
        owner_approve_r31 = True
        owner_approve_p2 = True
        batch_meta["crash_dip_mode"] = ("主人令崩盘日低吸一键模式: 行情类闸 R30a/R31/P2 特批已展开"
                                        "(R25/R39 已退役; 准入类与区域闸不受影响, 逐闸WARN留痕)")

    results = []
    for i, entry in enumerate(plan):
        code = str(entry.get("code", "")).strip()
        name = str(entry.get("name", "")).strip()
        src = str(entry.get("source", "")).strip()
        if not code or not name:
            results.append({"idx": i, "code": code, "skipped": True,
                            "reason": "缺 code/name（铁律: 代码必带名称）"})
            continue
        if src == "manual_confirmed":
            results.append({"idx": i, "code": code, "name": name, "skipped": True,
                            "reason": "manual_confirmed 主人逐笔特批不得进批量计划, 请单独执行"})
            continue
        # ---- 2026-11-04 主人令「选股必须过买卖点/买卖区域判定层, 过了才能买」(断根焊接):
        # 批量每笔强制现跑 entry_exit_gate 判定层(不允许计划自带结论, 防纸面标注)。
        # NO_TRADE/WAIT -> 该笔跳过不下单; BUY_ZONE -> 限价强制=买区上限(回踩接,不追);
        # CHASE_OK -> 限价强制=判定层追买限价(现价x1.005, 实验); 判定层任何异常 -> 该笔跳过
        # (fail-safe: 无判定不下单), 不中断整批。数据缺失不允许"先买了再补判定"。
        # ---- 断根键检查前置(2026-11-05 全量测试揪出的排序缺陷): 批准键条目整条拒绝的
        # 合同优先级最高, 必须先于判定层——否则判定层不过的批准键条目会被"跳过"而非"拒绝"。
        _present_keys = [k for k in _OWNER_ONLY_PLAN_KEYS if k in entry]
        if _present_keys:
            results.append({"idx": i, "code": code, "name": name, "skipped": True,
                            "error": ("ValueError: 计划条目携带主人批准键 %s —— 批准不得写进计划文件"
                                      "(2026-11-03主人令断根); 如需豁免请由执行会话持主人令在CLI显式传旗标"
                                      % _present_keys)})
            continue
        # ---- 2026-11-09 主人裁决直通(SOP三百一十二): 「选股环节过所有筛选, 第二天开盘
        # 不能有任何环节阻拦, 交易环节要快」——实验臂名单票(source=dip_*_v1, 选股夜终审层
        # 已过全闸+排雷)跳过盘中判定层与盘中重判, 直接按 现价×pct 挂单。
        # 保留=执行安全(资金/股数/幂等/台账/单笔R闸全家桶降WARN仍跑, 新个人性拒绝skip+通知)。
        if owner_direct_buy and str(entry.get("source", "")).startswith("dip_"):
            _sp = None
            try:
                _q = flatten_quote(ws.quote(norm(code)).get("data", {}))
                _sp = float(_q.get("price") or 0) or None
            except Exception:
                _sp = None
            if not _sp:
                results.append({"idx": i, "code": code, "name": name, "skipped": True,
                                "reason": "直通模式无实时价 fail-safe"})
                continue
            _px = round(_sp * owner_hang_open_pct, 2) if owner_hang_open_pct else round(_sp, 2)
            ns = _ns_from_entry(entry, execute)
            ns.price = _px
            ns.limit_price = _px
            ns.owner_high_hang_pct = owner_high_hang_pct
            ns.owner_approve_zone = True
            ns.owner_approve_prescreen = True
            ns.owner_approve_paper_obs = True
            ns.owner_approve_admission = True
            ns.owner_approve_r37 = True
            ns.owner_approve_r30a = True
            ns.owner_approve_r31 = True
            r = do_buy(ns, ws)
            r["idx"] = i
            r["direct_buy"] = True
            results.append(r)
            continue
        _eef_skip_reason = None
        _px = None
        _s = None
        try:
            if "eef_gate_mod" not in dir(do_buy_batch):
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location(
                    "eef_gate",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "analysis", "entry_exit_gate.py"))
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                do_buy_batch.eef_gate_mod = _mod  # type: ignore[attr-defined]
            _eef = do_buy_batch.eef_gate_mod
            _s = _eef.load_structure(code)
            _v = _eef.make_verdict(_s)
            _act = str(_v.get("action", ""))
            if _act in ("NO_TRADE", "WAIT"):
                _eef_skip_reason = "判定层=%s, 主人令『过了才能买』: 不下单" % _act
            elif owner_high_hang_pct:
                # 2026-11-06 盘中修正: 基准价优先取实时现价(ws.quote), qfq结构价盘中=昨收会
                # 产生过时挂价(stale_ref熔断同族); quote不可用时回退结构价。
                _sp = None
                _quote_err = ""
                try:
                    _q = flatten_quote(ws.quote(norm(code)).get("data", {}))
                    _sp = float(_q.get("price") or 0) or None
                except Exception as _qe:
                    _quote_err = "%s: %s" % (type(_qe).__name__, _qe)
                _sp = _sp or float(_s.get("price") or 0)
                if not _sp or _sp <= 0:
                    _eef_skip_reason = ("高挂pct无有效最新价(%s) -> 无判定不下单(fail-safe)"
                                       % (_quote_err or "行情字段缺失"))
                else:
                    # 2026-11-05 主人令「高挂1%」(CLI旗标=主人令唯一合法通道): 判定层放行的票
                    # 一律按 最新收盘×pct 挂价(覆盖买区上限/追买默认限价); P1-1豁免见 ns 透传;
                    # 结构闸拦的照旧不买。
                    _px = round(_sp * owner_high_hang_pct, 2)
            elif owner_hang_open_pct:
                # 2026-11-06 主人令「9点27看开盘多少钱, 挂单高挂1%, 涨停的就不买」:
                # 挂价基准=当日开盘价(09:25集合竞价撮合完成已可见), 挂开盘价×pct;
                # 开盘即涨停(开盘/昨收≥涨停线99.9%)=主人令直接剔除不买;
                # 开盘价缺失(未撮合/停牌)=fail-safe跳过; R7追高闸照常在do_buy现判(R39落刀闸已退役20261106)。
                _q = {}
                _quote_err = ""
                try:
                    _q = flatten_quote(ws.quote(norm(code)).get("data", {}))
                except Exception as _qe:
                    _quote_err = "%s: %s" % (type(_qe).__name__, _qe)
                _op = None
                for _k in ("open", "openPrice", "open_price", "today_open"):
                    if _q.get(_k):
                        try:
                            _op = float(_q[_k])
                            break
                        except (TypeError, ValueError):
                            pass
                if not _op or _op <= 0:
                    _eef_skip_reason = ("开盘价缺失(%s) -> 无开盘价不下单(fail-safe)"
                                       % (_quote_err or "未撮合/停牌"))
                else:
                    _pc = None
                    for _k in ("prev_close", "preClose", "preClosePrice", "pre_close"):
                        if _q.get(_k):
                            try:
                                _pc = float(_q[_k])
                                break
                            except (TypeError, ValueError):
                                pass
                    if _pc and _pc > 0:
                        _lim = 0.20 if code[:3] in ("300", "301") else 0.10
                        if _op / _pc - 1 >= _lim * 0.999:
                            _eef_skip_reason = ("开盘涨停(开盘%.2f vs 昨收%.2f = +%.1f%%) -> 主人令涨停不买"
                                                % (_op, _pc, (_op / _pc - 1) * 100))
                    if not _eef_skip_reason:
                        _px = round(_op * owner_hang_open_pct, 2)
            elif _act == "BUY_ZONE" and _v.get("buy_zone"):
                _px = round(float(_v["buy_zone"][1]), 2)          # 买区上限=回踩限价
            elif _act == "CHASE_OK" and _v.get("chase_limit"):
                _px = round(float(_v["chase_limit"]), 2)          # 追买限价=现价x1.005
            else:
                _eef_skip_reason = "判定层裁决无法定位限价 -> 无判定不下单(fail-safe)"
        except Exception as _e:
            _eef_skip_reason = ("判定层异常(%s: %s) -> 无判定不下单(fail-safe)"
                                % (type(_e).__name__, _e))
        if _eef_skip_reason:
            results.append({"idx": i, "code": code, "name": name, "skipped": True,
                            "reason": _eef_skip_reason})
            continue
        try:
            ns = _ns_from_entry(entry, execute)   # 断根: 批准键条目此处整条 ValueError 拒绝
            ns.owner_high_hang_pct = owner_high_hang_pct  # 2026-11-05 高挂批: P1-1豁免透传
            ns.owner_approve_zone = owner_approve_zone    # 2026-11-05 原计划令: 位置池闸特批透传
            ns.owner_approve_prescreen = owner_approve_prescreen  # 2026-11-05 七法28单主人令: 回测预筛特批透传(ml_signal/f_arm_replica在预筛黑名单, 主人当日明令买)
            ns.owner_approve_paper_obs = owner_approve_paper_obs  # 2026-11-07 模拟盘观察闸特批透传(method_picks/manual_confirmed)
            ns.owner_approve_admission = owner_approve_admission  # 2026-11-06: P0-A准入特批透传(两集不重叠合同下黑名单源的合规买入通道)
            ns.owner_approve_r37 = owner_approve_r37  # 2026-11-07: R37弱档特批批量透传(补齐10/16半截落地, 与admission同构)
            ns.owner_hang_open_pct = owner_hang_open_pct  # 2026-11-06 主人令: 开盘价×pct高挂+开盘涨停剔除, P1-1豁免同高挂批
            # ns.owner_approve_r39/r25 透传已退役(2026-11-06 主人令废除两闸)。
            ns.owner_approve_r30a = owner_approve_r30a    # 2026-11-06 主人令: R30a行业集中度特批透传
            ns.owner_approve_r31 = owner_approve_r31      # 2026-11-06 主人令: R31偏离度特批透传
            if _px is not None:
                # 2026-11-05 23:50 修复(判定层焊接时序bug, 今晨焊入时插错位置):
                # 覆盖必须落在 ns 上(entry 双写发生在 _ns_from_entry 之后=对 ns 无效,
                # 判定层限价从未真正生效; 今晨批次无损纯因计划价恰好=高挂计算值)。
                entry["price"] = _px
                entry["limit_price"] = _px
                ns.price = _px
                ns.limit_price = _px
            r = do_buy(ns, ws)
            r["idx"] = i
            if owner_high_hang_pct:
                r["owner_high_hang"] = {"pct": owner_high_hang_pct, "limit": _px}
            results.append(r)
        except Exception as e:
            results.append({"idx": i, "code": code, "name": name, "executed": False,
                            "error": f"{type(e).__name__}: {e}"})

    n_ok = sum(1 for r in results if r.get("executed"))
    n_rej = sum(1 for r in results if r.get("gate_verdict") == "REJECT" or r.get("skipped"))
    # O19(2026-08-26 E45): executed_cnt 只计即时成交——限价异步单计入 submitted/pending,
    # "submitted=20, filled_now=0" ≠ 失败; 成交回报由 fill_confirm_monitor 异步确认回填。
    # executed 语义改为"提交层面干净"(无异常/无跳过/无闸门拒), 决定 exit code;
    # 即时成交率不再是成功判据(限价贴现单本就多数等待成交)。
    clean_submit = not any(r.get("error") or r.get("skipped")
                           or r.get("gate_verdict") in ("REJECT", "SUBMIT_FAIL")
                           for r in results)
    _xid = extract_order_id
    n_sub = sum(1 for r in results if _xid and isinstance(r.get("order_resp"), dict)
                and _xid(r.get("order_resp") or {}))
    out = {"action": "BUY_BATCH", "file": path, "mode": "execute" if execute else "dry-run",
           "total": len(plan), "submitted_cnt": n_sub, "filled_now_cnt": n_ok,
           "pending_confirm_cnt": sum(1 for r in results
                                      if r.get("fill_status") == "PENDING_CONFIRM"),
           "executed_cnt": n_ok, "rejected_or_skipped": n_rej,
           "results": results, "executed": clean_submit}
    # O24: 预检结果与计划时效提示(8/26 科森死单教训=计划早于名单终版构建)
    if batch_meta:
        out.update(batch_meta)
    try:
        _mtime = os.path.getmtime(path)
        age_min = (time.time() - _mtime) / 60
        out["plan_age_minutes"] = round(age_min, 1)
        if execute and age_min > 120:
            out["staleness_hint"] = (f"计划文件已生成 {age_min:.0f} 分钟——确认其价格仍是最新口径"
                                     f"(名单价差防线已随名单制废除, 陈旧计划靠单笔 R 闸+人工确认兜底)")
    except OSError:
        pass
    # ---- 2026-11-06 复盘改进(主人令「改」): 被闸拦单自动落复挂预案文件 ----
    # 当日教训: 09:30 被拦后现场补旗标+等令耗 15 分钟错过低吸窗口。此后被拦单自动写
    # auto_replan_pending 文件, 主人一句令即可零等待复挂(预案前置, 不再手搓计划)。
    if execute:
        _blocked = []
        _seen_codes = set()
        for _i, _r in enumerate(results):
            _c = str(_r.get("code") or "")
            if _r.get("gate_verdict") == "REJECT" and _c and _c not in _seen_codes:
                _seen_codes.add(_c)
                _blocked.append(plan[_i])
        if _blocked:
            _rp = os.path.join(ROOT_DIR_FOR_BATCH_REPLAN,
                               "auto_replan_pending_%s_%s.json" % (
                                   time.strftime("%Y%m%d"), time.strftime("%H%M")))
            try:
                for _e in _blocked:
                    _e["note"] = str(_e.get("note") or "") + " | 闸拦自动复挂预案(待主人令)"
                json.dump(_blocked, open(_rp, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                out["replan_file"] = _rp
                out["replan_hint"] = ("%d只被闸拦, 复挂预案已落盘 %s —— 主人下令后直接 "
                                      "buy-batch --file 该文件+crash-dip/对应特批旗标即零等待执行"
                                      % (len(_blocked), _rp))
            except OSError as _we:
                out["replan_error"] = "预案写盘失败: %s" % _we
    return out


def main() -> int:
    # 20261030 全流量测试修复: GBK控制台下 ⚠️类 print 直接 UnicodeEncodeError →
    # 整单被批量循环捕获 → executed:false → rc=2(10/26 Mon0941 全批0成交实证)。
    # stdout 非UTF-8时重配 utf-8/replace, 与 batch_exec_run.cmd v2 PYTHONIOENCODING 双保险。
    for _s in (sys.stdout, sys.stderr):
        try:
            if (_s.encoding or "").lower().replace("-", "") not in ("utf8",):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="屠龙端到端交易执行器")
    sub = p.add_subparsers(dest="mode", required=True)
    for name in ("buy", "sell"):
        sp = sub.add_parser(name)
        sp.add_argument("--code", required=True)
        sp.add_argument("--name", default="")
        # 2026-08-25: buy 侧默认值从 manual_confirmed 改空——默认特批会整体跳过 R26 名单制
        # (pre_trade_check_v2 只在 src!="manual_confirmed" 时校验白名单); 显式声明信号源
        # 是既有惯例(文档示例全带 --source)。sell 侧保持默认(卖出无名单制问题,signal_type
        # 落库另有 a.source or "manual_confirmed" 兜底)。
        sp.add_argument("--source", default="" if name == "buy" else "manual_confirmed")
        sp.add_argument("--note", default="",
                        help="买入理由留痕(manual_confirmed + --execute 必填, 20261015留痕闸)")
        sp.add_argument("--sector", default="")
        sp.add_argument("--price", type=float)
        sp.add_argument("--volume", type=int)
        sp.add_argument("--amount", type=float)
        sp.add_argument("--execute", action="store_true")
    sub.choices["buy"].add_argument("--prev-close", type=float)
    sub.choices["buy"].add_argument("--pct-60d", type=float)
    sub.choices["buy"].add_argument("--amplitude-40d", type=float)
    sub.choices["buy"].add_argument("--sector-breadth", type=float)
    sub.choices["buy"].add_argument("--intent", default="")
    sub.choices["buy"].add_argument("--allow-add-position", action="store_true")
    sub.choices["buy"].add_argument("--budget", type=float,
                                    help="O07意图预算(元): 下单金额硬顶=budget×1.5, "
                                         "超限截断股数(计划类脚本调用必带)")
    # 2026-08-25 O1: 白名单 limit_price(参考价×0.995)物理消费——挂单价上限, 超限截断不追价
    sub.choices["buy"].add_argument("--limit-price", type=float,
                                    help="挂单价上限(白名单limit_price列); 传入价超限时截断至该价")
    # 2026-10-07 修复: 本参数原用循环变量 sp 定义, 循环后 sp=sell → buy 侧盘后授权
    # 通道 CLI 不可达(9/2 起潜伏); 唯一消费点=do_buy 会话闸(L287), 移挂 buy。
    sub.choices["buy"].add_argument("--owner-override-session", action="store_true",
                                    help="owner显式授权盘后/盘前 --execute(越过09:30-15:00硬闸; "
                                         "stderr留SESSION-OVERRIDE痕; 默认关)")
    # 2026-09-06: 透传闸门R-S5特批豁免(--owner-approve-rs5在pre_trade_check_v2已定义,
    # executor此前未透传致会话源特批通道不可达; 闸门侧降WARN留痕逻辑已内置)
    sub.choices["buy"].add_argument("--owner-approve-rs5", action="store_true",
                                    help="主人特批豁免R-S5不追高闸(仅manual_confirmed会话源; "
                                         "闸门降WARN留痕; 默认关)")
    sub.choices["buy"].add_argument("--owner-approve-r6", action="store_true",
                                    help="主人特批豁免R6收在高点闸(闸门降WARN留痕; 默认关; "
                                         "2026-09-06与R-S5豁免同构)")
    # 2026-11-05 两级制第一级豁免(PD-053批准): 回测预筛黑名单源的主人逐笔特批通道
    sub.choices["buy"].add_argument("--owner-approve-prescreen", action="store_true",
                                    help="主人特批豁免回测预筛闸(回测无优势观察区源逐笔买入; "
                                         "非判伪, 判伪=模拟盘; 默认关)")
    sub.choices["buy"].add_argument("--owner-approve-paper-obs", action="store_true",
                                    help="主人特批豁免模拟盘观察闸(paper_trading_observations区源: "
                                         "method_picks/manual_confirmed 实盘无优势; 逐笔特批可放行, 默认关)")
    # 2026-11-05 主人令: 买卖区域闸豁免(选股后未过买卖点/区域判定而主人逐笔特批买入)
    sub.choices["buy"].add_argument("--owner-approve-zone", action="store_true",
                                    help="主人特批豁免买卖区域闸(标的不在最新全宇宙买卖区域扫描位置池"
                                         "或扫描陈旧时逐笔特批买入; 默认关)")
    # 2026-09-08: R31买价豁免(主人裁决开豁免口——追价买强势股逐笔特批; 闸门侧降WARN留痕已内置)
    sub.choices["buy"].add_argument("--owner-approve-r31", action="store_true",
                                    help="主人特批豁免R31买价偏离闸(ext20>=20%%追价买入逐笔特批; "
                                         "闸门降WARN留痕; 默认关)")
    # 2026-09-10: R30a行业集中度豁免(主人裁决开豁免口——同行业日新买上限逐笔特批; 闸门侧降WARN留痕已内置)
    sub.choices["buy"].add_argument("--owner-approve-r30a", action="store_true",
                                    help="主人特批豁免R30a行业集中度闸(同行业当日新买上限逐笔特批; "
                                         "闸门降WARN留痕; 默认关)")
    # 2026-10-16: R37情绪档弱档豁免(补齐10/15半截落地; 检查器argparse/调用侧已就绪; 闸门侧降WARN留痕)
    sub.choices["buy"].add_argument("--owner-approve-r37", action="store_true",
                                    help="主人特批豁免R37情绪档弱档禁买(涨停<40家逆风日; "
                                         "闸门降WARN留痕; 默认关; ddgate/r38/r39同构)")
    # 2026-10-08: R38期望闸豁免(主人授权「修复优化迭代」落地; 闸门侧降WARN留痕已内置)
    sub.choices["buy"].add_argument("--owner-approve-r38", action="store_true",
                                    help="主人特批豁免R38期望闸(LOW_VOL弱档×gauge组级负期望; "
                                         "闸门降WARN留痕; 默认关)")
    # 2026-11-06: --owner-approve-r39 特批口已退役(主人令废除 R39 落刀闸, 判定块已删)。
    # 2026-11-06: P0-A暂行准入特批(两集不重叠合同下黑名单源主人令买入; 与批量同名旗标同语义)
    sub.choices["buy"].add_argument("--owner-approve-admission", action="store_true",
                                    help="主人特批豁免P0-A暂行准入闸(退役/观察源不放行; "
                                         "闸门降WARN留痕; 默认关)")
    # 2026-11-06: --owner-approve-r25 特批口已退役(主人令废除 R25 大盘择时闸, 判定块已删)。
    # 2026-10-09: S-3价格铁律豁免(主人特批兴齐眼药35.40单批准; 闸门侧降WARN留痕已内置)
    sub.choices["buy"].add_argument("--owner-approve-price-cap", action="store_true",
                                    help="主人特批豁免S-3价格铁律(股价>30元上限逐笔特批; "
                                         "闸门降WARN留痕; 默认关)")
    # 2026-11-02: 归因层重复踩坑拦截主人特批(主人令「趋势改变是可以买的」现场裁决;
    # 逐笔WARN留痕+默认关; 三段式文档回写当晚补)
    sub.choices["buy"].add_argument("--owner-approve-attrib", action="store_true",
                                    help="主人特批豁免归因层重复踩坑拦截(历史重亏票主人裁决"
                                         "趋势改变可买; 逐笔WARN留痕; 默认关)")
    sub.choices["sell"].add_argument("--reason", default="")
    sub.choices["sell"].add_argument("--confirmed", action="store_true")
    sub.choices["sell"].add_argument("--is-stop-loss", action="store_true")
    # 2026-10-10 A1(主人「继续」令批准): 事件驱动卖出限价执行标志。
    # S-5止损(--is-stop-loss)自动启用无需本标志; 走坏提请/爆量哨等agent侧事件驱动卖出
    # 显式传入 → 挂价=当日开盘×0.995 + 10:00未成交转市价兜底(建议制)。默认关=现行为不变。
    sub.choices["sell"].add_argument("--event-driven-sell", action="store_true",
                                     help="A1事件驱动卖出限价执行(开盘×0.995挂价+10:00"
                                          "转市价兜底建议制; reports/limit_sell_plan_20261009.md)")
    # 2026-09-10 M2: 卖出提交幂等键豁免口(同码当日已有未了结卖单时显式允许再挂)
    sub.choices["sell"].add_argument("--allow-resell", action="store_true",
                                     help="卖出提交幂等豁免(同码当日已有未了结卖单时显式允许再挂, 默认拒)")
    # R-20260828-01(2026-08-28): 板块强势趋势卖出减半的可选项——显式关闭此防护
    sub.choices["sell"].add_argument("--no-c6-halve", action="store_true",
                                     help="关闭C6板块强势趋势卖出减半(默认开启; -8%%止损始终豁免)")
    # 2026-08-24 P2修复: do_sell 引用 owner_emergency 但 sell 子命令从未定义(恒False不可达)
    sub.choices["sell"].add_argument("--owner-emergency", action="store_true",
                                      help="主人紧急确认(激活 pre_sell C9/时间守卫紧急分支)")
    # 2026-08-29 P0-E: 主人显式授权卖出(豁免 pre_sell 建议性保留 HOLD)——additive, 默认关
    sub.choices["sell"].add_argument("--owner-override-hold", action="store_true",
                                      help="P0-E: 主人显式授权卖出, 传 pre_sell --owner-override-hold "
                                           "豁免 C1-C9 建议性保留(信号源漂移退出等主人裁决场景); "
                                           "默认关=防卖飞 HOLD 拦截不变; 豁免依据落台账")
    # O1 批量买入强制 executor 循环(2026-08-26 E31收口): 计划JSON逐笔端到端
    bb = sub.add_parser("buy-batch", help="批量买入逐笔走executor端到端(闸门+limit_price+落库)")
    bb.add_argument("--file", required=True, help="批量买入计划JSON(数组,字段与buy子命令同名)")
    bb.add_argument("--execute", action="store_true")
    # 2026-11-05 主人令「高挂1%」: 判定层放行的票按 最新收盘×pct 挂价(覆盖买区/追买默认限价);
    # 结构闸(NO_TRADE/WAIT拦)不变; P1-1执行价纪律(市价×1.005封顶)照常兜底(10/19先例机制)
    bb.add_argument("--owner-high-hang-pct", type=float, default=None,
                    help="主人令高挂百分比(如1.01): 判定层放行票按 最新收盘×pct 挂单")
    # 2026-11-05 主人令「快按原计划买入」: 批量透传两道闸的主人特批(ZONE-GATE位置池/R39落刀),
    # 闸门侧自降WARN留痕(单笔 buy 同名旗标既有语义, 批量=逐笔同参); 原计划含被此两闸拦过的票。
    bb.add_argument("--owner-approve-zone", action="store_true",
                    help="主人特批豁免买卖区域闸(位置池), 逐笔WARN留痕")
    bb.add_argument("--owner-approve-r30a", action="store_true",
                    help="主人特批豁免R30a行业集中度闸, 逐笔WARN留痕")
    bb.add_argument("--owner-approve-r31", action="store_true",
                    help="主人特批豁免R31偏离度闸, 逐笔WARN留痕")
    # 2026-11-06 复盘改进(主人令「改」): 崩盘日低吸一键模式=行情类五闸特批一次展开,
    # 主人令买入时不再现场补旗标(当日曾现场补r25/p2/r30a/r31耗约10分钟教训)。
    bb.add_argument("--owner-approve-crash-dip", action="store_true",
                    help="崩盘日低吸一键模式: 一旗标展开R30a/R31/P2三个行情类闸特批(R25/R39已退役)"
                         "(准入类prescreen/admission与区域闸zone仍需单独旗标); 台账逐闸留痕")
    # O24(2026-08-27 E48收口): 闸门输入数据前置预检; 默认 WARN 留痕, 加本开关转硬闸
    bb.add_argument("--require-fresh-inputs", action="store_true",
                    help="R19/R22 输入预检 DEGRADED 时拒绝整批(fail-closed)")
    # 2026-11-05 七法28单主人令: 回测预筛特批透传(ml_signal/f_arm_replica在预筛黑名单;
    # 主人当日明令买 = 特批依据, 台账侧 owner-approve 留痕自解释)
    bb.add_argument("--owner-approve-prescreen", action="store_true",
                    help="主人特批豁免回测预筛闸(PRESCREEN-GATE), 逐笔WARN留痕")
    bb.add_argument("--owner-approve-paper-obs", action="store_true",
                    help="主人特批豁免模拟盘观察闸(PAPER-OBS-GATE: method_picks/manual_confirmed), 逐笔WARN留痕")
    bb.add_argument("--owner-approve-admission", action="store_true",
                    help="主人特批豁免P0-A暂行准入闸(两集不重叠合同下黑名单源合规买入), 逐笔WARN留痕")
    # 2026-11-06 主人令「9点27看开盘, 高挂1%, 涨停的就不买」: 挂价=当日开盘价×pct;
    # 开盘即涨停剔除(主人令); 开盘价缺失fail-safe跳过; R39/R7等闸照常在do_buy现判。
    bb.add_argument("--owner-hang-on-open-pct", type=float, default=None,
                    help="主人令开盘高挂(如1.01): 挂价=当日开盘价×pct, 开盘涨停票剔除")
    bb.add_argument("--owner-approve-r37", action="store_true",
                    help="R37情绪档弱档豁免批量透传(2026-11-07补齐10/16半截落地: "
                         "单笔/检查器已就绪, 批量argparse缺失=拒绝文案指挥用户用不存在的标志)")
    bb.add_argument("--owner-direct-buy", action="store_true",
                    help="主人裁决2026-11-09(SOP三百一十二): 选股夜已过全部筛选的实验臂票"
                         "(dip_*_v1), 交易日开盘直通零阻拦(跳过盘中判定层与重判, 保留执行安全)")
    a = p.parse_args()

    ws = WeStockClient()
    if a.mode == "buy-batch":
        out = do_buy_batch(a.file, ws, a.execute,
                           require_fresh_inputs=getattr(a, "require_fresh_inputs", False),
                           owner_high_hang_pct=getattr(a, "owner_high_hang_pct", None),
                           owner_approve_zone=getattr(a, "owner_approve_zone", False),
                           owner_approve_prescreen=getattr(a, "owner_approve_prescreen", False),
                           owner_approve_paper_obs=getattr(a, "owner_approve_paper_obs", False),
                           owner_approve_admission=getattr(a, "owner_approve_admission", False),
                           owner_approve_r37=getattr(a, "owner_approve_r37", False),
                           owner_direct_buy=getattr(a, "owner_direct_buy", False),
                           owner_approve_r30a=getattr(a, "owner_approve_r30a", False),
                           owner_approve_r31=getattr(a, "owner_approve_r31", False),
                           owner_approve_crash_dip=getattr(a, "owner_approve_crash_dip", False),
                           owner_hang_open_pct=getattr(a, "owner_hang_on_open_pct", None))
    else:
        out = do_buy(a, ws) if a.mode == "buy" else do_sell(a, ws)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.mode == "buy-batch":
        return 0 if out.get("executed") or not a.execute else 2
    # 20261011批2: SUBMIT_FAIL 与 REJECT 一律 rc=2——此前提交失败落在 else 分支伪装 rc=0,
    # 下游 cron 按退出码驱动会把提交失败当成功(:613 注释承诺的 rc=2 实际从未兑现)。
    _verdict = out.get("gate_verdict")
    return 0 if out.get("executed") or not a.execute else (2 if _verdict in ("REJECT", "SUBMIT_FAIL") else 0)


if __name__ == "__main__":
    raise SystemExit(main())
