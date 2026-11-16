#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xiaoshi-data — 小石历史数据本地命令行工具 (xiaoshi-data CLI)
=============================================================
遵循小石 history-public-v1 Manifest 契约:
- 先读 catalog / schema / coverage, 不遍历 R2 key 猜结构。
- 历史只经 R2 预签名下载(有效期7200s, 普通 GET 不带鉴权头), 逐文件校验 size+sha256。
- 财务历史只用 financial-as-reported, 必须传 as_of 并按 available_at/vintage_date 过滤。
- 429/bulk_download_required 为平台保护; 空结果/停牌/非交易日 != 0。

子命令:
  manifest            读取并展示历史 Manifest(含版本/校验和)
  catalog             列出 catalog 文件(可 --dataset/--market 过滤)
  schema             展示数据集 schema(数据字典)
  coverage           展示数据集覆盖范围
  download-session   请求 R2 预签名下载会话(打印文件清单与脱敏URL)
  download           按 dataset 契约批量下载到本地 canonical store
  query              查询本地 canonical store(dataset=cn-daily/cn-minute/...)
  freshness          本地 store 与远程 Manifest 新鲜度对比
  verify             校验本地 store 文件(size/sha256/schema/主键)

版本: xiaoshi-data-cli-2026-08-22.1
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xiaoshi_client as xc

STORE_DIR = os.environ.get("XIAOSHI_STORE_DIR", r"D:\zcodesj\xiaoshi\store")


