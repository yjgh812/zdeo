#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
改动同步检查工具 v1.0 — 每次改代码后强制运行
检查：代码 与 cron 与 测试 与 文档 四层一致

用法：python change_sync_checklist.py <关键词>
示例：python change_sync_checklist.py MAX_BUY_PER_DAY
      python change_sync_checklist.py S-4
      python change_sync_checklist.py R4
"""
import subprocess, sys, os, glob, re

ZCODESJ = r"D:\zcodesj"

# 目录裁剪白名单外的补充排除：venv 族含完整 site-packages（数万文件），全量扫描
# 是关键词检索卡死的根因（2026-09-02 实证：'--help' 关键词扫出 72 处 venv 命中超时）
SKIP_DIRS = ('.git', '__pycache__', 'node_modules', '.zcode', 'catboost_info')

def _prune(dirs):
    dirs[:] = [d for d in dirs
               if d not in SKIP_DIRS
               and not d.startswith('.venv')
               and d != 'venv']

MAX_SCAN_BYTES = 100 * 1024 * 1024  # 2026-09-19: 超大文本文件全量read会MemoryError(实弹踩坑), 超限跳过

def grep(pattern, path):
    """搜索文件内容，返回匹配文件列表"""
    matches = []
    for root, dirs, files in os.walk(path):
        _prune(dirs)
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext not in ('.py', '.md', '.json', '.txt', '.ini', '.cfg'):
                continue
            fp = os.path.join(root, f)
            try:
                if os.path.getsize(fp) > MAX_SCAN_BYTES:
                    continue
                with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                if pattern in content:
                    matches.append(fp)
            except OSError:
                pass
    return matches

def grep_exclude(pattern, path, exclude_patterns=None):
    """搜索但排除已废弃/已删除的引用"""
    matches = []
    exclude = exclude_patterns or ['已删除', '已移除', '已废除', '已解除', '已按主人', 's4-fabricated', '已废弃', '废除', '退役', '作废', '判死', '证伪', '已删', '已弃用', '已停用']
    for root, dirs, files in os.walk(path):
        _prune(dirs)
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext not in ('.py', '.md', '.json', '.txt'):
                continue
            fp = os.path.join(root, f)
            try:
                if os.path.getsize(fp) > MAX_SCAN_BYTES:
                    continue
                with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                if pattern in content:
                    # Check if this is an active reference
                    idx = content.index(pattern)
                    nearby = content[max(0,idx-50):idx+len(pattern)+50]
                    if any(e in nearby for e in exclude):
                        continue
                    matches.append(fp)
            except OSError:
                pass
    return matches

# ═══════════════════════════════════════════════════════════════
# A7 跨目录依赖登记表（2026-08-17 新增，分域主从 SSOT 的一部分）
# 维护：D:\deepseeksj\.agent-teams\deliverables\A7-单一事实源对齐.md §四
# 语义：改动「被依赖方」前必须检查对端调用方；改动「依赖方」后须 verify 对端不破。
# ═══════════════════════════════════════════════════════════════
CROSS_DIR_DEPS = [
    # (依赖方, 被依赖方, 说明)
    (r"D:\workbuddyspace\tulong_system_daily.py", r"D:\zcodesj\feedback_engine.py",
     "wb 晚班管线编排器 → zc 归因反馈（A1 修复 L136 硬编码）"),
    (r"D:\workbuddyspace\tulong_system_daily.py", r"D:\zcodesj\attribution_outcome_backfill.py",
     "wb 晚班管线编排器 → zc 归因回填（A1 修复 L141 硬编码）"),
    (r"D:\zcodesj\db_backup.py", r"D:\workbuddyspace\databases\fundamental_data.db",
     "zc 备份器 → wb 库文件（WEEKLY 组）"),
    (r"D:\zcodesj\db_backup.py", r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
     "zc 备份器 → wb qfq 库（WEEKLY 组）"),
    (r"D:\zcodesj\db_backup.py", r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
     "zc 备份器 → wb 主库（MONTHLY 组）"),
    (r"D:\zcodesj\db_backup.py", r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
     "zc 备份器 → wb meta 库（MONTHLY 组）"),
    (r"D:\zcodesj\db_backup.py", r"D:\workbuddyspace\databases\sw_industry.db",
     "zc 备份器 → wb 行业库（DAILY 组）"),
    (r"D:\zcodesj\health_check_daily.py", r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
     "zc 巡检 → wb 主库（7 表新鲜度检查，只读）"),
    (r"D:\zcodesj\health_check_daily.py", r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
     "zc 巡检 → wb meta 库（只读）"),
    (r"D:\zcodesj\health_check_daily.py", r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
     "zc 巡检 → wb qfq 库（只读）"),
    (r"D:\zcodesj\health_check_daily.py", r"D:\workbuddyspace\databases\fundamental_data.db",
     "zc 巡检 → wb 库文件（STRICT_TABLES 第7项 daily_basic 新鲜度，只读）"),
    (r"D:\zcodesj\health_check_daily.py", r"D:\zcodesj\attribution\attribution.db",
     "zc 巡检 → zc 归因业务库（A1 回填监控 S5，同 zc 跨 attribution 子目录，只读）"),
    (r"D:\zcodesj\discipline_stats.py", r"D:\zcodesj\attribution\attribution.db",
     "zc 纪律统计 → zc 归因业务库 guard_precheck_log/trade_records（同 zc，但跨 attribution 子目录）"),
]

# 生产主表（A7 分域主从，2026-08-17 主人裁决）：键=脚本名，值=(生产主目录, 说明)
SSOT_MASTER = {
    "guarded_trade_executor.py": ("D:\\zcodesj", "交易执行链"),
    "pre_trade_check_v2.py": ("D:\\zcodesj", "交易执行链"),
    "m5_impulse_blocker.py": ("D:\\zcodesj", "交易执行链"),
    "pre_sell_check.py": ("D:\\zcodesj", "交易执行链"),
    "trade_time_guard.py": ("D:\\zcodesj", "交易执行链"),
    "trade_state.py": ("D:\\zcodesj", "交易执行链"),
    "trade_executor.py": ("D:\\zcodesj", "交易执行链"),
    "daily_trade_plan.py": ("D:\\zcodesj", "交易执行链"),
    "db_backup.py": ("D:\\zcodesj", "工具链"),
    "holdings_risk_scan.py": ("D:\\zcodesj", "工具链"),
    "swing_phase_probe.py": ("D:\\zcodesj", "工具链"),
    "notify.py": ("D:\\zcodesj", "工具链"),
    "health_check_daily.py": ("D:\\zcodesj", "工具链"),
    "l0_system_health.py": ("D:\\zcodesj", "工具链"),
    "naked_trade_audit.py": ("D:\\zcodesj", "工具链"),
    "discipline_stats.py": ("D:\\zcodesj", "工具链"),
    "daily_chain_update.py": ("D:\\zcodesj", "数据链"),
    "build_qfq_meta_tdx.py": ("D:\\zcodesj", "数据链"),
    "execution_guard.py": ("D:\\workbuddyspace", "执行守卫族"),
    "enforce_stop_loss.py": ("D:\\workbuddyspace", "执行守卫族"),
    "stop_loss_scanner.py": ("D:\\workbuddyspace", "执行守卫族"),
    "execution_optimizer.py": ("D:\\workbuddyspace", "执行守卫族"),
    "pre_trade_checklist.py": ("D:\\workbuddyspace", "执行守卫族"),
    "tulong_system_daily.py": ("D:\\workbuddyspace", "晚班管线编排"),
    "agent_orchestrator.py": ("D:\\workbuddyspace", "Agent编排族"),
    "agent_daemon.py": ("D:\\workbuddyspace", "Agent编排族"),
    "agent_heartbeat.py": ("D:\\workbuddyspace", "Agent编排族"),
    "agent_sdk.py": ("D:\\workbuddyspace", "Agent编排族"),
    "communication.py": ("D:\\workbuddyspace", "Agent编排族"),
}


def check_cross_dir(keyword: str):
    """检查关键词是否命中跨目录依赖表或生产主表（A7 Phase3）。"""
    print("\n" + "=" * 60)
    print("A7 跨目录依赖 / 生产主检查")
    print("=" * 60)
    hit_deps = [d for d in CROSS_DIR_DEPS if keyword in d[0] or keyword in d[1] or keyword in d[2]]
    if hit_deps:
        print(f"⚠️ 关键词 '{keyword}' 命中 {len(hit_deps)} 条跨目录依赖：")
        for dep, depon, note in hit_deps:
            print(f"   {dep}  →  {depon}")
            print(f"      {note}")
        print("   ⚠️ 若你正在修改被依赖方（右侧），必须检查左侧调用方是否受影响！")
    else:
        print(f"✅ 关键词 '{keyword}' 未命中跨目录依赖登记表。")
    master_hits = [k for k in SSOT_MASTER if keyword.lower() in k.lower() or keyword.lower() in SSOT_MASTER[k][1]]
    if master_hits:
        for k in master_hits:
            d, dom = SSOT_MASTER[k]
            print(f"🏠 生产主提示: {k} 属 [{dom}]，生产主 = {d}")
    else:
        print(f"ℹ️ 关键词 '{keyword}' 不在分域生产主表（未登记脚本，请确认是否为研究性质）。")


def check(argv):
    if not argv:
        print("用法: python change_sync_checklist.py <关键词>")
        print("示例: python change_sync_checklist.py MAX_BUY_PER_DAY")
        return 1
    
    keyword = argv[0]
    print("=" * 60)
    print(f"改动同步检查工具 — 关键词: {keyword}")
    print("=" * 60)
    
    # 1. 搜索代码文件（.py）
    py_files = grep(keyword, ZCODESJ)
    active_py = grep_exclude(keyword, ZCODESJ)
    
    # 2. 搜索文档（.md）
    md_files = grep(keyword, ZCODESJ)
    active_md = grep_exclude(keyword, ZCODESJ)
    
    # 3. 检查cron（无法远程grep，列出已知cron位置）
    print(f"\n📂 代码层 (.py): {len(py_files)} 处引用")
    for f in py_files:
        tag = " ⚠️" if f in active_py else " 📝(历史)"
        print(f"   {f}{tag}")
    
    print(f"\n📂 文档层 (.md): {len(md_files)} 处引用")
    for f in md_files:
        tag = " ⚠️" if f in active_md else " 📝(历史/注释)"
        print(f"   {f}{tag}")
    
    print(f"\n📂 定时任务层 ⚠️ 需手动检查:")
    print("   CronList -> 查看所有automation的prompt是否含该关键词")
    
    # 汇总建议
    if not active_py and not active_md:
        print(f"\n✅ 全系统无活跃的 '{keyword}' 引用（所有引用均为历史记录/已注释）")
    else:
        print(f"\n⚠️ 发现 {len(active_py)} 处活跃代码引用 + {len(active_md)} 处活跃文档引用")
        print("   请逐项确认是否需要同步更新")

    # A7 Phase3: 跨目录依赖/生产主检查
    check_cross_dir(keyword)

    return 0

if __name__ == '__main__':
    raise SystemExit(check(sys.argv[1:]))