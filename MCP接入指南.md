# MCP 接入指南（crawler-mvp）

> 整理时点：2026-11-19　|　前置：《MCP爬虫工具集成调研报告.md》+ 已跑通的 crawler-mvp
> 你的情况：**大模型不在本机** → 主方案用 Streamable HTTP 远程挂载；stdio 作为本机备选。

---

## 0. 一句话现状

爬虫 MCP 已经做出来了，**4 个工具 + 双传输 + 5 层安全闸门，全部实测通过**。它不是从零写的通用爬虫，而是把咱们已跑通的 `crawler-mvp` 管线包成大模型能调用的工具——合规边界（robots/限速/脱敏/白名单）全部继承。

---

## 1. 四个工具（大模型能调用的）

| 工具 | 作用 | 联网？ | 风险 |
|---|---|---|---|
| `query_local` | 查已入库的 22 篇公告（可站点过滤），返回带出处的结论 | 否 | 零 |
| `search_site` | 按关键词列标题+URL，先发现资料再决定读哪篇（省 token） | 否 | 零 |
| `fetch_page` | 受控实时抓单个白名单 URL，返回脱敏+不可信标注的正文 | 是（受控） | 低（白名单闸门） |
| `research` | 多轮调研闭环：拆解→多关键词检索→带出处汇总简报 | 默认否 | 零（可开启受控抓取） |

**模型该怎么用**：默认先 `query_local` / `research`（不联网、零风险）；发现需要最新公告且 URL 是白名单站点时，再 `fetch_page`。工具描述里已写明这条工作流，模型会看到。

---

## 2. 启动服务（二选一）

### 方案 A：Streamable HTTP（你的情况——大模型不在本机）

```bash
cd C:/爬虫项目调研/crawler-mvp
.venv/Scripts/python.exe src/mcp_server.py --transport streamable-http --host 0.0.0.0 --port 8765
```

- 端点：`http://<你的机器IP>:8765/mcp`
- `--host 0.0.0.0` 允许外部连接；**必须配合下面的访问控制**，否则等于把爬虫能力暴露给全网。
- 实测：FastMCP 客户端经此端点成功列出 4 工具并调用（2026-11-19）。

### 方案 B：stdio（本机客户端，如本机 Claude Code）

```bash
.venv/Scripts/python.exe src/mcp_server.py --transport stdio
```

Claude Code 挂载命令（本机场景）：
```bash
claude mcp add --transport stdio crawler-mvp -- C:/爬虫项目调研/crawler-mvp/.venv/Scripts/python.exe C:/爬虫项目调研/crawler-mvp/src/mcp_server.py
```

---

## 3. 远程挂载的三种接法（按你的大模型形态选）

你说大模型不在本机，具体形态我猜不了，这里给三种最常见的接法。**你只要告诉我用哪种，我可以帮你把配置写好并测通**：

1. **对方客户端支持 MCP（如 Claude Desktop / Cursor / 自建 Agent 放在别的机器）**：在它的 MCP 配置里加一行 HTTP server：
   ```json
   {"mcpServers": {"crawler-mvp": {"url": "http://<你的机器IP>:8765/mcp"}}}
   ```
2. **你通过 API 调大模型、自己写 agent 循环**：用任意语言的 MCP 客户端连 HTTP 端点（Python 用 `fastmcp.Client("http://.../mcp")`），把 `tools/list` 拿到的大模型工具定义喂给你的模型，模型返回 tool_call 后调 `tools/call`。
3. **模型侧没有 MCP 客户端**：那就用 `research` 工具当"一步到位"的调研接口——你的应用调一次 `research`，拿到带出处的简报直接拼进 prompt，不需要模型自己编排工具。

---

## 4. 安全边界（已实测，别拆）

调研报告第 5 节的防御全部落地在 `src/mcp_security.py`，端到端测试逐条验证：

| 防线 | 实现 | 实测结果 |
|---|---|---|
| 出网白名单 | 仅 5 个已验证境内考试站 | 非白名单域名 `example.com` 被拒 ✅ |
| SSRF 防护 | 拦私网/localhost/云元数据/DNS 重绑定/非 80-443 端口 | `169.254.169.254`、`localhost`、`:8443` 全部被拒 ✅ |
| robots | RFC9309（4xx 放行/5xx 全禁），1h 缓存 | 真实抓取时正常判定 ✅ |
| 限速留痕 | 每域 1 req/s + 抖动，JSONL 全程留痕 | 抓取实测限速，审计日志 11 条 ✅ |
| 不可信内容 | nonce 包裹 `<untrusted_content>` + 剥隐藏文本/零宽字符 + 注入告警 | 埋的 "ignore previous instructions" 触发 `SUSPICIOUS_INSTRUCTION_PATTERN` ✅ |
| 幂等缓存 | 同 URL 24h 不重抓 | 第二次抓取 `from_cache=True` ✅ |
| 最小权限 | 只有读工具，无写/删/执行 | 工具集仅 4 个只读工具 ✅ |

**⚠️ 远程部署的两条硬提醒**：
1. `--host 0.0.0.0` 会把 MCP 暴露到局域网。当前**没有鉴权**（调研报告明确 MCP 协议本身不管鉴权）。务必放在内网、配防火墙只放可信 IP，或前面挂一层带 OAuth/API key 的网关。别直接暴露公网。
2. 白名单是核心资产——**不要为了方便把任意 URL 加进去**。加站点前先跑 `src/probe_sites.py` 验证 robots 与正文质量，走一遍当初选这 5 个站的标准。

---

## 5. 端到端测试（可复跑）

```bash
.venv/Scripts/python.exe tests/test_mcp.py
```

实测输出（2026-11-19）：
```
[1] 工具注册: ['query_local', 'search_site', 'fetch_page', 'research']
[2] query_local: count=3
[3] search_site: 命中 13 篇
[4] research: 资料 10 篇, 站点 5 个
[5] 拦截 云元数据 / localhost / example.com / 非标端口 => 4/4 DENIED OK
=== 全部测试通过 ===
```

另实测：M2 真实抓取（1028 字正文 + nonce 标注）、缓存幂等、注入告警、远程 HTTP 客户端连接+调用——全部通过。

---

## 6. 依赖与版本（已 pin 死）

- FastMCP **4.2.2**（含 MCP SDK 2.3.0），2026-11-19 实测
- pydantic 由 2.11.7 升级到 **2.14.3**（FastMCP 要求；升级后回归自检 4/4 通过）
- scrapy 2.13.3 / twisted 24.11.0 / trafilatura 2.0.0 不变
- 全部版本在 `requirements.txt`；换机器照它重装即可

**新增文件**：`src/mcp_server.py`（server + 4 工具 + 双传输）、`src/mcp_security.py`（安全闸门）、`tests/test_mcp.py`（端到端测试）、`data/mcp_audit.jsonl`（留痕）、`data/mcp_cache/`（幂等缓存）。

---

## 7. 还差你确认的一件事

**你的大模型具体是什么形态？**（本机 Claude Code？别处的 Claude/Cursor？自己调 API 写 agent？纯 API 没 MCP 客户端？）对应第 3 节四种接法之一。告诉我，我把配置和联调直接做完。

无论哪种，server 已经能跑、工具已经能用——接法只是"最后一百米"。
