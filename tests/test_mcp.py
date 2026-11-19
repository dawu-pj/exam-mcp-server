"""MCP Server 端到端测试：用 FastMCP 内存客户端实测四个工具。

覆盖：
- 工具注册（4 个）
- M1 query_local / search_site
- M3 research 闭环
- 安全闸门：SSRF（云元数据/localhost）、非白名单域名、非白名单端口必须被拒
运行：.venv/Scripts/python.exe tests/test_mcp.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastmcp import Client
from src.mcp_server import mcp


async def main():
    print("=== MCP 端到端测试 ===")
    async with Client(mcp) as c:
        tools = await c.list_tools()
        names = [t.name for t in tools]
        print(f"[1] 工具注册: {names}")
        assert set(names) == {"query_local", "search_site", "fetch_page", "research"}, "工具集不符"

        # M1 query_local
        r = await c.call_tool("query_local", {"query": "四六级报名条件", "top_k": 3})
        data = r.data
        print(f"[2] query_local: count={data['count']}, top={data['results'][0]['title'][:30] if data['results'] else 'NONE'}")
        assert data["count"] > 0, "检索无结果"

        # M1 search_site
        r = await c.call_tool("search_site", {"keyword": "报名"})
        data = r.data
        print(f"[3] search_site: 命中 {data['count']} 篇")

        # M3 research
        r = await c.call_tool("research", {"question": "2026 年下半年四六级报名有什么新变化", "max_rounds": 3})
        data = r.data
        print(f"[4] research: 资料 {data['brief']['资料数量']} 篇, 站点 {data['brief']['覆盖站点']}")
        assert data["brief"]["资料数量"] > 0, "调研无发现"

        # 安全闸门：SSRF 与非白名单
        blocked = [
            "http://169.254.169.254/latest/meta-data/",   # 云元数据
            "http://localhost/admin",                      # 本机
            "https://example.com/exam",                    # 非白名单
            "https://www.neea.edu.cn:8443/secret",         # 非白名单端口
        ]
        for url in blocked:
            r = await c.call_tool("fetch_page", {"url": url})
            data = r.data
            denied = "error" in data and ("安全闸门拒绝" in data["error"] or "robots" in data["error"])
            print(f"[5] 拦截 {url[:45]:45} => {'DENIED OK' if denied else '!!! NOT BLOCKED: ' + str(data)[:60]}")
            assert denied, f"安全闸门未拦截：{url}"

    print("=== 全部测试通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
