# 通用化调研（三）：MCP 工具形态与发现/抓取分层

> 调研时间：2026-11-24。子代理一手调研（官方 README / GitHub 源码 / 本地源码 / 定价页）。
> 标注：【一手】=官方文档/源码直读；【三方】=第三方带核查日期；【推断】=设计建议。
> 汇总文件见 `通用爬虫演进调研与实施方案.md`。

## 结论先行

1. **不要自己再造抓取引擎**：发现层用商用搜索 API（Brave/Tavily/Exa），抓取层复用现有 `trafilatura + mcp_security.py` 管线。一手工具（Tavily/Exa/Firecrawl）都采用"搜索返摘要 → 按需取全文"两段式，且显式限制返回体积。
2. **"通用目标 vs 白名单"的可行解 = 分层**：发现层可搜任意公开页（由搜索服务商完成爬取，我方不直接出网），抓取层仍走 `allow_url()` 逐域闸门 + 风险分级；搜索结果**绝不自动升级**为抓取。`allow_fetch` 保持默认 False、按 URL 显式授权。
3. **长任务用 FastMCP 原生能力**：项目已在 FastMCP 4.2.2，官方 4.0.0 起支持 `@mcp.tool(task=True)` + TasksExtension（SEP-2663，`io.modelcontextprotocol/tasks`），自带 task_id/轮询/Progress。**无需自研队列**，但要求 2026-07-28 协议模式，旧客户端退化同步 → 必须保留同步 fallback + `research_status` 兜底。
4. **2026 定价格局洗牌**：Bing Search API 2025-08-11 退役；Google CSE 新客关闭、2027-01-01 停服。主力：Brave（$5/千次、自有索引、无抓取法律风险）、Tavily（$0.008/credit、agent 原生）、Exa（$4–12/千次、直接返正文）。
5. **必须保留护栏**：所有外部文本（含搜索摘要）一律 `sanitize_untrusted()` + `wrap_untrusted(nonce)`；OWASP LLM01 明确间接注入无完全防御，目标是缩小攻击面+留痕+高风险动作人工确认。
6. **成本与 token 是同一件事**：工具签名必须内置 `max_results/max_chars/token_budget/depth` 硬上限；返回 `usage.est_cost_usd`；超预算直接拒绝不静默截断。

## 一、通用 Web Research MCP 工具对比（一手）

| 工具 | 核心签名 | 摘要/全文控制 | 分页 | 异步 |
|---|---|---|---|---|
| Firecrawl | `firecrawl_scrape/search/map/crawl/extract` | `formats=["json",{schema}]` 首选防 context overflow；`onlyMainContent`；`scrapeOptions` 内嵌 | `limit` / `maxDiscoveryDepth` | crawl 大响应，官方建议保持 limit 低 |
| Tavily | `tavily_search/extract/crawl/map/research` | `include_raw_content`；crawl 每页截断 500 字符，全文需 map→extract | `max_results`/`limit` | 独立 `tavily_research`（大输出，谨慎） |
| Exa | `web_search_exa/web_search_advanced_exa/web_fetch_exa` | `contents.highlights/text/summary` + `maxCharacters` | `numResults` | **`deep_researcher_start/check`** 现成异步 |
| Brave | `brave_web_search/summarizer`（v2.x） | **只返摘要+URL 无正文**；LLM Context 端点 | `count`+`offset≤9` | 无 |
| Fetch MCP | `fetch(url, max_length=5000, start_index=0, raw, timeout)` | `raw` 控制 markdown；**`start_index` 字符偏移分块** | 字符偏移 | 无 |

可借鉴点：Brave v2 删掉结果 base64 图片（省上下文）；Firecrawl scrape 单次一 URL、批量走 API（MCP 工具要小而可组合）；Exa 收敛接口用参数区分模式；Tavily 文档有 Output Size Awareness 表。

## 二、定价表（2026，核查日期见调研原文）

| 供应商 | 免费 | 每千次 | 返回 | 商业 |
|---|---|---|---|---|
| Brave | 每月 $5 抵用金（≈1000 次） | $5/1k；Answers $4/1k+$5/1M token；50 QPS | 摘要+URL，LLM Context | 可，ZDR/SOC2 |
| Tavily | 1000 credits/月免卡 | $0.008/credit；basic=1/advanced=2；research 4–250/次 | 摘要+`include_raw_content` 全文 | 可 |
| Exa | $10/月+$10 注册，10 QPS | Lite $4/Auto $7/Deep $12/1k；Contents $1/1k 页 | **直接返正文+highlights** | 可，ZDR/SOC2/HIPAA |
| Serper | 2500 次一次性 | $1/1k→$0.30；6 个月过期；50–300 QPS | Google SERP 摘要，仅摘要 | 可但法律风险 |
| Google CSE | 100/天 | $5/1k | JSON 摘要 | **新客关闭、2027-01-01 停服** |
| Bing | — | 已退役 2025-08-11 | — | 替代 Grounding $14/1k 仅 Azure、不返原始结果 |
| SearXNG | 免费（VPS 成本） | 0；AGPL-3.0 | 元搜索 JSON；**`format=json` 默认关需改配置** | 自托管 |
| Firecrawl | 1000 credits/月 | $19–749/月；search 2 credits/10 结果；scrape 1/页；JSON +4 | markdown/JSON/结构化 | 可 |

