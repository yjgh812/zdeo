# 两条连续失败自动化的重建指引（2026-09-11 凌晨备妥，待新会话执行）

> **为什么重建**：两条自动化连续两日 dispatched→failed（21:30 新闻 PIT：9/9+9/10；23:30 涨票画像：9/9+9/10），
> 失败发生在派发后 10-90 秒，run.error / automations.last_error / 应用日志三处均无线索（根因未定位）。
> 首选处置=换新会话（旧会话疑似"毒化"：21:30 的会话 sess_48a62268 与 23:30 的 sess_1d3ade0c 自失败后每次派发即失败）。
>
> **为什么本会话没执行**：CronCreate 在计划任务会话内被平台拒绝（"Cannot create a scheduled task inside a session
> that already belongs to a scheduled task. Ask the user to start a new chat"）——需主人开一个新会话，把本文件喂给它执行。

## 执行步骤（新会话，两分钟）

1. 读重建包（prompt/cron 逐字真源，勿手抄）：
   - `data/_rebuild_26418274.json`（21:30 新闻 PIT；用 `prompt_fixed_single_slot` 字段=单时点修正版，
     原 `prompt` 字段仅存档；cron `30 21 * * *`）
   - `data/_rebuild_737e364c.json`（23:30 涨票画像；`prompt` 逐字原版；cron `30 23 * * 1-5`）
2. 先建后删（避免空窗）：
   - CronCreate：title=`工作日21:30新闻PIT存档(单时点·9/11重建新会话)`，cron/prompt 如上
   - CronCreate：title=`工作日23:30涨票画像每日观测（只读·零接线·9/11重建新会话）`，cron/prompt 如上
   - CronDelete：`automation-26418274-1c13-4d70-ab04-965f8c98b351`、`automation-737e364c-a73d-420c-a9cd-0cf7a9e5c77b`
3. 验收：CronList 确认新两条在册（nextRunAt=今晚 21:30/23:30）、旧两条消失。
4. 台账回写：DAILY_WORKFLOW_SOP 登记"9/11 重建换新会话（原因+包路径）"。
5. 观察：今晚两条的运行结果写入 SOP——若新会话仍 dispatched→failed，则"毒化会话"假设证伪，
   转向模型/配额方向排查（原 model 分别为 GLM-5.3-Flash / GLM-5.3，重建后可能变默认模型，属已知取舍）。

## 🔴 2026-09-11 01:5x 补充：模型相关性假设（重建时必读）

时间线实证（automation_runs 13 条全量核对）：**9/9 09:40 之后所有 GLM 模型(G_glm-5.3/5.3-Flash)的自动化回合 100% 失败，
所有 DeepSeek 模型回合 100% 成功**；切点在 9/9 午后。23:27 应用内 glm-5.2 连通性测试亦失败(10.3s)。
凌晨干重活的会话(pid 37288)与当前会话均为 DeepSeek → 一切正常。

⇒ 高置信假设：**bigmodel GLM 编码计划配额耗尽/计划变更/模型下线**（机制层待主人到 bigmodel 控制台核对）。
可证伪预测：9/11 09:10 的十一合一(automation-b1a67387, GLM-5.3-Flash)若成功 → 本假设证伪。

**受影响面（即使 ZCode 应用在运行也会失败）**：08:00 三合一 / 09:10 十一合一 / 09:26 universe_scan /
09:40 改价监控 / 13:30 / 14:30 / 21:30 / 23:30 —— 全部 GLM 模型 LLM 链。
不受影响：22:00 / 22:45（DeepSeek）、全部 schtasks 脚本任务（Signal_Cron/哨兵/14:50 兜底/画像/备份/封单）。

**重建动作修正**：新会话执行本指引前，先把该会话模型切到 **DeepSeek-V4-Flash-0731**（与 22:00/22:45 链一致，
已验证可用），再 CronCreate——新建自动化继承创建会话的 model/provider/mode。
另一条路：主人直接到 bigmodel 控制台查配额/计划，GLM 恢复则全部自动化自愈（无需重建）。

## 已知取舍（重建代价）

- 运行历史（runCount/lastRunAt）丢失——原值：21:30 runCount=11、23:30 runCount=3。
- 重建后的 model/mode 由 CronCreate 默认决定，可能不再是 GLM-5.3(-Flash)+yolo+max。
- 若今晚新会话仍失败，回滚方式：按本文件再重建一次并原样回填原 prompt 即可（无其他状态）。
