# Kronos 供应商代码（vendored）

- 来源：https://github.com/shiyu-coder/Kronos @master（AAAI 2026, "Kronos: A Foundation Model for the Language of Financial Markets"）
- 拉取方式：jsdelivr CDN（`cdn.jsdelivr.net/gh/shiyu-coder/Kronos@master/...`，2026-09-04；GitHub 直连在本机不可达，jsdelivr 为等价只读镜像）
- 文件：`model/kronos.py`、`model/module.py`、`model/__init__.py`（原样未改）+ `examples/prediction_cn_markets_day.py`（官方 A 股日线示例，作 API 依据）
- 权重：NeoQuasar/Kronos-base（409MB）+ NeoQuasar/Kronos-Tokenizer-base（16MB），经 hf-mirror 下载至仓库根 `hf_cache/`（不入 git）
- 用途：仅 `attribution/kronos_zs_stage1_20260904.py`（PREREG_KRONOS_ZS_STAGE1_20260904）研究使用，零生产接线
- 上游 License 以原仓库为准（本目录不修改、不再分发）
