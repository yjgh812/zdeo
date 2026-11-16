# RA2_v3 配置字段参考

## 顶层字段
| 字段 | 类型 | 含义 |
|------|------|------|
| `name` | str | 配置名称 |
| `version` | str | 版本日期 |
| `capital` | int | 总资金（默认 100,000） |
| `universe` | object | 选股范围 |
| `position` | object | 仓位与风控 |
| `regime` | object | 牛熊判定 |
| `bull` | object | 牛市配置 |
| `bear` | object | 熊市配置 |
| `backtest_evidence` | object | 回测证据 |
| `risk_discipline` | str | 风控纪律文字 |
| `retrain` | object | WF 重训管线元数据 |
| `source_files` | list[str] | 相关脚本/结果文件 |
| `discipline` | str | 纸面交易纪律 |

## universe
```json
{
  "a_share_only": true,
  "exclude_prefix": ["688", "689", "8", "920", "430"],
  "max_price": 20
}
```

## position
```json
{
  "max_positions": 50,
  "single_pct": 0.20,
  "stop_loss": -0.08
}
```

## regime
```json
{
  "index": "000001",
  "ma_window": 250,
  "bull_when_above": true
}
```

## bull / bear 配置
```json
{
  "config": "C2_二买共振_移动止盈",
  "prob_thr": 0.70,
  "need_second_buy": true,
  "tp": 0.0,
  "trailing_pct": 0.06,
  "trailing_trigger": 0.10,
  "max_hold": 5,
  "pos_cap": 5
}
```

## 牛熊判定逻辑（上证 000001）
- 取最近 250 个交易日收盘均值 `MA250`
- 若最新收盘 > MA250，判为牛市
- 若最新收盘 ≤ MA250，判为熊市
- 盘前信号使用最近收盘数据，因此开盘前已知 regime
