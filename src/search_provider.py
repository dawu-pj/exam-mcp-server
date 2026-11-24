"""发现层搜索 provider：可插拔，默认 Brave。

设计（对齐《通用化调研_MCP工具形态》§2/§3）：
- 出网主体是搜索服务商，我方不直连任意主机 → 发现层不走 allow_url()。
- Brave API：https://api.search.brave.com/res/v1/web/search
  key 从环境变量 BRAVE_API_KEY 读；无 key 时明确降级，不崩溃、不伪造结果。
- 只返回标题/URL/摘要/日期等元数据，不抓正文、不自动触发抓取。
- fetcher 可注入 → 离线测试用 mock 响应，no_network 保险下不误触发。

定价依据（2026 一手/三方交叉，落地前复核官方账单）：
  Brave Web Search $5/1000 次；每月 $5 抵用金 ≈ 1000 次；本模块按 0.005 USD/次估算。
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_COST_PER_CALL_USD = 0.005

MAX_RESULTS_HARD = 10
MAX_SNIPPET_CHARS_HARD = 800


class SearchUnavailable(Exception):
    """provider 不可用（无 key / 网络错误 / HTTP 错误）——由工具层转成明确提示。"""


def brave_search(
    query: str,
    *,
    max_results: int = 5,
    freshness: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    max_snippet_chars: int = 300,
    api_key: str | None = None,
    fetcher=None,
) -> dict:
    """调用 Brave Web Search，返回规范化候选列表 + 成本留痕。

    Args:
        fetcher: 可注入 HTTP GET（签名同 requests.get），离线测试用 mock。
        api_key: 显式传入优先，否则读环境变量 BRAVE_API_KEY。

    Returns:
        {results, provider, estimated_cost_usd, query_sent}；无 key 抛 SearchUnavailable。
    """
    key = api_key or os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        raise SearchUnavailable(
            "未配置 BRAVE_API_KEY。请在境外节点设置环境变量 BRAVE_API_KEY 后重试；"
            "或使用 query_local 查已入库快照（无需 key）。"
        )

    n = max(1, min(max_results, MAX_RESULTS_HARD))
    snippet_cap = max(80, min(max_snippet_chars, MAX_SNIPPET_CHARS_HARD))
    params = {"q": query.strip(), "count": n}
    if freshness:
        params["freshness"] = freshness
    if include_domains:
        params["q"] += " " + " ".join(f"site:{d}" for d in include_domains[:5])
    if exclude_domains:
        params["q"] += " " + " ".join(f"-site:{d}" for d in exclude_domains[:5])

    get = fetcher
    if get is None:
        import requests
        get = requests.get
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": key,
        "User-Agent": "StudyDeskResearchBot/0.1 (public info research)",
    }
    try:
        resp = get(BRAVE_ENDPOINT, params=params, headers=headers, timeout=20)
    except Exception as e:
        raise SearchUnavailable(f"Brave 请求失败：{type(e).__name__}: {str(e)[:120]}") from e
    if resp.status_code != 200:
        raise SearchUnavailable(f"Brave HTTP {resp.status_code}（key 无效 / 额度用尽 / 限流）")

    try:
        payload = resp.json()
    except Exception as e:
        raise SearchUnavailable(f"Brave 响应非 JSON：{type(e).__name__}") from e

    raw = (payload.get("web") or {}).get("results") or []
    results = []
    for item in raw[:n]:
        url = str(item.get("url", ""))
        host = ""
        try:
            host = urlsplit(url).hostname or ""
        except Exception:
            pass
        results.append({
            "title": str(item.get("title", ""))[:200],
            "url": url,
            "site": host,
            "snippet": str(item.get("description", ""))[:snippet_cap],
            "published_at": item.get("age") or item.get("page_age"),
        })
    return {
        "results": results,
        "provider": "brave",
        "estimated_cost_usd": BRAVE_COST_PER_CALL_USD,
        "query_sent": params["q"],
    }
