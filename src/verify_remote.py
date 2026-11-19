"""验证线上 MCP 端点：OAuth 设备码换 token -> 列出工具 -> 实测调用。

用法：
  1. 打开 data/oauth_probe/device_flow.json 里的 verification_uri_complete，用部署账号登录确认
  2. 运行：.venv/Scripts/python.exe src/verify_remote.py
  3. 脚本每 5 秒轮询 token，最多 5 分钟；拿到 token 后自动连线上端点做端到端验证

注意：token 只存内存，不落盘、不入 git。
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FLOW_FILE = ROOT / "data" / "oauth_probe" / "device_flow.json"
MCP_URL = "https://exam-intel.fastmcp.app/mcp"


def poll_token(flow: dict, timeout: int = 300) -> str:
    """设备码轮询换 access_token。用户未确认时返回 authorization_pending，继续等。"""
    deadline = time.time() + timeout
    interval = 5
    print("等待授权确认中……（每 5 秒轮询一次，最多 5 分钟）")
    while time.time() < deadline:
        try:
            r = requests.post(
                flow["token_endpoint"],
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": flow["device_code"],
                    "client_id": flow["client_id"],
                },
                timeout=20,
            )
            body = r.json()
        except Exception as e:
            print("  轮询异常:", type(e).__name__, str(e)[:80])
            time.sleep(interval)
            continue

        if r.status_code == 200 and "access_token" in body:
            print("授权成功，已拿到 access_token（仅内存）")
            return body["access_token"]

        err = body.get("error", "")
        if err == "authorization_pending":
            print("  仍未确认……")
        elif err == "slow_down":
            interval += 2
        elif err == "expired_token":
            raise RuntimeError("设备码已过期，请重新生成")
        elif err == "access_denied":
            raise RuntimeError("授权被拒绝")
        else:
            print("  其他响应:", str(body)[:120])
        time.sleep(interval)

    raise TimeoutError("等不到授权确认（5 分钟超时）")


async def verify(token: str) -> None:
    """用 FastMCP 客户端带 Bearer token 连接线上端点，实测工具。"""
    sys.path.insert(0, str(ROOT))
    from fastmcp import Client
    from fastmcp.client.auth import BearerAuth

    async with Client(MCP_URL, auth=BearerAuth(token=token)) as c:
        tools = await c.list_tools()
        names = [t.name for t in tools]
        print("\n=== 线上工具列表 ===")
        print("  ", names)

        print("\n=== 实测 query_local ===")
        r = await c.call_tool("query_local", {"query": "考研报名", "top_k": 3})
        data = r.data
        print("  count:", data.get("count"))

        print("\n=== 实测 research ===")
        r2 = await c.call_tool("research", {"question": "四六级报名流程", "max_rounds": 2})
        d2 = r2.data
        print("  资料数:", len(d2.get("资料", [])), "| 站点:", d2.get("覆盖站点"))

        print("\n=== 实测 SSRF 拦截（应 DENIED）===")
        r3 = await c.call_tool("fetch_page", {"url": "http://169.254.169.254/latest/meta-data/"})
        print("  ", str(r3.data)[:160])


def main() -> int:
    flow = json.loads(FLOW_FILE.read_text(encoding="utf-8"))
    age = time.time() - flow["created_at"]
    if age > 290:
        print("警告：设备码可能已过期（生成于 %.0f 秒前），如失败请重新走设备流程" % age)
    token = poll_token(flow)
    asyncio.run(verify(token))
    print("\n线上端点验证完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
