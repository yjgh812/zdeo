# -*- coding: utf-8 -*-
"""
全库新鲜度检查：扫描所有关键 SQLite 数据库的最新日期，与目标交易日比对。
TARGET 动态推导（qfq 库最新日期）,避免硬编码过时; --target 显式覆盖。
用法: python check_db_freshness.py [--target 20260824]
"""
import os
import sqlite3
import sys
import struct
from collections import Counter


def _derive_target():
    """从 qfq 库最新日期动态推导目标交易日（只读）"""
    try:
        c = sqlite3.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro",
            uri=True)
        r = c.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
        c.close()
        if r and r[0]:
            return str(r[0])
    except Exception:
        pass
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")


TARGET = _derive_target() if len(sys.argv) < 2 else sys.argv[1]

DBS = {
    "dragon_quant_meta.db (统一数据源)": r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
    "dragon_quant.db (主库)": r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
    "dragon_quant_qfq.db (前复权)": r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
    "factor_pit.db (PIT 财务因子)": r"D:\zcodesj\factor_pit.db",
    "fundamental_data.db (基本面)": r"D:\workbuddyspace\databases\fundamental_data.db",
    "tulong_production_paper.db (模拟盘影子库)": r"D:\zcodesj\tulong_production_paper.db",
    "tulong_data_hub.db (数据中枢缓存)": r"D:\workbuddyspace\_v37_cache\tulong_data_hub.db",
    "tulong_system.db (屠龙系统)": r"D:\workbuddyspace\tulong_system\tulong_system.db",
    "eastmoney stock_data.db": r"D:\zcodesj\eastmoney_quant\stock\stock_data.db",
    "eastmoney sector_data.db": r"D:\zcodesj\eastmoney_quant\sector\sector_data.db",
    "attribution.db (归因库)": r"D:\zcodesj\attribution\attribution.db",
    # 2026-09-03 C1: 根目录 attribution.db=看板库(discipline_daily 真源写入方 discipline_stats.py 目标);
    # 与 attribution\attribution.db(交易归因核心库)同名不同库, 双库命名冲突已登记债务待主人裁决改名
    "attribution.db (看板库·根目录)": r"D:\zcodesj\attribution.db",
}

# 各库重点表（None = 自动列出全部业务表）
FOCUS = {
    # 2026-08-29 数据库优化专项: stock_info 表在主库而非 meta(此前抄错位置致每次巡检报"表不存在"), 已移回主库段
    "dragon_quant_meta.db (统一数据源)": ["stock_daily_meta", "index_daily_meta", "smart_money_factors", "stock_minute_5"],
    # 2026-09-10: stock_signals_v4 移出巡检(死表: MAX=20260528, 仅回测脚本消费,
    # 每夜恒 ⏳ 噪音; 生产信号表=stock_signals_v43/v375 + v4 管线 CSV)
    "dragon_quant.db (主库)": ["stock_daily_raw", "stock_factors", "stock_signals_v375", "stock_signals_v43", "stock_info"],
    "dragon_quant_qfq.db (前复权)": ["stock_daily_meta_qfq"],
    "eastmoney stock_data.db": ["stock_spot", "stock_rank", "stock_kline", "stock_indicators", "stock_basic"],
    # 2026-09-02 sector_kline 降级(主人"按你推荐继续"授权): push2his 全族(82./73.节点)对本机持续封锁
    # 无替代真实源, 全库唯一读者=本文件且生产零消费(9/2 grep 实证, daily_chain_update 不写不读) → 死表停巡检;
    # sector_basic/sector_member 保留观察; 复活条件=出现可用真实源并接通生产消费方
    "eastmoney sector_data.db": ["sector_basic", "sector_member"],
    "attribution.db (归因库)": None,
}

SKIP_TABLES = {"sqlite_sequence"}

# 2026-09-10: 核心表滞后 → 退出码 1(此前脚本恒 exit 0, 调用方无从感知断更;
# 与 daily_chain verify fail-closed 同批)。核心表=选股/信号生产直接消费表。
CRITICAL_TABLES = {
    "stock_daily_raw", "stock_daily_meta_qfq", "feature_daily_v2",
    "stock_ml_scores_v22", "stock_daily_meta", "stock_minute_5",
}

# 2026-09-03 C2: 事件驱动表/真源他库表标注——⏳ 但非断链, 消除巡检误读
TABLE_NOTES = {
    "monitor_events": "事件驱动表(high_board_verify_alert 有预警才INSERT): 零写入=零预警属正常",
    "discipline_daily": "本副本为历史遗留停更; 真源=根目录看板库段(见下)",
    "position_history": "写入器=closed_loop_sync.py(FIFO唯一owner,9/8落地,22:00链aux接线; 9/24实证活:13行闭环至20260923)。历史E82注记「唯一写入器已死」指旧一次性导入脚本断链(8/27),已被接管,勿再误读;updated_at滞后1-2日属链节奏正常",
}


