#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_name_lookup.py — 股票名称唯一合法取用接口
================================================
铁律（2026-08-15实战错误闭环产出）：
  任何脚本/分析需要股票名称，必须 import 本模块查询。
  禁止凭训练记忆硬编码A股名称——实测20%错误率
  （300319麦捷科技≠明阳电路 / 001260坤泰股份≠昆山漕河泾 / 300862蓝盾光电≠东方蓝天）。

用法：
  from stock_name_lookup import get_name, require_name
  name = get_name('300319')        # → '麦捷科技'；查不到返回 None
  name = require_name('300319')    # → '麦捷科技'；查不到直接抛异常（fail-closed）
  北交所 920xxx 新号段由 data/bj_920_names.json 补充(小石源, 2026-08-22 起)
  更名股覆盖由 data/name_overrides.json 补充(2026-09-15 起, 优先级最高:
  stock_info 表为静态快照无更名刷新链, 实证 002058 威尔泰→紫竹高科滞后;
  发现新的更名滞后时在此追加 code→现名 即可, 先例=bj_920_names.json)

CLI 验证单个：
  python stock_name_lookup.py 300319 002724 001260
"""
import sqlite3
import sys

_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
_cache: dict[str, str | None] = {}
_loaded = False

# 北交所 2026 新号段(920xxx)名称补充源: 小石(BJ全量331只)构建, 见 data/bj_920_names.json
# (stock_info 仅收录 8 开头旧码, 920xxx 为换码后新号段, 2026-08-22 补齐)
# 注: PROJECT_BOUNDARY §0 已剔除北交所, 此映射仅历史参考/防御性兜底, 不参与生产 universe
_BJ92_JSON = r'D:\zcodesj\data\bj_920_names.json'

# 更名股现名覆盖(2026-09-15 起): stock_info 为静态快照, 交易所侧更名后此处滞后
# (实证: 002058 威尔泰→紫竹高科, westock 实时+全量名称表双源确认)。
# 优先级最高, 直接覆盖 stock_info/bj92; 发现新的更名滞后在 name_overrides.json 追加。
_OVERRIDES_JSON = r'D:\zcodesj\data\name_overrides.json'


def _load_overrides() -> None:
    """加载更名股现名覆盖。失败不阻断(降级为旧行为)。直接赋值=覆盖一切前序来源。"""
    import json
    import os
    try:
        if os.path.exists(_OVERRIDES_JSON):
            with open(_OVERRIDES_JSON, encoding='utf-8') as f:
                for code, name in json.load(f).items():
                    _cache[str(code)] = name
    except Exception:
        pass


def _load_bj92() -> None:
    """加载北交所 920xxx 名称补充(小石源, 本地落盘)。失败不阻断(降级为空)。"""
    import json
    import os
    try:
        if os.path.exists(_BJ92_JSON):
            with open(_BJ92_JSON, encoding='utf-8') as f:
                for code, name in json.load(f).items():
                    _cache.setdefault(str(code), name)
    except Exception:
        pass


def _load() -> None:
    """一次性加载 code→name 映射（约5.5千条，毫秒级）+ 北交所920补充 + 更名覆盖。
    2026-09-16 起只加载 instrument_type='stock' 行（主人裁决#3 schema 方案）：
    stock_info 混命名空间存量（指数/债券/基金约6千行）不再进入名称缓存，
    根治"同数字码指数行顶替股票名"类冲突（000982=500等权 案）；列缺失时回退旧口径。"""
    global _loaded
    if _loaded:
        return
    with sqlite3.connect(f'file:{_DB}?mode=ro', uri=True) as c:
        try:
            rows = c.execute(
                "SELECT code, name FROM stock_info "
                "WHERE name IS NOT NULL AND instrument_type='stock'"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = c.execute(
                'SELECT code, name FROM stock_info WHERE name IS NOT NULL'
            ).fetchall()
        for code, name in rows:
            _cache[code] = name
    _load_bj92()
    _load_overrides()
    _loaded = True


def _norm(code: str) -> str:
    """归一化代码：剥离市场前缀（sh/sz/bj）与后缀。"""
    code = code.strip().lower()
    for p in ('sh', 'sz', 'bj'):
        if code.startswith(p):
            code = code[len(p):]
    code = code.split('.')[0]
    return code


def get_name(code: str) -> str | None:
    """查股票名称；查不到返回 None（调用方自行处理）。"""
    _load()
    return _cache.get(_norm(code))


_itype_cache: dict[str, str] = {}
_itype_loaded = False


def get_instrument_type(code: str) -> str | None:
    """查品种类型；无记录/NULL 返回 None（调用方自行处理，身份闸据此三分）。
    2026-11-08 新增（99指数码撞码污染事件防御，主人「继续」令批准品种闸立项）：
    独立于名称缓存全量加载 instrument_type（含 index/fund/bond 等非股票行）——
    名称缓存只载股票行（裁决#3），品种证据必须覆盖全命名空间才有正向拦截力。"""
    global _itype_loaded
    if not _itype_loaded:
        with sqlite3.connect(f'file:{_DB}?mode=ro', uri=True) as c:
            try:
                rows = c.execute(
                    'SELECT code, instrument_type FROM stock_info '
                    'WHERE instrument_type IS NOT NULL'
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []  # 列缺失（旧快照）= 无正向证据, 全量 None → 闸 fail-open
        for k, t in rows:
            _itype_cache[k] = str(t).strip()
        _itype_loaded = True
    return _itype_cache.get(_norm(code))


def require_name(code: str) -> str:
    """查股票名称；查不到直接抛异常（fail-closed，防止空名称流入输出）。"""
    name = get_name(code)
    if name is None:
        raise ValueError(f'stock_info中无 {code} —— 无法验证名称，禁止输出（实事求是铁律）')
    return name


def verify_pair(code: str, claimed_name: str) -> tuple[bool, str]:
    """校验 (code, claimed_name) 是否与库一致。
    返回 (是否一致, 库中实际名称或错误说明)。"""
    actual = get_name(code)
    if actual is None:
        return False, f'库中无此代码'
    if actual == claimed_name:
        return True, actual
    return False, actual


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    for code in sys.argv[1:]:
        name = get_name(code)
        print(f'{code}: {name if name else "❌ 库中未找到"}')