选型：默认 **Brave**；一步到位要正文的高价值查询路由 **Exa/Tavily**；数据出境/成本敏感用 **SearXNG**（需运维、处理 429/CAPTCHA）。不选 Google CSE 和原生 Bing。

## 三、长任务模式

- 同步阻塞（现有 research，≤5 轮）适用秒级；
- 异步 task_id+轮询：**MCP 2026-07-28 tasks 扩展 SEP-2663**（CreateTaskResult{taskId,ttl,pollInterval} → tasks/get，终态 completed/failed/cancelled/input_required；tasks/result 阻塞式已移除）；
- 进度通知：MCP `notifications/progress`（progressToken），FastMCP `Progress(set_total/increment/set_message)`。

FastMCP 4.2.2 原生用法：`pip install "fastmcp[tasks]"` + `mcp.add_extension(TasksExtension())` + `@mcp.tool(task=True)` (仅 async)。模式 forbidden/optional/required；后端 memory://（单进程）或 redis://（多 worker）。**限制**：走 2026-07-28 协商，旧客户端同步执行 → 保留同步 fallback。线上 Gateway 协商版本待实测。

## 四、演进方案（核心：发现开放 / 抓取受控）

```
用户目标（任意主题）
 ├─【发现层 web_search】任意公开页，仅返标题/URL/摘要/日期；出网由搜索服务商完成，我方不直接访问任意主机；结果全部 untrusted，禁止自动触发抓取
 └─【抓取层 web_fetch/fetch_page】逐域合规闸门（不可绕过）
     ├─ Tier A 已验证 7 白名单域 → 现有 allow_url+robots+限速+缓存
     ├─ Tier B 新域 → 人工/策略评估后加入 allowlist 或用第三方提取 API 代理
     └─ Tier C 高风险（PI/政府敏感/登录态/付费墙）→ 拒绝或人工确认
```

关键：不改 `allow_url()` 拒绝语义，它仍是唯一出网入口；搜索结果域名不自动进白名单；Tier B 加入需 `audit("domain_promote")`；第三方提取 API 只改变出网主体、不改变内容策略责任。

**增改清单**：新增 `web_search`（provider 可插拔，默认 Brave）、`web_fetch`（content_mode/start_index/max_chars）、`research_status/research_cancel`、`domain_risk_check`；改造 `research`（加 depth/max_sources/token_budget/include_domains，保留旧默认值）、`fetch_page`（加 max_chars/start_index/content_mode）、`mcp_security.py`（增 search_result_sanitize/check_domain_tier/新审计事件，不删改现有函数）；`query_local`/`search_site` 不动。

## 五、返回结构与 token 护栏

- 摘要 vs 全文：默认 `content_mode="summary"`，full 需显式且受 max_chars 限制，全文包在 untrusted_content；
- 出处强制：每条 `url+site+published_at?+relevance+trust_tier`；
- token 硬夹：`max_results≤10`、`max_chars≤8000`、`depth≤3`，返回 `usage.estimated_tokens`；
- 分页：游标不透明且服务端签名；正文用 start_index+max_length 分块；
- 错误/无结果：统一 `{"status":"ok/empty/error","count":0,"error":null,"hint":"..."}`，给模型下一步建议，不静默返回空串；
- 工具注解：发现层 `readOnlyHint/idempotentHint/openWorldHint=True`；
- 成本可见：`usage:{provider,credits,est_cost_usd,cached}`。

## 六、未决风险

1. 线上 Gateway 的 MCP 协商版本未实测，tasks 可能退化同步（需 research_status 兜底）；
2. memory:// 后端不适合多实例，多副本需 Redis + 统一加密 key；
3. Tier B 第三方提取是否构成对 robots/ToS 的规避需法务确认；
4. Brave 定价官方博客 $5/1k 与三方 $3/1k 冲突，以合同/账单为准；
5. 提示注入无完全防御，高风险动作（写库/改白名单/发消息）永不由网页内容触发；
6. 成本失控（tavily_research 单次 250 credits），adapter 层设每任务成本上限。
