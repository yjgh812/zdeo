# 时尚感视频提示词模板（屠龙 ComfyUI H3 专用）

> 2026-10-01 定谳版。依据四组对照实验（styleAB A/C/D + 主人 fAB 自测）：
> **"土"的主因 = turbo 4 步 LoRA 的质感坍缩（主刀，改词救不了）；
> 次因 = 提示词锚点密度（同 4 步下强化词可明显扳回）。
> 正片档 = base 24 步；turbo 4 步只用于草稿预览构图运镜。**

## 三档工作流

| 档 | 步数/LoRA | 用途 | 实测 |
|---|---|---|---|
| 草稿 | turbo LoRA + 4 步 | 快速看构图/运镜/节奏 | 608×352×56 ≈ 60s |
| **正片** | **无 LoRA + 24 步**（BasicScheduler steps=24，摘 LoraLoaderModelOnly） | 交付成片 | 608×352×56 **实测 170s**（仅草稿 2.8 倍，模型加载/编码时长固定是主因） |
| 首帧 | Schnell 首帧 + H3 I2V | 静态审美上限高的慢镜头（特写转身/缓步） | 出图 25s + I2V 73帧 ≈ 75s；大动作会糊帧 |

## 叠加增量终极样片

`styleFINAL_24step_anchored_00001_.mp4`（2026-10-01）：24 步 × 强化锚点词，608×352×56，170s——自然妆发/逆光发丝金边/黑高领叠穿/黄昏精品街景，达到首尔杂志编辑风目标审美。对照基线 `styleAB_A_original`（同尺寸 4 步原版词，土味广告感）。

## 时尚锚点词模板（骨架，方括号换内容）

中文版（Qwen3-VL 编码器中文理解无障碍）：

```
高级时尚街拍短片，首尔杂志编辑风。镜头缓慢推近[主体：年轻东亚女性]特写：
oversized [主单品：米白羊毛大衣] 内搭 [叠穿：黑色高领针织]，[配饰：小巧金耳环]，
妆容自然通透（可见皮肤质感），长发微乱被风带起、边缘金色轮廓光。
她转身沿 [场景：奢侈品大道] 缓步前行——玻璃幕墙精品店、湿润路面映着落日、霓虹初亮；
浅景深把背景融成奶油状光斑。镜头后拉成跟拍随步伐加速，再减速定格侧面中景，她回眸看镜头。
干净的韩系编辑调色，暖冷对比，轻 16 毫米胶片颗粒，柔体积雾，面料微纹理清晰。
画面中无文字、无字幕、无水印，非动画非 CG，保持真人实拍质感。
```

英文版（时尚术语密度更高，锚点更精确）：

```
High-fashion street style film, Seoul editorial aesthetic. Slow dolly-in to [SUBJECT]:
oversized [cream wool coat, sharp tailoring] over [slim black turtleneck], [delicate gold
earrings], glossy natural makeup with visible skin texture, wind-lifted hair catching golden
rim light. She turns and walks down [a modern luxury boulevard] — glass curtain-wall
boutiques, wet-look asphalt reflecting golden-hour sun, neon signage flickering on; shallow
depth of field melts the background into creamy bokeh. Camera pulls back into a smooth
tracking shot, then settles into a side medium shot as she glances back. Clean
Korean-editorial color grade, warm-cool contrast, subtle 16mm film grain, soft volumetric
haze, crisp fabric micro-texture. No text, no subtitles, no watermark, no cartoon or CG look,
keep live-action editorial realism.
```

## 锚点五要素（"土→时尚"的增量所在）

1. **造型叠穿**：主单品+内搭+配饰三件套（单件大衣=电商感；层次=杂志感）
2. **妆发自然度锚**：必须写"妆容自然/可见皮肤质感/glossy natural makeup"——不写则模型默认浓妆广告脸（土的第一嫌疑）
3. **场景美术具体化**：不写"街道"，写"玻璃幕墙精品店/湿路反光/橱窗假人/霓虹初亮"——generic 场景=宣传片感
4. **调色风格锚**：韩系清透/editorial grade/暖冷对比（主人原词已有，保留）
5. **排除词收尾**：no text/watermark/cartoon/CG——防动画化

## 已排除路线

- 官方 10 个 minimaxh3_* embeddings 全为特效类（爆炸/子弹时间/魔法等），无时尚审美件，不下载
- 模型提示词理解无问题（古风词被正确执行为古装雪夜）——理解对，土是审美先验+步数问题

## 时间流逝转场（同地点跨时代）——fl2v 首尾帧是唯一正解

> 2026-10-01 定谳（timelapse A/B/C 三组对照）。主人原句"时间不断流逝，无缝切换到现代景象"没体现：
> **根因 = "时间流逝/无缝切换"是抽象概念和剪辑指令，t2v 单段采样只渲染视觉可见物、没有剪辑概念**（A 组原句被完全无视，输出 generic 街景平移）。
> 视觉化改写词（B 组）能拿到"时间流逝的观感"（天色渐变/灯光点亮/车流光轨）但建筑不会演变；
> **首尾帧路线（C 组）完整实现**：旧时代首帧 + 现代尾帧 → H3 fl2v 中间自动叠化演化，同视角跨时代无缝过渡。

### 做法（成品级四步，2026-10-01 晚定稿）

1. **先出"现代帧"**（尾帧）：Schnell t2i 出现代街景 1024×576。
2. **img2img 反向改绘"旧时代帧"（首帧）**——比双生成构图一致性高一个量级：
   - Schnell img2img：LoadImage(现代帧) → VAEEncode → KSampler **denoise 0.92**（🔴0.78 不够：schnell 是 cfg=1 蒸馏模型，低 denoise 下原图 latent 残留会把画面拉回原内容；0.92 时提示词完全主导、构图骨架保留）
   - 提示词要点：时代元素正面清单（qilou arcades/lanterns/rickshaws/changshan）+ **现代元素显式排除**（no glass towers/no neon/no LED/no cars）+ 同 seed 或换 seed 均可
   - 实测：0.78 失败（输出仍现代），0.92 完美改绘
3. **H3 fl2v**：`MiniMaxH3ImageToVideo` 同时挂 `first_frame` + `last_frame`，提示词写过渡过程（"人流车流飞速流动，天色由黄昏入夜，建筑从旧骑楼渐变为现代高楼，时间无缝流逝"）
4. **步数**：草稿 turbo 4 步（608×352×73 ≈ 80s）；**正片 24 步无 LoRA（同参数 ≈ 260s）**——亲看验收：1930s 老街精致开场 → 新旧叠化过渡清晰 → 无缝切入现代霓虹，全要素达标。

⚠️ 运维注记：8G 显存跑 24 步长任务偶发 CUDA unknown error（H3 权重换页触发，进程随之消亡）——重启 ComfyUI 显存清零后原参数续跑即可成功，属偶发非参数问题。脚本 `timelapse_pro_20261001.py`/`timelapse_d2_resume_20261001.py`。

### 要点

- t2v 里写"无缝切换/转场"类**剪辑指令一律无效**（模型没有剪辑概念）——需要转场就分两段生成后期剪，或用 fl2v 让模型自己演化
- "时间流逝"必须翻译成视觉物：天色变化、光轨、灯光点亮、人流 sped-up；这些词 B 组已验证有效，可与 fl2v 叠加
- 双帧一致性首选 img2img 改绘法（像素级同构图）；双生成（同骨架词+同 seed）次之，构图会有漂移


