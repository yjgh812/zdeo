"""
财联社API签名库
================
签名算法逆向自 cls.cn 前端JS (2026-08-15):

模块链路:
  85073 (请求封装, 默认参数 app/os/sv)
  → 36498 (参数键排序, key=value&key=value, null跳过)
  → 37371 (SHA-1, sync=digest, 输出hex)
  → 81383 (MD5)

  最终: sign = md5( sha1_hex( sorted_querystring ) )

接口: /v1/roll/get_roll_list (电报)
      /v3/depth/home/assembled/{id} (深度)
"""

import hashlib
import requests
from datetime import datetime

BASE = "https://www.cls.cn"
DEFAULT_PARAMS = {
    "app": "CailianpressWeb",
    "os": "web",
    "sv": "8.7.9",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.cls.cn/telegraph",
}


def cls_sign(params: dict) -> str:
    """财联社API签名: md5(sha1_hex(排序参数串))"""
    parts = []
    for k in sorted(params.keys()):
        v = params[k]
        if v is None:
            continue
        parts.append(f"{k}={v}")
    query = "&".join(parts)
    sha1_hex = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_hex.encode("utf-8")).hexdigest()


def _signed_params(extra: dict = None) -> dict:
    """构造带签名的请求参数"""
    p = dict(DEFAULT_PARAMS)
    if extra:
        p.update({k: str(v) for k, v in extra.items()})
    p["sign"] = cls_sign(p)
    return p


def get_telegraph(rn: int = 30, last_time: int = None, only_important: bool = False) -> list:
    """
    获取财联社电报列表

    前端真实调用(逆向自telegraph页面JS):
      {refresh_type: 1, rn: 20, last_time: <当前秒级时间戳或游标>, category: nav}

    Args:
        rn: 条数 (1-50, 前端用20, 实测50可用, 100会被拒)
        last_time: 分页游标(上一批最早条目的ctime), None=当前时间往前取
        only_important: 只看重点(level A/B)

    Returns:
        list[dict]: [{ctime, title, content, level, ...}]
    """
    import time as _time
    extra = {
        "refresh_type": 1,
        "rn": min(rn, 50),
        "last_time": int(last_time if last_time else _time.time()),
    }
    params = _signed_params(extra)
    resp = requests.get(f"{BASE}/v1/roll/get_roll_list", params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errno") not in (0, None):
        raise RuntimeError(f"CLS API错误: errno={data.get('errno')} msg={data.get('msg')}")
    items = data.get("data", {}).get("roll_data", [])
    if only_important:
        items = [i for i in items if i.get("level") in ("A", "B")]
    return items


def format_telegraph(items: list, with_content: bool = True) -> str:
    """格式化电报为可读文本"""
    lines = []
    for it in items:
        t = datetime.fromtimestamp(it.get("ctime", 0)).strftime("%m-%d %H:%M")
        level = it.get("level", "")
        tag = {"A": "🔴加红", "B": "🟠重点", "C": "", "D": ""}.get(level, "")
        title = it.get("title") or ""
        content = it.get("content", "") or it.get("brief", "")
        # 去除HTML标签
        import re
        content = re.sub(r"<[^>]+>", "", content)
        head = f"[{t}]{(' ' + tag) if tag else ''} {title}" if title else f"[{t}]{(' ' + tag) if tag else ''}"
        if with_content and content:
            head += f"\n  {content[:150]}"
        lines.append(head)
    return "\n".join(lines)


if __name__ == "__main__":
    # 自测
    items = get_telegraph(rn=10)
    print(f"✅ 获取 {len(items)} 条电报")
    print(format_telegraph(items[:5]))