# ZCode × DeepSeek 适配调研报告

> 知微 · 2026-08-12 · 基于官方文档 + 本机 ZCode 3.7.6 实证取证

## 一、DeepSeek 模型全景（官方）

| 项目 | deepseek-v4-flash | deepseek-v4-pro |
|---|---|---|
| 版本 | V4-Flash-0731 | V4-Pro |
| 上下文 | 1M tokens | 1M tokens |
| 最大输出 | 384K tokens | 384K tokens |
| 思考模式 | 非思考+思考（默认开启） | 同左 |
| 输入价（缓存未命中） | 1 元/百万 | 3 元/百万 |
| 输入价（缓存命中） | **0.02 元/百万** | 0.025 元/百万 |
| 输出价 | 2 元/百万 | 6 元/百万 |
| 并发限制 | 2500 | 500 |
| Responses API | 支持 | 2026 年 8 月初新增 |
| base_url | https://api.deepseek.com（Anthropic 格式: /anthropic） | 同左 |

⚠️ 官方提示：近期将整体上调定价。

**思考模式关键规则**（详见 [官方文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode)）：
- 思维链经 `reasoning_content` 返回，与 `content` 平级
- **不支持** temperature / top_p / presence_penalty / frequency_penalty（不报错但不生效）
- 多轮对话**有工具调用时必须回传历史思维链**，否则 400
- effort 档位：flash 下 xhigh→high；pro 下 xhigh→max

## 二、ZCode 适配现状（本机实证）

### 2.1 内置供应商目录
`D:\anzhuang\zcode\resources\model-providers\models_catalog_china_llm_zcode_2026-06-03.json`
共 **10 家供应商**：DeepSeek、Z.AI、BigModel、Moonshot、MiniMax、Qwen×2、小米 MiMo 等。

DeepSeek 定义（开箱即用）：
```json
{"id": "deepseek", "name": "DeepSeek",
 "endpoints": {"baseURL": "https://api.deepseek.com"},
 "defaultKind": "anthropic",
 "models": [
   {"id": "deepseek-v4-flash", "contextWindow": 1000000, "maxOutputTokens": 384000,
    "reasoning": {"defaultLevel": "max", "levels": {
      "off":  {thinking: disabled},
      "high": {anthropic: output_config.effort=high + thinking enabled(budgetTokens=1024),
               openai: thinking.type=enabled + reasoningEffort=high},
      "max":  {anthropic: output_config.effort=max + thinking enabled(budgetTokens=1024),
               openai: thinking.type=enabled + reasoningEffort=max}}}},
   {"id": "deepseek-v4-pro", 同上}]
}
```

### 2.2 实际请求链路（model-io 日志实证）
`C:\Users\yujie\.zcode\cli\rollout\model-io-*.jsonl`

本会话（deepseek-v4-flash，providerId=`392fb662-535e-4d2e-84ec-80a0d909b150`）：
- 格式：OpenAI 兼容（chat/completions），`stream: true`
- **`reasoningEffort: "max"` 已透传**（默认档位）
- baseURL 由服务端网关注入（本地无直连配置）
- 辅助请求（lite role，如标题生成）：直连 `https://api.deepseek.com`，`maxOutputTokens: 4096`，非流式

对比：GLM 会话走 `builtin:zai` → `https://api.z.ai/api/anthropic`（anthropic-messages 格式）。

### 2.3 用量限额实证
model-io 日志记录到 GLM 侧报错：
`[1308] Usage limit reached for 5 hour. Your limit will reset at ...` — ZCode 云端网关对用量有 **5 小时滚动限额**，长时间使用会触发。

## 三、更适配建议（可执行）

1. **思考档位按任务调**：当前默认 `max`（最准最慢）。卡顿/日常问答可切 `high`；纯快查可 `off`（省 token 且响应快）。切换入口在 ZCode 模型设置（variant 字段）。
2. **吃满缓存红利（价差 50 倍）**：flash 缓存命中 0.02 元 vs 未命中 1 元。**长会话别频繁开新 session**、别换模型——多轮同一 session 前缀命中缓存，成本直降 98%。
3. **模型分工**：日常/量大的任务用 flash（1/2 元）；复杂推理、代码重构、长文生成用 pro（3/6 元）。
4. **留意 5 小时限额**：长时间连续使用会撞 ZCode 网关限额（GLM 已实证），DeepSeek 侧并发上限 2500 足够，瓶颈在网关侧。
5. **v4-pro 新能力**：Responses API 支持已于 2026 年 8 月初上线，ZCode 若更新 catalog 即可用新接口。
6. **别调采样参数**：思考模式下 temperature 等无效，调了白调（适用于直接调 API 的场景）。

## 四、ZCode 接入 DeepSeek（BYOK 直连方案）

官方文档确认支持**自定义供应商 + API Key 模式**（BYOK），DeepSeek 官方给了标准配置：

| 格式 | 接口地址 |
|---|---|
| Anthropic 协议 | `https://api.deepseek.com/anthropic` |
| OpenAI 协议 | `https://api.deepseek.com/v1` |

**操作步骤**：
1. 对话框点模型名称 → 模型选择器 → 底部「管理模型」→ 设置 → 模型设置
2. 供应商页右上角切换「API Key」模式，或「添加供应商」
3. 填 Base URL + API Key → 自动拉取模型列表
4. 选择 `deepseek-v4-flash` / `deepseek-v4-pro`

**BYOK 直连 vs 内置网关对比**：

| 维度 | 内置网关（现状） | BYOK 直连 |
|---|---|---|
| 限额 | ⚠️ 5 小时滚动限额（GLM 3 次实证报 1308） | 无网关限额，仅官方并发限制 |
| effort 控制 | 内置 catalog 三档（当前 max） | 官方 API 参数直接可控 |
| 缓存 | 依赖网关实现 | 官方自动缓存（命中价 0.02 vs 1 元） |
| 数据路径 | zcode.z.ai 中转 | 直连 api.deepseek.com |

## 五、不降智清单（实证 + 官方文档）

**⚠️ 降智风险点**：
1. **调高「最大输出 Token」→ 压缩历史对话更早 → 降智**（官方文档明示：调高会压缩可携带的历史，导致自动压缩更早发生）。保持默认留空，跟随模型上限（384K）。
2. **reasoningEffort 调低 → 推理变弱**。当前 max 安全；要省时只降到 high，别动 off（除纯快查）。
3. **5 小时网关限额撞墙 → 连续工作流中断**。对策：BYOK 直连，或规划长任务避开峰值。

**✅ 已排除的假风险**（日志实证）：
- 无自动 fallback 降级：报错就是报错（`[1308] Usage limit`），ZCode **不会**悄悄切到弱模型——不会"偷偷降智"
- `v4 session stopped` = 用户手动停止会话，无害
- lite 角色请求（标题生成）用同一模型、maxOutputTokens=4096，不影响主链路

**不降智正解**：
1. BYOK 直连官方 API（绕限额 + effort 可控 + 缓存自动生效）
2. reasoning 档位保持 max/high
3. 不调 maxOutputTokens
4. 长会话不换模型（保缓存 + 保上下文连续）

## 六、证据源
- 官方文档（模型/定价/思考模式）：https://api-docs.deepseek.com/zh-cn/
- ZCode 供应商目录：`D:\anzhuang\zcode\resources\model-providers\models_catalog_china_llm_zcode_2026-06-03.json`
- 实际请求日志：`C:\Users\yujie\.zcode\cli\rollout\model-io-*.jsonl`
- 环境变量：`ZCODE_BASE_URL=https://zcode.z.ai`