def date_cols(candidates):
    """从列名里找日期列，按优先级返回候选列表"""
    order = ["trade_date", "date", "datetime", "end_date", "ann_date", "ts", "day", "created_at", "updated_at"]
    hits = [c for c in order if c in candidates]
    # 兜底: 包含 date/时间戳关键词的列
    for c in candidates:
        if c not in hits and any(k in c.lower() for k in ("date", "time", "day")):
            hits.append(c)
    return hits


def latest_of(conn, table):
    """返回 (日期列, 最大值, 行数)，大表用索引优先"""
    if not str(table).isidentifier():
        raise ValueError(f"非法表名: {table!r}")
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]
    dcands = date_cols(cols)
    if not dcands:
        return None, None, None
    nrows = conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
    for dcol in dcands:
        if not str(dcol).isidentifier():
            continue
        try:
            # 优先走索引: 若有 (dcol) 或 (dcol desc) 索引则 MAX 很快
            row = conn.execute(f"SELECT MAX({dcol}) FROM '{table}'").fetchone()
            if row and row[0] is not None:
                return dcol, row[0], nrows
        except Exception:
            continue
    return None, None, nrows


def check_one(label, path):
    print(f"\n{'=' * 70}\n📦 {label}\n   {path}")
    if not os.path.exists(path):
        print("   ❌ 文件不存在")
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    except Exception as e:
        print(f"   ❌ 打开失败: {e}")
        return []
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    focus = FOCUS.get(label)
    targets = [t for t in tables if focus is None or t in focus]
    if focus is not None:
        missing = [t for t in focus if t not in tables]
        for m in missing:
            print(f"   ⚠️ 表不存在: {m}")
    behind_critical = []
    for t in targets:
        dcol, maxv, nrows = latest_of(conn, t)
        if dcol is None:
            print(f"   • {t} ({nrows}行): 无日期列")
            continue
        s = str(maxv)
        ok = (s.replace("-", "")[:8] >= TARGET)
        mark = "✅" if ok else "⏳"
        note = "" if ok else ("  ← " + TABLE_NOTES[t] if t in TABLE_NOTES else "")
        print(f"   {mark} {t} [{dcol}] MAX={s} ({nrows}行){note}")
        # 2026-09-10: 核心表落后登记(退出码用)
        if not ok and t in CRITICAL_TABLES:
            behind_critical.append(f"{label.split(' ')[0]}.{t}")
    conn.close()
    return behind_critical


def check_tdx():
    """通达信 vipdoc 日线众数检查"""
    print(f"\n{'=' * 70}\n📦 通达信本地 vipdoc 日线 (D:\\anzhuang\\tdx\\vipdoc)")
    prefix_ok = ("600", "601", "603", "605", "688", "689",
                 "000", "001", "002", "003", "300", "301",
                 "8", "4", "920")
    for mkt in ("sh", "sz", "bj"):
        d = rf"D:\anzhuang\tdx\vipdoc\{mkt}\lday"
        if not os.path.isdir(d):
            print(f"   ❌ {mkt} 目录不存在")
            continue
        dates = []
        n = 0
        for fn in os.listdir(d):
            if not fn.endswith(".day"):
                continue
            code = fn.replace(".day", "")[-6:]
            if mkt == "sh" and not (code.startswith("6") or code.startswith("9")):
                continue
            if mkt == "sz" and not code.startswith(("0", "3")):
                continue
            p = os.path.join(d, fn)
            try:
                size = os.path.getsize(p)
                if size < 32:
                    continue
                with open(p, "rb") as f:
                    f.seek(size - 32)
                    buf = f.read(32)
                dt = struct.unpack("<I", buf[:4])[0]
                dates.append(dt)
                n += 1
            except Exception:
                continue
        if dates:
            c = Counter(dates).most_common(3)
            latest = max(dates)
            ok = str(latest) >= TARGET
            print(f"   {'✅' if ok else '⏳'} {mkt}: 文件数={n} 最新众数={c} 最新={latest}")
        else:
            print(f"   ⚠️ {mkt}: 无可读日线文件")


