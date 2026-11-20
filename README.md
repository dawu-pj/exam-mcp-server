# crawler-mvp

给大模型供信息的爬虫 MVP —— Python 版，本机开发。

> ✅ **状态（2026-11-19）：MVP 闭环 + MCP 工具层 + 部署/日常入口已跑通**——5 站真实采集 22 篇、脱敏去重落盘、BM25 检索评估 Top-1 命中率 100%；MCP Server 4 工具 + 双传输 + 5 层安全闸门全部实测通过。详见 `MVP交付说明.md`、`MCP接入指南.md`（大模型调用）、`部署与日常使用速查.md`（装+用入口）、**`零环境部署指南.md`**（不装 Python，推 GitHub 上云 / 国内云函数 / 便携 exe / WSL2 四条路）。
>
> ☁️ **免安装上云已备好**：`app.py`（ASGI 入口，`mcp.http_app(stateless_http=True)`）+ `.gitignore`（缓存/venv 排除，保留 22 篇数据）。uvicorn 起服务 + 客户端经 `/mcp` 调用已实测通过。FastMCP Gateway 入口填 `src/mcp_server.py:mcp`。
>
> 📄 **许可证：MIT**（见 `LICENSE`）。依赖链全为 MIT/Apache-2.0/BSD 宽松许可，无 GPL/AGPL 传染性。
>
> 🚀 **已推 GitHub 并具备上云条件**：`git@github.com:dawu-pj/exam-mcp-server.git`（Public / main，最新 `14443db`）。**部署照做见 `部署操作卡.md`**——FastMCP Gateway 网页三步（GitHub 授权→选仓库→Entrypoint 填 `src/mcp_server.py:mcp`），约 60 秒拿 `https://<name>.fastmcp.app/mcp`。已做云端只读文件系统降级 + 双依赖声明 + 放宽 Python 版本，入口经 `fastmcp inspect` 验证 4 工具识别。

