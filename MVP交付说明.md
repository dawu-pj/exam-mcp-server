# crawler-mvp 交付说明

> 完成时间：2026-11-19　|　状态：**MVP 闭环已跑通，真实数据端到端验证通过**
> 交付物：本目录下全部代码 + 数据 + 文档。复现命令见文末。

## 一、MVP 成果（对照开工前定的 P0-1：CLI 管线 + 评估脚本，不做 Web UI）

**一句话**：5 个真实考试站 → 抓 22 篇公告 → 抽正文+脱敏+去重 → 落盘 Markdown+元数据 → BM25 检索 → 5 个真实问题评估全通过。

| 环节 | 实现 | 实测结果 |
|---|---|---|
| 抓取 | `crawler/spiders/exam_spider.py`（Scrapy，5 站配置驱动） | 32 请求 / 22 篇 / 20.6 秒 / 0 封禁 |
| robots | Scrapy 内建 + RFC9309 | 5 次请求：1 站 200 有规则、4 站 404 放行 |
| 限速 | 每域 1 并发 + 1s 延迟 + 抖动 + Autothrottle | 96 请求/分钟，礼貌区间 |
| 抽取 | trafilatura 2.0.0（FineWeb 同款） | 正文 310–1655 字，导航页脚剔除 |
| 脱敏 | `crawler/cleaning.py`（中文 PII 规则） | **实测命中并脱敏 2 处** |
| 去重 | URL 规范化 + 内容 SHA256 指纹 | 4 个 404 页跳过，无重复落盘 |
| 落盘 | `.md` + `.meta.json` sidecar | 22 组文件，`data/clean/` |
| 检索 | BM25 + jieba（P0-5：不接 embedding） | golden set Top-1 命中率 **100%** |
| 出处 | Hit.cite() 返回标题+URL | 可追溯率 **100%** |

## 二、评估实测输出（tests/evaluate.py）

```
索引文档数: 22 | 评估问题数: 5
[PASS] 2026年下半年四六级考试什么时候报名？     → 四六级考试网  Top1 17.392  关键词 3/3
[PASS] 2026年9月NCRE考试时间？                  → NCRE考试网    Top1 15.327  关键词 2/3
[PASS] 2027考研安全工作视频会讲了什么？          → 研招网         Top1 16.388  关键词 3/3
[PASS] 2026年度教育考试科研课题立项名单？        → 中国教育考试网 Top1 13.08   关键词 3/3
[PASS] 高校毕业生求职参考哪些就业信息平台？      → 中国教育在线   Top1 20.392  关键词 2/2
Top-1 站点命中率: 5/5 = 100%
出处可追溯率:     5/5 = 100%
```

## 三、8 个盲区落地核验（P0-4：按推荐全补）

| 盲区 | 承诺 | 落地情况 | 状态 |
|---|---|---|---|
| **B1 采后义务** | 误采 PII 删除/匿名化 | `cleaning.py` 身份证 GB11643 校验位 + 手机号号段 + 邮箱，落盘前脱敏；中文姓名只标记不删（避免误伤） | ✅ 已落，实测脱敏 2 处 |
| **B2 网页变了怎么办** | 重访/URL 规范化/指纹 | URL 去 utm/spm 追踪参数；正文归一化 SHA256 指纹；sidecar 记 fetched_at + parser_version + index_version；HTTP 缓存避免重抓 | ✅ 已落（重访调度 deferred 到阶段 2） |
| **B3 元数据边界** | sidecar 从 4 字段扩展 | 现 14 字段：doc_id/source_url/title/site_name/fetched_at/http_status/parser_version/index_version/lang/content_sha256/pii_redactions/text_chars/chunk_ids/extra；检索支持按 site 过滤 | ✅ 已落 |
| **B4 robots 取不到时** | RFC9309 异常分支 | Scrapy 内建 protego：200 按规则、404 放行、5xx 全禁；实测 4 站 404 放行、1 站 200 按规则 | ✅ 已落，实测验证 |
| **Q1 结构分块** | 非固定 512 | 当前按整篇文档建索引（BM25 阶段无分块必要）；结构分块延到接 embedding 前 | ⏳ 按依赖顺延，非遗漏 |
| **Q2 混合检索+reranker** | 延期阶段 2 | 已确认延期，未做 | ⏳ 按决策延期 |
| **Q3 评估集固化** | golden set | `tests/golden_set.json`（阶段 2 已扩到 20 题，含负例与文档标注）+ `tests/evaluate.py` 文档级评估 v2 | ✅ 已落（2026-11-20，见 `阶段2调研与实施方案.md`） |
| **S1 通用/聚焦微调** | A 通用 / B 聚焦 | 5 站中四六级+NCRE 服务 B（聚焦备考），其余服务 A（广覆盖资讯） | ✅ 已落 |

**结论**：4 个致命盲区（B1–B4）全部落地并实测；Q3/S1 已落；Q1/Q2 按已确认的依赖顺序与决策延期，不是遗漏。

## 四、关键工程坑（已解决，写在这里防复发）

**Twisted 版本冲突**：Scrapy 2.13.3 依赖 `twisted.internet._sslverify._setAcceptableProtocols`，但 twisted 26.x 移除了该 API，导致 `scrapy crawl` 直接 ImportError。解决：pin `twisted==24.11.0`，已写入 requirements.txt 并注释原因。这验证了调研阶段的结论——**顶层依赖 pin 死还不够，传递依赖也必须锁**。

## 五、复现命令

```powershell
cd C:\爬虫项目调研\crawler-mvp
# 1. 链路自检（无需联网，验证抽取/分词/BM25/元数据）
.\.venv\Scripts\python.exe src\selftest.py
# 2. 站点探测（可选，已跑过，留痕在 data/*.json）
.\.venv\Scripts\python.exe src\probe_sites.py
.\.venv\Scripts\python.exe src\probe_articles.py
# 3. 真实采集（限速约 20 秒）
.\.venv\Scripts\python.exe -m scrapy crawl exam
# 4. 检索评估
.\.venv\Scripts\python.exe tests\evaluate.py
# 5. 手动查询
.\.venv\Scripts\python.exe src\retrieve.py "四六级报名"
```

## 六、阶段 2 状态（2026-11-20 已实施，详见 `阶段2调研与实施方案.md`）

原本列的五项下一步经五路一手调研后重新裁定：

1. ✅ **扩量校验**：golden set 5→20 题（含 5 负例、文档级标注），评估升到文档级指标；**50 页人工校验仍未跑满**（当前 22 篇）。
2. ❌ **结构分块 + contextual**：取消——22–50 篇短文档用全量索引更合适，切块反稀释 BM25 区分度。
3. ⚠️ **混合检索 + reranker**：降格为实验——消融数据显示小语料精确匹配场景可能低于纯 BM25，未接。
4. ⚠️ **重访调度**：降格为手动 recheck——5 站 HTTP 头已实测（3 站支持 ETag+304），但限速约束下条件请求收益有限。
5. ✅ **embedding 选型**：本地 bge-small-zh-v1.5 技术储备完成（独立模块、不接主链路）；配对检验 CI 跨 0，印证不接。

**追加实验**：BM25 标题/正文分字段加权（alpha=0.5）经 LOOCV 无偏复核确认有效，文档级 Top-1 从 53% 提升到 87%，已接入主检索链路。

**MVP 阶段到此完整闭环，未接生成、未接 Web UI、未上托管 API——与开工前全部决策一致。**