# 2026-10-14 全面合并定谳(主人令「全面合并, 杜绝数据跑偏, 合并后是唯一真源」):
# qfq 副本白名单巡检——在役唯一真源=wb factor_engine\data\dragon_quant_qfq.db;
# 允许副本仅限 backups\dragon_quant_qfq\ 备份族整树与 wb databases\ dead 名标记件。
# 任何其他位置的 dragon_quant_qfq* 文件(尤其 data\dragon_quant_qfq.db 旧路径回置,
# 10/14 晨曾发生人工回置) = 跑偏违规态 → ❌ 且计入 exit 1。
QFQ_TRUTH_PATH = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
QFQ_COPY_ALLOWED_PREFIXES = (
    QFQ_TRUTH_PATH,                                    # 真源本体 + -shm/-wal sidecar
    r"D:\zcodesj\backups\dragon_quant_qfq" + os.sep,   # 备份族整树
)
QFQ_COPY_ALLOWED_EXACT = {
    r"D:\workbuddyspace\databases\dragon_quant_qfq.db.dead_20260923",  # 死名标记件
}
# 2026-10-14 三轮升级(主人「继续」批): 5扁平目录 → 双域递归+剪枝(实测~15s),
# 剪枝=巨型/生成物/归档目录 + c_backup_* 会话备份残骸(9/21 快照碎片 129+ 文件
# 非活体, 大小写不敏感前缀)。深层副本自此全覆盖。
QFQ_COPY_WALK_ROOTS = (r"D:\zcodesj", r"D:\workbuddyspace")
QFQ_COPY_PRUNE_DIRS = {
    ".git", ".zcode", "__pycache__", "node_modules", "qlib_bin", "archive",
    ".venv", "venv", "site-packages", "$RECYCLE.BIN", "System Volume Information",
    "dot_workbuddy",  # harness 会话数据(_app_home 下 modify_backup 残骸, 与 c_backup_ 同族非量化面)
}
QFQ_COPY_PRUNE_PREFIX = "c_backup_"


def check_qfq_copy_whitelist():
    """qfq 副本白名单巡检(双域递归+剪枝): 任何位置(除白名单)出现 dragon_quant_qfq*
    文件即违规。返回违规路径列表, 空=合规。"""
    violations, scanned = [], 0
    for walk_root in QFQ_COPY_WALK_ROOTS:
        if not os.path.isdir(walk_root):
            continue
        for root, dirs, files in os.walk(walk_root):
            dirs[:] = [d for d in dirs
                       if d.lower() not in QFQ_COPY_PRUNE_DIRS
                       and not d.lower().startswith(QFQ_COPY_PRUNE_PREFIX)]
            for fn in files:
                if "dragon_quant_qfq" not in fn:
                    continue
                scanned += 1
                p = os.path.join(root, fn)
                if any(p.startswith(pref) for pref in QFQ_COPY_ALLOWED_PREFIXES):
                    continue
                if p in QFQ_COPY_ALLOWED_EXACT:
                    continue
                violations.append(p)
    print(f"\n{'=' * 70}\n📦 qfq 副本白名单巡检 (唯一真源 = {QFQ_TRUTH_PATH})")
    print(f"   双域递归+剪枝扫描: 副本文件 {scanned} 个, 白名单外 {len(violations)} 个")
    for v in violations[:10]:
        print(f"   ❌ 跑偏违规副本: {v}")
    if len(violations) > 10:
        print(f"   ❌ ...(另 {len(violations) - 10} 条)")
    if not violations:
        print("   ✅ 全部副本在白名单内 (backups 备份族 + dead 名标记件, c_backup 残骸剪枝)")
    return violations