def _pp(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_manifest(args):
    m = xc.get_history_manifest(no_cache=True)
    # 展示核心字段
    out = {
        "version": m.get("version"),
        "generated_at": m.get("generated_at"),
        "verified_at": m.get("verified_at"),
        "quality_status": m.get("quality_status"),
        "publication_gate": m.get("publication_gate"),
        "manifest_version": m.get("manifest_version"),
        "data_version": m.get("data_version"),
        "application_version": m.get("application_version"),
        "prompt_version": m.get("prompt_version"),
        "canonical_contract": m.get("canonical_contract"),
        "expires_in": m.get("expires_in"),
        "coverage": {k: {kk: vv for kk, vv in v.items() if kk in ("first", "last", "files", "rows", "bytes")}
                     for k, v in (m.get("coverage") or {}).items()},
        "datasets": {k: v.get("schema_version") for k, v in (m.get("datasets") or {}).items()},
    }
    _pp(out)


def cmd_catalog(args):
    params = {}
    if args.dataset:
        params["dataset"] = args.dataset
    if args.market:
        params["market"] = args.market
    cat = xc.history_catalog(**params)
    if args.json:
        _pp(cat)
        return
    items = cat.get("items", [])
    print(f"catalog version={cat.get('version')} contract={cat.get('canonical_contract')} "
          f"count={cat.get('count')} total={cat.get('total')}")
    if args.limit:
        items = items[: args.limit]
    for it in items:
        print("{}\t{}\t{}\t{}\t{} rows\t{} bytes\t{}..{}\t{}".format(
            it.get("dataset"), it.get("market"), it.get("year") or it.get("date") or it.get("code") or "",
            it.get("adjust") or it.get("adjustment") or "", it.get("rows"), it.get("size"),
            it.get("first_date"), it.get("last_date"), it.get("quality_status")))


def cmd_schema(args):
    m = xc.get_history_manifest()
    datasets = m.get("schemas") or m.get("datasets") or {}
    if args.dataset:
        if args.dataset not in datasets:
            print(f"数据集 {args.dataset} 不在 Manifest 中。可用: {sorted(datasets.keys())}")
            sys.exit(1)
        _pp(datasets[args.dataset])
        return
    _pp(datasets)


def cmd_coverage(args):
    m = xc.get_history_manifest()
    cov = m.get("coverage") or {}
    if args.dataset:
        if args.dataset not in cov:
            print(f"数据集 {args.dataset} 无覆盖记录。可用: {sorted(cov.keys())}")
            sys.exit(1)
        _pp(cov[args.dataset])
        return
    _pp(cov)


def cmd_download_session(args):
    # 依据 download_contracts 校验必需参数
    # 注意: financial-as-reported 的 date 是 vintage_date(快照日, 见 catalog 分区),
    # 不是报告期; as_of 必须 >= 该文件的 min_available_at, 否则按 PIT 契约返回 404。
    contracts = {
        "cn-minute": ["code"],
        "cn-daily": ["year", "adjust"],
        "adjustment-factors": ["year", "adjust"],
        "global-daily": ["market", "year"],
        "sector-constituents": ["date"],
        "event-timeline": ["date"],
        "financial-current": ["year"],
        "financial-as-reported": ["date", "as_of"],
        "min1-market": None,
    }
    _PIT_NOTE = {
        "financial-as-reported": "date 传 vintage_date(见 catalog 分区, 如 2026-08-14); "
                                 "as_of 传带时区 ISO8601 且 >= 该文件 min_available_at。"
    }
    if args.dataset not in contracts:
        print(f"未知数据集 {args.dataset}. 可用: {sorted(contracts.keys())}")
        sys.exit(1)
    if contracts[args.dataset] is None:
        print("min1-market 返回 bulk_download_required; 全市场分钟请用 `xiaoshi-data query --dataset cn-minute` 查本地 canonical store。")
        sys.exit(1)
    if args.dataset in _PIT_NOTE:
        print("[提示]", _PIT_NOTE[args.dataset])
    params = {"dataset": args.dataset}
    for p in contracts[args.dataset]:
        v = getattr(args, "param_" + p, None) or getattr(args, p, None)
        if not v:
            print(f"数据集 {args.dataset} 需要参数: --param-{p} (如 --param-year 2024 --param-adjust qfq)")
            sys.exit(1)
        params[p] = v
    # 可选参数
    for opt in ("since", "to", "code", "event_type"):
        v = getattr(args, "param_" + opt, None) or getattr(args, opt, None)
        if v:
            params[opt] = v
    session = xc.history_download_session(**params)
    if args.json:
        _pp(session)
        return
    files = session.get("files", [])
    print("delivery:", session.get("delivery"), "format:", session.get("format"),
          "expires_in:", session.get("expires_in"), "scope:", session.get("scope"))
    print("verify:", session.get("verify"))
    print("filter_policy:", session.get("filter_policy"))
    for f in files:
        url = f.get("url", "")
        # 脱敏 URL: 仅显示前 100 字符
        print("{}\t{} bytes\t{} rows\t{}".format(
            f.get("key"), f.get("size"), f.get("rows"),
            (url[:90] + "...") if len(url) > 90 else url))


def cmd_download(args):
    """下载 download-session 返回的全部文件到本地 canonical store, 逐文件校验 size+sha256。"""
    if not args.session_json:
        print("请先用 `xiaoshi-data download-session ... --json > session.json` 保存会话, 再 `download --session-json session.json`")
        sys.exit(1)
    with open(args.session_json, "r", encoding="utf-8") as f:
        session = json.load(f)
    files = session.get("files", [])
    print(f"下载 {len(files)} 个文件到 {STORE_DIR}")
    import urllib.request
    import hashlib
    os.makedirs(STORE_DIR, exist_ok=True)
    ok = 0
    for f in files:
        url = f.get("url")
        key = f.get("key")
        size = f.get("size")
        sha = f.get("sha256")
        if not url or not key:
            print("  SKIP(无url/key):", key)
            continue
        dest = os.path.join(STORE_DIR, key.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            req = urllib.request.Request(url)  # 普通 GET, 不带鉴权头
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except Exception as e:
            print(f"  下载失败 {key}: {e}")
            continue
        if size is not None and len(data) != size:
            print(f"  size 不匹配 {key}: 期望 {size}, 实际 {len(data)}")
            continue
        if sha:
            actual = hashlib.sha256(data).hexdigest()
            if actual != sha:
                print(f"  sha256 不匹配 {key}")
                continue
        with open(dest, "wb") as fh:
            fh.write(data)
        print(f"  OK {key} ({len(data)} bytes)")
        ok += 1
    print(f"完成: {ok}/{len(files)}")


def cmd_query(args):
    """查询本地 canonical store(Parquet)。"""
    if not args.dataset:
        print("需要 --dataset (cn-daily/cn-minute/adjustment-factors/global-daily/financial-current/financial-as-reported 等)")
        sys.exit(1)
    try:
        import pandas as pd
    except ImportError:
        print("需要 pandas 以查询 Parquet")
        sys.exit(1)
    # 收集匹配文件
    pattern = os.path.join(STORE_DIR, "**", "*")
    matches = []
    import glob
    for p in glob.glob(pattern, recursive=True):
        if p.endswith(".parquet"):
            matches.append(p)
    if not matches:
        print(f"本地 canonical store({STORE_DIR}) 无 Parquet。请先 `xiaoshi-data download ...`。")
        return
    print(f"本地文件: {len(matches)}")
    for p in matches:
        print("  ", os.path.relpath(p, STORE_DIR))
    if not args.sample:
        return
    # 采样第一个文件
    df = pd.read_parquet(matches[0])
    print(f"\n[{os.path.basename(matches[0])}] rows={len(df)}")
    print(df.head(args.sample).to_string())


def cmd_freshness(args):
    """本地 store 与远程 Manifest 新鲜度对比(仅统计, 不自动下载)。"""
    m = xc.get_history_manifest(no_cache=True)
    print("remote manifest:", m.get("version"), "data_version:", m.get("data_version"))
    print("quality_status:", m.get("quality_status"))
    print("publication_gate:", m.get("publication_gate"))
    # 本地检查
    n_local = 0
    import glob
    for p in glob.glob(os.path.join(STORE_DIR, "**", "*.parquet"), recursive=True):
        n_local += 1
    print(f"local store: {STORE_DIR} parquet files={n_local}")
    # 按数据集汇总远程覆盖
    cov = m.get("coverage") or {}
    print("remote coverage:")
    for ds, v in cov.items():
        print("  {}: {}..{} files={} rows={}".format(ds, v.get("first"), v.get("last"), v.get("files"), v.get("rows")))


def cmd_verify(args):
    """校验本地 store 文件(存在性 + 记录主键 + 基础质量)。"""
    import glob
    files = sorted(glob.glob(os.path.join(STORE_DIR, "**", "*.parquet"), recursive=True))
    if not files:
        print(f"本地 canonical store({STORE_DIR}) 无 Parquet 可校验。")
        return
    import pandas as pd
    total_rows = 0
    for p in files:
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            print(f"  FAIL 无法读取 {os.path.relpath(p, STORE_DIR)}: {e}")
            continue
        total_rows += len(df)
        # 主键去重检查
        dup = 0
        for pk in (["market", "symbol", "trade_date", "adjustment"],
                   ["market", "symbol", "trade_time"], ["event_id"]):
            if all(c in df.columns for c in pk):
                dup = int(df.duplicated(subset=pk).sum())
                break
        print("  {} rows={} dup_pk={} cols={}".format(
            os.path.relpath(p, STORE_DIR), len(df), dup, len(df.columns)))
    print(f"verify: {len(files)} files, {total_rows} rows")


def main():
    ap = argparse.ArgumentParser(prog="xiaoshi-data", description="小石历史数据本地命令行工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("manifest", help="读取并展示历史 Manifest")
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("catalog", help="列出 catalog 文件")
    p.add_argument("--dataset")
    p.add_argument("--market")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("schema", help="展示数据集 schema")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("coverage", help="展示数据集覆盖范围")
    p.add_argument("--dataset")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("download-session", help="请求 R2 预签名下载会话")
    p.add_argument("--dataset", required=True)
    p.add_argument("--param-year")
    p.add_argument("--param-adjust")
    p.add_argument("--param-market")
    p.add_argument("--param-date")
    p.add_argument("--param-as_of")
    p.add_argument("--param-code")
    p.add_argument("--param-since")
    p.add_argument("--param-to")
    p.add_argument("--param-event_type")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_download_session)

    p = sub.add_parser("download", help="按会话下载到本地 canonical store")
    p.add_argument("--session-json", required=True)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("query", help="查询本地 canonical store")
    p.add_argument("--dataset")
    p.add_argument("--sample", type=int, default=5)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("freshness", help="本地与远程新鲜度对比")
    p.set_defaults(func=cmd_freshness)

    p = sub.add_parser("verify", help="校验本地 store")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    try:
        args.func(args)
    except xc.XiaoshiError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
