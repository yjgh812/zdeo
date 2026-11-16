# ZCode × Kimi K3 适配调研报告

> 知微 · 2026-08-12 · 官方文档 + 本机实证（ZCode 3.7.6）

## 一、Kimi K3 模型全景（官方）

| 项目 | kimi-k3 | k3-256k | kimi-k2.7-code |
|---|---|---|---|
| 定位 | **旗舰模型**（能力最强） | K3 短上下文版 | 编程专用 |
| 上下文 | 1,048,576（1M） | 262,144（256K） | 262,144 |
| 最大输出 | 131,072 | 131,072 | 131,072 |
| 输入模态 | text+image+video | text+image+video | text+image+video |
| 思考模式 | **始终推理**（无 off 档） | 同左 | 同左 |
| reasoning_effort | low / high / max（默认 max） | 同左 | 同左 |

**定价（每百万 token，官方）**：
- 输入（缓存未命中）：¥20；输入（**缓存命中）：¥2**；输出：**¥100**
- ⚠️ 输出价比 DeepSeek flash（2 元）贵 **50 倍**；比 v4-pro（6 元）贵 16 倍
- 缓存价差 10 倍（2 vs 20）——长会话吃缓存收益显著

**API**：通用端点 `https://api.moonshot.cn/v1`（OpenAI 兼容）+ `/anthropic/v1/messages`（Anthropic 兼容）；Bearer 鉴权。K3 顶层 `reasoning_effort` 控制推理强度。

## 二、ZCode 接入现状（本机实证）

### 2.1 内置 catalog 已就绪
`moonshot-kimi` provider（baseURL `api.moonshot.cn`，默认 anthropic 格式）内置 **11 个模型**：kimi-k3 / k3 / k3-256k / kimi-k2.6 / k2.5 / moonshot-v1 系列（8 月底下线）。reasoning 档位 low/high/max 默认 max 已配好——**UI 添加官方 provider 填 key 即可用**。

### 2.2 现有配置诊断（关键 ⚠️）
主人 v2/config.json 已有自定义条目 `6865b050... | Kimi Code`：
- `baseURL: https://api.kimi.com/coding/v1`（Kimi For Coding **订阅产品**端点，非通用 API）
- 模型：仅 `k3-256k`；reasoning low/high/max 默认 max

**实证结果**（2026-08-12）：
- ✓ `GET /v1/models` 通过 → key 有效，可用模型：k3 / k3-256k / kimi-for-coding / kimi-for-coding-highspeed
- ✗ `POST /chat/completions` **403 Forbidden** → **该订阅 key 无 API 直调权限**
- ✗ `api.moonshot.cn/v1/models` **401** → 非通用 API key

**结论：现有 Kimi key 无法在 ZCode 中实际调用**（只能列出模型）。

## 三、正确接入路径（二选一）

| 方案 | 操作 | 适合 |
|---|---|---|
| **A. 官方按量 API（推荐）** | platform.moonshot.cn 开通/充值 → 创建 API Key → ZCode UI 添加 Moonshot 官方 provider 填 key | 想真用 K3 旗舰能力 |
| B. Kimi Code 订阅直调 | 确认订阅套餐是否含 API 额度（当前 403 表明不含）；升级套餐或走官方客户端 | 已有订阅且不想按量 |

拿到可用 key 后 ZCode UI：模型选择器 → 管理模型 → 添加供应商 → Moonshot（或自定义，baseURL `https://api.moonshot.cn`，Anthropic 端点 `/anthropic/v1/messages`）→ 选 kimi-k3。

## 四、不降智清单（K3 特有）

1. **始终推理，无 off 档**——K3 没有"关推理"选项；省时只降 effort 到 low，能力折损在思考深度而非模型
2. **effort 保持 max**（默认）——量化分析重活别降
3. **别调高 maxOutputTokens**——与 DeepSeek 同理：压缩历史更早 = 记忆变短 = 降智
4. **长会话优先 1M 的 kimi-k3**（而非 k3-256k）——上下文大 = 压缩发生晚 = 不降智
5. **缓存 10 倍价差**（命中 2 元 vs 未命中 20 元）——同一 session 别换模型
6. **价格红线**：输出 ¥100/百万 ≈ flash 的 50 倍。**K3 只用于重推理**（策略评审/代码重构），日常流水任务仍用 DeepSeek flash；预算敏感时先用 highspeed 变体

## 五、证据源
- 官方定价：https://platform.kimi.com/docs/pricing/chat-k3
- 官方模型/接入：https://platform.kimi.com/docs/coding/getting-started
- ZCode catalog：`D:\anzhuang\zcode\resources\model-providers\models_catalog_china_llm_zcode_2026-06-03.json`
- 本机配置：`D:\zcodesj\.zcode\v2\config.json`（provider 6865b050 = Kimi Code）
- 实证：GET /models 200 + POST 403 + moonshot.cn 401（2026-08-12）