# 2026-10-14 补缝(主人「继续」批): 冻结副本读侧引用巡检——读 backups 副本/旧 data 路径
# 是唯一残余的"静默读陈旧数据"通道; AST 扫根级 *.py 字符串常量(排除 docstring,
# 白名单=退役桩 qfq_replica_sync[常量供 gen_data_locations 解析] + 本巡检器自身),
# 命中即 ❌ exit1。
# 2026-10-14 深夜扩域(主人「继续」批): 扫描域根级 → 全仓剪枝递归——当日实锤
# analysis/scan_board_watch.py 旧死路径引用致盘中哨 65 实例全灭(根级扫描盲区),
# 另 9 件 analysis/ 孤儿研究件同犯已批量改指真源; 剪枝复用副本巡检族并追加
# data/reports/logs/backups 等非码域, 防线自此覆盖子目录新建代码。
QFQ_FROZEN_REF_PATTERNS = (
    "backups\\dragon_quant_qfq", "backups/dragon_quant_qfq",
    "retired_stalled", "bak_premerge", "prefromamtfix", "resurrected",
    "zcodesj\\data\\dragon_quant_qfq", "zcodesj/data/dragon_quant_qfq",
)
QFQ_REF_SCAN_DIR = r"D:\zcodesj"
QFQ_REF_ALLOWLIST_FILES = {
    "qfq_replica_sync.py",     # 退役桩: SRC/DST 常量保留供 gen_data_locations 解析(DATA_LOCATIONS 已声明)
    "check_db_freshness.py",   # 本巡检器自身: 模式常量含上述字样属自指
    "fix_qfq_amount_20260906.py",  # 20260906 一次性修复件(已执行毕, 默认--db指向已轮转周备属历史语义;
                                   # health_check/qfq_amount_guard 按名引用故留根, 白名单声明 2026-10-14)
    # 2026-10-14 深夜: portrait_agent/v6_research 三件 20260906 当日研究, QFQ_DB 刻意
    # 钉死当日周备快照(可复现性语义); 该 vintage 已被周备轮转清除(weekly 仅存
    # 20261011), 交付物已入 reports/ 复跑无语义——改指真源反静默改研究语义, 按上例
    # 留根白名单声明。
    "doubled_batch_profile_20260906.py",
    "doubled_batch_profile_v2_20260906.py",
    "doubled_industry_role_20260906.py",
    # 2026-10-30: qfq 守卫 FAIL 处置规程规定审计件(行级 diff vs pre_repair_20261021 备份,
    # mode=ro 只读, 可复用工具——下次守卫 FAIL 处置第 1 步即复跑; 今日 qfq 修复回归审计产物,
    # 留根白名单声明, 主人「继续」令批准)。
    "qfq_audit_deep_20261030.py",
    "qfq_diff_audit_20261030.py",
}
QFQ_REF_SCAN_PRUNE_DIRS = QFQ_COPY_PRUNE_DIRS | {
    "backups", "data", "reports", "logs", "tmp", "_tmp", "_app_home", "docs",
}


def check_qfq_frozen_refs():
    """全仓(剪枝递归) py 字符串常量中的冻结副本/旧路径引用巡检(AST, 排除 docstring)。
    返回 (违规列表[相对路径:行], 扫描文件数)。"""
    import ast as _ast
    violations, scanned = [], 0
    for root, dirs, files in os.walk(QFQ_REF_SCAN_DIR):
        dirs[:] = [d for d in dirs
                   if d.lower() not in QFQ_REF_SCAN_PRUNE_DIRS
                   and not d.lower().startswith(QFQ_COPY_PRUNE_PREFIX)]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            scanned += 1
            if fn in QFQ_REF_ALLOWLIST_FILES:
                continue
            fpath = os.path.join(root, fn)
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    tree = _ast.parse(f.read())
            except Exception:
                continue  # 语法异常件非本防线目标(由各自运行时暴露)
            docstrings = set()
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    d = _ast.get_docstring(node, clean=False)
                    if d:
                        docstrings.add(d)
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                    s = node.value
                    if s in docstrings:
                        continue
                    if any(p in s for p in QFQ_FROZEN_REF_PATTERNS):
                        rel = os.path.relpath(fpath, QFQ_REF_SCAN_DIR)
                        violations.append("%s:%d" % (rel, getattr(node, "lineno", 0)))
    return violations, scanned


if __name__ == "__main__":
    # 2026-09-10: TARGET 默认由 qfq 自身 MAX 推导——qfq 陈旧时自参照掩盖, 独立校验请
    # 显式 --target <vipdoc最新日>; 退出码: 核心表落后>0 → exit 1(fail-closed)
    print(f"🎯 目标交易日: {TARGET} (默认= qfq 自推导; 独立校验建议显式 --target)")
    check_tdx()
    _behind = []
    for label, path in DBS.items():
        _behind.extend(check_one(label, path))
    _qfq_violations = check_qfq_copy_whitelist()
    _frozen_refs, _ref_scanned = check_qfq_frozen_refs()
    print(f"\n{'=' * 70}\n📦 qfq 冻结副本读侧引用巡检 (AST 全仓剪枝扫 py {_ref_scanned} 件, "
          f"白名单=qfq_replica_sync退役桩+本巡检器+留根历史件4+qfq守卫处置规程审计件2(20261030))")
    if _frozen_refs:
        for v in _frozen_refs:
            print(f"   ❌ 冻结副本/旧路径引用: {v}")
    else:
        print("   ✅ 零引用")
    print("\n" + "=" * 70)
    if _behind:
        print(f"⛔ 核心表落后: {', '.join(_behind)} → exit 1")
        print("检查完成(有核心断更)")
        sys.exit(1)
    if _qfq_violations or _frozen_refs:
        print(f"⛔ qfq 跑偏态: 白名单外副本 {len(_qfq_violations)} + 冻结引用 {len(_frozen_refs)} → exit 1")
        print("检查完成(qfq副本跑偏)")
        sys.exit(1)
    print("检查完成 ✅")
    sys.exit(0)
