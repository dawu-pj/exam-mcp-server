"""Scrapy 配置：合规红线全部在此固化，对应《100 词框架》B4/B2 与已定决策。"""
BOT_NAME = "study-desk-mvp"

SPIDER_MODULES = ["crawler.spiders"]
NEWSPIDER_MODULE = "crawler.spiders"

# ===== 合规：明示身份 + 严格遵守 robots（RFC9309 由 scrapy 内建 protego 处理）=====
USER_AGENT = "StudyDeskResearchBot/0.1 (public exam-info pages; educational research; contact: local)"
ROBOTSTXT_OBEY = True

# ===== 严格限速：每域 1 并发 + 1 秒延迟 + 随机抖动（scrapy 默认 0.5~1.5 倍）=====
CONCURRENT_REQUESTS = 2
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1.0
RANDOMIZE_DOWNLOAD_DELAY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 8.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

# ===== 稳健性：失败重试 + 退避，遇 429/503 不换个 IP 硬闯 =====
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]
DOWNLOAD_TIMEOUT = 25

# ===== 缓存：已抓过的页面不重复请求（"the request you do not send cannot get you banned"）=====
HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_DIR = "data/httpcache"

# ===== 只存必要字段，不落 cookie / 不维持登录态 =====
COOKIES_ENABLED = False

# ===== Pipeline：抽取 -> 清洗脱敏 -> 去重 -> 落盘（顺序即依赖）=====
ITEM_PIPELINES = {
    "crawler.pipelines.ExtractPipeline": 100,
    "crawler.pipelines.CleanPipeline": 200,
    "crawler.pipelines.DedupPipeline": 300,
    "crawler.pipelines.StorePipeline": 400,
}

FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = "INFO"
