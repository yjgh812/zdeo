# SQLite WAL模式知识卡片——屠龙系统专用
> 2026-08-15 自省产出 | 填补知识盲区

## 核心概念

### WAL（Write-Ahead Logging）
- **写入不直接进DB文件**，先追加到 `-wal` 文件尾部
- **读取**：同时读 DB + WAL，合并出最新数据
- **Checkpoint**：将WAL内容写回DB文件 → 清空WAL
- **好处**：读写不互斥（读可并发，写只互斥写）

### SHM（Shared Memory）
- `-shm` 文件是WAL模式的**进程间同步锁**
- 32KB固定大小，记录哪个page在WAL中、哪些进程在读
- **最后一个连接关闭时** SQLite自动删除SHM+WAL
- **SHM残留 = 有连接非正常关闭**

## 屠龙系统遇到的问题

### 问题1：`database is locked`

**触发场景**：aux阶段紧接daily_basic后写同一fundamental_data.db

**根本原因**：
1. daily_basic 用 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=60000`
2. daily_basic 结束后 `con.close()` 执行，但WAL/SHM未自动清除（仍有未决读取事务或WAL未checkpoint）
3. aux/northbound 立即打开同一DB → SHM文件仍在 → SQLite认为上一个连接未完全关闭 → "database is locked"

**修复方向**：
- ✅ 已跳过废弃的northbound阶段
- 若将来需要串行写同一DB：在`con.close()`前显式执行 `CHECKPOINT`

### 问题2：`disk I/O error`

**触发场景**：v375写入997万行到38GB的dragon_quant.db时

**根本原因**：
1. v375的INSERT批量写入大量数据
2. WAL文件快速增长 → SQLite自动触发checkpoint
3. checkpoint回写38GB主文件时发生瞬时文件系统I/O阻塞
4. SQLite视为"disk I/O error"并回滚当前batch
5. **但此前已提交的数据仍在DB中**（WAL内容已部分checkpoint完成）

**关键理解**：`disk I/O error ≠ 数据丢失`。SQLite的WAL模式下，已提交的事务即使checkpoint中断也不会丢失——数据仍安全在WAL文件中，下次打开时会自动recovery。

## 最佳实践（屠龙系统）

### 当前配置
```python
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA busy_timeout=60000")   # 60秒等待锁释放
con.execute("PRAGMA synchronous=NORMAL")   # 平衡性能与安全
```

### 建议改进

1. **批量写入后显式checkpoint**：
```python
con.execute("PRAGMA wal_checkpoint(PASSIVE)")  # 仅在安全时checkpoint
```

2. **大事务分批提交**（v375已有，每500条一次）：
```python
if batch_count % 500 == 0:
    con.commit()
```

3. **多脚本写同一DB时加退避重试**：
```python
for attempt in range(5):
    try:
        con.execute(...)
        break
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "disk I/O" in str(e):
            time.sleep(1 * (attempt + 1))
            continue
        raise
```

4. **确保连接完全关闭**：
```python
con.commit()
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # 强制checkpoint+清WAL
con.close()
# SHM文件应自动删除
```

## 速查
```python
# 检查WAL状态
cur = con.execute("PRAGMA wal_checkpoint")
# 返回 (checkpoint类型, 检查的page数, checkpoint的page数)
# (0, n, m) = PASSIVE, (1, n, m) = FULL, (2, n, m) = RESTART, (3, n, m) = TRUNCATE

# 查看journal模式
cur = con.execute("PRAGMA journal_mode")
print(cur.fetchone()[0])  # 'wal' 或 'delete' 或 'memory'

# 强制清理WAL
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.close()
# 确认 -wal 和 -shm 文件已删除
```