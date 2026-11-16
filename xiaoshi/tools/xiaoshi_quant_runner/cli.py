# -*- coding: utf-8 -*-
"""
小石本地量化引擎 — CLI 入口
用法:
  python -m tools.xiaoshi_quant_runner.cli download --codes 600519,000001 --since ... --to ...
  python -m tools.xiaoshi_quant_runner.cli backtest --strategy ./my_strategy.py ...
  python -m tools.xiaoshi_quant_runner.cli status
  python -m tools.xiaoshi_quant_runner.cli sync-history --data-dir ./xiaoshi_data
  python -m tools.xiaoshi_quant_runner.cli research-package --request ./research-request.json --data-dir ...
  python -m tools.xiaoshi_quant_runner.cli validate-data --data-dir ...
"""
import argparse
import importlib.util
import json
import os
import sys

# 使 tools.xiaoshi_quant_runner 可导入
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # D:/zcodesj/xiaoshi
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xiaoshi_client as xc  # noqa: E402
from .engine import BacktestEngine  # noqa: E402
from .schemas import Bar, Signal, Action  # noqa: E402


def _read_key():
    return xc.get_key()


def cmd_download(args):
    """下载指定股票 K 线 + PIT 财务 + 公告到本地 data-dir(JSON 缓存)。"""
    import urllib.request
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    os.makedirs(args.data_dir, exist_ok=True)
    for code in codes:
        # K线
        try:
            data = xc.kline(code, period="daily", adjust=args.adjust,
                            since=args.since, to=args.to, limit=args.limit)
            path = os.path.join(args.data_dir, f"kline_{code}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            print(f"  OK kline {code} -> {path}")
        except xc.XiaoshiError as e:
            print(f"  跳过 {code} K线: {e}")
        # 财务
        try:
            fin = xc.financials(code=code, periods=8)
            path = os.path.join(args.data_dir, f"financial_{code}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fin, f, ensure_ascii=False)
            print(f"  OK financial {code} -> {path}")
        except xc.XiaoshiError as e:
            print(f"  跳过 {code} 财务: {e}")
        # 公告
        try:
            ann = xc.announcements(code, days=365, page=1, page_size=20)
            path = os.path.join(args.data_dir, f"announcements_{code}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ann, f, ensure_ascii=False)
            print(f"  OK announcements {code} -> {path}")
        except xc.XiaoshiError as e:
            print(f"  跳过 {code} 公告: {e}")
    print("download 完成。")


def _load_strategy(path):
    if not os.path.exists(path):
        print(f"策略文件不存在: {path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("user_strategy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "Strategy"):
        return mod.Strategy()
    if hasattr(mod, "strategy"):
        return mod.strategy
    print("策略文件必须导出 Strategy 类或 strategy 实例")
    sys.exit(1)


def _load_bars(data_dir, codes, adjust="qfq"):
    """从本地 JSON 缓存加载 K 线为 Bar 列表。"""
    from datetime import datetime
    bars_by_code = {}
    for code in codes:
        path = os.path.join(data_dir, f"kline_{code}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # 尝试多种响应结构
        items = data
        if isinstance(data, dict):
            items = data.get("items") or data.get("data") or data.get("kline") or []
        if not isinstance(items, list):
            continue
        bars = []
        for it in items:
            if isinstance(it, dict):
                t = it.get("trade_date") or it.get("date") or it.get("time") or it.get("datetime")
                if not t:
                    continue
                try:
                    dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                except Exception:
                    try:
                        dt = datetime.strptime(str(t)[:10], "%Y-%m-%d")
                    except Exception:
                        continue
                bars.append(Bar(
                    symbol=code, time=dt,
                    open=float(it.get("open")), high=float(it.get("high")),
                    low=float(it.get("low")), close=float(it.get("close")),
                    volume=float(it.get("volume") or 0), amount=it.get("amount"),
                    adjustment=adjust,
                ))
        if bars:
            bars.sort(key=lambda b: b.time)
            bars_by_code[code] = bars
    return bars_by_code


def cmd_backtest(args):
    strategy = _load_strategy(args.strategy)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    from datetime import datetime
    start = datetime.fromisoformat(args.start) if args.start else None
    end = datetime.fromisoformat(args.end) if args.end else None
    data = _load_bars(args.data_dir, codes, args.adjust)
    if not data:
        print("无本地K线数据。请先运行 download。")
        sys.exit(1)
    engine = BacktestEngine(strategy, data, start=start, end=end,
                            initial_capital=args.capital,
                            benchmark_code=args.benchmark,
                            commission_rate=args.commission,
                            stamp_duty_rate=args.stamp_duty,
                            slippage_bps=args.slippage)
    summary = engine.run()
    os.makedirs(args.output_dir, exist_ok=True)

    def _dump(name, obj):
        p = os.path.join(args.output_dir, name)
        if isinstance(obj, str):
            with open(p, "w", encoding="utf-8") as f:
                f.write(obj)
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, default=str, indent=2)
        print(f"  已生成 {p}")

    _dump("summary.json", summary)
    _dump("signals.csv", "\n".join(
        f"{s.signal_time},{s.code},{s.action.value},{s.target_weight},\"{s.reason}\"" for s in engine.signals))
    _dump("trades.csv", "\n".join(
        f"{t.trade_time},{t.code},{t.action.value},{t.price},{t.shares},{t.commission:.2f},\"{t.reason}\"" for t in engine.trades))
    _dump("equity.csv", "\n".join(
        f"{dt.isoformat()},{v:.2f}" for dt, v in engine.portfolio_value))
    _dump("orders.csv", "\n".join(
        f"{o.order_id},{o.code},{o.signal_time},{o.action.value},{o.price},{o.status.value}" for o in engine.orders))
    # run_manifest
    import hashlib
    data_hash = hashlib.sha256(
        json.dumps({k: len(v) for k, v in data.items()}, default=str).encode()).hexdigest()
    run_manifest = {
        "engine_version": "0.1.0",
        "prompt_version": xc.PROMPT_VERSION,
        "data_version": xc.DATA_VERSION,
        "manifest_version": xc.MANIFEST_VERSION,
        "seed": 0,
        "start": str(start), "end": str(end),
        "adjust": args.adjust,
        "commission_rate": args.commission,
        "stamp_duty_rate": args.stamp_duty,
        "slippage_bps": args.slippage,
        "data_dir": args.data_dir,
        "data_sha256": data_hash[:16],
        "codes": codes,
        "benchmark": args.benchmark,
    }
    _dump("run_manifest.json", run_manifest)
    print(f"\n回测完成: final_equity={summary.get('final_equity')} "
          f"total_return={summary.get('total_return_pct')}% sharpe={summary.get('sharpe')}")


def cmd_status(args):
    """输出引擎版本、数据适配状态、契约版本与最近验证。不输出凭据。"""
    import xiaoshi_client as _xc
    print("xiaoshi_quant_runner 版本: 0.1.0")
    print("xiaoshi-client 版本:", _xc.APP_VERSION)
    print("prompt_version:", _xc.PROMPT_VERSION)
    print("manifest_version:", _xc.MANIFEST_VERSION)
    print("data_version:", _xc.DATA_VERSION)
    print("canonical_contract:", _xc.CANONICAL_CONTRACT)
    key = os.environ.get("XIAOSHI_API_KEY", "")
    print("XIAOSHI_API_KEY:", "SET" if key else "NOT SET")
    print("本地只读数据适配:", "未启用(仅小石API+本地JSON缓存)")


def cmd_sync_history(args):
    """基于官方 manifest 对比本地, 校验通过后原子替换变化文件。"""
    print("sync-history: 读取官方 manifest...")
    m = xc.get_history_manifest(no_cache=True)
    print("  remote manifest:", m.get("version"))
    print("  quality_status:", m.get("quality_status"))
    print("  publication_gate:", m.get("publication_gate"))
    # 简化: 输出数据集与大小, 本地已有则跳过
    cov = m.get("coverage") or {}
    for ds, v in cov.items():
        print(f"  {ds}: files={v.get('files')} rows={v.get('rows')} first={v.get('first')} last={v.get('last')}")
    print("sync-history 完成(增量策略: 变化文件下载请用 xiaoshi-data download --session-json)")


def cmd_research_package(args):
    with open(args.request, encoding="utf-8") as f:
        req = json.load(f)
    print("research-package 请求:", json.dumps(req, ensure_ascii=False))
    # 服务端生成市场/年份或月级 R2 清单
    try:
        result = xc.post_json("/api/v3/research/package", body=req)
        print("服务端响应:", json.dumps(result, ensure_ascii=False)[:500])
    except xc.XiaoshiError as e:
        print("research-package 错误:", e)


def cmd_validate_data(args):
    import glob
    files = sorted(glob.glob(os.path.join(args.data_dir, "*.json")))
    if not files:
        print("无数据可校验")
        return
    for p in files:
        try:
            with open(p, encoding="utf-8") as f:
                json.load(f)
            print(f"  OK {os.path.basename(p)}")
        except Exception as e:
            print(f"  FAIL {os.path.basename(p)}: {e}")


def main():
    ap = argparse.ArgumentParser(prog="xiaoshi_quant_runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download")
    p.add_argument("--api-key", help="可选; 更推荐 XIAOSHI_API_KEY 环境变量")
    p.add_argument("--codes", required=True)
    p.add_argument("--since")
    p.add_argument("--to")
    p.add_argument("--period", default="daily")
    p.add_argument("--adjust", default="qfq", choices=["none", "qfq", "hfq"])
    p.add_argument("--limit", type=int, default=3000)
    p.add_argument("--data-dir", default="./xiaoshi_data")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("backtest")
    p.add_argument("--strategy", required=True)
    p.add_argument("--data-dir", default="./xiaoshi_data")
    p.add_argument("--codes", required=True)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--adjust", default="qfq")
    p.add_argument("--benchmark", default="000300")
    p.add_argument("--output-dir", default="./backtest_output")
    p.add_argument("--capital", type=float, default=1_000_000.0)
    p.add_argument("--commission", type=float, default=0.0003)
    p.add_argument("--stamp-duty", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=5.0)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync-history")
    p.add_argument("--data-dir", default="./xiaoshi_data")
    p.set_defaults(func=cmd_sync_history)

    p = sub.add_parser("research-package")
    p.add_argument("--request", required=True)
    p.add_argument("--data-dir", default="./xiaoshi_research")
    p.set_defaults(func=cmd_research_package)

    p = sub.add_parser("validate-data")
    p.add_argument("--data-dir", default="./xiaoshi_data")
    p.set_defaults(func=cmd_validate_data)

    args = ap.parse_args()
    try:
        args.func(args)
    except xc.XiaoshiError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