## 最快上手

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1              # 新机器一键安装（本机已装可跳过）
.\.venv\Scripts\python.exe quick.py ask "四六级报名"             # 秒查已入库
.\.venv\Scripts\python.exe quick.py research "考研报名流程"      # 多轮调研闭环
.\.venv\Scripts\python.exe quick.py serve --http --port 8765    # 启动 MCP 服务给大模型
```
日常所有操作收拢在 `quick.py`（ask/sites/fetch/research/crawl/serve/add-site/verify）。三种拿信息的路径与速度对比见 `部署与日常使用速查.md` 第 2 节。

## MCP 入口（给大模型调用）

```powershell
.\.venv\Scripts\python.exe src\mcp_server.py --transport streamable-http --host 0.0.0.0 --port 8765   # 远程大模型（主方案）
.\.venv\Scripts\python.exe src\mcp_server.py --transport stdio                                        # 本机客户端
.\.venv\Scripts\python.exe tests\test_mcp.py                                                          # MCP 工具测试（本地闭环·零外网）
```

4 个工具：`query_local` / `search_site` / `fetch_page`（受控实时抓取）/ `research`（多轮调研闭环）。你的大模型不在本机 → 用 streamable-http 挂载，配置示例与安全边界见 `MCP接入指南.md`。

> **测试零外网保险**：`tests/test_mcp.py` 全程用 `tests/no_network.py` 的 `no_network()` 包裹——任何意外的真实 HTTP 请求会立刻失败（借鉴 13ft 源码，改用 context manager 封装）。自检确认 4 工具 + 4 类 SSRF 拦截都不需要联网；真正联网的只有 `src/verify_remote.py`（线上 OAuth 验证）。

## 一键复现

```powershell
.\.venv\Scripts\python.exe src\selftest.py                    # 链路自检
.\.venv\Scripts\python.exe -m scrapy crawl exam               # 真实采集（限速约 20 秒）
.\.venv\Scripts\python.exe tests\evaluate.py                  # 检索评估（主集 20 题）
.\.venv\Scripts\python.exe tests\evaluate_field_weight.py      # 字段加权 A/B + LOOCV 复核
.\.venv\Scripts\python.exe tests\validate_alpha_holdout.py     # alpha 独立复核（holdout 18 题）
.\.venv\Scripts\python.exe src\retrieve.py "四六级报名"        # 手动查询
```

> 语言决策已收敛：Python + 本机（`Go技术栈可行性修正报告.md` 中的 Go 方案已被用户否决，仅作推演记录）。
> 设计依据：`../爬虫项目立项调研报告.md`、`../GitHub开源项目调研与选型报告.md`、`../爬虫行业100关键词认知框架.md`、`../MVP开工确认清单.md`。
> **阶段 2 已实施（2026-11-20）**：`阶段2调研与实施方案.md`——五路一手调研 + 用户拍板后落地，另追加字段加权实验与 alpha 独立复核。golden set 5→20 题、evaluate.py 升级文档级指标（暴露站点 80% vs 文档 Top-1 53% 的虚高，gap +27%）；**BM25 标题/正文分字段加权（alpha=0.5）经"LOOCV + 独立 holdout 18 题"两层复核确认有效，Top-1 53%→87%（holdout 93%），已接入主链路**；5 站重访前提实测（3 站支持 ETag+304）；embedding 技术储备（本地 bge-small-zh 独立模块，实测 CI 跨 0 不显著，未接主链路）。Q1 分块/Q2 混合检索按裁定未做。**源头侦察（2026-11-20）**：初版"首页探测"曾误判"扩不了"，改用栏目页深挖后找出 171 篇高质量新公告 + 5 个省级考试院新源，源头已解锁（详见 `data/source_recon_report.md`），但未抓正文入库、未改主链路。

> **技术认知储备**：`技术认知地图与能力边界.md`——把多轮调研的能力手段、法律定价、自身现状系统沉淀，回答"如果场景变了该怎么做、可以不做但不能不知道"。含能力—风险光谱、13ft 源码逐项裁定、数据源侦察成果、解锁决策 Gate、法律锚点、调研资产索引。立场是认知地图而非操作手册：记录原理与代价，不写绕过防护的配方。

## 环境状态（2026-11-19 实测就绪）

| 项 | 值 |
|---|---|
| Python | **3.12.16**（`C:\software\uv\python\cpython-3.12.16-windows-x86_64-none\python.exe`） |
| uv | 0.13.9（`C:\software\uv\bin\uv.exe`） |
| 虚拟环境 | 项目内 `.venv\`，未改系统 PATH |
| 依赖 | 58 包，`requirements.txt` 全部 pin 死版本 |
| 链路自检 | **全通过**（抽取 / 分词 / BM25 检索 / 元数据契约） |

启动方式：

```powershell
.\.venv\Scripts\python.exe src\selftest.py
```

## 技术栈（按调研锁定，分阶段落地）

```
采集层   Scrapy 2.13.3  —— robots 遵守 + 每域 1 req/s + 下载延迟 1s（项目模板默认值）
         ↓ 原始 HTML + 元数据 sidecar（统一中间契约）
抽取层   trafilatura 2.0.0 —— 正文抽取（FineWeb 同款，Apache-2.0）
         + 站点级 CSS 选择器（goquery 思路的 Python 实现：parsel，Scrapy 自带）
清洗层   jieba 0.42.1 中文分词 + URL 规范化 + 中文 PII 规则脱敏（已落，实测命中 2 处）
         + SimHash 近重复检测（阶段 2，MVP 阶段先 URL 级去重）
