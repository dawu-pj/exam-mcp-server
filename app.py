"""云部署 ASGI 入口：供 uvicorn / Cloud Run / 云函数 / FastMCP Gateway 使用。

用法：
  uvicorn app:app --host 0.0.0.0 --port 8000
  gunicorn app:app -k uvicorn.workers.UvicornWorker        # 多 worker
  fastmcp run src/mcp_server.py:mcp --transport http       # FastMCP Gateway/CLI

stateless_http=True：云端多实例/自动扩缩容时，不依赖服务端 session，
每个请求自带上下文；单实例本地跑也不影响。对应 100 词框架里"无状态可水平扩展"。
数据路径基于 __file__，从任意工作目录启动都能找到 data/clean。
"""
from src.mcp_server import mcp

# 生产 ASGI app：默认 /mcp 端点，无状态模式适配云端多实例
app = mcp.http_app(stateless_http=True)