检索层   rank-bm25 0.2.2 —— 标题/正文分字段加权（alpha=0.5，LOOCV 验证），不接 embedding（已定 P0-5）
评估层   golden set 20 题已固化（6 exact/5 paraphrase/4 multi/5 负例），文档级指标 + 配对 bootstrap 检验
```

## 已纳入的 8 个盲区（来自 100 词框架）

> 状态已按 2026-11-19 交付实测同步（4 致命盲区全落）。详见 `MVP交付说明.md` 第三节核验表。

| 盲区 | 状态 | 落点 |
|---|---|---|
| B1 采后义务（PII 删除/匿名化） | ✅ 已落 | `cleaning.py`：身份证 GB11643 校验位+手机号+邮箱；实测脱敏 2 处，中文姓名只标记不删 |
| B2 网页变了怎么办（重访/URL 规范化/删除同步） | ✅ 已落 | URL 规范化 + 正文 SHA256 指纹 + fetched_at；重访调度延阶段 2 |
| B3 元数据边界 | ✅ 已落 | sidecar 14 字段（非 6）；检索按 site 过滤 |
| B4 robots 取不到时（4xx 放行 / 5xx 全禁） | ✅ 已落 | Scrapy protego + RFC9309；实测 4 站 404 放行、1 站 200 按规则 |
| Q1 结构分块（非固定 512） | ⏳ 顺延 | BM25 阶段整篇索引够用，接 embedding 前再做 |
| Q2 混合检索 + reranker | ⏳ 延期阶段 2 | 已确认，未做 |
| Q3 评估集固化 | ✅ 已落 | `tests/golden_set.json` 20 题（含负例/文档标注）+ `evaluate.py` 文档级评估 v2；旧站点级 100% 已证虚高 |
| S1 通用/聚焦微调 | ✅ 已定 | A 通用广覆盖 / B 聚焦领域站点 |

## 目标站点（P0-2 已由实测补齐，见 `考试站点实测选型结论.md`）

5 个境内官方/权威站，全部实测可抓、robots 允许、正文抽取质量过关：
neea.edu.cn / cet.neea.edu.cn / yz.chsi.com.cn / ncre.neea.edu.cn / eol.cn。
中国人事考试网（cpta.com.cn）因阿里云 WAF 拦截机器人 UA 已剔除（不绕、不换指纹）。

探测脚本：`src/probe_sites.py` + `src/probe_articles.py`，留痕在 `data/*.json`。

## 目录结构

```
crawler-mvp/
├── .venv/              隔离环境（不入库）
├── crawler/            Scrapy 采集与清洗
│   ├── settings.py     合规配置（robots/限速/退避/缓存）
│   ├── items.py        数据项（RawPageItem / CleanDoc）
│   ├── cleaning.py     PII 脱敏 + URL 规范化 + 内容指纹
│   ├── pipelines.py    抽取→清洗→去重→落盘
│   └── spiders/
│       └── exam_spider.py   5 站配置驱动 spider
├── src/
│   ├── selftest.py     链路自检（已通过）
│   ├── probe_sites.py  站点+robots 探测
│   ├── probe_articles.py  内页正文探测
│   ├── probe_expansion.py  扩量空间探测（GBK 编码修正，证据否决堆量）
│   ├── retrieve.py     BM25 检索 + 出处（主链路，标题/正文分字段 alpha=0.5）
│   ├── field_weighted.py  字段加权实验入口（薄封装，复用主链路）
│   └── embedding_research.py  阶段2技术储备：本地 bge-small-zh 独立实测（不接主链路）
├── data/
│   ├── clean/          22 组 Markdown + sidecar（已采集）
│   ├── probe_report.json / article_probe_report.json  站点探测留痕
│   └── bm25_baseline.json / field_weighted_main.json / field_weighted_alpha03.json / holdout_alpha_validation.json / expansion_probe.json / embedding_research.json / revisit_probe.json  阶段2实测留痕
├── tests/
│   ├── golden_set.json 20 个真实问题（6 exact/5 paraphrase/4 multi/5 负例，含文档级标注）
│   ├── holdout_set.json 18 题独立复核集（未参与 alpha 选择，15 正/3 负）
│   ├── evaluate.py     文档级评估 v2（doc_hit@1/@3、mrr@5、拒答代理、配对 bootstrap 检验）
│   ├── evaluate_field_weight.py  字段加权 A/B + LOOCV 无偏复核
│   └── validate_alpha_holdout.py  alpha 独立复核（holdout 18 题扫全部 alpha）
├── requirements-research.txt  本地技术储备依赖（fastembed/onnxruntime，不上云端）
├── MVP交付说明.md       完整交付与 8 盲区核验
├── 阶段2调研与实施方案.md  五路调研 + 实施记录（本阶段核心文档）
├── 考试站点实测选型结论.md
├── pyproject.toml      uv 项目配置
├── requirements.txt    pin 死版本依赖清单（主链路，云端 Gateway 读此文件）
└── README.md           本文件
```
